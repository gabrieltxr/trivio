"""Série mensal de tarifas aéreas domésticas a partir dos microdados da ANAC.

Cada arquivo mensal traz uma linha por (companhia, aeroporto de origem, aeroporto
de destino, valor de tarifa), com a quantidade de assentos vendidos naquele
valor. São as passagens efetivamente comercializadas ao público adulto,
registradas pelas companhias conforme a Resolução ANAC nº 437/2017.

Dois formatos de cabeçalho já foram publicados:

    ANO;MES;EMPRESA;ORIGEM;DESTINO;TARIFA;ASSENTOS                      (até ~2019)
    nr_ano_referencia;nr_mes_referencia;sg_empresa_icao;sg_icao_origem;
    sg_icao_destino;nr_tarifa;nr_assentos                              (recente)

e o separador decimal varia entre arquivos ('800,0' e '800.0').

Regras aplicadas:
  - o mês de referência é o mês da VENDA, não o do voo. A série é, portanto,
    "quanto se pagou pelos bilhetes vendidos em cada mês";
  - a tarifa NÃO inclui taxa de embarque nem serviços opcionais (bagagem,
    assento). A comparação com uma cotação deve usar só a tarifa base;
  - as companhias são somadas: a régua responde "esse preço é caro para esta
    rota?", independentemente de quem opera;
  - além da média ponderada, guardamos os percentis ponderados por assento
    (P10, P25, P50, P75, P90), porque a dispersão dentro de um mês é grande
    (no mesmo mês, CGH->SDU vendeu de R$ 100 a R$ 4.000).
"""

from dataclasses import dataclass, field
import logging
from typing import Dict, List, Optional

import polars as pl

from app.collectors.antt.monitriip import _parse_brazilian_money, build_route_key, normalize_column_name

logger = logging.getLogger("trivio.collectors.anac.airfares")

ANAC_SOURCE = "anac_tarifas_domesticas"

# A ANAC não distingue cabine nem família tarifária: a série cobre todas.
ANAC_ALL_CLASSES = "TODAS"

QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)

ANAC_COLUMN_ALIASES: Dict[str, List[str]] = {
    "year": ["nr_ano_referencia", "ano"],
    "month": ["nr_mes_referencia", "mes"],
    "carrier": ["sg_empresa_icao", "empresa"],
    "origin": ["sg_icao_origem", "origem"],
    "destination": ["sg_icao_destino", "destino"],
    "fare": ["nr_tarifa", "tarifa"],
    "seats": ["nr_assentos", "assentos"],
}

REQUIRED_FIELDS = ("year", "month", "origin", "destination", "fare", "seats")


@dataclass
class ANACSchema:
    """Mapeamento entre os campos lógicos e as colunas reais de um arquivo da ANAC."""

    columns: Dict[str, str] = field(default_factory=dict)
    header: List[str] = field(default_factory=list)
    separator: str = ";"
    encoding: str = "latin-1"

    def column(self, logical: str) -> Optional[str]:
        return self.columns.get(logical)

    @property
    def missing(self) -> List[str]:
        return [name for name in REQUIRED_FIELDS if name not in self.columns]

    @property
    def is_usable(self) -> bool:
        return not self.missing

    def describe(self) -> str:
        lines = [f"  {logical:<12} -> {self.columns.get(logical, '— ausente —')}" for logical in ANAC_COLUMN_ALIASES]
        if self.missing:
            lines.append(f"  Campos obrigatórios ausentes: {', '.join(self.missing)}")
        return "\n".join(lines)


def detect_anac_schema(path: str) -> ANACSchema:
    """Lê só o cabeçalho do arquivo e identifica as colunas pelos apelidos conhecidos."""
    with open(path, "rb") as handle:
        first_line = handle.readline()

    # Os arquivos são ASCII na prática; latin-1 nunca falha na decodificação.
    text = first_line.decode("utf-8-sig", errors="ignore") or first_line.decode("latin-1")
    separator = ";" if text.count(";") >= text.count(",") else ","
    header = [col.strip() for col in text.strip().split(separator)]

    normalized = {normalize_column_name(col): col for col in header}
    columns: Dict[str, str] = {}
    for logical, aliases in ANAC_COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                columns[logical] = normalized[alias]
                break

    return ANACSchema(columns=columns, header=header, separator=separator)


@dataclass
class AirRouteMonthlyAggregate:
    """Tarifas vendidas numa rota (aeroporto a aeroporto) num mês de venda."""

    route_key: str          # 'SBSP|SBRJ'
    origin_icao: str
    destination_icao: str
    reference_month: str    # 'AAAA-MM' (mês da venda)
    seats: int
    mean_brl: float
    min_brl: float
    max_brl: float
    p10_brl: float
    p25_brl: float
    p50_brl: float
    p75_brl: float
    p90_brl: float
    carriers: List[str] = field(default_factory=list)


def _quantile_alias(q: float) -> str:
    return f"p{int(round(q * 100))}"


def aggregate_air_monthly_fares(
    path: str,
    schema: Optional[ANACSchema] = None,
    max_fare_brl: float = 20000.0,
    min_seats: int = 1,
) -> List[AirRouteMonthlyAggregate]:
    """Lê um CSV mensal da ANAC e devolve uma linha por rota e mês de venda.

    Os percentis são ponderados por assento: o P50 é a tarifa abaixo da qual
    foram vendidos metade dos assentos da rota naquele mês.
    """
    schema = schema or detect_anac_schema(path)
    if not schema.is_usable:
        raise ValueError(
            "O arquivo não traz as colunas mínimas dos microdados de tarifas da ANAC.\n" + schema.describe()
        )

    frame = pl.read_csv(
        path,
        separator=schema.separator,
        encoding=schema.encoding,
        infer_schema_length=0,       # tudo como texto; convertemos explicitamente
        ignore_errors=True,
        truncate_ragged_lines=True,
    )

    carrier_col = schema.column("carrier")
    carrier_expr = (
        pl.col(carrier_col).cast(pl.Utf8).str.strip_chars().str.to_uppercase()
        if carrier_col else pl.lit(None, dtype=pl.Utf8)
    )

    year = pl.col(schema.column("year")).cast(pl.Utf8).str.strip_chars().cast(pl.Int32, strict=False)
    month = pl.col(schema.column("month")).cast(pl.Utf8).str.strip_chars().cast(pl.Int32, strict=False)

    tickets = frame.select(
        pl.col(schema.column("origin")).cast(pl.Utf8).str.strip_chars().str.to_uppercase().alias("origin"),
        pl.col(schema.column("destination")).cast(pl.Utf8).str.strip_chars().str.to_uppercase().alias("destination"),
        carrier_expr.alias("carrier"),
        year.alias("year"),
        month.alias("month"),
        _parse_brazilian_money(schema.column("fare")).alias("fare"),
        pl.col(schema.column("seats")).cast(pl.Utf8).str.strip_chars()
        .cast(pl.Float64, strict=False).alias("seats"),
    ).filter(
        pl.col("origin").is_not_null()
        & pl.col("destination").is_not_null()
        & (pl.col("origin").str.len_chars() == 4)
        & (pl.col("destination").str.len_chars() == 4)
        & (pl.col("origin") != pl.col("destination"))
        & pl.col("year").is_not_null()
        & pl.col("month").is_between(1, 12)
        & pl.col("fare").is_not_null()
        & (pl.col("fare") > 0.0)
        & (pl.col("fare") <= max_fare_brl)
        & pl.col("seats").is_not_null()
        & (pl.col("seats") > 0.0)
    ).with_columns(
        pl.concat_str([
            pl.col("year").cast(pl.Utf8), pl.lit("-"), pl.col("month").cast(pl.Utf8).str.zfill(2),
        ]).alias("reference_month")
    )

    if tickets.height == 0:
        logger.warning(f"[anac] Nenhuma linha utilizável em {path}.")
        return []

    keys = ["origin", "destination", "reference_month"]

    # Fração acumulada de assentos dentro de cada grupo, com as tarifas em ordem crescente.
    # O percentil ponderado q é a menor tarifa cuja fração acumulada alcança q.
    ranked = tickets.sort(keys + ["fare"]).with_columns(
        (pl.col("seats").cum_sum().over(keys) / pl.col("seats").sum().over(keys)).alias("_cum_share")
    )

    grouped = ranked.group_by(keys).agg(
        pl.col("seats").sum().alias("seats"),
        ((pl.col("fare") * pl.col("seats")).sum() / pl.col("seats").sum()).alias("mean"),
        pl.col("fare").min().alias("min"),
        pl.col("fare").max().alias("max"),
        pl.col("carrier").drop_nulls().unique().sort().alias("carriers"),
        *[
            pl.col("fare").filter(pl.col("_cum_share") >= q - 1e-9).min().alias(_quantile_alias(q))
            for q in QUANTILES
        ],
    ).filter(pl.col("seats") >= min_seats)

    aggregates = [
        AirRouteMonthlyAggregate(
            route_key=build_route_key(row["origin"], None, row["destination"], None),
            origin_icao=row["origin"],
            destination_icao=row["destination"],
            reference_month=row["reference_month"],
            seats=int(round(row["seats"])),
            mean_brl=round(float(row["mean"]), 2),
            min_brl=round(float(row["min"]), 2),
            max_brl=round(float(row["max"]), 2),
            p10_brl=round(float(row["p10"]), 2),
            p25_brl=round(float(row["p25"]), 2),
            p50_brl=round(float(row["p50"]), 2),
            p75_brl=round(float(row["p75"]), 2),
            p90_brl=round(float(row["p90"]), 2),
            carriers=list(row["carriers"] or []),
        )
        for row in grouped.sort(keys).to_dicts()
    ]

    logger.info(f"[anac] {path}: {tickets.height} linhas -> {len(aggregates)} rotas/mês.")
    return aggregates
