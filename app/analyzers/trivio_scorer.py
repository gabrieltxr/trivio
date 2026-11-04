"""Motor de Cálculo do Trivio Score (Agony Index) e Combinador Hacker Fares (Trivio Core).

Responsável por:
1. Trivio Score: Classificação holística de opções aéreas e rodoviárias em uma mesma régua
   quantitativa (quanto menor o score, melhor a relação custo x tempo x conforto):
   Score = Preço_Efetivo + (Tempo_Porta_a_Porta / 60 * V_hora * F_conforto) + Penalidades_Atrito
2. Hacker Fares Combinator: Combinação inteligente de bilhetes avulsos de ida e volta
   (ex: Ida de Avião na sexta + Volta de Ônibus Leito noturno no domingo), comparando com
   opções monomodais e destacando economias de até 60% com conforto.
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from app.analyzers.route_evaluator import _is_madrugada, _to_brazil_time
from app.config import settings

logger = logging.getLogger("trivio.analyzers.scorer")


# ==============================================================================
# 1. Constantes e Fatores de Conforto (F_conforto)
# ==============================================================================

# Fatores de desgaste da viagem (1.0 = referência neutra / voo direto / semi-leito)
COMFORT_FACTORS = {
    # Ônibus Leito / Leito Cama Noturno (dorme a noite toda e economiza 1 diária)
    "ROAD_LEITO_NOTURNO": 0.55,
    # Ônibus Leito Diurno
    "ROAD_LEITO_DIURNO": 0.85,
    # Voo Direto ou Ônibus Semi-Leito padrão
    "DEFAULT_NEUTRAL": 1.00,
    # Voo em Classe Executiva (Business)
    "AIR_BUSINESS": 0.70,
    # Voo com Conexão
    "AIR_CONNECTION": 1.15,
    # Ônibus Semi-Leito Diurno longo
    "ROAD_SEMI_LEITO_DIURNO_LONGO": 1.15,
    # Ônibus Convencional Diurno / Longo
    "ROAD_CONVENCIONAL": 1.35,
}

# Penalidades de atrito (em pontos / R$)
PENALTY_CONNECTION_DEFAULT = 40.0         # Conexão / baldeação comum
PENALTY_MADRUGADA = 60.0                   # Embarque ou desembarque entre 01:00 e 04:59
PENALTY_EXTRA_STOP_ROAD = 10.0             # Por parada adicional na rodovia acima de 2
PENALTY_AIRPORT_SWAP = 80.0                # Troca de aeroporto na conexão (CGH <-> GRU)
PENALTY_TIGHT_CONNECTION_CRITICAL = 100.0  # Conexão de alto risco (<50 min em hub)
PENALTY_TIGHT_CONNECTION_WARNING = 50.0   # Conexão apertada (50 a 65 min em hub)


@dataclass
class TrivioScoreBreakdown:
    """Detalhamento transparente de cada componente da pontuação Trivio."""

    effective_price: float
    door_to_door_duration_minutes: int
    hourly_time_value: float
    comfort_factor: float
    time_cost: float
    connection_penalty: float
    madrugada_penalty: float
    stops_penalty: float
    airport_swap_penalty: float
    tight_connection_penalty: float
    total_penalties: float
    trivio_score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "effective_price": self.effective_price,
            "door_to_door_duration_minutes": self.door_to_door_duration_minutes,
            "hourly_time_value": self.hourly_time_value,
            "comfort_factor": self.comfort_factor,
            "time_cost": round(self.time_cost, 2),
            "penalties": {
                "connection": self.connection_penalty,
                "madrugada": self.madrugada_penalty,
                "stops": self.stops_penalty,
                "airport_swap": self.airport_swap_penalty,
                "tight_connection": self.tight_connection_penalty,
                "total": self.total_penalties,
            },
            "trivio_score": round(self.trivio_score, 2),
        }


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def determine_comfort_factor(
    modal: str,
    service_class: str,
    dep_time: Optional[datetime],
    arr_time: Optional[datetime],
    is_direct: bool,
    duration_min: int,
) -> float:
    """Calcula o multiplicador F_conforto conforme o modal, horário e classe de assento."""
    modal_upper = str(modal).strip().upper()
    service_upper = str(service_class).strip().upper()

    local_dep = _to_brazil_time(dep_time)
    local_arr = _to_brazil_time(arr_time)
    is_night_departure = local_dep is not None and local_dep.hour >= 20
    is_morning_arrival = local_arr is not None and 5 <= local_arr.hour <= 9

    # Modal Rodoviário
    if "ROAD" in modal_upper:
        if "SEMI_LEITO" in service_upper or "SEMI-LEITO" in service_upper or "EXECUTIVO" in service_upper:
            if duration_min > 360:
                return COMFORT_FACTORS["ROAD_SEMI_LEITO_DIURNO_LONGO"]
            return COMFORT_FACTORS["DEFAULT_NEUTRAL"]

        if "LEITO" in service_upper:
            # Leito noturno com chegada matinal (pernoite produtivo)
            if is_night_departure and is_morning_arrival:
                return COMFORT_FACTORS["ROAD_LEITO_NOTURNO"]
            return COMFORT_FACTORS["ROAD_LEITO_DIURNO"]

        # Convencional básico
        return COMFORT_FACTORS["ROAD_CONVENCIONAL"]

    # Modal Aéreo
    if "AIR" in modal_upper:
        if "BUSINESS" in service_upper or "FIRST" in service_upper or "EXECUTIVA" in service_upper:
            return COMFORT_FACTORS["AIR_BUSINESS"]
        if not is_direct:
            return COMFORT_FACTORS["AIR_CONNECTION"]
        return COMFORT_FACTORS["DEFAULT_NEUTRAL"]

    return COMFORT_FACTORS["DEFAULT_NEUTRAL"]


# ==============================================================================
# 2. Cálculo do Trivio Score
# ==============================================================================

def calculate_score_breakdown(
    quote: Any,
    hourly_time_value: Optional[float] = None,
) -> TrivioScoreBreakdown:
    """Calcula a equação completa do Trivio Score retornando o detalhamento de atritos."""
    v_hour = (
        hourly_time_value
        if hourly_time_value is not None
        else settings.DEFAULT_HOURLY_TIME_VALUE_BRL
    )

    modal = str(_get_val(quote, "modal", "")).strip().upper()
    service_class = str(_get_val(quote, "service_class", "")).strip().upper()
    dep_time = _get_val(quote, "departure_time")
    arr_time = _get_val(quote, "arrival_time")
    duration_min = int(_get_val(quote, "duration_minutes", 0) or 0)
    d2d_minutes = int(_get_val(quote, "door_to_door_duration_minutes", 0) or duration_min)

    # Preço efetivo total (Base + Taxas + Bagagem + Transfer)
    p_effective = float(_get_val(quote, "total_effective_price", 0.0) or 0.0)
    if p_effective <= 0.0:
        base_p = float(_get_val(quote, "base_price", 0.0) or 0.0)
        tax_p = float(_get_val(quote, "taxes_price", 0.0) or 0.0)
        bag_p = float(_get_val(quote, "baggage_fee_estimate", 0.0) or 0.0)
        trans_p = float(_get_val(quote, "transfer_cost_estimate", 0.0) or 0.0)
        p_effective = round(base_p + tax_p + bag_p + trans_p, 2)

    meta = _get_val(quote, "route_metadata", {}) or {}
    is_direct = meta.get("is_direct", True)
    stops_count = int(meta.get("stops_count", 0) or 0)
    layovers = meta.get("layover_airports") or meta.get("intermediate_stops") or []

    # 1. Fator de Conforto
    f_comfort = determine_comfort_factor(
        modal=modal,
        service_class=service_class,
        dep_time=dep_time,
        arr_time=arr_time,
        is_direct=is_direct,
        duration_min=duration_min,
    )

    # 2. Custo do Tempo (Horas porta a porta * Valor da Hora * Fator de Conforto)
    time_cost = (d2d_minutes / 60.0) * v_hour * f_comfort

    # 3. Penalidades de Atrito
    connection_pen = 0.0
    madrugada_pen = 0.0
    stops_pen = 0.0
    airport_swap_pen = 0.0
    tight_conn_pen = 0.0

    # Conexão padrão
    if not is_direct or stops_count > 0:
        connection_pen = PENALTY_CONNECTION_DEFAULT * max(1, stops_count)

    # Madrugada (01:00 às 04:59)
    if _is_madrugada(dep_time) or _is_madrugada(arr_time):
        madrugada_pen = PENALTY_MADRUGADA

    # Paradas intermediárias rodoviárias excedentes (acima de 2)
    if "ROAD" in modal and stops_count > 2:
        stops_pen = (stops_count - 2) * PENALTY_EXTRA_STOP_ROAD

    # Troca de aeroporto na conexão (CGH <-> GRU ou SDU <-> GIG)
    if "AIR" in modal and layovers:
        has_sp_swap = any("CGH" in str(s) for s in layovers) and any("GRU" in str(s) for s in layovers)
        has_rj_swap = any("SDU" in str(s) for s in layovers) and any("GIG" in str(s) for s in layovers)
        if has_sp_swap or has_rj_swap:
            airport_swap_pen = PENALTY_AIRPORT_SWAP

    # Conexão Apertada (obtida dos connection_diagnostics gerados pelo RouteEvaluator)
    conn_diags = meta.get("connection_diagnostics")
    if conn_diags and isinstance(conn_diags, dict):
        tight_conn_pen = float(conn_diags.get("total_connection_penalty_points", 0.0))

    total_penalties = connection_pen + madrugada_pen + stops_pen + airport_swap_pen + tight_conn_pen
    final_score = round(p_effective + time_cost + total_penalties, 2)

    return TrivioScoreBreakdown(
        effective_price=p_effective,
        door_to_door_duration_minutes=d2d_minutes,
        hourly_time_value=v_hour,
        comfort_factor=f_comfort,
        time_cost=time_cost,
        connection_penalty=connection_pen,
        madrugada_penalty=madrugada_pen,
        stops_penalty=stops_pen,
        airport_swap_penalty=airport_swap_pen,
        tight_connection_penalty=tight_conn_pen,
        total_penalties=total_penalties,
        trivio_score=final_score,
    )


def calculate_trivio_score(
    quote: Any,
    hourly_time_value: Optional[float] = None,
) -> float:
    """Calcula o valor numérico final do Trivio Score."""
    breakdown = calculate_score_breakdown(quote, hourly_time_value=hourly_time_value)
    return breakdown.trivio_score


def score_and_rank_quotes(
    quotes: Sequence[Any],
    hourly_time_value: Optional[float] = None,
) -> List[Any]:
    """Calcula o score de todas as cotações, atualiza o campo trivio_score e ordena da melhor para a pior."""
    scored: List[Any] = []
    for q in quotes:
        breakdown = calculate_score_breakdown(q, hourly_time_value=hourly_time_value)
        score_val = breakdown.trivio_score

        if hasattr(q, "trivio_score"):
            setattr(q, "trivio_score", score_val)
        elif isinstance(q, dict):
            q["trivio_score"] = score_val

        # Anexa breakdown nos metadados para transparência no card do frontend
        if hasattr(q, "route_metadata"):
            meta = getattr(q, "route_metadata")
            if meta is not None and isinstance(meta, dict):
                meta["score_breakdown"] = breakdown.to_dict()
        elif isinstance(q, dict) and "route_metadata" in q:
            q["route_metadata"]["score_breakdown"] = breakdown.to_dict()

        scored.append(q)

    # Menor score = Melhor opção (menor atrito / custo-benefício superior)
    scored.sort(key=lambda item: _get_val(item, "trivio_score", float("inf")))
    return scored


# ==============================================================================
# 3. Combinador Hacker Fares (Ida Modal A + Volta Modal B)
# ==============================================================================

def _obj_to_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {"repr": str(obj)}


@dataclass
class HackerFareCombination:
    """Representa uma combinação inteligente de ida e volta (Hacker Fare)."""

    outbound: Any
    inbound: Any
    strategy_type: str                   # 'HYBRID_MODAL', 'SPLIT_CARRIER', 'SAME_CARRIER'
    combined_effective_price: float
    combined_trivio_score: float
    combined_door_to_door_minutes: int
    savings_vs_single_modal_brl: float
    hack_title: str
    hack_description: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_type": self.strategy_type,
            "combined_effective_price": self.combined_effective_price,
            "combined_trivio_score": self.combined_trivio_score,
            "combined_door_to_door_minutes": self.combined_door_to_door_minutes,
            "savings_vs_single_modal_brl": self.savings_vs_single_modal_brl,
            "hack_title": self.hack_title,
            "hack_description": self.hack_description,
            "outbound": _obj_to_dict(self.outbound),
            "inbound": _obj_to_dict(self.inbound),
        }


def generate_hacker_fares(
    outbound_quotes: Sequence[Any],
    inbound_quotes: Sequence[Any],
    min_stay_hours: float = 6.0,
    hourly_time_value: Optional[float] = None,
    top_n: int = 10,
) -> List[HackerFareCombination]:
    """Gera combinações inteligentes de Ida e Volta entre múltiplos modais e operadoras.
    
    Identifica oportunidades onde:
    - Ida Aérea + Volta Rodoviária Leito Noturna (ou vice-versa) oferece a melhor combinação
      de tempo e economia (elimina diárias de hotel e voos caros de domingo à noite).
    - Ida em uma companhia aérea + Volta em outra gera economia sobre a passagem round-trip fechada.
    """
    if not outbound_quotes or not inbound_quotes:
        return []

    # Garante que todas as cotações individuais estejam com Trivio Score calculado
    scored_outbound = score_and_rank_quotes(outbound_quotes, hourly_time_value)
    scored_inbound = score_and_rank_quotes(inbound_quotes, hourly_time_value)

    # Identifica o menor preço monomodal puro e single-carrier para calcular economia real
    cheapest_pure_air = float("inf")
    cheapest_pure_road = float("inf")
    cheapest_same_carrier_air = float("inf")
    cheapest_same_carrier_road = float("inf")

    for o in scored_outbound:
        for i in scored_inbound:
            o_arr = _get_val(o, "arrival_time")
            i_dep = _get_val(i, "departure_time")
            if o_arr and i_dep and (i_dep - o_arr).total_seconds() < min_stay_hours * 3600:
                continue

            o_modal = str(_get_val(o, "modal", "")).upper()
            i_modal = str(_get_val(i, "modal", "")).upper()
            o_carrier = str(_get_val(o, "carrier_name", "")).strip().upper()
            i_carrier = str(_get_val(i, "carrier_name", "")).strip().upper()
            total_price = _get_val(o, "total_effective_price", 0.0) + _get_val(i, "total_effective_price", 0.0)

            if "AIR" in o_modal and "AIR" in i_modal:
                if total_price < cheapest_pure_air:
                    cheapest_pure_air = total_price
                if o_carrier and i_carrier and o_carrier == i_carrier and total_price < cheapest_same_carrier_air:
                    cheapest_same_carrier_air = total_price

            if "ROAD" in o_modal and "ROAD" in i_modal:
                if total_price < cheapest_pure_road:
                    cheapest_pure_road = total_price
                if o_carrier and i_carrier and o_carrier == i_carrier and total_price < cheapest_same_carrier_road:
                    cheapest_same_carrier_road = total_price

    combinations: List[HackerFareCombination] = []

    for out_q in scored_outbound:
        out_arr = _get_val(out_q, "arrival_time")
        out_modal = str(_get_val(out_q, "modal", "")).upper()
        out_carrier = str(_get_val(out_q, "carrier_name", ""))
        out_service = str(_get_val(out_q, "service_class", "")).upper()
        out_price = float(_get_val(out_q, "total_effective_price", 0.0))
        out_score = float(_get_val(out_q, "trivio_score", 0.0))
        out_d2d = int(_get_val(out_q, "door_to_door_duration_minutes", 0))

        for in_q in scored_inbound:
            in_dep = _get_val(in_q, "departure_time")
            in_modal = str(_get_val(in_q, "modal", "")).upper()
            in_carrier = str(_get_val(in_q, "carrier_name", ""))
            in_service = str(_get_val(in_q, "service_class", "")).upper()
            in_price = float(_get_val(in_q, "total_effective_price", 0.0))
            in_score = float(_get_val(in_q, "trivio_score", 0.0))
            in_d2d = int(_get_val(in_q, "door_to_door_duration_minutes", 0))

            # Valida permanência mínima
            if out_arr and in_dep and (in_dep - out_arr).total_seconds() < min_stay_hours * 3600:
                continue

            comb_price = round(out_price + in_price, 2)
            comb_score = round(out_score + in_score, 2)
            comb_d2d = out_d2d + in_d2d

            # Classificação da Estratégia Hacker Fare
            is_hybrid = ("AIR" in out_modal and "ROAD" in in_modal) or ("ROAD" in out_modal and "AIR" in in_modal)
            is_split_carrier = (out_modal == in_modal) and (out_carrier.strip().upper() != in_carrier.strip().upper())

            if is_hybrid:
                strategy = "HYBRID_MODAL"
                benchmark = cheapest_pure_air if cheapest_pure_air < float("inf") else comb_price
                savings = round(max(0.0, benchmark - comb_price), 2)

                if "AIR" in out_modal:
                    hack_title = "Ida de Avião + Volta de Ônibus (Hacker Fare)"
                    hack_desc = (
                        f"Chegue rápido na ida ({out_carrier} {out_d2d // 60}h) e economize na volta "
                        f"com {in_carrier} ({in_service.replace('_', ' ').title()}). "
                        f"Economia de até R$ {savings:.2f} sobre o aéreo convencional de volta."
                    )
                else:
                    hack_title = "Ida de Ônibus + Volta de Avião (Hacker Fare)"
                    hack_desc = (
                        f"Economize na ida com {out_carrier} ({out_service.replace('_', ' ').title()}) "
                        f"e retorne com agilidade pelo ar via {in_carrier}. "
                        f"Economia estimada de R$ {savings:.2f}."
                    )
            elif is_split_carrier:
                strategy = "SPLIT_CARRIER"
                benchmark = cheapest_same_carrier_air if "AIR" in out_modal else cheapest_same_carrier_road
                if benchmark == float("inf"):
                    benchmark = comb_price
                savings = round(max(0.0, benchmark - comb_price), 2)
                hack_title = f"Ida com {out_carrier} + Volta com {in_carrier}"
                hack_desc = (
                    f"Compre trechos separados em operadoras diferentes para obter a melhor tarifa "
                    f"em cada sentido (economia de R$ {savings:.2f})."
                )
            else:
                strategy = "SAME_CARRIER"
                savings = 0.0
                hack_title = f"Ida e Volta com {out_carrier}"
                hack_desc = "Viagem monomodal com a mesma companhia em ambos os sentidos."

            combinations.append(
                HackerFareCombination(
                    outbound=out_q,
                    inbound=in_q,
                    strategy_type=strategy,
                    combined_effective_price=comb_price,
                    combined_trivio_score=comb_score,
                    combined_door_to_door_minutes=comb_d2d,
                    savings_vs_single_modal_brl=savings,
                    hack_title=hack_title,
                    hack_description=hack_desc,
                )
            )

    # Ordena pelo menor Trivio Score combinado (melhor custo-benefício)
    combinations.sort(key=lambda c: c.combined_trivio_score)
    return combinations[:top_n]
