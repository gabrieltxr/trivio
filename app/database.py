"""Trivio Database Configuration & Session Management.

SQLAlchemy 2.0+ engine setup supporting local SQLite development and
PostgreSQL in production with connection pooling and foreign key enforcement.
"""

from typing import Generator
import warnings
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.schema import CreateIndex
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
        # WAL: escritas vão para o arquivo -wal, então leitura e escrita não se bloqueiam (a
        # busca lê séries e grava cotações em threads ao mesmo tempo). Com WAL, NORMAL só faz
        # fsync nos checkpoints: uma queda de energia pode perder os últimos commits, sem
        # corromper o banco. Banco em memória não tem WAL e ignora o pedido.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
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


# Restrições únicas que foram trocadas por outra mais completa: (tabela, nome antigo).
# uq_bus_schedule não tinha o feed, então uma partida da ANTT e a mesma partida de um feed
# GTFS no mesmo período colidiam e o import do GTFS falhava (substituída por uq_bus_schedule_feed).
_REPLACED_UNIQUE_CONSTRAINTS = (
    ("bus_schedules", "uq_bus_schedule"),
)


def _rebuild_sqlite_table(conn, table_name: str) -> None:
    """Recria uma tabela SQLite pelo modelo atual, preservando as linhas.

    O SQLite não remove restrição de tabela com ALTER TABLE: a saída documentada é criar a
    tabela nova, copiar os dados e apagar a antiga. Índices da tabela antiga são apagados
    antes, para os nomes ficarem livres para a nova.
    """
    table = Base.metadata.tables[table_name]
    old_name = f"_old_{table_name}"
    for index in inspect(conn).get_indexes(table_name):
        conn.execute(text(f'DROP INDEX IF EXISTS "{index["name"]}"'))
    conn.execute(text(f'ALTER TABLE "{table_name}" RENAME TO "{old_name}"'))
    table.create(conn)
    old_columns = {col["name"] for col in inspect(conn).get_columns(old_name)}
    columns = ", ".join(f'"{c.name}"' for c in table.columns if c.name in old_columns)
    conn.execute(text(f'INSERT INTO "{table_name}" ({columns}) SELECT {columns} FROM "{old_name}"'))
    conn.execute(text(f'DROP TABLE "{old_name}"'))


def _replace_unique_constraints(conn) -> None:
    # A reflexão avisa a cada chamada que pulou o índice de expressão (uq_bus_schedule_feed):
    # é esperado, e o aviso apareceria a cada inicialização da API.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Skipped unsupported reflection of expression-based index")
        _replace_unique_constraints_inner(conn)


def _replace_unique_constraints_inner(conn) -> None:
    inspector = inspect(conn)
    for table_name, constraint in _REPLACED_UNIQUE_CONSTRAINTS:
        if not inspector.has_table(table_name):
            continue
        names = {uc.get("name") for uc in inspector.get_unique_constraints(table_name)}
        if constraint not in names:
            continue
        if conn.dialect.name == "sqlite":
            _rebuild_sqlite_table(conn, table_name)
        else:
            conn.execute(text(f'ALTER TABLE "{table_name}" DROP CONSTRAINT "{constraint}"'))
        inspector = inspect(conn)
        # A unicidade nova é um índice: create_all() só cria índice junto da tabela, então numa
        # tabela que já existia (PostgreSQL) ele é criado aqui. IF NOT EXISTS por nome, porque a
        # reflexão do SQLAlchemy não enxerga índice de expressão; no SQLite recriado já existe.
        for index in Base.metadata.tables[table_name].indexes:
            if index.unique:
                conn.execute(CreateIndex(index, if_not_exists=True))


def ensure_schema(bind=None) -> None:
    """Cria as tabelas que faltam, acrescenta colunas novas, troca restrições únicas
    antigas e remove índices aposentados.

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
        _replace_unique_constraints(conn)
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
