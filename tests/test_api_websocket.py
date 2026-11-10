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
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_search_orchestration_flow(mock_air, mock_road, client, monkeypatch):
    """Valida fluxo completo: SEARCH_STARTED -> QUOTE_BATCH (Road & Air) -> SEARCH_COMPLETED."""
    # Este teste cobre o caminho com ônibus ligado (fonte autorizada, hoje em hold).
    monkeypatch.setattr("app.api.websocket.ROAD_OFFERS_ENABLED", True)
    road_q = _create_dummy_quote("ROAD", "clickbus", "1001", 120.0)
    air_q = _create_dummy_quote("AIR", "ignav", "LATAM", 350.0)

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
        assert "ignav" in providers_received

        # 3. Evento final SEARCH_COMPLETED
        event_completed = ws.receive_json()
        assert event_completed["event"] == "SEARCH_COMPLETED"
        assert event_completed["total_quotes"] >= 2
        assert event_completed["cheapest_effective_price"] > 0.0


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_search_query_param_autoconnect(mock_air, mock_road, client):
    """Valida que parâmetros passados na URL iniciam a busca automaticamente ao conectar."""
    mock_road.return_value = [_create_dummy_quote("ROAD", "clickbus", "Cometa", 100.0)]
    mock_air.return_value = []

    with client.websocket_connect("/ws/search?origin=SAO&destination=RIO&date=2026-11-20") as ws:
        started = ws.receive_json()
        assert started["event"] == "SEARCH_STARTED"
        assert started["origin"] == "SAO"

        # Recebe batches dos dois provedores (clickbus e ignav)
        batch1 = ws.receive_json()
        assert batch1["event"] == "QUOTE_BATCH"

        batch2 = ws.receive_json()
        assert batch2["event"] == "QUOTE_BATCH"

        # Recebe completed
        completed = ws.receive_json()
        assert completed["event"] == "SEARCH_COMPLETED"


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_reports_air_not_queried_with_reason(mock_air, mock_road, client):
    """Aéreo sem chave ou sem orçamento não pode parecer 'zero voos': vira PROVIDER_ERROR com motivo."""
    from app.services.air_quotes import AirProviderUnavailableError

    mock_road.return_value = [_create_dummy_quote("ROAD", "clickbus", "Cometa", 100.0)]
    mock_air.side_effect = AirProviderUnavailableError("budget_exhausted", "Orçamento do aéreo ao vivo atingido")

    with client.websocket_connect("/ws/search") as ws:
        ws.send_text(json.dumps({
            "action": "start_search", "origin": "SAO", "destination": "RIO", "date": "2026-11-20",
        }))
        ws.receive_json()  # SEARCH_STARTED
        events = [ws.receive_json(), ws.receive_json()]

    [air_event] = [e for e in events if e["event"] == "PROVIDER_ERROR"]
    assert air_event["provider"] == "ignav"
    assert air_event["reason"] == "budget_exhausted"
    assert "Orçamento" in air_event["error"]


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_reports_billing_required_as_its_own_reason(mock_air, mock_road, client):
    """HTTP 402 da conta Ignav não pode virar 'provider_error' genérico: vira motivo próprio."""
    from app.collectors.air.ignav import IgnavBillingError

    mock_road.return_value = [_create_dummy_quote("ROAD", "clickbus", "Cometa", 100.0)]
    mock_air.side_effect = IgnavBillingError(
        "Ignav HTTP 402 (billing_required): conta sem crédito", reason="billing_required", code="billing_required"
    )

    with client.websocket_connect("/ws/search") as ws:
        ws.send_text(json.dumps({
            "action": "start_search", "origin": "SAO", "destination": "RIO", "date": "2026-11-20",
        }))
        ws.receive_json()  # SEARCH_STARTED
        events = [ws.receive_json(), ws.receive_json()]

    [air_event] = [e for e in events if e["event"] == "PROVIDER_ERROR"]
    assert air_event["provider"] == "ignav"
    assert air_event["reason"] == "billing_required"
    assert mock_air.call_count == 1  # sem retry: a cobrança não é repetida


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_provider_error_isolation(mock_air, mock_road, client, monkeypatch):
    """Valida padrão Bulkhead: erro em um coletor gera PROVIDER_ERROR sem derrubar o outro."""
    # Este teste cobre o caminho com ônibus ligado (fonte autorizada, hoje em hold).
    monkeypatch.setattr("app.api.websocket.ROAD_OFFERS_ENABLED", True)
    mock_road.return_value = [_create_dummy_quote("ROAD", "clickbus", "Cometa", 100.0)]
    mock_air.side_effect = RuntimeError("Ignav API Timeout Simulada")

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


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_simulation_mode_is_labeled_and_not_persisted(mock_air, mock_road, client, monkeypatch):
    """Sem dados reais: estimativas rotuladas, sem operadoras reais, com transfer e sem persistência.

    Também valida que o Virtual Interlining usa pernas Origem -> Hub e Hub -> Destino
    (Rio Grande -> ônibus -> Pelotas -> voo -> São Paulo).
    """
    # Este teste cobre o caminho com ônibus ligado (fonte autorizada, hoje em hold).
    monkeypatch.setattr("app.api.websocket.ROAD_OFFERS_ENABLED", True)
    from app.database import SessionLocal
    from app.models import FareQuote, Hub

    db = SessionLocal()
    try:
        if not db.query(Hub).filter(Hub.ibge_code.in_(["4315602", "4314407", "3550308"])).count() == 3:
            pytest.skip("Seed de hubs (scripts/init_db.py) ausente no banco de desenvolvimento.")
        rows_before = db.query(FareQuote).count()
    finally:
        db.close()

    mock_road.return_value = []
    mock_air.return_value = []

    events = []
    with client.websocket_connect("/ws/search") as ws:
        ws.send_text(json.dumps({
            "action": "start_search",
            "origin": "RIG",
            "destination": "SAO",
            "date": "2026-11-20",
        }))
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["event"] == "SEARCH_COMPLETED":
                break

    quotes = [q for e in events if e["event"] == "QUOTE_BATCH" for q in e["quotes"]]
    assert quotes, "o modo simulação deve retornar estimativas"
    for q in quotes:
        assert q["route_metadata"]["is_simulated"] is True
        assert q["provider_source"] in ("trivio_simulation", "trivio_interliner")
        assert "estimativa" in q["carrier_name"]
        assert q["transfer_cost_estimate"] > 0

    interline = [q for q in quotes if q["route_metadata"].get("is_virtual_interline")]
    assert interline, "deve haver conexão ônibus + voo via hub vizinho"
    for q in interline:
        assert q["route_metadata"]["intermodal_hub"] == "Pelotas, RS"
        transfer = q["route_metadata"]["transfer_details"]
        assert "Pelotas" in transfer["from_terminal_name"]
        assert "Pelotas" in transfer["to_terminal_name"]

    assert events[-1]["is_simulated"] is True

    db = SessionLocal()
    try:
        assert db.query(FareQuote).count() == rows_before
    finally:
        db.close()


@pytest.mark.parametrize(
    "origin,destination,date,expected",
    [
        ("", "RIO", "2026-11-20", "Informe a origem"),
        ("SAO", "XYZW-INEXISTENTE", "2026-11-20", "Destino não reconhecido"),
        ("SAO", "RIO", "20/11/2026", "Data de ida inválida"),
        ("SAO", "CGH", "2026-11-20", "cidades diferentes"),
    ],
)
@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_rejects_invalid_search_params(mock_air, mock_road, client, origin, destination, date, expected):
    """Busca inválida emite SEARCH_ERROR e não chama coletores nem gera estimativas."""
    with client.websocket_connect("/ws/search") as ws:
        ws.send_text(json.dumps({
            "action": "start_search",
            "origin": origin,
            "destination": destination,
            "date": date,
        }))
        event = ws.receive_json()

    assert event["event"] == "SEARCH_ERROR"
    assert expected in event["message"]
    mock_road.assert_not_called()
    mock_air.assert_not_called()


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_interline_flight_is_not_paid_for_without_bus_leg(mock_air, mock_road, client):
    """Sem ônibus até o hub não há rota híbrida: o voo do hub (request paga) não é consultado."""
    mock_road.return_value = []          # ex: ClickBus recusando acesso
    mock_air.return_value = [_create_dummy_quote("AIR", "ignav", "LATAM", 350.0)]

    with client.websocket_connect("/ws/search") as ws:
        ws.send_text(json.dumps({
            "action": "start_search", "origin": "SAO", "destination": "RIO", "date": "2026-11-20",
        }))
        while ws.receive_json()["event"] != "SEARCH_COMPLETED":
            pass

    assert mock_air.call_count == 1      # só o voo Origem -> Destino


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_reports_refused_site_as_access_denied(mock_air, mock_road, client, monkeypatch):
    """Site que recusa o acesso (403) não pode aparecer como 'zero resultados' sem explicação."""
    # Este teste cobre o caminho com ônibus ligado (fonte autorizada, hoje em hold).
    monkeypatch.setattr("app.api.websocket.ROAD_OFFERS_ENABLED", True)
    mock_road.return_value = []
    mock_air.return_value = []

    with patch("app.collectors.road.clickbus.ClickBusCollector.access_denied", True, create=True):
        with client.websocket_connect("/ws/search") as ws:
            ws.send_text(json.dumps({
                "action": "start_search", "origin": "SAO", "destination": "RIO", "date": "2026-11-20",
            }))
            ws.receive_json()  # SEARCH_STARTED
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["event"] == "SEARCH_COMPLETED":
                    break

    [denied] = [e for e in events if e["event"] == "PROVIDER_ERROR" and e["provider"] == "clickbus"]
    assert denied["reason"] == "access_denied"


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_road_offers_off_by_default_never_calls_bus_source(mock_air, mock_road, client):
    """Sem fonte de ônibus autorizada, o ClickBus não é consultado e nenhum ônibus entra no ranking."""
    mock_air.return_value = [_create_dummy_quote("AIR", "ignav", "LATAM", 350.0)]

    events = []
    with client.websocket_connect("/ws/search") as ws:
        ws.send_text(json.dumps({
            "action": "start_search", "origin": "SAO", "destination": "RIO", "date": "2026-11-20",
        }))
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["event"] == "SEARCH_COMPLETED":
                break

    mock_road.assert_not_called()
    assert "clickbus" not in [e.get("provider") for e in events]
    for event in events:
        if event["event"] == "QUOTE_BATCH":
            assert all(q["modal"] != "ROAD" for q in event["quotes"])
