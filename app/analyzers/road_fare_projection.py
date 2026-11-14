"""Projeção de tarifa rodoviária a partir da série ANTT/Monitriip.

A série traz bilhetes efetivamente vendidos, agregados por rota, classe, mês de
viagem e mês de emissão (venda). Não há preço anunciado nem disponibilidade: é o
que foi pago. A projeção resume essa série em:

- ``estimate_brl``: média ponderada pelo número de bilhetes;
- ``low_brl`` / ``high_brl``: faixa de variação entre os meses de venda;
- ``confidence``: alta, média ou baixa, pelo total de bilhetes da amostra.

Duas situações:

1. Há vendas para o próprio mês de viagem: usa todas elas (basis
   ``trip_month``), que é a informação mais específica disponível.
2. Não há: usa as vendas do mês de emissão dos últimos meses, sem sazonalidade
   (basis ``recent_level``). Isso mostra o nível atual da rota, não o preço da data.

Sem ajuste sazonal, a projeção para meses distantes é só o nível recente.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.antt.monitriip import build_route_key
from app.models import ModalType, RouteMonthlyFare

RECENT_EMISSION_MONTHS = 3          # quantos meses de venda formam o "nível recente"
CONFIDENCE_HIGH_SAMPLE = 200        # bilhetes a partir dos quais a confiança é alta
CONFIDENCE_MEDIUM_SAMPLE = 30       # abaixo disso, a amostra é considerada baixa

CONFIDENCE_HIGH = "alta"
CONFIDENCE_MEDIUM = "média"
CONFIDENCE_LOW = "baixa"
CONFIDENCE_NONE = "sem_dados"

BASIS_TRIP_MONTH = "trip_month"
BASIS_RECENT_LEVEL = "recent_level"
BASIS_NONE = "none"


@dataclass(frozen=True)
class FareObservation:
    """Uma linha da série ANTT (já filtrada por rota e classe)."""

    emission_period: str   # 'AAAA-MM', mês da venda
    trip_month: str        # 'AAAA-MM', mês da viagem
    sample_size: int       # bilhetes
    mean_brl: float        # tarifa média paga


@dataclass(frozen=True)
class RoadFareProjection:
    route_key: str
    service_class: str
    trip_month: str
    estimate_brl: Optional[float]
    low_brl: Optional[float]
    high_brl: Optional[float]
    sample_size: int
    confidence: str
    basis: str
    notice: str


def _weighted_mean(values: Sequence[float], weights: Sequence[int]) -> float:
    total = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / total


def _weighted_std(values: Sequence[float], weights: Sequence[int], mean: float) -> float:
    total = sum(weights)
    variance = sum(w * (v - mean) ** 2 for v, w in zip(values, weights)) / total
    return math.sqrt(variance)


def _confidence(sample_size: int) -> str:
    if sample_size >= CONFIDENCE_HIGH_SAMPLE:
        return CONFIDENCE_HIGH
    if sample_size >= CONFIDENCE_MEDIUM_SAMPLE:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def _summarize(
    observations: Sequence[FareObservation],
    route_key: str,
    service_class: str,
    trip_month: str,
    basis: str,
) -> RoadFareProjection:
    """Resume um conjunto de observações em estimativa, faixa e confiança."""
    means = [o.mean_brl for o in observations]
    weights = [o.sample_size for o in observations]
    estimate = _weighted_mean(means, weights)
    spread = _weighted_std(means, weights, estimate)
    sample = sum(weights)
    confidence = _confidence(sample)

    if basis == BASIS_TRIP_MONTH:
        periods = sorted({o.emission_period for o in observations})
        origin_note = (
            f"vendas da própria viagem, emitidas de {periods[0]} a {periods[-1]} "
            f"({len(periods)} mês(es) de venda)"
        )
    else:
        origin_note = (
            f"nível recente: vendas dos últimos {RECENT_EMISSION_MONTHS} meses de emissão, "
            "sem ajuste sazonal para a data"
        )
    notice = origin_note
    periods_count = len({o.emission_period for o in observations})
    if periods_count >= 2:
        low, high = round(max(0.0, estimate - spread), 2), round(estimate + spread, 2)
    else:
        # Com um só mês de venda não há variação observada: a faixa seria um ponto falso.
        low = high = None
        notice += ". Faixa indisponível: só há um mês de venda"
    if confidence == CONFIDENCE_LOW:
        notice += ". Amostra pequena: use como referência, não como preço."

    return RoadFareProjection(
        route_key=route_key,
        service_class=service_class,
        trip_month=trip_month,
        estimate_brl=round(estimate, 2),
        low_brl=low,
        high_brl=high,
        sample_size=sample,
        confidence=confidence,
        basis=basis,
        notice=notice,
    )


def project_road_fare(
    observations: Iterable[FareObservation],
    route_key: str,
    service_class: str,
    trip_month: str,
) -> RoadFareProjection:
    """Projeta a tarifa de uma rota/classe para um mês de viagem.

    ``observations`` deve conter só a rota e a classe pedidas.
    """
    rows = [o for o in observations if o.sample_size > 0]
    if not rows:
        return RoadFareProjection(
            route_key=route_key,
            service_class=service_class,
            trip_month=trip_month,
            estimate_brl=None,
            low_brl=None,
            high_brl=None,
            sample_size=0,
            confidence=CONFIDENCE_NONE,
            basis=BASIS_NONE,
            notice="Sem vendas registradas para esta rota e classe na série ANTT.",
        )

    same_trip = [o for o in rows if o.trip_month == trip_month]
    if same_trip:
        return _summarize(same_trip, route_key, service_class, trip_month, BASIS_TRIP_MONTH)

    recent_periods = sorted({o.emission_period for o in rows}, reverse=True)[:RECENT_EMISSION_MONTHS]
    recent = [o for o in rows if o.emission_period in recent_periods]
    return _summarize(recent, route_key, service_class, trip_month, BASIS_RECENT_LEVEL)


# ==============================================================================
# Acesso ao banco: única porta de entrada da série ANTT para a projeção
# ==============================================================================

RoadSeriesCache = Dict[Tuple[str, str], List[FareObservation]]
"""Série ANTT já lida, por (route_key, classe). Vale para uma busca: a projeção do ônibus,
a referência histórica e a conexão leem a mesma série, e com o cache ela vai ao banco uma vez."""


def fetch_fare_observations(
    db: Session,
    route_key: str,
    service_class: str,
    cache: Optional[RoadSeriesCache] = None,
) -> List[FareObservation]:
    """Linhas da série ANTT de uma rota e classe, prontas para a projeção."""
    key = (route_key, service_class)
    if cache is not None and key in cache:
        return cache[key]
    rows = db.execute(
        select(
            RouteMonthlyFare.emission_period,
            RouteMonthlyFare.trip_month,
            RouteMonthlyFare.sample_size,
            RouteMonthlyFare.mean_brl,
        ).where(
            RouteMonthlyFare.modal == ModalType.ROAD,
            RouteMonthlyFare.route_key == route_key,
            RouteMonthlyFare.service_class == service_class,
        )
    ).all()
    observations = [
        FareObservation(emission_period=e, trip_month=t, sample_size=int(n or 0), mean_brl=float(m))
        for e, t, n, m in rows
    ]
    if cache is not None:
        cache[key] = observations
    return observations


def monthly_means(observations: Iterable[FareObservation]) -> List[Tuple[str, float, int]]:
    """Média por mês de viagem, ponderada por bilhetes: [(AAAA-MM, média, bilhetes)].

    O mesmo mês de viagem aparece em vários meses de emissão: as médias são combinadas
    pela quantidade de bilhetes. Mês sem bilhete fica de fora.
    """
    by_month: Dict[str, List[Tuple[float, int]]] = {}
    for o in observations:
        by_month.setdefault(o.trip_month, []).append((o.mean_brl, o.sample_size))
    series: List[Tuple[str, float, int]] = []
    for trip_month in sorted(by_month):
        pairs = by_month[trip_month]
        total = sum(weight for _, weight in pairs)
        if total > 0:
            series.append((trip_month, sum(v * w for v, w in pairs) / total, int(total)))
    return series


def project_route_fare(
    db: Session,
    origin_city: str,
    origin_state: Optional[str],
    destination_city: str,
    destination_state: Optional[str],
    service_class: str,
    trip_month: str,
    cache: Optional[RoadSeriesCache] = None,
) -> RoadFareProjection:
    """Projeção de uma rota/classe lendo a série ANTT do banco (ou do cache da busca).

    É a função usada pelo endpoint, pela referência rodoviária da busca e pelas
    estimativas de ônibus: todos os valores rodoviários saem do mesmo cálculo.
    """
    route_key = build_route_key(origin_city, origin_state, destination_city, destination_state)
    observations = fetch_fare_observations(db, route_key, service_class, cache)
    return project_road_fare(observations, route_key, service_class, trip_month)


def projection_to_dict(projection: RoadFareProjection) -> Dict[str, Any]:
    return asdict(projection)
