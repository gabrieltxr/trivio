"""Database Initialization and Seed Script for Trivio.

Creates database tables and populates canonical hubs, terminals, and provider
mappings for the initial multi-modal network corridors:
- São Paulo (SP)
- Campinas (SP)
- Rio de Janeiro (RJ)
- Curitiba (PR)
- Porto Alegre (RS)
- Pelotas (RS)
- Rio Grande (RS)
"""

import sys
import argparse
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

from app.database import Base, engine, SessionLocal, ensure_schema
from app.models import Hub, ModalType
from app.seed import upsert_hubs

console = Console()


SEED_HUBS = [
    {
        "ibge_code": "3550308",
        "name": "São Paulo",
        "state": "SP",
        "latitude": -23.5505,
        "longitude": -46.6333,
        "terminals": [
            {
                "name": "Aeroporto de Congonhas",
                "modal": ModalType.AIR,
                "official_code": "CGH",
                "latitude": -23.6273,
                "longitude": -46.6565,
                "avg_city_center_distance_km": 9.5,
                "estimated_transfer_cost_brl": 38.0,
                "estimated_transfer_minutes": 25,
                "mappings": [
                    {"provider_name": "ignav", "external_id": "CGH"},
                ],
            },
            {
                "name": "Aeroporto Internacional de Guarulhos",
                "modal": ModalType.AIR,
                "official_code": "GRU",
                "latitude": -23.4356,
                "longitude": -46.4731,
                "avg_city_center_distance_km": 25.0,
                "estimated_transfer_cost_brl": 75.0,
                "estimated_transfer_minutes": 50,
                "mappings": [
                    {"provider_name": "ignav", "external_id": "GRU"},
                ],
            },
            {
                "name": "Terminal Rodoviário do Tietê",
                "modal": ModalType.ROAD,
                "official_code": "TIETE",
                "latitude": -23.5161,
                "longitude": -46.6253,
                "avg_city_center_distance_km": 5.0,
                "estimated_transfer_cost_brl": 22.0,
                "estimated_transfer_minutes": 15,
                "mappings": [
                    {"provider_name": "clickbus", "external_id": "sao-paulo-tiete-sp"},
                ],
            },
            {
                "name": "Terminal Rodoviário Barra Funda",
                "modal": ModalType.ROAD,
                "official_code": "BARRA_FUNDA",
                "latitude": -23.5258,
                "longitude": -46.6669,
                "avg_city_center_distance_km": 5.5,
                "estimated_transfer_cost_brl": 25.0,
                "estimated_transfer_minutes": 20,
                "mappings": [
                    {"provider_name": "clickbus", "external_id": "sao-paulo-barra-funda-sp"},
                ],
            },
        ],
    },
    {
        "ibge_code": "3509502",
        "name": "Campinas",
        "state": "SP",
        "latitude": -22.9056,
        "longitude": -47.0608,
        "terminals": [
            {
                "name": "Aeroporto Internacional de Viracopos",
                "modal": ModalType.AIR,
                "official_code": "VCP",
                "latitude": -23.0074,
                "longitude": -47.1345,
                "avg_city_center_distance_km": 14.0,
                "estimated_transfer_cost_brl": 45.0,
                "estimated_transfer_minutes": 25,
                "mappings": [
                    {"provider_name": "ignav", "external_id": "VCP"},
                ],
            },
            {
                "name": "Terminal Multimodal Ramos de Azevedo",
                "modal": ModalType.ROAD,
                "official_code": "ROD_CAMPINAS",
                "latitude": -22.9058,
                "longitude": -47.0700,
                "avg_city_center_distance_km": 1.5,
                "estimated_transfer_cost_brl": 15.0,
                "estimated_transfer_minutes": 10,
                "mappings": [
                    {"provider_name": "clickbus", "external_id": "campinas-sp"},
                ],
            },
        ],
    },
    {
        "ibge_code": "3304557",
        "name": "Rio de Janeiro",
        "state": "RJ",
        "latitude": -22.9068,
        "longitude": -43.1729,
        "terminals": [
            {
                "name": "Aeroporto Santos Dumont",
                "modal": ModalType.AIR,
                "official_code": "SDU",
                "latitude": -22.9105,
                "longitude": -43.1631,
                "avg_city_center_distance_km": 2.0,
                "estimated_transfer_cost_brl": 18.0,
                "estimated_transfer_minutes": 10,
                "mappings": [
                    {"provider_name": "ignav", "external_id": "SDU"},
                ],
            },
            {
                "name": "Aeroporto Internacional do Galeão",
                "modal": ModalType.AIR,
                "official_code": "GIG",
                "latitude": -22.8089,
                "longitude": -43.2436,
                "avg_city_center_distance_km": 18.0,
                "estimated_transfer_cost_brl": 65.0,
                "estimated_transfer_minutes": 35,
                "mappings": [
                    {"provider_name": "ignav", "external_id": "GIG"},
                ],
            },
            {
                "name": "Rodoviária Novo Rio",
                "modal": ModalType.ROAD,
                "official_code": "NOVO_RIO",
                "latitude": -22.8988,
                "longitude": -43.2096,
                "avg_city_center_distance_km": 3.5,
                "estimated_transfer_cost_brl": 20.0,
                "estimated_transfer_minutes": 15,
                "mappings": [
                    {"provider_name": "clickbus", "external_id": "rio-de-janeiro-novo-rio-rj"},
                ],
            },
        ],
    },
    {
        "ibge_code": "4106902",
        "name": "Curitiba",
        "state": "PR",
        "latitude": -25.4284,
        "longitude": -49.2733,
        "terminals": [
            {
                "name": "Aeroporto Internacional Afonso Pena",
                "modal": ModalType.AIR,
                "official_code": "CWB",
                "latitude": -25.5285,
                "longitude": -49.1758,
                "avg_city_center_distance_km": 18.0,
                "estimated_transfer_cost_brl": 50.0,
                "estimated_transfer_minutes": 35,
                "mappings": [
                    {"provider_name": "ignav", "external_id": "CWB"},
                ],
            },
            {
                "name": "Rodoferroviária de Curitiba",
                "modal": ModalType.ROAD,
                "official_code": "RODO_CWB",
                "latitude": -25.4367,
                "longitude": -49.2569,
                "avg_city_center_distance_km": 2.0,
                "estimated_transfer_cost_brl": 16.0,
                "estimated_transfer_minutes": 10,
                "mappings": [
                    {"provider_name": "clickbus", "external_id": "curitiba-pr"},
                ],
            },
        ],
    },
    {
        "ibge_code": "4314902",
        "name": "Porto Alegre",
        "state": "RS",
        "latitude": -30.0346,
        "longitude": -51.2177,
        "terminals": [
            {
                "name": "Aeroporto Internacional Salgado Filho",
                "modal": ModalType.AIR,
                "official_code": "POA",
                "latitude": -29.9935,
                "longitude": -51.1711,
                "avg_city_center_distance_km": 8.0,
                "estimated_transfer_cost_brl": 30.0,
                "estimated_transfer_minutes": 20,
                "mappings": [
                    {"provider_name": "ignav", "external_id": "POA"},
                ],
            },
            {
                "name": "Estação Rodoviária de Porto Alegre",
                "modal": ModalType.ROAD,
                "official_code": "ROD_POA",
                "latitude": -30.0239,
                "longitude": -51.2185,
                "avg_city_center_distance_km": 1.5,
                "estimated_transfer_cost_brl": 15.0,
                "estimated_transfer_minutes": 8,
                "mappings": [
                    {"provider_name": "clickbus", "external_id": "porto-alegre-rs"},
                ],
            },
        ],
    },
    {
        "ibge_code": "4314407",
        "name": "Pelotas",
        "state": "RS",
        "latitude": -31.7654,
        "longitude": -52.3376,
        "terminals": [
            {
                "name": "Aeroporto Internacional de Pelotas",
                "modal": ModalType.AIR,
                "official_code": "PET",
                "latitude": -31.7183,
                "longitude": -52.3275,
                "avg_city_center_distance_km": 7.0,
                "estimated_transfer_cost_brl": 25.0,
                "estimated_transfer_minutes": 15,
                "mappings": [
                    {"provider_name": "ignav", "external_id": "PET"},
                ],
            },
            {
                "name": "Terminal Rodoviário de Pelotas",
                "modal": ModalType.ROAD,
                "official_code": "ROD_PELOTAS",
                "latitude": -31.7456,
                "longitude": -52.3298,
                "avg_city_center_distance_km": 2.5,
                "estimated_transfer_cost_brl": 16.0,
                "estimated_transfer_minutes": 10,
                "mappings": [
                    {"provider_name": "clickbus", "external_id": "pelotas-rs"},
                ],
            },
        ],
    },
    {
        "ibge_code": "4315602",
        "name": "Rio Grande",
        "state": "RS",
        "latitude": -32.0350,
        "longitude": -52.0986,
        "terminals": [
            {
                "name": "Aeroporto Regional de Rio Grande",
                "modal": ModalType.AIR,
                "official_code": "RIG",
                "latitude": -32.0833,
                "longitude": -52.1667,
                "avg_city_center_distance_km": 10.0,
                "estimated_transfer_cost_brl": 30.0,
                "estimated_transfer_minutes": 20,
                "mappings": [
                    {"provider_name": "ignav", "external_id": "RIG"},
                ],
            },
            {
                "name": "Estação Rodoviária de Rio Grande",
                "modal": ModalType.ROAD,
                "official_code": "ROD_RIO_GRANDE",
                "latitude": -32.0319,
                "longitude": -52.0944,
                "avg_city_center_distance_km": 1.0,
                "estimated_transfer_cost_brl": 12.0,
                "estimated_transfer_minutes": 5,
                "mappings": [
                    {"provider_name": "clickbus", "external_id": "rio-grande-rs"},
                ],
            },
        ],
    },
]


def init_database(reset: bool = False):
    """Create database tables and seed baseline data."""
    console.rule("[bold cyan]Trivio Database Initialization[/bold cyan]")

    if reset:
        console.print("[yellow]Dropping existing database tables...[/yellow]")
        Base.metadata.drop_all(bind=engine)

    # ensure_schema, e não só create_all: num banco antigo também acrescenta colunas novas,
    # troca restrições únicas e remove índices aposentados (create_all não mexe em tabela existente).
    console.print("[green]Creating database tables...[/green]")
    ensure_schema(engine)

    db = SessionLocal()
    try:
        created = upsert_hubs(db, SEED_HUBS)
        total_hubs, total_terminals, total_mappings = created["hubs"], created["terminals"], created["mappings"]

        db.commit()

        # Display Summary Table
        table = Table(title="Trivio Initial Seed Summary", show_header=True, header_style="bold magenta")
        table.add_column("Hub / Cidade", style="cyan")
        table.add_column("UF", justify="center")
        table.add_column("Terminais", justify="center")
        table.add_column("Modais Disponíveis", style="green")

        for hub in db.query(Hub).order_by(Hub.name).all():
            modals = {t.modal.value for t in hub.terminals}
            term_names = ", ".join(f"{t.official_code or t.name} ({t.modal.value})" for t in hub.terminals)
            table.add_row(hub.name, hub.state, str(len(hub.terminals)), term_names)

        console.print(table)
        console.print(
            f"[bold green]Seed completed successfully![/bold green] "
            f"Seeded: {total_hubs} hubs, {total_terminals} terminals, {total_mappings} mappings."
        )

    except Exception as e:
        db.rollback()
        console.print(f"[bold red]Error during seed execution:[/bold red] {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize Trivio database schema and seed data.")
    parser.add_argument("--reset", action="store_true", help="Drop all tables and re-create schema from scratch.")
    args = parser.parse_args()

    init_database(reset=args.reset)
