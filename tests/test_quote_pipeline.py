"""Testes de Integração e Pipeline de Cotações (Resolução Canônica, Transfer e Persistência)."""

from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Hub, ModalType, ProviderMapping, Terminal, FareQuote
from app.collectors.base import StandardizedQuote
from app.services.quote_pipeline import QuotePipeline, process_and_persist_quotes
from app.analyzers.route_evaluator import evaluate_route


@pytest.fixture
def db_session():
    """Cria banco SQLite em memória com estrutura e mappings para os testes."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed Hub SP
    hub_sp = Hub(id=1, ibge_code="3550308", name="São Paulo", state="SP", latitude=-23.55, longitude=-46.63)
    # Seed Hub RJ
    hub_rj = Hub(id=2, ibge_code="3304557", name="Rio de Janeiro", state="RJ", latitude=-22.90, longitude=-43.17)
    session.add_all([hub_sp, hub_rj])
    session.flush()

    # Seed Terminais
    term_tiete = Terminal(
        id=1, hub_id=hub_sp.id, name="Terminal Rodoviário do Tietê",
        modal=ModalType.ROAD, official_code="TIETE",
        latitude=-23.516, longitude=-46.625,
        avg_city_center_distance_km=5.0, estimated_transfer_cost_brl=22.0, estimated_transfer_minutes=15,
    )
    term_novorio = Terminal(
        id=2, hub_id=hub_rj.id, name="Rodoviária Novo Rio",
        modal=ModalType.ROAD, official_code="NOVO_RIO",
        latitude=-22.898, longitude=-43.209,
        avg_city_center_distance_km=4.0, estimated_transfer_cost_brl=25.0, estimated_transfer_minutes=20,
    )
    session.add_all([term_tiete, term_novorio])
    session.flush()

    # Seed Mappings de diferentes brokers
    mappings = [
        # ClickBus
        ProviderMapping(terminal_id=term_tiete.id, provider_name="clickbus", external_id="sao-paulo-tiete-sp"),
        ProviderMapping(terminal_id=term_novorio.id, provider_name="clickbus", external_id="rio-de-janeiro-novo-rio-rj"),
        # DeÔnibus (slugs proprietários diferentes!)
        ProviderMapping(terminal_id=term_tiete.id, provider_name="deonibus", external_id="sao-paulo-tiete"),
        ProviderMapping(terminal_id=term_novorio.id, provider_name="deonibus", external_id="rio-de-janeiro-novo-rio"),
    ]
    session.add_all(mappings)
    session.commit()

    yield session
    session.close()


def test_cross_broker_canonical_deduplication(db_session):
    """Valida que cotações com slugs proprietários diferentes geram a mesma assinatura canônica."""
    dep = datetime(2026, 11, 25, 8, 0)
    arr = datetime(2026, 11, 25, 14, 0)

    # 1. Cotação vinda da ClickBus
    quote_clickbus = StandardizedQuote(
        origin_external_id="sao-paulo-tiete-sp",
        destination_external_id="rio-de-janeiro-novo-rio-rj",
        modal="ROAD",
        provider_source="clickbus",
        carrier_name="Viação Guanabara",
        service_class="SEMI_LEITO",
        departure_time=dep,
        arrival_time=arr,
        duration_minutes=360,
        base_price=135.0,
        taxes_price=18.0,
    )

    # 2. Cotação vinda da DeÔnibus (mesma viagem, slugs proprietários diferentes!)
    quote_deonibus = StandardizedQuote(
        origin_external_id="sao-paulo-tiete",
        destination_external_id="rio-de-janeiro-novo-rio",
        modal="ROAD",
        provider_source="deonibus",
        carrier_name="Viação Guanabara",
        service_class="SEMI_LEITO",
        departure_time=dep,
        arrival_time=arr,
        duration_minutes=360,
        base_price=118.0,
        taxes_price=14.0,
    )

    # Antes do enriquecimento, as assinaturas diferiam por causa do slug proprietário
    assert quote_clickbus.dedup_signature != quote_deonibus.dedup_signature

    # Executa o enriquecimento canônico no pipeline
    q1_enriched, t1_orig, t1_dest = QuotePipeline.enrich_quote(quote_clickbus, db_session)
    q2_enriched, t2_orig, t2_dest = QuotePipeline.enrich_quote(quote_deonibus, db_session)

    # Agora ambos apontam para os mesmos códigos canônicos ("TIETE" e "NOVO_RIO")
    assert q1_enriched.canonical_origin_id == "TIETE"
    assert q2_enriched.canonical_origin_id == "TIETE"
    assert q1_enriched.canonical_destination_id == "NOVO_RIO"
    assert q2_enriched.canonical_destination_id == "NOVO_RIO"

    # CRUCIAL: As assinaturas SHA-256 AGORA SÃO IDÊNTICAS!
    assert q1_enriched.dedup_signature == q2_enriched.dedup_signature

    # Injeção de transfer: 22 (origem) + 25 (destino) = 47.0
    assert q1_enriched.transfer_cost_estimate == 47.0
    assert q2_enriched.transfer_cost_estimate == 47.0

    # Duração porta a porta: 360 + 15 + 20 + 20 (buffer rodoviário) = 415 min
    assert q1_enriched.door_to_door_duration_minutes == 415


def test_quote_persistence_and_code_share_upsert(db_session):
    """Valida persistência e agrupamento code-share com atualização da melhor oferta no banco."""
    dep = datetime(2026, 11, 25, 8, 0)
    arr = datetime(2026, 11, 25, 14, 0)

    # ClickBus a R$ 135
    q_cb = StandardizedQuote(
        origin_external_id="sao-paulo-tiete-sp",
        destination_external_id="rio-de-janeiro-novo-rio-rj",
        modal="ROAD",
        provider_source="clickbus",
        carrier_name="Viação Guanabara",
        service_class="SEMI_LEITO",
        departure_time=dep,
        arrival_time=arr,
        duration_minutes=360,
        base_price=135.0,
    )

    # DeÔnibus a R$ 118 (Mais barata!)
    q_do = StandardizedQuote(
        origin_external_id="sao-paulo-tiete",
        destination_external_id="rio-de-janeiro-novo-rio",
        modal="ROAD",
        provider_source="deonibus",
        carrier_name="Viação Guanabara",
        service_class="SEMI_LEITO",
        departure_time=dep,
        arrival_time=arr,
        duration_minutes=360,
        base_price=118.0,
    )

    saved_quotes = process_and_persist_quotes([q_cb, q_do], db_session)

    # Apenas 1 registro único deve ser gravado para a mesma viagem física
    assert len(saved_quotes) == 1
    winner = saved_quotes[0]

    assert winner.provider_source == "deonibus"
    assert winner.base_price == 118.0
    assert winner.origin_terminal_id == 1
    assert winner.destination_terminal_id == 2
    assert winner.expires_at is not None

    meta = winner.route_metadata
    assert meta["is_code_share"] is True
    assert meta["sellers_count"] == 2
    assert meta["cheapest_provider"] == "deonibus"


def test_timezone_aware_madrugada_detection():
    """Valida conversão correta de datetimes UTC para fuso de Brasília nos alertas."""
    # 05:30 UTC equivale a 02:30 BRT (Janela crítica da madrugada!)
    dep_utc = datetime(2026, 11, 20, 5, 30, tzinfo=timezone.utc)
    arr_utc = datetime(2026, 11, 20, 11, 30, tzinfo=timezone.utc)

    quote = StandardizedQuote(
        origin_external_id="POA", destination_external_id="SDU", modal="AIR",
        provider_source="broker_a", carrier_name="Gol", service_class="ECONOMY",
        departure_time=dep_utc, arrival_time=arr_utc, duration_minutes=360, base_price=300.0,
    )

    evaluation = evaluate_route(quote)
    cons_text = " ".join(evaluation.cons)

    # O motor deve detectar que às 05:30 UTC é 02:30 no Brasil e alertar madrugada
    assert "madrugada" in cons_text.lower()
    assert "02:30" in cons_text


def test_simulated_and_interline_quotes_are_never_persisted(db_session):
    """Estimativas simuladas e rotas sintetizadas pelo interliner não podem ir para o histórico."""
    dep = datetime(2026, 11, 20, 8, 30)
    common = dict(
        origin_external_id="TIETE",
        destination_external_id="NOVO_RIO",
        modal="ROAD",
        service_class="SEMI_LEITO",
        departure_time=dep,
        arrival_time=dep.replace(hour=14),
        duration_minutes=330,
        base_price=150.0,
    )
    simulated = StandardizedQuote(
        provider_source="antt_projection",
        carrier_name="Ônibus Semi-Leito (estimativa)",
        route_metadata={"is_simulated": True},
        **common,
    )
    flagged = StandardizedQuote(
        provider_source="clickbus",
        carrier_name="Viação Fantasma",
        route_metadata={"is_simulated": True},
        **common,
    )
    interline = StandardizedQuote(
        provider_source="trivio_interliner",
        carrier_name="Ônibus + Voo",
        route_metadata={"is_virtual_interline": True},
        **common,
    )
    real = StandardizedQuote(
        provider_source="clickbus",
        carrier_name="Viação Real",
        route_metadata={},
        **common,
    )

    saved = process_and_persist_quotes([simulated, flagged, interline, real], db_session)

    assert [fq.carrier_name for fq in saved] == ["Viação Real"]
    assert db_session.query(FareQuote).count() == 1


def test_enrich_quote_exposes_city_for_official_statistics(db_session):
    """O pipeline precisa expor o município: a série da ANTT é por cidade, não por terminal."""
    dep = datetime(2026, 11, 20, 8, 30)
    quote = StandardizedQuote(
        origin_external_id="sao-paulo-tiete-sp",
        destination_external_id="rio-de-janeiro-novo-rio-rj",
        modal="ROAD",
        provider_source="clickbus",
        carrier_name="Viação Cometa",
        service_class="SEMI_LEITO",
        departure_time=dep,
        arrival_time=dep.replace(hour=14),
        duration_minutes=330,
        base_price=150.0,
    )

    enriched, origin_term, dest_term = QuotePipeline.enrich_quote(quote, db_session)

    assert origin_term is not None and dest_term is not None
    meta = enriched.route_metadata
    assert meta["origin_city"] == "São Paulo"
    assert meta["origin_state"] == "SP"
    assert meta["destination_city"] == "Rio de Janeiro"
    assert meta["destination_state"] == "RJ"
    # E a decomposição de tempo usada pelo interliner
    assert meta["origin_transfer_minutes"] == 15
    assert meta["destination_transfer_minutes"] == 20
    assert meta["checkin_buffer_minutes"] == 20


def test_terminal_cache_queries_each_provider_id_once(db_session):
    """Num lote, todas as cotações dividem os mesmos terminais: o banco é consultado uma vez por par."""
    from sqlalchemy import event
    from app.services.quote_pipeline import find_terminal_by_external_id

    statements = []

    def count(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    cache = {}
    try:
        found = [
            find_terminal_by_external_id(db_session, provider, "sao-paulo-tiete-sp ", cache=cache)
            for provider in ("clickbus", "ClickBus ", "clickbus")
        ]
        missing = [find_terminal_by_external_id(db_session, "clickbus", "nao-existe", cache=cache) for _ in range(3)]
    finally:
        event.remove(engine, "before_cursor_execute", count)

    assert all(t is not None and t.id == found[0].id for t in found)
    assert missing == [None, None, None]
    # 1 consulta para o terminal achado + 2 (mapping e código oficial) para o que não existe
    assert len(statements) == 3


@pytest.mark.parametrize("modal, setting", [
    ("AIR", "AIRPORT_DOMESTIC_CHECKIN_BUFFER_MINUTES"),
    ("ROAD", "BUS_TERMINAL_CHECKIN_BUFFER_MINUTES"),
])
def test_enrich_quote_reads_checkin_buffer_from_settings(db_session, monkeypatch, modal, setting):
    """A antecedência de embarque vem do app/config.py, não de um número fixo no pipeline."""
    from app.config import settings

    monkeypatch.setattr(settings, setting, 33)
    dep = datetime(2026, 11, 20, 8, 30)
    ids = ("CGH", "SDU") if modal == "AIR" else ("sao-paulo-tiete-sp", "rio-de-janeiro-novo-rio-rj")
    quote = StandardizedQuote(
        origin_external_id=ids[0],
        destination_external_id=ids[1],
        modal=modal,
        provider_source="ignav" if modal == "AIR" else "clickbus",
        carrier_name="Teste",
        service_class="ECONOMY" if modal == "AIR" else "SEMI_LEITO",
        departure_time=dep,
        arrival_time=dep.replace(hour=10),
        duration_minutes=90,
        base_price=150.0,
    )

    enriched, _, _ = QuotePipeline.enrich_quote(quote, db_session, evaluate=False)

    meta = enriched.route_metadata
    assert meta["checkin_buffer_minutes"] == 33
    assert enriched.door_to_door_duration_minutes == (
        90 + meta["origin_transfer_minutes"] + meta["destination_transfer_minutes"] + 33
    )
