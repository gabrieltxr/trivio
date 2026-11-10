"""Testes do coletor Ignav (parser sobre resposta real gravada, erros da API, links de compra)."""

import json
from pathlib import Path

import httpx
import pytest

from app.collectors.air.carriers import canonical_carrier_name
from app.collectors.air.ignav import (
    IgnavBillingError,
    IgnavClient,
    IgnavCollector,
    IgnavRequestError,
    parse_ignav_one_way,
    raise_for_ignav_status,
)
from app.collectors.base import CollectorNotAuthorizedError, CollectorParsingError, CollectorRateLimitError
from app.config import settings
from app.services.air_quotes import pick_booking_url

FIXTURE = Path(__file__).parent / "fixtures" / "ignav_one_way_cgh_sdu.json"


@pytest.fixture
def payload():
    """Trecho da resposta real de 07/11/2026 (CGH->SDU, 01/12/2026), com ignav_id fictícios."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _parse(payload, **kwargs):
    return parse_ignav_one_way(payload, "CGH", "SDU", "2026-12-01", **kwargs)


# ==============================================================================
# 1. Parser
# ==============================================================================

def test_parses_real_response(payload):
    quotes = _parse(payload)

    assert len(quotes) == len(payload["itineraries"]) == 5
    first = quotes[0]
    assert first.provider_source == "ignav"
    assert first.modal == "AIR"
    assert first.carrier_name == "Azul Linhas Aéreas"
    assert first.departure_time.isoformat() == "2026-12-01T07:20:00"
    assert first.arrival_time.isoformat() == "2026-12-01T08:20:00"
    assert first.duration_minutes == 60
    assert first.service_class == "ECONOMY"
    assert first.route_metadata["is_direct"] is True
    assert first.route_metadata["primary_flight_number"] == "AD6050"
    assert first.route_metadata["price_status"] == "verified"
    assert first.route_metadata["price_observed_at"] == payload["observed_at"]


def test_total_price_is_split_into_fare_and_estimated_boarding_fee(payload, monkeypatch):
    """A régua da ANAC mede a tarifa sem taxa de embarque; o Ignav devolve o total."""
    monkeypatch.setattr(settings, "DEFAULT_DOMESTIC_BOARDING_FEE_BRL", 40.0)
    first = _parse(payload)[0]

    assert first.route_metadata["price_total_brl"] == 610.0
    assert first.base_price == 570.0
    assert first.taxes_price == 40.0
    assert first.base_price + first.taxes_price == 610.0
    assert first.route_metadata["taxes_are_estimated"] is True


def test_baggage_uses_the_estimate_because_search_has_no_allowance(payload):
    first = _parse(payload, default_baggage_estimate_brl=110.0)[0]
    assert first.baggage_fee_estimate == 110.0
    assert first.route_metadata["baggage_included"] is False
    assert first.total_effective_price == 720.0   # 570 + 40 + 110


def test_carrier_names_are_canonical_by_code(payload):
    names = {q.carrier_name for q in _parse(payload)}
    assert names == {"Azul Linhas Aéreas", "LATAM Airlines", "Gol Linhas Aéreas"}


def test_booking_url_points_to_trivio_api_and_is_resolved_on_click(payload, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://api.trivio.test/")
    url = _parse(payload)[0].route_metadata["booking_url"]

    assert url.startswith("https://api.trivio.test/api/v1/air/booking-link?")
    assert "ignav_id=fixture" in url
    assert "carrier_code=AD" in url and "flight_number=6050" in url


def _itinerary(amount=500.0, currency="BRL", segments=None, **extra):
    seg = {
        "marketing_carrier_code": "G3", "flight_number": "1000", "operating_carrier_name": "Gol",
        "departure_airport": "CGH", "departure_time_local": "2026-12-01T08:00:00",
        "arrival_airport": "SDU", "arrival_time_local": "2026-12-01T09:00:00", "duration_minutes": 60,
    }
    return {
        "price": {"amount": amount, "currency": currency, "status": "verified"},
        "outbound": {"carrier": "Gol", "duration_minutes": 60, "segments": segments if segments is not None else [seg]},
        "cabin_class": "economy", "has_mixed_carriers": False, "ignav_id": "abc12345", **extra,
    }


def test_discards_zero_amount_other_currencies_and_empty_segments():
    payload = {"itineraries": [
        _itinerary(amount=0.0),            # placeholder que a API pede para não exibir
        _itinerary(currency="USD"),        # não convertemos câmbio
        _itinerary(segments=[]),
        _itinerary(amount=450.0),
    ], "observed_at": "2026-11-07T18:00:00Z"}

    quotes = _parse(payload)

    assert len(quotes) == 1
    assert quotes[0].route_metadata["price_total_brl"] == 450.0


def test_connection_itinerary_has_layover_details():
    segments = [
        {"marketing_carrier_code": "AD", "flight_number": "4000", "departure_airport": "CGH",
         "departure_time_local": "2026-12-01T06:00:00", "arrival_airport": "CNF",
         "arrival_time_local": "2026-12-01T07:10:00", "duration_minutes": 70},
        {"marketing_carrier_code": "AD", "flight_number": "4100", "departure_airport": "CNF",
         "departure_time_local": "2026-12-01T08:00:00", "arrival_airport": "SDU",
         "arrival_time_local": "2026-12-01T09:05:00", "duration_minutes": 65},
    ]
    payload = {"itineraries": [_itinerary(segments=segments)], "observed_at": None}
    payload["itineraries"][0]["outbound"]["duration_minutes"] = 185

    [quote] = _parse(payload)

    assert quote.route_metadata["stops_count"] == 1
    assert quote.route_metadata["layover_airports"] == ["CNF"]
    assert quote.route_metadata["layover_details"][0]["duration_minutes"] == 50
    assert quote.route_metadata["all_flight_numbers"] == ["AD4000", "AD4100"]
    assert quote.arrival_time.isoformat() == "2026-12-01T09:05:00"
    assert quote.duration_minutes == 185


def test_missing_itineraries_is_a_parsing_error():
    with pytest.raises(CollectorParsingError):
        _parse({"origin": "CGH"})


def test_canonical_carrier_name_fallbacks():
    assert canonical_carrier_name("la") == "LATAM Airlines"
    assert canonical_carrier_name("ZZ", "Nova Aérea") == "Nova Aérea"
    assert canonical_carrier_name("ZZ") == "Companhia ZZ"
    assert canonical_carrier_name(None) == "Companhia desconhecida"


# ==============================================================================
# 2. Erros documentados da API
# ==============================================================================

def _response(status, code=None):
    body = {"error": {"type": "x", "code": code, "message": "mensagem"}} if code else {}
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://ignav.com/api/fares/one-way"))


@pytest.mark.parametrize("status,code,exc", [
    (401, "invalid_api_key", CollectorNotAuthorizedError),
    (402, "billing_required", IgnavBillingError),
    (429, "monthly_spend_limit_reached", IgnavBillingError),
    (503, "server_busy", CollectorRateLimitError),
    (400, "departure_date_in_past", IgnavRequestError),
    (404, "route_not_found", IgnavRequestError),
    (424, "unsupported_search", IgnavRequestError),
])
def test_maps_documented_errors(status, code, exc):
    with pytest.raises(exc) as info:
        raise_for_ignav_status(_response(status, code))
    if exc is IgnavRequestError:
        assert info.value.code == code


@pytest.mark.parametrize("status,code,reason", [
    (402, "billing_required", "billing_required"),
    (429, "monthly_spend_limit_reached", "spend_limit_reached"),
])
def test_billing_errors_carry_reason_for_the_interface(status, code, reason):
    """402 e 429 têm motivos distintos: a tela explica o que fazer em cada caso."""
    with pytest.raises(IgnavBillingError) as info:
        raise_for_ignav_status(_response(status, code))
    assert info.value.reason == reason
    assert info.value.code == code


def test_success_does_not_raise():
    raise_for_ignav_status(_response(200))


# ==============================================================================
# 3. Cliente HTTP (transporte simulado: nenhuma request real)
# ==============================================================================

class _FakeTransportClient:
    """Substitui get_default_async_client e registra o que seria enviado."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def __call__(self, timeout=None, headers=None):
        outer = self

        class _Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, json=None):
                outer.sent.append({"url": url, "json": json, "headers": headers})
                status, body = outer.responses.pop(0)
                return httpx.Response(status, json=body, request=httpx.Request("POST", url))

        return _Ctx()


@pytest.mark.asyncio
async def test_search_sends_key_market_and_economy(monkeypatch, payload):
    fake = _FakeTransportClient([(200, payload)])
    monkeypatch.setattr("app.collectors.air.ignav.get_default_async_client", fake)

    quotes = await IgnavCollector(IgnavClient(api_key="k-test", market="BR")).fetch_quotes("cgh", "sdu", "2026-12-01")

    assert len(quotes) == 5
    sent = fake.sent[0]
    assert sent["url"] == "https://ignav.com/api/fares/one-way"
    assert sent["headers"]["X-Api-Key"] == "k-test"
    assert sent["json"] == {"origin": "CGH", "destination": "SDU", "departure_date": "2026-12-01",
                            "adults": 1, "cabin_class": "economy", "market": "BR"}


@pytest.mark.asyncio
async def test_booking_links_uses_only_ignav_id_then_falls_back_to_flight(monkeypatch):
    """A API recusa os dois modos juntos (conflicting_booking_lookup_mode)."""
    fake = _FakeTransportClient([
        (404, {"error": {"code": "ignav_id_not_found", "message": "expirado"}}),
        (200, {"booking_options": []}),
    ])
    monkeypatch.setattr("app.collectors.air.ignav.get_default_async_client", fake)

    await IgnavClient(api_key="k", market="BR").booking_links("id123456", "CGH", "SDU", "2026-12-01", "AD", "6050")

    assert fake.sent[0]["json"] == {"ignav_id": "id123456"}
    assert fake.sent[1]["json"]["outbound_carrier_code"] == "AD"
    assert "ignav_id" not in fake.sent[1]["json"]


def test_unconfigured_client():
    assert not IgnavClient(api_key="").is_configured


# ==============================================================================
# 4. Escolha do link de compra
# ==============================================================================

def test_prefers_airline_exact_flight_and_adds_scheme():
    response = {"booking_options": [{"legs": ["outbound"], "links": [
        {"provider_name": "OTA", "provider_type": "third_party", "specificity": "exact_flight", "url": "https://ota.example/x"},
        {"provider_name": "Azul", "provider_type": "airline", "specificity": "search_results", "url": "https://azul.example/s"},
        {"provider_name": "Azul", "provider_type": "airline", "specificity": "exact_flight", "url": "voeazul.example/b/1"},
    ]}]}
    assert pick_booking_url(response) == "https://voeazul.example/b/1"


def test_no_links_returns_none():
    assert pick_booking_url({"booking_options": [{"links": []}]}) is None
