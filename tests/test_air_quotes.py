"""Testes do AirQuoteService: cache de consultas, orçamento mensal e coalescência."""

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.collectors.base import CollectorTimeoutError, StandardizedQuote
from app.collectors.air.ignav import IgnavRequestError
from app.database import Base
from app.models import ProviderQuery
from app.services.air_quotes import AirProviderUnavailableError, AirQuoteService
from app.services.quote_pipeline import is_persistable_quote


def _quote(price=500.0):
    return StandardizedQuote(
        origin_external_id="CGH", destination_external_id="SDU", modal="AIR", provider_source="ignav",
        carrier_name="Gol Linhas Aéreas", service_class="ECONOMY",
        departure_time=datetime(2026, 12, 1, 8, 0), arrival_time=datetime(2026, 12, 1, 9, 0),
        duration_minutes=60, base_price=price - 40.0, taxes_price=40.0, baggage_fee_estimate=110.0,
        route_metadata={"origin_iata": "CGH", "destination_iata": "SDU", "ignav_id": "x"},
    )


class FakeCollector:
    """Coletor sem rede: conta chamadas e devolve o que for configurado."""

    provider_name = "ignav"
    modal = "AIR"

    def __init__(self, result=None, error=None, configured=True, delay=0.0):
        self.result = result if result is not None else [_quote()]
        self.error = error
        self.configured = configured
        self.delay = delay
        self.calls = 0
        self.client = self

    @property
    def is_configured(self):
        return self.configured

    async def fetch_quotes(self, origin, destination, travel_date):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return [StandardizedQuote(**{**q.__dict__, "route_metadata": dict(q.route_metadata)}) for q in self.result]

    async def booking_links(self, *args, **kwargs):
        self.calls += 1
        return {"booking_options": [{"links": [{"url": "https://cia.example/compra", "provider_type": "airline",
                                                "specificity": "exact_flight"}]}]}


class Clock:
    def __init__(self, start=datetime(2026, 11, 7, 12, 0)):
        self.now = start

    def __call__(self):
        return self.now


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'air.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _service(session_factory, collector, clock=None, budget=10, cache_max=7200):
    return AirQuoteService(collector=collector, session_factory=session_factory, monthly_budget=budget,
                           cache_max_seconds=cache_max, now=clock or Clock())


def _rows(session_factory):
    with session_factory() as session:
        return session.query(ProviderQuery).order_by(ProviderQuery.id).all()


# ==============================================================================
# 1. Cache
# ==============================================================================

@pytest.mark.asyncio
async def test_second_identical_search_is_served_from_cache(session_factory):
    collector = FakeCollector()
    service = _service(session_factory, collector)

    first = await service.get_quotes("CGH", "SDU", "2026-12-01")
    second = await service.get_quotes("cgh", "sdu", "2026-12-01")

    assert collector.calls == 1
    assert len(first) == len(second) == 1
    assert first[0].route_metadata["collector_stats"]["tier"] == "live"
    assert second[0].route_metadata["collector_stats"]["tier"] == "cache"
    assert second[0].departure_time == datetime(2026, 12, 1, 8, 0)
    assert second[0].total_effective_price == first[0].total_effective_price


@pytest.mark.asyncio
async def test_cached_quotes_are_independent_copies(session_factory):
    service = _service(session_factory, FakeCollector())
    await service.get_quotes("CGH", "SDU", "2026-12-01")

    a = await service.get_quotes("CGH", "SDU", "2026-12-01")
    a[0].route_metadata["price_thermometer"] = {"status": "LOW"}
    b = await service.get_quotes("CGH", "SDU", "2026-12-01")

    assert "price_thermometer" not in b[0].route_metadata


@pytest.mark.asyncio
async def test_cache_expires_at_the_cap(session_factory):
    clock = Clock()
    collector = FakeCollector()
    service = _service(session_factory, collector, clock=clock, cache_max=7200)

    await service.get_quotes("CGH", "SDU", "2026-12-01")   # voo a 24 dias: faixa longa (24 h), teto 2 h
    clock.now += timedelta(seconds=7100)
    await service.get_quotes("CGH", "SDU", "2026-12-01")
    assert collector.calls == 1

    clock.now += timedelta(seconds=200)
    await service.get_quotes("CGH", "SDU", "2026-12-01")
    assert collector.calls == 2


@pytest.mark.asyncio
async def test_imminent_flights_use_the_shorter_tier(session_factory):
    clock = Clock()
    collector = FakeCollector()
    service = _service(session_factory, collector, clock=clock)

    await service.get_quotes("CGH", "SDU", "2026-11-08")    # amanhã: faixa de 30 min
    row = _rows(session_factory)[0]
    assert (row.expires_at - row.requested_at) == timedelta(seconds=1800)


@pytest.mark.asyncio
async def test_different_dates_or_routes_are_different_queries(session_factory):
    collector = FakeCollector()
    service = _service(session_factory, collector)

    await service.get_quotes("CGH", "SDU", "2026-12-01")
    await service.get_quotes("CGH", "SDU", "2026-12-02")
    await service.get_quotes("SDU", "CGH", "2026-12-01")

    assert collector.calls == 3


@pytest.mark.asyncio
async def test_empty_result_is_cached_and_billed(session_factory):
    collector = FakeCollector(result=[])
    service = _service(session_factory, collector)

    assert await service.get_quotes("PET", "RIG", "2026-12-01") == []
    assert await service.get_quotes("PET", "RIG", "2026-12-01") == []

    assert collector.calls == 1
    [row] = _rows(session_factory)
    assert (row.status, row.billed) == ("empty", 1)


@pytest.mark.asyncio
async def test_route_not_found_is_cached_but_not_billed(session_factory):
    collector = FakeCollector(error=IgnavRequestError("sem rota", code="route_not_found", status_code=404))
    service = _service(session_factory, collector)

    assert await service.get_quotes("PET", "RIG", "2026-12-01") == []
    assert await service.get_quotes("PET", "RIG", "2026-12-01") == []

    assert collector.calls == 1
    [row] = _rows(session_factory)
    assert (row.status, row.billed) == ("empty", 0)


@pytest.mark.asyncio
async def test_errors_are_not_cached_nor_billed(session_factory):
    collector = FakeCollector(error=CollectorTimeoutError("timeout"))
    service = _service(session_factory, collector)

    for _ in range(2):
        with pytest.raises(CollectorTimeoutError):
            await service.get_quotes("CGH", "SDU", "2026-12-01")

    assert collector.calls == 2
    assert [(r.status, r.billed) for r in _rows(session_factory)] == [("error", 0), ("error", 0)]


@pytest.mark.asyncio
async def test_billing_refusal_is_not_retried_nor_billed(session_factory):
    """402 da conta: uma tentativa só, registrada como erro sem cobrança, e o erro sobe com o motivo."""
    from app.collectors.air.ignav import IgnavBillingError

    collector = FakeCollector(error=IgnavBillingError("Ignav HTTP 402 (billing_required)", reason="billing_required"))
    service = _service(session_factory, collector)

    with pytest.raises(IgnavBillingError) as info:
        await service.get_quotes("CGH", "SDU", "2026-12-01")

    assert info.value.reason == "billing_required"
    assert collector.calls == 1
    assert [(r.status, r.billed) for r in _rows(session_factory)] == [("error", 0)]


# ==============================================================================
# 2. Orçamento
# ==============================================================================

@pytest.mark.asyncio
async def test_budget_blocks_new_requests_but_not_cache_hits(session_factory):
    collector = FakeCollector()
    service = _service(session_factory, collector, budget=2)

    await service.get_quotes("CGH", "SDU", "2026-12-01")
    await service.get_quotes("CGH", "SDU", "2026-12-02")
    with pytest.raises(AirProviderUnavailableError) as info:
        await service.get_quotes("CGH", "SDU", "2026-12-03")

    assert info.value.reason == "budget_exhausted"
    assert collector.calls == 2
    assert len(await service.get_quotes("CGH", "SDU", "2026-12-01")) == 1   # cache continua valendo


@pytest.mark.asyncio
async def test_budget_resets_each_month(session_factory):
    clock = Clock(datetime(2026, 11, 30, 23, 0))
    service = _service(session_factory, FakeCollector(), clock=clock, budget=1)

    await service.get_quotes("CGH", "SDU", "2026-12-20")
    with pytest.raises(AirProviderUnavailableError):
        await service.get_quotes("CGH", "SDU", "2026-12-21")

    clock.now = datetime(2026, 12, 1, 0, 5)
    assert len(await service.get_quotes("CGH", "SDU", "2026-12-21")) == 1


@pytest.mark.asyncio
async def test_usage_counts_billed_requests(session_factory):
    service = _service(session_factory, FakeCollector(), budget=800)
    await service.get_quotes("CGH", "SDU", "2026-12-01")
    await service.get_quotes("CGH", "SDU", "2026-12-01")   # cache: não conta

    usage = service.usage()

    assert usage["requests_used_this_month"] == 1
    assert usage["requests_left_this_month"] == 799


@pytest.mark.asyncio
async def test_not_configured_and_disabled(session_factory):
    with pytest.raises(AirProviderUnavailableError) as info:
        await _service(session_factory, FakeCollector(configured=False)).get_quotes("CGH", "SDU", "2026-12-01")
    assert info.value.reason == "not_configured"

    with pytest.raises(AirProviderUnavailableError) as info:
        await _service(session_factory, FakeCollector(), budget=0).get_quotes("CGH", "SDU", "2026-12-01")
    assert info.value.reason == "disabled"


# ==============================================================================
# 3. Coalescência
# ==============================================================================

@pytest.mark.asyncio
async def test_concurrent_identical_searches_share_one_request(session_factory):
    collector = FakeCollector(delay=0.05)
    service = _service(session_factory, collector)

    results = await asyncio.gather(*[service.get_quotes("CGH", "SDU", "2026-12-01") for _ in range(5)])

    assert collector.calls == 1
    assert all(len(r) == 1 for r in results)
    tiers = sorted(r[0].route_metadata["collector_stats"]["tier"] for r in results)
    assert tiers == ["coalesced"] * 4 + ["live"]


@pytest.mark.asyncio
async def test_concurrent_waiters_receive_the_same_error(session_factory):
    collector = FakeCollector(error=CollectorTimeoutError("timeout"), delay=0.05)
    service = _service(session_factory, collector)

    results = await asyncio.gather(
        *[service.get_quotes("CGH", "SDU", "2026-12-01") for _ in range(3)], return_exceptions=True
    )

    assert collector.calls == 1
    assert all(isinstance(r, CollectorTimeoutError) for r in results)


# ==============================================================================
# 4. Link de compra e persistência
# ==============================================================================

@pytest.mark.asyncio
async def test_booking_link_is_billed_once_and_cached(session_factory):
    collector = FakeCollector()
    service = _service(session_factory, collector)

    url1 = await service.get_booking_url("id12345678", "CGH", "SDU", "2026-12-01", "AD", "6050")
    url2 = await service.get_booking_url("id12345678", "CGH", "SDU", "2026-12-01", "AD", "6050")

    assert url1 == url2 == "https://cia.example/compra"
    assert collector.calls == 1
    assert service.usage()["requests_used_this_month"] == 1


def test_live_quotes_stay_out_of_permanent_history_by_default(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "AIR_PERSIST_LIVE_QUOTES", False)
    assert not is_persistable_quote(_quote())

    monkeypatch.setattr(settings, "AIR_PERSIST_LIVE_QUOTES", True)
    assert is_persistable_quote(_quote())
