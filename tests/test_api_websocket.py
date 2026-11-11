"""Unit and Integration tests for Real-Time Search WebSocket (/ws/search).

Fontes da busca: ônibus -> banco ANTT + projeção; avião -> Ignav (mockado aqui);
ônibus + avião -> as duas. A ClickBus segue desligada (ROAD_OFFERS_ENABLED).
"""

from datetime import datetime, timezone
import json
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.collectors.base import StandardizedQuote

ROUTE_SP_RJ = "SAO PAULO-SP|RIO DE JANEIRO-RJ"


@pytest.fixture
def client():
    """Cliente de teste FastAPI."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sp_rj_bus(antt_series):
    """Vendas ANTT de SP->RJ em novembro/2026: duas classes viram duas opções de ônibus."""
    antt_series.add(ROUTE_SP_RJ, "SEMI_LEITO", "2026-11", "2026-09", 100, 150.0)
    antt_series.add(ROUTE_SP_RJ, "LEITO", "2026-11", "2026-09", 40, 260.0)
    return antt_series


def _create_dummy_quote(modal: str, provider: str, carrier: str, price: float) -> StandardizedQuote:
    """Gera cotação padronizada de teste."""
    now = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    return StandardizedQuote(
        origin_external_id="CGH" if modal == "AIR" else "SAO",
        destination_external_id="SDU" if modal == "AIR" else "RIO",
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


def _search(client, payload=None, url="/ws/search"):
    """Executa uma busca e devolve todos os eventos até SEARCH_COMPLETED/SEARCH_ERROR."""
    events = []
    with client.websocket_connect(url) as ws:
        if payload is not None:
            ws.send_text(json.dumps({"action": "start_search", **payload}))
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["event"] in ("SEARCH_COMPLETED", "SEARCH_ERROR"):
                return events


def _batches(events, provider=None):
    return [e for e in events if e["event"] == "QUOTE_BATCH" and (provider is None or e["provider"] == provider)]


def _errors(events, provider=None):
    return [e for e in events if e["event"] == "PROVIDER_ERROR" and (provider is None or e["provider"] == provider)]


SP_RJ = {"origin": "SAO", "destination": "RIO", "date": "2026-11-20"}


def test_websocket_ping_pong(client):
    """Valida heartbeat ping/pong no canal WebSocket."""
    with client.websocket_connect("/ws/search") as ws:
        ws.send_text(json.dumps({"action": "ping"}))
        data = ws.receive_json()
        assert data["event"] == "pong"
        assert "timestamp" in data


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_search_orchestration_flow(mock_air, mock_road, client, sp_rj_bus):
    """SEARCH_STARTED -> ônibus (ANTT) e avião (Ignav) em lotes próprios -> SEARCH_COMPLETED."""
    mock_air.return_value = [_create_dummy_quote("AIR", "ignav", "LATAM", 350.0)]

    events = _search(client, {**SP_RJ, "hourly_value": 40.0})

    started = events[0]
    assert started["event"] == "SEARCH_STARTED"
    assert started["origin"] == "SAO" and started["destination"] == "RIO"
    assert set(started["providers"]) == {"antt_projection", "ignav"}

    [road] = _batches(events, "antt_projection")
    [air] = _batches(events, "ignav")
    assert road["modal"] == "ROAD" and road["count"] == 2
    assert air["modal"] == "AIR" and air["count"] == 1

    completed = events[-1]
    assert completed["event"] == "SEARCH_COMPLETED"
    assert completed["total_quotes"] == 3
    assert completed["cheapest_effective_price"] > 0.0
    mock_road.assert_not_called()


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_search_query_param_autoconnect(mock_air, client):
    """Parâmetros na URL iniciam a busca automaticamente ao conectar."""
    mock_air.return_value = [_create_dummy_quote("AIR", "ignav", "LATAM", 350.0)]

    events = _search(client, url="/ws/search?origin=SAO&destination=RIO&date=2026-11-20")

    assert events[0]["event"] == "SEARCH_STARTED" and events[0]["origin"] == "SAO"
    assert _batches(events, "ignav")
    assert events[-1]["event"] == "SEARCH_COMPLETED"


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_reports_air_not_queried_with_reason(mock_air, client):
    """Aéreo sem chave ou sem saldo não pode parecer 'zero voos': vira PROVIDER_ERROR com motivo."""
    from app.services.air_quotes import AirProviderUnavailableError

    mock_air.side_effect = AirProviderUnavailableError("budget_exhausted", "Saldo de requests do Ignav na margem")

    [air_event] = _errors(_search(client, SP_RJ), "ignav")
    assert air_event["reason"] == "budget_exhausted"
    assert "Saldo" in air_event["error"]


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_reports_billing_required_as_its_own_reason(mock_air, client):
    """HTTP 402 da conta Ignav não pode virar 'provider_error' genérico: vira motivo próprio."""
    from app.collectors.air.ignav import IgnavBillingError

    mock_air.side_effect = IgnavBillingError(
        "Ignav HTTP 402 (billing_required): conta sem crédito", reason="billing_required", code="billing_required"
    )

    [air_event] = _errors(_search(client, SP_RJ), "ignav")
    assert air_event["reason"] == "billing_required"
    assert mock_air.call_count == 1  # sem retry: a cobrança não é repetida


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_provider_error_isolation(mock_air, client, sp_rj_bus):
    """Bulkhead: o Ignav falhando não derruba o ônibus (ANTT), e a busca termina normalmente."""
    mock_air.side_effect = RuntimeError("Ignav API Timeout Simulada")

    events = _search(client, SP_RJ)

    [road] = _batches(events, "antt_projection")
    [air_error] = _errors(events, "ignav")
    assert air_error["reason"] == "provider_error"
    assert events[-1]["event"] == "SEARCH_COMPLETED"
    assert events[-1]["total_quotes"] == road["count"] == 2


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_bus_estimates_are_labeled_and_not_persisted(mock_air, client, sp_rj_bus):
    """Ônibus da ANTT é estimativa: rotulado, sem empresa real, sem link e fora do banco."""
    from app.database import SessionLocal
    from app.models import FareQuote

    db = SessionLocal()
    try:
        rows_before = db.query(FareQuote).count()
    finally:
        db.close()

    mock_air.return_value = []
    events = _search(client, SP_RJ)

    [road] = _batches(events, "antt_projection")
    for q in road["quotes"]:
        meta = q["route_metadata"]
        assert q["provider_source"] == "antt_projection"
        assert meta["is_simulated"] is True
        assert meta["price_basis"] == "antt_projection"
        assert "estimativa ANTT" in q["carrier_name"]
        assert "booking_url" not in meta
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


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_interline_flight_is_not_paid_for_without_bus_leg(mock_air, client):
    """Conexão pedida, mas sem ônibus na ANTT até o hub: o voo do hub (pago) não é consultado."""
    mock_air.return_value = [_create_dummy_quote("AIR", "ignav", "LATAM", 350.0)]

    _search(client, {**SP_RJ, "enable_virtual_interlining": True})

    assert mock_air.call_count == 1      # só o voo Origem -> Destino


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_interline_is_off_unless_requested(mock_air, client, antt_series):
    """Mesmo com ônibus até o hub, sem pedir a conexão nenhum voo de hub é consultado."""
    antt_series.add("SAO PAULO-SP|CAMPINAS-SP", "CONVENCIONAL", "2026-11", "2026-09", 300, 40.0)
    mock_air.return_value = []

    events = _search(client, SP_RJ)          # sem enable_virtual_interlining

    assert mock_air.call_count == 1
    assert "virtual_interlining" not in events[0]["providers"]


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_reports_refused_site_as_access_denied(mock_air, mock_road, client, monkeypatch):
    """Site que recusa o acesso (403) não pode aparecer como 'zero resultados' sem explicação."""
    # Este teste cobre o caminho com a ClickBus ligada (fonte autorizada, hoje em hold).
    monkeypatch.setattr("app.api.websocket.ROAD_OFFERS_ENABLED", True)
    mock_road.return_value = []
    mock_air.return_value = []

    with patch("app.collectors.road.clickbus.ClickBusCollector.access_denied", True, create=True):
        events = _search(client, SP_RJ)

    [denied] = _errors(events, "clickbus")
    assert denied["reason"] == "access_denied"


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_road_offers_off_by_default_never_calls_bus_source(mock_air, mock_road, client, sp_rj_bus):
    """Sem fonte de ônibus autorizada, a ClickBus não é consultada: o ônibus vem só da ANTT."""
    mock_air.return_value = [_create_dummy_quote("AIR", "ignav", "LATAM", 350.0)]

    events = _search(client, SP_RJ)

    mock_road.assert_not_called()
    assert "clickbus" not in [e.get("provider") for e in events]
    road_quotes = [q for b in _batches(events) for q in b["quotes"] if q["modal"] == "ROAD"]
    assert road_quotes and all(q["provider_source"] == "antt_projection" for q in road_quotes)
