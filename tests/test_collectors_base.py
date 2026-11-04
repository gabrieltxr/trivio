"""Testes unitários para o contrato BaseCollector, StandardizedQuote e Resiliência Tenacity."""

from datetime import datetime
from typing import List
import httpx
import pytest

from app.collectors.base import (
    BaseCollector,
    CollectorBlockedError,
    CollectorError,
    CollectorNetworkError,
    CollectorParsingError,
    CollectorRateLimitError,
    CollectorTimeoutError,
    StandardizedQuote,
    collector_retry,
    is_transient_error,
)


# ==============================================================================
# 1. Testes de DTO StandardizedQuote
# ==============================================================================

def test_standardized_quote_properties():
    """Valida cálculo de preço total efetivo, dedup_signature e serialização to_dict."""
    dep = datetime(2026, 11, 20, 10, 0)
    arr = datetime(2026, 11, 20, 11, 15)

    quote = StandardizedQuote(
        origin_external_id="GRU",
        destination_external_id="SDU",
        modal="AIR",
        provider_source="amadeus",
        carrier_name="LATAM",
        service_class="ECONOMY",
        departure_time=dep,
        arrival_time=arr,
        duration_minutes=75,
        door_to_door_duration_minutes=200,
        base_price=350.00,
        taxes_price=35.50,
        baggage_fee_estimate=120.00,
        transfer_cost_estimate=75.00,
        trivio_score=520.5,
        route_metadata={"is_direct": True},
    )

    # 350 + 35.50 + 120 + 75 = 580.50
    assert quote.total_effective_price == 580.50
    assert len(quote.dedup_signature) == 32

    serialized = quote.to_dict()
    assert serialized["modal"] == "AIR"
    assert serialized["total_effective_price"] == 580.50
    assert serialized["dedup_signature"] == quote.dedup_signature
    assert serialized["route_metadata"]["is_direct"] is True


# ==============================================================================
# 2. Testes da Política de Classificação de Erros Transitórios
# ==============================================================================

def test_is_transient_error_classification():
    """Valida identificação correta de erros transitórios vs permanentes."""
    # Erros transitórios que devem sofrer retry
    assert is_transient_error(CollectorNetworkError("Connection reset")) is True
    assert is_transient_error(CollectorTimeoutError("Gateway timeout")) is True
    assert is_transient_error(CollectorRateLimitError("429 Too Many Requests")) is True
    assert is_transient_error(httpx.ConnectTimeout("Connect timeout")) is True
    assert is_transient_error(httpx.ReadTimeout("Read timeout")) is True

    # HTTP Status transitórios
    request = httpx.Request("GET", "https://api.example.com")
    resp_429 = httpx.Response(status_code=429, request=request)
    assert is_transient_error(httpx.HTTPStatusError("Rate limited", request=request, response=resp_429)) is True

    resp_503 = httpx.Response(status_code=503, request=request)
    assert is_transient_error(httpx.HTTPStatusError("Service Unavailable", request=request, response=resp_503)) is True

    # Erros NÃO transitórios (não devem sofrer retry cego)
    assert is_transient_error(CollectorBlockedError("403 Cloudflare challenge")) is False
    assert is_transient_error(CollectorParsingError("Invalid JSON structure")) is False
    assert is_transient_error(ValueError("Invalid argument")) is False

    resp_404 = httpx.Response(status_code=404, request=request)
    assert is_transient_error(httpx.HTTPStatusError("Not Found", request=request, response=resp_404)) is False


# ==============================================================================
# 3. Testes do Decorador Tenacity (collector_retry)
# ==============================================================================

@pytest.mark.asyncio
async def test_collector_retry_recovers_after_transient_failures():
    """Valida recuperação com sucesso após falhas transitórias consecutivas."""
    attempts = 0

    @collector_retry(max_attempts=3, min_wait=0.01, max_wait=0.05)
    async def flaky_api_call():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise CollectorTimeoutError(f"Falha tentativa {attempts}")
        return "success"

    result = await flaky_api_call()
    assert result == "success"
    assert attempts == 3


@pytest.mark.asyncio
async def test_collector_retry_exhausts_and_reraises():
    """Valida que esgota retentativas e relança o erro caso o problema persista."""
    attempts = 0

    @collector_retry(max_attempts=3, min_wait=0.01, max_wait=0.05)
    async def always_failing_api():
        nonlocal attempts
        attempts += 1
        raise CollectorNetworkError("API offline")

    with pytest.raises(CollectorNetworkError, match="API offline"):
        await always_failing_api()

    assert attempts == 3


@pytest.mark.asyncio
async def test_collector_retry_fails_immediately_on_non_transient():
    """Erros não transitórios devem falhar imediatamente sem retry inútil."""
    attempts = 0

    @collector_retry(max_attempts=3, min_wait=0.01, max_wait=0.05)
    async def blocked_api():
        nonlocal attempts
        attempts += 1
        raise CollectorBlockedError("Cloudflare Turnstile 403")

    with pytest.raises(CollectorBlockedError):
        await blocked_api()

    # Não deve tentar mais de uma vez
    assert attempts == 1


# ==============================================================================
# 4. Testes de BaseCollector e Safe Fetch (Tier 1 vs Tier 2 Fallback)
# ==============================================================================

class MockDirectRoadCollector(BaseCollector):
    """Implementação mock de coletor rodoviário com sucesso no Tier 1."""
    provider_name = "mock_road"
    modal = "ROAD"

    async def fetch_quotes(self, origin_ext_id: str, dest_ext_id: str, travel_date: str) -> List[StandardizedQuote]:
        return [
            StandardizedQuote(
                origin_external_id=origin_ext_id,
                destination_external_id=dest_ext_id,
                modal=self.modal,
                provider_source=self.provider_name,
                carrier_name="Viação Teste",
                service_class="CONVENCIONAL",
                departure_time=datetime(2026, 11, 20, 8, 0),
                arrival_time=datetime(2026, 11, 20, 14, 0),
                duration_minutes=360,
                base_price=90.0,
            )
        ]


class MockBlockedRoadCollector(BaseCollector):
    """Implementação mock que sofre bloqueio WAF e aciona Tier 2 Headless Fallback."""
    provider_name = "mock_blocked"
    modal = "ROAD"
    supports_headless_fallback = True

    async def fetch_quotes(self, origin_ext_id: str, dest_ext_id: str, travel_date: str) -> List[StandardizedQuote]:
        raise CollectorBlockedError("Blocked by Cloudflare WAF")

    async def fallback_fetch_quotes(self, origin_ext_id: str, dest_ext_id: str, travel_date: str) -> List[StandardizedQuote]:
        return [
            StandardizedQuote(
                origin_external_id=origin_ext_id,
                destination_external_id=dest_ext_id,
                modal=self.modal,
                provider_source=self.provider_name,
                carrier_name="Viação Salva Pelo Headless",
                service_class="LEITO",
                departure_time=datetime(2026, 11, 20, 22, 0),
                arrival_time=datetime(2026, 11, 21, 6, 0),
                duration_minutes=480,
                base_price=180.0,
            )
        ]


class MockCrashingCollector(BaseCollector):
    """Implementação mock que sofre falha inesperada fatal."""
    provider_name = "mock_crashing"
    modal = "AIR"

    async def fetch_quotes(self, origin_ext_id: str, dest_ext_id: str, travel_date: str) -> List[StandardizedQuote]:
        raise RuntimeError("Falha inesperada no parser")


@pytest.mark.asyncio
async def test_base_collector_safe_fetch_success():
    """Valida safe_fetch bem-sucedido com injeção de estatísticas de latência."""
    collector = MockDirectRoadCollector()
    quotes = await collector.safe_fetch("orig_1", "dest_1", "2026-11-20")

    assert len(quotes) == 1
    stats = quotes[0].route_metadata.get("collector_stats", {})
    assert stats["provider"] == "mock_road"
    assert stats["tier"] == "tier_1_httpx"
    assert "latency_ms" in stats
    assert stats["latency_ms"] >= 0.0


@pytest.mark.asyncio
async def test_base_collector_safe_fetch_triggers_tier2_fallback():
    """Valida acionamento automático de Tier 2 quando Tier 1 sofre CollectorBlockedError."""
    collector = MockBlockedRoadCollector()
    quotes = await collector.safe_fetch("orig_1", "dest_1", "2026-11-20")

    assert len(quotes) == 1
    assert quotes[0].carrier_name == "Viação Salva Pelo Headless"
    stats = quotes[0].route_metadata.get("collector_stats", {})
    assert stats["provider"] == "mock_blocked"
    assert stats["tier"] == "tier_2_headless_fallback"


@pytest.mark.asyncio
async def test_base_collector_safe_fetch_isolates_fatal_exceptions():
    """safe_fetch não deve lançar exceção para não derrubar o WebSocket / orquestrador concorrente."""
    collector = MockCrashingCollector()
    quotes = await collector.safe_fetch("orig_1", "dest_1", "2026-11-20")

    # Retorna lista vazia de forma segura
    assert quotes == []
