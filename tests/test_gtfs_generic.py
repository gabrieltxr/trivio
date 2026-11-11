"""Importador genérico de GTFS, com um feed sintético em zip."""

import importlib.util
import zipfile
from pathlib import Path

import pytest

from app.collectors.gtfs.feeds import FEEDS
from app.collectors.gtfs.generic import GtfsSource, parse_gtfs
from app.database import SessionLocal
from app.models import BusSchedule, GtfsRouteFare

FEED = "teste_gtfs"


def _zip(files: dict, tmp_path: Path) -> str:
    path = tmp_path / "feed.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text.encode("utf-8"))
    return str(path)


@pytest.fixture
def feed_zip(tmp_path):
    files = {
        "agency.txt": "agency_id,agency_name\n1,VIACAO EXEMPLO\n",
        "feed_info.txt": "feed_publisher_name,feed_version\nTESTE,20261109\n",
        "calendar.txt": (
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            "WD,1,1,1,1,1,0,0,20260101,20261231\n"
            "SAT,0,0,0,0,0,1,0,20260101,20261231\n"
        ),
        # Linha A -> B: "Cidade A (bairro) - Cidade B (terminal)"
        "routes.txt": (
            "route_id,agency_id,route_short_name,route_long_name,route_type\n"
            "R1,1,001,ALFA (CENTRO) - BETA (TERMINAL),3\n"
            "R2,1,002,ALFA - GAMA,3\n"
            "R3,1,003,SEM FORMATO,3\n"
        ),
        "trips.txt": (
            "route_id,service_id,trip_id,trip_headsign,direction_id\n"
            "R1,WD,T1,BETA (TERMINAL),0\n"          # sentido ALFA -> BETA pelo destino
            "R1,WD,T2,ALFA (CENTRO),1\n"            # sentido BETA -> ALFA pelo destino
            "R1,SAT,T3,BETA,0\n"                    # sábado, só a cidade
            "R2,WD,T4,XYZ,1\n"                      # destino desconhecido: usa direction_id
            "R3,WD,T5,QUALQUER,0\n"                 # linha sem extremos: ignorada
        ),
        "stop_times.txt": (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "T1,08:30:00,08:30:00,S1,1\n"           # segunda parada, aparece antes no arquivo
            "T1,09:10:00,09:10:00,S9,5\n"
            "T1,08:20:00,08:20:00,S0,0\n"           # menor sequência = partida real
            "T2,25:10:00,25:10:00,S2,0\n"           # passa da meia-noite
            "T3,07:00:00,07:00:00,S3,0\n"
            "T4,06:45:00,06:45:00,S4,0\n"
            "T5,06:00:00,06:00:00,S5,0\n"
        ),
        "fare_attributes.txt": "fare_id,price,currency_type,payment_method,transfers\nF1,12.5,BRL,0,0\nF2,7.0,BRL,0,0\n",
        "fare_rules.txt": "fare_id,route_id\nF1,R1\nF2,R2\n",
        "calendar_dates.txt": "service_id,date,exception_type\nWD,20261225,2\n",
    }
    return _zip(files, tmp_path)


def _parse(path):
    source = GtfsSource(path)
    try:
        return parse_gtfs(source, feed=FEED, default_state="SP")
    finally:
        source.close()


def _by(result, departure):
    return [r for r in result.schedules if r.departure_time == departure]


def test_departure_is_the_first_stop_regardless_of_row_order(feed_zip):
    """Partida = parada de menor sequência, mesmo que a linha apareça depois no arquivo."""
    result = _parse(feed_zip)
    departures = {r.departure_time for r in result.schedules}
    assert "08:20" in departures and "08:30" not in departures and "09:10" not in departures


def test_sentido_comes_from_headsign_and_state_is_the_feed_default(feed_zip):
    result = _parse(feed_zip)
    [ida] = [r for r in result.schedules if r.departure_time == "08:20" and r.route_key.startswith("ALFA")]
    assert ida.route_key == "ALFA-SP|BETA-SP"
    assert (ida.origin_city, ida.destination_city) == ("ALFA", "BETA")
    [volta] = _by(result, "01:10")                       # 25:10 -> 01:10, após meia-noite
    assert volta.route_key == "BETA-SP|ALFA-SP"
    assert result.stats["after_midnight"] == 1


def test_unknown_headsign_falls_back_to_direction_id_and_is_counted(feed_zip):
    result = _parse(feed_zip)
    [gama] = [r for r in result.schedules if r.line_prefix == f"{FEED}:R2"]
    assert gama.route_key == "GAMA-SP|ALFA-SP"             # direction_id 1 = B -> A
    assert result.stats["direction_guessed"] == 1


def test_route_without_city_endpoints_is_skipped(feed_zip):
    result = _parse(feed_zip)
    assert result.stats["routes_without_endpoints"] == 1
    assert not [r for r in result.schedules if r.line_prefix == f"{FEED}:R3"]


def test_weekdays_months_and_class_come_from_calendar(feed_zip):
    result = _parse(feed_zip)
    [sabado] = [r for r in result.schedules if r.departure_time == "07:00"]
    assert sabado.weekdays == "0000010"                    # só sábado
    assert sabado.months == "1" * 12                       # período 2026-01 a 2026-12
    assert all(r.service_class == "NAO_INFORMADA" for r in result.schedules)


def test_calendar_exceptions_are_counted_not_applied(feed_zip):
    result = _parse(feed_zip)
    assert result.stats["calendar_exceptions_ignored"] == 1


def test_company_comes_from_agency(feed_zip):
    result = _parse(feed_zip)
    assert all(r.company_name == "VIACAO EXEMPLO" for r in result.schedules)


def test_fares_are_one_per_route_from_fare_rules(feed_zip):
    result = _parse(feed_zip)
    fares = {f.route_id: f for f in result.fares}
    assert fares["R1"].price_brl == 12.5 and fares["R1"].route_key == "ALFA-SP|BETA-SP"
    assert fares["R2"].price_brl == 7.0 and fares["R2"].route_key == "ALFA-SP|GAMA-SP"
    assert "R3" not in fares


def test_folder_and_zip_give_the_same_result(feed_zip, tmp_path):
    folder = tmp_path / "pasta"
    with zipfile.ZipFile(feed_zip) as z:
        z.extractall(folder)
    from_zip = _parse(feed_zip)
    from_folder = parse_gtfs(GtfsSource(str(folder)), feed=FEED, default_state="SP")
    assert len(from_zip.schedules) == len(from_folder.schedules)
    assert len(from_zip.fares) == len(from_folder.fares)


def test_import_script_is_idempotent_and_isolated_from_antt(feed_zip, antt_series, monkeypatch):
    """Reimportar substitui só o feed; linhas da ANTT (feed NULL) não são apagadas."""
    monkeypatch.setitem(FEEDS, FEED, {"name": "teste", "default_state": "SP"})
    spec = importlib.util.spec_from_file_location(
        "import_gtfs", Path(__file__).resolve().parent.parent / "scripts" / "import_gtfs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    db = SessionLocal()
    try:
        antt_row = BusSchedule(
            route_key="SAO PAULO-SP|RIO DE JANEIRO-RJ", origin_city="SAO PAULO", origin_state="SP",
            destination_city="RIO DE JANEIRO", destination_state="RJ", company_name="ANTT TESTE",
            company_cnpj="", line_prefix="ANTT-X", service_class="SEMI_LEITO", departure_time="10:00",
            weekdays="1111111", months="1" * 12, source_period="2026-11", feed=None,
        )
        db.add(antt_row)
        db.commit()
    finally:
        db.close()

    try:
        first = module.import_feed(feed_zip, FEED)
        second = module.import_feed(feed_zip, FEED)
        assert first["schedules"] == second["schedules"] > 0
        db = SessionLocal()
        try:
            assert db.query(BusSchedule).filter(BusSchedule.feed == FEED).count() == first["schedules"]
            assert db.query(GtfsRouteFare).filter(GtfsRouteFare.feed == FEED).count() == first["fares"]
            assert db.query(BusSchedule).filter(BusSchedule.feed.is_(None), BusSchedule.line_prefix == "ANTT-X").count() == 1
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(BusSchedule).filter(BusSchedule.feed == FEED).delete(synchronize_session=False)
            db.query(BusSchedule).filter(BusSchedule.line_prefix == "ANTT-X").delete(synchronize_session=False)
            db.query(GtfsRouteFare).filter(GtfsRouteFare.feed == FEED).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()
