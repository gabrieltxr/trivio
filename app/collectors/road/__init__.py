"""Módulo de coletores rodoviários do Trivio."""

from app.collectors.road.clickbus import (
    ClickBusCollector,
    normalize_bus_service_class,
    parse_clickbus_trips,
)

__all__ = [
    "ClickBusCollector",
    "normalize_bus_service_class",
    "parse_clickbus_trips",
]
