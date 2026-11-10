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
from datetime import datetime, timedelta, timezone
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from geopy.distance import geodesic
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
from app.collectors.road.clickbus import ClickBusCollector, CLICKBUS_SLUG_MAP
from app.config import settings
from app.database import SessionLocal
from app.geo.catchment import CatchmentFinder
from app.geo.entity_resolver import EntityResolver
from app.models import Hub, Terminal, ModalType, ProviderMapping
from app.services.quote_pipeline import QuotePipeline, process_and_persist_quotes

logger = logging.getLogger("trivio.api.websocket")

router = APIRouter(tags=["Real-Time Streaming"])

# Identificador do provedor de cotações simuladas (estimativas sem preço real de mercado)
SIMULATION_PROVIDER = "trivio_simulation"
SIMULATION_NOTICE = (
    "Estimativa simulada pelo Trivio a partir da distância geodésica: não é uma tarifa "
    "real de mercado nem está disponível para compra."
)
MAX_INTERLINE_HUBS = 2

# Ônibus com preço real está DESLIGADO: o ClickBus recusa o acesso automatizado (403) e ainda
# não há fonte autorizada. Enquanto isso, o ônibus aparece só como referência histórica da ANTT
# (evento ROAD_REFERENCE), fora do ranking. Ligar de novo exige uma fonte autorizada.
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
        road_term = term if term.modal == ModalType.ROAD else next((t for t in hub.terminals if t.modal == ModalType.ROAD), None)
        air_term = term if term.modal == ModalType.AIR else next((t for t in hub.terminals if t.modal == ModalType.AIR), None)
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
        if not road_term or not air_term:
            continue
        seen_hubs.add(hub.id)
        selected.append((hub, road_term, air_term))
        if len(selected) >= max_hubs:
            break
    return selected


# ==============================================================================
# 2. Cotações Simuladas (Modo Demonstração)
# ==============================================================================

def get_corridor_quotes(
    origin: str,
    dest: str,
    date_str: str,
    db: Optional[Session] = None,
) -> List[StandardizedQuote]:
    """Gera cotações SIMULADAS a partir da distância geodésica entre os hubs.

    Usadas apenas quando nenhum coletor real retorna dados (WAF ou credenciais ausentes).
    Todas são marcadas com provider_source='trivio_simulation' e route_metadata['is_simulated']=True,
    usam operadoras genéricas (nunca nomes de empresas reais) e não são persistidas no banco.
    """
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True

    try:
        try:
            dep_base = datetime.strptime(date_str, "%Y-%m-%d")
        except Exception:
            dep_base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=7)

        resolver = EntityResolver(db)
        orig_hub, orig_road_term, orig_air_term = _resolve_hub_and_terminals(origin, db, resolver)
        dest_hub, dest_road_term, dest_air_term = _resolve_hub_and_terminals(dest, db, resolver)

        # Distância geodésica real entre os hubs
        if orig_hub and dest_hub:
            dist_km = round(
                geodesic((orig_hub.latitude, orig_hub.longitude), (dest_hub.latitude, dest_hub.longitude)).kilometers,
                1,
            )
        else:
            dist_km = 350.0

        # Fator de sinuosidade rodoviária no relevo brasileiro (~1.18x)
        road_km = max(40.0, round(dist_km * 1.18, 1))

        # Duração rodoviária baseada em velocidade comercial de ônibus de linha (~75 km/h)
        road_duration_min = max(45, int((road_km / 75.0) * 60))
        door_to_door_road = road_duration_min + settings.BUS_TERMINAL_CHECKIN_BUFFER_MINUTES

        orig_road_code = orig_road_term.official_code if orig_road_term else f"{origin.upper()}-ROD"
        dest_road_code = dest_road_term.official_code if dest_road_term else f"{dest.upper()}-ROD"

        # Tarifas estimadas por quilômetro (heurística, não tabela ANTT oficial)
        base_semi = round(max(32.0, road_km * 0.38 + 12.0), 2)
        base_leito = round(max(65.0, road_km * 0.62 + 20.0), 2)

        def _sim_meta(extra: Dict[str, Any]) -> Dict[str, Any]:
            meta = {
                "is_simulated": True,
                "simulation_notice": SIMULATION_NOTICE,
                "is_direct": True,
            }
            meta.update(extra)
            return meta

        quotes: List[StandardizedQuote] = []

        # 1. Ônibus Semi-Leito Diurno (estimativa)
        semi_dep = dep_base.replace(hour=8, minute=30)
        quotes.append(
            StandardizedQuote(
                origin_external_id=orig_road_code,
                destination_external_id=dest_road_code,
                modal="ROAD",
                provider_source=SIMULATION_PROVIDER,
                carrier_name="Ônibus Semi-Leito (estimativa)",
                service_class="SEMI_LEITO",
                departure_time=semi_dep,
                arrival_time=semi_dep + timedelta(minutes=road_duration_min),
                duration_minutes=road_duration_min,
                door_to_door_duration_minutes=door_to_door_road,
                base_price=base_semi,
                route_metadata=_sim_meta({
                    "stops_count": 1 if road_km > 200 else 0,
                    "road_distance_km": road_km,
                }),
            )
        )

        # 2. Ônibus Leito Cama Noturno (estimativa)
        leito_dep_hour = 23 if road_duration_min >= 270 else 18
        leito_dep_min = 0 if road_duration_min >= 270 else 30
        leito_dep = dep_base.replace(hour=leito_dep_hour, minute=leito_dep_min)
        quotes.append(
            StandardizedQuote(
                origin_external_id=orig_road_code,
                destination_external_id=dest_road_code,
                modal="ROAD",
                provider_source=SIMULATION_PROVIDER,
                carrier_name="Ônibus Leito-Cama (estimativa)",
                service_class="LEITO_CAMA",
                departure_time=leito_dep,
                arrival_time=leito_dep + timedelta(minutes=road_duration_min),
                duration_minutes=road_duration_min,
                door_to_door_duration_minutes=door_to_door_road,
                base_price=base_leito,
                route_metadata=_sim_meta({
                    "stops_count": 0,
                    "road_distance_km": road_km,
                }),
            )
        )

        # 3. Voo direto (se ambos os hubs tiverem aeroporto e a distância for superior a 100km)
        if orig_air_term and dest_air_term and dist_km >= 100.0:
            orig_air_code = orig_air_term.official_code or "AER"
            dest_air_code = dest_air_term.official_code or "AER"

            # Duração de voo (cruzeiro ~650 km/h + 25 min subida/descida)
            flight_duration_min = max(45, int((dist_km / 650.0) * 60) + 25)
            base_air = round(max(240.0, dist_km * 0.65 + 150.0), 2)
            tax_air = round(max(35.0, dist_km * 0.08 + 25.0), 2)
            air_dep = dep_base.replace(hour=14, minute=0)

            quotes.append(
                StandardizedQuote(
                    origin_external_id=orig_air_code,
                    destination_external_id=dest_air_code,
                    modal="AIR",
                    provider_source=SIMULATION_PROVIDER,
                    carrier_name="Voo direto (estimativa)",
                    service_class="ECONOMY",
                    departure_time=air_dep,
                    arrival_time=air_dep + timedelta(minutes=flight_duration_min),
                    duration_minutes=flight_duration_min,
                    door_to_door_duration_minutes=flight_duration_min + settings.AIRPORT_DOMESTIC_CHECKIN_BUFFER_MINUTES,
                    base_price=base_air,
                    taxes_price=tax_air,
                    baggage_fee_estimate=settings.DEFAULT_FLIGHT_BAGGAGE_FEE_ESTIMATE_BRL,
                    route_metadata=_sim_meta({
                        "stops_count": 0,
                        "flight_distance_km": dist_km,
                    }),
                )
            )

        return quotes

    finally:
        if own_session:
            db.close()


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


async def _fetch_interline_pair(
    hub_id: int,
    road_collector: Any,
    origin_road: str,
    hub_road: str,
    air_collector: Any,
    hub_air: str,
    dest_air: str,
    travel_date: str,
) -> Dict[str, Any]:
    """Ônibus origem -> hub e, só se houver ônibus, voo hub -> destino."""
    leg1 = await _fetch_provider_batch(f"interline_leg1::{hub_id}", road_collector, origin_road, hub_road, travel_date)
    legs = [leg1]
    if leg1["success"] and leg1["quotes"]:
        legs.append(await _fetch_provider_batch(
            f"interline_leg2::{hub_id}", air_collector, hub_air, dest_air, travel_date
        ))
    else:
        logger.info(f"[interline] Hub {hub_id}: sem ônibus até o hub; voo não consultado (evita request paga).")
    return {"provider": f"interline_pair::{hub_id}", "legs": legs}


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

    # 1. Terminais canônicos, custo/tempo de transfer urbano e tempo porta a porta
    if db_session is not None:
        for q in quotes:
            QuotePipeline.enrich_quote(q, db_session)

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
    travel_date: str,
    interline_hubs: List[Tuple[Hub, Terminal, Terminal]],
    leg_results: Dict[str, List[StandardizedQuote]],
    simulation_mode: bool,
    hourly_value: float,
    db: Session,
) -> List[StandardizedQuote]:
    """Constrói rotas Ônibus (Origem -> Hub) + Voo (Hub -> Destino) para cada hub candidato."""
    all_routes: List[VirtualInterlineRoute] = []

    for hub, hub_road_term, hub_air_term in interline_hubs:
        leg1_raw = list(leg_results.get(f"interline_leg1::{hub.id}", []))
        leg2_raw = list(leg_results.get(f"interline_leg2::{hub.id}", []))

        # No modo simulação, as pernas também são estimadas (nunca misturamos com dados reais)
        if simulation_mode:
            if not leg1_raw:
                leg1_raw = [
                    q for q in get_corridor_quotes(origin, hub_road_term.official_code or hub.name, travel_date, db=db)
                    if q.modal == "ROAD"
                ]
            if not leg2_raw:
                leg2_raw = [
                    q for q in get_corridor_quotes(hub_air_term.official_code or hub.name, destination, travel_date, db=db)
                    if q.modal == "AIR"
                ]

        hub_codes = {t.official_code for t in hub.terminals if t.official_code}
        terminal_lookup = {t.official_code: t for t in hub.terminals if t.official_code}

        # Mantém apenas pernas que de fato chegam ao / partem do hub intermediário
        leg1 = [
            q for q in _process_and_score_quotes(leg1_raw, hourly_value, db)
            if q.modal == "ROAD" and q.canonical_destination_id in hub_codes
        ]
        leg2 = [
            q for q in _process_and_score_quotes(leg2_raw, hourly_value, db)
            if q.modal == "AIR" and q.canonical_origin_id in hub_codes
        ]
        if not leg1 or not leg2:
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
        legs_simulated = any(
            (getattr(leg, "route_metadata", None) or {}).get("is_simulated") for leg in (route.leg1, route.leg2)
        )
        if legs_simulated:
            quote.route_metadata["is_simulated"] = True
            quote.route_metadata["simulation_notice"] = SIMULATION_NOTICE
        interline_quotes.append(quote)
    return interline_quotes


# ==============================================================================
# 4. Orquestrador Principal de Busca em Streaming
# ==============================================================================

async def orchestrate_search(
    websocket: WebSocket,
    params: Dict[str, Any],
) -> None:
    """Executa o pipeline concorrente de busca e transmite eventos em tempo real."""
    session_id = uuid.uuid4().hex[:12]
    origin = str(params.get("origin", "")).strip()
    destination = str(params.get("destination", "")).strip()
    travel_date = str(params.get("date", "")).strip()
    return_date = params.get("return_date")
    hourly_value = float(params.get("hourly_value", settings.DEFAULT_HOURLY_TIME_VALUE_BRL))
    radius_km = float(params.get("radius_km") or settings.DEFAULT_CATCHMENT_RADIUS_KM)
    enable_interlining = str(params.get("enable_virtual_interlining", True)).strip().lower() not in ("false", "0", "no")

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
    for label, value in (("ida", travel_date), ("volta", return_date)):
        if label == "volta" and not value:
            continue
        try:
            datetime.strptime(str(value), "%Y-%m-%d")
        except ValueError:
            return f"Data de {label} inválida. Utilize o formato AAAA-MM-DD."
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
    """Valida os parâmetros e executa a busca concorrente com streaming de eventos."""
    resolver = EntityResolver(db)
    orig_hub, orig_road_term, orig_air_term = _resolve_hub_and_terminals(origin, db, resolver)
    dest_hub, dest_road_term, dest_air_term = _resolve_hub_and_terminals(destination, db, resolver)

    validation_error = _validate_search_params(origin, destination, travel_date, return_date, orig_hub, dest_hub)
    if validation_error:
        await websocket.send_json({
            "event": "SEARCH_ERROR",
            "session_id": session_id,
            "message": validation_error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return

    # 1. Emite SEARCH_STARTED
    await websocket.send_json({
        "event": "SEARCH_STARTED",
        "session_id": session_id,
        "origin": origin,
        "destination": destination,
        "travel_date": travel_date,
        "return_date": return_date,
        "providers": (
            (["clickbus"] if ROAD_OFFERS_ENABLED else [])
            + [get_air_quote_service().provider_name]
            + (["virtual_interlining"] if ROAD_OFFERS_ENABLED else [])
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    air_collector = get_air_quote_service()

    orig_clickbus = _clickbus_external_id(db, orig_road_term, origin) if ROAD_OFFERS_ENABLED else ""
    dest_clickbus = _clickbus_external_id(db, dest_road_term, destination) if ROAD_OFFERS_ENABLED else ""
    orig_air_code = orig_air_term.official_code if orig_air_term else origin.upper()
    dest_air_code = dest_air_term.official_code if dest_air_term else destination.upper()

    # Hubs intermediários para Virtual Interlining (só faz sentido se o destino tiver aeroporto)
    interline_hubs: List[Tuple[Hub, Terminal, Terminal]] = []
    if ROAD_OFFERS_ENABLED and enable_interlining and dest_air_term:
        interline_hubs = _find_interline_hubs(orig_hub, dest_hub, db, radius_km)

    all_outbound_quotes: List[StandardizedQuote] = []
    all_inbound_quotes: List[StandardizedQuote] = []
    leg_results: Dict[str, List[StandardizedQuote]] = {}

    # Tarefas assíncronas concorrentes de Ida
    tasks = [
        asyncio.create_task(
            _fetch_provider_batch(air_collector.provider_name, air_collector, orig_air_code, dest_air_code, travel_date)
        ),
    ]
    if ROAD_OFFERS_ENABLED:
        road_collector = ClickBusCollector()
        tasks.insert(0, asyncio.create_task(
            _fetch_provider_batch("clickbus", road_collector, orig_clickbus, dest_clickbus, travel_date)
        ))

    # Se houver data de volta, adiciona tarefas concorrentes de Volta para Hacker Fares
    if return_date and ROAD_OFFERS_ENABLED:
        tasks.append(asyncio.create_task(
            _fetch_provider_batch("clickbus_return", road_collector, dest_clickbus, orig_clickbus, return_date)
        ))
        tasks.append(asyncio.create_task(
            _fetch_provider_batch(f"{air_collector.provider_name}_return", air_collector, dest_air_code, orig_air_code, return_date)
        ))

    # Pernas do Virtual Interlining: Ônibus Origem -> Hub e Voo Hub -> Destino
    # O voo é pago: só é consultado depois que o ônibus até o hub trouxe resultado.
    # Sem a perna rodoviária a rota híbrida não se forma, e a request seria desperdiçada.
    for hub, hub_road_term, hub_air_term in interline_hubs:
        hub_clickbus = _clickbus_external_id(db, hub_road_term, hub_road_term.official_code or hub.name)
        tasks.append(asyncio.create_task(
            _fetch_interline_pair(
                hub.id, road_collector, orig_clickbus, hub_clickbus,
                air_collector, hub_air_term.official_code, dest_air_code, travel_date,
            )
        ))

    try:
        # 2. Processa cada lote progressivamente à medida que responde (as_completed)
        for completed_coroutine in asyncio.as_completed(tasks):
            result = await completed_coroutine
            prov = result["provider"]

            # Pernas de interlining não são opções Origem -> Destino: apenas armazena
            if prov.startswith("interline_"):
                for leg in result.get("legs", [result]):
                    if leg["success"]:
                        leg_results[leg["provider"]] = leg["quotes"]
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
            else:
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

        # Referência histórica rodoviária (ANTT): não entra no ranking, só informa a faixa de preço.
        if orig_hub and dest_hub:
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

        # Modo simulação: nenhum coletor real retornou dados (ex: WAF 403 ou sem credenciais no .env)
        simulation_mode = not all_outbound_quotes
        if simulation_mode:
            logger.warning(
                f"[{session_id}] Nenhum coletor real retornou cotações para {origin}->{destination}. "
                "Exibindo estimativas simuladas (não persistidas)."
            )
            simulated = get_corridor_quotes(origin, destination, travel_date, db=db)
            processed_sample = _process_and_score_quotes(simulated, hourly_time_value=hourly_value, db_session=db)
            all_outbound_quotes.extend(processed_sample)
            await websocket.send_json({
                "event": "QUOTE_BATCH",
                "session_id": session_id,
                "provider": SIMULATION_PROVIDER,
                "modal": "HYBRID",
                "is_simulated": True,
                "count": len(processed_sample),
                "quotes": [q.to_dict() for q in processed_sample],
                "elapsed_ms": round((time.perf_counter() - start_search_time) * 1000, 1),
            })

        if return_date and not all_inbound_quotes:
            inbound_sample = get_corridor_quotes(destination, origin, return_date, db=db)
            processed_inbound = _process_and_score_quotes(inbound_sample, hourly_time_value=hourly_value, db_session=db)
            all_inbound_quotes.extend(processed_inbound)

        # 3. Síntese de Virtual Interlining (Ônibus Origem -> Hub + Voo Hub -> Destino)
        if interline_hubs:
            try:
                interline_quotes = _build_interline_quotes(
                    origin=origin,
                    destination=destination,
                    travel_date=travel_date,
                    interline_hubs=interline_hubs,
                    leg_results=leg_results,
                    simulation_mode=simulation_mode,
                    hourly_value=hourly_value,
                    db=db,
                )
                if interline_quotes:
                    all_outbound_quotes.extend(interline_quotes)
                    await websocket.send_json({
                        "event": "QUOTE_BATCH",
                        "session_id": session_id,
                        "provider": "trivio_interliner",
                        "modal": "ROAD_AIR",
                        "is_simulated": any(q.route_metadata.get("is_simulated") for q in interline_quotes),
                        "count": len(interline_quotes),
                        "quotes": [q.to_dict() for q in interline_quotes],
                        "elapsed_ms": round((time.perf_counter() - start_search_time) * 1000, 1),
                    })
            except Exception as il_err:
                logger.warning(f"Erro ao sintetizar rotas de interlining: {il_err}", exc_info=True)

        # 4. Geração de Hacker Fares (se houver bilhetes de volta)
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

        # 5. Persistência com Smart Cache TTL (o pipeline descarta simulações e rotas sintetizadas)
        try:
            if all_outbound_quotes:
                process_and_persist_quotes(all_outbound_quotes, db)
            if all_inbound_quotes:
                process_and_persist_quotes(all_inbound_quotes, db)
        except Exception as db_err:
            db.rollback()
            logger.warning(f"Erro não fatal ao salvar cotações no cache: {db_err}")

        # 6. Emite SEARCH_COMPLETED com estatísticas finais
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
            "is_simulated": simulation_mode,
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
