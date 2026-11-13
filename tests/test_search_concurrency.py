"""A busca sob concorrência: muitas buscas ao mesmo tempo e o event loop livre durante a esteira.

Cobrem dois problemas que a suíte não pegava:
- cada busca segurava a sua conexão enquanto as threads de _run_db_work pediam outra, e o
  pool esgotava (TimeoutError após 30 s com ~16 buscas simultâneas);
- o trabalho síncrono de banco rodava no event loop e atrasava pings e outros clientes.
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import websocket as ws
from app.api.main import app
from app.collectors.base import StandardizedQuote
from app.database import engine

ROUTE_SP_RJ = "SAO PAULO-SP|RIO DE JANEIRO-RJ"


@pytest.fixture
def sp_rj_bus(antt_series):
    """Vendas ANTT de SP->RJ: o lote de ônibus passa pela esteira (e pelo banco)."""
    antt_series.add(ROUTE_SP_RJ, "SEMI_LEITO", "2026-11", "2026-09", 100, 150.0)
    antt_series.add(ROUTE_SP_RJ, "LEITO", "2026-11", "2026-09", 40, 260.0)
    return antt_series


def _flights(origin, destination, travel_date, count=10):
    day = datetime.strptime(travel_date, "%Y-%m-%d")
    return [
        StandardizedQuote(
            origin_external_id=origin, destination_external_id=destination, modal="AIR",
            provider_source="ignav", carrier_name=("LATAM", "GOL", "AZUL")[i % 3], service_class="ECONOMY",
            departure_time=day + timedelta(hours=6 + i), arrival_time=day + timedelta(hours=7 + i),
            duration_minutes=60, base_price=300.0 + 10 * i, taxes_price=40.0,
            route_metadata={"is_direct": True},
        )
        for i in range(count)
    ]


class FakeAir:
    provider_name = "ignav"
    modal = "AIR"

    async def get_quotes(self, origin, destination, travel_date):
        await asyncio.sleep(0.05)
        return _flights(origin, destination, travel_date)


class FakeSocket:
    client = ("127.0.0.1", 1)

    def __init__(self):
        self.events = []

    async def send_json(self, data):
        self.events.append(data)


# ==============================================================================
# 1. Muitas buscas ao mesmo tempo com um pool pequeno
# ==============================================================================

SMALL_POOL = {"pool_size": 2, "max_overflow": 1, "pool_timeout": 3}


@pytest.mark.asyncio
async def test_many_simultaneous_searches_do_not_exhaust_a_small_pool(monkeypatch, sp_rj_bus):
    """16 buscas de ida e volta num pool de 3 conexões terminam todas, sem TimeoutError do pool.

    Com o pool de produção (5+10) o mesmo travamento aparecia a partir de ~16 buscas; aqui o pool
    é reduzido para o teste ser rápido. Se uma busca segurar uma conexão enquanto pede outra, as
    3 conexões acabam presas e as demais esperam os 3 s do pool_timeout e falham.
    """
    small_engine = create_engine(str(engine.url), connect_args={"check_same_thread": False}, **SMALL_POOL)
    monkeypatch.setattr(ws, "SessionLocal", sessionmaker(bind=small_engine, autoflush=False))
    monkeypatch.setattr(ws, "get_air_quote_service", lambda: FakeAir())
    monkeypatch.setattr(ws.search_limiter, "allow", lambda key: True)

    sockets = [FakeSocket() for _ in range(16)]
    started = time.perf_counter()
    try:
        results = await asyncio.gather(
            *[
                ws.orchestrate_search(s, {"origin": "SAO", "destination": "RIO", "date": "2026-11-20",
                                          "return_date": "2026-11-27"})
                for s in sockets
            ],
            return_exceptions=True,
        )
    finally:
        small_engine.dispose()
    elapsed = time.perf_counter() - started

    errors = [f"{type(r).__name__}: {r}" for r in results if isinstance(r, Exception)]
    completed = sum(1 for s in sockets if s.events and s.events[-1]["event"] == "SEARCH_COMPLETED")
    print(f"\n16 buscas, pool 2+1: {completed} completas, erros={errors[:3]}, {elapsed:.2f} s")
    assert errors == []
    assert [s.events[-1]["event"] for s in sockets] == ["SEARCH_COMPLETED"] * 16
    # Cada busca recebeu os dois lotes (ônibus da ANTT e voos)
    for s in sockets:
        providers = {e.get("provider") for e in s.events if e["event"] == "QUOTE_BATCH"}
        assert providers == {"antt_projection", "ignav"}
    # Nenhuma busca ficou esperando o pool_timeout
    assert elapsed < SMALL_POOL["pool_timeout"]


# ==============================================================================
# 2. Ping respondido enquanto a busca processa
# ==============================================================================

SLOW_ROAD_SECONDS = 0.6


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_ping_is_answered_while_the_search_is_processing(mock_air, monkeypatch, sp_rj_bus):
    """Com a montagem do ônibus lenta (0,6 s de trabalho síncrono), o ping enviado no meio da
    busca volta na hora. Se esse trabalho rodasse no event loop, o pong só sairia depois dele."""
    mock_air.return_value = []
    original = ws.build_antt_road_quotes

    def slow_build(*args, **kwargs):
        time.sleep(SLOW_ROAD_SECONDS)   # trabalho síncrono pesado (banco, esteira)
        return original(*args, **kwargs)

    monkeypatch.setattr(ws, "build_antt_road_quotes", slow_build)

    arrivals = []   # (evento, momento em que chegou ao cliente)
    with TestClient(app) as client, client.websocket_connect("/ws/search") as socket:
        socket.send_text(json.dumps({"action": "start_search", "origin": "SAO", "destination": "RIO",
                                     "date": "2026-11-20"}))
        first = socket.receive_json()
        assert first["event"] == "SEARCH_STARTED"
        # A busca está agora montando o ônibus (0,6 s): é aqui que o ping chega
        ping_sent = time.perf_counter()
        socket.send_text(json.dumps({"action": "ping"}))
        while True:
            event = socket.receive_json()
            arrivals.append((event.get("event"), event.get("provider"), time.perf_counter() - ping_sent))
            if event.get("event") in ("SEARCH_COMPLETED", "SEARCH_ERROR"):
                break

    names = [(name, provider) for name, provider, _ in arrivals]
    pong_at = next((t for name, _, t in arrivals if name == "pong"), None)
    assert pong_at is not None, f"o ping só foi respondido depois do fim da busca: {names}"
    road_at = next(t for name, provider, t in arrivals if provider == "antt_projection")
    print(f"\nping no meio da busca: pong em {pong_at * 1000:.0f} ms, lote de ônibus em {road_at * 1000:.0f} ms, "
          f"ordem={[n for n, _ in names]}")

    # O pong chega antes do lote de ônibus, e bem antes de o trabalho lento terminar
    assert names.index(("pong", None)) < names.index(("QUOTE_BATCH", "antt_projection"))
    assert pong_at < SLOW_ROAD_SECONDS / 3
    assert road_at >= SLOW_ROAD_SECONDS * 0.8
