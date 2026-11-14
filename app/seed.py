"""Gravação idempotente de hubs, terminais e mapeamentos de provedor.

Usada pela semente padrão (scripts/init_db.py) e pelos hubs candidatos
(scripts/hub_candidates.py), para que as duas listas sigam as mesmas regras:

- hub: identificado pelo código IBGE;
- terminal: identificado pelo código oficial (IATA ou sigla da rodoviária), que a busca
  trata como único no cadastro inteiro (ver `find_terminal_by_external_id`);
- mapeamento: identificado por (provedor, id externo), como na restrição da tabela.

Nada que já existe é alterado: rodar de novo só acrescenta o que falta.
"""

from typing import Any, Dict, Iterable, Optional

from sqlalchemy.orm import Session

from app.models import Hub, ModalType, ProviderMapping, Terminal

# Valores de transfer urbano usados quando o terminal da lista não informa os seus.
DEFAULT_TERMINAL_VALUES: Dict[str, Any] = {
    "avg_city_center_distance_km": 5.0,
    "estimated_transfer_cost_brl": 25.0,
    "estimated_transfer_minutes": 20,
}


def _modal(value: Any) -> ModalType:
    return value if isinstance(value, ModalType) else ModalType(str(value).upper())


def upsert_hubs(
    db: Session,
    hubs: Iterable[Dict[str, Any]],
    terminal_defaults: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    """Grava os hubs, terminais e mapeamentos que faltam. Devolve quantos foram criados.

    Não faz commit: quem chama decide (e desfaz em caso de erro).
    """
    defaults = {**DEFAULT_TERMINAL_VALUES, **(terminal_defaults or {})}
    created = {"hubs": 0, "terminals": 0, "mappings": 0}

    for hub_data in hubs:
        hub = db.query(Hub).filter(Hub.ibge_code == hub_data["ibge_code"]).first()
        if hub is None:
            hub = Hub(
                ibge_code=hub_data["ibge_code"],
                name=hub_data["name"],
                state=hub_data["state"],
                latitude=hub_data["latitude"],
                longitude=hub_data["longitude"],
            )
            db.add(hub)
            db.flush()
            created["hubs"] += 1

        for term_data in hub_data["terminals"]:
            terminal = db.query(Terminal).filter(Terminal.official_code == term_data["official_code"]).first()
            if terminal is None:
                terminal = Terminal(
                    hub_id=hub.id,
                    name=term_data["name"],
                    modal=_modal(term_data["modal"]),
                    official_code=term_data["official_code"],
                    latitude=term_data["latitude"],
                    longitude=term_data["longitude"],
                    **{key: term_data.get(key, value) for key, value in defaults.items()},
                )
                db.add(terminal)
                db.flush()
                created["terminals"] += 1

            for map_data in term_data.get("mappings", []):
                exists = db.query(ProviderMapping.id).filter(
                    ProviderMapping.provider_name == map_data["provider_name"],
                    ProviderMapping.external_id == map_data["external_id"],
                ).first()
                if exists is None:
                    db.add(ProviderMapping(
                        terminal_id=terminal.id,
                        provider_name=map_data["provider_name"],
                        external_id=map_data["external_id"],
                    ))
                    created["mappings"] += 1

    db.flush()
    return created
