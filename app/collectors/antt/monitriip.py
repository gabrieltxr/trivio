"""Leitura e agregação dos CSVs de bilhetes do Monitriip (ANTT).

Os arquivos `venda_passagem_MM_AAAA.csv` trazem os bilhetes efetivamente
comercializados no transporte rodoviário interestadual e internacional de
passageiros. São ~20 MB por mês, publicados sob Creative Commons Attribution.

Em vez de persistir cada bilhete (milhões de linhas), agregamos por corredor e
classe de serviço, guardando os percentis que o Termômetro de Preço consome.

Sobre o esquema: a ANTT já alterou nomes de coluna entre safras, e os arquivos
costumam vir em `;` com acentuação Latin-1. Por isso o módulo **detecta** o
esquema em vez de assumi-lo: `detect_monitriip_schema` casa os campos lógicos
contra uma lista de apelidos conhecidos e informa o que não encontrou. Use
`scripts/import_antt_fares.py --inspect` para ver o cabeçalho real de um arquivo.
"""

from dataclasses import dataclass, field
import csv
import logging
from typing import Dict, List, Optional, Tuple

import polars as pl

from app.text import strip_accents


logger = logging.getLogger("trivio.collectors.antt.monitriip")

# Campo lógico -> apelidos possíveis de coluna (já normalizados)
MONITRIIP_COLUMN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "origin": (
        "origem", "municipio_origem", "cidade_origem", "nome_municipio_origem",
        "origem_municipio", "local_origem", "ponto_origem", "descricao_origem",
    ),
    "origin_state": (
        "uf_origem", "estado_origem", "sigla_uf_origem", "origem_uf",
    ),
    "destination": (
        "destino", "municipio_destino", "cidade_destino", "nome_municipio_destino",
        "destino_municipio", "local_destino", "ponto_destino", "descricao_destino",
    ),
    "destination_state": (
        "uf_destino", "estado_destino", "sigla_uf_destino", "destino_uf",
    ),
    "fare": (
        "valor_tarifa", "tarifa", "valor_bilhete", "valor_passagem", "preco_passagem",
        "valor_total", "valor_pago", "preco", "valor",
    ),
    "service_class": (
        "tipo_servico", "tipo_de_servico", "servico", "classe", "classe_servico",
        "categoria", "nome_servico",
    ),
    "carrier": (
        "empresa", "nome_empresa", "razao_social", "transportadora",
        "permissionaria", "autorizataria", "nome_fantasia",
    ),
    "travel_date": (
        "data_viagem", "data_da_viagem", "data_embarque", "data_partida",
        "data_venda", "data_emissao",
    ),
    # Campos do Monitriip agregado (ver cabeçalho real em 2024-10)
    "trip_month": ("mes_viagem", "mes_da_viagem"),
    "issue_month": ("mes_emissao_bilhete", "mes_emissao"),
    "gratuity": ("tipo_gratuidade", "tipo_de_gratuidade", "gratuidade"),
    "quantity": ("quantidade_bilhetes", "quantidade", "qtd_bilhetes"),
}

# Campos sem os quais não dá para agregar nada de útil
REQUIRED_FIELDS = ("origin", "destination", "fare")

CANDIDATE_SEPARATORS = (";", ",", "\t", "|")
CANDIDATE_ENCODINGS = ("utf-8-sig", "latin-1")


def normalize_column_name(name: str) -> str:
    """Normaliza um nome de coluna: sem acento, minúsculo, com underscores."""
    stripped = strip_accents(str(name))
    cleaned = stripped.strip().lower()
    for ch in (" ", "-", ".", "/"):
        cleaned = cleaned.replace(ch, "_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def normalize_place(name: str) -> str:
    """Normaliza o nome de um município para compor a chave de rota."""
    stripped = strip_accents(str(name))
    return " ".join(stripped.upper().split())


def build_route_key(
    origin_city: str,
    origin_state: Optional[str],
    destination_city: str,
    destination_state: Optional[str],
) -> str:
    """Chave canônica de corredor: 'CURITIBA-PR|PORTO ALEGRE-RS'."""
    left = normalize_place(origin_city)
    right = normalize_place(destination_city)
    if origin_state:
        left = f"{left}-{normalize_place(origin_state)}"
    if destination_state:
        right = f"{right}-{normalize_place(destination_state)}"
    return f"{left}|{right}"


@dataclass
class MonitriipSchema:
    """Mapeamento entre os campos lógicos do Trivio e as colunas reais do arquivo."""

    columns: Dict[str, str] = field(default_factory=dict)  # campo lógico -> coluna real
    separator: str = ";"
    encoding: str = "latin-1"
    header: List[str] = field(default_factory=list)

    @property
    def missing_required(self) -> List[str]:
        return [f for f in REQUIRED_FIELDS if f not in self.columns]

    @property
    def is_aggregated(self) -> bool:
        """True quando o arquivo já vem agregado (uma linha = um grupo, não um bilhete).

        O Monitriip publicado em 2024 traz `quantidade_bilhetes` e `media_valor_total`
        por grupo. A agregação pondera cada grupo pela quantidade de bilhetes.
        """
        normalized = {normalize_column_name(col) for col in self.header}
        return "quantidade_bilhetes" in normalized

    @property
    def unsupported_reason(self) -> Optional[str]:
        """Mantido por compatibilidade: hoje nenhum formato conhecido é recusado."""
        return None

    @property
    def is_usable(self) -> bool:
        return not self.missing_required

    def column(self, logical_field: str) -> Optional[str]:
        return self.columns.get(logical_field)

    def describe(self) -> str:
        lines = [
            f"separador={self.separator!r} encoding={self.encoding!r} colunas={len(self.header)}",
            "Mapeamento detectado:",
        ]
        for logical in MONITRIIP_COLUMN_ALIASES:
            actual = self.columns.get(logical)
            mark = "OK " if actual else ("FALTA" if logical in REQUIRED_FIELDS else " - ")
            lines.append(f"  [{mark}] {logical:18} -> {actual or '(não encontrado)'}")
        if self.missing_required:
            lines.append(
                "\nCampos obrigatórios ausentes: "
                + ", ".join(self.missing_required)
                + "\nAjuste MONITRIIP_COLUMN_ALIASES em app/collectors/antt/monitriip.py "
                "com os nomes reais vistos no cabeçalho acima."
            )
        if self.unsupported_reason:
            lines.append("\nNão suportado: " + self.unsupported_reason)
        return "\n".join(lines)


def read_csv_header(path: str) -> Tuple[List[str], str, str]:
    """Lê só o cabeçalho, inferindo separador e codificação.

    Devolve (colunas, separador, encoding).
    """
    last_error: Optional[Exception] = None
    for encoding in CANDIDATE_ENCODINGS:
        try:
            with open(path, "r", encoding=encoding, newline="") as handle:
                first_line = handle.readline()
            if not first_line:
                continue

            separator = max(CANDIDATE_SEPARATORS, key=first_line.count)
            if first_line.count(separator) == 0:
                continue

            columns = next(csv.reader([first_line], delimiter=separator))
            return [c.strip() for c in columns], separator, encoding
        except (UnicodeDecodeError, StopIteration) as exc:
            last_error = exc
            continue

    raise ValueError(f"Não foi possível interpretar o cabeçalho de {path}: {last_error}")


def detect_monitriip_schema(path: str) -> MonitriipSchema:
    """Descobre quais colunas do arquivo correspondem aos campos que usamos."""
    header, separator, encoding = read_csv_header(path)
    normalized = {normalize_column_name(col): col for col in header}

    mapping: Dict[str, str] = {}
    for logical, aliases in MONITRIIP_COLUMN_ALIASES.items():
        # 1) casamento exato pelo apelido
        for alias in aliases:
            if alias in normalized:
                mapping[logical] = normalized[alias]
                break
        if logical in mapping:
            continue
        # 2) casamento parcial (ex: 'vl_tarifa_bilhete' contém 'tarifa')
        for alias in aliases:
            match = next((orig for norm, orig in normalized.items() if alias in norm), None)
            if match:
                mapping[logical] = match
                break

    schema = MonitriipSchema(
        columns=mapping, separator=separator, encoding=encoding, header=header
    )
    logger.info(f"[monitriip] Esquema detectado em {path}: {len(mapping)} campos mapeados.")
    return schema


def _parse_brazilian_money(column: str) -> pl.Expr:
    """Converte '1.234,56' ou '1234.56' em float.

    Só remove o ponto de milhar quando há vírgula decimal, para não estragar
    valores já no formato americano.
    """
    raw = pl.col(column).cast(pl.Utf8).str.strip_chars().str.replace_all(r"[R$\s]", "")
    with_comma = raw.str.replace_all(r"\.", "").str.replace(",", ".")
    return (
        pl.when(raw.str.contains(","))
        .then(with_comma)
        .otherwise(raw)
        .cast(pl.Float64, strict=False)
    )


def _split_place_and_state(column: str) -> Tuple[pl.Expr, pl.Expr]:
    """Separa 'CURITIBA/PR' em cidade e UF, quando não há coluna de UF própria."""
    base = pl.col(column).cast(pl.Utf8).str.strip_chars()
    city = base.str.split("/").list.get(0).str.strip_chars()
    state = base.str.split("/").list.get(1, null_on_oob=True).str.strip_chars()
    return city, state
