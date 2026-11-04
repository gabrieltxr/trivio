"""Testes unitários para o coletor aéreo Amadeus, ISO durations e cálculo de bagagem/check-in."""

from datetime import datetime
import json
import httpx
import pytest

from app.collectors.air.amadeus import (
    AmadeusCollector,
    parse_amadeus_flight_offers,
    parse_iso_duration,
)


# ==============================================================================
# 1. Testes de Parsing de Duração ISO 8601
# ==============================================================================

def test_parse_iso_duration():
    """Valida conversão de formatos ISO 8601 (PT1H15M, PT50M, PT2H) em minutos."""
    assert parse_iso_duration("PT1H15M") == 75
    assert parse_iso_duration("PT50M") == 50
    assert parse_iso_duration("PT2H") == 120
    assert parse_iso_duration("PT3H45M") == 225
    assert parse_iso_duration("") == 0
    assert parse_iso_duration(None) == 0


# ==============================================================================
# 2. Testes de Parsing de Ofertas de Voos (Amadeus v2)
# ==============================================================================

SAMPLE_AMADEUS_DIRECT_NO_BAG = {
    "data": [
        {
            "id": "offer-latam-1",
            "itineraries": [
                {
                    "duration": "PT1H10M",
                    "segments": [
                        {
                            "departure": {"iataCode": "CGH", "at": "2026-11-20T09:00:00"},
                            "arrival": {"iataCode": "SDU", "at": "2026-11-20T10:10:00"},
                            "carrierCode": "LA",
                            "number": "3456",
                            "aircraft": {"code": "320"},
                            "numberOfStops": 0,
                        }
                    ],
                }
            ],
            "price": {
                "currency": "BRL",
                "total": "395.50",
                "base": "340.00",
            },
            "travelerPricings": [
                {
                    "fareDetailsBySegment": [
                        {
                            "cabin": "ECONOMY",
                            "includedCheckedBags": {"quantity": 0},
                        }
                    ]
                }
            ],
        }
    ],
    "dictionaries": {
        "carriers": {"LA": "LATAM Airlines"},
        "aircraft": {"320": "Airbus A320"},
    },
}

SAMPLE_AMADEUS_LAYOVER_WITH_BAG = {
    "data": [
        {
            "id": "offer-gol-2",
            "itineraries": [
                {
                    "duration": "PT4H30M",
                    "segments": [
                        {
                            "departure": {"iataCode": "POA", "at": "2026-11-20T06:00:00"},
                            "arrival": {"iataCode": "BSB", "at": "2026-11-20T08:30:00"},
                            "carrierCode": "G3",
                            "number": "1502",
                            "aircraft": {"code": "738"},
                            "numberOfStops": 0,
                        },
                        {
                            "departure": {"iataCode": "BSB", "at": "2026-11-20T09:30:00"},
                            "arrival": {"iataCode": "SDU", "at": "2026-11-20T10:30:00"},
                            "carrierCode": "G3",
                            "number": "1800",
                            "aircraft": {"code": "738"},
                            "numberOfStops": 0,
                        },
                    ],
                }
            ],
            "price": {
                "currency": "BRL",
                "total": "620.00",
                "base": "550.00",
            },
            "travelerPricings": [
                {
                    "fareDetailsBySegment": [
                        {
                            "cabin": "ECONOMY",
                            "includedCheckedBags": {"quantity": 1},
                        }
                    ]
                }
            ],
        }
    ],
    "dictionaries": {
        "carriers": {"G3": "Gol Linhas Aéreas"},
        "aircraft": {"738": "Boeing 738"},
    },
}


def test_parse_amadeus_direct_flight_without_baggage():
    """Valida voo direto sem bagagem despachada inclusa (heurística de R$ 110 e 90min buffer)."""
    quotes = parse_amadeus_flight_offers(
        SAMPLE_AMADEUS_DIRECT_NO_BAG,
        origin_ext_id="CGH",
        dest_ext_id="SDU",
        default_baggage_estimate_brl=110.0,
        checkin_buffer_minutes=90,
    )

    assert len(quotes) == 1
    q = quotes[0]

    assert q.modal == "AIR"
    assert q.provider_source == "amadeus"
    assert q.carrier_name == "LATAM Airlines"
    assert q.service_class == "ECONOMY"
    assert q.departure_time == datetime(2026, 11, 20, 9, 0)
    assert q.arrival_time == datetime(2026, 11, 20, 10, 10)
    assert q.duration_minutes == 70
    assert q.door_to_door_duration_minutes == 160  # 70 min voo + 90 min antecedência aeroporto

    assert q.base_price == 340.00
    assert q.taxes_price == 55.50  # 395.50 - 340.00
    assert q.baggage_fee_estimate == 110.00  # Projetado por não estar inclusa na tarifa básica
    assert q.total_effective_price == 505.50  # 340.00 + 55.50 + 110.00
    assert len(q.dedup_signature) == 32

    meta = q.route_metadata
    assert meta["is_direct"] is True
    assert meta["stops_count"] == 0
    assert meta["layover_airports"] == []
    assert meta["primary_flight_number"] == "LA3456"
    assert meta["baggage_included"] is False
    assert meta["aircraft"] == "Airbus A320"


def test_parse_amadeus_flight_with_layover_and_baggage_included():
    """Valida voo com conexão e bagagem despachada inclusa na tarifa."""
    quotes = parse_amadeus_flight_offers(
        SAMPLE_AMADEUS_LAYOVER_WITH_BAG,
        origin_ext_id="POA",
        dest_ext_id="SDU",
        default_baggage_estimate_brl=110.0,
        checkin_buffer_minutes=90,
    )

    assert len(quotes) == 1
    q = quotes[0]

    assert q.carrier_name == "Gol Linhas Aéreas"
    assert q.duration_minutes == 270  # PT4H30M
    assert q.door_to_door_duration_minutes == 360  # 270 + 90
    assert q.base_price == 550.00
    assert q.taxes_price == 70.00  # 620 - 550
    assert q.baggage_fee_estimate == 0.0  # Inclusa na tarifa!
    assert q.total_effective_price == 620.00

    meta = q.route_metadata
    assert meta["is_direct"] is False
    assert meta["stops_count"] == 1
    assert meta["layover_airports"] == ["BSB"]
    assert meta["baggage_included"] is True
    assert meta["primary_flight_number"] == "G31502"
    assert meta["all_flight_numbers"] == ["G31502", "G31800"]


# ==============================================================================
# 3. Testes do AmadeusCollector e Autenticação OAuth
# ==============================================================================

@pytest.mark.asyncio
async def test_amadeus_collector_missing_credentials_returns_empty(monkeypatch):
    """Se as credenciais não estiverem no .env, fetch_quotes retorna lista vazia com segurança."""
    from app.config import settings
    monkeypatch.setattr(settings, "AMADEUS_CLIENT_ID", "")
    monkeypatch.setattr(settings, "AMADEUS_CLIENT_SECRET", "")
    collector = AmadeusCollector(client_id="", client_secret="")
    quotes = await collector.fetch_quotes("GRU", "SDU", "2026-11-20")
    assert quotes == []


@pytest.mark.asyncio
async def test_amadeus_collector_oauth_and_search_mocked(monkeypatch):
    """Valida fluxo completo de autenticação OAuth e consumo de voos com mock do client HTTPX."""
    collector = AmadeusCollector(
        client_id="test_key",
        client_secret="test_secret",
        env="test",
    )

    class MockAuthResponse:
        status_code = 200

        def json(self):
            return {"access_token": "mocked_jwt_token", "expires_in": 1799}

        def raise_for_status(self):
            pass

    class MockSearchResponse:
        status_code = 200

        def json(self):
            return SAMPLE_AMADEUS_DIRECT_NO_BAG

        def raise_for_status(self):
            pass

    async def mock_post(self, url, *args, **kwargs):
        return MockAuthResponse()

    async def mock_get(self, url, *args, **kwargs):
        return MockSearchResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    quotes = await collector.fetch_quotes("CGH", "SDU", "2026-11-20")
    assert len(quotes) == 1
    assert quotes[0].carrier_name == "LATAM Airlines"
    assert collector._access_token == "mocked_jwt_token"
