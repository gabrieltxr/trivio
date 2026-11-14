"""Testes unitários para o avaliador de rotas (Prós, Contras, Contratempos e Diagnóstico de Conexões)."""

from datetime import datetime

from app.analyzers.route_evaluator import enrich_quote_with_evaluation, evaluate_route
from app.collectors.base import StandardizedQuote


# ==============================================================================
# 1. Testes de Viagem Rodoviária Noturna Leito (Pernoite Produtivo)
# ==============================================================================

def test_evaluate_road_leito_night_trip():
    """Valida identificação de pernoite produtivo, bagagem ANTT e contratempo da Dutra."""
    quote = StandardizedQuote(
        origin_external_id="sao-paulo-tiete-sp",
        destination_external_id="rio-de-janeiro-novo-rio-rj",
        modal="ROAD",
        provider_source="clickbus",
        carrier_name="Auto Viação 1001",
        service_class="LEITO_CAMA",
        departure_time=datetime(2026, 11, 20, 23, 0),
        arrival_time=datetime(2026, 11, 21, 6, 0),
        duration_minutes=420,
        base_price=240.0,
        taxes_price=22.0,
        baggage_fee_estimate=0.0,
        transfer_cost_estimate=22.0,  # Transfer central econômico
        route_metadata={
            "is_direct": True,
            "stops_count": 0,
            "amenities": ["wifi", "usb"],
        },
    )

    evaluation = evaluate_route(quote)

    pros_text = " ".join(evaluation.pros)
    assert "30kg" in pros_text or "ANTT" in pros_text
    assert "pernoite produtivo" in pros_text.lower()
    assert "fácil acesso central" in pros_text.lower()
    assert "Wi-Fi" in pros_text or "wi-fi" in pros_text.lower()

    setbacks_text = " ".join(evaluation.potential_setbacks)
    assert "Presidente Dutra" in setbacks_text or "BR-116" in setbacks_text


# ==============================================================================
# 2. Testes de Viagem Rodoviária com Madrugada e Paradas Excessivas
# ==============================================================================

def test_evaluate_road_madrugada_and_paradora():
    """Valida detecção de embarque de madrugada, linha paradora e desgaste convencional."""
    quote = StandardizedQuote(
        origin_external_id="curitiba-pr",
        destination_external_id="porto-alegre-rs",
        modal="ROAD",
        provider_source="clickbus",
        carrier_name="Viação Catarinense",
        service_class="CONVENCIONAL",
        departure_time=datetime(2026, 11, 20, 3, 30),  # Madrugada crítica
        arrival_time=datetime(2026, 11, 20, 14, 0),
        duration_minutes=630,  # 10.5 horas
        base_price=160.0,
        route_metadata={
            "is_direct": False,
            "stops_count": 5,
        },
    )

    evaluation = evaluate_route(quote)

    cons_text = " ".join(evaluation.cons)
    assert "madrugada" in cons_text.lower()
    assert "5 paradas" in cons_text
    assert "convencional" in cons_text.lower()


# ==============================================================================
# 3. Testes de Voo Direto sem Bagagem
# ==============================================================================

def test_evaluate_air_direct_no_baggage():
    """Valida prós de voo direto e contra de ausência de mala despachada de 23kg."""
    quote = StandardizedQuote(
        origin_external_id="CGH",
        destination_external_id="SDU",
        modal="AIR",
        provider_source="broker_a",
        carrier_name="LATAM Airlines",
        service_class="ECONOMY",
        departure_time=datetime(2026, 11, 20, 10, 0),
        arrival_time=datetime(2026, 11, 20, 11, 10),
        duration_minutes=70,
        base_price=350.0,
        taxes_price=45.0,
        baggage_fee_estimate=110.0,
        route_metadata={
            "is_direct": True,
            "stops_count": 0,
            "baggage_included": False,
        },
    )

    evaluation = evaluate_route(quote)

    assert any("direto" in p.lower() for p in evaluation.pros)
    assert any("sem mala despachada" in c.lower() or "110" in c for c in evaluation.cons)


# ==============================================================================
# 4. Testes de Conexão Apertada (Tight Connection Engine)
# ==============================================================================

def test_tight_connection_critical_in_major_hub():
    """Conexão de apenas 42 minutos em GRU deve disparar alerta de Contratempo Crítico (Setback)."""
    quote = StandardizedQuote(
        origin_external_id="POA",
        destination_external_id="SDU",
        modal="AIR",
        provider_source="broker_a",
        carrier_name="Gol Linhas Aéreas",
        service_class="ECONOMY",
        departure_time=datetime(2026, 11, 20, 6, 0),
        arrival_time=datetime(2026, 11, 20, 9, 30),
        duration_minutes=210,
        base_price=420.0,
        route_metadata={
            "is_direct": False,
            "stops_count": 1,
            "layover_details": [
                {
                    "airport": "GRU",
                    "duration_minutes": 42,  # Apertadíssimo em GRU!
                    "arrival_time": "2026-11-20T07:40:00",
                    "departure_time": "2026-11-20T08:22:00",
                }
            ],
        },
    )

    enriched = enrich_quote_with_evaluation(quote)
    meta = enriched.route_metadata
    diag = meta.get("connection_diagnostics")

    assert diag is not None
    assert diag["worst_severity"] == "CRITICAL"
    assert diag["tightest_layover_minutes"] == 42
    assert diag["is_tight"] is True
    assert diag["total_connection_penalty_points"] == 100

    # Deve estar nos contratempos potenciais
    setbacks_text = " ".join(meta["potential_setbacks"])
    assert "Conexão crítica de apenas 42 min em GRU" in setbacks_text
    assert "extravio de bagagem" in setbacks_text


def test_tight_connection_warning_in_bsb():
    """Conexão de 55 minutos em BSB deve entrar como Contra de Atenção (Warning)."""
    quote = StandardizedQuote(
        origin_external_id="POA",
        destination_external_id="SSA",
        modal="AIR",
        provider_source="broker_a",
        carrier_name="LATAM Airlines",
        service_class="ECONOMY",
        departure_time=datetime(2026, 11, 20, 7, 0),
        arrival_time=datetime(2026, 11, 20, 12, 0),
        duration_minutes=300,
        base_price=480.0,
        route_metadata={
            "is_direct": False,
            "stops_count": 1,
            "layover_details": [
                {"airport": "BSB", "duration_minutes": 55}
            ],
        },
    )

    enriched = enrich_quote_with_evaluation(quote)
    meta = enriched.route_metadata
    diag = meta.get("connection_diagnostics")

    assert diag["worst_severity"] == "WARNING"
    assert diag["total_connection_penalty_points"] == 50

    cons_text = " ".join(meta["cons"])
    assert "Conexão curta (55 min) em BSB" in cons_text


# ==============================================================================
# 5. Testes de Conexão Virtual Intermodal (Hacker Fare / Interlining)
# ==============================================================================

def test_evaluate_virtual_interlining_risks():
    """Valida alerta sobre ausência de proteção de bilhete único em conexões híbridas."""
    quote = StandardizedQuote(
        origin_external_id="pelotas-rs",
        destination_external_id="SDU",
        modal="AIR",
        provider_source="trivio_interliner",
        carrier_name="Intermodal Híbrido",
        service_class="MISTO",
        departure_time=datetime(2026, 11, 20, 7, 0),
        arrival_time=datetime(2026, 11, 20, 15, 0),
        duration_minutes=480,
        base_price=490.0,
        route_metadata={
            "is_virtual_interline": True,
            "stops_count": 1,
        },
    )

    evaluation = evaluate_route(quote)

    setbacks_text = " ".join(evaluation.potential_setbacks)
    assert "intermodal independente" in setbacks_text.lower()
    assert "sem garantia de reacomodação" in setbacks_text.lower()


# ==============================================================================
# 6. Testes de Sensibilidade Climática e Teto Operacional (CWB e SDU)
# ==============================================================================

def test_airport_weather_and_ceiling_setbacks():
    """Valida alerta de nevoeiro matinal em Curitiba (CWB) e teto estrito no Santos Dumont (SDU)."""
    # 1. Voo saindo de Curitiba às 06:30 da manhã (janela crítica de névoa)
    q_cwb_morning = StandardizedQuote(
        origin_external_id="CWB",
        destination_external_id="CGH",
        modal="AIR",
        provider_source="broker_a",
        carrier_name="LATAM",
        service_class="ECONOMY",
        departure_time=datetime(2026, 11, 20, 6, 30),
        arrival_time=datetime(2026, 11, 20, 7, 30),
        duration_minutes=60,
        base_price=290.0,
        route_metadata={"is_direct": True, "stops_count": 0},
    )
    eval_cwb = evaluate_route(q_cwb_morning)
    setbacks_cwb = " ".join(eval_cwb.potential_setbacks)
    assert "Curitiba (CWB)" in setbacks_cwb
    assert "nevoeiro matinal" in setbacks_cwb.lower()

    # 2. Voo saindo de Curitiba à tarde (14:00) -> NÃO deve ter alerta de nevoeiro
    q_cwb_afternoon = StandardizedQuote(
        origin_external_id="CWB",
        destination_external_id="CGH",
        modal="AIR",
        provider_source="broker_a",
        carrier_name="LATAM",
        service_class="ECONOMY",
        departure_time=datetime(2026, 11, 20, 14, 0),
        arrival_time=datetime(2026, 11, 20, 15, 0),
        duration_minutes=60,
        base_price=290.0,
        route_metadata={"is_direct": True, "stops_count": 0},
    )
    eval_cwb_pm = evaluate_route(q_cwb_afternoon)
    setbacks_cwb_pm = " ".join(eval_cwb_pm.potential_setbacks)
    assert "nevoeiro matinal" not in setbacks_cwb_pm.lower()

    # 3. Voo pousando no Santos Dumont (SDU)
    q_sdu = StandardizedQuote(
        origin_external_id="CGH",
        destination_external_id="SDU",
        modal="AIR",
        provider_source="broker_a",
        carrier_name="Gol",
        service_class="ECONOMY",
        departure_time=datetime(2026, 11, 20, 12, 0),
        arrival_time=datetime(2026, 11, 20, 13, 0),
        duration_minutes=60,
        base_price=310.0,
        route_metadata={"is_direct": True, "stops_count": 0},
    )
    eval_sdu = evaluate_route(q_sdu)
    setbacks_sdu = " ".join(eval_sdu.potential_setbacks)
    assert "Santos Dumont (SDU)" in setbacks_sdu
    assert "Galeão (GIG)" in setbacks_sdu


# ==============================================================================
# 7. Testes de Aeronaves Comerciais Turboélice (ATR 72 / Voepass / Azul)
# ==============================================================================

def test_commercial_turboprop_overhead_bin_and_connection_setback():
    """Valida alerta de bagageiro estreito e atraso na entrega da mala na pista para turboélice comercial."""
    # Voo comercial regular com ATR-72 (ex: rota POA -> PET com conexão em POA)
    q_atr = StandardizedQuote(
        origin_external_id="PET",
        destination_external_id="CGH",
        modal="AIR",
        provider_source="broker_a",
        carrier_name="Voepass / LATAM",
        service_class="ECONOMY",
        departure_time=datetime(2026, 11, 20, 10, 0),
        arrival_time=datetime(2026, 11, 20, 14, 0),
        duration_minutes=240,
        base_price=380.0,
        route_metadata={
            "aircraft": "ATR 72-600",
            "is_direct": False,
            "stops_count": 1,
            "layover_airports": ["POA"],
            "layover_details": [{"airport": "POA", "duration_minutes": 60}],
        },
    )

    evaluation = evaluate_route(q_atr)

    # 1. Deve conter Contra de bagageiro de teto reduzido
    cons_text = " ".join(evaluation.cons)
    assert "turboélice" in cons_text.lower()
    assert "bagagem de mão reduzido" in cons_text.lower()
    assert "escada do avião" in cons_text.lower()

    # 2. Deve conter Contratempo Potencial de atraso na devolução da mala na pista por ter conexão
    setbacks_text = " ".join(evaluation.potential_setbacks)
    assert "turboélice" in setbacks_text.lower()
    assert "10 a 15 min de espera" in setbacks_text.lower()


