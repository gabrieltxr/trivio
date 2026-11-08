"""Referência histórica rodoviária (ANTT): faixa por classe, sem entrar no ranking."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.analyzers.road_reference import build_road_references
from app.database import Base
from app.models import ModalType, RouteMonthlyFare


def _add(session, month, mean, tickets=100, service="SEMI_LEITO"):
    session.add(RouteMonthlyFare(
        route_key="SAO PAULO-SP|RIO DE JANEIRO-RJ",
        origin_city="SAO PAULO", origin_state="SP",
        destination_city="RIO DE JANEIRO", destination_state="RJ",
        modal=ModalType.ROAD, service_class=service,
        trip_month=month, emission_period=month, source="antt_monitriip",
        sample_size=tickets, mean_brl=mean, min_brl=mean, max_brl=mean,
    ))


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_reference_has_band_and_travel_month_mean():
    db = _db()
    for month, mean in zip(range(1, 7), [100.0, 110.0, 120.0, 130.0, 140.0, 150.0]):
        _add(db, f"2026-{month:02d}", mean)
    db.commit()

    [ref] = build_road_references(db, "São Paulo", "SP", "Rio de Janeiro", "RJ", travel_date="2026-03-15")

    assert ref["service_class"] == "SEMI_LEITO"
    assert ref["months"] == 6
    assert ref["reference_period"] == "2026-01 a 2026-06"
    assert ref["median_brl"] == 125.0
    assert ref["p25_brl"] < ref["median_brl"] < ref["p75_brl"]
    assert ref["travel_month_mean_brl"] == 120.0


def test_reference_needs_six_months():
    db = _db()
    for month in range(1, 6):
        _add(db, f"2026-{month:02d}", 100.0)
    db.commit()

    assert build_road_references(db, "São Paulo", "SP", "Rio de Janeiro", "RJ") == []


def test_reference_is_per_class():
    db = _db()
    for month in range(1, 7):
        _add(db, f"2026-{month:02d}", 100.0, service="LEITO")
    db.commit()

    refs = build_road_references(db, "São Paulo", "SP", "Rio de Janeiro", "RJ")

    assert [r["service_class"] for r in refs] == ["LEITO"]
    assert refs[0]["travel_month_mean_brl"] is None
