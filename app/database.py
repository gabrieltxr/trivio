"""Trivio Database Configuration & Session Management.

SQLAlchemy 2.0+ engine setup supporting local SQLite development and
PostgreSQL in production with connection pooling and foreign key enforcement.
"""

from typing import Generator
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from app.config import settings

# Determine engine connection arguments
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

# Create the SQLAlchemy Engine
engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=settings.DB_ECHO,
    pool_pre_ping=True,
)

# Enable Foreign Key constraints for SQLite connections
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# Session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


class Base(DeclarativeBase):
    """Base declarative class for all Trivio ORM models."""
    pass


def get_db() -> Generator[Session, None, None]:
    """Dependency for yielding transactional SQLAlchemy database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Colunas acrescentadas depois da criação inicial das tabelas. O projeto não usa
# Alembic; create_all() não altera tabelas existentes, então as acrescentamos aqui.
_ADDED_COLUMNS = {
    "route_monthly_fares": {
        "p10_brl": "FLOAT",
        "p25_brl": "FLOAT",
        "p50_brl": "FLOAT",
        "p75_brl": "FLOAT",
        "p90_brl": "FLOAT",
    },
    "bus_schedules": {
        "feed": "VARCHAR(40)",
    },
}


# Índices que já existiram e foram retirados dos modelos. Índice de coluna com só dois valores
# (modal) atrapalha: sem estatísticas, o SQLite o escolhia no lugar do índice de route_key
# e a busca de uma rota varria ~300 mil linhas (56 ms em vez de 0,15 ms por consulta).
_DROPPED_INDEXES = (
    "ix_route_monthly_fares_modal",
    "ix_fare_quotes_modal",
)


def ensure_schema(bind=None) -> None:
    """Cria as tabelas que faltam, acrescenta colunas novas e remove índices aposentados.

    Idempotente: pode rodar a cada inicialização. Só acrescenta colunas anuláveis.
    """
    import app.models  # noqa: F401  (registra os modelos no metadata)

    bind = bind or engine
    Base.metadata.create_all(bind)
    inspector = inspect(bind)
    with bind.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if not inspector.has_table(table):
                continue
            existing = {col["name"] for col in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
        for index_name in _DROPPED_INDEXES:
            conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))


def analyze_tables(*tables: str, bind=None) -> None:
    """Atualiza as estatísticas do otimizador depois de uma carga grande.

    Com as estatísticas (sqlite_stat1 no SQLite), o planejador escolhe o índice mais
    seletivo em vez de adivinhar. Funciona igual no SQLite e no PostgreSQL.
    """
    bind = bind or engine
    with bind.begin() as conn:
        for table in tables:
            conn.execute(text(f"ANALYZE {table}"))
