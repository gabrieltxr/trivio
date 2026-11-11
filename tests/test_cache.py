"""Testes unitários para o Smart Cache TTL Policy e Deduplicação Code-Share."""

from datetime import date, datetime, timedelta, timezone

from app.cache.ttl_policy import (
    TTLTier,
    TTL_IMMINENT_SECONDS,
    TTL_SHORT_SECONDS,
    TTL_MEDIUM_SECONDS,
    TTL_LONG_SECONDS,
    calculate_ttl,
    calculate_expiration_datetime,
    get_ttl_tier,
)
from app.cache.deduplicator import deduplicate_quotes
from app.collectors.base import (
    StandardizedQuote,
    generate_dedup_signature,
)
from app.models import FareQuote, ModalType


# ==============================================================================
# 1. Testes de TTL Policy
# ==============================================================================

def test_ttl_tiers_by_days_ahead():
    """Valida as 4 faixas de TTL dinâmico com base na distância da viagem."""
    ref_date = date(2026, 11, 1)

    # 1. Viagem em <= 2 dias -> 30 minutos (1.800s)
    assert calculate_ttl(date(2026, 11, 1), reference_date=ref_date) == TTL_IMMINENT_SECONDS
    assert calculate_ttl(date(2026, 11, 2), reference_date=ref_date) == TTL_IMMINENT_SECONDS
    assert calculate_ttl(date(2026, 11, 3), reference_date=ref_date) == TTL_IMMINENT_SECONDS
    assert get_ttl_tier(date(2026, 11, 3), reference_date=ref_date) == TTLTier.IMMINENT

    # 2. Viagem entre 3 e 7 dias -> 2 horas (7.200s)
    assert calculate_ttl(date(2026, 11, 4), reference_date=ref_date) == TTL_SHORT_SECONDS
    assert calculate_ttl(date(2026, 11, 8), reference_date=ref_date) == TTL_SHORT_SECONDS
    assert get_ttl_tier(date(2026, 11, 8), reference_date=ref_date) == TTLTier.SHORT

    # 3. Viagem entre 8 e 21 dias -> 6 horas (21.600s)
    assert calculate_ttl(date(2026, 11, 9), reference_date=ref_date) == TTL_MEDIUM_SECONDS
    assert calculate_ttl(date(2026, 11, 22), reference_date=ref_date) == TTL_MEDIUM_SECONDS
    assert get_ttl_tier(date(2026, 11, 22), reference_date=ref_date) == TTLTier.MEDIUM

    # 4. Viagem em > 21 dias -> 24 horas (86.400s)
    assert calculate_ttl(date(2026, 11, 23), reference_date=ref_date) == TTL_LONG_SECONDS
    assert calculate_ttl(date(2026, 12, 15), reference_date=ref_date) == TTL_LONG_SECONDS
    assert get_ttl_tier(date(2026, 12, 15), reference_date=ref_date) == TTLTier.LONG


def test_ttl_for_past_date():
    """Viagem com data no passado deve retornar TTL 0 e tier EXPIRED."""
    ref_date = date(2026, 11, 10)
    past_date = date(2026, 11, 9)

    assert calculate_ttl(past_date, reference_date=ref_date) == 0
    assert get_ttl_tier(past_date, reference_date=ref_date) == TTLTier.EXPIRED


def test_ttl_input_formats():
    """Valida aceitação de strings ISO, datetimes e dates."""
    ref_date = date(2026, 11, 1)

    # String YYYY-MM-DD
    assert calculate_ttl("2026-11-02", reference_date=ref_date) == TTL_IMMINENT_SECONDS
    # String com horário ISO
    assert calculate_ttl("2026-11-05T08:30:00", reference_date=ref_date) == TTL_SHORT_SECONDS
    # Datetime
    dt = datetime(2026, 11, 15, 10, 0, 0)
    assert calculate_ttl(dt, reference_date=ref_date) == TTL_MEDIUM_SECONDS


def test_calculate_expiration_datetime():
    """Valida cálculo de datetime exato de expiração."""
    ref_dt = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
    travel_date = date(2026, 11, 2)  # 1 dia à frente -> 1800s (30m)

    expires_at = calculate_expiration_datetime(travel_date, reference_datetime=ref_dt)
    expected = ref_dt + timedelta(seconds=TTL_IMMINENT_SECONDS)

    assert expires_at == expected


# ==============================================================================
# 2. Testes de Assinatura SHA-256 e Deduplicação
# ==============================================================================

def test_dedup_signature_generation():
    """Valida que viagens idênticas geram a mesma assinatura hash de 32 chars."""
    dep_time = datetime(2026, 11, 15, 8, 30)

    sig1 = generate_dedup_signature(
        modal="ROAD",
        carrier_name="Viação Guanabara",
        origin_id="sao-paulo-tiete-sp",
        destination_id="rio-de-janeiro-novo-rio-rj",
        departure_time=dep_time,
        service_class="SEMI_LEITO",
    )

    sig2 = generate_dedup_signature(
        modal="road",
        carrier_name="  viação GUANABARA ",
        origin_id="sao-paulo-tiete-sp",
        destination_id="rio-de-janeiro-novo-rio-rj",
        departure_time=dep_time,
        service_class="semi_leito",
    )

    assert len(sig1) == 32
    assert sig1 == sig2

    # Horário diferente deve gerar assinatura diferente
    sig_diff_time = generate_dedup_signature(
        modal="ROAD",
        carrier_name="Viação Guanabara",
        origin_id="sao-paulo-tiete-sp",
        destination_id="rio-de-janeiro-novo-rio-rj",
        departure_time=datetime(2026, 11, 15, 8, 35),
        service_class="SEMI_LEITO",
    )
    assert sig1 != sig_diff_time


def test_deduplicator_chooses_cheapest_seller():
    """Cenário Code-Share: Mesma viagem de ônibus vendida por 3 provedores com preços diferentes."""
    dep = datetime(2026, 11, 20, 8, 0)
    arr = datetime(2026, 11, 20, 14, 0)

    # 1. ClickBus: R$ 139.90 (120 base + 19.90 taxa)
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
        base_price=120.0,
        taxes_price=19.90,
    )

    # 2. DeÔnibus: R$ 128.50 (115 base + 13.50 taxa)
    quote_deonibus = StandardizedQuote(
        origin_external_id="sao-paulo-tiete-sp",
        destination_external_id="rio-de-janeiro-novo-rio-rj",
        modal="ROAD",
        provider_source="deonibus",
        carrier_name="Viação Guanabara",
        service_class="SEMI_LEITO",
        departure_time=dep,
        arrival_time=arr,
        duration_minutes=360,
        base_price=115.0,
        taxes_price=13.50,
    )

    # 3. Buser: R$ 119.00 (119 base + 0 taxa) -> MAIS BARATO!
    quote_buser = StandardizedQuote(
        origin_external_id="sao-paulo-tiete-sp",
        destination_external_id="rio-de-janeiro-novo-rio-rj",
        modal="ROAD",
        provider_source="buser",
        carrier_name="Viação Guanabara",
        service_class="SEMI_LEITO",
        departure_time=dep,
        arrival_time=arr,
        duration_minutes=360,
        base_price=119.0,
        taxes_price=0.0,
    )

    # 4. Outra viagem distinta (Cometa às 09:30)
    quote_cometa = StandardizedQuote(
        origin_external_id="sao-paulo-tiete-sp",
        destination_external_id="rio-de-janeiro-novo-rio-rj",
        modal="ROAD",
        provider_source="clickbus",
        carrier_name="Viação Cometa",
        service_class="EXECUTIVO",
        departure_time=datetime(2026, 11, 20, 9, 30),
        arrival_time=datetime(2026, 11, 20, 15, 30),
        duration_minutes=360,
        base_price=150.0,
        taxes_price=15.0,
    )

    all_quotes = [quote_clickbus, quote_cometa, quote_deonibus, quote_buser]
    deduped = deduplicate_quotes(all_quotes)

    # Devem restar apenas 2 cotações únicas
    assert len(deduped) == 2

    # Encontra a cotação da Guanabara
    guanabara_winner = next(q for q in deduped if q.carrier_name == "Viação Guanabara")

    # A vencedora DEVE ser a da Buser (R$ 119.00)
    assert guanabara_winner.provider_source == "buser"
    assert guanabara_winner.total_effective_price == 119.00

    # Metadados de code-share
    meta = guanabara_winner.route_metadata
    assert meta["is_code_share"] is True
    assert meta["sellers_count"] == 3
    assert meta["cheapest_provider"] == "buser"
    assert meta["cheapest_price"] == 119.00
    assert meta["highest_price"] == 139.90
    assert meta["savings_brl"] == round(139.90 - 119.00, 2)
    assert len(meta["available_sellers"]) == 3


def test_deduplicator_with_fare_quote_orm():
    """Valida compatibilidade com instâncias do modelo ORM FareQuote."""
    dep = datetime(2026, 11, 25, 14, 0)
    arr = datetime(2026, 11, 25, 15, 10)

    # Voo Gol vendido via Amadeus (R$ 380) vs. Kiwi (R$ 420)
    sig = generate_dedup_signature("AIR", "GOL", "1", "2", dep, "ECONOMY")

    fq_amadeus = FareQuote(
        dedup_signature=sig,
        origin_terminal_id=1,
        destination_terminal_id=2,
        modal=ModalType.AIR,
        provider_source="amadeus",
        carrier_name="GOL",
        service_class="ECONOMY",
        departure_time=dep,
        arrival_time=arr,
        duration_minutes=70,
        door_to_door_duration_minutes=190,
        base_price=350.0,
        taxes_price=30.0,
        total_effective_price=380.0,
        expires_at=datetime(2026, 11, 25, 20, 0),
        route_metadata={},
    )

    fq_kiwi = FareQuote(
        dedup_signature=sig,
        origin_terminal_id=1,
        destination_terminal_id=2,
        modal=ModalType.AIR,
        provider_source="kiwi",
        carrier_name="GOL",
        service_class="ECONOMY",
        departure_time=dep,
        arrival_time=arr,
        duration_minutes=70,
        door_to_door_duration_minutes=190,
        base_price=380.0,
        taxes_price=40.0,
        total_effective_price=420.0,
        expires_at=datetime(2026, 11, 25, 20, 0),
        route_metadata={},
    )

    deduped = deduplicate_quotes([fq_kiwi, fq_amadeus])
    assert len(deduped) == 1
    assert deduped[0].provider_source == "amadeus"
    assert deduped[0].total_effective_price == 380.0
    assert deduped[0].route_metadata["is_code_share"] is True
    assert deduped[0].route_metadata["savings_brl"] == 40.0
