"""Motor de Análise de Preços e Termômetro Histórico (Hopper Pattern).

Utiliza Polars para análise quantitativa e cálculo de percentis (P25, P50, P75)
sobre o histórico de cotações armazenadas no banco, classificando cada oferta:
- LOW: Preço abaixo do quartil inferior (ótimo momento de compra)
- MEDIUM: Preço na faixa típica de mercado
- HIGH: Preço inflacionado (alerta para flexibilizar datas ou modais)
- INSUFFICIENT_DATA: Poucas amostras coletadas para a rota (< min_samples)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import polars as pl
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.anac.airfares import ANAC_ALL_CLASSES, ANAC_SOURCE
from app.collectors.anac.airports import to_icao
from app.collectors.antt.monitriip import build_route_key
from app.models import FareQuote, ModalType, RouteMonthlyFare

logger = logging.getLogger("trivio.analyzers.thermometer")

MIN_SAMPLES_THRESHOLD = 5


@dataclass
class PriceThermometerResult:
    """Resultado da análise quantitativa de preço de uma cotação."""

    status: str                              # "LOW", "MEDIUM", "HIGH", "INSUFFICIENT_DATA"
    percentile: Optional[int]               # 1 a 99 (ou None se insuficiente)
    historical_median_brl: Optional[float]  # Mediana histórica (P50)
    p25_brl: Optional[float]                # Primeiro quartil
    p75_brl: Optional[float]                # Terceiro quartil
    sample_size: int                         # Quantidade de amostras consideradas
    difference_percentage: Optional[float]  # % de variação vs mediana (+15% ou -24%)
    recommendation: str                      # Recomendação amigável em português
    source: str = "coletas_trivio"          # 'antt_monitriip', 'anac_tarifas_domesticas' ou 'coletas_trivio'
    reference_period: Optional[str] = None  # Janela da série oficial (ex: '2024-09')

    def to_dict(self) -> Dict[str, Any]:
        """Formata o JSON conforme a especificação do Trivio route_metadata."""
        return {
            "status": self.status,
            "percentile": self.percentile,
            "historical_median_brl": self.historical_median_brl,
            "p25_brl": self.p25_brl,
            "p75_brl": self.p75_brl,
            "sample_size": self.sample_size,
            "difference_percentage": self.difference_percentage,
            "recommendation": self.recommendation,
            "source": self.source,
            "reference_period": self.reference_period,
        }


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _get_terminal_ids(obj: Any) -> Tuple[Optional[int], Optional[int]]:
    """Obtém os IDs de terminal do ORM (FareQuote) ou do route_metadata (StandardizedQuote enriquecida)."""
    meta = _get_val(obj, "route_metadata", {}) or {}
    if not isinstance(meta, dict):
        meta = {}
    orig_id = _get_val(obj, "origin_terminal_id") or meta.get("origin_terminal_id")
    dest_id = _get_val(obj, "destination_terminal_id") or meta.get("destination_terminal_id")
    return orig_id, dest_id


def evaluate_price_distribution(
    current_price: float,
    historical_prices: Sequence[float],
    min_samples: int = MIN_SAMPLES_THRESHOLD,
) -> PriceThermometerResult:
    """Analisa uma tarifa em relação à série histórica utilizando Polars."""
    if not historical_prices or len(historical_prices) == 0:
        return PriceThermometerResult(
            status="INSUFFICIENT_DATA",
            percentile=None,
            historical_median_brl=None,
            p25_brl=None,
            p75_brl=None,
            sample_size=0,
            difference_percentage=None,
            recommendation="Histórico tarifário insuficiente para esta rota. Coletando mais dados.",
        )

    # Converte para Polars Series limpando valores nulos ou inválidos
    series = pl.Series("price", [float(p) for p in historical_prices if p is not None and p > 0.0])
    sample_count = len(series)

    if sample_count < min_samples:
        median_val = round(float(series.median()), 2) if sample_count > 0 else None
        return PriceThermometerResult(
            status="INSUFFICIENT_DATA",
            percentile=None,
            historical_median_brl=median_val,
            p25_brl=None,
            p75_brl=None,
            sample_size=sample_count,
            difference_percentage=None,
            recommendation=(
                f"Amostragem histórica inicial ({sample_count} cotações). "
                "Monitorando variações para refinar o termômetro."
            ),
        )

    # Cálculos quantitativos via Polars
    p25 = round(float(series.quantile(0.25)), 2)
    median = round(float(series.median()), 2)
    p75 = round(float(series.quantile(0.75)), 2)

    # Cálculo da posição percentil exata (0 a 100)
    # Proporção de amostras estritamente menores que a tarifa atual
    strictly_lower = int((series < current_price).sum())
    percentile_float = (strictly_lower / sample_count) * 100.0
    percentile_int = max(1, min(99, int(round(percentile_float))))

    # Variação percentual em relação à mediana histórica
    if median > 0:
        diff_pct = round(((current_price - median) / median) * 100.0, 1)
    else:
        diff_pct = 0.0

    # Classificação do status e recomendação (Hopper Pattern)
    if current_price <= p25 or percentile_int <= 25:
        status = "LOW"
        diff_abs = abs(diff_pct)
        if diff_pct < -5.0:
            rec = (
                f"Preço {diff_abs:.0f}% abaixo da média histórica para esta rota. "
                "Excelente momento para compra."
            )
        else:
            rec = "Tarifa próxima às mínimas históricas. Excelente momento para compra."
    elif current_price > p75 or percentile_int >= 75:
        status = "HIGH"
        rec = (
            f"Preço {diff_pct:.0f}% acima da média histórica para esta rota (mediana R$ {median:.2f}). "
            "Se tiver flexibilidade, considere outros horários ou modais."
        )
    else:
        status = "MEDIUM"
        rec = f"Preço dentro da faixa padrão para esta rota (mediana histórica R$ {median:.2f})."

    return PriceThermometerResult(
        status=status,
        percentile=percentile_int,
        historical_median_brl=median,
        p25_brl=p25,
        p75_brl=p75,
        sample_size=sample_count,
        difference_percentage=diff_pct,
        recommendation=rec,
    )


# ==============================================================================
# Série mensal oficial (ANTT / Monitriip): percentis sobre meses de viagem
# ==============================================================================

MIN_OFFICIAL_MONTHS = 6
"""Mínimo de meses de viagem distintos para calcular percentis oficiais.

Abaixo disso a série é curta demais para os quartis significarem algo. O termômetro
então recorre ao histórico coletado pelo próprio Trivio, ou mostra INSUFFICIENT_DATA."""


def fetch_route_monthly_means(
    db: Session,
    origin_city: str,
    origin_state: Optional[str],
    destination_city: str,
    destination_state: Optional[str],
    service_class: str,
    modal: ModalType = ModalType.ROAD,
) -> List[Tuple[str, float, int]]:
    """Preço médio por mês de viagem de uma rota e classe: [(AAAA-MM, média, bilhetes)].

    Quando o mesmo mês de viagem aparece em mais de um arquivo de emissão, as médias
    são combinadas ponderando pela quantidade de bilhetes.
    """
    route_key = build_route_key(origin_city, origin_state, destination_city, destination_state)
    stmt = select(
        RouteMonthlyFare.trip_month,
        RouteMonthlyFare.mean_brl,
        RouteMonthlyFare.sample_size,
    ).where(
        RouteMonthlyFare.route_key == route_key,
        RouteMonthlyFare.service_class == service_class,
        RouteMonthlyFare.modal == modal,
    )
    try:
        rows = db.execute(stmt).all()
    except Exception as exc:
        logger.warning(
            f"Falha ao consultar a série mensal oficial ({type(exc).__name__}: {exc})."
        )
        return []

    by_month: Dict[str, List[Tuple[float, float]]] = {}
    for trip_month, mean, sample in rows:
        by_month.setdefault(trip_month, []).append((float(mean), float(sample)))

    series: List[Tuple[str, float, int]] = []
    for trip_month in sorted(by_month):
        pairs = by_month[trip_month]
        total = sum(weight for _, weight in pairs)
        if total > 0:
            mean = sum(value * weight for value, weight in pairs) / total
            series.append((trip_month, mean, int(round(total))))
    return series


def fetch_historical_prices_from_db(
    db: Session,
    origin_terminal_id: int,
    destination_terminal_id: int,
    modal: Optional[str] = None,
    service_class: Optional[str] = None,
    days_back: int = 90,
) -> List[float]:
    """Busca preços históricos de bilhetes já registrados para o mesmo corredor."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    stmt = (
        select(FareQuote.total_effective_price)
        .where(
            FareQuote.origin_terminal_id == origin_terminal_id,
            FareQuote.destination_terminal_id == destination_terminal_id,
            FareQuote.collected_at >= cutoff,
        )
    )

    if modal:
        modal_clean = str(modal).strip().upper()
        if "AIR" in modal_clean:
            stmt = stmt.where(FareQuote.modal == ModalType.AIR)
        elif "ROAD" in modal_clean:
            stmt = stmt.where(FareQuote.modal == ModalType.ROAD)

    if service_class:
        stmt = stmt.where(FareQuote.service_class == service_class)

    stmt = stmt.order_by(FareQuote.collected_at.desc()).limit(500)
    try:
        results = db.execute(stmt).scalars().all()
        return [float(p) for p in results if p is not None and p > 0.0]
    except Exception as exc:
        logger.warning(
            f"Falha ao consultar histórico de preços no banco ({type(exc).__name__}: {exc}). "
            "Degradando termômetro para INSUFFICIENT_DATA suavemente."
        )
        return []


def _thousands(value: int) -> str:
    """Inteiro com ponto de milhar, no padrão brasileiro: 135400 -> '135.400'."""
    return f"{int(value):,}".replace(",", ".")


def _ticket_price(quote: Any) -> float:
    """Preço do bilhete em si (tarifa + taxas), sem transfer urbano nem bagagem.

    É o que a série da ANTT mede: comparar o custo efetivo porta a porta contra
    tarifas de bilhete enviesaria toda cotação para 'cara'.
    """
    base = float(_get_val(quote, "base_price", 0.0) or 0.0)
    taxes = float(_get_val(quote, "taxes_price", 0.0) or 0.0)
    if base > 0.0:
        return round(base + taxes, 2)
    return float(_get_val(quote, "total_effective_price", 0.0) or 0.0)


def _evaluate_with_official_statistics(quote: Any, db: Session) -> Optional[PriceThermometerResult]:
    """Classifica a cotação contra a série oficial do seu modal.

    ROAD usa a ANTT (Monitriip); AIR usa a ANAC (tarifas domésticas). Devolve None,
    e o termômetro recorre ao histórico próprio, quando não há série suficiente.
    """
    modal = str(_get_val(quote, "modal", "")).upper()
    if modal == "AIR":
        return _evaluate_with_anac_statistics(quote, db)
    if "ROAD" in modal:   # inclui rotas híbridas, como antes
        return _evaluate_with_antt_statistics(quote, db)
    return None


def _evaluate_with_antt_statistics(quote: Any, db: Session) -> Optional[PriceThermometerResult]:
    """Classifica a cotação rodoviária contra a série mensal da ANTT da rota e classe.

    Devolve None quando a rota não tem município de origem e destino, não tem
    classe, ou tem menos de MIN_OFFICIAL_MONTHS meses de viagem.
    """

    meta = _get_val(quote, "route_metadata", {}) or {}
    if not isinstance(meta, dict):
        return None

    origin_city = meta.get("origin_city")
    destination_city = meta.get("destination_city")
    service_class = str(_get_val(quote, "service_class", "") or "")
    if not origin_city or not destination_city or not service_class:
        return None

    series = fetch_route_monthly_means(
        db=db,
        origin_city=str(origin_city),
        origin_state=meta.get("origin_state"),
        destination_city=str(destination_city),
        destination_state=meta.get("destination_state"),
        service_class=service_class,
    )
    if len(series) < MIN_OFFICIAL_MONTHS:
        return None

    means = [mean for _, mean, _ in series]
    result = evaluate_price_distribution(
        current_price=_ticket_price(quote),
        historical_prices=means,
        min_samples=MIN_OFFICIAL_MONTHS,
    )

    tickets = sum(sample for _, _, sample in series)
    result.source = "antt_monitriip"
    result.sample_size = tickets
    result.reference_period = f"{series[0][0]} a {series[-1][0]}"
    result.recommendation = (
        f"{result.recommendation} Base: {len(series)} meses de viagem, "
        f"{_thousands(tickets)} bilhetes (ANTT)."
    )
    return result


# ==============================================================================
# Série mensal oficial da ANAC (aéreo): percentis ponderados por assento
# ==============================================================================

ANAC_WINDOW_MONTHS = 12
"""Quantos meses de venda mais recentes da rota entram na régua aérea."""

MIN_ANAC_MONTHS = 3
"""Mínimo de meses com vendas na janela para a régua aérea classificar.

Bem abaixo dos 6 meses da ANTT porque aqui cada mês já traz a distribuição
completa das tarifas vendidas, não uma única média."""

MIN_ANAC_SEATS = 300
"""Mínimo de assentos vendidos na janela. Rotas mais finas oscilam demais."""


@dataclass
class AirFareBand:
    """Percentis ponderados de uma rota aérea, combinados sobre a janela de meses."""

    months: List[str]
    seats: int
    min_brl: float
    p10_brl: float
    p25_brl: float
    p50_brl: float
    p75_brl: float
    p90_brl: float
    max_brl: float

    def points(self) -> List[Tuple[float, float]]:
        """Pares (tarifa, percentil) para interpolar a posição de um preço."""
        return [
            (self.min_brl, 0.0), (self.p10_brl, 10.0), (self.p25_brl, 25.0), (self.p50_brl, 50.0),
            (self.p75_brl, 75.0), (self.p90_brl, 90.0), (self.max_brl, 100.0),
        ]


def _quote_airport_codes(quote: Any) -> Tuple[Optional[str], Optional[str]]:
    """Códigos ICAO de origem e destino da cotação, a partir do IATA que ela carrega."""
    meta = _get_val(quote, "route_metadata", {}) or {}
    if not isinstance(meta, dict):
        meta = {}
    origin_candidates = (
        meta.get("origin_iata"), _get_val(quote, "canonical_origin_id"), _get_val(quote, "origin_external_id"),
    )
    dest_candidates = (
        meta.get("destination_iata"), _get_val(quote, "canonical_destination_id"),
        _get_val(quote, "destination_external_id"),
    )
    origin = next((icao for icao in (to_icao(c) for c in origin_candidates) if icao), None)
    dest = next((icao for icao in (to_icao(c) for c in dest_candidates) if icao), None)
    return origin, dest


def fetch_air_fare_band(
    db: Session,
    origin_icao: str,
    destination_icao: str,
    window_months: int = ANAC_WINDOW_MONTHS,
) -> Optional[AirFareBand]:
    """Combina os percentis dos últimos `window_months` meses de venda da rota.

    Cada percentil é a média dos percentis mensais ponderada pelos assentos do
    mês. É uma aproximação do percentil da janela inteira (os microdados não são
    guardados), boa o suficiente para uma régua de caro/barato.
    """
    route_key = build_route_key(origin_icao, None, destination_icao, None)
    stmt = select(
        RouteMonthlyFare.trip_month,
        RouteMonthlyFare.sample_size,
        RouteMonthlyFare.min_brl,
        RouteMonthlyFare.p10_brl,
        RouteMonthlyFare.p25_brl,
        RouteMonthlyFare.p50_brl,
        RouteMonthlyFare.p75_brl,
        RouteMonthlyFare.p90_brl,
        RouteMonthlyFare.max_brl,
    ).where(
        RouteMonthlyFare.route_key == route_key,
        RouteMonthlyFare.modal == ModalType.AIR,
        RouteMonthlyFare.source == ANAC_SOURCE,
        RouteMonthlyFare.service_class == ANAC_ALL_CLASSES,
        RouteMonthlyFare.p50_brl.is_not(None),
    ).order_by(RouteMonthlyFare.trip_month.desc()).limit(window_months)

    try:
        rows = db.execute(stmt).all()
    except Exception as exc:
        logger.warning(f"Falha ao consultar a série aérea da ANAC ({type(exc).__name__}: {exc}).")
        return None

    rows = [row for row in rows if row.sample_size and row.sample_size > 0]
    if not rows:
        return None

    total = float(sum(row.sample_size for row in rows))

    def weighted(attr: str) -> float:
        return round(sum(float(getattr(row, attr)) * row.sample_size for row in rows) / total, 2)

    return AirFareBand(
        months=sorted(row.trip_month for row in rows),
        seats=int(total),
        min_brl=weighted("min_brl"),
        p10_brl=weighted("p10_brl"),
        p25_brl=weighted("p25_brl"),
        p50_brl=weighted("p50_brl"),
        p75_brl=weighted("p75_brl"),
        p90_brl=weighted("p90_brl"),
        max_brl=weighted("max_brl"),
    )


def _interpolate_percentile(price: float, points: List[Tuple[float, float]]) -> int:
    """Posição aproximada (1 a 99) de um preço na distribuição, por interpolação linear."""
    if price <= points[0][0]:
        return 1
    for (low_price, low_pct), (high_price, high_pct) in zip(points, points[1:]):
        if price <= high_price:
            if high_price <= low_price:
                return max(1, min(99, int(round(high_pct))))
            share = (price - low_price) / (high_price - low_price)
            return max(1, min(99, int(round(low_pct + share * (high_pct - low_pct)))))
    return 99


def evaluate_against_air_band(price: float, band: AirFareBand) -> PriceThermometerResult:
    """Classifica uma tarifa base contra a faixa de preços vendidos da rota."""
    percentile = _interpolate_percentile(price, band.points())
    median = band.p50_brl
    diff_pct = round(((price - median) / median) * 100.0, 1) if median > 0 else 0.0

    if price <= band.p25_brl:
        status = "LOW"
        rec = (
            f"Tarifa {abs(diff_pct):.0f}% abaixo da mediana do que se pagou nesta rota "
            f"(R$ {median:.2f}). Mais barata que {100 - percentile}% dos assentos vendidos: ótimo momento."
        )
    elif price > band.p75_brl:
        status = "HIGH"
        rec = (
            f"Tarifa {diff_pct:.0f}% acima da mediana do que se pagou nesta rota (R$ {median:.2f}). "
            "Se tiver flexibilidade, considere outras datas, aeroportos ou modais."
        )
    else:
        status = "MEDIUM"
        rec = f"Tarifa dentro da faixa usual desta rota (mediana do que se pagou: R$ {median:.2f})."

    return PriceThermometerResult(
        status=status,
        percentile=percentile,
        historical_median_brl=median,
        p25_brl=band.p25_brl,
        p75_brl=band.p75_brl,
        sample_size=band.seats,
        difference_percentage=diff_pct,
        recommendation=rec,
        source=ANAC_SOURCE,
        reference_period=f"{band.months[0]} a {band.months[-1]}",
    )


def _evaluate_with_anac_statistics(quote: Any, db: Session) -> Optional[PriceThermometerResult]:
    """Classifica a cotação aérea contra as tarifas vendidas na rota (ANAC).

    Compara só a tarifa base: o valor registrado na ANAC não inclui taxa de
    embarque nem bagagem. Devolve None quando o aeroporto não está no de-para,
    a cotação não tem tarifa base, ou a rota não tem meses e assentos suficientes.
    """
    origin_icao, dest_icao = _quote_airport_codes(quote)
    if not origin_icao or not dest_icao:
        return None

    base_price = float(_get_val(quote, "base_price", 0.0) or 0.0)
    if base_price <= 0.0:
        return None

    band = fetch_air_fare_band(db, origin_icao, dest_icao)
    if band is None or len(band.months) < MIN_ANAC_MONTHS or band.seats < MIN_ANAC_SEATS:
        return None

    result = evaluate_against_air_band(base_price, band)
    result.recommendation = (
        f"{result.recommendation} Base: {len(band.months)} meses de vendas, "
        f"{_thousands(band.seats)} assentos (ANAC; tarifa sem taxa de embarque)."
    )
    return result


def _is_antt_estimate(quote: Any) -> bool:
    """O preço da cotação já É a média da ANTT (estimativa): compará-lo com a própria
    série ANTT não diz nada, então essas cotações ficam sem termômetro."""
    meta = _get_val(quote, "route_metadata", {}) or {}
    return isinstance(meta, dict) and str(meta.get("price_basis", "")).startswith("antt_projection")


def _attach_thermometer(quote: Any, result: PriceThermometerResult) -> None:
    """Injeta o resultado do termômetro no route_metadata da cotação."""
    data = result.to_dict()
    if hasattr(quote, "route_metadata"):
        meta = getattr(quote, "route_metadata")
        if meta is None or not isinstance(meta, dict):
            meta = {}
            setattr(quote, "route_metadata", meta)
        meta["price_thermometer"] = data
    elif isinstance(quote, dict):
        if "route_metadata" not in quote or not isinstance(quote["route_metadata"], dict):
            quote["route_metadata"] = {}
        quote["route_metadata"]["price_thermometer"] = data


def enrich_quote_with_thermometer(
    quote: Any,
    db: Optional[Session] = None,
    historical_prices: Optional[Sequence[float]] = None,
    min_samples: int = MIN_SAMPLES_THRESHOLD,
) -> PriceThermometerResult:
    """Enriquece uma cotação individual com a análise do termômetro de preço."""
    price = float(_get_val(quote, "total_effective_price", 0.0) or _get_val(quote, "base_price", 0.0) or 0.0)

    if _is_antt_estimate(quote):
        return evaluate_price_distribution(current_price=price, historical_prices=[], min_samples=min_samples)

    # 1. Série oficial do modal, quando houver (ANTT no rodoviário, ANAC no aéreo):
    #    bilhetes realmente vendidos. Tem prioridade sobre o histórico próprio.
    if historical_prices is None and db is not None:
        official = _evaluate_with_official_statistics(quote, db)
        if official is not None:
            _attach_thermometer(quote, official)
            return official

    prices_to_use: Sequence[float] = []
    if historical_prices is not None:
        prices_to_use = historical_prices
    elif db is not None:
        orig_id, dest_id = _get_terminal_ids(quote)
        modal = str(_get_val(quote, "modal", ""))
        serv_cls = str(_get_val(quote, "service_class", ""))

        if orig_id and dest_id:
            prices_to_use = fetch_historical_prices_from_db(
                db=db,
                origin_terminal_id=orig_id,
                destination_terminal_id=dest_id,
                modal=modal,
                service_class=serv_cls,
            )

    result = evaluate_price_distribution(
        current_price=price,
        historical_prices=prices_to_use,
        min_samples=min_samples,
    )

    _attach_thermometer(quote, result)
    return result


def enrich_quotes_batch_with_thermometer(
    quotes: Sequence[Any],
    db: Optional[Session] = None,
    min_samples: int = MIN_SAMPLES_THRESHOLD,
) -> Sequence[Any]:
    """Enriquece em lote uma lista de cotações, agrupando buscas de histórico para performance."""
    if not quotes:
        return quotes

    # Cache de histórico por par de rota e modal
    corridor_cache: Dict[Tuple[int, int, str], List[float]] = {}

    for q in quotes:
        if _is_antt_estimate(q):
            continue   # preço já é a média da ANTT: sem termômetro

        # 1. Série oficial do modal tem prioridade (ANTT/ANAC: preço transacionado)
        if db is not None:
            official = _evaluate_with_official_statistics(q, db)
            if official is not None:
                _attach_thermometer(q, official)
                continue

        orig_id, dest_id = _get_terminal_ids(q)
        modal = str(_get_val(q, "modal", "")).upper()

        historical_prices: List[float] = []
        if db is not None and orig_id and dest_id:
            cache_key = (orig_id, dest_id, modal)
            if cache_key not in corridor_cache:
                corridor_cache[cache_key] = fetch_historical_prices_from_db(
                    db=db,
                    origin_terminal_id=orig_id,
                    destination_terminal_id=dest_id,
                    modal=modal,
                )
            historical_prices = corridor_cache[cache_key]

        enrich_quote_with_thermometer(
            quote=q,
            db=None,  # Já buscado pelo cache
            historical_prices=historical_prices,
            min_samples=min_samples,
        )

    return quotes
