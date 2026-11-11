"""CLI da busca do Trivio no terminal (Rich): roda a mesma busca do site, sem navegador.

Abre o WebSocket `/ws/search` dentro do próprio processo (sem subir o uvicorn) e mostra os
eventos que o frontend receberia: ônibus da ANTT/GTFS, voos do Ignav, conexões ônibus + avião,
referência histórica da ANTT e Hacker Fares. Nada aqui é inventado: sem dados no banco ou sem
provedor, a CLI mostra o mesmo aviso que a interface.

Uso:

    # Cidade inteira (HUB:) ou terminal (CGH, TIETE...). Sem --live-air, o Ignav não é consultado.
    python scripts/test_collector.py --origin HUB:SAO --dest HUB:RIO --date 2026-12-18

    # Com voos ao vivo: cada sentido gasta 1 request paga do Ignav (ida e volta = 2)
    python scripts/test_collector.py --origin HUB:SAO --dest HUB:POA --live-air --return-date 2026-12-22

    # Conexão ônibus + avião (cada hub candidato gasta mais 1 request com --live-air)
    python scripts/test_collector.py --origin HUB:PET --dest HUB:RIO --interline --live-air

Usa o banco configurado em DATABASE_URL (rode antes scripts/init_db.py e os importadores).
"""

import argparse
import logging
import sys
import warnings
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

# Garante suporte UTF-8 no terminal Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# A CLI mostra só o resultado da busca; logs e avisos da API ficam para o servidor.
logging.disable(logging.INFO)
warnings.filterwarnings("ignore", message=".*deprecated.*")

from fastapi.testclient import TestClient
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.api.main import app

console = Console()

THERMOMETER_STYLE = {"LOW": "bold green", "MEDIUM": "yellow", "HIGH": "bold red"}
TERMINAL_EVENTS = ("SEARCH_COMPLETED", "SEARCH_ERROR")


def run_search(payload: Dict[str, Any], live_air: bool) -> List[Dict[str, Any]]:
    """Executa a busca pelo WebSocket em processo e devolve todos os eventos recebidos."""
    # Sem --live-air, o serviço aéreo devolve vazio: nenhuma request paga sai daqui.
    air_guard = (
        nullcontext() if live_air
        else patch("app.services.air_quotes.AirQuoteService.get_quotes", return_value=[])
    )
    events: List[Dict[str, Any]] = []
    with air_guard, TestClient(app) as client, client.websocket_connect("/ws/search") as ws:
        ws.send_json({"action": "start_search", **payload})
        while True:
            event = ws.receive_json()
            events.append(event)
            if event.get("event") in TERMINAL_EVENTS:
                return events


def _hhmm(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m %H:%M")
    except (TypeError, ValueError):
        return "-"


def _quotes_table(title: str, quotes: List[Dict[str, Any]]) -> Table:
    table = Table(title=title, box=box.SIMPLE_HEAVY, title_justify="left", header_style="bold magenta")
    table.add_column("#", justify="right", width=3)
    table.add_column("Modal / Empresa", style="cyan")
    table.add_column("Partida -> Chegada", justify="center")
    table.add_column("Porta a porta", justify="right")
    table.add_column("Tarifa", justify="right")
    table.add_column("Custo efetivo", justify="right", style="bold green")
    table.add_column("Score", justify="right", style="bold yellow")
    table.add_column("Termômetro", justify="center")
    table.add_column("Fonte", style="dim")

    ranked = sorted(quotes, key=lambda q: q.get("trivio_score") if q.get("trivio_score") is not None else float("inf"))
    for idx, q in enumerate(ranked, 1):
        meta = q.get("route_metadata") or {}
        thermo = (meta.get("price_thermometer") or {}).get("status", "-")
        estimate = " (estimativa)" if meta.get("is_simulated") else ""
        d2d = q.get("door_to_door_duration_minutes") or q.get("duration_minutes") or 0
        table.add_row(
            str(idx),
            f"{'✈️' if q['modal'] == 'AIR' else '🚌'} {q.get('carrier_name') or '-'}",
            f"{_hhmm(q.get('departure_time'))}\n{_hhmm(q.get('arrival_time'))}",
            f"{d2d // 60}h{d2d % 60:02d}",
            f"R$ {q.get('base_price', 0):.2f}",
            f"R$ {q.get('total_effective_price', 0):.2f}",
            f"{q['trivio_score']:.1f}" if q.get("trivio_score") is not None else "-",
            f"[{THERMOMETER_STYLE.get(thermo, 'dim')}]{thermo}[/]",
            f"{q.get('provider_source', '-')}{estimate}",
        )
    return table


def render(events: List[Dict[str, Any]], live_air: bool) -> None:
    started = next((e for e in events if e.get("event") == "SEARCH_STARTED"), None)
    if started:
        console.print(Panel(
            f"[bold]{started['origin']} -> {started['destination']}[/bold]  |  ida {started['travel_date']}"
            + (f"  |  volta {started['return_date']}" if started.get("return_date") else "")
            + f"\nProvedores: {', '.join(started.get('providers') or []) or 'nenhum'}",
            title="TRIVIO · busca", border_style="cyan", box=box.ROUNDED,
        ))
    if not live_air:
        console.print("[yellow]Aéreo não consultado (sem --live-air): nenhuma request paga do Ignav.[/yellow]")

    for e in events:
        if e.get("event") == "PROVIDER_ERROR":
            console.print(f"[yellow]⚠ {e.get('provider')}: {e.get('error')} ({e.get('reason', '-')})[/yellow]")
        elif e.get("event") == "SEARCH_ERROR":
            console.print(f"[bold red]✖ {e.get('message') or e}[/bold red]")

    quotes = [q for e in events if e.get("event") == "QUOTE_BATCH" for q in e.get("quotes", [])]
    if quotes:
        console.print(_quotes_table("Opções de ida (ordenadas pelo Trivio Score)", quotes))
    elif not any(e.get("event") == "SEARCH_ERROR" for e in events):
        console.print("[dim]Nenhuma cotação para a rota e data.[/dim]")

    for e in events:
        if e.get("event") == "ROAD_REFERENCE":
            lines = []
            for ref in e.get("references", []):
                low, mid, high = ref.get("p25_brl"), ref.get("median_brl"), ref.get("p75_brl")
                price = (f"R$ {low:.0f} a R$ {high:.0f} (mediana R$ {mid:.0f})"
                         if None not in (low, mid, high) else "-")
                lines.append(f"{ref.get('service_class', '-')}: {price} · {ref.get('reference_period', '')}")
            if lines:
                console.print(Panel("\n".join(lines), title="Referência histórica ANTT (não é oferta)",
                                    border_style="blue", box=box.ROUNDED))
        elif e.get("event") == "HACKER_FARES":
            for combo in e.get("combinations", [])[:3]:
                console.print(Panel(
                    f"{combo.get('hack_description', '')}\n"
                    f"Ida + volta: [bold green]R$ {combo.get('combined_effective_price', 0):.2f}[/bold green]  "
                    f"(economia R$ {combo.get('savings_vs_single_modal_brl', 0):.2f})",
                    title=f"💡 {combo.get('hack_title', 'Hacker Fare')}", border_style="green", box=box.ROUNDED,
                ))

    done = next((e for e in events if e.get("event") == "SEARCH_COMPLETED"), None)
    if done:
        console.print(
            f"[dim]{done['total_quotes']} opções em {done['total_elapsed_ms']:.0f} ms · "
            f"menor custo efetivo R$ {done['cheapest_effective_price']:.2f} · "
            f"melhor score {done['best_trivio_score']:.1f}[/dim]"
        )


def parse_arguments() -> argparse.Namespace:
    default_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    parser = argparse.ArgumentParser(description="Busca do Trivio no terminal (mesmo fluxo do /ws/search)")
    parser.add_argument("--origin", default="HUB:SAO", help="HUB:<código> para a cidade, ou código do terminal (CGH, TIETE)")
    parser.add_argument("--dest", default="HUB:RIO", help="HUB:<código> para a cidade, ou código do terminal")
    parser.add_argument("--date", default=default_date, help="Data de ida AAAA-MM-DD (padrão: D+7)")
    parser.add_argument("--return-date", default=None, help="Data de volta AAAA-MM-DD (habilita Hacker Fares)")
    parser.add_argument("--hourly-value", type=float, default=None, help="Valor da hora do viajante em R$")
    parser.add_argument("--interline", action="store_true", help="Inclui conexões ônibus + avião")
    parser.add_argument("--live-air", action="store_true", help="Consulta o Ignav (gasta requests pagas)")
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    payload: Dict[str, Any] = {
        "origin": args.origin,
        "destination": args.dest,
        "date": args.date,
        "enable_virtual_interlining": args.interline,
    }
    if args.return_date:
        payload["return_date"] = args.return_date
    if args.hourly_value is not None:
        payload["hourly_value"] = args.hourly_value
    render(run_search(payload, live_air=args.live_air), live_air=args.live_air)


if __name__ == "__main__":
    main()
