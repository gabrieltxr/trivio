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
                "mappings": [{"provider_name": "clickbus", "external_id": "florianopolis-sc"}],  # ver CLICKBUS_SLUG_MAP
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
                "mappings": [{"provider_name": "clickbus", "external_id": "belo-horizonte-mg"}],  # ver CLICKBUS_SLUG_MAP
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
                "mappings": [{"provider_name": "clickbus", "external_id": "brasilia-df"}],  # ver CLICKBUS_SLUG_MAP
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
    """Grava o que falta (mesmas regras da semente padrão, em app/seed.py)."""
    from app.database import SessionLocal, ensure_schema
    from app.seed import upsert_hubs

    ensure_schema()
    with SessionLocal() as db:
        created = upsert_hubs(db, candidates)
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
