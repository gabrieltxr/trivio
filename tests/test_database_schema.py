"""ensure_schema: índices aposentados saem do banco sem quebrar quem já não os tem."""

from sqlalchemy import create_engine, inspect, text

from app.database import Base, ensure_schema


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
