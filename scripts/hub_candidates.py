"""Hubs candidatos do primeiro lote (Florianópolis, Belo Horizonte, Brasília).

NÃO faz parte da semente padrão (scripts/init_db.py): enquanto não for aplicado, esses hubs
não aparecem na busca. Aplicação explícita, idempotente:

    python scripts/hub_candidates.py --check        # valida a lista, sem gravar
    python scripts/hub_candidates.py --apply        # grava no banco (só o que falta)

Pendências antes de aplicar (ver comentários "CONFERIR"):
- coordenadas das rodoviárias são do centro da cidade (aproximação);
- nomes das rodoviárias precisam ser confirmados;
- Ignav: a cobertura desses aeroportos não foi verificada; cada consulta gasta request.
"""

import argparse
import os
import sys
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collectors.antt.monitriip import normalize_place  # noqa: E402

CANDIDATE_HUBS: List[Dict] = [
    {
        "ibge_code": "4205407",
        "name": "Florianópolis",
        "state": "SC",
        "latitude": -27.5954,
        "longitude": -48.5480,
        "terminals": [
            {   # CONFERIR: nome e coordenadas da rodoviária
                "name": "Rodoviária de Florianópolis", "modal": "ROAD", "official_code": "ROD_FLORIANOPOLIS",
                "latitude": -27.5954, "longitude": -48.5480,
            },
            {
                "name": "Aeroporto Internacional Hercílio Luz", "modal": "AIR", "official_code": "FLN",
                "latitude": -27.6703, "longitude": -48.5525,
                "avg_city_center_distance_km": 12.0, "estimated_transfer_cost_brl": 45.0,
                "estimated_transfer_minutes": 30,
                "mappings": [{"provider_name": "ignav", "external_id": "FLN"}],
            },
        ],
    },
    {
        "ibge_code": "3106200",
        "name": "Belo Horizonte",
        "state": "MG",
        "latitude": -19.9167,
        "longitude": -43.9345,
        "terminals": [
            {   # CONFERIR: nome e coordenadas da rodoviária
                "name": "Rodoviária de Belo Horizonte", "modal": "ROAD", "official_code": "ROD_BELO_HORIZONTE",
                "latitude": -19.9167, "longitude": -43.9345,
            },
            {   # Confins: principal aeroporto comercial da região. Pampulha (PLU) fica de fora deste lote.
                "name": "Aeroporto Internacional Tancredo Neves (Confins)", "modal": "AIR", "official_code": "CNF",
                "latitude": -19.6244, "longitude": -43.9719,
                "avg_city_center_distance_km": 40.0, "estimated_transfer_cost_brl": 120.0,
                "estimated_transfer_minutes": 60,
                "mappings": [{"provider_name": "ignav", "external_id": "CNF"}],
            },
        ],
    },
    {
        "ibge_code": "5300108",
        "name": "Brasília",
        "state": "DF",
        "latitude": -15.7939,
        "longitude": -47.8828,
        "terminals": [
            {   # CONFERIR: nome e coordenadas da rodoviária
                "name": "Rodoviária do Plano Piloto", "modal": "ROAD", "official_code": "ROD_BRASILIA",
                "latitude": -15.7939, "longitude": -47.8828,
            },
            {
                "name": "Aeroporto Internacional de Brasília", "modal": "AIR", "official_code": "BSB",
                "latitude": -15.8697, "longitude": -47.9208,
                "avg_city_center_distance_km": 12.0, "estimated_transfer_cost_brl": 50.0,
                "estimated_transfer_minutes": 25,
                "mappings": [{"provider_name": "ignav", "external_id": "BSB"}],
            },
        ],
    },
]

REQUIRED_HUB_FIELDS = ("ibge_code", "name", "state", "latitude", "longitude", "terminals")


def validate(candidates: List[Dict]) -> List[str]:
    """Erros que impedem aplicar: campos ausentes, códigos repetidos, terminal sem modal."""
    errors: List[str] = []
    seen_ibge, seen_codes = set(), set()
    for hub in candidates:
        for field in REQUIRED_HUB_FIELDS:
            if field not in hub:
                errors.append(f"{hub.get('name', '?')}: falta '{field}'")
        if hub.get("ibge_code") in seen_ibge:
            errors.append(f"IBGE repetido: {hub.get('ibge_code')}")
        seen_ibge.add(hub.get("ibge_code"))
        modals = set()
        for term in hub.get("terminals", []):
            modals.add(term.get("modal"))
            code = term.get("official_code")
            if not code:
                errors.append(f"{hub['name']}: terminal sem official_code")
            if code in seen_codes:
                errors.append(f"código de terminal repetido: {code}")
            seen_codes.add(code)
        if not {"ROAD", "AIR"} <= modals:
            errors.append(f"{hub['name']}: precisa de rodoviária e aeroporto")
    return errors


def route_city_names(candidates: List[Dict]) -> List[str]:
    """Nome normalizado usado nas rotas (ANTT/GTFS): deve bater com a chave de rota."""
    return [normalize_place(h["name"]) for h in candidates]


def apply(candidates: List[Dict]) -> Dict[str, int]:
    from app.database import SessionLocal, ensure_schema
    from app.models import Hub, ProviderMapping, Terminal

    ensure_schema()
    created = {"hubs": 0, "terminals": 0, "mappings": 0}
    with SessionLocal() as db:
        for data in candidates:
            hub = db.query(Hub).filter(Hub.ibge_code == data["ibge_code"]).first()
            if hub is None:
                hub = Hub(ibge_code=data["ibge_code"], name=data["name"], state=data["state"],
                          latitude=data["latitude"], longitude=data["longitude"])
                db.add(hub)
                db.flush()
                created["hubs"] += 1
            for term in data["terminals"]:
                existing = db.query(Terminal).filter(Terminal.official_code == term["official_code"]).first()
                if existing is not None:
                    continue
                terminal = Terminal(
                    hub_id=hub.id, name=term["name"], modal=term["modal"], official_code=term["official_code"],
                    latitude=term["latitude"], longitude=term["longitude"],
                    avg_city_center_distance_km=term.get("avg_city_center_distance_km", 5.0),
                    estimated_transfer_cost_brl=term.get("estimated_transfer_cost_brl", 25.0),
                    estimated_transfer_minutes=term.get("estimated_transfer_minutes", 20),
                )
                db.add(terminal)
                db.flush()
                created["terminals"] += 1
                for mapping in term.get("mappings", []):
                    db.add(ProviderMapping(terminal_id=terminal.id, **mapping))
                    created["mappings"] += 1
        db.commit()
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Valida a lista sem gravar.")
    group.add_argument("--apply", action="store_true", help="Grava os hubs que ainda não existem.")
    args = parser.parse_args()

    errors = validate(CANDIDATE_HUBS)
    if errors:
        print("Erros:"); [print("  -", e) for e in errors]
        raise SystemExit(1)
    print("Válido:", ", ".join(route_city_names(CANDIDATE_HUBS)))
    if args.apply:
        print("Criados:", apply(CANDIDATE_HUBS))


if __name__ == "__main__":
    main()
