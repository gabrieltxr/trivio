"""Unit and Integration tests for Real-Time Search WebSocket (/ws/search)."""

from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.collectors.base import StandardizedQuote


@pytest.fixture
def client():
    """Cliente de teste FastAPI."""
    with TestClient(app) as c:
        yield c


def _create_dummy_quote(modal: str, provider: str, carrier: str, price: float) -> StandardizedQuote:
    """Gera cotação padronizada de teste."""
    now = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    return StandardizedQuote(
        origin_external_id="SAO",
        destination_external_id="RIO",
        modal=modal,
        provider_source=provider,
        carrier_name=carrier,
        service_class="ECONOMY" if modal == "AIR" else "CONVENCIONAL",
        departure_time=now,
        arrival_time=now,
        duration_minutes=120,
        door_to_door_duration_minutes=180,
        base_price=price,
        taxes_price=0.0,
        baggage_fee_estimate=0.0,
        transfer_cost_estimate=25.0,
        route_metadata={"is_direct": True},
    )


def test_websocket_ping_pong(client):
    """Valida heartbeat ping/pong no canal WebSocket."""
    with client.websocket_connect("/ws/search") as ws:
        ws.send_text(json.dumps({"action": "ping"}))
        data = ws.receive_json()
        assert data["event"] == "pong"
        assert "timestamp" in data


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.collectors.air.amadeus.AmadeusCollector.safe_fetch")
def test_websocket_search_orchestration_flow(mock_air, mock_road, client):
    """Valida fluxo completo: SEARCH_STARTED -> QUOTE_BATCH (Road & Air) -> SEARCH_COMPLETED."""
    road_q = _create_dummy_quote("ROAD", "clickbus", "1001", 120.0)
    air_q = _create_dummy_quote("AIR", "amadeus", "LATAM", 350.0)

    mock_road.return_value = [road_q]
    mock_air.return_value = [air_q]

    with client.websocket_connect("/ws/search") as ws:
        ws.send_text(json.dumps({
            "action": "start_search",
            "origin": "SAO",
            "destination": "RIO",
            "date": "2026-11-20",
            "hourly_value": 40.0,
        }))

        # 1. Evento inicial SEARCH_STARTED
        event_started = ws.receive_json()
        assert event_started["event"] == "SEARCH_STARTED"
        assert event_started["origin"] == "SAO"
        assert event_started["destination"] == "RIO"

        # 2. Recebe lotes QUOTE_BATCH (concorrentes - ordem pode variar)
        batch1 = ws.receive_json()
        assert batch1["event"] == "QUOTE_BATCH"
        assert batch1["count"] >= 1

        batch2 = ws.receive_json()
        assert batch2["event"] == "QUOTE_BATCH"
        assert batch2["count"] >= 1

        providers_received = {batch1["provider"], batch2["provider"]}
        assert "clickbus" in providers_received
        assert "amadeus" in providers_received

        # 3. Evento final SEARCH_COMPLETED
        event_completed = ws.receive_json()
        assert event_completed["event"] == "SEARCH_COMPLETED"
        assert event_completed["total_quotes"] >= 2
        assert event_completed["cheapest_effective_price"] > 0.0


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.collectors.air.amadeus.AmadeusCollector.safe_fetch")
def test_websocket_search_query_param_autoconnect(mock_air, mock_road, client):
    """Valida que parâmetros passados na URL iniciam a busca automaticamente ao conectar."""
    mock_road.return_value = [_create_dummy_quote("ROAD", "clickbus", "Cometa", 100.0)]
    mock_air.return_value = []

    with client.websocket_connect("/ws/search?origin=SAO&destination=RIO&date=2026-11-20") as ws:
        started = ws.receive_json()
        assert started["event"] == "SEARCH_STARTED"
        assert started["origin"] == "SAO"

        # Recebe batches dos dois provedores (clickbus e amadeus)
        batch1 = ws.receive_json()
        assert batch1["event"] == "QUOTE_BATCH"

        batch2 = ws.receive_json()
        assert batch2["event"] == "QUOTE_BATCH"

        # Recebe completed
        completed = ws.receive_json()
        assert completed["event"] == "SEARCH_COMPLETED"


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.collectors.air.amadeus.AmadeusCollector.safe_fetch")
def test_websocket_provider_error_isolation(mock_air, mock_road, client):
    """Valida padrão Bulkhead: erro em um coletor gera PROVIDER_ERROR sem derrubar o outro."""
    mock_road.return_value = [_create_dummy_quote("ROAD", "clickbus", "Cometa", 100.0)]
    mock_air.side_effect = RuntimeError("Amadeus API Timeout Simulada")

    with client.websocket_connect("/ws/search") as ws:
        ws.send_text(json.dumps({
            "action": "start_search",
            "origin": "SAO",
            "destination": "RIO",
            "date": "2026-11-20",
        }))

        ws.receive_json()  # SEARCH_STARTED

        events = [ws.receive_json(), ws.receive_json()]
        event_types = {e["event"] for e in events}

        assert "QUOTE_BATCH" in event_types
        assert "PROVIDER_ERROR" in event_types

        # WebSocket permanece vivo e finaliza a busca
        completed = ws.receive_json()
        assert completed["event"] == "SEARCH_COMPLETED"
        assert completed["total_quotes"] == 1
