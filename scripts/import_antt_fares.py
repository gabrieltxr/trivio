"""Importa tarifas históricas reais da ANTT (Monitriip) para o Termômetro de Preço.

Fonte: Portal de Dados Abertos da ANTT (https://dados.antt.gov.br), conjunto
`monitriip-bilhetes-de-passagem` — bilhetes efetivamente comercializados no
transporte rodoviário interestadual e internacional, CSV mensal, licença CC-BY.

Uso:

    # 1. Ver quais arquivos mensais estão publicados
    python scripts/import_antt_fares.py --list

    # 2. Conferir o cabeçalho real de um período antes de importar (baixa só esse mês)
    python scripts/import_antt_fares.py --inspect-remote 2024-10
    #    e guardar o CSV numa pasta sua para abrir e ver as linhas
    python scripts/import_antt_fares.py --inspect-remote 2024-10 --save-dir D:/Dados/ANTT

    #    ou de um CSV que já está no disco
    python scripts/import_antt_fares.py --inspect caminho/venda_passagem_09_2024.csv

    # 3. Baixar e importar os N meses mais recentes
    python scripts/import_antt_fares.py --months 3

    # 4. Importar um arquivo já baixado
    python scripts/import_antt_fares.py --file caminho/venda_passagem_09_2024.csv

Os bilhetes individuais NÃO são persistidos: guardamos apenas os percentis por
corredor e classe de serviço (tabela `route_fare_statistics`).
"""

import argparse
import asyncio
import logging
import os
import sys
import tempfile
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.table import Table

from app.collectors.antt import (
    ANTTOpenDataClient,
    ANTTResource,
    RouteMonthlyAggregate,
    aggregate_monthly_fares,
    check_date_consistency,
    detect_monitriip_schema,
)
from app.analyzers.price_thermometer import MIN_OFFICIAL_MONTHS
from app.database import SessionLocal, analyze_tables, engine, ensure_schema
from app.models import ModalType, RouteMonthlyFare

console = Console()
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def persist_monthly(
    aggregates: List[RouteMonthlyAggregate],
    emission_period: str,
    source: str = "antt_monitriip",
) -> int:
    """Grava a série mensal de um arquivo de emissão, substituindo o que já existia dele.

    Substituir em vez de atualizar linha a linha garante que reimportar o mesmo mês
    não duplica nada, e que uma correção da fonte chega inteira.
    """
    ensure_schema(engine)
    with SessionLocal() as session:
        session.query(RouteMonthlyFare).filter(
            RouteMonthlyFare.emission_period == emission_period,
            RouteMonthlyFare.source == source,
        ).delete(synchronize_session=False)

        session.add_all(
            RouteMonthlyFare(
                route_key=agg.route_key,
                origin_city=agg.origin_city,
                origin_state=agg.origin_state,
                destination_city=agg.destination_city,
                destination_state=agg.destination_state,
                modal=ModalType.ROAD,
                service_class=agg.service_class,
                trip_month=agg.trip_month,
                emission_period=agg.emission_period,
                source=source,
                sample_size=agg.sample_size,
                mean_brl=agg.mean_brl,
                min_brl=agg.min_brl,
                max_brl=agg.max_brl,
            )
            for agg in aggregates
        )
        session.commit()
    # Carga grande: estatísticas novas para o planejador escolher o índice de route_key
    analyze_tables(RouteMonthlyFare.__tablename__)
    return len(aggregates)


def import_local_file(path: str, reference_period: Optional[str] = None) -> None:
    """Monta a série mensal de um CSV local e grava no banco."""
    if not os.path.exists(path):
        console.print(f"[red]Arquivo não encontrado:[/red] {path}")
        raise SystemExit(1)

    period = reference_period or _period_from_filename(path)
    if not period:
        console.print("[red]Não foi possível identificar o mês de emissão. Use --period AAAA-MM.[/red]")
        raise SystemExit(1)

    console.print(f"[cyan]Montando série mensal[/cyan] de {os.path.basename(path)} (emissão {period})...")

    dates = check_date_consistency(path)
    if dates:
        console.print(
            f"[dim]Viagens anteriores à emissão, descartadas: "
            f"{dates['viagem_antes_da_emissao']:,} de {dates['total_linhas']:,} linhas "
            f"({dates['proporcao'] * 100:.2f}%).[/dim]".replace(",", ".")
        )

    aggregates = aggregate_monthly_fares(path)
    if not aggregates:
        console.print("[yellow]Nenhuma linha utilizável neste arquivo.[/yellow]")
        return

    written = persist_monthly(aggregates, emission_period=period)
    _coverage(aggregates)
    console.print(f"[green]OK[/green] {written:,} linhas de série gravadas.".replace(",", "."))


def _period_from_filename(path: str) -> Optional[str]:
    import re
    match = re.search(r"(\d{2})[_-](\d{4})", os.path.basename(path))
    return f"{match.group(2)}-{match.group(1)}" if match else None


def _coverage(aggregates: List[RouteMonthlyAggregate]) -> None:
    """Quantas rotas já têm meses suficientes para o Termômetro classificar."""
    months_by_route: Dict[tuple, set] = {}
    tickets_by_route: Dict[tuple, int] = {}
    for agg in aggregates:
        key = (agg.route_key, agg.service_class)
        months_by_route.setdefault(key, set()).add(agg.trip_month)
        tickets_by_route[key] = tickets_by_route.get(key, 0) + agg.sample_size

    total = len(months_by_route)
    ready = sum(1 for months in months_by_route.values() if len(months) >= MIN_OFFICIAL_MONTHS)
    console.print(
        f"Rotas e classes: {total:,} — com {MIN_OFFICIAL_MONTHS}+ meses de viagem neste arquivo: "
        f"{ready:,} (o resto aguarda outros meses)".replace(",", ".")
    )

    table = Table(title="Maior volume de bilhetes (tarifa normal)")
    table.add_column("Rota", style="cyan")
    table.add_column("Classe", style="magenta")
    table.add_column("Meses", justify="right")
    table.add_column("Bilhetes", justify="right")
    for key, tickets in sorted(tickets_by_route.items(), key=lambda kv: -kv[1])[:10]:
        route_key, service = key
        table.add_row(
            route_key.replace("|", " -> "),
            service,
            str(len(months_by_route[key])),
            f"{tickets:,}".replace(",", "."),
        )
    console.print(table)


async def list_available_files() -> List[ANTTResource]:
    client = ANTTOpenDataClient()
    resources = await client.list_monthly_ticket_files()

    table = Table(title="Monitriip — arquivos mensais publicados pela ANTT")
    table.add_column("Período", style="cyan")
    table.add_column("Arquivo")

    # A página do portal não informa tamanho; por isso a tabela não tem essa coluna.
    for resource in resources[:24]:
        table.add_row(resource.reference_period or "—", resource.filename)

    console.print(table)
    console.print(
        f"[dim]{len(resources)} arquivos CSV disponíveis. "
        "Para inspecionar um deles: --inspect-remote AAAA-MM[/dim]"
    )
    return resources


async def download_for_period(period: str, destination_dir: str) -> str:
    """Baixa o arquivo do período informado (AAAA-MM) e devolve o caminho local."""
    client = ANTTOpenDataClient()
    resources = await client.list_monthly_ticket_files()
    match = next((r for r in resources if r.reference_period == period), None)
    if match is None:
        available = ", ".join(sorted({r.reference_period for r in resources if r.reference_period}))
        console.print(f"[red]Período {period} não publicado.[/red] Disponíveis: {available}")
        raise SystemExit(1)

    destination = os.path.join(destination_dir, match.filename)
    console.print(f"[cyan]Baixando[/cyan] {match.filename} (aguarda o Crawl-Delay do portal)...")
    await client.download_resource(match, destination)
    return destination


def inspect_remote(period: str, save_dir: Optional[str] = None) -> None:
    """Baixa o arquivo de um período e mostra o cabeçalho.

    Sem `save_dir`, usa uma pasta temporária que é apagada ao final do comando.
    Com `save_dir`, mantém o CSV nessa pasta para você abrir e ver as linhas.
    """
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        path = asyncio.run(download_for_period(period, save_dir))
        console.print(f"[green]Arquivo salvo em:[/green] {path}")
        inspect_file(path)
        return

    with tempfile.TemporaryDirectory(prefix="trivio_antt_") as tmpdir:
        path = asyncio.run(download_for_period(period, tmpdir))
        inspect_file(path)


async def download_and_import(months: int) -> None:
    client = ANTTOpenDataClient()
    resources = await client.list_monthly_ticket_files(months=months)

    if not resources:
        console.print("[yellow]A API da ANTT não devolveu arquivos CSV mensais.[/yellow]")
        return

    with tempfile.TemporaryDirectory(prefix="trivio_antt_") as tmpdir:
        for resource in resources:
            period = resource.reference_period or "desconhecido"
            destination = os.path.join(tmpdir, f"venda_passagem_{period}.csv")
            console.print(f"[cyan]Baixando[/cyan] {period}...")
            try:
                await client.download_resource(resource, destination)
            except Exception as exc:
                console.print(f"[red]Falha ao baixar {period}:[/red] {exc}")
                continue
            try:
                import_local_file(destination, reference_period=period)
            except Exception as exc:
                console.print(f"[red]Falha ao importar {period}:[/red] {type(exc).__name__}: {exc}")
                continue


def inspect_file(path: str) -> None:
    """Mostra o cabeçalho real e o mapeamento detectado, sem importar nada."""
    if not os.path.exists(path):
        console.print(f"[red]Arquivo não encontrado:[/red] {path}")
        raise SystemExit(1)

    schema = detect_monitriip_schema(path)
    console.print(f"\n[bold]Cabeçalho de {os.path.basename(path)}[/bold]")
    for column in schema.header:
        console.print(f"  • {column}")
    console.print()
    console.print(schema.describe())
    if schema.is_usable:
        console.print("\n[green]Pronto para importar.[/green]")
    elif schema.is_aggregated:
        console.print("\n[red]Não importar: o formato agregado ainda não é suportado.[/red]")
    else:
        console.print("\n[red]Ajuste os apelidos de coluna antes de importar.[/red]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa tarifas históricas reais da ANTT (Monitriip) para o Trivio.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Lista os arquivos mensais publicados.")
    group.add_argument("--inspect", metavar="CSV", help="Mostra o cabeçalho de um CSV já existente no disco.")
    group.add_argument("--inspect-remote", metavar="AAAA-MM", help="Baixa o arquivo do período e mostra o cabeçalho.")
    group.add_argument("--file", metavar="CSV", help="Importa um CSV já baixado.")
    group.add_argument("--months", type=int, metavar="N", help="Baixa e importa os N meses mais recentes.")
    parser.add_argument("--period", help="Período de referência (ex: 2024-09), se não der para inferir do nome.")
    parser.add_argument(
        "--save-dir",
        metavar="PASTA",
        help="Com --inspect-remote: guarda o CSV nesta pasta em vez de apagá-lo ao final.",
    )

    args = parser.parse_args()

    if args.list:
        asyncio.run(list_available_files())
    elif args.inspect:
        inspect_file(args.inspect)
    elif args.inspect_remote:
        inspect_remote(args.inspect_remote, save_dir=args.save_dir)
    elif args.file:
        import_local_file(args.file, reference_period=args.period)
    elif args.months:
        asyncio.run(download_and_import(args.months))


if __name__ == "__main__":
    main()
