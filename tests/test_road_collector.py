"""Testes unitários para o coletor rodoviário ClickBus e parsers de viagens."""

from datetime import datetime
import httpx
import pytest

from app.collectors.compliance import NotAuthorizedError, RobotsDisallowedError
from app.collectors.road.clickbus import (
    ClickBusCollector,
    normalize_bus_service_class,
    parse_clickbus_trips,
)


# ==============================================================================
# 1. Testes de Normalização de Serviços Rodoviários (ANTT)
# ==============================================================================

def test_normalize_bus_service_class():
    """Valida a categorização das classes de serviço rodoviário brasileiras."""
    assert normalize_bus_service_class("Convencional") == "CONVENCIONAL"
    assert normalize_bus_service_class("convencional com ar") == "CONVENCIONAL"
    assert normalize_bus_service_class("Executivo Especial") == "EXECUTIVO"
    assert normalize_bus_service_class("Semi-Leito") == "SEMI_LEITO"
    assert normalize_bus_service_class("semi leito confort") == "SEMI_LEITO"
    assert normalize_bus_service_class("Leito Turismo") == "LEITO"
    assert normalize_bus_service_class("Leito Cama") == "LEITO_CAMA"
    assert normalize_bus_service_class("Prime Cama") == "LEITO_CAMA"


# ==============================================================================
# 2. Testes de Parsing de Payload ClickBus
# ==============================================================================

SAMPLE_CLICKBUS_PAYLOAD = {
    "items": [
        {
            "id": "trip-cometa-101",
            "bus_company": {"id": "12", "name": "Viação Cometa"},
            "service": {"name": "Semi-Leito"},
            "departure": {"date": "2026-11-20", "time": "08:30:00"},
            "arrival": {"date": "2026-11-20", "time": "14:30:00"},
            "duration": "06:00",
            "price": {"amount": 145.50, "service_fee": 18.20},
            "available_seats": 14,
            "features": [{"name": "wifi"}, {"name": "ar condicionado"}, {"name": "tomada USB"}],
        },
        {
            "id": "trip-1001-202",
            "bus_company": {"id": "18", "name": "Auto Viação 1001"},
            "service": {"name": "Leito Cama"},
            "departure": {"date": "2026-11-20", "time": "23:00:00"},
            "arrival": {"date": "2026-11-21", "time": "05:00:00"},
            "price": {"amount": 260.00, "service_fee": 22.00},
            "available_seats": 6,
            "features": [{"name": "wifi"}, {"name": "leito cama 180"}],
        },
    ]
}


def test_parse_clickbus_trips_standard():
    """Valida a conversão do payload JSON em StandardizedQuote com regra de bagagem ANTT."""
    quotes = parse_clickbus_trips(
        SAMPLE_CLICKBUS_PAYLOAD,
        origin_ext_id="sao-paulo-tiete-sp",
        dest_ext_id="rio-de-janeiro-novo-rio-rj",
    )

    assert len(quotes) == 2

    q1 = quotes[0]
    assert q1.modal == "ROAD"
    assert q1.provider_source == "clickbus"
    assert q1.carrier_name == "Viação Cometa"
    assert q1.service_class == "SEMI_LEITO"
    assert q1.departure_time == datetime(2026, 11, 20, 8, 30)
    assert q1.arrival_time == datetime(2026, 11, 20, 14, 30)
    assert q1.duration_minutes == 360
    assert q1.door_to_door_duration_minutes == 380  # 360 + 20 min buffer rodoviário
    assert q1.base_price == 145.50
    assert q1.taxes_price == 18.20
    assert q1.baggage_fee_estimate == 0.0  # Franquia gratuita ANTT
    assert q1.total_effective_price == 163.70
    assert len(q1.dedup_signature) == 32
    assert "wifi" in q1.route_metadata["amenities"]
    assert q1.route_metadata["seats_available"] == 14

    q2 = quotes[1]
    assert q2.carrier_name == "Auto Viação 1001"
    assert q2.service_class == "LEITO_CAMA"
    assert q2.departure_time == datetime(2026, 11, 20, 23, 0)
    assert q2.arrival_time == datetime(2026, 11, 21, 5, 0)
    assert q2.total_effective_price == 282.00


def test_parse_clickbus_trips_cents_normalization():
    """Valida suporte a valores retornados em centavos (ex: 15000 = R$ 150.00)."""
    cents_payload = {
        "items": [
            {
                "id": "trip-cents",
                "bus_company": {"name": "Catarinense"},
                "service": {"name": "Executivo"},
                "departure": {"date": "2026-11-20", "time": "10:00:00"},
                "arrival": {"date": "2026-11-20", "time": "16:00:00"},
                "price": {"amount": 18000, "service_fee": 1500},
            }
        ]
    }
    quotes = parse_clickbus_trips(cents_payload, "curitiba-pr", "florianopolis-sc")
    assert len(quotes) == 1
    assert quotes[0].base_price == 180.00
    assert quotes[0].taxes_price == 15.00
    assert quotes[0].total_effective_price == 195.00


# ==============================================================================
# 3. Testes do ClickBusCollector com Mock de HTTP
# ==============================================================================

@pytest.fixture(autouse=True)
def permissive_robots(monkeypatch):
    """Isola os testes de rede: robots.txt permissivo e sem espera entre requisições."""
    from app.collectors import compliance

    class AllowAllRobots:
        async def can_fetch(self, url: str) -> bool:
            return True

        async def crawl_delay(self, url: str) -> float:
            return 0.0

        async def sitemaps(self, url: str):
            return []

    monkeypatch.setattr(compliance, "_shared_robots", AllowAllRobots())
    monkeypatch.setattr(compliance, "_shared_limiter", compliance.PolitenessLimiter())

@pytest.mark.asyncio
async def test_clickbus_collector_fetch_quotes_success(monkeypatch):
    """Valida chamada Tier 1 bem-sucedida através de mock do transporte HTTPX."""
    collector = ClickBusCollector()

    class MockResponse:
        status_code = 200

        def json(self):
            return SAMPLE_CLICKBUS_PAYLOAD

        def raise_for_status(self):
            pass

    async def mock_get(self, url, *args, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    quotes = await collector.fetch_quotes(
        origin_ext_id="sao-paulo-tiete-sp",
        dest_ext_id="rio-de-janeiro-novo-rio-rj",
        travel_date="2026-11-20",
    )

    assert len(quotes) == 2
    assert quotes[0].carrier_name == "Viação Cometa"


@pytest.mark.asyncio
async def test_clickbus_403_is_a_final_denial(monkeypatch):
    """HTTP 403 é recusa explícita: vira NotAuthorizedError, sem contorno."""
    collector = ClickBusCollector()

    class MockDeniedResponse:
        status_code = 403
        text = "ST: Forbidden request"
        headers: dict = {}

    async def mock_get(self, url, *args, **kwargs):
        return MockDeniedResponse()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    with pytest.raises(NotAuthorizedError, match="recusou o acesso automatizado"):
        await collector.fetch_quotes(
            origin_ext_id="sao-paulo-tiete-sp",
            dest_ext_id="rio-de-janeiro-novo-rio-rj",
            travel_date="2026-11-20",
        )

    # E o safe_fetch encerra a coleta registrando a negativa, sem tentar alternativas
    quotes = await collector.safe_fetch("sao-paulo-tiete-sp", "rio-de-janeiro-novo-rio-rj", "2026-11-20")
    assert quotes == []
    assert collector.access_denied is True


@pytest.mark.asyncio
async def test_clickbus_does_not_request_when_robots_disallows(monkeypatch):
    """Se o robots.txt proíbe a URL, nenhuma requisição chega a ser emitida."""
    from app.collectors import compliance

    class DenyAllRobots:
        async def can_fetch(self, url: str) -> bool:
            return False

        async def crawl_delay(self, url: str) -> float:
            return 0.0

    monkeypatch.setattr(compliance, "_shared_robots", DenyAllRobots())

    requested = []

    async def mock_get(self, url, *args, **kwargs):
        requested.append(url)
        raise AssertionError("Nenhuma requisição deveria ser emitida.")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    with pytest.raises(RobotsDisallowedError):
        await ClickBusCollector().fetch_quotes("sao-paulo-tiete-sp", "rio-de-janeiro-novo-rio-rj", "2026-11-20")

    assert requested == []
