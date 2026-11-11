"""Preços aéreos ao vivo com cache de consultas, saldo de requests e coalescência.

Toda request ao provedor aéreo é paga. Este serviço fica entre a busca e o
coletor e decide, nesta ordem:

  1. Cache   — há consulta igual ainda válida em `provider_queries`? Responde dela.
  2. Saldo   — o saldo lido no painel do Ignav, menos o que foi cobrado desde a leitura,
               chegou à margem de segurança? Não chama.
  3. Coalescência — a mesma consulta já está em andamento? Espera a mesma chamada.
  4. Coletor — faz a request, registra o resultado e guarda as cotações no cache.

Falhas viram `AirProviderUnavailableError` (sem chave, sem orçamento, desligado)
ou `CollectorError` (erro do provedor), para a busca avisar o frontend em vez de
mostrar zero voos como se fosse sucesso.
"""

import asyncio
from dataclasses import fields
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cache.ttl_policy import calculate_ttl
from app.collectors.air.ignav import IgnavCollector, IgnavRequestError, PROVIDER_NAME
from app.collectors.base import CollectorError, StandardizedQuote
from app.config import settings
from app.database import SessionLocal
from app.models import ProviderQuery

logger = logging.getLogger("trivio.services.air_quotes")

# Erros do provedor que significam "não há voos para isso", e não falha: viram
# consulta vazia em cache (não vale repetir). Nenhum deles é cobrado pelo Ignav.
EMPTY_RESULT_CODES = {"route_not_found", "unsupported_search"}

BOOKING_LINK_TTL_SECONDS = 1800   # o Ignav reaproveita resultados por até 30 min

# Com menos voos que isso, um preço repetido pode ser coincidência; a partir daqui, é suspeito.
UNIFORM_PRICE_MIN_FLIGHTS = 3


def flag_uniform_prices(quotes: List[StandardizedQuote]) -> List[StandardizedQuote]:
    """Marca a lista quando o provedor devolve o mesmo preço para voos distintos.

    Caso visto em 17/11 (CGH->SDU): 55 voos com R$ 2.737 cada, marcados como
    'verified' pelo Ignav. Não corrigimos o preço; só sinalizamos para o usuário.
    """
    prices = {q.route_metadata.get("price_total_brl") for q in quotes}
    suspect = len(quotes) >= UNIFORM_PRICE_MIN_FLIGHTS and len(prices) == 1 and None not in prices
    for quote in quotes:
        quote.route_metadata["price_suspect"] = suspect
        if suspect:
            quote.route_metadata["price_suspect_flights"] = len(quotes)
    if suspect:
        logger.warning(f"[ignav] {len(quotes)} voos com o mesmo preço ({next(iter(prices))}): suspeito")
    return quotes


class AirProviderUnavailableError(CollectorError):
    """O aéreo ao vivo não foi consultado: sem chave, desligado ou orçamento esgotado."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason   # 'not_configured', 'disabled', 'budget_exhausted'


def _utcnow() -> datetime:
    """UTC sem fuso: é como o SQLite devolve as datas, e comparamos sempre assim."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_as_of(value: Any) -> datetime:
    """Momento da leitura do saldo no painel (UTC, sem fuso, como o SQLite guarda)."""
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def build_query_key(provider: str, kind: str, params: Dict[str, Any]) -> str:
    raw = json.dumps({"provider": provider, "kind": kind, **params}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_QUOTE_FIELDS = {f.name for f in fields(StandardizedQuote)}


def quote_from_dict(data: Dict[str, Any]) -> StandardizedQuote:
    """Reconstrói uma StandardizedQuote a partir de `to_dict()`."""
    values = {key: value for key, value in data.items() if key in _QUOTE_FIELDS}
    for key in ("departure_time", "arrival_time"):
        if isinstance(values.get(key), str):
            values[key] = datetime.fromisoformat(values[key])
    values["route_metadata"] = json.loads(json.dumps(values.get("route_metadata") or {}))
    return StandardizedQuote(**values)


class AirQuoteService:
    """Busca de voos só ida com cache, orçamento e coalescência."""

    modal = "AIR"

    _inflight: Dict[str, "asyncio.Future[List[Dict[str, Any]]]"] = {}

    def __init__(
        self,
        collector: Optional[IgnavCollector] = None,
        session_factory: Callable[[], Session] = SessionLocal,
        requests_remaining: Optional[int] = None,
        remaining_as_of: Optional[Any] = None,
        safety_margin: Optional[int] = None,
        cache_max_seconds: Optional[int] = None,
        now: Callable[[], datetime] = _utcnow,
    ):
        self.collector = collector or IgnavCollector()
        self.provider_name = self.collector.provider_name
        self.session_factory = session_factory
        self.requests_remaining = (
            settings.AIR_REQUESTS_REMAINING if requests_remaining is None else requests_remaining
        )
        self.remaining_as_of = _parse_as_of(
            settings.AIR_REQUESTS_REMAINING_AS_OF if remaining_as_of is None else remaining_as_of
        )
        self.safety_margin = (
            settings.AIR_REQUESTS_SAFETY_MARGIN if safety_margin is None else safety_margin
        )
        self.cache_max_seconds = (
            settings.AIR_LIVE_CACHE_MAX_SECONDS if cache_max_seconds is None else cache_max_seconds
        )
        self._now = now

    # ------------------------------------------------------------------ saldo

    def requests_used_since_snapshot(self, session: Session) -> int:
        """Requests cobradas desde a leitura do saldo no painel do Ignav."""
        stmt = select(func.coalesce(func.sum(ProviderQuery.billed), 0)).where(
            ProviderQuery.provider == self.provider_name,
            ProviderQuery.requested_at >= self.remaining_as_of,
        )
        return int(session.execute(stmt).scalar() or 0)

    def requests_left(self, session: Session) -> int:
        return self.requests_remaining - self.requests_used_since_snapshot(session)

    def _ensure_available(self, session: Session) -> None:
        if not self.collector.is_configured:
            raise AirProviderUnavailableError(
                "not_configured",
                "Aéreo ao vivo desligado: IGNAV_API_KEY não está configurada no .env.",
            )
        if self.requests_remaining <= 0:
            raise AirProviderUnavailableError(
                "disabled", "Aéreo ao vivo desligado (AIR_REQUESTS_REMAINING=0)."
            )
        left = self.requests_left(session)
        if left <= self.safety_margin:
            raise AirProviderUnavailableError(
                "budget_exhausted",
                f"Saldo de requests do Ignav na margem de segurança ({left} restantes; "
                f"margem {self.safety_margin}). Confira o painel e atualize AIR_REQUESTS_REMAINING.",
            )

    def usage(self) -> Dict[str, Any]:
        """Saldo estimado de requests do Ignav, para a rota de status."""
        with self.session_factory() as session:
            used = self.requests_used_since_snapshot(session)
        left = self.requests_remaining - used
        return {
            "provider": self.provider_name,
            "configured": self.collector.is_configured,
            "requests_remaining_at_snapshot": self.requests_remaining,
            "snapshot_as_of": self.remaining_as_of.isoformat(),
            "requests_used_since_snapshot": used,
            "requests_left": max(0, left),
            "safety_margin": self.safety_margin,
        }

    # ------------------------------------------------------------------ cache

    def _ttl_seconds(self, travel_date: str) -> int:
        tiered = calculate_ttl(travel_date, reference_date=self._now().date())
        return min(tiered or self.cache_max_seconds, self.cache_max_seconds)

    def _cached(self, session: Session, kind: str, query_key: str) -> Optional[ProviderQuery]:
        stmt = (
            select(ProviderQuery)
            .where(
                ProviderQuery.provider == self.provider_name,
                ProviderQuery.kind == kind,
                ProviderQuery.query_key == query_key,
                ProviderQuery.status.in_(("ok", "empty")),
                ProviderQuery.expires_at > self._now(),
            )
            .order_by(ProviderQuery.requested_at.desc())
            .limit(1)
        )
        return session.execute(stmt).scalars().first()

    def _record(self, session: Session, **values: Any) -> None:
        session.add(ProviderQuery(provider=self.provider_name, requested_at=self._now(), **values))
        session.commit()

    # ------------------------------------------------------------------ busca

    async def get_quotes(self, origin: str, destination: str, travel_date: str) -> List[StandardizedQuote]:
        """Voos só ida de `origin` a `destination` (IATA) na data, ao vivo ou do cache."""
        origin, destination = origin.strip().upper(), destination.strip().upper()
        params = {"origin": origin, "destination": destination, "travel_date": travel_date,
                  "adults": 1, "cabin": "economy", "market": settings.IGNAV_MARKET}
        query_key = build_query_key(self.provider_name, "search", params)
        started = time.perf_counter()

        with self.session_factory() as session:
            cached = self._cached(session, "search", query_key)
            if cached is not None:
                data = list(cached.payload or [])
                age = round((self._now() - cached.requested_at).total_seconds())
                logger.info(f"[{self.provider_name}] cache {origin}->{destination} {travel_date}: {len(data)} voos ({age}s)")
                return self._materialize(data, tier="cache", started=started, cache_age_seconds=age)
            self._ensure_available(session)

        inflight = self._inflight.get(query_key)
        if inflight is not None:
            data = await asyncio.shield(inflight)
            return self._materialize(data, tier="coalesced", started=started)

        future: "asyncio.Future[List[Dict[str, Any]]]" = asyncio.get_running_loop().create_future()
        self._inflight[query_key] = future
        try:
            data = await self._fetch_and_record(origin, destination, travel_date, query_key)
            future.set_result(data)
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()   # marca como observada; quem espera recebe a mesma exceção
            raise
        finally:
            self._inflight.pop(query_key, None)
        return self._materialize(data, tier="live", started=started)

    async def _fetch_and_record(
        self, origin: str, destination: str, travel_date: str, query_key: str
    ) -> List[Dict[str, Any]]:
        started = time.perf_counter()
        base = dict(kind="search", query_key=query_key, origin=origin, destination=destination,
                    travel_date=travel_date)
        try:
            quotes = await self.collector.fetch_quotes(origin, destination, travel_date)
        except IgnavRequestError as exc:
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            with self.session_factory() as session:
                if exc.code in EMPTY_RESULT_CODES:
                    self._record(session, **base, status="empty", billed=0, quotes_count=0, elapsed_ms=elapsed,
                                 error=str(exc)[:500], payload=[],
                                 expires_at=self._now() + timedelta(seconds=self._ttl_seconds(travel_date)))
                    return []
                self._record(session, **base, status="error", billed=0, elapsed_ms=elapsed, error=str(exc)[:500])
            raise
        except Exception as exc:
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            with self.session_factory() as session:
                self._record(session, **base, status="error", billed=0, elapsed_ms=elapsed,
                             error=f"{type(exc).__name__}: {exc}"[:500])
            raise

        elapsed = round((time.perf_counter() - started) * 1000, 1)
        data = [quote.to_dict() for quote in quotes]
        with self.session_factory() as session:
            self._record(
                session, **base,
                status="ok" if data else "empty", billed=1, quotes_count=len(data), elapsed_ms=elapsed,
                payload=data, expires_at=self._now() + timedelta(seconds=self._ttl_seconds(travel_date)),
            )
        logger.info(f"[{self.provider_name}] ao vivo {origin}->{destination} {travel_date}: {len(data)} voos em {elapsed}ms")
        return data

    def _materialize(
        self, data: List[Dict[str, Any]], tier: str, started: float, cache_age_seconds: Optional[int] = None
    ) -> List[StandardizedQuote]:
        """Cópias novas das cotações (o termômetro e o pipeline alteram os metadados)."""
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        quotes = flag_uniform_prices([quote_from_dict(item) for item in data])
        for quote in quotes:
            stats = quote.route_metadata.setdefault("collector_stats", {})
            stats.update({"provider": self.provider_name, "tier": tier, "latency_ms": elapsed})
            if cache_age_seconds is not None:
                stats["cache_age_seconds"] = cache_age_seconds
        return quotes

    # ------------------------------------------------------------------ link de compra

    async def get_booking_url(self, ignav_id: str, origin: str, destination: str, departure_date: str,
                              carrier_code: Optional[str] = None, flight_number: Optional[str] = None) -> str:
        """URL de compra do voo escolhido. Custa uma request; fica em cache por 30 min."""
        params = {"ignav_id": ignav_id}
        query_key = build_query_key(self.provider_name, "booking_link", params)
        with self.session_factory() as session:
            cached = self._cached(session, "booking_link", query_key)
            if cached is not None and cached.payload:
                return cached.payload["url"]
            self._ensure_available(session)

        started = time.perf_counter()
        base = dict(kind="booking_link", query_key=query_key, origin=origin.upper(),
                    destination=destination.upper(), travel_date=departure_date)
        try:
            response = await self.collector.client.booking_links(
                ignav_id, origin, destination, departure_date, carrier_code, flight_number,
            )
        except Exception as exc:
            with self.session_factory() as session:
                self._record(session, **base, status="error", billed=0,
                             elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                             error=f"{type(exc).__name__}: {exc}"[:500])
            raise

        url = pick_booking_url(response)
        with self.session_factory() as session:
            self._record(
                session, **base, status="ok" if url else "empty", billed=1, quotes_count=1 if url else 0,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                payload={"url": url} if url else None,
                expires_at=self._now() + timedelta(seconds=BOOKING_LINK_TTL_SECONDS),
            )
        if not url:
            raise CollectorError("O provedor não devolveu link de compra para este voo.")
        return url


def pick_booking_url(response: Dict[str, Any]) -> Optional[str]:
    """Escolhe o melhor link: companhia aérea com o voo já selecionado, depois qualquer voo exato."""
    links = [
        link
        for option in (response.get("booking_options") or [])
        for link in (option.get("links") or [])
        if link.get("url")
    ]
    if not links:
        return None

    def rank(link: Dict[str, Any]) -> tuple:
        return (
            link.get("specificity") != "exact_flight",
            link.get("provider_type") != "airline",
        )

    url = sorted(links, key=rank)[0]["url"].strip()
    return url if url.startswith(("http://", "https://")) else f"https://{url}"


_default_service: Optional[AirQuoteService] = None


def get_air_quote_service() -> AirQuoteService:
    """Instância compartilhada (a coalescência e o cliente valem para o processo todo)."""
    global _default_service
    if _default_service is None:
        _default_service = AirQuoteService()
    return _default_service
