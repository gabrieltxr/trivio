"""Configuração global da suíte.

Cada execução de testes usa um SQLite temporário, semeado com os hubs e terminais de
`scripts/init_db.py`. Assim os testes não dependem do `trivio_dev.db` de cada máquina
(que pode estar vazio numa worktree nova) e nunca gravam nele.

A variável é definida aqui, antes de qualquer importação de `app`, porque o engine do
SQLAlchemy é criado na importação de `app.database`.
"""

import atexit
import importlib.util
import io
import os
import shutil
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_TEST_DIR = tempfile.mkdtemp(prefix="trivio_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{(Path(_TEST_DIR) / 'trivio_test.db').as_posix()}"


def _seed_test_database() -> None:
    """Cria as tabelas e aplica o seed oficial (`init_database`) no banco temporário."""
    spec = importlib.util.spec_from_file_location("trivio_init_db", ROOT / "scripts" / "init_db.py")
    init_db = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(init_db)
    with redirect_stdout(io.StringIO()):  # o seed imprime tabelas no terminal; os testes não precisam disso
        init_db.init_database(reset=True)


def _cleanup() -> None:
    from app.database import engine

    engine.dispose()
    shutil.rmtree(_TEST_DIR, ignore_errors=True)


_seed_test_database()
atexit.register(_cleanup)


import pytest


class AnttSeries:
    """Insere linhas da série ANTT no banco de teste; tudo é removido ao fim do teste."""

    SOURCE = "antt_test"

    def __init__(self):
        from app.database import SessionLocal

        self.db = SessionLocal()

    def add(self, route_key, service_class, trip_month, emission_period, sample_size, mean_brl):
        from app.models import ModalType, RouteMonthlyFare

        origin, destination = route_key.split("|")
        origin_city, origin_state = origin.rsplit("-", 1)
        destination_city, destination_state = destination.rsplit("-", 1)
        self.db.add(RouteMonthlyFare(
            route_key=route_key, origin_city=origin_city, origin_state=origin_state,
            destination_city=destination_city, destination_state=destination_state,
            modal=ModalType.ROAD, service_class=service_class, trip_month=trip_month,
            emission_period=emission_period, source=self.SOURCE, sample_size=sample_size,
            mean_brl=mean_brl, min_brl=mean_brl - 10, max_brl=mean_brl + 10,
        ))
        self.db.commit()
        return self

    def cleanup(self):
        from app.models import RouteMonthlyFare

        self.db.query(RouteMonthlyFare).filter(RouteMonthlyFare.source == self.SOURCE).delete()
        self.db.commit()
        self.db.close()


@pytest.fixture
def antt_series():
    series = AnttSeries()
    try:
        yield series
    finally:
        series.cleanup()
