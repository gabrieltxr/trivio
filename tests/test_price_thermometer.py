"""Unit tests for Price Thermometer Engine (Hopper Pattern) using Polars."""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.analyzers.price_thermometer import (
    enrich_quote_with_thermometer,
    enrich_quotes_batch_with_thermometer,
    evaluate_price_distribution,
    fetch_historical_prices_from_db,
)
from app.models import Base, FareQuote, Hub, ModalType, Terminal


@pytest.fixture
def mock_db_session():
    """Cria uma sessão SQLite in-memory para testes de consulta histórica."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()

    # Seed Hub e Terminais de teste
    hub = Hub(
        id=1,
        ibge_code="3550308",
        name="São Paulo",
        state="SP",
        latitude=-23.5505,
        longitude=-46.6333,
    )
    term1 = Terminal(
        id=10,
        hub_id=1,
        name="Aeroporto de Congonhas",
        official_code="CGH",
        modal=ModalType.AIR,
        latitude=-23.6273,
        longitude=-46.6565,
    )
    term2 = Terminal(
        id=20,
        hub_id=1,
        name="Aeroporto Santos Dumont",
        official_code="SDU",
        modal=ModalType.AIR,
        latitude=-22.9105,
        longitude=-43.1631,
    )
    session.add_all([hub, term1, term2])
    session.commit()

    # Seed de 10 cotações históricas para CGH -> SDU
    prices = [220.0, 240.0, 260.0, 280.0, 300.0, 320.0, 350.0, 380.0, 420.0, 500.0]
    now = datetime.now(timezone.utc)
    for idx, p in enumerate(prices):
        fq = FareQuote(
            dedup_signature=f"hash_{idx}",
            origin_terminal_id=10,
            destination_terminal_id=20,
            modal=ModalType.AIR,
            provider_source="broker_a",
            carrier_name="LATAM",
            service_class="ECONOMY",
            departure_time=now + timedelta(days=5),
            arrival_time=now + timedelta(days=5, hours=1),
            duration_minutes=60,
            door_to_door_duration_minutes=180,
            base_price=p,
            taxes_price=35.0,
            total_effective_price=p + 35.0,
            collected_at=now - timedelta(days=idx),
            expires_at=now + timedelta(hours=2),
        )
        session.add(fq)
    session.commit()

    yield session
    session.close()


def test_price_thermometer_cold_start():
    """Valida comportamento para rota sem histórico ou com poucas amostras (<5)."""
    # 0 amostras
    res_empty = evaluate_price_distribution(250.0, [])
    assert res_empty.status == "INSUFFICIENT_DATA"
    assert res_empty.percentile is None
    assert res_empty.sample_size == 0
    assert "insuficiente" in res_empty.recommendation.lower()

    # 3 amostras (< 5)
    res_few = evaluate_price_distribution(200.0, [180.0, 220.0, 250.0])
    assert res_few.status == "INSUFFICIENT_DATA"
    assert res_few.sample_size == 3
    assert res_few.historical_median_brl == 220.0


def test_price_thermometer_low_status():
    """Valida identificação de passagem barata (abaixo de P25 - Ótimo momento)."""
    # 10 amostras: P25 = 140, Median = 210, P75 = 270
    history = [100.0, 120.0, 140.0, 160.0, 200.0, 220.0, 240.0, 270.0, 300.0, 350.0]
    
    # Preço 115 está bem abaixo de P25 (140)
    res = evaluate_price_distribution(115.0, history)
    assert res.status == "LOW"
    assert res.percentile <= 25
    assert res.difference_percentage < 0
    assert "abaixo da média histórica" in res.recommendation or "Excelente momento" in res.recommendation


def test_price_thermometer_medium_status():
    """Valida preço na faixa padrão de mercado (entre P25 e P75)."""
    history = [100.0, 120.0, 140.0, 160.0, 200.0, 220.0, 240.0, 270.0, 300.0, 350.0]
    
    # Preço 210 está exatamente na mediana
    res = evaluate_price_distribution(210.0, history)
    assert res.status == "MEDIUM"
    assert 25 < res.percentile < 75
    assert "faixa padrão" in res.recommendation


def test_price_thermometer_high_status():
    """Valida tarifa inflacionada (acima de P75 - Alerta para o usuário)."""
    history = [100.0, 120.0, 140.0, 160.0, 200.0, 220.0, 240.0, 270.0, 300.0, 350.0]
    
    # Preço 380 está acima de P75 (270)
    res = evaluate_price_distribution(380.0, history)
    assert res.status == "HIGH"
    assert res.percentile >= 75
    assert res.difference_percentage > 0
    assert "acima da média histórica" in res.recommendation
    assert "flexibilidade" in res.recommendation


def test_enrich_quote_object_and_dict():
    """Valida injeção de route_metadata['price_thermometer'] em objetos e dicionários."""
    history = [150.0, 180.0, 200.0, 220.0, 250.0, 280.0]

    # Teste com dicionário
    quote_dict = {
        "total_effective_price": 160.0,
        "modal": "ROAD",
        "route_metadata": {},
    }
    enrich_quote_with_thermometer(quote_dict, historical_prices=history)
    assert "price_thermometer" in quote_dict["route_metadata"]
    therm = quote_dict["route_metadata"]["price_thermometer"]
    assert therm["status"] in ("LOW", "MEDIUM")
    assert therm["sample_size"] == 6

    # Teste com classe/objeto
    class MockQuote:
        def __init__(self):
            self.total_effective_price = 320.0
            self.modal = "AIR"
            self.route_metadata = {}

    quote_obj = MockQuote()
    enrich_quote_with_thermometer(quote_obj, historical_prices=history)
    assert quote_obj.route_metadata["price_thermometer"]["status"] == "HIGH"


def test_db_historical_query_and_batch_enrichment(mock_db_session):
    """Valida busca no banco de dados e enriquecimento em lote otimizado."""
    # 1. Valida query direta ao banco
    prices = fetch_historical_prices_from_db(
        db=mock_db_session,
        origin_terminal_id=10,
        destination_terminal_id=20,
        modal="AIR",
    )
    assert len(prices) == 10
    assert all(isinstance(p, float) for p in prices)

    # 2. Valida batch enrichment
    quotes = [
        {
            "origin_terminal_id": 10,
            "destination_terminal_id": 20,
            "modal": "AIR",
            "total_effective_price": 240.0,
            "route_metadata": {},
        },
        {
            "origin_terminal_id": 10,
            "destination_terminal_id": 20,
            "modal": "AIR",
            "total_effective_price": 490.0,
            "route_metadata": {},
        },
    ]

    enrich_quotes_batch_with_thermometer(quotes, db=mock_db_session)

    # A cotação de 240 deve ser LOW ou MEDIUM inferior
    therm1 = quotes[0]["route_metadata"]["price_thermometer"]
    assert therm1["status"] in ("LOW", "MEDIUM")
    assert therm1["sample_size"] == 10

    # A cotação de 490 deve ser HIGH
    therm2 = quotes[1]["route_metadata"]["price_thermometer"]
    assert therm2["status"] == "HIGH"
    assert therm2["sample_size"] == 10


def _air_quote(price: float):
    from app.collectors.base import StandardizedQuote

    dep = datetime(2026, 12, 10, 10, 0, tzinfo=timezone.utc)
    return StandardizedQuote(
        origin_external_id="CGH", destination_external_id="SDU", modal="AIR",
        provider_source="ignav", carrier_name="LATAM", service_class="ECONOMY",
        departure_time=dep, arrival_time=dep + timedelta(minutes=60), duration_minutes=60,
        base_price=price,
    )


def test_batch_reads_anac_band_once_per_route(monkeypatch):
    """Os voos de um lote dividem a rota: a faixa da ANAC sai do banco uma vez só."""
    from app.analyzers import price_thermometer as pt

    calls = []

    def fake_band(db, origin_icao, destination_icao, window_months=pt.ANAC_WINDOW_MONTHS):
        calls.append((origin_icao, destination_icao))
        return pt.AirFareBand(
            months=["2026-01", "2026-02", "2026-03"], seats=5000, min_brl=150.0, p10_brl=200.0,
            p25_brl=260.0, p50_brl=330.0, p75_brl=420.0, p90_brl=520.0, max_brl=900.0,
        )

    monkeypatch.setattr(pt, "fetch_air_fare_band", fake_band)
    quotes = [_air_quote(price) for price in (220.0, 330.0, 480.0, 700.0)]

    enrich_quotes_batch_with_thermometer(quotes, db=object())

    assert calls == [("SBSP", "SBRJ")]
    assert all(q.route_metadata["price_thermometer"]["source"] == pt.ANAC_SOURCE for q in quotes)


def test_batch_reads_antt_series_once_per_route_and_class(monkeypatch):
    """Na série da ANTT a chave é rota + classe: classes diferentes leem séries diferentes."""
    from app.analyzers import price_thermometer as pt
    from app.collectors.base import StandardizedQuote

    calls = []

    def fake_series(db, origin_city, origin_state, destination_city, destination_state, service_class, modal=None):
        calls.append(service_class)
        return [(f"2026-{m:02d}", 150.0 + m, 1000) for m in range(1, 9)]

    monkeypatch.setattr(pt, "fetch_route_monthly_means", fake_series)
    dep = datetime(2026, 12, 10, 22, 0, tzinfo=timezone.utc)
    meta = {"origin_city": "São Paulo", "origin_state": "SP", "destination_city": "Rio de Janeiro",
            "destination_state": "RJ"}
    quotes = [
        StandardizedQuote(
            origin_external_id="TIETE", destination_external_id="NOVO_RIO", modal="ROAD",
            provider_source="clickbus", carrier_name="Cometa", service_class=cls,
            departure_time=dep, arrival_time=dep + timedelta(hours=6), duration_minutes=360,
            base_price=price, route_metadata=dict(meta),
        )
        for cls, price in (("LEITO", 200.0), ("LEITO", 150.0), ("CONVENCIONAL", 120.0), ("LEITO", 170.0))
    ]

    enrich_quotes_batch_with_thermometer(quotes, db=object())

    assert sorted(calls) == ["CONVENCIONAL", "LEITO"]
    assert all(q.route_metadata["price_thermometer"]["source"] == "antt_monitriip" for q in quotes)
