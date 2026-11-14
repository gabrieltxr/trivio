"""Trivio Route Evaluator, Trivio Scorer, Hacker Fares, Price Thermometer & Virtual Interlining Package."""

from app.analyzers.interliner import (
    TransferLeg,
    VirtualInterlineRoute,
    build_virtual_interline_routes,
    find_interline_routes,
    calculate_terminal_transfer,
)
from app.analyzers.price_thermometer import (
    PriceThermometerResult,
    enrich_quote_with_thermometer,
    enrich_quotes_batch_with_thermometer,
    evaluate_price_distribution,
    fetch_historical_prices_from_db,
    fetch_route_monthly_means,
    MIN_OFFICIAL_MONTHS,
)
from app.analyzers.route_evaluator import (
    ConnectionDiagnostic,
    MAJOR_BRAZILIAN_HUBS,
    RouteEvaluation,
    RouteEvaluator,
    diagnose_connections,
    enrich_quote_with_evaluation,
    evaluate_route,
)
from app.analyzers.trivio_scorer import (
    COMFORT_FACTORS,
    HackerFareCombination,
    TrivioScoreBreakdown,
    calculate_score_breakdown,
    calculate_trivio_score,
    determine_comfort_factor,
    generate_hacker_fares,
    score_and_rank_quotes,
)

__all__ = [
    # Route Evaluator
    "ConnectionDiagnostic",
    "MAJOR_BRAZILIAN_HUBS",
    "RouteEvaluation",
    "RouteEvaluator",
    "diagnose_connections",
    "enrich_quote_with_evaluation",
    "evaluate_route",
    # Trivio Scorer & Hacker Fares
    "COMFORT_FACTORS",
    "HackerFareCombination",
    "TrivioScoreBreakdown",
    "calculate_score_breakdown",
    "calculate_trivio_score",
    "determine_comfort_factor",
    "generate_hacker_fares",
    "score_and_rank_quotes",
    # Price Thermometer
    "PriceThermometerResult",
    "enrich_quote_with_thermometer",
    "enrich_quotes_batch_with_thermometer",
    "evaluate_price_distribution",
    "fetch_historical_prices_from_db",
    "fetch_route_monthly_means",
    "MIN_OFFICIAL_MONTHS",
    # Virtual Interlining
    "TransferLeg",
    "VirtualInterlineRoute",
    "find_interline_routes",
    "build_virtual_interline_routes",
    "calculate_terminal_transfer",
]
