"""Importador genérico de GTFS (horários de partida e tarifa por linha).

Lê um feed GTFS (arquivo .zip ou pasta) e devolve:

- partidas: uma por (linha, sentido, horário de partida), com os dias da semana e meses
  em que a viagem opera, a partir de calendar.txt;
- tarifas: uma por linha, a partir de fare_attributes.txt + fare_rules.txt.

Decisões:
- Só o horário de partida na PRIMEIRA parada de cada viagem é guardado. stop_times.txt é
  lido em streaming e não entra no banco: a busca não precisa das paradas intermediárias.
- Sentido: o nome da linha ("A - B") dá os extremos. Se trip_headsign diz qual é o destino,
  ele define o sentido; senão, direction_id 0 = A -> B (contado em `direction_guessed`).
- Cidades: o texto antes do parêntese ("SAO PAULO (METRO X)" -> "SAO PAULO"). Trechos que não
  são cidades (ex: "VILA PRUDENTE") viram rotas que nenhuma cidade cadastrada casa, sem erro.
- Exceções de calendar_dates.txt não são aplicadas (contadas em `calendar_exceptions_ignored`).
- Viagens após meia-noite (hora >= 24) são guardadas na hora do dia; o dia de operação não é
  deslocado (contadas em `after_midnight`).
"""

from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterator, List, Optional, Tuple

from app.collectors.antt.monitriip import build_route_key, normalize_place
from app.collectors.antt.schedules import ScheduleRecord, _merge_flags

NOT_CLASSIFIED = "NAO_INFORMADA"        # GTFS não traz classe de serviço (leito, executivo...)
WEEKDAY_FIELDS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_PARENS = re.compile(r"\([^)]*\)")


@dataclass(frozen=True)
class GtfsFare:
    feed: str
    route_id: str
    route_name: str
    route_key: str
    price_brl: float
    currency: str


@dataclass
class GtfsImport:
    schedules: List[ScheduleRecord] = field(default_factory=list)
    fares: List[GtfsFare] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)


class GtfsSource:
    """Acesso às tabelas de um feed, seja zip ou pasta extraída."""

    def __init__(self, path: str):
        self.path = path
        self._zip = zipfile.ZipFile(path) if zipfile.is_zipfile(path) else None
        if self._zip is None and not os.path.isdir(path):
            raise ValueError(f"Feed GTFS não encontrado: {path} (esperado .zip ou pasta)")

    def has(self, name: str) -> bool:
        if self._zip is not None:
            return name in self._zip.namelist()
        return os.path.exists(os.path.join(self.path, name))

    def _binary(self, name: str):
        if self._zip is not None:
            return self._zip.open(name)
        return open(os.path.join(self.path, name), "rb")

    def rows(self, name: str) -> Iterator[Dict[str, str]]:
        """Linhas de uma tabela como dicionários. Aceita UTF-8 com ou sem BOM."""
        if not self.has(name):
            return iter(())
        text = io.TextIOWrapper(self._binary(name), encoding="utf-8-sig", newline="")
        return csv.DictReader(text)

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()


def _clean_place(raw: str) -> str:
    """'SAO PAULO (METRO CAPAO REDONDO)' -> 'SAO PAULO'."""
    return normalize_place(_PARENS.sub("", raw or "").strip())


def _endpoints(long_name: str) -> Optional[Tuple[str, str]]:
    # Parênteses vêm antes da divisão: "CAJAMAR (CTE - VILA NOVA) - SAO PAULO" tem dois traços
    parts = _PARENS.sub("", long_name or "").split(" - ")
    if len(parts) != 2:
        return None
    a, b = (normalize_place(p) for p in parts)
    return (a, b) if a and b else None


def _normalize_time(raw: str) -> Tuple[str, bool]:
    """'25:10:00' -> ('01:10', True): hora do dia e se passou da meia-noite."""
    hour, minute = (int(x) for x in raw.strip().split(":")[:2])
    return f"{hour % 24:02d}:{minute:02d}", hour >= 24


def _flags_from_calendar(row: Dict[str, str]) -> str:
    return "".join("1" if row.get(day) == "1" else "0" for day in WEEKDAY_FIELDS)


def _months_from_calendar(row: Dict[str, str]) -> str:
    """Meses cobertos pelo período de validade do serviço."""
    try:
        start = date(int(row["start_date"][:4]), int(row["start_date"][4:6]), 1)
        end = date(int(row["end_date"][:4]), int(row["end_date"][4:6]), 1)
    except (KeyError, ValueError):
        return "1" * 12
    covered = [False] * 12
    if start.year == end.year:
        for month in range(start.month, end.month + 1):
            covered[month - 1] = True
    else:
        covered = [True] * 12   # período que atravessa anos: considera o ano todo
    return "".join("1" if c else "0" for c in covered)


def parse_gtfs(source: GtfsSource, feed: str, default_state: str, company_by_agency: bool = True) -> GtfsImport:
    """Converte um feed GTFS em partidas e tarifas prontas para o banco."""
    stats: Dict[str, int] = {
        "routes": 0, "routes_without_endpoints": 0, "trips": 0, "trips_without_service": 0,
        "trips_without_stop": 0, "direction_guessed": 0, "after_midnight": 0,
        "calendar_exceptions_ignored": sum(1 for _ in source.rows("calendar_dates.txt")),
        "fares": 0, "routes_with_fare": 0,
    }

    agencies = {r.get("agency_id", ""): (r.get("agency_name") or "").strip() for r in source.rows("agency.txt")}

    routes: Dict[str, Dict[str, object]] = {}
    for row in source.rows("routes.txt"):
        stats["routes"] += 1
        ends = _endpoints(row.get("route_long_name", ""))
        if not ends:
            stats["routes_without_endpoints"] += 1
            continue
        routes[row["route_id"]] = {
            "ends": ends,
            "name": (row.get("route_long_name") or "").strip(),
            "agency": agencies.get(row.get("agency_id", ""), "") if company_by_agency else "",
        }

    services: Dict[str, Dict[str, str]] = {}
    for row in source.rows("calendar.txt"):
        services[row["service_id"]] = {
            "weekdays": _flags_from_calendar(row),
            "months": _months_from_calendar(row),
        }

    trips: Dict[str, Dict[str, str]] = {}
    for row in source.rows("trips.txt"):
        stats["trips"] += 1
        if row.get("route_id") not in routes:
            continue
        trips[row["trip_id"]] = {
            "route_id": row["route_id"],
            "service_id": row.get("service_id", ""),
            "headsign": _clean_place(row.get("trip_headsign", "")),
            "direction": row.get("direction_id", "0") or "0",
        }

    # Primeira parada de cada viagem: menor stop_sequence. Streaming: não guarda as demais.
    first_stop: Dict[str, Tuple[int, str]] = {}
    for row in source.rows("stop_times.txt"):
        trip_id = row.get("trip_id")
        if trip_id not in trips:
            continue
        try:
            sequence = int(row.get("stop_sequence", "0") or 0)
        except ValueError:
            continue
        current = first_stop.get(trip_id)
        if current is None or sequence < current[0]:
            first_stop[trip_id] = (sequence, row.get("departure_time") or row.get("arrival_time") or "")

    merged: Dict[Tuple[str, str, str], ScheduleRecord] = {}
    for trip_id, trip in trips.items():
        if trip_id not in first_stop:
            stats["trips_without_stop"] += 1
            continue
        service = services.get(trip["service_id"])
        if service is None:
            stats["trips_without_service"] += 1
            continue
        departure_raw = first_stop[trip_id][1]
        if not departure_raw:
            stats["trips_without_stop"] += 1
            continue
        departure, after = _normalize_time(departure_raw)
        if after:
            stats["after_midnight"] += 1

        route = routes[trip["route_id"]]
        (a_city, a_state), (b_city, b_state) = (
            (route["ends"][0], default_state), (route["ends"][1], default_state)
        )
        if trip["headsign"] == a_city:
            origin, destination = (b_city, b_state), (a_city, a_state)
        elif trip["headsign"] == b_city:
            origin, destination = (a_city, a_state), (b_city, b_state)
        else:
            stats["direction_guessed"] += 1
            origin, destination = ((a_city, a_state), (b_city, b_state)) if trip["direction"] != "1" \
                else ((b_city, b_state), (a_city, a_state))

        line = f"{feed}:{trip['route_id']}"[:20]
        record = ScheduleRecord(
            route_key=build_route_key(origin[0], origin[1], destination[0], destination[1]),
            origin_city=origin[0], origin_state=origin[1],
            destination_city=destination[0], destination_state=destination[1],
            company_name=str(route["agency"]) or feed,
            company_cnpj="",
            line_prefix=line,
            service_class=NOT_CLASSIFIED,
            departure_time=departure,
            weekdays=service["weekdays"],
            months=service["months"],
        )
        key = (record.route_key, record.line_prefix, record.departure_time)
        previous = merged.get(key)
        if previous:
            record = ScheduleRecord(**{
                **previous.__dict__,
                "weekdays": _merge_flags(previous.weekdays, record.weekdays),
                "months": _merge_flags(previous.months, record.months),
            })
        merged[key] = record

    prices = {r["fare_id"]: (r.get("price"), r.get("currency_type") or "BRL")
              for r in source.rows("fare_attributes.txt")}
    fares: Dict[str, GtfsFare] = {}
    for rule in source.rows("fare_rules.txt"):
        price_row = prices.get(rule.get("fare_id"))
        route = routes.get(rule.get("route_id"))
        if not price_row or not route or rule["route_id"] in fares:
            continue
        try:
            price = float(price_row[0])
        except (TypeError, ValueError):
            continue
        a, b = route["ends"]
        fares[rule["route_id"]] = GtfsFare(
            feed=feed, route_id=rule["route_id"], route_name=str(route["name"]),
            route_key=build_route_key(a, default_state, b, default_state),
            price_brl=price, currency=price_row[1],
        )
    stats["fares"] = len(fares)
    stats["routes_with_fare"] = len(fares)

    return GtfsImport(schedules=list(merged.values()), fares=list(fares.values()), stats=stats)
