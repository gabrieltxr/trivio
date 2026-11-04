"""Unit and Integration tests for Trivio REST API Routes (/api/v1)."""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.database import Base, get_db
from app.models import FareQuote, Hub, ModalType, Terminal


@pytest.fixture
def test_db_session():
    """Cria banco SQLite em memória com estrutura e seeds completos para testes de rotas."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()

    # 1. Seed Hubs
    hub_sp = Hub(id=1, ibge_code="3550308", name="São Paulo", state="SP", latitude=-23.5505, longitude=-46.6333)
    hub_rj = Hub(id=2, ibge_code="3304557", name="Rio de Janeiro", state="RJ", latitude=-22.9068, longitude=-43.1729)
    session.add_all([hub_sp, hub_rj])
    session.flush()

    # 2. Seed Terminais
    term_cgh = Terminal(
        id=10, hub_id=1, name="Aeroporto de Congonhas", modal=ModalType.AIR, official_code="CGH",
        latitude=-23.6273, longitude=-46.6565, avg_city_center_distance_km=9.5,
        estimated_transfer_cost_brl=38.0, estimated_transfer_minutes=25,
    )
    term_tiete = Terminal(
        id=11, hub_id=1, name="Terminal Rodoviário do Tietê", modal=ModalType.ROAD, official_code="TIETE",
        latitude=-23.5162, longitude=-46.6251, avg_city_center_distance_km=5.0,
        estimated_transfer_cost_brl=22.0, estimated_transfer_minutes=15,
    )
    term_sdu = Terminal(
        id=20, hub_id=2, name="Aeroporto Santos Dumont", modal=ModalType.AIR, official_code="SDU",
        latitude=-22.9105, longitude=-43.1631, avg_city_center_distance_km=2.0,
        estimated_transfer_cost_brl=18.0, estimated_transfer_minutes=10,
    )
    session.add_all([term_cgh, term_tiete, term_sdu])
    session.flush()

    # 3. Seed Cotações em Cache
    now = datetime.now(timezone.utc)
    target_date = (now + timedelta(days=5)).replace(hour=14, minute=0, second=0, microsecond=0)

    # Cotação 1: Voo CGH -> SDU
    q_flight = FareQuote(
        id=101,
        dedup_signature="sig_flight_101",
        origin_terminal_id=10,
        destination_terminal_id=20,
        modal=ModalType.AIR,
        provider_source="amadeus",
        carrier_name="LATAM Airlines",
        service_class="ECONOMY",
        departure_time=target_date,
        arrival_time=target_date + timedelta(hours=1, minutes=10),
        duration_minutes=70,
        door_to_door_duration_minutes=190,
        base_price=350.0,
        taxes_price=35.0,
        baggage_fee_estimate=110.0,
        transfer_cost_estimate=38.0,
        total_effective_price=533.0,
        trivio_score=680.0,
        route_metadata={"is_direct": True},
        collected_at=now,
        expires_at=now + timedelta(hours=4),
    )

    # Cotação 2: Voo mais caro CGH -> SDU
    q_flight_expensive = FareQuote(
        id=102,
        dedup_signature="sig_flight_102",
        origin_terminal_id=10,
        destination_terminal_id=20,
        modal=ModalType.AIR,
        provider_source="amadeus",
        carrier_name="GOL",
        service_class="ECONOMY",
        departure_time=target_date + timedelta(hours=2),
        arrival_time=target_date + timedelta(hours=3, minutes=10),
        duration_minutes=70,
        door_to_door_duration_minutes=190,
        base_price=480.0,
        taxes_price=35.0,
        baggage_fee_estimate=110.0,
        transfer_cost_estimate=38.0,
        total_effective_price=663.0,
        trivio_score=810.0,
        route_metadata={"is_direct": True},
        collected_at=now,
        expires_at=now + timedelta(hours=4),
    )

    session.add_all([q_flight, q_flight_expensive])
    session.commit()

    yield session
    session.close()


@pytest.fixture
def client(test_db_session):
    """Configura o TestClient com override da dependência get_db."""
    def override_get_db():
        yield test_db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_list_hubs(client):
    """Valida listagem de hubs e filtros por estado e texto."""
    # Listagem completa
    res = client.get("/api/v1/hubs")
    assert res.status_code == 200
    hubs = res.json()
    assert len(hubs) == 2

    # Filtro por UF
    res_sp = client.get("/api/v1/hubs?state=SP")
    assert res_sp.status_code == 200
    assert len(res_sp.json()) == 1
    assert res_sp.json()[0]["name"] == "São Paulo"

    # Busca por texto / autocomplete
    res_search = client.get("/api/v1/hubs?query=Janeiro")
    assert res_search.status_code == 200
    assert len(res_search.json()) == 1
    assert res_search.json()[0]["state"] == "RJ"


def test_get_hub_detail(client):
    """Valida detalhes do Hub e terminais vinculados."""
    # Hub existente
    res = client.get("/api/v1/hubs/1")
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "São Paulo"
    assert len(data["terminals"]) == 2
    codes = [t["official_code"] for t in data["terminals"]]
    assert "CGH" in codes
    assert "TIETE" in codes

    # Hub inexistente (404)
    res_404 = client.get("/api/v1/hubs/9999")
    assert res_404.status_code == 404
    assert res_404.json()["error"] is True


def test_list_terminals(client):
    """Valida listagem e filtros de terminais."""
    # Todos os terminais
    res = client.get("/api/v1/terminals")
    assert res.status_code == 200
    assert len(res.json()) == 3

    # Filtro por modal Aéreo
    res_air = client.get("/api/v1/terminals?modal=AIR")
    assert res_air.status_code == 200
    assert len(res_air.json()) == 2
    assert all(t["modal"] == "AIR" for t in res_air.json())

    # Filtro por código IATA
    res_cgh = client.get("/api/v1/terminals?code=CGH")
    assert res_cgh.status_code == 200
    assert len(res_cgh.json()) == 1
    assert res_cgh.json()[0]["official_code"] == "CGH"


def test_get_catchment_terminals(client):
    """Valida busca geodésica de terminais por hub_id e por coordenadas."""
    # 1. Por hub_id
    res_hub = client.get("/api/v1/catchment?hub_id=1&radius_km=50")
    assert res_hub.status_code == 200
    data_hub = res_hub.json()
    assert data_hub["radius_km"] == 50.0
    assert data_hub["total_terminals"] >= 2
    assert data_hub["origin"]["type"] == "hub"

    # 2. Por coordenadas (Centro de SP)
    res_coords = client.get("/api/v1/catchment?lat=-23.5505&lon=-46.6333&radius_km=30")
    assert res_coords.status_code == 200
    data_coords = res_coords.json()
    assert data_coords["origin"]["type"] == "coordinates"
    assert data_coords["total_terminals"] >= 2

    # 3. Requisição inválida sem hub_id nem coordenadas (400)
    res_bad = client.get("/api/v1/catchment")
    assert res_bad.status_code == 400
    assert "Informe" in res_bad.json()["message"]


def test_get_cached_quotes(client):
    """Valida consulta e ordenação por Trivio Score de cotações em cache."""
    # Consulta rota CGH (10) -> SDU (20)
    res = client.get("/api/v1/quotes/cached?origin_terminal_id=10&destination_terminal_id=20")
    assert res.status_code == 200
    data = res.json()
    assert data["total_quotes"] == 2
    quotes = data["quotes"]

    # Deve estar ordenado pelo menor Trivio Score (680.0 antes de 810.0)
    assert quotes[0]["trivio_score"] == 680.0
    assert quotes[1]["trivio_score"] == 810.0
    assert quotes[0]["carrier_name"] == "LATAM Airlines"
    assert quotes[0]["modal"] == "AIR"

    # Filtro de modal
    res_road = client.get("/api/v1/quotes/cached?origin_terminal_id=10&destination_terminal_id=20&modal=ROAD")
    assert res_road.status_code == 200
    assert res_road.json()["total_quotes"] == 0
