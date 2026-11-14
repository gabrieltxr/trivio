"""A série ANTT de uma rota e classe vai ao banco uma vez por busca.

O ônibus direto, a referência histórica e o trecho até o hub da conexão leem a mesma
série; antes, cada um fazia a própria consulta (e o has_antt_route montava as quatro
projeções só para responder se a rota existe).
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.analyzers.road_fare_projection import fetch_fare_observations, monthly_means
from app.api.main import app
from app.database import engine
from app.models import Hub
from app.services.road_quotes import ROAD_ESTIMATE_CLASSES, has_antt_route, route_projections

ROUTE_SP_RJ = "SAO PAULO-SP|RIO DE JANEIRO-RJ"


@contextmanager
def _series_reads():
    """Conta as consultas à série ANTT (route_monthly_fares) feitas no bloco."""
    reads = []

    def count(conn, cursor, statement, parameters, context, executemany):
        if "FROM route_monthly_fares" in statement:
            reads.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        yield reads
    finally:
        event.remove(engine, "before_cursor_execute", count)


def _search(client, **params):
    with client.websocket_connect("/ws/search") as ws:
        ws.send_text(json.dumps({"action": "start_search", **params}))
        while True:
            message = ws.receive_json()
            if message["event"] in ("SEARCH_COMPLETED", "SEARCH_ERROR"):
                return message


@patch("app.services.air_quotes.AirQuoteService.get_quotes", return_value=[])
def test_search_reads_each_route_class_once(_air, antt_series):
    for emission in ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"):
        antt_series.add(ROUTE_SP_RJ, "SEMI_LEITO", emission, emission, 50, 150.0)

    with TestClient(app) as client, _series_reads() as reads:
        done = _search(client, origin="SAO", destination="RIO", date="2026-11-20")

    assert done["event"] == "SEARCH_COMPLETED"
    # Ida SP -> RJ: uma leitura por classe, reaproveitada pela referência histórica.
    # (Sem volta e sem conexão, nenhuma outra rota é lida.)
    assert len(reads) == len(ROAD_ESTIMATE_CLASSES)


def test_has_antt_route_matches_route_projections(antt_series):
    db = antt_series.db
    sao = db.query(Hub).filter(Hub.name == "São Paulo").one()
    rio = db.query(Hub).filter(Hub.name == "Rio de Janeiro").one()
    assert has_antt_route(db, sao, rio, "2026-11-20") is False
    assert not route_projections(db, sao, rio, "2026-11")

    antt_series.add(ROUTE_SP_RJ, "LEITO", "2026-03", "2026-02", 10, 300.0)   # outro mês: vale o nível recente
    assert has_antt_route(db, sao, rio, "2026-11-20") is True
    assert route_projections(db, sao, rio, "2026-11")


def test_cache_serves_the_second_read(antt_series):
    antt_series.add(ROUTE_SP_RJ, "LEITO", "2026-03", "2026-02", 10, 300.0)
    cache = {}
    with _series_reads() as reads:
        first = fetch_fare_observations(antt_series.db, ROUTE_SP_RJ, "LEITO", cache)
        second = fetch_fare_observations(antt_series.db, ROUTE_SP_RJ, "LEITO", cache)
    assert first == second and len(reads) == 1


@pytest.mark.parametrize("rows, expected", [
    ([], []),
    ([("2026-01", 100.0, 10), ("2026-01", 200.0, 30)], [("2026-01", 175.0, 40)]),   # ponderada por bilhetes
    ([("2026-02", 100.0, 0)], []),                                                 # mês sem bilhete sai
])
def test_monthly_means(rows, expected):
    from app.analyzers.road_fare_projection import FareObservation

    observations = [FareObservation("2025-12", month, n, mean) for month, mean, n in rows]
    assert monthly_means(observations) == expected
