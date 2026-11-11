"""Busca usa horários e tarifas do GTFS quando a ANTT não cobre a rota.

O feed real da ARTESP não liga dois hubs cadastrados; por isso os dados de teste são
inseridos entre São Paulo e Campinas (dois hubs) e removidos ao fim do teste.
"""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.database import SessionLocal
from app.models import BusSchedule, GtfsRouteFare

FEED = "teste_busca"
ROUTE = "SAO PAULO-SP|CAMPINAS-SP"


@pytest.fixture
def gtfs_sp_campinas():
    db = SessionLocal()
    db.add_all([
        BusSchedule(
            route_key=ROUTE, origin_city="SAO PAULO", origin_state="SP", destination_city="CAMPINAS",
            destination_state="SP", company_name="VIACAO TESTE", company_cnpj="", line_prefix=f"{FEED}:L1",
            service_class="NAO_INFORMADA", departure_time=hhmm, weekdays="1111111", months="1" * 12,
            source_period="2026-11", feed=FEED,
        )
        for hhmm in ("07:00", "13:00")
    ])
    db.add(GtfsRouteFare(
        feed=FEED, source_period="2026-11", route_id="L1", route_name="SAO PAULO - CAMPINAS",
        route_key=ROUTE, price_brl=31.5, currency="BRL",
    ))
    db.commit()
    try:
        yield db
    finally:
        db.query(BusSchedule).filter(BusSchedule.feed == FEED).delete(synchronize_session=False)
        db.query(GtfsRouteFare).filter(GtfsRouteFare.feed == FEED).delete(synchronize_session=False)
        db.commit()
        db.close()


def _search(client, origin, destination, date="2026-11-20", **extra):
    events = []
    with client.websocket_connect("/ws/search") as ws:
        ws.send_text(json.dumps({"action": "start_search", "origin": origin, "destination": destination,
                                 "date": date, **extra}))
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["event"] in ("SEARCH_COMPLETED", "SEARCH_ERROR"):
                return events


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_bus_without_antt_comes_from_gtfs_with_real_schedule_and_fare(mock_air, gtfs_sp_campinas):
    mock_air.return_value = []
    with TestClient(app) as client:
        events = _search(client, "SAO", "CAMPINAS")

    [batch] = [e for e in events if e["event"] == "QUOTE_BATCH" and e["provider"] == f"gtfs_{FEED}"]
    quotes = batch["quotes"]
    assert sorted(q["departure_time"][11:16] for q in quotes) == ["07:00", "13:00"]
    for q in quotes:
        assert q["base_price"] == 31.5                      # tarifa cadastrada da linha
        assert q["service_class"] == "ONIBUS"
        assert "VIACAO TESTE" in q["carrier_name"]
        assert q["route_metadata"]["price_basis"] == "gtfs_fare_regulated"
        assert q["route_metadata"]["schedule_is_estimated"] is False
        assert "booking_url" not in q["route_metadata"]
    assert events[-1]["event"] == "SEARCH_COMPLETED"


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_gtfs_bus_is_never_persisted(mock_air, gtfs_sp_campinas):
    from app.models import FareQuote
    db = SessionLocal()
    try:
        before = db.query(FareQuote).count()
    finally:
        db.close()
    mock_air.return_value = []
    with TestClient(app) as client:
        _search(client, "SAO", "CAMPINAS")
    db = SessionLocal()
    try:
        assert db.query(FareQuote).count() == before
    finally:
        db.close()
