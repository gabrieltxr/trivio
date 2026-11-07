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


def check_db_connection() -> bool:
    """Utility to quickly verify database connectivity."""
    try:
        with engine.connect() as conn:
            from sqlalchemy import text
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


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
}


def ensure_schema(bind=None) -> None:
    """Cria as tabelas que faltam e acrescenta colunas novas às existentes.

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
