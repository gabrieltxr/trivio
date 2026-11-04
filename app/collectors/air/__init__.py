"""Módulo de coletores aéreos do Trivio."""

from app.collectors.air.amadeus import (
    BRAZILIAN_AIRLINES,
    AmadeusCollector,
    parse_amadeus_flight_offers,
    parse_iso_duration,
)

__all__ = [
    "BRAZILIAN_AIRLINES",
    "AmadeusCollector",
    "parse_amadeus_flight_offers",
    "parse_iso_duration",
]
