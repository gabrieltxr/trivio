"""Unit tests for Virtual Interlining Graph Engine (NetworkX)."""

from datetime import datetime, timedelta
import pytest

from app.analyzers.interliner import (
    DEFAULT_INTERMODAL_TRANSFER_COST_BRL,
    DEFAULT_INTERMODAL_TRANSFER_MINUTES,
    MIN_INTERMODAL_CONNECTION_MINUTES,
    RECOMMENDED_SAFE_CONNECTION_MINUTES,
    TransferLeg,
    VirtualInterlineRoute,
    VirtualInterliningGraph,
    build_virtual_interline_routes,
    calculate_terminal_transfer,
)
from app.collectors.base import StandardizedQuote
from app.models import ModalType, Terminal


class DummyTerminal:
    """Mock Terminal para testes geodésicos."""

    def __init__(self, id, name, modal, lat, lon):
        self.id = id
        self.name = name
        self.modal = modal
        self.latitude = lat
        self.longitude = lon


def test_calculate_terminal_transfer_same_terminal():
    """Valida transferência interna no mesmo terminal físico (custo e tempo zero)."""
    t1 = DummyTerminal(1, "Terminal Central", ModalType.ROAD, -23.55, -46.63)
    leg = calculate_terminal_transfer(from_term=t1, to_term=t1)

    assert leg.transfer_duration_minutes == 0
    assert leg.transfer_cost_brl == 0.0
    assert leg.distance_km == 0.0
    assert leg.transfer_type == "SAME_TERMINAL"


def test_calculate_terminal_transfer_geodesic():
    """Valida cálculo geodésico de transfer entre Rodoviária Novo Rio e Aeroporto Santos Dumont (~5 km)."""
    rodoviaria = DummyTerminal(10, "Rodoviária Novo Rio", ModalType.ROAD, -22.8986, -43.2096)
    santos_dumont = DummyTerminal(20, "Aeroporto Santos Dumont", ModalType.AIR, -22.9105, -43.1631)

    leg = calculate_terminal_transfer(from_term=rodoviaria, to_term=santos_dumont)

    assert leg.distance_km > 4.0
    assert leg.distance_km < 7.0
    assert leg.transfer_duration_minutes >= 25
    assert leg.transfer_cost_brl >= 20.0
    assert leg.transfer_type == "METROPOLITAN_GROUND_TRANSFER"


def test_virtual_interlining_valid_safe_route():
    """Valida rota intermodal segura (Ônibus Pelotas -> Porto Alegre + Voo Porto Alegre -> SP)."""
    base_date = datetime(2026, 11, 20)

    # Perna 1: Ônibus Pelotas -> Rodoviária Porto Alegre (Chegada às 09:30)
    bus_quote = {
        "origin_external_id": "PELOTAS_ROD",
        "destination_external_id": "POA_ROD",
        "canonical_origin_id": "PELOTAS_ROD",
        "canonical_destination_id": "POA_ROD",
        "modal": "ROAD",
        "carrier_name": "Empresa Santo Anjo",
        "service_class": "CONVENCIONAL",
        "departure_time": base_date.replace(hour=6, minute=0),
        "arrival_time": base_date.replace(hour=9, minute=30),
        "duration_minutes": 210,
        "door_to_door_duration_minutes": 240,
        "base_price": 85.0,
        "total_effective_price": 85.0,
        "transfer_cost_estimate": 10.0,
    }

    # Perna 2: Voo Aeroporto Salgado Filho (POA_AERO) -> Congonhas (Partida às 13:30)
    # Janela de Conexão = 13:30 - 09:30 = 240 min (4h) -> SAFE (>= 180 min)
    flight_quote = {
        "origin_external_id": "POA_AERO",
        "destination_external_id": "CGH",
        "canonical_origin_id": "POA_AERO",
        "canonical_destination_id": "CGH",
        "modal": "AIR",
        "carrier_name": "Azul",
        "service_class": "ECONOMY",
        "departure_time": base_date.replace(hour=13, minute=30),
        "arrival_time": base_date.replace(hour=15, minute=10),
        "duration_minutes": 100,
        "door_to_door_duration_minutes": 220,
        "base_price": 320.0,
        "taxes_price": 35.0,
        "total_effective_price": 355.0,
        "transfer_cost_estimate": 30.0,
    }

    routes = build_virtual_interline_routes(
        origin_id="PELOTAS_ROD",
        destination_id="CGH",
        leg1_quotes=[bus_quote],
        leg2_quotes=[flight_quote],
        hub_name="Porto Alegre Hub",
        hub_id=1,
    )

    assert len(routes) == 1
    r = routes[0]
    assert r.risk_level == "SAFE"
    assert r.connection_window_minutes == 240
    assert r.buffer_minutes == 240 - DEFAULT_INTERMODAL_TRANSFER_MINUTES
    assert r.total_effective_price == round(85.0 + DEFAULT_INTERMODAL_TRANSFER_COST_BRL + 355.0, 2)
    assert r.trivio_score > 0
    assert r.route_metadata["is_virtual_interline"] is True
    assert any("bilhetes avulsos" in s for s in r.route_metadata["potential_setbacks"])
    assert any("Janela de conexão segura" in p for p in r.route_metadata["pros"])


def test_virtual_interlining_tight_connection_warning():
    """Valida janela de conexão apertada (120 a 179 min) que gera alerta WARNING."""
    base_date = datetime(2026, 11, 20)

    # Ônibus chega às 11:00
    bus_quote = {
        "origin_external_id": "CAMPINAS_ROD",
        "destination_external_id": "TIETE_SP",
        "modal": "ROAD",
        "carrier_name": "Cometa",
        "departure_time": base_date.replace(hour=9, minute=30),
        "arrival_time": base_date.replace(hour=11, minute=0),
        "base_price": 45.0,
        "total_effective_price": 45.0,
    }

    # Voo sai às 13:30 de Guarulhos (Janela = 150 min, menor que 180 min recomendados)
    flight_quote = {
        "origin_external_id": "GRU",
        "destination_external_id": "SDU",
        "modal": "AIR",
        "carrier_name": "GOL",
        "departure_time": base_date.replace(hour=13, minute=30),
        "arrival_time": base_date.replace(hour=14, minute=30),
        "base_price": 250.0,
        "total_effective_price": 250.0,
    }

    routes = build_virtual_interline_routes(
        origin_id="CAMPINAS_ROD",
        destination_id="SDU",
        leg1_quotes=[bus_quote],
        leg2_quotes=[flight_quote],
        hub_name="São Paulo Hub",
    )

    assert len(routes) == 1
    r = routes[0]
    assert r.risk_level == "WARNING"
    assert r.connection_window_minutes == 150
    assert any("Janela de conexão de atenção" in s for s in r.route_metadata["potential_setbacks"])


def test_virtual_interlining_rejected_under_minimum_mct():
    """Valida descarte automático de conexão com tempo inferior ao limite mínimo seguro (120 min)."""
    base_date = datetime(2026, 11, 20)

    # Ônibus chega às 12:00
    bus_quote = {
        "origin_external_id": "ORIGIN",
        "destination_external_id": "HUB_ROD",
        "departure_time": base_date.replace(hour=10, minute=0),
        "arrival_time": base_date.replace(hour=12, minute=0),
        "base_price": 50.0,
        "total_effective_price": 50.0,
    }

    # Voo sai às 13:30 (Janela de apenas 90 min - inviável com transfer metropolitano)
    flight_quote = {
        "origin_external_id": "HUB_AIR",
        "destination_external_id": "DEST",
        "departure_time": base_date.replace(hour=13, minute=30),
        "arrival_time": base_date.replace(hour=14, minute=30),
        "base_price": 200.0,
        "total_effective_price": 200.0,
    }

    routes = build_virtual_interline_routes(
        origin_id="ORIGIN",
        destination_id="DEST",
        leg1_quotes=[bus_quote],
        leg2_quotes=[flight_quote],
    )

    # Rota rejeitada por risco crítico de perda de conexão
    assert len(routes) == 0


def test_virtual_interlining_rejected_chronology_and_excessive():
    """Valida descarte quando voo sai antes da chegada ou após mais de 12 horas."""
    base_date = datetime(2026, 11, 20)

    bus_quote = {
        "origin_external_id": "ORIGIN",
        "destination_external_id": "HUB_ROD",
        "departure_time": base_date.replace(hour=10, minute=0),
        "arrival_time": base_date.replace(hour=14, minute=0),
        "base_price": 50.0,
        "total_effective_price": 50.0,
    }

    # 1. Voo parte antes do ônibus chegar
    flight_before = {
        "origin_external_id": "HUB_AIR",
        "destination_external_id": "DEST",
        "departure_time": base_date.replace(hour=12, minute=0),
        "arrival_time": base_date.replace(hour=13, minute=0),
        "base_price": 200.0,
        "total_effective_price": 200.0,
    }

    # 2. Voo parte 14 horas depois (conexão excessivamente longa para interlining)
    flight_too_late = {
        "origin_external_id": "HUB_AIR",
        "destination_external_id": "DEST",
        "departure_time": base_date.replace(hour=14, minute=0) + timedelta(hours=14),
        "arrival_time": base_date.replace(hour=15, minute=0) + timedelta(hours=14),
        "base_price": 200.0,
        "total_effective_price": 200.0,
    }

    routes = build_virtual_interline_routes(
        origin_id="ORIGIN",
        destination_id="DEST",
        leg1_quotes=[bus_quote],
        leg2_quotes=[flight_before, flight_too_late],
    )

    assert len(routes) == 0


def test_to_standardized_quote_conversion():
    """Valida conversão de VirtualInterlineRoute para StandardizedQuote com DTO completo."""
    base_date = datetime(2026, 11, 20)
    bus_quote = {
        "origin_external_id": "PELOTAS_ROD",
        "destination_external_id": "POA_ROD",
        "modal": "ROAD",
        "carrier_name": "Santo Anjo",
        "service_class": "LEITO",
        "departure_time": base_date.replace(hour=6, minute=0),
        "arrival_time": base_date.replace(hour=9, minute=30),
        "duration_minutes": 210,
        "door_to_door_duration_minutes": 240,
        "base_price": 100.0,
        "taxes_price": 10.0,
        "baggage_fee_estimate": 0.0,
        "transfer_cost_estimate": 15.0,
        "total_effective_price": 125.0,
    }
    flight_quote = {
        "origin_external_id": "POA_AERO",
        "destination_external_id": "CGH",
        "modal": "AIR",
        "carrier_name": "LATAM",
        "service_class": "ECONOMY",
        "departure_time": base_date.replace(hour=13, minute=30),
        "arrival_time": base_date.replace(hour=15, minute=10),
        "duration_minutes": 100,
        "door_to_door_duration_minutes": 200,
        "base_price": 300.0,
        "taxes_price": 40.0,
        "baggage_fee_estimate": 110.0,
        "transfer_cost_estimate": 25.0,
        "total_effective_price": 475.0,
    }

    routes = build_virtual_interline_routes(
        origin_id="PELOTAS_ROD",
        destination_id="CGH",
        leg1_quotes=[bus_quote],
        leg2_quotes=[flight_quote],
    )
    assert len(routes) == 1
    interline = routes[0]

    std_quote = interline.to_standardized_quote()
    assert isinstance(std_quote, StandardizedQuote)
    assert std_quote.modal == "ROAD_AIR"
    assert "Santo Anjo" in std_quote.carrier_name
    assert "LATAM" in std_quote.carrier_name
    assert std_quote.provider_source == "trivio_interliner"
    assert std_quote.departure_time == base_date.replace(hour=6, minute=0)
    assert std_quote.arrival_time == base_date.replace(hour=15, minute=10)
    assert std_quote.total_effective_price == round(125.0 + DEFAULT_INTERMODAL_TRANSFER_COST_BRL + 475.0, 2)
    assert std_quote.route_metadata["is_virtual_interline"] is True


def test_virtual_interlining_same_hub_terminal_id_is_not_dropped():
    """Perna 1 chegando ao mesmo identificador de onde parte a perna 2 deve gerar rota.

    Antes, o nó único do hub tinha o tipo HUB_ARRIVAL sobrescrito por HUB_DEPARTURE
    e nenhuma rota era encontrada.
    """
    base_date = datetime(2026, 11, 20)
    bus_quote = {
        "canonical_origin_id": "ROD_RIO_GRANDE",
        "canonical_destination_id": "HUB_PELOTAS",
        "modal": "ROAD",
        "carrier_name": "Ônibus",
        "service_class": "CONVENCIONAL",
        "departure_time": base_date.replace(hour=7, minute=0),
        "arrival_time": base_date.replace(hour=8, minute=0),
        "duration_minutes": 60,
        "door_to_door_duration_minutes": 80,
        "base_price": 40.0,
        "total_effective_price": 40.0,
    }
    flight_quote = {
        "canonical_origin_id": "HUB_PELOTAS",
        "canonical_destination_id": "CGH",
        "modal": "AIR",
        "carrier_name": "Voo",
        "service_class": "ECONOMY",
        "departure_time": base_date.replace(hour=12, minute=0),
        "arrival_time": base_date.replace(hour=14, minute=0),
        "duration_minutes": 120,
        "door_to_door_duration_minutes": 210,
        "base_price": 400.0,
        "total_effective_price": 400.0,
    }

    routes = build_virtual_interline_routes(
        origin_id="RIG",
        destination_id="SAO",
        leg1_quotes=[bus_quote],
        leg2_quotes=[flight_quote],
        hub_name="Pelotas, RS",
        hub_id=6,
    )

    assert len(routes) == 1
    assert routes[0].connection_window_minutes == 240


def test_virtual_interlining_door_to_door_includes_hub_wait():
    """O porta a porta da rota híbrida nunca pode ser menor que o tempo corrido da viagem.

    Deve somar: acesso na origem + (partida da perna 1 até chegada da perna 2) + saída no destino.
    """
    base_date = datetime(2026, 11, 20)
    bus_quote = {
        "canonical_destination_id": "HUB_ROD",
        "modal": "ROAD",
        "service_class": "SEMI_LEITO",
        "departure_time": base_date.replace(hour=8, minute=30),
        "arrival_time": base_date.replace(hour=9, minute=30),
        "duration_minutes": 60,
        "door_to_door_duration_minutes": 60 + 15 + 20 + 20,
        "total_effective_price": 50.0,
        "route_metadata": {
            "origin_transfer_minutes": 15,
            "destination_transfer_minutes": 20,
            "checkin_buffer_minutes": 20,
        },
    }
    flight_quote = {
        "canonical_origin_id": "HUB_AIR",
        "modal": "AIR",
        "service_class": "ECONOMY",
        "departure_time": base_date.replace(hour=14, minute=0),
        "arrival_time": base_date.replace(hour=16, minute=0),
        "duration_minutes": 120,
        "door_to_door_duration_minutes": 120 + 30 + 40 + 90,
        "total_effective_price": 400.0,
        "route_metadata": {
            "origin_transfer_minutes": 30,
            "destination_transfer_minutes": 40,
            "checkin_buffer_minutes": 90,
        },
    }

    routes = build_virtual_interline_routes(
        origin_id="ORIG",
        destination_id="DEST",
        leg1_quotes=[bus_quote],
        leg2_quotes=[flight_quote],
    )

    assert len(routes) == 1
    r = routes[0]
    assert r.total_duration_minutes == 450  # 08:30 -> 16:00
    # 15 (transfer origem) + 20 (antecedência rodoviária) + 450 + 40 (transfer destino)
    assert r.door_to_door_duration_minutes == 525
    assert r.door_to_door_duration_minutes >= r.total_duration_minutes
