"""Trivio Smart Cache & Deduplication Package.

Módulo de políticas inteligentes de cache por proximidade da viagem e
deduplicação de ofertas repetidas em múltiplos canais de venda (code-share).
"""

from app.cache.ttl_policy import (
    TTLTier,
    TTL_IMMINENT_SECONDS,
    TTL_SHORT_SECONDS,
    TTL_MEDIUM_SECONDS,
    TTL_LONG_SECONDS,
    calculate_ttl,
    calculate_expiration_datetime,
    get_ttl_tier,
    is_quote_expired,
)
from app.cache.deduplicator import (
    QuoteDeduplicator,
    SellerOffer,
    deduplicate_quotes,
)

__all__ = [
    "TTLTier",
    "TTL_IMMINENT_SECONDS",
    "TTL_SHORT_SECONDS",
    "TTL_MEDIUM_SECONDS",
    "TTL_LONG_SECONDS",
    "calculate_ttl",
    "calculate_expiration_datetime",
    "get_ttl_tier",
    "is_quote_expired",
    "QuoteDeduplicator",
    "SellerOffer",
    "deduplicate_quotes",
]
