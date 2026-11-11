"""Cotações rodoviárias da busca: banco ANTT (preço e grade de horários) + projeção.

Regra da busca:
- trecho de ônibus  -> este módulo (dados da ANTT no banco, sem custo);
- trecho de avião   -> Ignav (app/services/air_quotes.py);
- ônibus + avião    -> trecho de ônibus daqui + voo do Ignav, combinados no interliner.

De onde vem cada informação do ônibus:
- preço: projeção sobre as vendas da ANTT (`project_route_fare`), por classe;
- horário e empresa: grade "Horários" da ANTT (`bus_schedules`), quando a rota tem linha
  interestadual cadastrada. Sem grade (ex: rota dentro do mesmo estado), o horário é
  ilustrativo nas opções diretas e encaixado antes do voo na conexão;
- duração: estimada pela distância, porque nenhuma das duas bases traz duração.

Toda cotação daqui é estimativa (`is_simulated`): o preço é a média paga na classe, não
a tarifa daquela partida. Não tem link de compra e nunca é gravada no banco.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from geopy.distance import geodesic
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.analyzers.interliner import MAX_INTERMODAL_CONNECTION_MINUTES, MIN_INTERMODAL_CONNECTION_MINUTES
from app.analyzers.road_fare_projection import (
    RoadFareProjection,
    project_route_fare,
    projection_to_dict,
)
from app.collectors.antt.monitriip import build_route_key
from app.collectors.base import StandardizedQuote
from app.config import settings
from app.models import BusSchedule, Hub, Terminal

ROAD_ESTIMATE_PROVIDER = "antt_projection"
PRICE_BASIS_ANTT = "antt_projection"
SCHEDULE_SOURCE_ANTT = "antt_horarios"
ROAD_ESTIMATE_CLASSES = ("CONVENCIONAL", "EXECUTIVO", "SEMI_LEITO", "LEITO")
CLASS_LABELS = {
    "CONVENCIONAL": "Convencional",
    "EXECUTIVO": "Executivo",
    "SEMI_LEITO": "Semi-Leito",
    "LEITO": "Leito",
}
ANTT_ESTIMATE_NOTICE = (
    "Estimativa do Trivio com dados da ANTT: o preço é a média paga nesta rota e classe, "
    "não a tarifa desta partida. Não está disponível para compra."
)

ROAD_SINUOSITY_FACTOR = 1.18        # estrada / linha reta no relevo brasileiro
BUS_COMMERCIAL_SPEED_KMH = 75.0     # velocidade comercial de ônibus de linha
CONNECTION_LEAD_MINUTES = 180       # na conexão, o ônibus deve chegar ao hub 3 h antes do voo
MAX_DEPARTURES_PER_CLASS = 4        # partidas reais exibidas por classe, espalhadas no dia


def road_distance_km(orig_hub: Hub, dest_hub: Hub) -> float:
    straight = geodesic(
        (orig_hub.latitude, orig_hub.longitude), (dest_hub.latitude, dest_hub.longitude)
    ).kilometers
    return max(40.0, round(straight * ROAD_SINUOSITY_FACTOR, 1))


def road_duration_minutes(orig_hub: Hub, dest_hub: Hub) -> int:
    return max(45, int(road_distance_km(orig_hub, dest_hub) / BUS_COMMERCIAL_SPEED_KMH * 60))


# ------------------------------------------------------------------ preço (projeção ANTT)

def route_projections(
    db: Session,
    orig_hub: Hub,
    dest_hub: Hub,
    trip_month: str,
    classes: Iterable[str] = ROAD_ESTIMATE_CLASSES,
) -> List[RoadFareProjection]:
    """Projeções ANTT com estimativa para a rota, uma por classe que tem vendas."""
    projections = []
    for service_class in classes:
        projection = project_route_fare(
            db, orig_hub.name, orig_hub.state, dest_hub.name, dest_hub.state, service_class, trip_month
        )
        if projection.estimate_brl is not None:
            projections.append(projection)
    return projections


def has_antt_route(db: Session, orig_hub: Hub, dest_hub: Hub, travel_date: str) -> bool:
    """Há vendas na ANTT para o ônibus desta rota (no mês da viagem ou no nível recente)?"""
    return bool(route_projections(db, orig_hub, dest_hub, travel_date[:7]))


# ------------------------------------------------------------------ horário (grade ANTT)

def _operates(schedule: BusSchedule, day: date) -> bool:
    weekdays = schedule.weekdays or ""
    months = schedule.months or ""
    if len(weekdays) != 7 or weekdays[day.weekday()] != "1":
        return False
    if len(months) == 12 and "1" in months:   # grade sem meses marcados vale o ano todo
        return months[day.month - 1] == "1"
    return True


def scheduled_departures(
    db: Session, orig_hub: Hub, dest_hub: Hub, service_class: str, day: date
) -> List[BusSchedule]:
    """Partidas da grade ANTT (período mais recente) que operam neste dia, por horário."""
    # Só a grade da ANTT (feed NULL): um feed GTFS mais novo não pode esconder a grade ANTT.
    latest = db.query(func.max(BusSchedule.source_period)).filter(BusSchedule.feed.is_(None)).scalar()
    if not latest:
        return []
    route_key = build_route_key(orig_hub.name, orig_hub.state, dest_hub.name, dest_hub.state)
    rows = (
        db.query(BusSchedule)
        .filter(
            BusSchedule.route_key == route_key,
            BusSchedule.service_class == service_class,
            BusSchedule.source_period == latest,
        )
        .order_by(BusSchedule.departure_time, BusSchedule.company_name)
        .all()
    )
    departures, seen = [], set()
    for row in rows:
        if row.departure_time in seen or not _operates(row, day):
            continue
        seen.add(row.departure_time)
        departures.append(row)
    return departures


def _spread(items: Sequence[Any], k: int) -> List[Any]:
    """Até k itens espalhados ao longo da lista (manhã, tarde, noite...)."""
    if len(items) <= k:
        return list(items)
    indexes = sorted({round(i * (len(items) - 1) / (k - 1)) for i in range(k)})
    return [items[i] for i in indexes]


def _at(day: date, hhmm: str) -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(hour, minute))


# ------------------------------------------------------------------ montagem das cotações

def _estimate_quote(
    orig_term: Terminal,
    dest_term: Terminal,
    projection: RoadFareProjection,
    departure: datetime,
    duration_min: int,
    distance_km: float,
    schedule: Optional[BusSchedule] = None,
) -> StandardizedQuote:
    label = CLASS_LABELS.get(projection.service_class, projection.service_class)
    metadata: Dict[str, Any] = {
        "is_simulated": True,
        "is_direct": True,
        "stops_count": 0,
        "price_basis": PRICE_BASIS_ANTT,
        "antt_projection": projection_to_dict(projection),
        "simulation_notice": ANTT_ESTIMATE_NOTICE,
        "duration_is_estimated": True,
        "road_distance_km": distance_km,
    }
    if schedule is not None:
        carrier = f"{schedule.company_name} · {label} (preço estimado ANTT)"
        metadata.update({
            "schedule_source": SCHEDULE_SOURCE_ANTT,
            "schedule_is_estimated": False,
            "line_prefix": schedule.line_prefix,
            "schedule_period": schedule.source_period,
        })
    else:
        carrier = f"Ônibus {label} (estimativa ANTT)"
        metadata["schedule_is_estimated"] = True
    return StandardizedQuote(
        origin_external_id=orig_term.official_code,
        destination_external_id=dest_term.official_code,
        modal="ROAD",
        provider_source=ROAD_ESTIMATE_PROVIDER,
        carrier_name=carrier,
        service_class=projection.service_class,
        departure_time=departure,
        arrival_time=departure + timedelta(minutes=duration_min),
        duration_minutes=duration_min,
        door_to_door_duration_minutes=duration_min + settings.BUS_TERMINAL_CHECKIN_BUFFER_MINUTES,
        base_price=projection.estimate_brl,
        route_metadata=metadata,
    )


def build_antt_road_quotes(
    db: Session,
    orig_hub: Hub,
    orig_road_term: Terminal,
    dest_hub: Hub,
    dest_road_term: Terminal,
    travel_date: str,
) -> List[StandardizedQuote]:
    """Opções de ônibus Origem -> Destino para cada classe com vendas na ANTT.

    Com grade ANTT para a classe: até MAX_DEPARTURES_PER_CLASS partidas reais do dia.
    Sem grade: uma opção com horário ilustrativo (diurno às 08:30; leito à noite).
    """
    day = datetime.strptime(travel_date, "%Y-%m-%d").date()
    projections = route_projections(db, orig_hub, dest_hub, day.strftime("%Y-%m"))
    if not projections:
        return []

    distance = road_distance_km(orig_hub, dest_hub)
    duration = road_duration_minutes(orig_hub, dest_hub)
    quotes = []
    for projection in projections:
        departures = scheduled_departures(db, orig_hub, dest_hub, projection.service_class, day)
        if departures:
            for schedule in _spread(departures, MAX_DEPARTURES_PER_CLASS):
                quotes.append(_estimate_quote(
                    orig_road_term, dest_road_term, projection,
                    _at(day, schedule.departure_time), duration, distance, schedule,
                ))
            continue
        if projection.service_class == "LEITO":
            departure = _at(day, "23:00") if duration >= 270 else _at(day, "18:30")
        else:
            departure = _at(day, "08:30")
        quotes.append(_estimate_quote(orig_road_term, dest_road_term, projection, departure, duration, distance))
    return quotes


def _best_scheduled_bus(
    db: Session,
    orig_hub: Hub,
    hub: Hub,
    service_class: str,
    flight_departure: datetime,
    duration: int,
) -> Optional[Tuple[date, BusSchedule]]:
    """Partida real que chega ao hub a tempo do voo, a mais tarde possível.

    Prefere chegar CONNECTION_LEAD_MINUTES antes do voo; aceita até o mínimo do interliner.
    Considera partidas no dia do voo e na véspera (viagens noturnas).
    """
    candidates = []
    for day in (flight_departure.date() - timedelta(days=1), flight_departure.date()):
        for schedule in scheduled_departures(db, orig_hub, hub, service_class, day):
            arrival = _at(day, schedule.departure_time) + timedelta(minutes=duration)
            wait = (flight_departure - arrival).total_seconds() / 60
            if MIN_INTERMODAL_CONNECTION_MINUTES <= wait <= MAX_INTERMODAL_CONNECTION_MINUTES:
                candidates.append((wait >= CONNECTION_LEAD_MINUTES, arrival, day, schedule))
    if not candidates:
        return None
    # Primeiro os que respeitam a folga recomendada; entre eles, o que chega mais tarde.
    _safe, _arrival, day, schedule = max(candidates, key=lambda c: (c[0], c[1]))
    return day, schedule


def build_antt_legs_to_hub(
    db: Session,
    orig_hub: Hub,
    orig_road_term: Terminal,
    hub: Hub,
    hub_road_term: Terminal,
    flight_departures: Iterable[datetime],
) -> List[StandardizedQuote]:
    """Trecho de ônibus Origem -> hub da conexão, um para cada voo do hub.

    Com grade ANTT: a partida real que chega a tempo. Sem grade: ônibus encaixado para
    chegar CONNECTION_LEAD_MINUTES antes do voo. Preço: a classe mais barata com vendas
    (com grade, a mais barata que tem horário cadastrado).
    """
    departures = sorted(set(flight_departures))
    if not departures:
        return []
    projections = route_projections(db, orig_hub, hub, departures[0].strftime("%Y-%m"))
    if not projections:
        return []
    projections.sort(key=lambda p: p.estimate_brl)

    distance = road_distance_km(orig_hub, hub)
    duration = road_duration_minutes(orig_hub, hub)
    has_schedule = {
        p.service_class: bool(scheduled_departures(db, orig_hub, hub, p.service_class, departures[0].date())
                              or scheduled_departures(db, orig_hub, hub, p.service_class,
                                                      departures[0].date() - timedelta(days=1)))
        for p in projections
    }
    scheduled = [p for p in projections if has_schedule[p.service_class]]

    legs = []
    for flight_departure in departures:
        if scheduled:
            projection = scheduled[0]
            found = _best_scheduled_bus(db, orig_hub, hub, projection.service_class, flight_departure, duration)
            if not found:
                continue   # nenhum ônibus da grade chega a tempo deste voo
            day, schedule = found
            legs.append(_estimate_quote(
                orig_road_term, hub_road_term, projection,
                _at(day, schedule.departure_time), duration, distance, schedule,
            ))
        else:
            projection = projections[0]
            arrival = flight_departure - timedelta(minutes=CONNECTION_LEAD_MINUTES)
            legs.append(_estimate_quote(
                orig_road_term, hub_road_term, projection, arrival - timedelta(minutes=duration),
                duration, distance,
            ))
    return legs
