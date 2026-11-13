"""Separação ônibus x avião na busca.

- Ônibus: banco ANTT + analisador de projeção. Nunca passa pelo Ignav.
- Avião: Ignav (pago), só com códigos IATA de aeroportos cadastrados.
- Ônibus + avião (só quando pedido): ônibus da ANTT até o hub + voo do Ignav saindo do hub.
"""

import json
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.analyzers.road_fare_projection import project_route_fare
from app.api.main import app
from app.collectors.base import StandardizedQuote
from app.database import SessionLocal
from app.models import Hub, ModalType, Terminal
from app.services.road_quotes import CONNECTION_LEAD_MINUTES

ROUTE_SP_RJ = "SAO PAULO-SP|RIO DE JANEIRO-RJ"
ROUTE_RG_PELOTAS = "RIO GRANDE-RS|PELOTAS-RS"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _flight(origin: str, destination: str, departure: datetime, base: float = 400.0) -> StandardizedQuote:
    return StandardizedQuote(
        origin_external_id=origin, destination_external_id=destination, modal="AIR",
        provider_source="ignav", carrier_name="LATAM", service_class="ECONOMY",
        departure_time=departure, arrival_time=departure.replace(hour=departure.hour + 1),
        duration_minutes=60, door_to_door_duration_minutes=150, base_price=base, taxes_price=40.0,
        route_metadata={"is_direct": True},
    )


def _search(client, origin: str, destination: str, date: str = "2026-11-20", **extra):
    events = []
    with client.websocket_connect("/ws/search") as ws:
        ws.send_text(json.dumps({
            "action": "start_search", "origin": origin, "destination": destination, "date": date, **extra,
        }))
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["event"] in ("SEARCH_COMPLETED", "SEARCH_ERROR"):
                return events


def _quotes(events, provider=None):
    return [
        q for e in events if e["event"] == "QUOTE_BATCH" and (provider is None or e["provider"] == provider)
        for q in e["quotes"]
    ]


def _airport_codes():
    db = SessionLocal()
    try:
        return {t.official_code for t in db.query(Terminal).filter(Terminal.modal == ModalType.AIR)}
    finally:
        db.close()


# ----------------------------------------------------------------- ônibus -> ANTT

@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_bus_prices_come_from_antt_projection_alongside_flights(mock_air, client, antt_series):
    """Ônibus sai do banco ANTT junto com os voos do Ignav, cada um no seu lote."""
    antt_series.add(ROUTE_SP_RJ, "SEMI_LEITO", "2026-11", "2026-08", 40, 150.0)
    antt_series.add(ROUTE_SP_RJ, "SEMI_LEITO", "2026-11", "2026-09", 60, 170.0)
    antt_series.add(ROUTE_SP_RJ, "LEITO", "2026-11", "2026-09", 20, 280.0)
    mock_air.return_value = [_flight("CGH", "SDU", datetime(2026, 11, 20, 10, 0))]

    events = _search(client, "SAO", "RIO")
    road = _quotes(events, "antt_projection")
    assert {q["service_class"] for q in road} == {"SEMI_LEITO", "LEITO"}
    assert _quotes(events, "ignav"), "os voos continuam vindo do Ignav"

    for q in road:
        expected = project_route_fare(
            antt_series.db, "São Paulo", "SP", "Rio de Janeiro", "RJ", q["service_class"], "2026-11"
        )
        assert q["base_price"] == expected.estimate_brl
        assert q["route_metadata"]["antt_projection"]["route_key"] == ROUTE_SP_RJ


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_route_without_antt_sales_has_no_bus_estimate(mock_air, client):
    """Sem vendas na ANTT não há ônibus inventado: a busca avisa o motivo."""
    mock_air.return_value = []
    events = _search(client, "SAO", "RIO")

    assert not [q for q in _quotes(events) if q["modal"] == "ROAD"]
    [notice] = [e for e in events if e["event"] == "PROVIDER_ERROR" and e["provider"] == "antt_projection"]
    assert notice["reason"] == "no_antt_data"


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_road_reference_carries_analyzer_projection(mock_air, client, antt_series):
    """A referência ANTT da busca leva a projeção do analisador para o mês da viagem."""
    mock_air.return_value = []
    for m in range(4, 12):     # 8 meses: passa do mínimo da faixa histórica
        antt_series.add(ROUTE_SP_RJ, "CONVENCIONAL", f"2026-{m:02d}", f"2026-{m:02d}", 100, 100.0 + m)

    events = _search(client, "SAO", "RIO")

    [reference] = [e for e in events if e["event"] == "ROAD_REFERENCE"]
    [conv] = [r for r in reference["references"] if r["service_class"] == "CONVENCIONAL"]
    projection = conv["projection"]
    assert projection["trip_month"] == "2026-11"
    assert projection["basis"] == "trip_month"
    assert projection["estimate_brl"] == 111.0
    assert projection["route_key"] == ROUTE_SP_RJ


# ----------------------------------------------------------------- avião -> Ignav

@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_bus_station_selected_never_calls_ignav(mock_air, client, antt_series):
    """Rodoviária escolhida (TIETE -> ROD_POA): só ônibus, sem request ao Ignav nem aeroporto da cidade."""
    antt_series.add("SAO PAULO-SP|PORTO ALEGRE-RS", "CONVENCIONAL", "2026-12", "2026-09", 100, 120.0)
    mock_air.return_value = []
    events = _search(client, "TIETE", "ROD_POA")

    mock_air.assert_not_called()
    assert "ignav" not in events[0]["providers"]
    assert not [q for q in _quotes(events) if q["modal"] == "AIR"]


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_airport_selected_never_brings_the_city_bus(mock_air, client, antt_series):
    """Aeroporto escolhido (CGH): só voo, sem ônibus da cidade."""
    antt_series.add("SAO PAULO-SP|PORTO ALEGRE-RS", "CONVENCIONAL", "2026-12", "2026-09", 100, 120.0)
    mock_air.return_value = [_flight("CGH", "POA", datetime(2026, 12, 18, 10, 0))]
    events = _search(client, "CGH", "POA")

    assert mock_air.call_count == 1
    assert not [q for q in _quotes(events) if q["modal"] == "ROAD"]


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_city_selected_brings_bus_even_when_airport_shares_its_code(mock_air, client, antt_series):
    """Cidade escolhida (HUB:POA): ônibus e voo. 'POA' sozinho é o aeroporto Salgado Filho."""
    antt_series.add("SAO PAULO-SP|PORTO ALEGRE-RS", "CONVENCIONAL", "2026-12", "2026-09", 100, 120.0)
    mock_air.return_value = [_flight("CGH", "POA", datetime(2026, 12, 18, 10, 0))]

    city = _search(client, "HUB:SAO", "HUB:POA", date="2026-12-18")
    assert [q for q in _quotes(city) if q["modal"] == "ROAD"]
    assert [q for q in _quotes(city) if q["modal"] == "AIR"]

    airport = _search(client, "HUB:SAO", "POA", date="2026-12-18")
    assert not [q for q in _quotes(airport) if q["modal"] == "ROAD"]


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_unregistered_city_code_is_rejected_not_swapped(mock_air, client):
    """'SSA' (Salvador) não está cadastrado: a busca falha em vez de pesquisar São Paulo."""
    mock_air.return_value = []
    events = _search(client, "SSA", "RIO")
    assert events[-1]["event"] == "SEARCH_ERROR"
    mock_air.assert_not_called()


@pytest.fixture
def hub_without_airport():
    """Município só com rodoviária: o aéreo não pode ser consultado."""
    db = SessionLocal()
    hub = Hub(ibge_code="9999991", name="Vila Teste Sem Aeroporto", state="SP",
              latitude=-23.0, longitude=-46.0)
    hub.terminals.append(Terminal(
        name="Rodoviária de Vila Teste", modal=ModalType.ROAD, official_code="ROD_VILA_TESTE",
        latitude=-23.0, longitude=-46.0,
    ))
    db.add(hub)
    db.commit()
    try:
        yield hub
    finally:
        db.delete(hub)
        db.commit()
        db.close()


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_city_without_airport_never_calls_ignav(mock_air, client, hub_without_airport):
    events = _search(client, "ROD_VILA_TESTE", "RIO")

    mock_air.assert_not_called()
    assert "ignav" not in events[0]["providers"]
    [notice] = [e for e in events if e["event"] == "PROVIDER_ERROR" and e["provider"] == "ignav"]
    assert notice["reason"] == "no_airport"
    assert events[-1]["event"] == "SEARCH_COMPLETED"


# ----------------------------------------------------------------- ônibus + avião

@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_interline_without_antt_bus_to_hub_pays_only_direct_flight(mock_air, mock_road, client):
    """Conexão pedida, sem vendas ANTT até Pelotas: o voo de Pelotas não é consultado."""
    mock_air.return_value = []
    _search(client, "RIG", "SAO", enable_virtual_interlining=True)

    assert mock_air.call_count == 1
    assert mock_air.call_args.args[0] == "RIG"
    mock_road.assert_not_called()


@patch("app.collectors.road.clickbus.ClickBusCollector.safe_fetch")
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_interline_combines_antt_bus_and_ignav_flight(mock_air, mock_road, client, antt_series):
    """Ônibus Rio Grande -> Pelotas (ANTT) + voo Pelotas -> São Paulo (Ignav)."""
    antt_series.add(ROUTE_RG_PELOTAS, "CONVENCIONAL", "2026-11", "2026-09", 300, 45.0)
    flight_departure = datetime(2026, 11, 20, 14, 0)

    def ignav(origin, destination, date):
        return [_flight(origin, destination, flight_departure, base=500.0)] if origin == "PET" else []

    mock_air.side_effect = ignav
    # Cidade (hub), não o código do aeroporto: escolher o aeroporto não traz o ônibus da cidade.
    events = _search(client, "Rio Grande", "SAO", enable_virtual_interlining=True)

    # Ignav: só IATA cadastrado, um voo direto e um voo do hub; ClickBus nunca
    airports = _airport_codes()
    air_calls = [c.args[:2] for c in mock_air.call_args_list]
    assert len(air_calls) == 2
    assert all(o in airports and d in airports for o, d in air_calls)
    assert any(origin == "PET" for origin, _ in air_calls)
    mock_road.assert_not_called()

    connections = _quotes(events, "trivio_interliner")
    assert connections, "deve haver conexão ônibus (ANTT) + voo (Ignav) via Pelotas"
    bus = project_route_fare(antt_series.db, "Rio Grande", "RS", "Pelotas", "RS", "CONVENCIONAL", "2026-11")
    for q in connections:
        meta = q["route_metadata"]
        assert meta["price_basis"] == "antt_projection+ignav"
        assert meta["intermodal_hub"] == "Pelotas, RS"
        assert "estimativa ANTT" in q["carrier_name"] and "LATAM" in q["carrier_name"]
        assert q["base_price"] == round(bus.estimate_brl + 500.0, 2)
        assert meta["connection_window_minutes"] == CONNECTION_LEAD_MINUTES


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_antt_bus_estimates_have_no_price_thermometer(mock_air, client, antt_series):
    """O preço do ônibus já é a média da ANTT: o termômetro (contra a mesma ANTT) não aparece."""
    for m in range(4, 12):     # série longa o bastante para o termômetro poder classificar
        antt_series.add(ROUTE_SP_RJ, "SEMI_LEITO", f"2026-{m:02d}", f"2026-{m:02d}", 100, 100.0 + m)
    mock_air.return_value = []

    road = _quotes(_search(client, "SAO", "RIO"), "antt_projection")

    assert road
    assert all("price_thermometer" not in q["route_metadata"] for q in road)


@pytest.mark.asyncio
async def test_search_returns_its_connection_to_the_pool_while_waiting_for_flights():
    """A busca não segura conexão enquanto espera o aéreo. Segurá-la enquanto cada thread de
    _run_db_work pede outra esgotava o pool (5+10) e travava tudo com ~16 buscas simultâneas."""
    import asyncio

    from app.api import websocket as ws
    from app.database import engine

    in_use_while_waiting = []

    class FakeSocket:
        client = ("127.0.0.1", 1)

        def __init__(self):
            self.events = []

        async def send_json(self, data):
            self.events.append(data)

    socket = FakeSocket()

    class SlowAir:
        provider_name = "ignav"
        modal = "AIR"

        async def get_quotes(self, origin, destination, travel_date):
            # espera o lote de ônibus sair (a thread dele já devolveu a conexão)
            while not any(e.get("provider") == "antt_projection" for e in socket.events):
                await asyncio.sleep(0.005)
            in_use_while_waiting.append(engine.pool.checkedout())
            return []

    original = ws.get_air_quote_service
    ws.get_air_quote_service = lambda: SlowAir()
    try:
        await ws.orchestrate_search(socket, {"origin": "SAO", "destination": "RIO", "date": "2026-11-20"})
    finally:
        ws.get_air_quote_service = original

    assert socket.events[-1]["event"] == "SEARCH_COMPLETED"
    assert in_use_while_waiting == [0]
