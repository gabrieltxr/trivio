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
