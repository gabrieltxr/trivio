"""Trivio Entity Resolution Module.

Uses RapidFuzz for fuzzy matching and normalization of user search inputs
and third-party provider terminal/city names to Trivio Hubs and Terminals.
"""

import re
from dataclasses import dataclass
from typing import Optional, Dict
from rapidfuzz import fuzz, process
from sqlalchemy.orm import Session

from app.text import strip_accents
from app.models import Hub, Terminal, ModalType


@dataclass
class ResolvedEntity:
    entity_type: str  # "HUB" or "TERMINAL"
    id: int
    name: str
    state: str
    official_code: Optional[str] = None
    modal: Optional[ModalType] = None
    latitude: float = 0.0
    longitude: float = 0.0
    confidence_score: float = 100.0


# Prefixo que marca uma cidade inteira (hub) na busca. Sem ele, um código como "CWB" é o
# aeroporto: vários hubs têm o mesmo código do próprio aeroporto (CWB, POA, VCP, PET, RIG).
HUB_CODE_PREFIX = "HUB:"

# Código curto de cada hub (fonte única para o autocomplete e para a resolução da busca).
# Cobre a semente (scripts/init_db.py) e os candidatos (scripts/hub_candidates.py).
HUB_METRO_CODES: Dict[str, str] = {
    "sao paulo": "SAO",
    "rio de janeiro": "RIO",
    "curitiba": "CWB",
    "porto alegre": "POA",
    "campinas": "VCP",
    "pelotas": "PET",
    "rio grande": "RIG",
    "belo horizonte": "BHZ",
    "brasilia": "BSB",
    "florianopolis": "FLN",
}

# Texto em formato de código (ex.: "SSA", "REC"). Se não for um terminal nem um hub
# cadastrado, não passa pela busca aproximada: "SSA" não pode virar "São Paulo".
_CODE_SHAPED = re.compile(r"[A-Z0-9]{2,6}")


def normalize_text(text: str) -> str:
    """Normalize string by lowercasing, stripping accents, and removing excess whitespace."""
    if not text:
        return ""
    stripped = strip_accents(text)
    # Remove special punctuation and lowercase
    cleaned = re.sub(r"[^\w\s]", " ", stripped).lower()
    return re.sub(r"\s+", " ", cleaned).strip()


def hub_metro_code(hub_name: str) -> str:
    """Código curto do hub (ex.: 'SAO'); hubs fora da tabela usam as 3 primeiras letras."""
    norm = normalize_text(hub_name)
    return HUB_METRO_CODES.get(norm, norm.replace(" ", "")[:3].upper())


class EntityResolver:
    """Resolves arbitrary text queries to canonical Hubs and Terminals in the database."""

    def __init__(self, db: Session):
        self.db = db

    def resolve_official_code(self, code: str) -> Optional[ResolvedEntity]:
        """Look for direct official code match (e.g. IATA 'GRU', 'CGH', 'SDU', 'GIG', 'CWB')."""
        if not code or len(code.strip()) > 10:
            return None

        clean_code = code.strip().upper()
        terminal = (
            self.db.query(Terminal)
            .filter(Terminal.official_code == clean_code)
            .first()
        )
        if terminal:
            return ResolvedEntity(
                entity_type="TERMINAL",
                id=terminal.id,
                name=terminal.name,
                state=terminal.hub.state,
                official_code=terminal.official_code,
                modal=terminal.modal,
                latitude=terminal.latitude,
                longitude=terminal.longitude,
                confidence_score=100.0,
            )
        return None

    def resolve_hub_code(self, code: str) -> Optional[ResolvedEntity]:
        """Resolve o código curto de um hub ('SAO' ou 'HUB:SAO'), sem busca aproximada."""
        clean = (code or "").strip().upper()
        if clean.startswith(HUB_CODE_PREFIX):
            clean = clean[len(HUB_CODE_PREFIX):]
        if not clean:
            return None
        for hub in self.db.query(Hub).all():
            if hub_metro_code(hub.name) == clean:
                return ResolvedEntity(
                    entity_type="HUB",
                    id=hub.id,
                    name=hub.name,
                    state=hub.state,
                    latitude=hub.latitude,
                    longitude=hub.longitude,
                    confidence_score=100.0,
                )
        return None

    def resolve_hub(
        self, query: str, state_hint: Optional[str] = None, min_score: float = 70.0
    ) -> Optional[ResolvedEntity]:
        """Fuzzy match query against Hub names in the database."""
        clean_query = normalize_text(query)
        if not clean_query:
            return None

        hubs = self.db.query(Hub).all()
        if not hubs:
            return None

        # Build candidate map
        hub_candidates: Dict[str, Hub] = {}
        for hub in hubs:
            norm_name = normalize_text(hub.name)
            hub_candidates[norm_name] = hub
            # Also register name + state (e.g. "sao paulo sp")
            hub_candidates[f"{norm_name} {hub.state.lower()}"] = hub

        match = process.extractOne(
            clean_query,
            list(hub_candidates.keys()),
            scorer=fuzz.WRatio,
            score_cutoff=min_score,
        )

        if match:
            matched_key, score, _ = match
            hub = hub_candidates[matched_key]
            return ResolvedEntity(
                entity_type="HUB",
                id=hub.id,
                name=hub.name,
                state=hub.state,
                latitude=hub.latitude,
                longitude=hub.longitude,
                confidence_score=float(score),
            )
        return None

    def resolve_terminal(
        self,
        query: str,
        modal_filter: Optional[ModalType] = None,
        min_score: float = 70.0,
    ) -> Optional[ResolvedEntity]:
        """Fuzzy match query against Terminal names in the database."""
        # Check direct IATA / official code first
        direct_code = self.resolve_official_code(query)
        if direct_code:
            return direct_code

        clean_query = normalize_text(query)
        if not clean_query:
            return None

        terminal_query = self.db.query(Terminal)
        if modal_filter:
            terminal_query = terminal_query.filter(Terminal.modal == modal_filter)

        terminals = terminal_query.all()
        if not terminals:
            return None

        terminal_candidates: Dict[str, Terminal] = {}
        for term in terminals:
            norm_name = normalize_text(term.name)
            terminal_candidates[norm_name] = term
            if term.official_code:
                terminal_candidates[f"{norm_name} {term.official_code.lower()}"] = term

        match = process.extractOne(
            clean_query,
            list(terminal_candidates.keys()),
            scorer=fuzz.WRatio,
            score_cutoff=min_score,
        )

        if match:
            matched_key, score, _ = match
            term = terminal_candidates[matched_key]
            return ResolvedEntity(
                entity_type="TERMINAL",
                id=term.id,
                name=term.name,
                state=term.hub.state,
                official_code=term.official_code,
                modal=term.modal,
                latitude=term.latitude,
                longitude=term.longitude,
                confidence_score=float(score),
            )
        return None

    def resolve_any(self, query: str, min_score: float = 70.0) -> Optional[ResolvedEntity]:
        """Resolve a busca na ordem de precisão: hub explícito, código, nome do hub, terminal.

        - 'HUB:CWB'  -> a cidade inteira (aeroportos e rodoviárias);
        - 'CWB'      -> o terminal com esse código (aqui, o aeroporto);
        - 'SAO'      -> código de hub sem terminal homônimo;
        - 'SSA'      -> nada, se não estiver cadastrado (código não entra na busca aproximada);
        - 'Curitiba' -> busca aproximada por nome.
        """
        clean = (query or "").strip()
        if clean.upper().startswith(HUB_CODE_PREFIX):
            return self.resolve_hub_code(clean)

        # 1. Code match (e.g. 'GRU')
        direct = self.resolve_official_code(clean)
        if direct:
            return direct

        metro = self.resolve_hub_code(clean)
        if metro:
            return metro

        if _CODE_SHAPED.fullmatch(clean):
            return None

        query = clean
        # 2. Hub match (e.g. 'São Paulo', 'Curitiba')
        hub = self.resolve_hub(query, min_score=min_score)
        if hub and hub.confidence_score >= 85.0:
            return hub

        # 3. Terminal match (e.g. 'Terminal Rodoviário Tietê')
        terminal = self.resolve_terminal(query, min_score=min_score)
        if terminal and (not hub or terminal.confidence_score >= hub.confidence_score):
            return terminal

        return hub
