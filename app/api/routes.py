"""Endpoints REST da API v1 do Trivio.

Disponibiliza rotas para:
1. Hubs: Listagem, busca textual por autocomplete e detalhes de municípios âncora.
2. Terminais: Filtro de aeroportos e rodoviárias por hub ou modalidade.
3. Catchment Area: Busca geodésica de terminais num raio de até 120km (Rome2Rio pattern).
4. Cotações em Cache: Consulta cotações pré-coletadas válidas e ordenadas pelo Trivio Score.
"""

from datetime import datetime, timedelta, timezone
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.schemas import (
    CachedSearchResponse,
    CatchmentQueryResponse,
    CatchmentTerminalResponse,
    FareQuoteResponse,
    HubBase,
    HubDetailResponse,
    TerminalBase,
)
from app.database import get_db
from app.geo.catchment import CatchmentFinder
from app.models import FareQuote, Hub, ModalType, Terminal

logger = logging.getLogger("trivio.api.routes")

router = APIRouter(prefix="/api/v1", tags=["Travel Intelligence"])


# ==============================================================================
# 1. Endpoints de Hubs (Municípios Âncora)
# ==============================================================================

@router.get("/hubs", response_model=List[HubBase], summary="Listar ou buscar Hubs âncora")
def list_hubs(
    query: Optional[str] = Query(None, description="Busca por nome da cidade ou código IBGE"),
    state: Optional[str] = Query(None, min_length=2, max_length=2, description="Filtro por UF (ex: SP, RJ, RS)"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Retorna a lista de Hubs urbanos com suporte a autocomplete e filtro por estado."""
    stmt = select(Hub)

    if query:
        clean_q = f"%{query.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Hub.name).like(clean_q) | (Hub.ibge_code.like(clean_q))
        )

    if state:
        stmt = stmt.where(func.upper(Hub.state) == state.strip().upper())

    stmt = stmt.order_by(Hub.name.asc()).offset(offset).limit(limit)
    hubs = db.execute(stmt).scalars().all()
    return hubs


@router.get("/hubs/{hub_id}", response_model=HubDetailResponse, summary="Obter detalhes do Hub e seus terminais")
def get_hub_detail(
    hub_id: int,
    db: Session = Depends(get_db),
):
    """Retorna os dados cadastrais do Hub acompanhado de todos os aeroportos e rodoviárias vinculados."""
    stmt = select(Hub).options(joinedload(Hub.terminals)).where(Hub.id == hub_id)
    hub = db.execute(stmt).scalars().first()

    if not hub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hub com id {hub_id} não encontrado.",
        )
    return hub


# ==============================================================================
# 2. Endpoints de Terminais (Aeroportos e Rodoviárias)
# ==============================================================================

@router.get("/terminals", response_model=List[TerminalBase], summary="Listar terminais com filtros")
def list_terminals(
    hub_id: Optional[int] = Query(None, description="ID do Hub proprietário"),
    modal: Optional[str] = Query(None, description="Modalidade: AIR ou ROAD"),
    code: Optional[str] = Query(None, description="Código IATA ou oficial do terminal (ex: CGH, TIETE)"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Lista terminais com suporte a filtros por modalidade de transporte, código e Hub."""
    stmt = select(Terminal)

    if hub_id is not None:
        stmt = stmt.where(Terminal.hub_id == hub_id)

    if modal:
        modal_clean = modal.strip().upper()
        if "AIR" in modal_clean:
            stmt = stmt.where(Terminal.modal == ModalType.AIR)
        elif "ROAD" in modal_clean:
            stmt = stmt.where(Terminal.modal == ModalType.ROAD)

    if code:
        stmt = stmt.where(func.upper(Terminal.official_code) == code.strip().upper())

    stmt = stmt.order_by(Terminal.name.asc()).offset(offset).limit(limit)
    terminals = db.execute(stmt).scalars().all()
    return terminals


# ==============================================================================
# 3. Endpoint de Catchment Area (Rome2Rio Geodesic Search)
# ==============================================================================

@router.get("/catchment", response_model=CatchmentQueryResponse, summary="Buscar terminais em raio geodésico")
def get_catchment_terminals(
    hub_id: Optional[int] = Query(None, description="ID do Hub de referência"),
    lat: Optional[float] = Query(None, ge=-90.0, le=90.0, description="Latitude do ponto de partida"),
    lon: Optional[float] = Query(None, ge=-180.0, le=180.0, description="Longitude do ponto de partida"),
    radius_km: float = Query(120.0, ge=10.0, le=300.0, description="Raio de busca em quilômetros"),
    modal: Optional[str] = Query(None, description="Filtro opcional de modal: AIR ou ROAD"),
    db: Session = Depends(get_db),
):
    """Localiza todos os aeroportos e rodoviárias em um raio geodésico de até 300km."""
    modal_filter: Optional[ModalType] = None
    if modal:
        modal_clean = modal.strip().upper()
        if "AIR" in modal_clean:
            modal_filter = ModalType.AIR
        elif "ROAD" in modal_clean:
            modal_filter = ModalType.ROAD

    finder = CatchmentFinder(db)
    origin_info = {}

    if hub_id is not None:
        hub = db.execute(select(Hub).where(Hub.id == hub_id)).scalar_one_or_none()
        if not hub:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Hub com id {hub_id} não encontrado para consulta geodésica.",
            )
        origin_info = {
            "type": "hub",
            "hub_id": hub.id,
            "name": hub.name,
            "state": hub.state,
            "latitude": hub.latitude,
            "longitude": hub.longitude,
        }
        terminals = finder.find_terminals_for_hub(
            hub_id=hub_id,
            max_radius_km=radius_km,
            modal_filter=modal_filter,
        )
    elif lat is not None and lon is not None:
        origin_info = {
            "type": "coordinates",
            "latitude": lat,
            "longitude": lon,
        }
        terminals = finder.find_terminals_near_coordinates(
            lat=lat,
            lon=lon,
            max_radius_km=radius_km,
            modal_filter=modal_filter,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Informe o parâmetro 'hub_id' OU as coordenadas 'lat' e 'lon'.",
        )

    # Converte os resultados do CatchmentTerminal DTO
    terminal_responses = [
        CatchmentTerminalResponse(
            terminal_id=t.terminal_id,
            terminal_name=t.terminal_name,
            official_code=t.official_code,
            modal=t.modal.value if hasattr(t.modal, "value") else str(t.modal),
            hub_id=t.hub_id,
            hub_name=t.hub_name,
            latitude=t.latitude,
            longitude=t.longitude,
            distance_km=t.distance_km,
            is_primary_hub=t.is_primary_hub,
            estimated_transfer_cost_brl=t.estimated_transfer_cost_brl,
            estimated_transfer_minutes=t.estimated_transfer_minutes,
        )
        for t in terminals
    ]

    return CatchmentQueryResponse(
        origin=origin_info,
        radius_km=radius_km,
        total_terminals=len(terminal_responses),
        terminals=terminal_responses,
    )


# ==============================================================================
# 4. Endpoint de Cotações em Cache (Fare Quotes)
# ==============================================================================

@router.get("/quotes/cached", response_model=CachedSearchResponse, summary="Consultar cotações em cache por rota")
def get_cached_quotes(
    origin_terminal_id: int = Query(..., description="ID do terminal de embarque"),
    destination_terminal_id: int = Query(..., description="ID do terminal de desembarque"),
    travel_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Data da viagem (YYYY-MM-DD)"),
    modal: Optional[str] = Query(None, description="Filtro de modal: AIR ou ROAD"),
    include_expired: bool = Query(False, description="Se True, inclui cotações expiradas para análise histórica"),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Consulta bilhetes registrados em cache para o par de terminais especificado."""
    stmt = select(FareQuote).where(
        FareQuote.origin_terminal_id == origin_terminal_id,
        FareQuote.destination_terminal_id == destination_terminal_id,
    )

    if not include_expired:
        now = datetime.now(timezone.utc)
        stmt = stmt.where(FareQuote.expires_at > now)

    if travel_date:
        try:
            start_dt = datetime.strptime(travel_date, "%Y-%m-%d")
            end_dt = start_dt + timedelta(days=1)
            stmt = stmt.where(
                FareQuote.departure_time >= start_dt,
                FareQuote.departure_time < end_dt,
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Formato de data inválido. Utilize YYYY-MM-DD.",
            )

    if modal:
        modal_clean = modal.strip().upper()
        if "AIR" in modal_clean:
            stmt = stmt.where(FareQuote.modal == ModalType.AIR)
        elif "ROAD" in modal_clean:
            stmt = stmt.where(FareQuote.modal == ModalType.ROAD)

    # Ordena pelo Trivio Score (melhores opções primeiro)
    stmt = stmt.order_by(
        FareQuote.trivio_score.asc().nulls_last(),
        FareQuote.total_effective_price.asc(),
    ).limit(limit)

    quotes = db.execute(stmt).scalars().all()
    now_utc = datetime.now(timezone.utc)

    quote_responses = []
    for q in quotes:
        exp_at = q.expires_at
        # Normaliza timezone para comparação
        if exp_at.tzinfo is None:
            is_expired = exp_at < datetime.now()
        else:
            is_expired = exp_at < now_utc

        quote_responses.append(
            FareQuoteResponse(
                id=q.id,
                dedup_signature=q.dedup_signature,
                origin_terminal_id=q.origin_terminal_id,
                destination_terminal_id=q.destination_terminal_id,
                modal=q.modal.value if hasattr(q.modal, "value") else str(q.modal),
                provider_source=q.provider_source,
                carrier_name=q.carrier_name,
                service_class=q.service_class,
                departure_time=q.departure_time,
                arrival_time=q.arrival_time,
                duration_minutes=q.duration_minutes,
                door_to_door_duration_minutes=q.door_to_door_duration_minutes,
                base_price=q.base_price,
                taxes_price=q.taxes_price or 0.0,
                baggage_fee_estimate=q.baggage_fee_estimate or 0.0,
                transfer_cost_estimate=q.transfer_cost_estimate or 0.0,
                total_effective_price=q.total_effective_price,
                trivio_score=q.trivio_score,
                route_metadata=q.route_metadata or {},
                collected_at=q.collected_at,
                expires_at=q.expires_at,
                is_expired=is_expired,
            )
        )

    return CachedSearchResponse(
        origin_terminal_id=origin_terminal_id,
        destination_terminal_id=destination_terminal_id,
        travel_date=travel_date,
        total_quotes=len(quote_responses),
        quotes=quote_responses,
    )
