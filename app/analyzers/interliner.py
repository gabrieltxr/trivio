"""Motor de Virtual Interlining: ônibus até um hub + voo saindo do hub.

Responsável por:
1. Construir rotas conectadas híbridas (ex: Ônibus Interior -> Hub -> Voo Capital).
2. Combinar as pernas pelo terminal do hub: cada chegada da perna 1 com cada partida da
   perna 2 ligada a ela por um transfer terrestre (mesmo terminal ou troca de terminal).
3. Validar janelas mínimas e máximas de conexão (MCT Intermodal):
   - Mínimo de 120 min para troca de modal (absorve atrasos rodoviários e antecedência no aeroporto).
   - Classificação de risco: SAFE (>= 180 min), WARNING (120-179 min), CRITICAL (< 120 min - rejeitada).
4. Gerar rotas sintéticas VirtualInterlineRoute e convertê-las para StandardizedQuote,
   enriquecendo com Trivio Score, prós, contras e contratempos explícitos (bilhetes não conjugados).
"""

from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from geopy.distance import geodesic

from app.analyzers.trivio_scorer import calculate_score_breakdown
from app.collectors.base import StandardizedQuote
from app.models import Terminal

logger = logging.getLogger("trivio.analyzers.interliner")

# ==============================================================================
# 1. Constantes e Limiares Operacionais
# ==============================================================================

MIN_INTERMODAL_CONNECTION_MINUTES = 120      # Mínimo absoluto (2 horas)
RECOMMENDED_SAFE_CONNECTION_MINUTES = 180    # Janela segura recomendada (3 horas)
MAX_INTERMODAL_CONNECTION_MINUTES = 720      # Máximo aceitável para o mesmo dia (12 horas)

DEFAULT_INTERMODAL_TRANSFER_MINUTES = 45     # Tempo médio transfer urbano rodoviária -> aeroporto
DEFAULT_INTERMODAL_TRANSFER_COST_BRL = 40.0  # Custo médio de transfer urbano (Uber/táxi/shuttle)
PENALTY_VIRTUAL_INTERLINE = 50.0             # Penalidade no Trivio Score por bilhetes não conjugados


# ==============================================================================
# 2. DTOs de Conexão e Rota Intermodal
# ==============================================================================

@dataclass
class TransferLeg:
    """Representa a perna terrestre de transfer entre terminais no mesmo Hub."""

    from_terminal_id: Any
    from_terminal_name: str
    to_terminal_id: Any
    to_terminal_name: str
    transfer_duration_minutes: int
    transfer_cost_brl: float
    distance_km: float = 0.0
    transfer_type: str = "INTER_TERMINAL_TRANSFER"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_terminal_id": self.from_terminal_id,
            "from_terminal_name": self.from_terminal_name,
            "to_terminal_id": self.to_terminal_id,
            "to_terminal_name": self.to_terminal_name,
            "transfer_duration_minutes": self.transfer_duration_minutes,
            "transfer_cost_brl": self.transfer_cost_brl,
            "distance_km": self.distance_km,
            "transfer_type": self.transfer_type,
        }


@dataclass
class VirtualInterlineRoute:
    """Representa uma viagem intermodal completa construída por Virtual Interlining."""

    origin_id: Any
    destination_id: Any
    hub_name: str
    hub_id: Optional[int]
    leg1: Any                               # Cotação perna 1 (ex: Ônibus Interior -> Hub)
    transfer: TransferLeg                   # Transfer entre terminais no Hub
    leg2: Any                               # Cotação perna 2 (ex: Voo Hub -> Capital)
    connection_window_minutes: int          # Tempo total entre chegada perna 1 e partida perna 2
    buffer_minutes: int                     # Margem livre após deslocamento terrestre
    risk_level: str                         # "SAFE", "WARNING", "CRITICAL"
    total_effective_price: float
    total_duration_minutes: int
    door_to_door_duration_minutes: int
    trivio_score: float = 0.0
    route_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        leg1_dict = self.leg1.to_dict() if hasattr(self.leg1, "to_dict") else dict(self.leg1)
        leg2_dict = self.leg2.to_dict() if hasattr(self.leg2, "to_dict") else dict(self.leg2)
        return {
            "is_virtual_interline": True,
            "origin_id": self.origin_id,
            "destination_id": self.destination_id,
            "hub_name": self.hub_name,
            "hub_id": self.hub_id,
            "connection_window_minutes": self.connection_window_minutes,
            "buffer_minutes": self.buffer_minutes,
            "risk_level": self.risk_level,
            "total_effective_price": self.total_effective_price,
            "total_duration_minutes": self.total_duration_minutes,
            "door_to_door_duration_minutes": self.door_to_door_duration_minutes,
            "trivio_score": self.trivio_score,
            "transfer": self.transfer.to_dict(),
            "legs": [leg1_dict, leg2_dict],
            "route_metadata": self.route_metadata,
        }

    def to_standardized_quote(self) -> StandardizedQuote:
        """Converte a rota intermodal para o DTO padrão StandardizedQuote."""
        carrier_name = f"{_get_val(self.leg1, 'carrier_name', 'Trecho 1')} + {_get_val(self.leg2, 'carrier_name', 'Trecho 2')}"
        modal_name = f"{_get_val(self.leg1, 'modal', 'ROAD')}_{_get_val(self.leg2, 'modal', 'AIR')}"
        service_class = f"{_get_val(self.leg1, 'service_class', 'CONVENCIONAL')}+{_get_val(self.leg2, 'service_class', 'ECONOMY')}"
        dep_time = _get_val(self.leg1, "departure_time")
        arr_time = _get_val(self.leg2, "arrival_time")

        base_p = float(_get_val(self.leg1, "base_price", 0.0)) + float(_get_val(self.leg2, "base_price", 0.0))
        taxes_p = float(_get_val(self.leg1, "taxes_price", 0.0)) + float(_get_val(self.leg2, "taxes_price", 0.0))
        bag_p = float(_get_val(self.leg1, "baggage_fee_estimate", 0.0)) + float(_get_val(self.leg2, "baggage_fee_estimate", 0.0))
        trans_p = (
            float(_get_val(self.leg1, "transfer_cost_estimate", 0.0))
            + self.transfer.transfer_cost_brl
            + float(_get_val(self.leg2, "transfer_cost_estimate", 0.0))
        )

        quote = StandardizedQuote(
            origin_external_id=str(self.origin_id),
            destination_external_id=str(self.destination_id),
            modal=modal_name,
            provider_source="trivio_interliner",
            carrier_name=carrier_name,
            service_class=service_class,
            departure_time=dep_time,
            arrival_time=arr_time,
            duration_minutes=self.total_duration_minutes,
            door_to_door_duration_minutes=self.door_to_door_duration_minutes,
            base_price=round(base_p, 2),
            taxes_price=round(taxes_p, 2),
            baggage_fee_estimate=round(bag_p, 2),
            transfer_cost_estimate=round(trans_p, 2),
            trivio_score=self.trivio_score,
            route_metadata=dict(self.route_metadata),
        )
        return quote


# ==============================================================================
# 3. Auxiliares de Extração e Cálculo Geodésico de Transfer
# ==============================================================================

def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _leg1_hub_id(quote: Any) -> str:
    """Identificador do terminal de chegada da perna 1 (ponto de conexão no hub)."""
    return str(_get_val(quote, "canonical_destination_id") or _get_val(quote, "destination_external_id"))


def _leg2_hub_id(quote: Any) -> str:
    """Identificador do terminal de partida da perna 2 (ponto de conexão no hub)."""
    return str(_get_val(quote, "canonical_origin_id") or _get_val(quote, "origin_external_id"))


def _leg_overhead_minutes(quote: Any) -> int:
    """Minutos de porta a porta além do tempo de viagem (transfers urbanos + antecedência)."""
    d2d = int(_get_val(quote, "door_to_door_duration_minutes", 0) or 0)
    duration = int(_get_val(quote, "duration_minutes", 0) or 0)
    return max(0, d2d - duration)


def _origin_access_minutes(quote: Any) -> int:
    """Tempo para sair de casa e embarcar na perna 1 (transfer urbano de origem + antecedência)."""
    meta = _get_val(quote, "route_metadata", {}) or {}
    if "origin_transfer_minutes" in meta:
        return int(meta.get("origin_transfer_minutes") or 0) + int(meta.get("checkin_buffer_minutes") or 0)
    # Sem decomposição disponível: assume todo o overhead da perna 1 no lado da origem (estimativa conservadora)
    return _leg_overhead_minutes(quote)


def _destination_egress_minutes(quote: Any) -> int:
    """Tempo do desembarque da perna 2 até o destino final (transfer urbano de destino)."""
    meta = _get_val(quote, "route_metadata", {}) or {}
    if "destination_transfer_minutes" in meta:
        return int(meta.get("destination_transfer_minutes") or 0)
    return 0


def calculate_terminal_transfer(
    from_term: Optional[Terminal],
    to_term: Optional[Terminal],
    default_minutes: int = DEFAULT_INTERMODAL_TRANSFER_MINUTES,
    default_cost: float = DEFAULT_INTERMODAL_TRANSFER_COST_BRL,
) -> TransferLeg:
    """Calcula custo e tempo real de deslocamento entre terminais do mesmo Hub."""
    from_id = from_term.id if from_term else "T1"
    from_name = from_term.name if from_term else "Terminal Rodoviário Hub"
    to_id = to_term.id if to_term else "T2"
    to_name = to_term.name if to_term else "Aeroporto Hub"

    # Caso seja exatamente o mesmo terminal físico
    if from_term and to_term and from_term.id == to_term.id:
        return TransferLeg(
            from_terminal_id=from_id,
            from_terminal_name=from_name,
            to_terminal_id=to_id,
            to_terminal_name=to_name,
            transfer_duration_minutes=0,
            transfer_cost_brl=0.0,
            distance_km=0.0,
            transfer_type="SAME_TERMINAL",
        )

    # Cálculo geodésico se ambos os terminais tiverem coordenadas
    dist_km = 0.0
    if (
        from_term
        and to_term
        and from_term.latitude is not None
        and from_term.longitude is not None
        and to_term.latitude is not None
        and to_term.longitude is not None
    ):
        p1 = (from_term.latitude, from_term.longitude)
        p2 = (to_term.latitude, to_term.longitude)
        dist_km = round(geodesic(p1, p2).kilometers, 2)

        # Regra empírica brasileira de trânsito metropolitano:
        # Tempo: 15 min base + 1.8 min por km
        # Custo Uber/Táxi: R$ 15 base + R$ 2.20 por km
        calc_min = max(25, int(15 + (dist_km * 1.8)))
        calc_cost = max(20.0, round(15.0 + (dist_km * 2.2), 2))
        return TransferLeg(
            from_terminal_id=from_id,
            from_terminal_name=from_name,
            to_terminal_id=to_id,
            to_terminal_name=to_name,
            transfer_duration_minutes=calc_min,
            transfer_cost_brl=calc_cost,
            distance_km=dist_km,
            transfer_type="METROPOLITAN_GROUND_TRANSFER",
        )

    # Fallback caso não haja coordenadas no banco
    return TransferLeg(
        from_terminal_id=from_id,
        from_terminal_name=from_name,
        to_terminal_id=to_id,
        to_terminal_name=to_name,
        transfer_duration_minutes=default_minutes,
        transfer_cost_brl=default_cost,
        distance_km=dist_km,
        transfer_type="DEFAULT_ESTIMATE",
    )


# ==============================================================================
# 4. Combinação das pernas (ônibus até o hub + voo saindo do hub)
# ==============================================================================

def find_interline_routes(
    origin_id: str,
    destination_id: str,
    leg1_quotes: Sequence[Any],
    leg2_quotes: Sequence[Any],
    transfer_map: Dict[Tuple[str, str], TransferLeg],
    hub_name: str = "Hub Intermodal",
    hub_id: Optional[int] = None,
    min_connection_minutes: int = MIN_INTERMODAL_CONNECTION_MINUTES,
    max_connection_minutes: int = MAX_INTERMODAL_CONNECTION_MINUTES,
    hourly_time_value: Optional[float] = None,
    max_results: int = 10,
) -> List[VirtualInterlineRoute]:
    """Combina cada perna 1 com as pernas 2 do terminal ligado a ela por transfer, valida a
    conexão e devolve as rotas ordenadas por Trivio Score.

    As pernas são agrupadas pelo terminal do hub (chegada da perna 1, partida da perna 2) e
    cada par de terminais com transfer é percorrido na ordem em que as cotações chegaram.
    Antes isso era um MultiDiGraph do NetworkX, usado só como agrupamento (nenhum algoritmo
    de grafo era chamado); a ordem de percurso e as regras são as mesmas.
    """
    leg1_by_arrival: Dict[str, List[Any]] = {}
    for q1 in leg1_quotes:
        leg1_by_arrival.setdefault(_leg1_hub_id(q1), []).append(q1)
    leg2_by_departure: Dict[str, List[Any]] = {}
    for q2 in leg2_quotes:
        leg2_by_departure.setdefault(_leg2_hub_id(q2), []).append(q2)

    valid_routes: List[VirtualInterlineRoute] = []

    for arr_id, leg1_group in leg1_by_arrival.items():
        for dep_id, leg2_group in leg2_by_departure.items():
            transfer = transfer_map.get((arr_id, dep_id))
            if transfer is None:
                continue

            for q1 in leg1_group:
                arr1 = _get_val(q1, "arrival_time")
                dep1 = _get_val(q1, "departure_time")

                for q2 in leg2_group:
                    dep2 = _get_val(q2, "departure_time")
                    arr2 = _get_val(q2, "arrival_time")

                    if not arr1 or not dep2:
                        continue

                    # Validação de Cronologia e Janela de Conexão
                    conn_seconds = (dep2 - arr1).total_seconds()
                    if conn_seconds <= 0:
                        continue  # O segundo trecho parte antes de o primeiro chegar

                    conn_minutes = int(conn_seconds // 60)
                    if conn_minutes < min_connection_minutes or conn_minutes > max_connection_minutes:
                        continue

                    # Margem de segurança após o deslocamento de transfer
                    buffer_min = conn_minutes - transfer.transfer_duration_minutes
                    if buffer_min < 45:  # Menos de 45 min livres é inviável para check-in/embarque
                        continue

                    # Classificação de Risco
                    if conn_minutes < RECOMMENDED_SAFE_CONNECTION_MINUTES:
                        risk_level = "WARNING"
                    else:
                        risk_level = "SAFE"

                    # Métricas Financeiras e Temporais
                    p1 = float(_get_val(q1, "total_effective_price", 0.0) or _get_val(q1, "base_price", 0.0))
                    p2 = float(_get_val(q2, "total_effective_price", 0.0) or _get_val(q2, "base_price", 0.0))
                    total_price = round(p1 + transfer.transfer_cost_brl + p2, 2)

                    total_duration_minutes = int((arr2 - dep1).total_seconds() // 60) if dep1 and arr2 else 0
                    # Porta a porta = acesso até o terminal de origem (transfer urbano + antecedência)
                    # + tempo corrido da partida da perna 1 à chegada da perna 2 (já inclui a espera
                    # e o transfer entre terminais no hub) + transfer urbano a partir do destino final.
                    total_d2d = (
                        _origin_access_minutes(q1)
                        + total_duration_minutes
                        + _destination_egress_minutes(q2)
                    )

                    # Construção de Metadados Ricos
                    metadata: Dict[str, Any] = {
                        "is_virtual_interline": True,
                        "intermodal_hub": hub_name,
                        "transfer_details": transfer.to_dict(),
                        "connection_window_minutes": conn_minutes,
                        "buffer_minutes": buffer_min,
                        "risk_level": risk_level,
                        "pros": [
                            f"Integração Intermodal ({_get_val(q1, 'modal')}+{_get_val(q2, 'modal')}): "
                            f"conecta interior ao destino sem depender de voo regional inflacionado."
                        ],
                        "cons": [
                            f"Requer troca de terminal em {hub_name} ({transfer.from_terminal_name} -> "
                            f"{transfer.to_terminal_name}, ~{transfer.transfer_duration_minutes} min)."
                        ],
                        "potential_setbacks": [
                            "Conexão independente (bilhetes avulsos): atrasos na primeira perna "
                            "não são cobertos pela companhia aérea da segunda perna."
                        ],
                    }

                    if risk_level == "WARNING":
                        metadata["potential_setbacks"].append(
                            f"Janela de conexão de atenção ({conn_minutes // 60}h{conn_minutes % 60:02d}m): "
                            f"restam apenas {buffer_min} min livres após o transfer em caso de trânsito na rodovia."
                        )
                    else:
                        metadata["pros"].append(
                            f"Janela de conexão segura ({conn_minutes // 60}h{conn_minutes % 60:02d}m) "
                            f"com {buffer_min} min livres após transfer."
                        )

                    # Cálculo do Trivio Score com penalidade de bilhete avulso
                    score_quote_mock = {
                        "modal": "AIR",  # Ponderação mista
                        "service_class": _get_val(q2, "service_class", "ECONOMY"),
                        "departure_time": dep1,
                        "arrival_time": arr2,
                        "duration_minutes": total_duration_minutes,
                        "door_to_door_duration_minutes": total_d2d,
                        "total_effective_price": total_price,
                        "route_metadata": {
                            "is_direct": False,
                            "stops_count": 1,
                            "connection_diagnostics": {
                                "total_connection_penalty_points": PENALTY_VIRTUAL_INTERLINE + (30.0 if risk_level == "WARNING" else 0.0)
                            },
                        },
                    }
                    breakdown = calculate_score_breakdown(score_quote_mock, hourly_time_value=hourly_time_value)
                    metadata["score_breakdown"] = breakdown.to_dict()

                    interline_route = VirtualInterlineRoute(
                        origin_id=origin_id,
                        destination_id=destination_id,
                        hub_name=hub_name,
                        hub_id=hub_id,
                        leg1=q1,
                        transfer=transfer,
                        leg2=q2,
                        connection_window_minutes=conn_minutes,
                        buffer_minutes=buffer_min,
                        risk_level=risk_level,
                        total_effective_price=total_price,
                        total_duration_minutes=total_duration_minutes,
                        door_to_door_duration_minutes=total_d2d,
                        trivio_score=breakdown.trivio_score,
                        route_metadata=metadata,
                    )
                    valid_routes.append(interline_route)

    # Ordena pelo menor Trivio Score (melhor custo-benefício e menor atrito)
    valid_routes.sort(key=lambda r: r.trivio_score)
    return valid_routes[:max_results]


# ==============================================================================
# 5. Função de Entrada Pública para Construção de Rotas Intermodais
# ==============================================================================

def build_virtual_interline_routes(
    origin_id: str,
    destination_id: str,
    leg1_quotes: Sequence[Any],
    leg2_quotes: Sequence[Any],
    hub_name: str = "Hub Intermodal",
    hub_id: Optional[int] = None,
    terminal_lookup: Optional[Dict[str, Terminal]] = None,
    min_connection_minutes: int = MIN_INTERMODAL_CONNECTION_MINUTES,
    max_connection_minutes: int = MAX_INTERMODAL_CONNECTION_MINUTES,
    hourly_time_value: Optional[float] = None,
    max_results: int = 10,
) -> List[VirtualInterlineRoute]:
    """Constrói, valida e ranqueia rotas de Virtual Interlining entre duas pernas de viagem."""
    if not leg1_quotes or not leg2_quotes:
        return []

    # Mapeia pares de terminais e calcula os transfers terrestres
    transfer_map: Dict[Tuple[str, str], TransferLeg] = {}
    term_dict = terminal_lookup or {}

    for q1 in leg1_quotes:
        arr_node = _leg1_hub_id(q1)
        for q2 in leg2_quotes:
            dep_node = _leg2_hub_id(q2)
            pair_key = (arr_node, dep_node)
            if pair_key not in transfer_map:
                t1 = term_dict.get(arr_node)
                t2 = term_dict.get(dep_node)
                transfer_leg = calculate_terminal_transfer(from_term=t1, to_term=t2)
                transfer_map[pair_key] = transfer_leg

    return find_interline_routes(
        origin_id=origin_id,
        destination_id=destination_id,
        leg1_quotes=leg1_quotes,
        leg2_quotes=leg2_quotes,
        transfer_map=transfer_map,
        hub_name=hub_name,
        hub_id=hub_id,
        min_connection_minutes=min_connection_minutes,
        max_connection_minutes=max_connection_minutes,
        hourly_time_value=hourly_time_value,
        max_results=max_results,
    )
