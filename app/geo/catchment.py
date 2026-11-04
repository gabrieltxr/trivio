"""Trivio Geodesic Catchment Area Module.

Implements the Rome2Rio / Skyscanner pattern: finds all relevant airports and
bus terminals within a configurable radius (e.g. 100-120 km) of a city or point,
calculating the ground transfer cost and travel time.
"""

from dataclasses import dataclass
from typing import List, Optional
from geopy.distance import geodesic
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Hub, Terminal, ModalType


@dataclass
class CatchmentTerminal:
    terminal_id: int
    terminal_name: str
    official_code: Optional[str]
    modal: ModalType
    hub_id: int
    hub_name: str
    latitude: float
    longitude: float
    distance_km: float
    is_primary_hub: bool
    estimated_transfer_cost_brl: float
    estimated_transfer_minutes: int


class CatchmentFinder:
    """Finds terminals in geographic proximity to a hub or coordinate."""

    def __init__(self, db: Session):
        self.db = db

    def find_terminals_near_coordinates(
        self,
        lat: float,
        lon: float,
        max_radius_km: Optional[float] = None,
        modal_filter: Optional[ModalType] = None,
        primary_hub_id: Optional[int] = None,
    ) -> List[CatchmentTerminal]:
        """Find all terminals within max_radius_km of (lat, lon)."""
        radius = max_radius_km if max_radius_km is not None else settings.DEFAULT_CATCHMENT_RADIUS_KM
        origin_point = (lat, lon)

        query = self.db.query(Terminal)
        if modal_filter:
            query = query.filter(Terminal.modal == modal_filter)

        terminals = query.all()
        results: List[CatchmentTerminal] = []

        for term in terminals:
            if term.latitude is None or term.longitude is None:
                continue

            terminal_point = (term.latitude, term.longitude)
            dist_km = round(geodesic(origin_point, terminal_point).kilometers, 2)

            if dist_km <= radius:
                is_primary = (primary_hub_id is not None and term.hub_id == primary_hub_id)

                # Transfer cost calculation:
                # If it's the primary hub's native terminal, use the baseline transfer cost.
                # If it's a secondary terminal in the catchment area (e.g. Campinas/VCP from SP),
                # compute ground transfer cost (bus/shuttle/uber).
                if is_primary:
                    transfer_cost = term.estimated_transfer_cost_brl or 25.0
                    transfer_mins = term.estimated_transfer_minutes or 30
                else:
                    # Ground transfer: base shuttle + distance factor
                    transfer_cost = round(max(term.estimated_transfer_cost_brl, 30.0 + (dist_km * 0.45)), 2)
                    transfer_mins = max(term.estimated_transfer_minutes, int(dist_km * 1.15) + 20)

                results.append(
                    CatchmentTerminal(
                        terminal_id=term.id,
                        terminal_name=term.name,
                        official_code=term.official_code,
                        modal=term.modal,
                        hub_id=term.hub_id,
                        hub_name=term.hub.name if term.hub else "",
                        latitude=term.latitude,
                        longitude=term.longitude,
                        distance_km=dist_km,
                        is_primary_hub=is_primary,
                        estimated_transfer_cost_brl=transfer_cost,
                        estimated_transfer_minutes=transfer_mins,
                    )
                )

        # Sort by distance (closest first)
        results.sort(key=lambda x: (not x.is_primary_hub, x.distance_km))
        return results

    def find_terminals_for_hub(
        self,
        hub_id: int,
        max_radius_km: Optional[float] = None,
        modal_filter: Optional[ModalType] = None,
    ) -> List[CatchmentTerminal]:
        """Find all terminals within the catchment area of a specific Hub."""
        hub = self.db.query(Hub).filter(Hub.id == hub_id).first()
        if not hub:
            return []

        return self.find_terminals_near_coordinates(
            lat=hub.latitude,
            lon=hub.longitude,
            max_radius_km=max_radius_km,
            modal_filter=modal_filter,
            primary_hub_id=hub.id,
        )
