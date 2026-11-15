"""Slugs da ClickBus: o mapa estático do coletor e os ProviderMapping da semente dizem o mesmo."""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.websocket import _clickbus_external_id
from app.collectors.road.clickbus import CLICKBUS_SLUG_MAP
from app.database import Base
from app.models import Hub, ModalType, ProviderMapping, Terminal
from app.seed import upsert_hubs
from app.services.quote_pipeline import find_terminal_by_external_id

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _road_terminals(hubs):
    for hub in hubs:
        for term in hub["terminals"]:
            if str(getattr(term["modal"], "value", term["modal"])) == "ROAD":
                yield term


CANDIDATE_HUBS = _load("hub_candidates").CANDIDATE_HUBS
SEED_ROAD = list(_road_terminals(_load("init_db").SEED_HUBS))
CANDIDATE_ROAD = list(_road_terminals(CANDIDATE_HUBS))


def _empty_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_every_road_terminal_has_a_slug_and_only_road_terminals_do():
    codes = {t["official_code"] for t in SEED_ROAD + CANDIDATE_ROAD}
    assert set(CLICKBUS_SLUG_MAP) == codes


@pytest.mark.parametrize("term", SEED_ROAD + CANDIDATE_ROAD, ids=lambda t: t["official_code"])
def test_seed_mapping_agrees_with_the_static_map(term):
    """A ida (slug enviado) e a volta (rodoviária achada pelo slug da cotação) usam o mesmo valor."""
    slugs = [m["external_id"] for m in term.get("mappings", []) if m["provider_name"] == "clickbus"]
    assert slugs == [CLICKBUS_SLUG_MAP[term["official_code"]]]


@pytest.fixture
def db():
    session = _empty_db()
    hub = Hub(id=1, ibge_code="0", name="Teste", state="SP", latitude=0.0, longitude=0.0)
    session.add(hub)
    session.flush()
    mapped = Terminal(id=1, hub_id=1, name="Tietê", modal=ModalType.ROAD, official_code="TIETE",
                      latitude=0.0, longitude=0.0)
    unmapped = Terminal(id=2, hub_id=1, name="Rodoviária de BH", modal=ModalType.ROAD,
                        official_code="ROD_BELO_HORIZONTE", latitude=0.0, longitude=0.0)
    session.add_all([mapped, unmapped])
    session.add(ProviderMapping(terminal_id=1, provider_name="clickbus", external_id="slug-do-banco"))
    session.commit()
    yield session
    session.close()


def test_external_id_prefers_the_provider_mapping(db):
    assert _clickbus_external_id(db, db.get(Terminal, 1), "qualquer") == "slug-do-banco"


def test_external_id_falls_back_to_the_static_map_without_mapping(db):
    # Antes o mapa usava ROD_BH e a busca saía com "rod_belo_horizonte".
    assert _clickbus_external_id(db, db.get(Terminal, 2), "qualquer") == "belo-horizonte-mg"


def test_candidate_hub_quote_finds_its_terminal_back():
    """Com o vínculo dos candidatos gravado, a cotação que volta com o slug acha a rodoviária."""
    db = _empty_db()
    upsert_hubs(db, CANDIDATE_HUBS)
    db.commit()
    terminal = db.query(Terminal).filter(Terminal.official_code == "ROD_BELO_HORIZONTE").one()
    slug = _clickbus_external_id(db, terminal, "qualquer")
    assert slug == "belo-horizonte-mg"
    assert find_terminal_by_external_id(db, "clickbus", slug).id == terminal.id
    db.close()
