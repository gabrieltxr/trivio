"""Série mensal de preços por rota a partir do Monitriip (ANTT).

Cada arquivo mensal traz, por rota, classe de serviço, tipo de gratuidade, mês de
viagem e mês de emissão, a média de preço e a quantidade de bilhetes do grupo.

Aqui transformamos isso em uma linha por (rota, classe, mês de viagem, mês de
emissão), ponderada pela quantidade de bilhetes. O Termômetro de Preço calcula os
percentis sobre a série de meses de viagem de cada rota (ver price_thermometer.py),
e só classifica quando há meses suficientes.

Regras aplicadas:
  - só a tarifa normal (sem desconto) entra: é o preço que um passageiro comum paga;
  - viagens com mês anterior ao mês de emissão são descartadas (inconsistência da fonte);
  - o mês de emissão identifica o arquivo; o mês de viagem é a chave da série.
"""

from dataclasses import dataclass
import logging
from typing import Dict, List, Optional, Tuple

import polars as pl

from app.collectors.antt.monitriip import (
    MonitriipSchema,
    _parse_brazilian_money,
    _split_place_and_state,
    build_route_key,
    detect_monitriip_schema,
    normalize_place,
)
from app.collectors.road.taxonomy import normalize_bus_service_class

logger = logging.getLogger("trivio.collectors.antt.monthly")

TARIFA_NORMAL = "Tarifa Normal - sem desconto"


@dataclass
class RouteMonthlyAggregate:
    """Preço médio ponderado de uma rota, classe e mês de viagem, num mês de emissão."""

    route_key: str
    origin_city: str
    origin_state: Optional[str]
    destination_city: str
    destination_state: Optional[str]
    service_class: str
    trip_month: str        # 'AAAA-MM'
    emission_period: str   # 'AAAA-MM'
    sample_size: int       # bilhetes
    mean_brl: float
    min_brl: float
    max_brl: float


def _read_frame(path: str, schema: MonitriipSchema) -> pl.DataFrame:
    return pl.read_csv(
        path,
        separator=schema.separator,
        encoding=schema.encoding,
        infer_schema_length=0,       # tudo como texto; convertemos explicitamente
        ignore_errors=True,
        truncate_ragged_lines=True,
    )


def _month_parts(column: str) -> Tuple[pl.Expr, pl.Expr]:
    """Separa 'MM/AAAA' em (ano, mês) como inteiros."""
    parts = pl.col(column).cast(pl.Utf8).str.strip_chars().str.split("/")
    month = parts.list.get(0, null_on_oob=True).cast(pl.Int32, strict=False)
    year = parts.list.get(1, null_on_oob=True).cast(pl.Int32, strict=False)
    return year, month


def _month_index(column: str) -> pl.Expr:
    """Índice cronológico do mês (ano * 12 + mês), para comparar datas."""
    year, month = _month_parts(column)
    return year * 12 + month


def _month_label(column: str) -> pl.Expr:
    """Rótulo 'AAAA-MM' a partir de 'MM/AAAA'."""
    year, month = _month_parts(column)
    return pl.concat_str([year.cast(pl.Utf8), pl.lit("-"), month.cast(pl.Utf8).str.zfill(2)])


def _prepare_tickets(
    path: str,
    schema: MonitriipSchema,
    gratuity_filter: Optional[str],
    max_fare_brl: float,
) -> pl.DataFrame:
    frame = _read_frame(path, schema)

    origin_col = schema.column("origin")
    dest_col = schema.column("destination")
    fare_col = schema.column("fare")

    if schema.column("origin_state"):
        origin_city = pl.col(origin_col).cast(pl.Utf8).str.strip_chars()
        origin_state = pl.col(schema.column("origin_state")).cast(pl.Utf8).str.strip_chars()
    else:
        origin_city, origin_state = _split_place_and_state(origin_col)

    if schema.column("destination_state"):
        dest_city = pl.col(dest_col).cast(pl.Utf8).str.strip_chars()
        dest_state = pl.col(schema.column("destination_state")).cast(pl.Utf8).str.strip_chars()
    else:
        dest_city, dest_state = _split_place_and_state(dest_col)

    service_col = schema.column("service_class")
    service_expr = (
        pl.col(service_col).cast(pl.Utf8).str.strip_chars() if service_col else pl.lit("CONVENCIONAL")
    )

    gratuity_col = schema.column("gratuity")
    gratuity_expr = (
        pl.col(gratuity_col).cast(pl.Utf8).str.strip_chars() if gratuity_col else pl.lit(None, dtype=pl.Utf8)
    )

    quantity_col = schema.column("quantity")
    quantity_expr = (
        pl.col(quantity_col).cast(pl.Float64, strict=False).fill_null(0.0) if quantity_col else pl.lit(1.0)
    )

    trip_col = schema.column("trip_month")
    issue_col = schema.column("issue_month")

    prepared = frame.select(
        origin_city.alias("_origin_city"),
        origin_state.alias("_origin_state"),
        dest_city.alias("_dest_city"),
        dest_state.alias("_dest_state"),
        service_expr.alias("_service_raw"),
        gratuity_expr.alias("_gratuity"),
        _parse_brazilian_money(fare_col).alias("_fare"),
        quantity_expr.alias("_qty"),
        _month_label(trip_col).alias("_trip"),
        _month_label(issue_col).alias("_emission"),
        (_month_index(trip_col) - _month_index(issue_col)).alias("_gap"),
    ).filter(
        pl.col("_fare").is_not_null()
        & (pl.col("_fare") > 0.0)
        & (pl.col("_fare") <= max_fare_brl)
        & (pl.col("_qty") > 0.0)
        & pl.col("_service_raw").is_not_null()          # sem classe, não há como comparar
        & (pl.col("_service_raw").str.len_chars() > 0)
        & pl.col("_trip").is_not_null()
        & pl.col("_emission").is_not_null()
        & (pl.col("_gap") >= 0)              # viagem não pode ser anterior à emissão
        & pl.col("_origin_city").is_not_null()
        & pl.col("_dest_city").is_not_null()
        & (pl.col("_origin_city").str.len_chars() > 0)
        & (pl.col("_dest_city").str.len_chars() > 0)
    )

    if gratuity_filter and gratuity_col:
        prepared = prepared.filter(pl.col("_gratuity") == gratuity_filter)

    return prepared


def aggregate_monthly_fares(
    path: str,
    schema: Optional[MonitriipSchema] = None,
    gratuity_filter: Optional[str] = TARIFA_NORMAL,
    max_fare_brl: float = 5000.0,
) -> List[RouteMonthlyAggregate]:
    """Lê um CSV mensal do Monitriip e devolve uma linha por rota, classe e mês de viagem.

    O preço de cada linha é a média ponderada pela quantidade de bilhetes. Quando o
    mesmo grupo aparece mais de uma vez, as médias são combinadas pelo mesmo critério.

    Exige as colunas de mês de viagem e de emissão. Sem elas não há série temporal.
    """
    schema = schema or detect_monitriip_schema(path)
    if not schema.is_usable:
        raise ValueError(
            "O arquivo não traz as colunas mínimas (origem, destino e tarifa).\n" + schema.describe()
        )
    if not schema.column("trip_month") or not schema.column("issue_month"):
        raise ValueError(
            "O arquivo não tem as colunas de mês de viagem e de emissão, "
            "necessárias para montar a série mensal."
        )

    prepared = _prepare_tickets(path, schema, gratuity_filter, max_fare_brl)
    if prepared.height == 0:
        logger.warning(f"[monthly] Nenhuma linha utilizável em {path}.")
        return []

    prepared = prepared.with_columns(
        pl.col("_service_raw")
        .map_elements(normalize_bus_service_class, return_dtype=pl.Utf8)
        .alias("_service_class"),
        pl.col("_origin_city").map_elements(normalize_place, return_dtype=pl.Utf8).alias("_origin_norm"),
        pl.col("_dest_city").map_elements(normalize_place, return_dtype=pl.Utf8).alias("_dest_norm"),
    )

    groups: Dict[tuple, List[Tuple[float, float]]] = {}
    for row in prepared.select(
        ["_origin_norm", "_origin_state", "_dest_norm", "_dest_state", "_service_class",
         "_trip", "_emission", "_fare", "_qty"]
    ).to_dicts():
        origin_state_value = (row["_origin_state"] or "").strip().upper() or None
        dest_state_value = (row["_dest_state"] or "").strip().upper() or None
        key = (
            row["_origin_norm"], origin_state_value,
            row["_dest_norm"], dest_state_value,
            row["_service_class"], row["_trip"], row["_emission"],
        )
        groups.setdefault(key, []).append((float(row["_fare"]), float(row["_qty"])))

    aggregates: List[RouteMonthlyAggregate] = []
    for key, pairs in groups.items():
        origin_norm, origin_state_value, dest_norm, dest_state_value, service, trip, emission = key
        total = sum(q for _, q in pairs)
        mean = sum(f * q for f, q in pairs) / total
        fares = [f for f, _ in pairs]
        aggregates.append(
            RouteMonthlyAggregate(
                route_key=build_route_key(origin_norm, origin_state_value, dest_norm, dest_state_value),
                origin_city=origin_norm,
                origin_state=origin_state_value,
                destination_city=dest_norm,
                destination_state=dest_state_value,
                service_class=service,
                trip_month=trip,
                emission_period=emission,
                sample_size=int(round(total)),
                mean_brl=round(mean, 2),
                min_brl=round(min(fares), 2),
                max_brl=round(max(fares), 2),
            )
        )

    logger.info(
        f"[monthly] {path}: {len(groups)} grupos -> {len(aggregates)} linhas de série mensal."
    )
    return aggregates


def check_date_consistency(path: str, schema: Optional[MonitriipSchema] = None) -> Optional[dict]:
    """Conta linhas em que o mês de viagem é anterior ao mês de emissão do bilhete.

    Devolve None se o arquivo não tiver as duas colunas de mês.
    """
    schema = schema or detect_monitriip_schema(path)
    trip_col = schema.column("trip_month")
    issue_col = schema.column("issue_month")
    if not trip_col or not issue_col:
        return None

    frame = _read_frame(path, schema)
    checked = frame.select(
        _month_index(trip_col).alias("trip"),
        _month_index(issue_col).alias("issue"),
    ).drop_nulls()

    total = checked.height
    before = checked.filter(pl.col("trip") < pl.col("issue")).height
    return {
        "total_linhas": total,
        "viagem_antes_da_emissao": before,
        "proporcao": round(before / total, 4) if total else 0.0,
    }
