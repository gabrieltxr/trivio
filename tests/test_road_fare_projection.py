"""Projeção rodoviária a partir da série ANTT: função pura e endpoint."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.analyzers.road_fare_projection import (
    BASIS_NONE,
    BASIS_RECENT_LEVEL,
    BASIS_TRIP_MONTH,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_NONE,
    FareObservation,
    project_road_fare,
)
from app.api.main import app
from app.database import Base, get_db
from app.models import ModalType, RouteMonthlyFare

ROUTE = "SAO PAULO-SP|RIO DE JANEIRO-RJ"


def _obs(emission, trip, n, mean):
    return FareObservation(emission_period=emission, trip_month=trip, sample_size=n, mean_brl=mean)


# ----------------------------------------------------------------- função pura

def test_no_observations_gives_no_estimate():
    p = project_road_fare([], ROUTE, "CONVENCIONAL", "2026-12")
    assert p.estimate_brl is None and p.low_brl is None and p.high_brl is None
    assert p.confidence == CONFIDENCE_NONE and p.basis == BASIS_NONE and p.sample_size == 0


def test_same_trip_month_uses_weighted_mean_of_its_own_sales():
    obs = [_obs("2026-06", "2026-12", 10, 100.0), _obs("2026-09", "2026-12", 30, 200.0)]
    p = project_road_fare(obs, ROUTE, "CONVENCIONAL", "2026-12")
    assert p.basis == BASIS_TRIP_MONTH
    assert p.estimate_brl == 175.0          # (10*100 + 30*200) / 40
    assert p.sample_size == 40


def test_without_trip_month_sales_uses_only_last_three_emission_months():
    obs = [
        _obs("2026-04", "2026-04", 100, 110.0),
        _obs("2026-07", "2026-07", 100, 90.0),
        _obs("2026-08", "2026-08", 100, 80.0),
        _obs("2026-09", "2026-09", 100, 70.0),
        _obs("2024-10", "2024-10", 5000, 999.0),   # muito antigo: não entra no nível recente
    ]
    p = project_road_fare(obs, ROUTE, "CONVENCIONAL", "2027-03")
    assert p.basis == BASIS_RECENT_LEVEL
    assert p.estimate_brl == 80.0           # média de 90, 80, 70 com pesos iguais (2024-10 excluído)


def test_band_is_never_negative_and_brackets_the_estimate():
    obs = [_obs("2026-08", "2026-12", 50, 20.0), _obs("2026-09", "2026-12", 50, 300.0)]
    p = project_road_fare(obs, ROUTE, "LEITO", "2026-12")
    assert p.low_brl >= 0.0
    assert p.low_brl <= p.estimate_brl <= p.high_brl


def test_single_emission_month_has_no_band():
    p = project_road_fare([_obs("2026-09", "2026-12", 300, 100.0)], ROUTE, "LEITO", "2026-12")
    assert p.estimate_brl == 100.0
    assert p.low_brl is None and p.high_brl is None
    assert "Faixa indisponível" in p.notice


def test_confidence_levels_follow_sample_size():
    high = project_road_fare([_obs("2026-09", "2026-12", 500, 100.0)], ROUTE, "LEITO", "2026-12")
    low = project_road_fare([_obs("2026-09", "2026-12", 5, 100.0)], ROUTE, "LEITO", "2026-12")
    assert high.confidence == CONFIDENCE_HIGH and "Amostra pequena" not in high.notice
    assert low.confidence == CONFIDENCE_LOW and "Amostra pequena" in low.notice


def test_zero_sample_rows_are_ignored():
    p = project_road_fare([_obs("2026-09", "2026-12", 0, 100.0)], ROUTE, "LEITO", "2026-12")
    assert p.estimate_brl is None and p.confidence == CONFIDENCE_NONE


# ----------------------------------------------------------------- endpoint

@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(RouteMonthlyFare(
        route_key=ROUTE, origin_city="SAO PAULO", origin_state="SP",
        destination_city="RIO DE JANEIRO", destination_state="RJ",
        modal=ModalType.ROAD, service_class="CONVENCIONAL", trip_month="2026-12",
        emission_period="2026-09", source="antt_monitriip",
        sample_size=10, mean_brl=58.53, min_brl=40.0, max_brl=90.0,
    ))
    session.commit()

    def override():
        yield session

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    session.close()


PARAMS = {
    "origin_city": "São Paulo", "origin_state": "SP",
    "destination_city": "Rio de Janeiro", "destination_state": "RJ",
    "trip_month": "2026-12", "service_class": "convencional",
}


def test_endpoint_projects_from_stored_series(client):
    r = client.get("/api/v1/road/projection", params=PARAMS)
    assert r.status_code == 200
    body = r.json()
    assert body["route_key"] == ROUTE
    assert body["service_class"] == "CONVENCIONAL"
    assert body["estimate_brl"] == 58.53
    assert body["basis"] == BASIS_TRIP_MONTH


def test_endpoint_unknown_route_returns_no_data_not_error(client):
    r = client.get("/api/v1/road/projection", params={**PARAMS, "destination_city": "Curitiba", "destination_state": "PR"})
    assert r.status_code == 200
    assert r.json()["confidence"] == CONFIDENCE_NONE and r.json()["estimate_brl"] is None


def test_endpoint_rejects_invalid_class_and_month(client):
    assert client.get("/api/v1/road/projection", params={**PARAMS, "service_class": "AVIAO"}).status_code == 422
    assert client.get("/api/v1/road/projection", params={**PARAMS, "trip_month": "2026-13"}).status_code == 422
