"""Pydantic Schemas para a Camada de API REST do Trivio."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


# ==============================================================================
# 1. Schemas de Terminais e Hubs
# ==============================================================================

class TerminalBase(BaseModel):
    id: int
    hub_id: int
    name: str
    modal: str
    official_code: Optional[str] = None
    latitude: float
    longitude: float
    avg_city_center_distance_km: float = 10.0
    estimated_transfer_cost_brl: float = 25.0
    estimated_transfer_minutes: int = 30

    model_config = ConfigDict(from_attributes=True)


class HubBase(BaseModel):
    id: int
    ibge_code: str
    name: str
    state: str
    latitude: float
    longitude: float

    model_config = ConfigDict(from_attributes=True)


class HubDetailResponse(HubBase):
    terminals: List[TerminalBase] = Field(default_factory=list)


# ==============================================================================
# 2. Schemas de Catchment Area (Rome2Rio Pattern)
# ==============================================================================

class CatchmentTerminalResponse(BaseModel):
    terminal_id: int
    terminal_name: str
    official_code: Optional[str] = None
    modal: str
    hub_id: int
    hub_name: str
    latitude: float
    longitude: float
    distance_km: float
    is_primary_hub: bool
    estimated_transfer_cost_brl: float
    estimated_transfer_minutes: int


class CatchmentQueryResponse(BaseModel):
    origin: Dict[str, Any]
    radius_km: float
    total_terminals: int
    terminals: List[CatchmentTerminalResponse]


# ==============================================================================
# 3. Schemas de Cotações (Fare Quotes em Cache)
# ==============================================================================

class FareQuoteResponse(BaseModel):
    id: int
    dedup_signature: str
    origin_terminal_id: int
    destination_terminal_id: int
    modal: str
    provider_source: str
    carrier_name: str
    service_class: Optional[str] = None
    departure_time: datetime
    arrival_time: datetime
    duration_minutes: int
    door_to_door_duration_minutes: int
    base_price: float
    taxes_price: float
    baggage_fee_estimate: float
    transfer_cost_estimate: float
    total_effective_price: float
    trivio_score: Optional[float] = None
    route_metadata: Dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime
    expires_at: datetime
    is_expired: bool = False

    model_config = ConfigDict(from_attributes=True)


class CachedSearchResponse(BaseModel):
    origin_terminal_id: int
    destination_terminal_id: int
    travel_date: Optional[str] = None
    total_quotes: int
    quotes: List[FareQuoteResponse]


# ==============================================================================
# 4. Schemas de Autocomplete e Busca de Localidades
# ==============================================================================

class LocationSuggestion(BaseModel):
    id: str
    type: str  # "hub" | "airport" | "bus_station"
    name: str
    city: str
    state: str
    code: str
    modal: str  # "ALL" | "AIR" | "ROAD"
    title: str
    subtitle: str
    highlight: Optional[str] = None

