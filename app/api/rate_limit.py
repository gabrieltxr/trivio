"""Limite de uso por cliente nos caminhos que gastam request paga do Ignav.

Janela deslizante em memória, por processo: com vários workers ou instâncias, cada um conta
o seu. Serve para impedir que um único cliente esgote o saldo do Ignav; o teto global continua
sendo o orçamento do `AirQuoteService`.
"""

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Callable, Deque, Dict

from app.config import settings

WINDOW_SECONDS = 3600.0


class RateLimiter:
    """Permite até `limit()` eventos por chave a cada `WINDOW_SECONDS`. Limite 0 desliga."""

    def __init__(self, limit: Callable[[], int], clock: Callable[[], float] = time.monotonic):
        self._limit = limit
        self._clock = clock
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Registra uma tentativa e informa se ela cabe no limite."""
        limit = self._limit()
        if limit <= 0:
            return True
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


search_limiter = RateLimiter(lambda: settings.SEARCH_RATE_LIMIT_PER_HOUR)
booking_link_limiter = RateLimiter(lambda: settings.BOOKING_LINK_RATE_LIMIT_PER_HOUR)


def client_key(client) -> str:
    """IP do cliente (Starlette `request.client` / `websocket.client`)."""
    return getattr(client, "host", None) or "unknown"
