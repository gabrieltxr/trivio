"""Unit tests for Trivio Scorer (Agony Index) and Hacker Fares Combinator."""

from datetime import datetime

from app.analyzers.trivio_scorer import (
    calculate_score_breakdown,
    calculate_trivio_score,
    determine_comfort_factor,
    generate_hacker_fares,
    score_and_rank_quotes,
)


class DummyQuote:
    """Helper mock object for quote testing."""

    def __init__(
        self,
        modal: str,
        service_class: str,
        dep_time: datetime,
        arr_time: datetime,
        total_effective_price: float,
        door_to_door_duration_minutes: int,
        carrier_name: str = "Test Carrier",
        route_metadata: dict = None,
    ):
        self.modal = modal
        self.service_class = service_class
        self.departure_time = dep_time
        self.arrival_time = arr_time
        self.total_effective_price = total_effective_price
        self.door_to_door_duration_minutes = door_to_door_duration_minutes
        self.duration_minutes = door_to_door_duration_minutes
        self.carrier_name = carrier_name
        self.route_metadata = route_metadata or {}
        self.trivio_score = None

    def to_dict(self):
        return {
            "modal": self.modal,
            "service_class": self.service_class,
            "carrier_name": self.carrier_name,
            "total_effective_price": self.total_effective_price,
            "door_to_door_duration_minutes": self.door_to_door_duration_minutes,
            "trivio_score": self.trivio_score,
            "route_metadata": self.route_metadata,
        }


def test_determine_comfort_factors():
    """Valida multiplicadores de desgaste físico e produtividade (F_conforto)."""
    # 1. Leito Noturno: Sai às 23:00, chega às 07:00 -> Dorme a noite toda (0.55)
    dep_night = datetime(2026, 11, 20, 23, 0)
    arr_morning = datetime(2026, 11, 21, 7, 0)
    assert determine_comfort_factor("ROAD", "LEITO_CAMA", dep_night, arr_morning, True, 480) == 0.55

    # 2. Leito Diurno: Sai às 09:00, chega às 16:00 -> Confortável mas horas ativas perdidas (0.85)
    dep_day = datetime(2026, 11, 20, 9, 0)
    arr_day = datetime(2026, 11, 20, 16, 0)
    assert determine_comfort_factor("ROAD", "LEITO", dep_day, arr_day, True, 420) == 0.85

    # 3. Semi-Leito Curto (<= 360 min) -> Referência Neutra (1.00)
    assert determine_comfort_factor("ROAD", "SEMI_LEITO", dep_day, arr_day, True, 300) == 1.00

    # 4. Semi-Leito Diurno Longo (> 360 min) -> Levemente cansativo (1.15)
    assert determine_comfort_factor("ROAD", "SEMI_LEITO", dep_day, arr_day, True, 480) == 1.15

    # 5. Convencional -> Desgastante (1.35)
    assert determine_comfort_factor("ROAD", "CONVENCIONAL", dep_day, arr_day, True, 360) == 1.35

    # 6. Aéreo Executiva (Business) -> Confortável (0.70)
    assert determine_comfort_factor("AIR", "BUSINESS", dep_day, arr_day, True, 90) == 0.70

    # 7. Aéreo Econômica Direto -> Referência Neutra (1.00)
    assert determine_comfort_factor("AIR", "ECONOMY", dep_day, arr_day, True, 90) == 1.00

    # 8. Aéreo Econômica com Conexão -> Desgaste de troca de portão (1.15)
    assert determine_comfort_factor("AIR", "ECONOMY", dep_day, arr_day, False, 240) == 1.15


def test_calculate_score_breakdown_formula():
    """Valida a precisão da fórmula Trivio Score e a decomposição de atritos."""
    # Voo Direto GRU -> SDU: R$ 450 total, 180 min porta a porta, V_hora = R$ 50/h, sem atritos
    q = DummyQuote(
        modal="AIR",
        service_class="ECONOMY",
        dep_time=datetime(2026, 11, 20, 14, 0),
        arr_time=datetime(2026, 11, 20, 17, 0),
        total_effective_price=450.0,
        door_to_door_duration_minutes=180,
        route_metadata={"is_direct": True, "stops_count": 0},
    )

    breakdown = calculate_score_breakdown(q, hourly_time_value=50.0)
    # Custo de tempo = (180 / 60) * 50 * 1.0 = 150.0
    # Score = 450 + 150 = 600.0
    assert breakdown.effective_price == 450.0
    assert breakdown.hourly_time_value == 50.0
    assert breakdown.comfort_factor == 1.00
    assert breakdown.time_cost == 150.0
    assert breakdown.total_penalties == 0.0
    assert breakdown.trivio_score == 600.0

    score_num = calculate_trivio_score(q, hourly_time_value=50.0)
    assert score_num == 600.0


def test_penalties_accumulation():
    """Valida acúmulo de penalidades: madrugada, paradas, conexão apertada e troca de aeroporto."""
    # Voo com:
    # - Embarque na madrugada às 03:30 (+60)
    # - Conexão não-direta (+40)
    # - Troca de aeroporto CGH -> GRU (+80)
    # - Penalidade de conexão apertada de 100 pontos nos metadados (+100)
    q_air = DummyQuote(
        modal="AIR",
        service_class="ECONOMY",
        dep_time=datetime(2026, 11, 20, 3, 30),
        arr_time=datetime(2026, 11, 20, 8, 30),
        total_effective_price=300.0,
        door_to_door_duration_minutes=300,
        route_metadata={
            "is_direct": False,
            "stops_count": 1,
            "layover_airports": ["CGH", "GRU"],
            "connection_diagnostics": {"total_connection_penalty_points": 100.0},
        },
    )

    breakdown = calculate_score_breakdown(q_air, hourly_time_value=50.0)
    assert breakdown.madrugada_penalty == 60.0
    assert breakdown.connection_penalty == 40.0
    assert breakdown.airport_swap_penalty == 80.0
    assert breakdown.tight_connection_penalty == 100.0
    assert breakdown.total_penalties == 280.0

    # Ônibus com 5 paradas intermediárias (3 paradas acima de 2 = 3 * 10 = +30)
    q_bus = DummyQuote(
        modal="ROAD",
        service_class="CONVENCIONAL",
        dep_time=datetime(2026, 11, 20, 10, 0),
        arr_time=datetime(2026, 11, 20, 18, 0),
        total_effective_price=120.0,
        door_to_door_duration_minutes=480,
        route_metadata={"is_direct": False, "stops_count": 5},
    )
    breakdown_bus = calculate_score_breakdown(q_bus, hourly_time_value=50.0)
    assert breakdown_bus.stops_penalty == 30.0
    assert breakdown_bus.connection_penalty == 200.0  # 40 * 5


def test_night_leito_vs_day_flight_ranking():
    """Valida como o Leito Noturno pode vencer voo com conexão devido ao conforto e eliminação de diária."""
    # Voo com conexão desgastante de tarde: R$ 600, 300 min porta a porta, f_comfort 1.15
    flight_quote = DummyQuote(
        modal="AIR",
        service_class="ECONOMY",
        dep_time=datetime(2026, 11, 20, 14, 0),
        arr_time=datetime(2026, 11, 20, 19, 0),
        total_effective_price=600.0,
        door_to_door_duration_minutes=300,
        carrier_name="GOL",
        route_metadata={"is_direct": False, "stops_count": 1},
    )

    # Ônibus Leito Cama Noturno: R$ 220, 480 min (8h), f_comfort 0.55, sem paradas extras
    bus_quote = DummyQuote(
        modal="ROAD",
        service_class="LEITO_CAMA",
        dep_time=datetime(2026, 11, 20, 23, 0),
        arr_time=datetime(2026, 11, 21, 7, 0),
        total_effective_price=220.0,
        door_to_door_duration_minutes=480,
        carrier_name="Cometa",
        route_metadata={"is_direct": True, "stops_count": 0},
    )

    ranked = score_and_rank_quotes([flight_quote, bus_quote], hourly_time_value=50.0)
    # Bus Score = 220 + (8 * 50 * 0.55 = 220) + 0 = 440
    # Flight Score = 600 + (5 * 50 * 1.15 = 287.5) + 40 (conexão) = 927.5
    assert ranked[0].carrier_name == "Cometa"
    assert ranked[1].carrier_name == "GOL"
    assert ranked[0].trivio_score < ranked[1].trivio_score
    assert "score_breakdown" in ranked[0].route_metadata


def test_hacker_fares_generation_hybrid():
    """Valida geração de Hacker Fares combinando Aéreo na Ida e Ônibus Leito na Volta."""
    # Outbound: Sexta-feira
    # - Voo Rápido Sexta à tarde: R$ 380, 150 min d2d
    air_out = DummyQuote(
        modal="AIR",
        service_class="ECONOMY",
        dep_time=datetime(2026, 11, 20, 17, 0),
        arr_time=datetime(2026, 11, 20, 19, 30),
        total_effective_price=380.0,
        door_to_door_duration_minutes=150,
        carrier_name="LATAM",
        route_metadata={"is_direct": True},
    )
    # - Ônibus Sexta à tarde: R$ 180, 420 min d2d
    bus_out = DummyQuote(
        modal="ROAD",
        service_class="SEMI_LEITO",
        dep_time=datetime(2026, 11, 20, 14, 0),
        arr_time=datetime(2026, 11, 20, 21, 0),
        total_effective_price=180.0,
        door_to_door_duration_minutes=420,
        carrier_name="Cometa",
        route_metadata={"is_direct": True},
    )

    # Inbound: Domingo
    # - Voo de Domingo à noite inflacionado: R$ 900, 150 min d2d
    air_in = DummyQuote(
        modal="AIR",
        service_class="ECONOMY",
        dep_time=datetime(2026, 11, 22, 19, 0),
        arr_time=datetime(2026, 11, 22, 21, 30),
        total_effective_price=900.0,
        door_to_door_duration_minutes=150,
        carrier_name="LATAM",
        route_metadata={"is_direct": True},
    )
    # - Ônibus Leito Domingo à noite: R$ 250, 480 min d2d
    bus_in = DummyQuote(
        modal="ROAD",
        service_class="LEITO_CAMA",
        dep_time=datetime(2026, 11, 22, 23, 0),
        arr_time=datetime(2026, 11, 23, 7, 0),
        total_effective_price=250.0,
        door_to_door_duration_minutes=480,
        carrier_name="1001",
        route_metadata={"is_direct": True},
    )

    # Pure air cost = 380 + 900 = 1280.0
    # Hybrid cost (Air out + Bus in) = 380 + 250 = 630.0 -> Economia de R$ 650.0
    hacks = generate_hacker_fares(
        outbound_quotes=[air_out, bus_out],
        inbound_quotes=[air_in, bus_in],
        min_stay_hours=12.0,
        hourly_time_value=50.0,
    )

    assert len(hacks) > 0
    # Procura a combinação híbrida (Air Out + Bus In)
    hybrid = next((h for h in hacks if h.strategy_type == "HYBRID_MODAL" and h.outbound.modal == "AIR"), None)
    assert hybrid is not None
    assert hybrid.combined_effective_price == 630.0
    assert hybrid.savings_vs_single_modal_brl == 650.0
    assert "Hacker Fare" in hybrid.hack_title
    assert "LATAM" in hybrid.hack_description
    assert "1001" in hybrid.hack_description

    dict_repr = hybrid.to_dict()
    assert dict_repr["strategy_type"] == "HYBRID_MODAL"
    assert dict_repr["combined_effective_price"] == 630.0


def test_hacker_fares_split_carrier():
    """Valida economia ao comprar trechos em companhias aéreas diferentes."""
    air_out_latam = DummyQuote(
        modal="AIR",
        service_class="ECONOMY",
        dep_time=datetime(2026, 11, 20, 8, 0),
        arr_time=datetime(2026, 11, 20, 10, 0),
        total_effective_price=250.0,
        door_to_door_duration_minutes=180,
        carrier_name="LATAM",
    )
    air_out_gol = DummyQuote(
        modal="AIR",
        service_class="ECONOMY",
        dep_time=datetime(2026, 11, 20, 9, 0),
        arr_time=datetime(2026, 11, 20, 11, 0),
        total_effective_price=400.0,
        door_to_door_duration_minutes=180,
        carrier_name="GOL",
    )

    air_in_latam = DummyQuote(
        modal="AIR",
        service_class="ECONOMY",
        dep_time=datetime(2026, 11, 22, 18, 0),
        arr_time=datetime(2026, 11, 22, 20, 0),
        total_effective_price=450.0,
        door_to_door_duration_minutes=180,
        carrier_name="LATAM",
    )
    air_in_gol = DummyQuote(
        modal="AIR",
        service_class="ECONOMY",
        dep_time=datetime(2026, 11, 22, 19, 0),
        arr_time=datetime(2026, 11, 22, 21, 0),
        total_effective_price=280.0,
        door_to_door_duration_minutes=180,
        carrier_name="GOL",
    )

    # Pure LATAM = 250 + 450 = 700.0
    # Pure GOL = 400 + 280 = 680.0 -> cheapest same-carrier air = 680.0
    # Split Carrier (LATAM out 250 + GOL in 280) = 530.0 -> Economia de R$ 150.0
    hacks = generate_hacker_fares(
        outbound_quotes=[air_out_latam, air_out_gol],
        inbound_quotes=[air_in_latam, air_in_gol],
        min_stay_hours=24.0,
    )

    best_split = next((h for h in hacks if h.strategy_type == "SPLIT_CARRIER"), None)
    assert best_split is not None
    assert best_split.combined_effective_price == 530.0
    assert best_split.savings_vs_single_modal_brl == 150.0
    assert "LATAM" in best_split.hack_title
    assert "GOL" in best_split.hack_title


def test_hacker_fares_min_stay_filter():
    """Valida que combinações com tempo de permanência menor que min_stay_hours são descartadas."""
    out_q = DummyQuote(
        modal="AIR",
        service_class="ECONOMY",
        dep_time=datetime(2026, 11, 20, 8, 0),
        arr_time=datetime(2026, 11, 20, 10, 0),
        total_effective_price=200.0,
        door_to_door_duration_minutes=180,
    )
    # Volta apenas 2 horas depois do desembarque (inválido para min_stay_hours=6.0)
    in_q_invalid = DummyQuote(
        modal="AIR",
        service_class="ECONOMY",
        dep_time=datetime(2026, 11, 20, 12, 0),
        arr_time=datetime(2026, 11, 20, 14, 0),
        total_effective_price=200.0,
        door_to_door_duration_minutes=180,
    )

    hacks = generate_hacker_fares([out_q], [in_q_invalid], min_stay_hours=6.0)
    assert len(hacks) == 0
