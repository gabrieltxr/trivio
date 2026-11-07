"""CLI Interativa de Visualização e Teste dos Motores Trivio (Rich CLI Runner).

Executa a esteira analítica completa do Trivio para uma rota:
1. Coleta ou simula cotações aéreas, rodoviárias e de virtual interlining.
2. Executa deduplicação SHA-256 e seleção da melhor oferta por transportadora.
3. Avalia Prós, Contras e Contratempos operacionais (MCT, conexões curtas, neblina, turboélices).
4. Aplica o Termômetro Histórico de Preços (Polars - Quartis P25, P50, P75).
5. Calcula o Trivio Score (Índice de Agonia) e ranqueia as opções na mesma régua.
6. Gera recomendações Hacker Fares (combinações híbridas e split-carrier).
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path
from typing import Any, List, Optional

# Garante suporte UTF-8 no terminal Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.analyzers.interliner import build_virtual_interline_routes
from app.analyzers.price_thermometer import enrich_quotes_batch_with_thermometer
from app.analyzers.route_evaluator import enrich_quote_with_evaluation
from app.analyzers.trivio_scorer import generate_hacker_fares, score_and_rank_quotes
from app.cache.deduplicator import QuoteDeduplicator as Deduplicator
from app.services.air_quotes import get_air_quote_service
from app.collectors.base import StandardizedQuote
from app.collectors.road.clickbus import ClickBusCollector
from app.config import settings
from app.database import SessionLocal

console = Console(force_terminal=True, legacy_windows=False)


def get_sample_quotes(origin: str, dest: str, travel_date: datetime) -> List[StandardizedQuote]:
    """Gera amostras realistas para demonstração e smoke testing da rota."""
    dep_base = travel_date.replace(hour=0, minute=0, second=0, microsecond=0)

    quotes = [
        # 1. Ônibus Leito Cama Noturno (Excelente conforto, elimina diária)
        StandardizedQuote(
            origin_external_id=f"{origin}-tiete",
            destination_external_id=f"{dest}-novo-rio",
            modal="ROAD",
            provider_source="clickbus",
            carrier_name="Auto Viação 1001",
            service_class="LEITO_CAMA",
            departure_time=dep_base.replace(hour=23, minute=30),
            arrival_time=dep_base.replace(hour=23, minute=30) + timedelta(hours=6, minutes=30),
            duration_minutes=390,
            door_to_door_duration_minutes=430,
            base_price=239.90,
            taxes_price=0.0,
            baggage_fee_estimate=0.0,
            transfer_cost_estimate=25.0,
            route_metadata={
                "is_direct": True,
                "stops_count": 0,
                "amenities": ["wi-fi", "poltrona 180 graus", "tomada usb", "manta e travesseiro"],
            },
        ),
        # 2. Voo Direto Ponte Aérea (Rápido, sem mala)
        StandardizedQuote(
            origin_external_id="CGH",
            destination_external_id="SDU",
            modal="AIR",
            provider_source="trivio_simulation",
            carrier_name="LATAM Airlines",
            service_class="ECONOMY",
            departure_time=dep_base.replace(hour=14, minute=0),
            arrival_time=dep_base.replace(hour=15, minute=10),
            duration_minutes=70,
            door_to_door_duration_minutes=190,
            base_price=410.0,
            taxes_price=35.50,
            baggage_fee_estimate=110.0,
            transfer_cost_estimate=38.0,
            route_metadata={
                "is_direct": True,
                "stops_count": 0,
                "aircraft": "Airbus A320",
                "operating_carrier_name": "LATAM Airlines",
            },
        ),
        # 3. Voo com Conexão Apertada em GRU (Alto Risco)
        StandardizedQuote(
            origin_external_id="CGH",
            destination_external_id="SDU",
            modal="AIR",
            provider_source="trivio_simulation",
            carrier_name="GOL Linhas Aéreas",
            service_class="ECONOMY",
            departure_time=dep_base.replace(hour=16, minute=30),
            arrival_time=dep_base.replace(hour=19, minute=45),
            duration_minutes=195,
            door_to_door_duration_minutes=315,
            base_price=289.0,
            taxes_price=42.0,
            baggage_fee_estimate=110.0,
            transfer_cost_estimate=45.0,
            route_metadata={
                "is_direct": False,
                "stops_count": 1,
                "layover_airports": ["GRU"],
                "connection_diagnostics": {
                    "total_connection_penalty_points": 100.0,
                    "tightest_layover_minutes": 42,
                    "is_tight": True,
                },
                "layover_durations_minutes": [42],
            },
        ),
        # 4. Ônibus Convencional Diurno Parador (Alto atrito)
        StandardizedQuote(
            origin_external_id=f"{origin}-barra-funda",
            destination_external_id=f"{dest}-novo-rio",
            modal="ROAD",
            provider_source="clickbus",
            carrier_name="Viação Águia Branca",
            service_class="CONVENCIONAL",
            departure_time=dep_base.replace(hour=8, minute=30),
            arrival_time=dep_base.replace(hour=16, minute=0),
            duration_minutes=450,
            door_to_door_duration_minutes=490,
            base_price=119.50,
            taxes_price=0.0,
            baggage_fee_estimate=0.0,
            transfer_cost_estimate=25.0,
            route_metadata={
                "is_direct": False,
                "stops_count": 4,
                "intermediate_stops": ["Aparecida", "Resende", "Barra Mansa", "Volta Redonda"],
            },
        ),
    ]
    return quotes


async def fetch_live_or_sample_quotes(
    origin: str,
    dest: str,
    travel_date_str: str,
    is_live: bool,
) -> List[StandardizedQuote]:
    """Coleta ao vivo ou carrega amostras de alta fidelidade."""
    dt = datetime.strptime(travel_date_str, "%Y-%m-%d")

    if not is_live:
        return get_sample_quotes(origin, dest, dt)

    console.print(f"[bold cyan]🔍 Executando busca ao vivo em coletores para {origin} -> {dest} em {travel_date_str}...[/bold cyan]")
    quotes: List[StandardizedQuote] = []

    # 1. Coleta Rodoviária
    road_collector = ClickBusCollector()
    try:
        road_quotes = await road_collector.safe_fetch(origin, dest, travel_date_str)
        quotes.extend(road_quotes)
        console.print(f"  [green]✓ ClickBus:[/green] {len(road_quotes)} cotações coletadas")
    except Exception as e:
        console.print(f"  [yellow]⚠ ClickBus indisponível ({e})[/yellow]")

    # 2. Coleta Aérea
    # Usa o mesmo serviço da busca: respeita o cache e o orçamento mensal (cada request é paga).
    air_service = get_air_quote_service()
    try:
        air_quotes = await air_service.get_quotes(origin, dest, travel_date_str)
        quotes.extend(air_quotes)
        console.print(f"  [green]✓ Ignav:[/green] {len(air_quotes)} voos coletados")
    except Exception as e:
        console.print(f"  [yellow]⚠ Aéreo ao vivo indisponível ({e})[/yellow]")

    # Fallback se ambos retornarem vazio
    if not quotes:
        console.print("  [yellow]ℹ Sem resultados ao vivo ou credenciais ausentes. Carregando dados demonstrativos da rota.[/yellow]")
        return get_sample_quotes(origin, dest, dt)

    return quotes


def render_cli_dashboard(
    origin: str,
    dest: str,
    travel_date: str,
    ranked_quotes: List[Any],
    hacker_fares: List[Any],
    hourly_value: float,
):
    """Renderiza a visualização rica no console usando painéis e tabelas Rich."""
    # Banner Principal
    banner_text = Text()
    banner_text.append("TRIVIO TRAVEL INTELLIGENCE\n", style="bold cyan")
    banner_text.append(f"Rota: {origin.upper()} -> {dest.upper()}  |  Data: {travel_date}  |  V_hora: R$ {hourly_value:.2f}/h", style="white")
    console.print(Panel(banner_text, box=box.ROUNDED, border_style="cyan"))

    # Tabela Comparativa de Cotações
    table = Table(
        title="🏆 Ranking Intermodal Inteligente (Ordenado por Trivio Score)",
        box=box.HEAVY_EDGE,
        header_style="bold magenta",
        title_justify="left",
    )

    table.add_column("#", justify="center", style="bold", width=3)
    table.add_column("Modal / Operadora", style="cyan", width=22)
    table.add_column("Horários", justify="center", width=15)
    table.add_column("Tempo P2P", justify="center", width=10)
    table.add_column("Tarifa Base", justify="right", width=11)
    table.add_column("Custo Efetivo", justify="right", style="bold green", width=13)
    table.add_column("Score", justify="right", style="bold yellow", width=8)
    table.add_column("Termômetro", justify="center", width=12)
    table.add_column("Destaques & Contratempos", width=38)

    for idx, q in enumerate(ranked_quotes, 1):
        modal = str(getattr(q, "modal", "")).upper()
        carrier = getattr(q, "carrier_name", "N/A")
        service = str(getattr(q, "service_class", "")).replace("_", " ").title()

        # Ícone do Modal
        if "AIR" in modal and "ROAD" in modal:
            modal_icon = "🔀 Híbrido"
        elif "AIR" in modal:
            modal_icon = "✈️ Aéreo"
        else:
            modal_icon = "🚌 Rodov."

        dep = getattr(q, "departure_time", None)
        arr = getattr(q, "arrival_time", None)
        dep_str = dep.strftime("%H:%M") if dep else "--:--"
        arr_str = arr.strftime("%H:%M") if arr else "--:--"

        d2d_min = getattr(q, "door_to_door_duration_minutes", 0)
        d2d_str = f"{d2d_min // 60}h {d2d_min % 60:02d}m"

        base_p = getattr(q, "base_price", 0.0)
        eff_p = getattr(q, "total_effective_price", 0.0)
        score = getattr(q, "trivio_score", 0.0)

        # Termômetro Badge
        meta = getattr(q, "route_metadata", {}) or {}
        therm = meta.get("price_thermometer", {})
        status = therm.get("status", "MEDIUM")
        if status == "LOW":
            therm_badge = "[bold white on green]  LOW  [/]"
        elif status == "HIGH":
            therm_badge = "[bold white on red] HIGH  [/]"
        else:
            therm_badge = "[bold black on yellow] MEDIUM [/]"

        # Prós, Contras e Contratempos
        notes = []
        pros = meta.get("pros", [])
        if pros:
            notes.append(f"[green]✓ {pros[0][:50]}[/green]")

        cons = meta.get("cons", [])
        if cons:
            notes.append(f"[yellow]• {cons[0][:50]}[/yellow]")

        setbacks = meta.get("potential_setbacks", [])
        if setbacks:
            notes.append(f"[bold red]⚠ {setbacks[0][:50]}[/bold red]")

        notes_str = "\n".join(notes) if notes else "[dim]Sem notas adicionais[/dim]"

        rank_str = f"[bold green]#{idx}[/bold green]" if idx == 1 else f"#{idx}"

        table.add_row(
            rank_str,
            f"{modal_icon}\n[bold]{carrier}[/bold]\n[dim]{service}[/dim]",
            f"{dep_str} -> {arr_str}",
            d2d_str,
            f"R$ {base_p:.2f}",
            f"R$ {eff_p:.2f}",
            f"{score:.1f}",
            therm_badge,
            notes_str,
        )

    console.print(table)
    console.print()

    # Painel de Hacker Fares
    if hacker_fares:
        hf = hacker_fares[0]
        hf_panel = Panel(
            f"[bold green]💡 {hf.hack_title}[/bold green]\n\n"
            f"{hf.hack_description}\n"
            f"[bold white]Preço Combinado (Ida + Volta):[/bold white] [bold green]R$ {hf.combined_effective_price:.2f}[/bold green] "
            f"([bold yellow]Economia Real: R$ {hf.savings_vs_single_modal_brl:.2f}[/bold yellow])\n"
            f"[dim]Tempo total porta a porta acumulado: {hf.combined_door_to_door_minutes // 60}h | Score Combinado: {hf.combined_trivio_score:.1f}[/dim]",
            title="🎯 Oportunidade Hacker Fare Detectada",
            border_style="green",
            box=box.ROUNDED,
        )
        console.print(hf_panel)
        console.print()

    # Dica Didática da Equação
    formula_info = (
        "[dim]Legenda Trivio Score: Score = Preço_Efetivo + (Tempo_P2P * V_hora * F_conforto) + Penalidades. "
        "Menor pontuação indica melhor custo x tempo x conforto.[/dim]"
    )
    console.print(formula_info)


def parse_arguments():
    parser = argparse.ArgumentParser(description="Trivio Travel Intelligence CLI")
    parser.add_argument("--origin", default="SAO", help="Código de origem (IATA ou Rodoviária)")
    parser.add_argument("--dest", default="RIO", help="Código de destino (IATA ou Rodoviária)")
    parser.add_argument("--date", default=None, help="Data de ida YYYY-MM-DD (padrão: D+7)")
    parser.add_argument("--return-date", default=None, help="Data de volta YYYY-MM-DD (padrão: D+9)")
    parser.add_argument("--hourly-value", type=float, default=35.0, help="Valor da hora do viajante em R$")
    parser.add_argument("--live", action="store_true", help="Dispara chamadas de rede ao vivo para coletores")
    return parser.parse_args()


async def run_pipeline(args):
    # Determina datas padrão caso não informadas
    now = datetime.now()
    travel_date = args.date or (now + timedelta(days=7)).strftime("%Y-%m-%d")
    return_date = args.return_date or (now + timedelta(days=9)).strftime("%Y-%m-%d")

    # 1. Coleta ou Carrega cotações de Ida
    quotes_outbound = await fetch_live_or_sample_quotes(args.origin, args.dest, travel_date, args.live)

    # 2. Coleta ou Carrega cotações de Volta (para Hacker Fares)
    quotes_inbound = await fetch_live_or_sample_quotes(args.dest, args.origin, return_date, args.live)

    # 3. Deduplicação e Seleção da Melhor Oferta
    dedup = Deduplicator()
    deduped_out = dedup.deduplicate(quotes_outbound)

    # 4. Enriquecimento com RouteEvaluator (Prós/Contras/Contratempos)
    for q in deduped_out:
        enrich_quote_with_evaluation(q)

    # 5. Enriquecimento com Termômetro de Preço (Polars)
    # Gera distribuição sintética se banco local estiver sem histórico
    sample_history = [120.0, 160.0, 220.0, 250.0, 310.0, 380.0, 450.0, 520.0]
    enrich_quotes_batch_with_thermometer(deduped_out, db=None)
    for q in deduped_out:
        meta = getattr(q, "route_metadata", {})
        if "price_thermometer" not in meta or meta["price_thermometer"].get("status") == "INSUFFICIENT_DATA":
            from app.analyzers.price_thermometer import evaluate_price_distribution
            res = evaluate_price_distribution(q.total_effective_price, sample_history)
            meta["price_thermometer"] = res.to_dict()

    # 6. Cálculo do Trivio Score e Ordenação
    ranked = score_and_rank_quotes(deduped_out, hourly_time_value=args.hourly_value)

    # 7. Geração de Hacker Fares (Ida + Volta)
    hacker_fares = generate_hacker_fares(
        outbound_quotes=deduped_out,
        inbound_quotes=quotes_inbound,
        hourly_time_value=args.hourly_value,
    )

    # 8. Renderização no Console Rich
    render_cli_dashboard(
        origin=args.origin,
        dest=args.dest,
        travel_date=travel_date,
        ranked_quotes=ranked,
        hacker_fares=hacker_fares,
        hourly_value=args.hourly_value,
    )


if __name__ == "__main__":
    cli_args = parse_arguments()
    asyncio.run(run_pipeline(cli_args))
