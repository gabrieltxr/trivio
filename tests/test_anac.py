"""Testes da integração com os microdados de tarifas aéreas da ANAC (régua do aéreo)."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.analyzers.price_thermometer import (
    MIN_ANAC_MONTHS,
    enrich_quote_with_thermometer,
    enrich_quotes_batch_with_thermometer,
    fetch_air_fare_band,
)
from app.collectors.anac.airfares import (
    ANAC_ALL_CLASSES,
    ANAC_SOURCE,
    aggregate_air_monthly_fares,
    detect_anac_schema,
)
from app.collectors.anac.airports import IATA_TO_ICAO, ICAO_TO_IATA, to_iata, to_icao
from app.collectors.anac.portal import ANACResource, parse_monthly_files, parse_year_directories
from app.collectors.base import StandardizedQuote
from app.database import Base, ensure_schema
from app.models import ModalType, RouteMonthlyFare


# ==============================================================================
# 1. De-para IATA <-> ICAO
# ==============================================================================

@pytest.mark.parametrize("iata,icao", [
    ("CGH", "SBSP"), ("GRU", "SBGR"), ("SDU", "SBRJ"), ("GIG", "SBGL"),
    ("VCP", "SBKP"), ("POA", "SBPA"), ("CWB", "SBCT"), ("BSB", "SBBR"),
])
def test_iata_to_icao_for_seeded_airports(iata, icao):
    assert to_icao(iata) == icao
    assert to_iata(icao) == iata


def test_to_icao_accepts_icao_and_normalizes_case():
    assert to_icao(" cgh ") == "SBSP"
    assert to_icao("sbsp") == "SBSP"


def test_to_icao_rejects_unknown_codes():
    assert to_icao("XXX") is None
    assert to_icao("KJFK") is None   # ICAO estrangeiro: a ANAC doméstica não cobre
    assert to_icao(None) is None
    assert to_icao("") is None


def test_icao_table_is_one_to_one():
    assert len(IATA_TO_ICAO) == len(ICAO_TO_IATA)


# ==============================================================================
# 2. Descoberta de arquivos na listagem do servidor da ANAC
# ==============================================================================

# Trechos fiéis das listagens IIS de https://sas.anac.gov.br/sas/tarifadomestica/
ROOT_LISTING = """
<pre><A HREF="/sas/">[To Parent Directory]</A><br><br>
 3/8/2025 12:00 PM  &lt;dir&gt; <A HREF="/sas/tarifadomestica/2002/">2002</A><br>
 1/30/2026 11:20 AM &lt;dir&gt; <A HREF="/sas/tarifadomestica/2025/">2025</A><br>
 10/21/2026 12:44 PM &lt;dir&gt; <A HREF="/sas/tarifadomestica/2026/">2026</A><br>
 1/1/2020 12:00 PM 1234 <A HREF="/sas/tarifadomestica/web.config">web.config</A><br></pre>
"""

YEAR_LISTING = """
<pre><A HREF="/sas/tarifadomestica/">[To Parent Directory]</A><br><br>
 3/20/2026 10:42 AM 23509954 <A HREF="/sas/tarifadomestica/2026/202601.csv">202601.csv</A><br>
 6/24/2026 10:47 AM 26929792 <A HREF="/sas/tarifadomestica/2026/202605%20(1).csv">202605 (1).csv</A><br>
 10/21/2026 12:44 PM 27705269 <A HREF="/sas/tarifadomestica/2026/202609.csv">202609.csv</A><br>
 12/18/2025 10:31 AM 29921156 <A HREF="/sas/tarifadomestica/2026/202609.xlsx">202609.xlsx</A><br></pre>
"""


def test_parses_year_directories_newest_first():
    assert parse_year_directories(ROOT_LISTING) == [2026, 2025, 2002]


def test_parses_monthly_csv_files_and_ignores_xlsx():
    files = parse_monthly_files(YEAR_LISTING)

    assert [f.reference_period for f in files] == ["2026-09", "2026-05", "2026-01"]
    assert files[0].url == "https://sas.anac.gov.br/sas/tarifadomestica/2026/202609.csv"


def test_irregular_file_name_keeps_its_period():
    resource = ANACResource(url="https://sas.anac.gov.br/sas/tarifadomestica/2026/202605%20(1).csv")
    assert resource.filename == "202605 (1).csv"
    assert resource.reference_period == "2026-05"
    assert not resource.is_canonical_name


def test_canonical_name_wins_when_a_period_is_duplicated():
    listing = (
        '<A HREF="/sas/tarifadomestica/2026/202605%20(1).csv">x</A>'
        '<A HREF="/sas/tarifadomestica/2026/202605.csv">y</A>'
    )
    files = parse_monthly_files(listing)
    assert len(files) == 1
    assert files[0].filename == "202605.csv"


# ==============================================================================
# 3. Leitura e agregação dos microdados
# ==============================================================================

RECENT_HEADER = "nr_ano_referencia;nr_mes_referencia;sg_empresa_icao;sg_icao_origem;sg_icao_destino;nr_tarifa;nr_assentos"
LEGACY_HEADER = "ANO;MES;EMPRESA;ORIGEM;DESTINO;TARIFA;ASSENTOS"


def _write_csv(tmp_path, header, rows, name="202609.csv"):
    path = tmp_path / name
    path.write_text("\r\n".join([header, *rows]) + "\r\n", encoding="latin-1")
    return str(path)


def test_detects_recent_and_legacy_headers(tmp_path):
    for header in (RECENT_HEADER, LEGACY_HEADER):
        schema = detect_anac_schema(_write_csv(tmp_path, header, ["2026;9;TAM;SBSP;SBRJ;500,0;1"]))
        assert schema.is_usable, schema.describe()


def test_reports_missing_columns(tmp_path):
    path = _write_csv(tmp_path, "ANO;MES;ORIGEM;DESTINO", ["2026;9;SBSP;SBRJ"])
    schema = detect_anac_schema(path)
    assert not schema.is_usable
    assert set(schema.missing) == {"fare", "seats"}
    with pytest.raises(ValueError):
        aggregate_air_monthly_fares(path)


def test_weighted_mean_and_quantiles_follow_seats(tmp_path):
    # 10 assentos: 4 a R$ 100, 4 a R$ 200, 2 a R$ 1000
    path = _write_csv(tmp_path, RECENT_HEADER, [
        "2026;9;TAM;SBSP;SBRJ;100,0;4",
        "2026;9;GLO;SBSP;SBRJ;200,0;4",
        "2026;9;AZU;SBSP;SBRJ;1000,0;2",
    ])

    [agg] = aggregate_air_monthly_fares(path)

    assert agg.route_key == "SBSP|SBRJ"
    assert agg.reference_month == "2026-09"
    assert agg.seats == 10
    assert agg.mean_brl == 320.0                     # (400 + 800 + 2000) / 10
    assert (agg.min_brl, agg.max_brl) == (100.0, 1000.0)
    assert agg.p10_brl == 100.0
    assert agg.p25_brl == 100.0
    assert agg.p50_brl == 200.0                       # 40% vendidos a 100: a mediana já é 200
    assert agg.p75_brl == 200.0
    assert agg.p90_brl == 1000.0
    assert agg.carriers == ["AZU", "GLO", "TAM"]


def test_carriers_are_pooled_and_routes_are_directional(tmp_path):
    path = _write_csv(tmp_path, RECENT_HEADER, [
        "2026;9;TAM;SBSP;SBRJ;300,0;1",
        "2026;9;GLO;SBSP;SBRJ;500,0;1",
        "2026;9;TAM;SBRJ;SBSP;900,0;1",
    ])
    by_route = {agg.route_key: agg for agg in aggregate_air_monthly_fares(path)}

    assert set(by_route) == {"SBSP|SBRJ", "SBRJ|SBSP"}
    assert by_route["SBSP|SBRJ"].seats == 2
    assert by_route["SBRJ|SBSP"].mean_brl == 900.0


@pytest.mark.parametrize("fare", ["800,0", "800.0", "800,00", "1.150,00"])
def test_parses_both_decimal_styles(tmp_path, fare):
    path = _write_csv(tmp_path, LEGACY_HEADER, [f"2015;6;AZU;SBSV;SBRF;{fare};2"])
    [agg] = aggregate_air_monthly_fares(path)
    assert agg.mean_brl in (800.0, 1150.0)
    assert agg.reference_month == "2015-06"


def test_drops_invalid_rows(tmp_path):
    path = _write_csv(tmp_path, RECENT_HEADER, [
        "2026;9;TAM;SBSP;SBRJ;500,0;3",       # válida
        "2026;9;TAM;SBSP;SBRJ;0,0;5",         # tarifa zero
        "2026;9;TAM;SBSP;SBRJ;99999,0;5",     # acima do teto
        "2026;9;TAM;SBSP;SBRJ;400,0;0",       # sem assentos
        "2026;9;TAM;SBSP;SBSP;400,0;5",       # origem = destino
        "2026;13;TAM;SBSP;SBRJ;400,0;5",      # mês inválido
        "2026;9;TAM;CGH;SBRJ;400,0;5",        # não é ICAO
    ])
    [agg] = aggregate_air_monthly_fares(path)
    assert agg.seats == 3
    assert agg.mean_brl == 500.0


# ==============================================================================
# 4. Migração leve do esquema
# ==============================================================================

def test_ensure_schema_adds_percentile_columns_to_old_table():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:   # tabela como era antes desta mudança
        conn.execute(text(
            "CREATE TABLE route_monthly_fares (id INTEGER PRIMARY KEY, route_key VARCHAR(160), "
            "mean_brl FLOAT)"
        ))

    ensure_schema(engine)
    ensure_schema(engine)   # idempotente

    columns = {col["name"] for col in inspect(engine).get_columns("route_monthly_fares")}
    assert {"p10_brl", "p25_brl", "p50_brl", "p75_brl", "p90_brl"} <= columns


# ==============================================================================
# 5. Termômetro aéreo sobre a série da ANAC
# ==============================================================================

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


BAND = dict(min_brl=100.0, p10_brl=200.0, p25_brl=300.0, p50_brl=450.0, p75_brl=700.0, p90_brl=1300.0, max_brl=4000.0)


def _add_air_month(session, month, seats=1000, route=("SBSP", "SBRJ"), **overrides):
    values = {**BAND, **overrides}
    session.add(RouteMonthlyFare(
        route_key=f"{route[0]}|{route[1]}",
        origin_city=route[0], destination_city=route[1],
        modal=ModalType.AIR, service_class=ANAC_ALL_CLASSES,
        trip_month=month, emission_period=month, source=ANAC_SOURCE,
        sample_size=seats, mean_brl=values["p50_brl"], **values,
    ))


def _load_air_months(session, count, **kwargs):
    for month in range(1, count + 1):
        _add_air_month(session, f"2026-{month:02d}", **kwargs)
    session.commit()


def _air_quote(base_price, taxes=60.0, baggage=110.0, origin="CGH", dest="SDU") -> StandardizedQuote:
    return StandardizedQuote(
        origin_external_id=origin, destination_external_id=dest,
        modal="AIR", provider_source="ignav", carrier_name="LATAM Airlines",
        service_class="ECONOMY",
        departure_time=datetime(2026, 11, 20, 8, 0),
        arrival_time=datetime(2026, 11, 20, 9, 5),
        duration_minutes=65, base_price=base_price, taxes_price=taxes,
        baggage_fee_estimate=baggage,
        route_metadata={"origin_iata": origin, "destination_iata": dest},
    )


def test_air_quote_uses_anac_series(db):
    _load_air_months(db, 3)
    quote = _air_quote(base_price=250.0)

    result = enrich_quote_with_thermometer(quote, db=db)

    assert MIN_ANAC_MONTHS == 3
    assert result.source == ANAC_SOURCE
    assert result.status == "LOW"
    assert result.historical_median_brl == 450.0
    assert result.sample_size == 3000
    assert result.reference_period == "2026-01 a 2026-03"
    assert "ANAC" in result.recommendation
    assert quote.route_metadata["price_thermometer"]["source"] == ANAC_SOURCE


@pytest.mark.parametrize("price,expected", [(250.0, "LOW"), (300.0, "LOW"), (450.0, "MEDIUM"), (700.0, "MEDIUM"), (900.0, "HIGH")])
def test_air_status_bands(db, price, expected):
    _load_air_months(db, 3)
    assert enrich_quote_with_thermometer(_air_quote(base_price=price), db=db).status == expected


def test_air_percentile_is_interpolated(db):
    _load_air_months(db, 3)
    result = enrich_quote_with_thermometer(_air_quote(base_price=450.0), db=db)
    assert result.percentile == 50
    assert enrich_quote_with_thermometer(_air_quote(base_price=575.0), db=db).percentile == 62


def test_air_comparison_ignores_taxes_and_baggage(db):
    """A tarifa da ANAC não tem taxa de embarque: comparar o total classificaria tudo como caro."""
    _load_air_months(db, 3)
    quote = _air_quote(base_price=280.0, taxes=60.0, baggage=110.0)
    assert quote.total_effective_price == 450.0

    assert enrich_quote_with_thermometer(quote, db=db).status == "LOW"


def test_two_months_are_not_enough(db):
    _load_air_months(db, 2)
    result = enrich_quote_with_thermometer(_air_quote(base_price=250.0), db=db)
    assert result.source == "coletas_trivio"


def test_thin_routes_are_not_classified(db):
    _load_air_months(db, 3, seats=50)   # 150 assentos na janela
    result = enrich_quote_with_thermometer(_air_quote(base_price=250.0), db=db)
    assert result.source == "coletas_trivio"


def test_unmapped_airport_falls_back(db):
    _load_air_months(db, 3)
    result = enrich_quote_with_thermometer(_air_quote(base_price=250.0, origin="XXX"), db=db)
    assert result.source == "coletas_trivio"


def test_route_direction_matters(db):
    _load_air_months(db, 3)   # só SBSP -> SBRJ
    result = enrich_quote_with_thermometer(_air_quote(base_price=250.0, origin="SDU", dest="CGH"), db=db)
    assert result.source == "coletas_trivio"


def test_band_uses_only_the_most_recent_window(db):
    for month in range(1, 13):
        _add_air_month(db, f"2025-{month:02d}", p50_brl=9999.0)   # ano antigo, fora da janela
    for month in range(1, 13):
        _add_air_month(db, f"2026-{month:02d}")
    db.commit()

    band = fetch_air_fare_band(db, "SBSP", "SBRJ")

    assert band.months[0] == "2026-01" and band.months[-1] == "2026-12"
    assert band.p50_brl == 450.0


def test_band_weights_months_by_seats(db):
    _add_air_month(db, "2026-01", seats=3000, p50_brl=400.0)
    _add_air_month(db, "2026-02", seats=1000, p50_brl=800.0)
    db.commit()

    band = fetch_air_fare_band(db, "SBSP", "SBRJ")

    assert band.p50_brl == 500.0   # (400*3000 + 800*1000) / 4000
    assert band.seats == 4000


def test_road_quotes_do_not_use_the_air_series(db):
    _load_air_months(db, 3)
    quote = _air_quote(base_price=250.0)
    quote.modal = "ROAD"
    assert enrich_quote_with_thermometer(quote, db=db).source != ANAC_SOURCE


def test_batch_enrichment_uses_anac_for_air(db):
    _load_air_months(db, 3)
    quotes = [_air_quote(base_price=250.0), _air_quote(base_price=900.0)]

    enrich_quotes_batch_with_thermometer(quotes, db=db)

    statuses = [q.route_metadata["price_thermometer"]["status"] for q in quotes]
    sources = {q.route_metadata["price_thermometer"]["source"] for q in quotes}
    assert statuses == ["LOW", "HIGH"]
    assert sources == {ANAC_SOURCE}


def test_recommendation_keeps_punctuation_and_formats_seats(db):
    _load_air_months(db, 3)
    result = enrich_quote_with_thermometer(_air_quote(base_price=900.0), db=db)
    assert "flexibilidade, considere outras datas, aeroportos" in result.recommendation
    assert "3 meses de vendas, 3.000 assentos" in result.recommendation
