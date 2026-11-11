"""Ônibus da busca a partir de feeds GTFS (horário real + tarifa cadastrada do feed).

Entra na busca quando a série da ANTT não cobre a rota (ex: linhas estaduais de SP).
Diferenças para as cotações da ANTT (app/services/road_quotes.py):
- o horário e a empresa vêm da grade do feed (`bus_schedules` com `feed` preenchido);
- o preço é a tarifa cadastrada da linha (`gtfs_route_fares`), não a média vendida;
- a classe de serviço não existe no GTFS: a cotação recebe `service_class` = "ONIBUS".

Assim como a ANTT, nada disto é oferta de compra: não tem link, não é gravado no banco.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.analyzers.interliner import MAX_INTERMODAL_CONNECTION_MINUTES, MIN_INTERMODAL_CONNECTION_MINUTES
from app.collectors.antt.monitriip import build_route_key
from app.collectors.base import StandardizedQuote
from app.config import settings
from app.models import BusSchedule, GtfsRouteFare, Hub, Terminal
from app.services.road_quotes import (
    MAX_DEPARTURES_PER_CLASS,
    _at,
    _operates,
    _spread,
    road_distance_km,
    road_duration_minutes,
)

SERVICE_CLASS = "ONIBUS"
PRICE_BASIS_GTFS = "gtfs_fare_regulated"
GTFS_NOTICE = (
    "Horário do feed GTFS e tarifa cadastrada da linha (tarifa regulada, não é o preço de venda "
    "desta partida). Não está disponível para compra."
)


def _latest_period(db: Session, feed: str) -> Optional[str]:
    return db.query(func.max(BusSchedule.source_period)).filter(BusSchedule.feed == feed).scalar()


def _gtfs_feeds(db: Session) -> List[str]:
    return [f for (f,) in db.query(BusSchedule.feed).filter(BusSchedule.feed.isnot(None)).distinct()]


def gtfs_departures(db: Session, orig_hub: Hub, dest_hub: Hub, day: date) -> List[BusSchedule]:
    """Partidas GTFS que operam no dia, no período mais recente de cada feed."""
    route_key = build_route_key(orig_hub.name, orig_hub.state, dest_hub.name, dest_hub.state)
    found: List[BusSchedule] = []
    for feed in _gtfs_feeds(db):
        period = _latest_period(db, feed)
        rows = (
            db.query(BusSchedule)
            .filter(
                BusSchedule.feed == feed,
                BusSchedule.source_period == period,
                BusSchedule.route_key == route_key,
            )
            .order_by(BusSchedule.departure_time, BusSchedule.company_name)
            .all()
        )
        found.extend(r for r in rows if _operates(r, day))
    return sorted(found, key=lambda r: r.departure_time)


def gtfs_fare(db: Session, feed: str, route_key: str) -> Optional[GtfsRouteFare]:
    """Tarifa da linha no período mais recente do feed."""
    period = db.query(func.max(GtfsRouteFare.source_period)).filter(GtfsRouteFare.feed == feed).scalar()
    if not period:
        return None
    return (
        db.query(GtfsRouteFare)
        .filter(GtfsRouteFare.feed == feed, GtfsRouteFare.source_period == period,
                GtfsRouteFare.route_key == route_key)
        .order_by(GtfsRouteFare.price_brl)
        .first()
    )


def has_gtfs_route(db: Session, orig_hub: Hub, dest_hub: Hub) -> bool:
    route_key = build_route_key(orig_hub.name, orig_hub.state, dest_hub.name, dest_hub.state)
    return db.query(BusSchedule.id).filter(
        BusSchedule.feed.isnot(None), BusSchedule.route_key == route_key
    ).first() is not None


def _quote(
    schedule: BusSchedule,
    fare: GtfsRouteFare,
    orig_term: Terminal,
    dest_term: Terminal,
    departure: datetime,
    duration: int,
    distance: float,
) -> StandardizedQuote:
    return StandardizedQuote(
        origin_external_id=orig_term.official_code,
        destination_external_id=dest_term.official_code,
        modal="ROAD",
        provider_source=f"gtfs_{schedule.feed}",
        carrier_name=f"{schedule.company_name} · Ônibus (horário GTFS)",
        service_class=SERVICE_CLASS,
        departure_time=departure,
        arrival_time=departure + timedelta(minutes=duration),
        duration_minutes=duration,
        door_to_door_duration_minutes=duration + settings.BUS_TERMINAL_CHECKIN_BUFFER_MINUTES,
        base_price=fare.price_brl,
        route_metadata={
            "is_simulated": True,
            "is_direct": True,
            "stops_count": 0,
            "price_basis": PRICE_BASIS_GTFS,
            "simulation_notice": GTFS_NOTICE,
            "schedule_source": f"gtfs_{schedule.feed}",
            "schedule_is_estimated": False,
            "schedule_period": schedule.source_period,
            "line_prefix": schedule.line_prefix,
            "fare_source": f"gtfs_{schedule.feed}",
            "duration_is_estimated": True,
            "road_distance_km": distance,
        },
    )


def build_gtfs_road_quotes(
    db: Session, orig_hub: Hub, orig_term: Terminal, dest_hub: Hub, dest_term: Terminal, travel_date: str
) -> List[StandardizedQuote]:
    """Ônibus Origem -> Destino pelo GTFS. Partida sem tarifa cadastrada não é mostrada."""
    day = datetime.strptime(travel_date, "%Y-%m-%d").date()
    distance = road_distance_km(orig_hub, dest_hub)
    duration = road_duration_minutes(orig_hub, dest_hub)
    quotes = []
    for schedule in _spread(gtfs_departures(db, orig_hub, dest_hub, day), MAX_DEPARTURES_PER_CLASS * 2):
        route_key = build_route_key(orig_hub.name, orig_hub.state, dest_hub.name, dest_hub.state)
        fare = gtfs_fare(db, schedule.feed, route_key)
        if fare is None:
            continue
        quotes.append(_quote(schedule, fare, orig_term, dest_term, _at(day, schedule.departure_time),
                             duration, distance))
    return quotes


def build_gtfs_leg_to_hub(
    db: Session, orig_hub: Hub, orig_term: Terminal, hub: Hub, hub_term: Terminal,
    flight_departures: List[datetime],
) -> List[StandardizedQuote]:
    """Trecho de ônibus Origem -> hub da conexão: partida GTFS que chega a tempo de cada voo."""
    if not flight_departures:
        return []
    distance = road_distance_km(orig_hub, hub)
    duration = road_duration_minutes(orig_hub, hub)
    route_key = build_route_key(orig_hub.name, orig_hub.state, hub.name, hub.state)
    legs = []
    for flight in sorted(set(flight_departures)):
        best = None
        for day in (flight.date() - timedelta(days=1), flight.date()):
            for schedule in gtfs_departures(db, orig_hub, hub, day):
                arrival = _at(day, schedule.departure_time) + timedelta(minutes=duration)
                wait = (flight - arrival).total_seconds() / 60
                if MIN_INTERMODAL_CONNECTION_MINUTES <= wait <= MAX_INTERMODAL_CONNECTION_MINUTES:
                    if best is None or arrival > best[0]:
                        best = (arrival, day, schedule)
        if best is None:
            continue
        _arrival, day, schedule = best
        fare = gtfs_fare(db, schedule.feed, route_key)
        if fare is None:
            continue
        legs.append(_quote(schedule, fare, orig_term, hub_term, _at(day, schedule.departure_time),
                           duration, distance))
    return legs
