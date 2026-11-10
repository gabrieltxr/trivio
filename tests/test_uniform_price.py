"""Alerta de preço idêntico: o provedor devolve o mesmo valor para voos distintos."""

from datetime import datetime

from app.analyzers.route_evaluator import RouteEvaluator
from app.collectors.base import StandardizedQuote
from app.services.air_quotes import UNIFORM_PRICE_MIN_FLIGHTS, flag_uniform_prices


def _air(price_total: float, hour: int = 7) -> StandardizedQuote:
    dep = datetime(2026, 11, 17, hour, 0)
    return StandardizedQuote(
        origin_external_id="CGH", destination_external_id="SDU", modal="AIR", provider_source="ignav",
        carrier_name="Azul Linhas Aéreas", service_class="ECONOMY",
        departure_time=dep, arrival_time=datetime(2026, 11, 17, hour + 1, 0),
        duration_minutes=60, door_to_door_duration_minutes=150,
        base_price=price_total - 40.0, taxes_price=40.0, baggage_fee_estimate=110.0,
        route_metadata={"is_direct": True, "stops_count": 0, "price_total_brl": price_total},
    )


def test_identical_prices_across_many_flights_are_flagged():
    quotes = flag_uniform_prices([_air(2737.0, hour=h) for h in range(7, 7 + UNIFORM_PRICE_MIN_FLIGHTS)])
    assert all(q.route_metadata["price_suspect"] is True for q in quotes)
    assert all(q.route_metadata["price_suspect_flights"] == UNIFORM_PRICE_MIN_FLIGHTS for q in quotes)


def test_varied_prices_are_not_flagged():
    quotes = flag_uniform_prices([_air(610.0), _air(688.0), _air(922.0)])
    assert all(q.route_metadata["price_suspect"] is False for q in quotes)
    assert all("price_suspect_flights" not in q.route_metadata for q in quotes)


def test_too_few_flights_are_not_flagged_even_if_equal():
    quotes = flag_uniform_prices([_air(500.0), _air(500.0)])
    assert all(q.route_metadata["price_suspect"] is False for q in quotes)


def test_missing_price_never_counts_as_uniform():
    a, b, c = _air(500.0), _air(500.0), _air(500.0)
    for q in (a, b, c):
        q.route_metadata.pop("price_total_brl")
    flag_uniform_prices([a, b, c])
    assert all(q.route_metadata["price_suspect"] is False for q in (a, b, c))


def test_suspect_flag_becomes_a_setback_shown_on_the_card():
    quotes = flag_uniform_prices([_air(2737.0, hour=h) for h in (7, 8, 9)])
    evaluation = RouteEvaluator.evaluate(quotes[0])
    assert any("Preço suspeito" in s and "3 voos" in s for s in evaluation.potential_setbacks)


def test_normal_quote_has_no_price_setback():
    quotes = flag_uniform_prices([_air(610.0), _air(688.0), _air(922.0)])
    evaluation = RouteEvaluator.evaluate(quotes[0])
    assert not any("Preço suspeito" in s for s in evaluation.potential_setbacks)
