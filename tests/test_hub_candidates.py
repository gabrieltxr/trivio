"""Hubs candidatos do primeiro lote: lista válida e fora da semente padrão."""

import importlib.util
from pathlib import Path

from app.collectors.antt.monitriip import build_route_key

_spec = importlib.util.spec_from_file_location(
    "hub_candidates", Path(__file__).resolve().parent.parent / "scripts" / "hub_candidates.py"
)
hub_candidates = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hub_candidates)


def test_candidate_list_is_valid():
    assert hub_candidates.validate(hub_candidates.CANDIDATE_HUBS) == []


def test_validation_catches_duplicates_and_missing_modal():
    bad = [
        {**hub_candidates.CANDIDATE_HUBS[0], "terminals": [hub_candidates.CANDIDATE_HUBS[0]["terminals"][0]]},
        {**hub_candidates.CANDIDATE_HUBS[0], "name": "Outra"},   # mesmo IBGE
    ]
    errors = hub_candidates.validate(bad)
    assert any("aeroporto" in e for e in errors)
    assert any("IBGE repetido" in e for e in errors)


def test_city_names_match_the_route_keys_used_by_antt_and_gtfs():
    """O nome do hub precisa gerar a mesma chave de rota que as fontes de dados usam."""
    keys = {
        name: build_route_key(name, "SP", "SAO PAULO", "SP")
        for name in hub_candidates.route_city_names(hub_candidates.CANDIDATE_HUBS)
    }
    assert keys["FLORIANOPOLIS"] == "FLORIANOPOLIS-SP|SAO PAULO-SP"
    assert "BRASILIA" in hub_candidates.route_city_names(hub_candidates.CANDIDATE_HUBS)
    assert "BELO HORIZONTE" in hub_candidates.route_city_names(hub_candidates.CANDIDATE_HUBS)


def test_candidates_are_not_in_the_default_seed():
    """Enquanto não forem aplicados, os hubs não aparecem na busca nem no seed padrão."""
    init_db = importlib.util.spec_from_file_location(
        "init_db", Path(__file__).resolve().parent.parent / "scripts" / "init_db.py"
    )
    module = importlib.util.module_from_spec(init_db)
    init_db.loader.exec_module(module)
    default_names = {h["name"] for h in module.SEED_HUBS}
    for hub in hub_candidates.CANDIDATE_HUBS:
        assert hub["name"] not in default_names
