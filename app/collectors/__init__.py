"""Trivio Ingestion & Collectors Package.

Módulos de coletores rodoviários e aéreos, contratos abstratos e DTOs padronizados.
"""

from app.collectors.base import (
    BaseCollector,
    CollectorBlockedError,
    CollectorError,
    CollectorNetworkError,
    CollectorParsingError,
    CollectorRateLimitError,
    CollectorTimeoutError,
    StandardizedQuote,
    collector_retry,
    generate_dedup_signature,
    get_default_async_client,
    is_transient_error,
)

__all__ = [
    "BaseCollector",
    "CollectorBlockedError",
    "CollectorError",
    "CollectorNetworkError",
    "CollectorParsingError",
    "CollectorRateLimitError",
    "CollectorTimeoutError",
    "StandardizedQuote",
    "collector_retry",
    "generate_dedup_signature",
    "get_default_async_client",
    "is_transient_error",
]
