"""ensure_schema: índices aposentados saem do banco sem quebrar quem já não os tem."""

from sqlalchemy import create_engine, inspect, text

from app.database import Base, ensure_schema

import pytest

# Esperado: o SQLAlchemy não reflete índice de expressão (uq_bus_schedule_feed) e avisa.
pytestmark = pytest.mark.filterwarnings("ignore:Skipped unsupported reflection of expression-based index")


def _index_names(engine, table):
    return {ix["name"] for ix in inspect(engine).get_indexes(table)}


def test_ensure_schema_drops_low_cardinality_modal_indexes():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:   # banco criado antes da mudança: os índices ainda existem
        conn.execute(text("CREATE INDEX ix_route_monthly_fares_modal ON route_monthly_fares (modal)"))
        conn.execute(text("CREATE INDEX ix_fare_quotes_modal ON fare_quotes (modal)"))

    ensure_schema(engine)
    ensure_schema(engine)   # idempotente

    assert "ix_route_monthly_fares_modal" not in _index_names(engine, "route_monthly_fares")
    assert "ix_fare_quotes_modal" not in _index_names(engine, "fare_quotes")
    # O índice que a busca por rota precisa continua lá
    assert "ix_route_monthly_fares_route_key" in _index_names(engine, "route_monthly_fares")


def test_route_lookup_uses_route_key_index():
    engine = create_engine("sqlite://")
    ensure_schema(engine)
    with engine.connect() as conn:
        plan = conn.execute(text(
            "EXPLAIN QUERY PLAN SELECT trip_month FROM route_monthly_fares "
            "WHERE modal = 'ROAD' AND route_key = 'a|b' AND service_class = 'LEITO'"
        )).fetchall()
    detail = " ".join(str(row[-1]) for row in plan)
    assert "ix_route_monthly_fares_modal" not in detail
    assert "route_key=?" in detail


def test_sqlite_connections_use_wal_with_normal_sync():
    """Leitura e escrita não se bloqueiam (WAL) e o commit não faz fsync a cada transação."""
    from app.database import engine

    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA synchronous").scalar() == 1   # NORMAL
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


# Tabela bus_schedules como era antes de a unicidade incluir o feed.
_OLD_BUS_SCHEDULES_DDL = """
CREATE TABLE bus_schedules (
    id INTEGER NOT NULL PRIMARY KEY,
    route_key VARCHAR(160) NOT NULL,
    origin_city VARCHAR(120) NOT NULL,
    origin_state VARCHAR(2) NOT NULL,
    destination_city VARCHAR(120) NOT NULL,
    destination_state VARCHAR(2) NOT NULL,
    company_name VARCHAR(150) NOT NULL,
    company_cnpj VARCHAR(20),
    line_prefix VARCHAR(20) NOT NULL,
    service_class VARCHAR(30) NOT NULL,
    departure_time VARCHAR(5) NOT NULL,
    weekdays VARCHAR(7) NOT NULL,
    months VARCHAR(12) NOT NULL,
    source_period VARCHAR(7) NOT NULL,
    feed VARCHAR(40),
    imported_at DATETIME,
    CONSTRAINT uq_bus_schedule UNIQUE (route_key, line_prefix, service_class, departure_time, source_period)
)
"""


def _has_sqlite_index(engine, name):
    """Índice de expressão não aparece em inspect().get_indexes(): consulta o sqlite_master."""
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = :n"), {"n": name}
        ).first() is not None


def _schedule(conn, feed):
    conn.execute(text(
        "INSERT INTO bus_schedules (route_key, origin_city, origin_state, destination_city, "
        "destination_state, company_name, line_prefix, service_class, departure_time, weekdays, "
        "months, source_period, feed) VALUES ('A-SP|B-SP', 'A', 'SP', 'B', 'SP', 'Viação', '1234', "
        "'CONVENCIONAL', '08:00', '1111111', '111111111111', '2026-10', :feed)"
    ), {"feed": feed})


def test_ensure_schema_lets_antt_and_gtfs_share_a_departure():
    """A mesma partida da ANTT e de um feed GTFS no mesmo período convivem; duplicata não."""
    from sqlalchemy.exc import IntegrityError

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(_OLD_BUS_SCHEDULES_DDL))
        conn.execute(text("CREATE INDEX ix_bus_schedules_route_key ON bus_schedules (route_key)"))
        _schedule(conn, None)   # linha da ANTT gravada antes da migração

    ensure_schema(engine)
    ensure_schema(engine)   # idempotente

    names = {uc["name"] for uc in inspect(engine).get_unique_constraints("bus_schedules")}
    assert "uq_bus_schedule" not in names
    assert _has_sqlite_index(engine, "uq_bus_schedule_feed")
    assert "ix_bus_schedules_route_key" in _index_names(engine, "bus_schedules")
    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM bus_schedules")).scalar() == 1   # dado preservado
        _schedule(conn, "artesp")   # mesma partida, outro feed: aceita

    for feed in (None, "artesp"):   # repetida dentro da mesma fonte: recusada (inclusive feed NULL)
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _schedule(conn, feed)


def test_fresh_schema_has_the_feed_aware_unique_index():
    engine = create_engine("sqlite://")
    ensure_schema(engine)
    ensure_schema(engine)
    assert _has_sqlite_index(engine, "uq_bus_schedule_feed")
    assert inspect(engine).get_unique_constraints("bus_schedules") == []
