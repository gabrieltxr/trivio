"""Limpeza do banco: só sai o que é de provedor removido, estimativa gravada por engano ou cache vencido."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.database import SessionLocal
from app.models import FareQuote, ModalType, ProviderMapping, ProviderQuery, Terminal

_spec = importlib.util.spec_from_file_location(
    "cleanup_legacy_data", Path(__file__).resolve().parent.parent / "scripts" / "cleanup_legacy_data.py"
)
cleanup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cleanup)

NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _quote(db, provider, expires_at, collected_at=NOW, signature="sig"):
    gru, sdu = (db.query(Terminal).filter(Terminal.official_code == c).one() for c in ("GRU", "SDU"))
    fq = FareQuote(
        dedup_signature=f"cleanup-{signature}", origin_terminal_id=gru.id, destination_terminal_id=sdu.id,
        modal=ModalType.AIR, provider_source=provider, carrier_name="LATAM", service_class="ECONOMY",
        departure_time=NOW + timedelta(days=5), arrival_time=NOW + timedelta(days=5, hours=1),
        duration_minutes=60, door_to_door_duration_minutes=180, base_price=300.0,
        total_effective_price=300.0, collected_at=collected_at, expires_at=expires_at,
    )
    db.add(fq)
    return fq


@pytest.fixture
def db():
    session = SessionLocal()
    created = []
    terminal = session.query(Terminal).filter(Terminal.official_code == "GRU").one()
    for provider, ext in (("amadeus", "GRU-cleanup"), ("kiwi", "GRU-cleanup")):
        created.append(ProviderMapping(terminal_id=terminal.id, provider_name=provider, external_id=ext))
    created += [
        _quote(session, "amadeus", NOW - timedelta(days=1), signature="old"),
        _quote(session, "trivio_interliner", NOW + timedelta(days=1), signature="interline"),
        _quote(session, "ignav", NOW + timedelta(hours=1), signature="valid"),
        _quote(session, "ignav", NOW - timedelta(hours=1), signature="recent-expired"),
        ProviderQuery(provider="ignav", query_key="q-old", status="ok", billed=1,
                      requested_at=datetime(2020, 1, 1), expires_at=datetime(2020, 1, 1, 2)),
        ProviderQuery(provider="ignav", query_key="q-new", status="ok", billed=1,
                      requested_at=NOW, expires_at=NOW + timedelta(hours=2)),
    ]
    session.add_all(created)
    session.commit()
    yield session
    for obj in created:
        session.query(type(obj)).filter(type(obj).id == obj.id).delete()
    session.commit()
    session.close()


def _left(query):
    return query.count()


def test_selects_only_legacy_and_expired_rows(db):
    queries = cleanup.build_queries(db, keep_history=False, prune_queries=True)
    mapping_providers = {m.provider_name for m in queries["provider_mappings"]}
    assert {"amadeus", "kiwi"} <= mapping_providers
    assert not mapping_providers & cleanup.ACTIVE_MAPPING_PROVIDERS   # ignav e clickbus ficam

    signatures = {q.dedup_signature for q in queries["fare_quotes"]}
    assert {"cleanup-old", "cleanup-interline", "cleanup-recent-expired"} <= signatures
    assert "cleanup-valid" not in signatures

    keys = {q.query_key for q in queries["provider_queries"]}
    assert "q-old" in keys and "q-new" not in keys   # q-new ainda conta no saldo e é cache válido


def test_keep_history_preserves_recent_quotes_of_active_providers(db):
    signatures = {q.dedup_signature for q in cleanup.build_queries(db, keep_history=True)["fare_quotes"]}
    assert "cleanup-recent-expired" not in signatures
    assert {"cleanup-old", "cleanup-interline"} <= signatures


def test_provider_queries_are_kept_unless_asked(db):
    assert "provider_queries" not in cleanup.build_queries(db, keep_history=False, prune_queries=False)
