"""Canal de Streaming em Tempo Real via WebSockets (/ws/search).

Responsável por:
1. Orquestração concorrente de múltiplos coletores assíncronos (Aéreo, Rodoviário e Intermodal).
2. Streaming progressivo: emite eventos QUOTE_BATCH para o frontend à medida que cada
   provedor responde, sem bloquear a interface esperando o mais lento ("pipocamento" de cards).
3. Resiliência por isolamento (Bulkhead): se um provedor falhar ou sofrer timeout, emite
   PROVIDER_ERROR sem interromper os demais.
4. Geração em tempo real de combinações Hacker Fares (quando há data de volta informada).
5. Consolidação e persistência automática no cache com TTL dinâmico ao finalizar.
6. Cancelamento gracioso de tarefas pendentes em caso de desconexão do cliente.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.analyzers.interliner import build_virtual_interline_routes
from app.analyzers.price_thermometer import enrich_quotes_batch_with_thermometer
from app.analyzers.route_evaluator import enrich_quote_with_evaluation
from app.analyzers.trivio_scorer import generate_hacker_fares, score_and_rank_quotes
from app.cache.deduplicator import deduplicate_quotes
from app.collectors.air.amadeus import AmadeusCollector
from app.collectors.base import StandardizedQuote
from app.collectors.road.clickbus import ClickBusCollector
from app.database import SessionLocal
from app.services.quote_pipeline import process_and_persist_quotes

logger = logging.getLogger("trivio.api.websocket")

router = APIRouter(tags=["Real-Time Streaming"])


# ==============================================================================
# 1. Estruturas de Tarefas e Coleta Concorrente
# ==============================================================================

async def _fetch_provider_batch(
    collector_name: str,
    collector_instance: Any,
    origin: str,
    destination: str,
    travel_date: str,
) -> Dict[str, Any]:
    """Executa a coleta de um único provedor isolando exceções e medindo latência."""
    start_time = time.perf_counter()
    try:
        quotes = await collector_instance.safe_fetch(origin, destination, travel_date)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
        return {
            "success": True,
            "provider": collector_name,
            "modal": collector_instance.modal,
            "quotes": quotes,
            "elapsed_ms": elapsed_ms,
            "error": None,
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
        }


def _process_and_score_quotes(
    quotes: List[StandardizedQuote],
    hourly_time_value: float,
    db_session: Optional[Session] = None,
) -> List[StandardizedQuote]:
    """Aplica a esteira de inteligência: deduplicação, route evaluator, termômetro e Trivio Score."""
    if not quotes:
        return []

    # 1. Deduplicação
    deduped = deduplicate_quotes(quotes, attach_sellers_info=True)

    # 2. Prós, Contras e Contratempos
    for q in deduped:
        enrich_quote_with_evaluation(q)

    # 3. Termômetro de Preço
    enrich_quotes_batch_with_thermometer(deduped, db=db_session)

    # 4. Cálculo do Trivio Score e ordenação
    ranked = score_and_rank_quotes(deduped, hourly_time_value=hourly_time_value)
    return ranked


# ==============================================================================
# 2. Orquestrador Principal de Busca em Streaming
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
    hourly_value = float(params.get("hourly_value", 35.0))
    enable_interlining = bool(params.get("enable_virtual_interlining", True))

    start_search_time = time.perf_counter()

    # 1. Emite SEARCH_STARTED
    await websocket.send_json({
        "event": "SEARCH_STARTED",
        "session_id": session_id,
        "origin": origin,
        "destination": destination,
        "travel_date": travel_date,
        "return_date": return_date,
        "providers": ["clickbus", "amadeus", "virtual_interlining"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    # Instancia coletores
    road_collector = ClickBusCollector()
    air_collector = AmadeusCollector()

    # Cria sessão de banco para termômetro e persistência
    db = SessionLocal()

    all_outbound_quotes: List[StandardizedQuote] = []
    all_inbound_quotes: List[StandardizedQuote] = []
    road_outbound: List[StandardizedQuote] = []
    air_outbound: List[StandardizedQuote] = []

    # Tarefas assíncronas concorrentes de Ida
    tasks = {
        asyncio.create_task(
            _fetch_provider_batch("clickbus", road_collector, origin, destination, travel_date)
        ): "clickbus",
        asyncio.create_task(
            _fetch_provider_batch("amadeus", air_collector, origin, destination, travel_date)
        ): "amadeus",
    }

    # Se houver data de volta, adiciona tarefas concorrentes de Volta para Hacker Fares
    if return_date:
        tasks[asyncio.create_task(
            _fetch_provider_batch("clickbus_return", road_collector, destination, origin, return_date)
        )] = "clickbus_return"
        tasks[asyncio.create_task(
            _fetch_provider_batch("amadeus_return", air_collector, destination, origin, return_date)
        )] = "amadeus_return"

    try:
        # 2. Processa cada lote progressivamente à medida que responde (as_completed)
        for completed_coroutine in asyncio.as_completed(tasks.keys()):
            result = await completed_coroutine
            prov = result["provider"]

            if not result["success"]:
                await websocket.send_json({
                    "event": "PROVIDER_ERROR",
                    "session_id": session_id,
                    "provider": prov,
                    "error": result["error"],
                    "elapsed_ms": result["elapsed_ms"],
                })
                continue

            raw_quotes = result["quotes"]

            # Processa e ranqueia lote
            processed_quotes = _process_and_score_quotes(
                quotes=raw_quotes,
                hourly_time_value=hourly_value,
                db_session=db,
            )

            # Rastreia cotações para sínteses posteriores
            if "_return" in prov:
                all_inbound_quotes.extend(processed_quotes)
            else:
                all_outbound_quotes.extend(processed_quotes)
                if result["modal"] == "ROAD":
                    road_outbound.extend(processed_quotes)
                elif result["modal"] == "AIR":
                    air_outbound.extend(processed_quotes)

            # Transmite QUOTE_BATCH imediatamente para o cliente
            await websocket.send_json({
                "event": "QUOTE_BATCH",
                "session_id": session_id,
                "provider": prov,
                "modal": result["modal"],
                "count": len(processed_quotes),
                "quotes": [q.to_dict() for q in processed_quotes],
                "elapsed_ms": result["elapsed_ms"],
            })

        # 3. Síntese de Virtual Interlining (se habilitado e viável)
        if enable_interlining and road_outbound and air_outbound:
            try:
                interline_routes = build_virtual_interline_routes(
                    origin_id=origin,
                    destination_id=destination,
                    leg1_quotes=road_outbound,
                    leg2_quotes=air_outbound,
                    hourly_time_value=hourly_value,
                )
                if interline_routes:
                    interline_quotes = [r.to_standardized_quote() for r in interline_routes]
                    all_outbound_quotes.extend(interline_quotes)
                    await websocket.send_json({
                        "event": "QUOTE_BATCH",
                        "session_id": session_id,
                        "provider": "trivio_interliner",
                        "modal": "ROAD_AIR",
                        "count": len(interline_quotes),
                        "quotes": [q.to_dict() for q in interline_quotes],
                        "elapsed_ms": round((time.perf_counter() - start_search_time) * 1000, 1),
                    })
            except Exception as il_err:
                logger.warning(f"Erro ao sintetizar rotas de interlining: {il_err}")

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

        # 5. Persistência em Banco de Dados com Smart Cache TTL
        try:
            if all_outbound_quotes:
                process_and_persist_quotes(all_outbound_quotes, db)
            if all_inbound_quotes:
                process_and_persist_quotes(all_inbound_quotes, db)
        except Exception as db_err:
            logger.warning(f"Erro não fatal ao salvar cotações no cache: {db_err}")

        # 6. Emite SEARCH_COMPLETED com estatísticas finais
        total_time_ms = round((time.perf_counter() - start_search_time) * 1000, 1)
        cheapest_price = (
            min((q.total_effective_price for q in all_outbound_quotes), default=0.0)
            if all_outbound_quotes
            else 0.0
        )
        best_score = (
            min((q.trivio_score for q in all_outbound_quotes if q.trivio_score), default=0.0)
            if all_outbound_quotes
            else 0.0
        )

        await websocket.send_json({
            "event": "SEARCH_COMPLETED",
            "session_id": session_id,
            "total_quotes": len(all_outbound_quotes),
            "total_elapsed_ms": total_time_ms,
            "cheapest_effective_price": cheapest_price,
            "best_trivio_score": best_score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    finally:
        # Garante cancelamento de tarefas que ainda estejam rodando
        for t in tasks.keys():
            if not t.done():
                t.cancel()
        db.close()


# ==============================================================================
# 3. Endpoint WebSocket FastAPI
# ==============================================================================

@router.websocket("/ws/search")
@router.websocket("/api/v1/ws/search")
async def websocket_search_endpoint(websocket: WebSocket) -> None:
    """Endpoint WebSocket para orquestração de buscas intermodais em tempo real."""
    await websocket.accept()
    logger.info("Cliente WebSocket conectado para busca em tempo real.")

    try:
        # Verifica se parâmetros foram fornecidos via query string
        query_params = dict(websocket.query_params)
        if query_params.get("origin") and query_params.get("destination") and query_params.get("date"):
            # Inicia busca automaticamente
            await orchestrate_search(websocket, query_params)

        # Loop de escuta de mensagens do cliente (start_search, ping, cancelamento)
        while True:
            raw_msg = await websocket.receive_text()
            try:
                data = json.loads(raw_msg)
            except json.JSONDecodeError:
                await websocket.send_json({"event": "ERROR", "message": "JSON inválido."})
                continue

            action = data.get("action", "").lower()

            if action == "ping":
                await websocket.send_json({"event": "pong", "timestamp": datetime.now(timezone.utc).isoformat()})
            elif action in ("start_search", "search"):
                await orchestrate_search(websocket, data)
            elif action == "cancel":
                await websocket.send_json({"event": "SEARCH_CANCELLED"})
            else:
                await websocket.send_json({"event": "ERROR", "message": f"Ação desconhecida: '{action}'"})

    except WebSocketDisconnect:
        logger.info("Cliente WebSocket desconectado normalmente.")
    except Exception as exc:
        logger.error(f"Erro inesperado na conexão WebSocket: {exc}", exc_info=True)
