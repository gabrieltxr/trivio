"""Trivio Entity Resolution Module.

Uses RapidFuzz for fuzzy matching and normalization of user search inputs
and third-party provider terminal/city names to Trivio Hubs and Terminals.
"""

import unicodedata
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any
from rapidfuzz import fuzz, process
from sqlalchemy.orm import Session
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


def normalize_text(text: str) -> str:
    """Normalize string by lowercasing, stripping accents, and removing excess whitespace."""
    if not text:
        return ""
    # Strip accents
    normalized = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in normalized if not unicodedata.combining(c))
    # Remove special punctuation and lowercase
    cleaned = re.sub(r"[^\w\s]", " ", stripped).lower()
    return re.sub(r"\s+", " ", cleaned).strip()


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
        """Resolve query attempting Official Code -> Terminal -> Hub in order of precision."""
        # 1. Code match (e.g. 'GRU')
        direct = self.resolve_official_code(query)
        if direct:
            return direct

        # 2. Hub match (e.g. 'São Paulo', 'Curitiba')
        hub = self.resolve_hub(query, min_score=min_score)
        if hub and hub.confidence_score >= 85.0:
            return hub

        # 3. Terminal match (e.g. 'Terminal Rodoviário Tietê')
        terminal = self.resolve_terminal(query, min_score=min_score)
        if terminal and (not hub or terminal.confidence_score >= hub.confidence_score):
            return terminal

        return hub
