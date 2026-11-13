"""Canal de Streaming em Tempo Real via WebSockets (/ws/search).

Responsável por:
1. Orquestração concorrente de múltiplos coletores assíncronos (Aéreo, Rodoviário e Intermodal).
2. Streaming progressivo: emite eventos QUOTE_BATCH para o frontend à medida que cada
   provedor responde, sem bloquear a interface esperando o mais lento ("pipocamento" de cards).
3. Resiliência por isolamento (Bulkhead): se um provedor falhar ou sofrer timeout, emite
   PROVIDER_ERROR sem interromper os demais.
4. Virtual Interlining real: busca as pernas Origem -> Hub (ônibus) e Hub -> Destino (voo)
   para hubs com aeroporto dentro do raio geodésico da origem.
5. Geração em tempo real de combinações Hacker Fares (quando há data de volta informada).
6. Consolidação e persistência automática no cache com TTL dinâmico ao finalizar
   (apenas preços reais de mercado; simulações e rotas sintetizadas nunca são gravadas).
7. Cancelamento gracioso de tarefas pendentes em caso de desconexão do cliente.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
import json
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid
from zoneinfo import ZoneInfo

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.analyzers.interliner import VirtualInterlineRoute, build_virtual_interline_routes
from app.analyzers.price_thermometer import enrich_quotes_batch_with_thermometer
from app.analyzers.route_evaluator import enrich_quote_with_evaluation
from app.analyzers.trivio_scorer import generate_hacker_fares, score_and_rank_quotes
from app.cache.deduplicator import deduplicate_quotes
from app.services.air_quotes import AirProviderUnavailableError, get_air_quote_service
from app.collectors.air.ignav import IgnavBillingError
from app.collectors.base import StandardizedQuote
from app.analyzers.road_reference import build_road_references
from app.api.rate_limit import client_key, search_limiter
from app.collectors.road.clickbus import ClickBusCollector, CLICKBUS_SLUG_MAP
from app.config import settings
from app.database import SessionLocal
from app.geo.catchment import CatchmentFinder
from app.geo.entity_resolver import EntityResolver
from app.models import Hub, Terminal, ModalType, ProviderMapping
from app.services.quote_pipeline import QuotePipeline, TerminalCache, process_and_persist_quotes
from app.services.road_quotes import (
    ROAD_ESTIMATE_PROVIDER,
    build_antt_legs_to_hub,
    build_antt_road_quotes,
    has_antt_route,
)
from app.services.gtfs_road_quotes import build_gtfs_leg_to_hub, build_gtfs_road_quotes, has_gtfs_route

logger = logging.getLogger("trivio.api.websocket")

router = APIRouter(tags=["Real-Time Streaming"])

# Fontes de cada trecho da busca:
#   ônibus          -> banco ANTT + projeção (app/services/road_quotes.py), sem custo;
#   avião           -> Ignav (app/services/air_quotes.py), request paga;
#   ônibus + avião  -> ônibus da ANTT até o hub + voo do Ignav saindo do hub.
INTERLINER_PROVIDER = "trivio_interliner"
PRICE_BASIS_INTERLINE = "antt_projection+ignav"
INTERLINE_NOTICE = (
    "Conexão montada pelo Trivio: trecho de ônibus estimado pelas vendas registradas na ANTT "
    "(preço e horário estimados) e voo com tarifa ao vivo do Ignav. Os bilhetes são comprados "
    "separadamente."
)
MAX_INTERLINE_HUBS = 2

# Ofertas reais de ônibus (ClickBus) seguem DESLIGADAS: o site recusa o acesso automatizado e a
# API B2B ainda não foi liberada. O ônibus da busca vem da projeção ANTT (road_quotes).
ROAD_OFFERS_ENABLED = False

# ==============================================================================
# 1. Resolução de Localidades
# ==============================================================================

def _resolve_hub_and_terminals(query_str: str, db: Session, resolver: EntityResolver):
    """Resolve uma string (código IATA, nome da cidade ou terminal) para seu Hub e respectivos terminais."""
    clean = (query_str or "").strip()
    if not clean:
        return None, None, None
    res = resolver.resolve_any(clean)
    if not res:
        return None, None, None
    if res.entity_type == "TERMINAL":
        term = db.query(Terminal).filter(Terminal.id == res.id).first()
        if not term:
            return None, None, None
        hub = term.hub
        # Terminal escolhido por nome/código restringe a busca àquele modal: uma rodoviária
        # não traz o aeroporto da cidade (e vice-versa), senão o Ignav seria consultado à toa.
        road_term = term if term.modal == ModalType.ROAD else None
        air_term = term if term.modal == ModalType.AIR else None
    else:
        hub = db.query(Hub).filter(Hub.id == res.id).first()
        if not hub:
            return None, None, None
        road_term = next((t for t in hub.terminals if t.modal == ModalType.ROAD), None)
        air_term = next((t for t in hub.terminals if t.modal == ModalType.AIR), None)
    return hub, road_term, air_term


def _clickbus_external_id(db: Session, road_term: Optional[Terminal], fallback_query: str) -> str:
    """Obtém o slug da ClickBus via ProviderMapping, com fallback para o mapa estático."""
    if road_term:
        mapping = db.query(ProviderMapping).filter(
            ProviderMapping.terminal_id == road_term.id,
            ProviderMapping.provider_name == "clickbus",
        ).first()
        if mapping:
            return mapping.external_id
        if road_term.official_code:
            return CLICKBUS_SLUG_MAP.get(road_term.official_code.upper(), road_term.official_code.lower())
    return CLICKBUS_SLUG_MAP.get(fallback_query.upper(), fallback_query.lower())


def _find_interline_hubs(
    orig_hub: Optional[Hub],
    dest_hub: Optional[Hub],
    db: Session,
    radius_km: float,
    max_hubs: int = MAX_INTERLINE_HUBS,
) -> List[Tuple[Hub, Terminal, Terminal]]:
    """Seleciona hubs intermediários (com rodoviária e aeroporto) no raio geodésico da origem.

    O padrão de Virtual Interlining modelado é: Ônibus (Origem -> Hub) + Voo (Hub -> Destino).
    """
    if not orig_hub:
        return []

    excluded = {orig_hub.id}
    if dest_hub:
        excluded.add(dest_hub.id)

    finder = CatchmentFinder(db)
    nearby_airports = finder.find_terminals_for_hub(
        hub_id=orig_hub.id,
        max_radius_km=radius_km,
        modal_filter=ModalType.AIR,
    )

    selected: List[Tuple[Hub, Terminal, Terminal]] = []
    seen_hubs = set()
    for candidate in nearby_airports:  # já ordenado por distância
        if candidate.hub_id in excluded or candidate.hub_id in seen_hubs:
            continue
        hub = db.query(Hub).filter(Hub.id == candidate.hub_id).first()
        if not hub:
            continue
        road_term = next((t for t in hub.terminals if t.modal == ModalType.ROAD), None)
        air_term = next((t for t in hub.terminals if t.id == candidate.terminal_id), None)
        # Sem código IATA o voo do hub não pode ir ao Ignav (pago): o hub fica de fora.
        if not road_term or not air_term or not air_term.official_code:
            continue
        seen_hubs.add(hub.id)
        selected.append((hub, road_term, air_term))
        if len(selected) >= max_hubs:
            break
    return selected


# ==============================================================================
# 3. Coleta Concorrente e Esteira de Inteligência
# ==============================================================================

async def _fetch_provider_batch(
    collector_name: str,
    collector_instance: Any,
    origin: str,
    destination: str,
    travel_date: str,
) -> Dict[str, Any]:
    """Executa a coleta de um único provedor isolando exceções e medindo latência.

    O aéreo ao vivo (AirQuoteService.get_quotes) propaga as falhas em vez de devolver
    lista vazia: assim a busca avisa o frontend que o aéreo não foi consultado.
    """
    start_time = time.perf_counter()
    try:
        fetch = getattr(collector_instance, "get_quotes", None) or collector_instance.safe_fetch
        quotes = await fetch(origin, destination, travel_date)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
        # O coletor de sites recusados (401/403 ou robots.txt) devolve lista vazia; aqui
        # transformamos isso em aviso, para não parecer 'zero resultados'.
        if getattr(collector_instance, "access_denied", False):
            logger.warning(f"Coletor {collector_name} sem acesso autorizado pelo provedor.")
            return {
                "success": False,
                "provider": collector_name,
                "modal": collector_instance.modal,
                "quotes": [],
                "elapsed_ms": elapsed_ms,
                "error": "O provedor recusou o acesso automatizado a esta fonte.",
                "reason": "access_denied",
            }
        return {
            "success": True,
            "provider": collector_name,
            "modal": collector_instance.modal,
            "quotes": quotes,
            "elapsed_ms": elapsed_ms,
            "error": None,
            "reason": None,
        }
    except AirProviderUnavailableError as exc:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
        logger.warning(f"Coletor {collector_name} não consultado ({exc.reason}): {exc}")
        return {
            "success": False,
            "provider": collector_name,
            "modal": collector_instance.modal,
            "quotes": [],
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
            "reason": exc.reason,
        }
    except IgnavBillingError as exc:
        # Conta sem crédito ou no teto mensal do provedor: não é falha técnica, e retentar não ajuda.
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
        logger.warning(f"Coletor {collector_name} recusado por cobrança ({exc.reason}): {exc}")
        return {
            "success": False,
            "provider": collector_name,
            "modal": collector_instance.modal,
            "quotes": [],
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
            "reason": exc.reason,
        }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
        logger.error(f"Erro no coletor {collector_name} ({type(exc).__name__}: {exc})", exc_info=True)
        return {
            "success": False,
            "provider": collector_name,
            "modal": getattr(collector_instance, "modal", "UNKNOWN"),
            "quotes": [],
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
            "reason": "provider_error",
        }


def _process_and_score_quotes(
    quotes: List[StandardizedQuote],
    hourly_time_value: float,
    db_session: Optional[Session] = None,
) -> List[StandardizedQuote]:
    """Aplica a esteira de inteligência ANTES do streaming.

    Ordem: resolução canônica + transfer urbano + porta a porta -> deduplicação ->
    prós/contras -> termômetro -> Trivio Score. Assim o card recebido pelo frontend
    já contém o Custo Efetivo Real completo e o Score coerente com ele.
    """
    if not quotes:
        return []

    # 1. Terminais canônicos, custo/tempo de transfer urbano e tempo porta a porta.
    #    Os terminais do lote vêm do cache (os voos dividem os mesmos aeroportos) e os
    #    prós/contras ficam para o passo 3, depois da deduplicação.
    if db_session is not None:
        terminal_cache: TerminalCache = {}
        for q in quotes:
            QuotePipeline.enrich_quote(q, db_session, terminal_cache=terminal_cache, evaluate=False)

    # 2. Deduplicação (assinatura usa os IDs canônicos resolvidos acima)
    deduped = deduplicate_quotes(quotes, attach_sellers_info=True)

    # 3. Prós, Contras e Contratempos (reavaliados com os dados de code-share)
    for q in deduped:
        enrich_quote_with_evaluation(q)

    # 4. Termômetro de Preço
    enrich_quotes_batch_with_thermometer(deduped, db=db_session)

    # 5. Cálculo do Trivio Score e ordenação
    return score_and_rank_quotes(deduped, hourly_time_value=hourly_time_value)


def _build_interline_quotes(
    origin: str,
    destination: str,
    orig_hub: Hub,
    orig_road_term: Terminal,
    interline_hubs: List[Tuple[Hub, Terminal, Terminal]],
    leg_results: Dict[str, List[StandardizedQuote]],
    hourly_value: float,
    db: Session,
) -> List[StandardizedQuote]:
    """Conexão Ônibus (Origem -> Hub) + Voo (Hub -> Destino) para cada hub candidato.

    O voo vem do Ignav (já consultado). O ônibus vem da projeção ANTT e é encaixado
    antes de cada voo, porque a série da ANTT não tem horários.
    """
    all_routes: List[VirtualInterlineRoute] = []

    for hub, hub_road_term, hub_air_term in interline_hubs:
        flights_raw = list(leg_results.get(f"interline_leg2::{hub.id}", []))
        if not flights_raw:
            continue

        hub_codes = {t.official_code for t in hub.terminals if t.official_code}
        terminal_lookup = {t.official_code: t for t in hub.terminals if t.official_code}

        leg2 = [
            q for q in _process_and_score_quotes(flights_raw, hourly_value, db)
            if q.modal == "AIR" and q.canonical_origin_id in hub_codes
        ]
        if not leg2:
            continue

        leg1_raw = build_antt_legs_to_hub(
            db, orig_hub, orig_road_term, hub, hub_road_term, [q.departure_time for q in leg2]
        )
        if not leg1_raw:   # sem série ANTT para este trecho: horário e tarifa do GTFS
            leg1_raw = build_gtfs_leg_to_hub(
                db, orig_hub, orig_road_term, hub, hub_road_term, [q.departure_time for q in leg2]
            )
        leg1 = [
            q for q in _process_and_score_quotes(leg1_raw, hourly_value, db)
            if q.modal == "ROAD" and q.canonical_destination_id in hub_codes
        ]
        if not leg1:
            continue

        all_routes.extend(
            build_virtual_interline_routes(
                origin_id=origin,
                destination_id=destination,
                leg1_quotes=leg1,
                leg2_quotes=leg2,
                hub_name=f"{hub.name}, {hub.state}",
                hub_id=hub.id,
                terminal_lookup=terminal_lookup,
                hourly_time_value=hourly_value,
            )
        )

    all_routes.sort(key=lambda r: r.trivio_score)

    interline_quotes: List[StandardizedQuote] = []
    for route in all_routes[:10]:
        quote = route.to_standardized_quote()
        # O trecho de ônibus é estimativa: a conexão não tem compra única nem vai para o banco.
        quote.route_metadata["is_simulated"] = True
        quote.route_metadata["price_basis"] = PRICE_BASIS_INTERLINE
        quote.route_metadata["simulation_notice"] = INTERLINE_NOTICE
        interline_quotes.append(quote)
    return interline_quotes


async def _emit_antt_road_batch(
    websocket: WebSocket,
    session_id: str,
    db: Session,
    from_hub: Hub,
    from_road_term: Terminal,
    to_hub: Hub,
    to_road_term: Terminal,
    travel_date: str,
    hourly_value: float,
    start_search_time: float,
) -> List[StandardizedQuote]:
    """Ônibus Origem -> Destino a partir do banco ANTT (sem custo), emitido como lote."""
    raw = build_antt_road_quotes(db, from_hub, from_road_term, to_hub, to_road_term, travel_date)
    if not raw:   # sem série ANTT para a rota: horário e tarifa do feed GTFS, se houver
        raw = build_gtfs_road_quotes(db, from_hub, from_road_term, to_hub, to_road_term, travel_date)
    elapsed = round((time.perf_counter() - start_search_time) * 1000, 1)
    if not raw:
        await websocket.send_json({
            "event": "PROVIDER_ERROR",
            "session_id": session_id,
            "provider": ROAD_ESTIMATE_PROVIDER,
            "error": f"Sem vendas ANTT nem horário de feed GTFS de {from_hub.name} a {to_hub.name}.",
            "reason": "no_antt_data",
            "elapsed_ms": elapsed,
        })
        return []
    processed = _process_and_score_quotes(raw, hourly_time_value=hourly_value, db_session=db)
    await websocket.send_json({
        "event": "QUOTE_BATCH",
        "session_id": session_id,
        "provider": processed[0].provider_source if processed else ROAD_ESTIMATE_PROVIDER,
        "modal": "ROAD",
        "is_simulated": True,
        "count": len(processed),
        "quotes": [q.to_dict() for q in processed],
        "elapsed_ms": elapsed,
    })
    return processed


# ==============================================================================
# 4. Orquestrador Principal de Busca em Streaming
# ==============================================================================

BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")
HOURLY_VALUE_RANGE = (0.0, 1000.0)   # R$/h aceitos para o valor do tempo
RADIUS_KM_RANGE = (0.0, 500.0)       # raio geodésico aceito para a conexão híbrida


def _today() -> date:
    """Data de hoje no Brasil (fronteira das datas de viagem aceitas)."""
    return datetime.now(BRAZIL_TZ).date()


def _number_param(params: Dict[str, Any], key: str, default: float, bounds: Tuple[float, float], label: str) -> float:
    """Lê um número da busca; ausente usa o padrão, inválido ou fora da faixa gera ValueError."""
    raw = params.get(key)
    if raw is None or raw == "":
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{label} inválido: '{raw}'.")
    low, high = bounds
    if not math.isfinite(value) or not (low <= value <= high):
        raise ValueError(f"{label} fora da faixa aceita ({low:g} a {high:g}).")
    return value


async def _send_search_error(websocket: WebSocket, session_id: str, message: str, reason: str) -> None:
    await websocket.send_json({
        "event": "SEARCH_ERROR",
        "session_id": session_id,
        "message": message,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def orchestrate_search(
    websocket: WebSocket,
    params: Dict[str, Any],
) -> None:
    """Executa o pipeline concorrente de busca e transmite eventos em tempo real."""
    session_id = uuid.uuid4().hex[:12]
    origin = str(params.get("origin", "")).strip()
    destination = str(params.get("destination", "")).strip()
    travel_date = str(params.get("date", "")).strip()
    return_date = params.get("return_date") or None
    try:
        hourly_value = _number_param(
            params, "hourly_value", settings.DEFAULT_HOURLY_TIME_VALUE_BRL, HOURLY_VALUE_RANGE, "Valor da hora",
        )
        radius_km = _number_param(
            params, "radius_km", settings.DEFAULT_CATCHMENT_RADIUS_KM, RADIUS_KM_RANGE, "Raio de busca",
        )
    except ValueError as exc:
        await _send_search_error(websocket, session_id, str(exc), "invalid_params")
        return
    # Conexão ônibus + avião só quando o usuário pede (cada hub custa uma request do Ignav)
    enable_interlining = str(params.get("enable_virtual_interlining", False)).strip().lower() in ("true", "1", "yes")

    # Cada busca pode gastar requests pagas do Ignav: limite por cliente
    if not search_limiter.allow(client_key(websocket.client)):
        await _send_search_error(
            websocket, session_id,
            "Limite de buscas por hora atingido. Tente de novo mais tarde.", "rate_limited",
        )
        return

    start_search_time = time.perf_counter()

    # Sessão de banco para resolução canônica, termômetro e persistência
    db = SessionLocal()
    try:
        await _run_search(
            websocket, db, session_id, origin, destination, travel_date, return_date,
            hourly_value, radius_km, enable_interlining, start_search_time,
        )
    finally:
        db.close()


def _validate_search_params(
    origin: str,
    destination: str,
    travel_date: str,
    return_date: Optional[str],
    orig_hub: Optional[Hub],
    dest_hub: Optional[Hub],
) -> Optional[str]:
    """Retorna uma mensagem de erro amigável se os parâmetros da busca forem inválidos."""
    if not origin or not destination:
        return "Informe a origem e o destino da viagem."
    parsed: Dict[str, date] = {}
    for label, value in (("ida", travel_date), ("volta", return_date)):
        if label == "volta" and not value:
            continue
        try:
            parsed[label] = datetime.strptime(str(value), "%Y-%m-%d").date()
        except ValueError:
            return f"Data de {label} inválida. Utilize o formato AAAA-MM-DD."
    # Datas fora da janela não viram request paga ao Ignav
    today = _today()
    if parsed["ida"] < today:
        return "A data de ida já passou."
    if parsed["ida"] > today + timedelta(days=settings.SEARCH_MAX_DAYS_AHEAD):
        return f"A data de ida pode ser no máximo {settings.SEARCH_MAX_DAYS_AHEAD} dias à frente."
    if "volta" in parsed and parsed["volta"] < parsed["ida"]:
        return "A data de volta não pode ser anterior à de ida."
    if not orig_hub:
        return f"Origem não reconhecida: '{origin}'."
    if not dest_hub:
        return f"Destino não reconhecido: '{destination}'."
    if orig_hub.id == dest_hub.id:
        return "A origem e o destino precisam ser cidades diferentes."
    return None


async def _run_search(
    websocket: WebSocket,
    db: Session,
    session_id: str,
    origin: str,
    destination: str,
    travel_date: str,
    return_date: Optional[str],
    hourly_value: float,
    radius_km: float,
    enable_interlining: bool,
    start_search_time: float,
) -> None:
    """Valida os parâmetros e executa a busca concorrente com streaming de eventos.

    Fonte de cada trecho:
    - ônibus: banco ANTT + projeção (nunca o Ignav);
    - avião: Ignav, só com aeroporto cadastrado nos dois lados;
    - ônibus + avião (só quando pedido): ônibus da ANTT até o hub + voo do Ignav do hub.
    """
    resolver = EntityResolver(db)
    orig_hub, orig_road_term, orig_air_term = _resolve_hub_and_terminals(origin, db, resolver)
    dest_hub, dest_road_term, dest_air_term = _resolve_hub_and_terminals(destination, db, resolver)

    validation_error = _validate_search_params(origin, destination, travel_date, return_date, orig_hub, dest_hub)
    if validation_error:
        await websocket.send_json({
            "event": "SEARCH_ERROR",
            "session_id": session_id,
            "message": validation_error,
            "reason": "invalid_params",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return

    air_collector = get_air_quote_service()

    # Avião: o Ignav (pago) só recebe códigos IATA de aeroportos cadastrados nos dois lados.
    orig_air_code = orig_air_term.official_code if orig_air_term else None
    dest_air_code = dest_air_term.official_code if dest_air_term else None
    air_available = bool(orig_air_code and dest_air_code)

    # Ônibus: precisa de rodoviária cadastrada nos dois lados; o preço vem do banco ANTT.
    orig_road_ok = bool(orig_road_term and orig_road_term.official_code)
    dest_road_ok = bool(dest_road_term and dest_road_term.official_code)
    road_available = orig_road_ok and dest_road_ok

    # Ônibus + avião: só quando o usuário pede a conexão. Cada hub custa uma request do
    # Ignav (o voo do hub), então só entram hubs com ônibus registrado na ANTT até eles.
    interline_hubs: List[Tuple[Hub, Terminal, Terminal]] = []
    if enable_interlining and dest_air_code and orig_road_ok:
        for hub, hub_road_term, hub_air_term in _find_interline_hubs(orig_hub, dest_hub, db, radius_km):
            if has_antt_route(db, orig_hub, hub, travel_date) or has_gtfs_route(db, orig_hub, hub):
                interline_hubs.append((hub, hub_road_term, hub_air_term))
            else:
                logger.info(f"[interline] {hub.name}: sem ônibus na ANTT até o hub; voo não consultado.")

    # 1. Emite SEARCH_STARTED
    await websocket.send_json({
        "event": "SEARCH_STARTED",
        "session_id": session_id,
        "origin": origin,
        "destination": destination,
        "travel_date": travel_date,
        "return_date": return_date,
        "providers": (
            ([ROAD_ESTIMATE_PROVIDER] if road_available else [])
            + (["clickbus"] if ROAD_OFFERS_ENABLED and road_available else [])
            + ([air_collector.provider_name] if air_available else [])
            + (["virtual_interlining"] if interline_hubs else [])
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    if not air_available:
        missing = origin if not orig_air_code else destination
        await websocket.send_json({
            "event": "PROVIDER_ERROR",
            "session_id": session_id,
            "provider": air_collector.provider_name,
            "error": f"Aéreo não consultado: '{missing}' não tem aeroporto cadastrado.",
            "reason": "no_airport",
            "elapsed_ms": 0.0,
        })
    if not road_available:
        missing = origin if not orig_road_ok else destination
        await websocket.send_json({
            "event": "PROVIDER_ERROR",
            "session_id": session_id,
            "provider": ROAD_ESTIMATE_PROVIDER,
            "error": f"Ônibus não consultado: '{missing}' não tem rodoviária cadastrada.",
            "reason": "no_bus_station",
            "elapsed_ms": 0.0,
        })

    all_outbound_quotes: List[StandardizedQuote] = []
    all_inbound_quotes: List[StandardizedQuote] = []
    leg_results: Dict[str, List[StandardizedQuote]] = {}

    # Tarefas concorrentes: voos no Ignav (e a ClickBus, hoje desligada)
    tasks = []
    if air_available:
        tasks.append(asyncio.create_task(
            _fetch_provider_batch(air_collector.provider_name, air_collector, orig_air_code, dest_air_code, travel_date)
        ))
        if return_date:
            tasks.append(asyncio.create_task(
                _fetch_provider_batch(
                    f"{air_collector.provider_name}_return", air_collector, dest_air_code, orig_air_code, return_date
                )
            ))
    if ROAD_OFFERS_ENABLED and road_available:
        road_collector = ClickBusCollector()
        orig_clickbus = _clickbus_external_id(db, orig_road_term, origin)
        dest_clickbus = _clickbus_external_id(db, dest_road_term, destination)
        tasks.append(asyncio.create_task(
            _fetch_provider_batch("clickbus", road_collector, orig_clickbus, dest_clickbus, travel_date)
        ))
        if return_date:
            tasks.append(asyncio.create_task(
                _fetch_provider_batch("clickbus_return", road_collector, dest_clickbus, orig_clickbus, return_date)
            ))
    # Voo de cada hub da conexão (Hub -> Destino); o ônibus até o hub sai do banco ANTT
    for hub, _hub_road_term, hub_air_term in interline_hubs:
        tasks.append(asyncio.create_task(
            _fetch_provider_batch(
                f"interline_leg2::{hub.id}", air_collector, hub_air_term.official_code, dest_air_code, travel_date
            )
        ))

    try:
        # 2. Ônibus pela ANTT: sai na hora, enquanto os voos ainda estão sendo consultados
        if road_available:
            all_outbound_quotes.extend(await _emit_antt_road_batch(
                websocket, session_id, db, orig_hub, orig_road_term, dest_hub, dest_road_term,
                travel_date, hourly_value, start_search_time,
            ))
            if return_date:
                inbound_road = build_antt_road_quotes(
                    db, dest_hub, dest_road_term, orig_hub, orig_road_term, return_date
                )
                all_inbound_quotes.extend(
                    _process_and_score_quotes(inbound_road, hourly_time_value=hourly_value, db_session=db)
                )

        # 3. Voos (e ClickBus, se ligada) à medida que respondem
        for completed_coroutine in asyncio.as_completed(tasks):
            result = await completed_coroutine
            prov = result["provider"]

            # Voo do hub não é opção Origem -> Destino: guarda para montar a conexão
            if prov.startswith("interline_"):
                if result["success"]:
                    leg_results[prov] = result["quotes"]
                else:
                    logger.warning(f"[interline] {prov} sem voos: {result.get('error')}")
                continue

            if not result["success"]:
                await websocket.send_json({
                    "event": "PROVIDER_ERROR",
                    "session_id": session_id,
                    "provider": prov,
                    "error": result["error"],
                    "reason": result.get("reason"),
                    "elapsed_ms": result["elapsed_ms"],
                })
                continue

            processed_quotes = _process_and_score_quotes(
                quotes=result["quotes"],
                hourly_time_value=hourly_value,
                db_session=db,
            )

            if "_return" in prov:
                all_inbound_quotes.extend(processed_quotes)
                continue   # a volta entra nas combinações Hacker Fares, não na lista de ida

            all_outbound_quotes.extend(processed_quotes)
            await websocket.send_json({
                "event": "QUOTE_BATCH",
                "session_id": session_id,
                "provider": prov,
                "modal": result["modal"],
                "count": len(processed_quotes),
                "quotes": [q.to_dict() for q in processed_quotes],
                "elapsed_ms": result["elapsed_ms"],
            })

        # Referência histórica rodoviária (ANTT): faixa por classe, com a projeção do mês.
        try:
            references = build_road_references(
                db,
                origin_city=orig_hub.name,
                origin_state=orig_hub.state,
                destination_city=dest_hub.name,
                destination_state=dest_hub.state,
                travel_date=travel_date,
            )
            if references:
                await websocket.send_json({
                    "event": "ROAD_REFERENCE",
                    "session_id": session_id,
                    "source": "antt_monitriip",
                    "reference_only": True,
                    "origin": origin,
                    "destination": destination,
                    "references": references,
                })
        except Exception as ref_err:
            logger.warning(f"Falha ao montar referência rodoviária ANTT: {ref_err}")

        # 4. Conexão Ônibus (ANTT) + Voo (Ignav)
        if interline_hubs:
            try:
                interline_quotes = _build_interline_quotes(
                    origin=origin,
                    destination=destination,
                    orig_hub=orig_hub,
                    orig_road_term=orig_road_term,
                    interline_hubs=interline_hubs,
                    leg_results=leg_results,
                    hourly_value=hourly_value,
                    db=db,
                )
                if interline_quotes:
                    all_outbound_quotes.extend(interline_quotes)
                    await websocket.send_json({
                        "event": "QUOTE_BATCH",
                        "session_id": session_id,
                        "provider": INTERLINER_PROVIDER,
                        "modal": "ROAD_AIR",
                        "is_simulated": True,
                        "count": len(interline_quotes),
                        "quotes": [q.to_dict() for q in interline_quotes],
                        "elapsed_ms": round((time.perf_counter() - start_search_time) * 1000, 1),
                    })
            except Exception as il_err:
                logger.warning(f"Erro ao montar conexões ônibus + voo: {il_err}", exc_info=True)

        # 5. Combinações Hacker Fares (se houver bilhetes de volta)
        if return_date and all_outbound_quotes and all_inbound_quotes:
            try:
                hacker_combinations = generate_hacker_fares(
                    outbound_quotes=all_outbound_quotes,
                    inbound_quotes=all_inbound_quotes,
                    hourly_time_value=hourly_value,
                )
                if hacker_combinations:
                    await websocket.send_json({
                        "event": "HACKER_FARES",
                        "session_id": session_id,
                        "count": len(hacker_combinations),
                        "combinations": [c.to_dict() for c in hacker_combinations],
                    })
            except Exception as hf_err:
                logger.warning(f"Erro ao gerar combinações Hacker Fares: {hf_err}")

        # 6. Persistência com Smart Cache TTL (o pipeline descarta estimativas e conexões)
        try:
            if all_outbound_quotes:
                process_and_persist_quotes(all_outbound_quotes, db)
            if all_inbound_quotes:
                process_and_persist_quotes(all_inbound_quotes, db)
        except Exception as db_err:
            db.rollback()
            logger.warning(f"Erro não fatal ao salvar cotações no cache: {db_err}")

        # 7. Emite SEARCH_COMPLETED com estatísticas finais
        total_search_elapsed = round((time.perf_counter() - start_search_time) * 1000, 1)
        cheapest_price = (
            min(q.total_effective_price for q in all_outbound_quotes)
            if all_outbound_quotes
            else 0.0
        )
        scores = [q.trivio_score for q in all_outbound_quotes if q.trivio_score is not None]
        best_trivio_score = min(scores) if scores else 0.0

        await websocket.send_json({
            "event": "SEARCH_COMPLETED",
            "session_id": session_id,
            "total_quotes": len(all_outbound_quotes),
            "total_elapsed_ms": total_search_elapsed,
            "cheapest_effective_price": round(cheapest_price, 2),
            "best_trivio_score": round(best_trivio_score, 1),
            # Há estimativas (ônibus ANTT ou conexão) entre os resultados?
            "is_simulated": any(q.route_metadata.get("is_simulated") for q in all_outbound_quotes),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    finally:
        # Busca cancelada ou com erro: não deixa coletores rodando em segundo plano
        for task in tasks:
            if not task.done():
                task.cancel()


# ==============================================================================
# 5. Endpoint WebSocket do FastAPI
# ==============================================================================

@router.websocket("/ws/search")
async def websocket_search_endpoint(websocket: WebSocket):
    """Endpoint WebSocket para orquestração concorrente e streaming de passagens."""
    # CORS não vale para WebSocket: sem esta checagem, qualquer site poderia abrir buscas
    # (e gastar o Ignav) pelo navegador de quem o visita. Clientes sem Origin (CLI, testes) passam.
    origin_header = websocket.headers.get("origin")
    if origin_header and origin_header not in settings.allowed_origins():
        logger.warning(f"WebSocket recusado para a origem {origin_header!r}.")
        await websocket.close(code=1008)
        return
    await websocket.accept()
    logger.info("Cliente conectado ao WebSocket de busca.")

    active_task: Optional[asyncio.Task] = None

    try:
        # Se houver parâmetros de query string na URL inicial de conexão, dispara busca imediata
        query_params = dict(websocket.query_params)
        if query_params.get("origin") and query_params.get("destination") and query_params.get("date"):
            active_task = asyncio.create_task(orchestrate_search(websocket, query_params))

        # Loop de mensagens do cliente
        while True:
            raw_text = await websocket.receive_text()
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Payload JSON inválido"})
                continue

            action = payload.get("action")

            if action == "ping":
                await websocket.send_json({
                    "event": "pong",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                continue

            if action == "start_search":
                # Cancela busca anterior se estiver rodando
                if active_task and not active_task.done():
                    active_task.cancel()
                    try:
                        await active_task
                    except asyncio.CancelledError:
                        pass

                active_task = asyncio.create_task(orchestrate_search(websocket, payload))
                continue

            if action == "cancel":
                if active_task and not active_task.done():
                    active_task.cancel()
                    logger.info("Busca cancelada a pedido do cliente.")
                continue

            await websocket.send_json({"error": f"Ação desconhecida: {action}"})

    except WebSocketDisconnect:
        logger.info("Cliente desconectou do WebSocket.")
        if active_task and not active_task.done():
            active_task.cancel()
    except Exception as exc:
        logger.error(f"Erro inesperado no WebSocket: {exc}", exc_info=True)
        if active_task and not active_task.done():
            active_task.cancel()
