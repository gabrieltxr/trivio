"""Leitura do conjunto "Horários" (SIGMA) da ANTT: grade de partidas das linhas interestaduais.

Fonte: https://dados.antt.gov.br, conjunto `gerenciamento-de-autorizacoes`, recurso mensal
"Horários - SIGMA" (CSV `;`, latin-1, licença CC-BY). Colunas usadas:

    razao_social, cnpj, prefixo, descricao_linha, tipo_veiculo, sentido, horario,
    segunda_feira .. domingo, janeiro .. dezembro  ('x' = opera)

`descricao_linha` traz só os extremos da linha, em dois formatos:
    'SP/São Paulo - RJ/Rio de Janeiro'            (UF/Cidade - UF/Cidade)
    'GOIÂNIA/GO-SÃO BERNARDO/MA, via PALMAS/TO'   (Cidade/UF-Cidade/UF, via ...)
`sentido` 'ida' parte do primeiro extremo; 'volta' parte do segundo.

Paradas intermediárias não constam: uma linha SP -> Curitiba não vira horário SP -> Registro.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from app.collectors.antt.monitriip import build_route_key, normalize_place

WEEKDAY_COLUMNS = (
    "segunda_feira", "terca_feira", "quarta_feira", "quinta_feira", "sexta_feira", "sabado", "domingo",
)
MONTH_COLUMNS = (
    "janeiro", "fevereiro", "marco", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)

# tipo_veiculo da ANTT -> classe de serviço usada no Trivio (mesma da série de tarifas)
VEHICLE_TO_SERVICE_CLASS = {
    "BASICA": "CONVENCIONAL",
    "CONVENCIONAL": "CONVENCIONAL",
    "EXECUTIVA": "EXECUTIVO",
    "EXECUTIVO": "EXECUTIVO",
    "SEMILEITO": "SEMI_LEITO",
    "SEMI-LEITO": "SEMI_LEITO",
    "SEMI LEITO": "SEMI_LEITO",
    "LEITO": "LEITO",
    "CAMA": "LEITO",
    "LEITO CAMA": "LEITO",
    "LEITO-CAMA": "LEITO",
}

_VIA_SUFFIX = re.compile(r"(,\s*via\b.*$)|(\s+-\s+via\b.*$)|(\s*\(\s*via\b.*\)\s*$)", re.IGNORECASE)
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class ScheduleRecord:
    route_key: str
    origin_city: str
    origin_state: str
    destination_city: str
    destination_state: str
    company_name: str
    company_cnpj: str
    line_prefix: str
    service_class: str
    departure_time: str
    weekdays: str
    months: str


def _endpoint(raw: str) -> Optional[Tuple[str, str]]:
    """'SP/São Paulo' ou 'SÃO PAULO/SP' -> ('SAO PAULO', 'SP')."""
    parts = [p.strip() for p in raw.split("/", 1)]
    if len(parts) != 2 or not all(parts):
        return None
    first, second = parts
    if len(first) == 2 and first.isalpha():
        return normalize_place(second), first.upper()
    if len(second) == 2 and second.isalpha():
        return normalize_place(first), second.upper()
    return None


def parse_line_endpoints(description: str) -> Optional[Tuple[Tuple[str, str], Tuple[str, str]]]:
    """Extremos (cidade, UF) de uma descrição de linha da ANTT; None se não reconhecer."""
    text = _VIA_SUFFIX.sub("", (description or "").strip()).strip()
    for separator in (" - ", " x ", " X "):
        if separator in text:
            # Partes que não são 'UF/Cidade' (ex: um código antes dos extremos) são ignoradas
            endpoints = [e for e in (_endpoint(part) for part in text.split(separator)) if e]
            return (endpoints[0], endpoints[1]) if len(endpoints) == 2 else None
    # 'CIDADE/UF-CIDADE/UF': o hífen que separa vem logo depois da UF
    match = re.match(r"^(.+?/[A-Za-z]{2})-(.+)$", text)
    if not match:
        return None
    a, b = _endpoint(match.group(1)), _endpoint(match.group(2))
    if not a or not b:
        return None
    return a, b


def _flags(row: Dict[str, str], columns: Iterable[str]) -> str:
    return "".join("1" if (row.get(c) or "").strip().lower() == "x" else "0" for c in columns)


def _merge_flags(a: str, b: str) -> str:
    return "".join("1" if "1" in (x, y) else "0" for x, y in zip(a, b))


def parse_schedule_rows(rows: Iterable[Dict[str, str]]) -> Tuple[List[ScheduleRecord], Dict[str, int]]:
    """Converte as linhas do CSV em partidas no sentido da viagem.

    Linhas repetidas para o mesmo horário (ex: uma com gratuidade, outra sem) viram uma só,
    com a união dos dias e meses de operação.
    """
    merged: Dict[Tuple[str, str, str, str], ScheduleRecord] = {}
    skipped = {"description": 0, "vehicle": 0, "time": 0, "direction": 0}

    for row in rows:
        endpoints = parse_line_endpoints(row.get("descricao_linha", ""))
        if not endpoints:
            skipped["description"] += 1
            continue
        vehicle = normalize_place(row.get("tipo_veiculo", ""))
        service_class = VEHICLE_TO_SERVICE_CLASS.get(vehicle)
        if not service_class:
            skipped["vehicle"] += 1
            continue
        departure = (row.get("horario") or "").strip()
        if not _TIME.match(departure):
            skipped["time"] += 1
            continue
        direction = (row.get("sentido") or "").strip().lower()
        if direction == "ida":
            (oc, os_), (dc, ds) = endpoints
        elif direction == "volta":
            (dc, ds), (oc, os_) = endpoints
        else:
            skipped["direction"] += 1
            continue

        record = ScheduleRecord(
            route_key=build_route_key(oc, os_, dc, ds),
            origin_city=oc, origin_state=os_, destination_city=dc, destination_state=ds,
            company_name=(row.get("razao_social") or "").strip(),
            company_cnpj=(row.get("cnpj") or "").strip(),
            line_prefix=(row.get("prefixo") or "").strip(),
            service_class=service_class,
            departure_time=departure,
            weekdays=_flags(row, WEEKDAY_COLUMNS),
            months=_flags(row, MONTH_COLUMNS),
        )
        key = (record.route_key, record.line_prefix, record.service_class, record.departure_time)
        previous = merged.get(key)
        if previous:
            record = ScheduleRecord(**{
                **previous.__dict__,
                "weekdays": _merge_flags(previous.weekdays, record.weekdays),
                "months": _merge_flags(previous.months, record.months),
            })
        merged[key] = record

    return list(merged.values()), skipped


def read_schedule_csv(path: str) -> Tuple[List[ScheduleRecord], Dict[str, int]]:
    """Lê o CSV da ANTT (separador ';'; UTF-8 ou latin-1)."""
    raw = open(path, "rb").read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    reader = csv.DictReader(text.splitlines(), delimiter=";")
    return parse_schedule_rows(reader)
