"""Grade de horários da ANTT: leitura do CSV e uso nas opções de ônibus da busca."""

import importlib.util
from datetime import date, datetime
from pathlib import Path

import pytest

from app.collectors.antt.schedules import parse_line_endpoints, parse_schedule_rows
from app.database import SessionLocal
from app.models import BusSchedule, Hub
from app.services.road_quotes import (
    MAX_DEPARTURES_PER_CLASS,
    build_antt_legs_to_hub,
    build_antt_road_quotes,
    road_duration_minutes,
)

ROUTE_SP_RJ = "SAO PAULO-SP|RIO DE JANEIRO-RJ"
EVERY_DAY = {d: "x" for d in (
    "segunda_feira", "terca_feira", "quarta_feira", "quinta_feira", "sexta_feira", "sabado", "domingo")}
ALL_MONTHS = {m: "x" for m in (
    "janeiro", "fevereiro", "marco", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")}


def _row(description, direction, hhmm, vehicle="Semileito", company="VIACAO TESTE LTDA", prefix="SPRJ0001", **flags):
    return {
        "razao_social": company, "cnpj": "00.000.000/0001-00", "prefixo": prefix,
        "descricao_linha": description, "tipo_veiculo": vehicle, "sentido": direction,
        "horario": hhmm, **(flags or {**EVERY_DAY, **ALL_MONTHS}),
    }


# ----------------------------------------------------------------- leitura do CSV

@pytest.mark.parametrize("description,expected", [
    ("SP/São Paulo - RJ/Rio de Janeiro", (("SAO PAULO", "SP"), ("RIO DE JANEIRO", "RJ"))),
    ("TO/Palmas - DF/Brasília - Via Uruaçu", (("PALMAS", "TO"), ("BRASILIA", "DF"))),
    ("GOIÂNIA/GO-SÃO BERNARDO/MA, via PALMAS/TO", (("GOIANIA", "GO"), ("SAO BERNARDO", "MA"))),
    ("Passo Fundo/RS x Sinop/MT (Via Chapecó/Umuarama)", (("PASSO FUNDO", "RS"), ("SINOP", "MT"))),
    ("TJK - MG/Belo Horizonte - RJ/Cabo Frio", (("BELO HORIZONTE", "MG"), ("CABO FRIO", "RJ"))),
])
def test_line_endpoints_in_both_antt_formats(description, expected):
    assert parse_line_endpoints(description) == expected


def test_unrecognized_description_is_skipped():
    records, skipped = parse_schedule_rows([_row("Linha sem formato", "ida", "08:00")])
    assert records == [] and skipped["description"] == 1


def test_direction_vehicle_class_and_flags():
    records, _ = parse_schedule_rows([
        _row("SP/São Paulo - RJ/Rio de Janeiro", "ida", "08:00", vehicle="Básica"),
        _row("SP/São Paulo - RJ/Rio de Janeiro", "volta", "09:30", vehicle="Cama"),
    ])
    by_time = {r.departure_time: r for r in records}
    assert by_time["08:00"].route_key == ROUTE_SP_RJ
    assert by_time["08:00"].service_class == "CONVENCIONAL"
    assert by_time["09:30"].route_key == "RIO DE JANEIRO-RJ|SAO PAULO-SP"    # volta parte do 2º extremo
    assert by_time["09:30"].service_class == "LEITO"
    assert by_time["08:00"].weekdays == "1111111" and by_time["08:00"].months == "1" * 12


def test_duplicated_departure_merges_operating_days():
    """A ANTT repete o horário (ex: com e sem gratuidade): vira uma partida com a união dos dias."""
    weekend = {"sabado": "x", "domingo": "x", **ALL_MONTHS}
    weekdays = {"segunda_feira": "x", "terca_feira": "x", **ALL_MONTHS}
    records, _ = parse_schedule_rows([
        _row("SP/São Paulo - RJ/Rio de Janeiro", "ida", "08:00", **weekend),
        _row("SP/São Paulo - RJ/Rio de Janeiro", "ida", "08:00", **weekdays),
    ])
    [record] = records
    assert record.weekdays == "1100011"


def test_period_is_inferred_from_the_antt_file_name():
    spec = importlib.util.spec_from_file_location(
        "import_antt_schedules", Path(__file__).resolve().parent.parent / "scripts" / "import_antt_schedules.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.period_from_filename("04-2026_horarios_sigma.csv") == "2026-04"


# ----------------------------------------------------------------- uso na busca

@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.query(BusSchedule).filter(BusSchedule.source_period == "2099-01").delete()
        session.commit()
        session.close()


def _schedule(db, route_key, hhmm, service_class="SEMI_LEITO", weekdays="1111111", company="VIACAO TESTE LTDA"):
    origin, destination = route_key.split("|")
    oc, os_ = origin.rsplit("-", 1)
    dc, ds = destination.rsplit("-", 1)
    db.add(BusSchedule(
        route_key=route_key, origin_city=oc, origin_state=os_, destination_city=dc, destination_state=ds,
        company_name=company, company_cnpj="00.000.000/0001-00", line_prefix=f"P{hhmm}",
        service_class=service_class, departure_time=hhmm, weekdays=weekdays, months="1" * 12,
        source_period="2099-01",
    ))
    db.commit()


def _hub(db, name):
    return db.query(Hub).filter(Hub.name == name).one()


def _road_term(hub):
    return next(t for t in hub.terminals if t.modal.value == "ROAD")


def test_direct_bus_uses_real_antt_departures_and_company(db, antt_series):
    antt_series.add(ROUTE_SP_RJ, "SEMI_LEITO", "2026-11", "2026-09", 300, 150.0)
    for hhmm in ("06:00", "09:00", "12:00", "15:00", "18:00", "23:30"):
        _schedule(db, ROUTE_SP_RJ, hhmm)
    _schedule(db, ROUTE_SP_RJ, "07:15", weekdays="0000011")       # só fim de semana

    sp, rj = _hub(db, "São Paulo"), _hub(db, "Rio de Janeiro")
    quotes = build_antt_road_quotes(db, sp, _road_term(sp), rj, _road_term(rj), "2026-11-20")   # sexta

    times = [q.departure_time.strftime("%H:%M") for q in quotes]
    assert len(quotes) == MAX_DEPARTURES_PER_CLASS
    assert "07:15" not in times                                  # não opera às sextas
    assert times[0] == "06:00" and times[-1] == "23:30"           # espalhadas no dia
    for q in quotes:
        assert q.base_price == 150.0                              # preço: projeção da classe
        assert "VIACAO TESTE LTDA" in q.carrier_name and "preço estimado ANTT" in q.carrier_name
        assert q.route_metadata["schedule_source"] == "antt_horarios"
        assert q.route_metadata["schedule_is_estimated"] is False


def test_direct_bus_without_schedule_keeps_illustrative_time(db, antt_series):
    antt_series.add(ROUTE_SP_RJ, "SEMI_LEITO", "2026-11", "2026-09", 300, 150.0)
    sp, rj = _hub(db, "São Paulo"), _hub(db, "Rio de Janeiro")

    [quote] = build_antt_road_quotes(db, sp, _road_term(sp), rj, _road_term(rj), "2026-11-20")

    assert quote.departure_time == datetime(2026, 11, 20, 8, 30)
    assert quote.route_metadata["schedule_is_estimated"] is True
    assert "schedule_source" not in quote.route_metadata


def test_connection_bus_is_the_latest_real_departure_that_makes_the_flight(db, antt_series):
    """Rio de Janeiro -> São Paulo (ônibus) para um voo às 20:00 em São Paulo."""
    route = "RIO DE JANEIRO-RJ|SAO PAULO-SP"
    antt_series.add(route, "SEMI_LEITO", "2026-11", "2026-09", 300, 150.0)
    rj, sp = _hub(db, "Rio de Janeiro"), _hub(db, "São Paulo")
    duration = road_duration_minutes(rj, sp)
    flight = datetime(2026, 11, 20, 20, 0)
    for hhmm in ("06:00", "08:00", "10:00", "13:00"):
        _schedule(db, route, hhmm)

    [leg] = build_antt_legs_to_hub(db, rj, _road_term(rj), sp, _road_term(sp), [flight])

    wait = (flight - leg.arrival_time).total_seconds() / 60
    assert leg.route_metadata["schedule_source"] == "antt_horarios"
    assert wait >= 180                                            # folga recomendada
    later = [h for h in ("06:00", "08:00", "10:00", "13:00") if h > leg.departure_time.strftime("%H:%M")]
    for hhmm in later:                                            # as mais tardias não dariam tempo
        hour, minute = map(int, hhmm.split(":"))
        arrival = datetime(2026, 11, 20, hour, minute) + (leg.arrival_time - leg.departure_time)
        assert (flight - arrival).total_seconds() / 60 < 180


def test_gtfs_period_newer_than_antt_does_not_hide_the_antt_grade(db, antt_series):
    """Regressão: um feed GTFS com período mais novo não pode apagar a grade ANTT da busca."""
    antt_series.add(ROUTE_SP_RJ, "SEMI_LEITO", "2026-11", "2026-09", 300, 150.0)
    _schedule(db, ROUTE_SP_RJ, "09:00")
    newer = BusSchedule(
        route_key="OUTRA CIDADE-SP|OUTRA CIDADE 2-SP", origin_city="OUTRA CIDADE", origin_state="SP",
        destination_city="OUTRA CIDADE 2", destination_state="SP", company_name="GTFS", company_cnpj="",
        line_prefix="gtfs:x", service_class="NAO_INFORMADA", departure_time="10:00", weekdays="1111111",
        months="1" * 12, source_period="2099-02", feed="teste_periodo",
    )
    db.add(newer)
    db.commit()
    try:
        sp, rj = _hub(db, "São Paulo"), _hub(db, "Rio de Janeiro")
        quotes = build_antt_road_quotes(db, sp, _road_term(sp), rj, _road_term(rj), "2026-11-20")
        assert any(q.departure_time.strftime("%H:%M") == "09:00" for q in quotes)
    finally:
        db.query(BusSchedule).filter(BusSchedule.feed == "teste_periodo").delete(synchronize_session=False)
        db.commit()
