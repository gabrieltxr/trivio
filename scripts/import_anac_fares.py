"""Importa tarifas aéreas domésticas reais da ANAC para o Termômetro de Preço.

Fonte: microdados de tarifas aéreas domésticas comercializadas, publicados pela
ANAC em https://sas.anac.gov.br/sas/tarifadomestica/ (um CSV por mês, desde 2002,
com cerca de um mês de defasagem). Cada arquivo tem ~25 MB.

Uso:

    # 1. Ver quais meses estão publicados
    python scripts/import_anac_fares.py --list

    # 2. Conferir o cabeçalho de um CSV já baixado
    python scripts/import_anac_fares.py --inspect caminho/202609.csv

    # 3. Baixar e importar os N meses mais recentes (12 recomendado: é a janela do Termômetro)
    python scripts/import_anac_fares.py --months 12

    # 4. Importar um arquivo já baixado
    python scripts/import_anac_fares.py --file caminho/202609.csv

As linhas individuais NÃO são persistidas: guardamos, por rota e mês de venda,
a média, os extremos e os percentis ponderados por assento (tabela
`route_monthly_fares`, source='anac_tarifas_domesticas').
"""

import argparse
import asyncio
import logging
import os
import sys
import tempfile
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.table import Table

from app.analyzers.price_thermometer import MIN_ANAC_MONTHS
from app.collectors.anac import (
    ANAC_ALL_CLASSES,
    ANAC_SOURCE,
    ANACOpenDataClient,
    AirRouteMonthlyAggregate,
    aggregate_air_monthly_fares,
    detect_anac_schema,
    to_iata,
)
from app.database import SessionLocal, analyze_tables, engine, ensure_schema
from app.models import ModalType, RouteMonthlyFare

console = Console()
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def persist_air_monthly(aggregates: List[AirRouteMonthlyAggregate]) -> int:
    """Grava a série dos meses presentes em `aggregates`, substituindo o que já existia deles.

    Substituir em vez de atualizar linha a linha garante que reimportar o mesmo mês
    não duplica nada, e que uma correção da fonte chega inteira.
    """
    ensure_schema(engine)
    months = sorted({agg.reference_month for agg in aggregates})
    with SessionLocal() as session:
        session.query(RouteMonthlyFare).filter(
            RouteMonthlyFare.source == ANAC_SOURCE,
            RouteMonthlyFare.emission_period.in_(months),
        ).delete(synchronize_session=False)

        session.add_all(
            RouteMonthlyFare(
                route_key=agg.route_key,
                origin_city=agg.origin_icao,
                origin_state=None,
                destination_city=agg.destination_icao,
                destination_state=None,
                modal=ModalType.AIR,
                service_class=ANAC_ALL_CLASSES,
                trip_month=agg.reference_month,       # mês da venda (a ANAC não informa o do voo)
                emission_period=agg.reference_month,
                source=ANAC_SOURCE,
                sample_size=agg.seats,
                mean_brl=agg.mean_brl,
                min_brl=agg.min_brl,
                max_brl=agg.max_brl,
                p10_brl=agg.p10_brl,
                p25_brl=agg.p25_brl,
                p50_brl=agg.p50_brl,
                p75_brl=agg.p75_brl,
                p90_brl=agg.p90_brl,
            )
            for agg in aggregates
        )
        session.commit()
    # Carga grande: estatísticas novas para o planejador escolher o índice de route_key
    analyze_tables(RouteMonthlyFare.__tablename__)
    return len(aggregates)


def _label(icao: str) -> str:
    iata = to_iata(icao)
    return f"{iata} ({icao})" if iata else icao


def _summary(aggregates: List[AirRouteMonthlyAggregate]) -> None:
    routes = {agg.route_key for agg in aggregates}
    seats = sum(agg.seats for agg in aggregates)
    console.print(f"Rotas: {len(routes):,} — assentos vendidos: {seats:,}".replace(",", "."))

    table = Table(title="Rotas com mais assentos vendidos (tarifa sem taxa de embarque)")
    table.add_column("Rota", style="cyan")
    table.add_column("Mês", justify="center")
    table.add_column("Assentos", justify="right")
    table.add_column("P25", justify="right")
    table.add_column("Mediana", justify="right")
    table.add_column("P75", justify="right")
    for agg in sorted(aggregates, key=lambda a: -a.seats)[:10]:
        table.add_row(
            f"{_label(agg.origin_icao)} -> {_label(agg.destination_icao)}",
            agg.reference_month,
            f"{agg.seats:,}".replace(",", "."),
            f"R$ {agg.p25_brl:,.2f}",
            f"R$ {agg.p50_brl:,.2f}",
            f"R$ {agg.p75_brl:,.2f}",
        )
    console.print(table)


def import_local_file(path: str) -> int:
    """Monta a série mensal de um CSV local e grava no banco."""
    if not os.path.exists(path):
        console.print(f"[red]Arquivo não encontrado:[/red] {path}")
        raise SystemExit(1)

    console.print(f"[cyan]Montando série[/cyan] de {os.path.basename(path)}...")
    aggregates = aggregate_air_monthly_fares(path)
    if not aggregates:
        console.print("[yellow]Nenhuma linha utilizável neste arquivo.[/yellow]")
        return 0

    written = persist_air_monthly(aggregates)
    _summary(aggregates)
    console.print(f"[green]OK[/green] {written:,} linhas de série gravadas.".replace(",", "."))
    return written


async def list_available_files() -> None:
    resources = await ANACOpenDataClient().list_monthly_files(months=24)
    table = Table(title="ANAC — tarifas aéreas domésticas (meses mais recentes)")
    table.add_column("Mês de venda", style="cyan")
    table.add_column("Arquivo")
    for resource in resources:
        table.add_row(resource.reference_period or "—", resource.filename)
    console.print(table)
    console.print("[dim]Para importar os mais recentes: --months N[/dim]")


async def download_and_import(months: int) -> None:
    client = ANACOpenDataClient()
    resources = await client.list_monthly_files(months=months)
    if not resources:
        console.print("[yellow]Nenhum arquivo mensal encontrado no servidor da ANAC.[/yellow]")
        return

    imported: List[str] = []
    with tempfile.TemporaryDirectory(prefix="trivio_anac_") as tmpdir:
        for resource in resources:
            period = resource.reference_period or "desconhecido"
            destination = os.path.join(tmpdir, f"{period}.csv")
            console.print(f"[cyan]Baixando[/cyan] {period} ({resource.filename})...")
            try:
                await client.download_resource(resource, destination)
            except Exception as exc:
                console.print(f"[red]Falha ao baixar {period}:[/red] {type(exc).__name__}: {exc}")
                continue
            try:
                if import_local_file(destination):
                    imported.append(period)
            except Exception as exc:
                console.print(f"[red]Falha ao importar {period}:[/red] {type(exc).__name__}: {exc}")
            finally:
                os.remove(destination)

    console.print(
        f"\n[bold]Meses importados:[/bold] {', '.join(sorted(imported)) or 'nenhum'}. "
        f"O Termômetro aéreo classifica rotas com {MIN_ANAC_MONTHS}+ meses de vendas."
    )


def inspect_file(path: str) -> None:
    """Mostra o cabeçalho real e o mapeamento detectado, sem importar nada."""
    if not os.path.exists(path):
        console.print(f"[red]Arquivo não encontrado:[/red] {path}")
        raise SystemExit(1)
    schema = detect_anac_schema(path)
    console.print(f"\n[bold]Cabeçalho de {os.path.basename(path)}[/bold]: {';'.join(schema.header)}")
    console.print(schema.describe())
    console.print("\n[green]Pronto para importar.[/green]" if schema.is_usable else "\n[red]Formato não reconhecido.[/red]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa tarifas aéreas domésticas reais da ANAC para o Trivio.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Lista os meses publicados.")
    group.add_argument("--inspect", metavar="CSV", help="Mostra o cabeçalho de um CSV já baixado.")
    group.add_argument("--file", metavar="CSV", help="Importa um CSV já baixado.")
    group.add_argument("--months", type=int, metavar="N", help="Baixa e importa os N meses mais recentes.")
    args = parser.parse_args()

    if args.list:
        asyncio.run(list_available_files())
    elif args.inspect:
        inspect_file(args.inspect)
    elif args.file:
        import_local_file(args.file)
    elif args.months:
        asyncio.run(download_and_import(args.months))


if __name__ == "__main__":
    main()
