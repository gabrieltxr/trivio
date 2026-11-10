"""Garante que a suíte roda no banco temporário semeado (ver tests/conftest.py)."""

from sqlalchemy import func, select

from app.database import SessionLocal, engine
from app.models import Hub


def test_suite_uses_temporary_database():
    assert "trivio_tests_" in str(engine.url)
    assert "trivio_dev.db" not in str(engine.url)


def test_suite_database_is_seeded_with_hubs():
    with SessionLocal() as session:
        total = session.execute(select(func.count()).select_from(Hub)).scalar()
    assert total >= 7
