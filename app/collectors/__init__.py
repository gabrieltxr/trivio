"""Trivio Ingestion & Collectors Package.

Módulos de coletores rodoviários e aéreos, contratos abstratos e DTOs padronizados.
"""

from app.collectors.base import (
    BaseCollector,
    CollectorBlockedError,
    CollectorError,
    CollectorNetworkError,
    CollectorNotAuthorizedError,
    CollectorParsingError,
    CollectorRateLimitError,
    CollectorTimeoutError,
    StandardizedQuote,
    collector_retry,
    generate_dedup_signature,
    get_default_async_client,
    get_default_headers,
    is_transient_error,
)
from app.collectors.compliance import (
    ComplianceError,
    CompliantHTTPClient,
    NotAuthorizedError,
    PolitenessLimiter,
    RobotsDisallowedError,
    RobotsPolicy,
    build_user_agent,
    compliant_headers,
    get_robots_policy,
)

__all__ = [
    "BaseCollector",
    "CollectorBlockedError",
    "CollectorError",
    "CollectorNetworkError",
    "CollectorNotAuthorizedError",
    "CollectorParsingError",
    "CollectorRateLimitError",
    "CollectorTimeoutError",
    "StandardizedQuote",
    "collector_retry",
    "generate_dedup_signature",
    "get_default_async_client",
    "get_default_headers",
    "is_transient_error",
    # Conformidade de coleta
    "ComplianceError",
    "CompliantHTTPClient",
    "NotAuthorizedError",
    "PolitenessLimiter",
    "RobotsDisallowedError",
    "RobotsPolicy",
    "build_user_agent",
    "compliant_headers",
    "get_robots_policy",
]
