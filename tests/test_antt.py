"""Testes da integração com os Dados Abertos da ANTT (portal + série mensal do Monitriip)."""

from datetime import date, datetime
import io
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.analyzers.price_thermometer import (
    MIN_OFFICIAL_MONTHS,
    enrich_quote_with_thermometer,
    fetch_route_monthly_means,
)
from app.collectors.antt.monitriip import (
    build_route_key,
    detect_monitriip_schema,
    normalize_column_name,
    normalize_place,
    read_csv_header,
)
from app.collectors.antt.monthly import aggregate_monthly_fares, check_date_consistency
from app.collectors.antt.portal import ANTTResource, parse_resource_links
from app.collectors.base import StandardizedQuote
from app.database import Base
from app.models import ModalType, RouteMonthlyFare


# ==============================================================================
# 1. Descoberta de arquivos no portal da ANTT
# ==============================================================================

# Trecho fiel da página real do conjunto (dados.antt.gov.br/dataset/monitriip-bilhetes-de-passagem)
DATASET_PAGE_HTML = """
<ul class="resource-list">
  <li class="resource-item">
    <a class="heading" href="/dataset/989414bc-6327-4a1c-be47-22ba31a9d271/resource/f3eb0796-c00f-4c7e-8bd3-74d44839c578/download/venda_passagem_09_2024.csv">Set24 - Bilhete de Passagem</a>
  </li>
  <li class="resource-item">
    <a class="heading" href="https://dados.antt.gov.br/dataset/989414bc-6327-4a1c-be47-22ba31a9d271/resource/9d804707-2729-4ff8-9abe-813572b4e511/download/venda_passagem_01_2024.csv">Jan24 - Bilhete de Passagem</a>
  </li>
  <li class="resource-item">
    <a class="heading" href="/dataset/989414bc-6327-4a1c-be47-22ba31a9d271/resource/e8eeb076-0d43-48ac-a74f-073c7dea9762/download/venda_passagem_01_2019.csv">Jan19 - Bilhete de Passagem</a>
  </li>
  <li><a href="/dataset/989414bc/resource/abc/download/dicionario.pdf">Dicionário de dados</a></li>
  <li><a href="/dataset/monitriip-bilhetes-de-passagem/history">Histórico de alterações</a></li>
</ul>
"""


def test_parses_csv_links_from_dataset_page():
    """A descoberta usa a página pública; /api/ é proibido pelo robots.txt da ANTT."""
    resources = parse_resource_links(DATASET_PAGE_HTML)

    assert len(resources) == 3  # o PDF e o link de histórico ficam de fora
    assert all(r.url.startswith("https://dados.antt.gov.br/") for r in resources)
    assert {r.reference_period for r in resources} == {"2024-09", "2024-01", "2019-01"}


def test_resource_links_are_deduplicated():
    doubled = DATASET_PAGE_HTML + DATASET_PAGE_HTML
    assert len(parse_resource_links(doubled)) == 3


def test_resource_infers_reference_period_from_filename():
    resource = ANTTResource(
        url="https://dados.antt.gov.br/dataset/x/resource/y/download/venda_passagem_09_2024.csv",
    )
    assert resource.filename == "venda_passagem_09_2024.csv"
    assert resource.reference_period == "2024-09"
    assert resource.period_date == date(2024, 9, 1)


def test_resource_without_monthly_pattern_has_no_period():
    resource = ANTTResource(url="https://x/dicionario.pdf", name="Dicionário")
    assert resource.reference_period is None
    assert resource.period_date is None


# ==============================================================================
# 2. Normalização
# ==============================================================================

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Município Origem", "municipio_origem"),
        ("  UF  Destino ", "uf_destino"),
        ("Valor-Tarifa", "valor_tarifa"),
        ("Tipo de Serviço", "tipo_de_servico"),
    ],
)
def test_normalize_column_name(raw, expected):
    assert normalize_column_name(raw) == expected


def test_normalize_place_strips_accents_and_case():
    assert normalize_place(" são  paulo ") == "SAO PAULO"


def test_build_route_key_is_canonical():
    assert build_route_key("Curitiba", "pr", "Porto Alegre", "rs") == "CURITIBA-PR|PORTO ALEGRE-RS"
    # Sem UF, a chave usa só o município
    assert build_route_key("Curitiba", None, "Porto Alegre", None) == "CURITIBA|PORTO ALEGRE"


# ==============================================================================
# 3. Detecção de esquema do CSV
# ==============================================================================

def _write_csv(tmp_path, name, rows, encoding="latin-1", separator=";"):
    path = os.path.join(str(tmp_path), name)
    content = "\n".join(separator.join(str(c) for c in row) for row in rows)
    with io.open(path, "w", encoding=encoding, newline="") as handle:
        handle.write(content)
    return path


def test_detects_accented_latin1_header_with_semicolons(tmp_path):
    path = _write_csv(tmp_path, "venda_passagem_09_2024.csv", [
        ["Data Viagem", "Município Origem", "UF Origem", "Município Destino",
         "UF Destino", "Empresa", "Tipo de Serviço", "Valor Tarifa"],
        ["2024-09-01", "Curitiba", "PR", "Porto Alegre", "RS", "Viação X", "SEMI-LEITO", "180,00"],
    ])

    schema = detect_monitriip_schema(path)

    assert schema.separator == ";"
    assert schema.is_usable
    assert schema.column("origin") == "Município Origem"
    assert schema.column("fare") == "Valor Tarifa"
    assert schema.column("service_class") == "Tipo de Serviço"
    assert schema.missing_required == []


def test_detects_alternative_column_names_and_comma_separator(tmp_path):
    path = _write_csv(tmp_path, "bilhetes.csv", [
        ["origem", "destino", "tarifa"],
        ["Curitiba/PR", "Porto Alegre/RS", "180.00"],
    ], encoding="utf-8-sig", separator=",")

    schema = detect_monitriip_schema(path)

    assert schema.separator == ","
    assert schema.is_usable
    assert schema.column("origin") == "origem"
    assert schema.column("fare") == "tarifa"
    # Campos opcionais ausentes não impedem o uso
    assert schema.column("carrier") is None


def test_schema_reports_missing_required_fields(tmp_path):
    path = _write_csv(tmp_path, "incompleto.csv", [
        ["origem", "destino", "empresa"],
        ["Curitiba", "Porto Alegre", "Viação X"],
    ])

    schema = detect_monitriip_schema(path)

    assert not schema.is_usable
    assert schema.missing_required == ["fare"]
    assert "FALTA" in schema.describe()


REAL_AGGREGATED_HEADER = [
    "mes_emissao_bilhete", "mes_viagem", "ponto_origem_viagem", "ponto_destino_viagem",
    "tipo_servico", "tipo_gratuidade", "media_valor_total", "dp_valor_total",
    "quantidade_bilhetes",
]


def _agg_row(origin, dest, service, gratuity, fare, qty, trip="11/2024", issue="10/2024"):
    """Uma linha do Monitriip agregado, no formato real (emissão, viagem, ...)."""
    return [issue, trip, origin, dest, service, gratuity, f"{fare:.2f}".replace(".", ","), "0,00", str(qty)]


NORMAL = "Tarifa Normal - sem desconto"
IDOSO_50 = "Bilhete de Viagem do Idoso 50%"
CWB_POA = ("CURITIBA/PR", "PORTO ALEGRE/RS", "Semileito")


def test_real_aggregated_monitriip_is_detected_and_usable(tmp_path):
    """Cabeçalho real de venda_passagem_10_2024.csv é reconhecido e importável."""
    path = _write_csv(tmp_path, "venda_passagem_10_2024.csv", [
        REAL_AGGREGATED_HEADER,
        ["10/2024", "11/2024", "CURITIBA", "PORTO ALEGRE", "SEMI LEITO", NORMAL, "210,50", "12,30", "45"],
    ])

    schema = detect_monitriip_schema(path)

    assert schema.is_aggregated
    assert schema.is_usable
    assert schema.column("fare") == "media_valor_total"
    assert schema.column("quantity") == "quantidade_bilhetes"
    assert schema.column("gratuity") == "tipo_gratuidade"
    assert schema.column("trip_month") == "mes_viagem"
    assert schema.column("issue_month") == "mes_emissao_bilhete"


def test_check_date_consistency_counts_trips_before_issue(tmp_path):
    rows = [REAL_AGGREGATED_HEADER,
            _agg_row(*CWB_POA, NORMAL, 100, 1, trip="12/2024", issue="10/2024"),   # após a emissão
            _agg_row(*CWB_POA, NORMAL, 100, 1, trip="02/2024", issue="10/2024"),   # antes da emissão
            _agg_row(*CWB_POA, NORMAL, 100, 1, trip="10/2024", issue="10/2024")]   # mesmo mês
    path = _write_csv(tmp_path, "venda_passagem_10_2024.csv", rows)

    result = check_date_consistency(path)

    assert result == {"total_linhas": 3, "viagem_antes_da_emissao": 1, "proporcao": 0.3333}


def test_check_date_consistency_is_none_without_month_columns(tmp_path):
    path = _write_csv(tmp_path, "bilhetes.csv", [
        ["origem", "destino", "tarifa"], ["Curitiba/PR", "Porto Alegre/RS", "180,00"],
    ])
    assert check_date_consistency(path) is None


def test_read_csv_header_detects_encoding_and_separator(tmp_path):
    path = _write_csv(tmp_path, "acentos.csv", [["Município", "Tarifa"], ["Içara", "10,00"]])
    columns, separator, encoding = read_csv_header(path)

    assert columns == ["Município", "Tarifa"]
    assert separator == ";"
    assert encoding in ("latin-1", "utf-8-sig")


# ==============================================================================
# 4. Série mensal por rota (caminho A)
# ==============================================================================

def test_monthly_series_groups_by_trip_month_and_weights_tickets(tmp_path):
    """Um mês com 8 bilhetes a R$300 pesa mais que dois de R$100 e R$200 em outro mês."""
    rows = [REAL_AGGREGATED_HEADER,
            _agg_row(*CWB_POA, NORMAL, 100, 1, trip="11/2024"),
            _agg_row(*CWB_POA, NORMAL, 200, 1, trip="11/2024"),
            _agg_row(*CWB_POA, NORMAL, 300, 8, trip="12/2024")]
    path = _write_csv(tmp_path, "venda_passagem_10_2024.csv", rows)

    aggs = sorted(aggregate_monthly_fares(path), key=lambda a: a.trip_month)

    assert [a.trip_month for a in aggs] == ["2024-11", "2024-12"]
    nov, dec = aggs
    assert nov.sample_size == 2 and nov.mean_brl == 150.0
    assert dec.sample_size == 8 and dec.mean_brl == 300.0
    assert nov.emission_period == "2024-10"
    assert nov.route_key == "CURITIBA-PR|PORTO ALEGRE-RS"
    assert nov.service_class == "SEMI_LEITO"


def test_monthly_series_excludes_trips_before_emission_and_discounts(tmp_path):
    rows = [REAL_AGGREGATED_HEADER,
            _agg_row(*CWB_POA, NORMAL, 100, 3, trip="11/2024"),
            _agg_row(*CWB_POA, NORMAL, 50, 1, trip="09/2024"),        # viagem antes da emissão
            _agg_row(*CWB_POA, IDOSO_50, 50, 100, trip="11/2024")]    # desconto de grupo específico
    path = _write_csv(tmp_path, "venda_passagem_10_2024.csv", rows)

    [agg] = aggregate_monthly_fares(path)

    assert agg.trip_month == "2024-11"
    assert agg.sample_size == 3
    assert agg.mean_brl == 100.0


def test_monthly_series_is_per_route_class_and_month(tmp_path):
    rows = [REAL_AGGREGATED_HEADER,
            _agg_row(*CWB_POA, NORMAL, 180, 5, trip="11/2024"),
            _agg_row("SAO PAULO/SP", "RIO DE JANEIRO/RJ", "Leito", NORMAL, 300, 9, trip="11/2024")]
    path = _write_csv(tmp_path, "venda_passagem_10_2024.csv", rows)

    keys = {(a.route_key, a.service_class, a.trip_month) for a in aggregate_monthly_fares(path)}

    assert keys == {
        ("CURITIBA-PR|PORTO ALEGRE-RS", "SEMI_LEITO", "2024-11"),
        ("SAO PAULO-SP|RIO DE JANEIRO-RJ", "LEITO", "2024-11"),
    }


def test_monthly_series_parses_brazilian_and_american_decimals(tmp_path):
    rows = [REAL_AGGREGATED_HEADER,
            ["10/2024", "11/2024", "CURITIBA", "PORTO ALEGRE", "LEITO", NORMAL, "1.234,56", "0,00", "3"],
            ["10/2024", "11/2024", "CURITIBA", "PORTO ALEGRE", "LEITO", NORMAL, "1234.56", "0,00", "3"]]
    path = _write_csv(tmp_path, "venda_passagem_10_2024.csv", rows)

    [agg] = aggregate_monthly_fares(path)

    assert agg.mean_brl == 1234.56
    assert agg.sample_size == 6


def test_monthly_series_drops_outliers(tmp_path):
    rows = [REAL_AGGREGATED_HEADER,
            _agg_row(*CWB_POA, NORMAL, 180, 5, trip="11/2024"),
            _agg_row(*CWB_POA, NORMAL, 99999, 1, trip="11/2024")]   # erro de digitação
    path = _write_csv(tmp_path, "venda_passagem_10_2024.csv", rows)

    [agg] = aggregate_monthly_fares(path)

    assert agg.sample_size == 5
    assert agg.max_brl == 180.0


def test_monthly_series_requires_month_columns(tmp_path):
    path = _write_csv(tmp_path, "bilhetes.csv", [
        ["origem", "destino", "tarifa"], ["Curitiba/PR", "Porto Alegre/RS", "180,00"],
    ])
    with pytest.raises(ValueError, match="colunas de mês"):
        aggregate_monthly_fares(path)


def test_monthly_series_rejects_file_without_fare_column(tmp_path):
    path = _write_csv(tmp_path, "ruim.csv", [["origem", "destino"], ["Curitiba", "Porto Alegre"]])

    with pytest.raises(ValueError, match="colunas mínimas"):
        aggregate_monthly_fares(path)


# ==============================================================================
# 5. Termômetro sobre a série mensal oficial
# ==============================================================================

ROUTE_KEY = "CURITIBA-PR|PORTO ALEGRE-RS"
SIX_MONTH_MEANS = [("2024-01", 180.0), ("2024-02", 190.0), ("2024-03", 200.0),
                   ("2024-04", 210.0), ("2024-05", 220.0), ("2024-06", 230.0)]


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _add_month(session, trip_month, mean, tickets=100, emission="2024-10", service="SEMI_LEITO"):
    session.add(RouteMonthlyFare(
        route_key=ROUTE_KEY,
        origin_city="CURITIBA", origin_state="PR",
        destination_city="PORTO ALEGRE", destination_state="RS",
        modal=ModalType.ROAD, service_class=service,
        trip_month=trip_month, emission_period=emission, source="antt_monitriip",
        sample_size=tickets, mean_brl=mean, min_brl=mean, max_brl=mean,
    ))


def _load_months(session, months, **kwargs):
    for trip_month, mean in months:
        _add_month(session, trip_month, mean, **kwargs)
    session.commit()


def _road_quote(base_price: float, transfer: float = 0.0, service="SEMI_LEITO") -> StandardizedQuote:
    return StandardizedQuote(
        origin_external_id="ROD_CWB", destination_external_id="ROD_POA",
        modal="ROAD", provider_source="clickbus", carrier_name="Viação X",
        service_class=service,
        departure_time=datetime(2026, 11, 20, 8, 0),
        arrival_time=datetime(2026, 11, 20, 18, 0),
        duration_minutes=600, base_price=base_price,
        transfer_cost_estimate=transfer,
        route_metadata={
            "origin_city": "Curitiba", "origin_state": "PR",
            "destination_city": "Porto Alegre", "destination_state": "RS",
        },
    )


def test_six_months_enable_official_classification(db):
    _load_months(db, SIX_MONTH_MEANS)
    quote = _road_quote(base_price=150.0)

    result = enrich_quote_with_thermometer(quote, db=db)

    assert MIN_OFFICIAL_MONTHS == 6
    assert result.source == "antt_monitriip"
    assert result.status == "LOW"                      # 150 abaixo do P25 (192,5)
    assert result.reference_period == "2024-01 a 2024-06"
    assert result.sample_size == 600                    # bilhetes somados
    assert quote.route_metadata["price_thermometer"]["source"] == "antt_monitriip"
    assert "ANTT" in result.recommendation or "bilhetes" in result.recommendation


@pytest.mark.parametrize("price,expected", [(150.0, "LOW"), (200.0, "MEDIUM"), (300.0, "HIGH")])
def test_official_status_bands(db, price, expected):
    _load_months(db, SIX_MONTH_MEANS)
    result = enrich_quote_with_thermometer(_road_quote(base_price=price), db=db)
    assert result.status == expected


def test_five_months_fall_back_to_own_history(db):
    """Com menos de 6 meses a série não classifica; o termômetro recorre ao próprio histórico."""
    _load_months(db, SIX_MONTH_MEANS[:5])

    result = enrich_quote_with_thermometer(_road_quote(base_price=150.0), db=db)

    assert result.source == "coletas_trivio"
    assert result.status == "INSUFFICIENT_DATA"


def test_series_must_match_the_service_class(db):
    """Preço de Leito não pode ser comparado com a série de Semi-Leito."""
    _load_months(db, SIX_MONTH_MEANS)  # SEMI_LEITO

    result = enrich_quote_with_thermometer(_road_quote(base_price=150.0, service="LEITO_CAMA"), db=db)

    assert result.source == "coletas_trivio"


def test_official_comparison_ignores_urban_transfer(db):
    """A série mede o bilhete; somar transfer urbano classificaria tudo como caro."""
    _load_months(db, SIX_MONTH_MEANS)
    quote = _road_quote(base_price=140.0, transfer=120.0)
    assert quote.total_effective_price == 260.0

    result = enrich_quote_with_thermometer(quote, db=db)

    assert result.status == "LOW"


def test_air_quotes_do_not_use_the_road_series(db):
    _load_months(db, SIX_MONTH_MEANS)
    quote = _road_quote(base_price=180.0)
    quote.modal = "AIR"

    result = enrich_quote_with_thermometer(quote, db=db)

    assert result.source != "antt_monitriip"


def test_same_trip_month_from_two_emissions_is_weighted(db):
    _add_month(db, "2024-01", 100.0, tickets=1, emission="2024-10")
    _add_month(db, "2024-01", 200.0, tickets=3, emission="2024-11")
    db.commit()

    series = fetch_route_monthly_means(db, "Curitiba", "PR", "Porto Alegre", "RS", "SEMI_LEITO")

    assert series == [("2024-01", 175.0, 4)]


def test_route_lookup_is_accent_and_case_insensitive(db):
    _load_months(db, SIX_MONTH_MEANS)
    for origin, dest in [
        ("Curitiba", "Porto Alegre"),
        ("CURITIBA", "pôrto alegre"),
        ("  curitiba ", "PORTO  ALEGRE"),
    ]:
        series = fetch_route_monthly_means(db, origin, "pr", dest, "RS", "SEMI_LEITO")
        assert len(series) == 6, f"{origin} -> {dest} deveria casar"


def test_unknown_route_has_no_series(db):
    _load_months(db, SIX_MONTH_MEANS)
    assert fetch_route_monthly_means(db, "Pelotas", "RS", "Manaus", "AM", "SEMI_LEITO") == []
