"""Tests for the Geo Layer: EntityResolver and CatchmentFinder."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import Hub, Terminal, ModalType
from app.geo.entity_resolver import EntityResolver, normalize_text
from app.geo.catchment import CatchmentFinder


@pytest.fixture
def db_session():
    """Create a fresh in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed Hubs
    sp = Hub(
        ibge_code="3550308",
        name="São Paulo",
        state="SP",
        latitude=-23.5505,
        longitude=-46.6333,
    )
    campinas = Hub(
        ibge_code="3509502",
        name="Campinas",
        state="SP",
        latitude=-22.9056,
        longitude=-47.0608,
    )
    session.add_all([sp, campinas])
    session.flush()

    # Seed Terminals in SP
    cgh = Terminal(
        hub_id=sp.id,
        name="Aeroporto de Congonhas",
        modal=ModalType.AIR,
        official_code="CGH",
        latitude=-23.6273,
        longitude=-46.6565,
        avg_city_center_distance_km=9.5,
        estimated_transfer_cost_brl=35.0,
        estimated_transfer_minutes=25,
    )
    gru = Terminal(
        hub_id=sp.id,
        name="Aeroporto Internacional de Guarulhos",
        modal=ModalType.AIR,
        official_code="GRU",
        latitude=-23.4356,
        longitude=-46.4731,
        avg_city_center_distance_km=25.0,
        estimated_transfer_cost_brl=75.0,
        estimated_transfer_minutes=50,
    )
    tiete = Terminal(
        hub_id=sp.id,
        name="Terminal Rodoviário Tietê",
        modal=ModalType.ROAD,
        official_code="TIETE",
        latitude=-23.5161,
        longitude=-46.6253,
        avg_city_center_distance_km=5.0,
        estimated_transfer_cost_brl=20.0,
        estimated_transfer_minutes=15,
    )

    # Seed Terminal in Campinas (Viracopos)
    vcp = Terminal(
        hub_id=campinas.id,
        name="Aeroporto Internacional de Viracopos",
        modal=ModalType.AIR,
        official_code="VCP",
        latitude=-23.0074,
        longitude=-47.1345,
        avg_city_center_distance_km=14.0,
        estimated_transfer_cost_brl=45.0,
        estimated_transfer_minutes=30,
    )

    session.add_all([cgh, gru, tiete, vcp])
    session.commit()

    yield session
    session.close()


def test_normalize_text():
    assert normalize_text("São Paulo - SP") == "sao paulo sp"
    assert normalize_text("  CURITIBA / PR  ") == "curitiba pr"


def test_resolve_official_code(db_session):
    resolver = EntityResolver(db_session)
    res = resolver.resolve_official_code("gru")
    assert res is not None
    assert res.official_code == "GRU"
    assert res.modal == ModalType.AIR


def test_resolve_hub(db_session):
    resolver = EntityResolver(db_session)
    res = resolver.resolve_hub("Sao Paulo")
    assert res is not None
    assert res.name == "São Paulo"
    assert res.entity_type == "HUB"


def test_resolve_terminal_fuzzy(db_session):
    resolver = EntityResolver(db_session)
    res = resolver.resolve_terminal("Rodoviaria Tiete")
    assert res is not None
    assert res.official_code == "TIETE"
    assert res.modal == ModalType.ROAD


def test_catchment_finder(db_session):
    finder = CatchmentFinder(db_session)
    # Search from São Paulo Hub coordinates
    sp_hub = db_session.query(Hub).filter(Hub.name == "São Paulo").first()
    results = finder.find_terminals_for_hub(sp_hub.id, max_radius_km=120.0)

    # Should find 4 terminals (CGH, Tietê, GRU from SP, and VCP from Campinas)
    assert len(results) == 4

    # Check that primary hub terminals are identified
    primary_terms = [t for t in results if t.is_primary_hub]
    assert len(primary_terms) == 3

    # Check that VCP is captured in the catchment radius (~80-100km away)
    vcp = next((t for t in results if t.official_code == "VCP"), None)
    assert vcp is not None
    assert not vcp.is_primary_hub
    assert 70.0 <= vcp.distance_km <= 110.0
    # Transfer cost to VCP from SP should reflect intercity transfer
    assert vcp.estimated_transfer_cost_brl > 50.0
