"""Coletor aéreo ao vivo via Ignav Flight Prices API (https://ignav.com/docs).

API contratada (chave em `X-Api-Key`), não raspagem: usa o cliente identificado
de APIs (`get_default_async_client`), sem verificação de robots.txt.

O que a API entrega, conferido numa resposta real (CGH->SDU, 07/11/2026):
  - preços em BRL com `market='BR'`, das três grandes (LATAM, GOL, Azul);
  - `price.status` 'verified' ou 'unverified' e `observed_at` (UTC) da consulta;
  - o preço é o TOTAL do bilhete, com taxas. Não há separação de tarifa e taxa;
  - não traz franquia de bagagem nas buscas sem `checked_bags`;
  - não traz link de compra: ele sai do endpoint de booking links, sob demanda,
    a partir do `ignav_id` (token opaco da conta, válido para a busca que o gerou).

Cobrança: por request bem-sucedida. Quem controla cache e orçamento é o
`app.services.air_quotes.AirQuoteService`; este módulo só fala com a API.
"""

from datetime import datetime
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from app.collectors.air.boarding_fees import boarding_fee_brl
from app.collectors.air.carriers import canonical_carrier_name
from app.collectors.base import (
    BaseCollector,
    CollectorError,
    CollectorNetworkError,
    CollectorNotAuthorizedError,
    CollectorParsingError,
    CollectorRateLimitError,
    CollectorTimeoutError,
    StandardizedQuote,
    get_default_async_client,
)
from app.config import settings

logger = logging.getLogger("trivio.collectors.ignav")

PROVIDER_NAME = "ignav"


class IgnavBillingError(CollectorError):
    """Sem crédito ou com limite de gasto atingido (HTTP 402 / 429). Não adianta retentar.

    `reason` diz o motivo para a interface: 'billing_required' (402, a conta ainda não tem
    forma de pagamento ou crédito) ou 'spend_limit_reached' (429, teto mensal da conta).
    """

    def __init__(self, message: str, reason: str, code: Optional[str] = None):
        super().__init__(message)
        self.reason = reason
        self.code = code


class IgnavRequestError(CollectorError):
    """Requisição recusada pela API (HTTP 400/404/424): parâmetros ou rota sem resultado."""

    def __init__(self, message: str, code: Optional[str] = None, status_code: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _error_details(response: httpx.Response) -> Dict[str, Any]:
    try:
        return (response.json() or {}).get("error") or {}
    except Exception:
        return {}


def raise_for_ignav_status(response: httpx.Response) -> None:
    """Converte os erros documentados do Ignav nas exceções dos coletores.

    Só 503 (servidor ocupado) e erros de rede são transitórios e retentáveis.
    """
    status = response.status_code
    if status < 400:
        return
    details = _error_details(response)
    code = details.get("code")
    message = details.get("message") or response.text[:200]
    summary = f"Ignav HTTP {status} ({code or 'sem código'}): {message}"

    if status in (401, 403):
        raise CollectorNotAuthorizedError(f"{summary}. Confira IGNAV_API_KEY no .env.")
    if status == 402:
        raise IgnavBillingError(summary, reason="billing_required", code=code)
    if status == 429:
        raise IgnavBillingError(summary, reason="spend_limit_reached", code=code)
    if status == 503:
        raise CollectorRateLimitError(summary)
    raise IgnavRequestError(summary, code=code, status_code=status)


def _parse_local(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def booking_link_url(
    ignav_id: str,
    origin: str,
    destination: str,
    departure_date: str,
    carrier_code: Optional[str],
    flight_number: Optional[str],
) -> str:
    """Link da API do Trivio que resolve o link de compra só quando o usuário clica."""
    query = urlencode({
        "ignav_id": ignav_id,
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "carrier_code": carrier_code or "",
        "flight_number": flight_number or "",
    })
    return f"{settings.PUBLIC_API_BASE_URL.rstrip('/')}/api/v1/air/booking-link?{query}"


def parse_ignav_one_way(
    payload: Dict[str, Any],
    origin_iata: str,
    dest_iata: str,
    departure_date: str,
    default_baggage_estimate_brl: float = 110.0,
    checkin_buffer_minutes: int = 90,
    expected_currency: str = "BRL",
) -> List[StandardizedQuote]:
    """Converte a resposta de `/api/fares/one-way` em StandardizedQuote.

    Descarta itinerários sem segmentos, com valor zero (placeholder que a própria
    API pede para não exibir) ou em outra moeda (não convertemos câmbio).
    """
    itineraries = payload.get("itineraries")
    if not isinstance(itineraries, list):
        raise CollectorParsingError("Resposta do Ignav sem a lista 'itineraries'.")

    observed_at = payload.get("observed_at")
    quotes: List[StandardizedQuote] = []
    skipped = {"currency": 0, "zero": 0, "invalid": 0}

    for itinerary in itineraries:
        try:
            price = itinerary.get("price") or {}
            amount = float(price.get("amount") or 0.0)
            currency = str(price.get("currency") or "").upper()
            if amount <= 0.0:
                skipped["zero"] += 1
                continue
            if currency != expected_currency:
                skipped["currency"] += 1
                continue

            outbound = itinerary.get("outbound") or {}
            segments = outbound.get("segments") or []
            if not segments:
                skipped["invalid"] += 1
                continue
            first, last = segments[0], segments[-1]

            departure = _parse_local(first.get("departure_time_local"))
            arrival = _parse_local(last.get("arrival_time_local"))
            if departure is None or arrival is None:
                skipped["invalid"] += 1
                continue

            duration = int(outbound.get("duration_minutes") or 0)
            if duration <= 0:
                duration = max(1, int((arrival - departure).total_seconds() // 60))

            carrier_code = (first.get("marketing_carrier_code") or "").strip().upper() or None
            carrier_name = canonical_carrier_name(carrier_code, outbound.get("carrier"))

            layovers = []
            for current, nxt in zip(segments, segments[1:]):
                arr = _parse_local(current.get("arrival_time_local"))
                dep = _parse_local(nxt.get("departure_time_local"))
                layovers.append({
                    "airport": current.get("arrival_airport"),
                    "arrival_time": current.get("arrival_time_local"),
                    "departure_time": nxt.get("departure_time_local"),
                    "duration_minutes": int((dep - arr).total_seconds() // 60) if arr and dep else None,
                })

            # O preço do Ignav é o total. A régua da ANAC mede a tarifa sem taxa de
            # embarque, então separamos a taxa estimada do aeroporto de origem.
            taxes = min(boarding_fee_brl(origin_iata), amount)
            base = round(amount - taxes, 2)

            ignav_id = itinerary.get("ignav_id")
            flight_number = f"{carrier_code or ''}{first.get('flight_number') or ''}" or None

            metadata = {
                "is_direct": len(segments) == 1,
                "stops_count": len(segments) - 1,
                "layover_airports": [s.get("arrival_airport") for s in segments[:-1]],
                "layover_details": layovers,
                "primary_flight_number": flight_number,
                "all_flight_numbers": [
                    f"{s.get('marketing_carrier_code') or ''}{s.get('flight_number') or ''}" for s in segments
                ],
                "operating_carrier_name": first.get("operating_carrier_name"),
                "has_mixed_carriers": bool(itinerary.get("has_mixed_carriers")),
                "aircraft": first.get("aircraft"),
                "baggage_included": False,          # a busca não informa franquia
                "origin_iata": origin_iata,
                "destination_iata": dest_iata,
                "price_total_brl": round(amount, 2),
                "price_status": price.get("status"),
                "price_observed_at": observed_at,
                "taxes_are_estimated": True,
                "ignav_id": ignav_id,
            }
            if ignav_id:
                metadata["booking_url"] = booking_link_url(
                    ignav_id, origin_iata, dest_iata, departure_date, carrier_code, first.get("flight_number"),
                )

            quotes.append(StandardizedQuote(
                origin_external_id=origin_iata,
                destination_external_id=dest_iata,
                modal="AIR",
                provider_source=PROVIDER_NAME,
                carrier_name=carrier_name,
                service_class=str(itinerary.get("cabin_class") or "economy").upper(),
                departure_time=departure,
                arrival_time=arrival,
                duration_minutes=duration,
                door_to_door_duration_minutes=duration + checkin_buffer_minutes,
                base_price=base,
                taxes_price=round(taxes, 2),
                baggage_fee_estimate=round(default_baggage_estimate_brl, 2),
                route_metadata=metadata,
            ))
        except Exception as exc:  # um itinerário estranho não derruba o lote
            skipped["invalid"] += 1
            logger.debug(f"[ignav] Itinerário ignorado ({type(exc).__name__}: {exc})")

    if any(skipped.values()):
        logger.info(f"[ignav] {origin_iata}->{dest_iata}: itinerários descartados {skipped}")
    return quotes


class IgnavClient:
    """Chamadas HTTP à API do Ignav (busca só ida e link de compra)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        market: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ):
        self.api_key = api_key if api_key is not None else settings.IGNAV_API_KEY
        self.base_url = (base_url or settings.IGNAV_BASE_URL).rstrip("/")
        self.market = market or settings.IGNAV_MARKET
        self.timeout_seconds = timeout_seconds or settings.IGNAV_TIMEOUT_SECONDS

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_configured:
            raise CollectorError("IGNAV_API_KEY não configurada.")
        headers = {"X-Api-Key": self.api_key, "Content-Type": "application/json"}
        try:
            async with get_default_async_client(timeout=self.timeout_seconds, headers=headers) as client:
                response = await client.post(f"{self.base_url}{path}", json=body)
        except httpx.TimeoutException as exc:
            raise CollectorTimeoutError(f"Timeout no Ignav ({path}): {exc}") from exc
        except httpx.NetworkError as exc:
            raise CollectorNetworkError(f"Falha de rede no Ignav ({path}): {exc}") from exc

        raise_for_ignav_status(response)
        try:
            return response.json()
        except ValueError as exc:
            raise CollectorParsingError(f"Resposta não-JSON do Ignav ({path}).") from exc

    async def search_one_way(self, origin: str, destination: str, departure_date: str) -> Dict[str, Any]:
        return await self._post("/api/fares/one-way", {
            "origin": origin.upper(),
            "destination": destination.upper(),
            "departure_date": departure_date,
            "adults": 1,
            "cabin_class": "economy",
            "market": self.market,
        })

    async def booking_links(
        self,
        ignav_id: str,
        origin: str,
        destination: str,
        departure_date: str,
        carrier_code: Optional[str] = None,
        flight_number: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Links de compra. A API aceita UM modo de busca por vez (ignav_id OU dados do voo).

        Tenta pelo `ignav_id`; se ele não existir mais (expira com a busca que o gerou),
        tenta pelos dados do voo, quando informados.
        """
        try:
            return await self._post("/api/fares/booking-links", {"ignav_id": ignav_id})
        except IgnavRequestError as exc:
            if exc.code != "ignav_id_not_found" or not (carrier_code and flight_number):
                raise
        logger.info(f"[ignav] ignav_id expirado; buscando link pelos dados do voo {carrier_code}{flight_number}.")
        return await self._post("/api/fares/booking-links", {
            "origin": origin.upper(),
            "destination": destination.upper(),
            "departure_date": departure_date,
            "outbound_carrier_code": carrier_code,
            "outbound_flight_number": flight_number,
            "adults": 1,
            "market": self.market,
        })


class IgnavCollector(BaseCollector):
    """Coletor de voos só ida via Ignav. Uma chamada de `fetch_quotes` = uma request paga."""

    provider_name = PROVIDER_NAME
    modal = "AIR"

    def __init__(self, client: Optional[IgnavClient] = None):
        self.client = client or IgnavClient()

    @property
    def is_configured(self) -> bool:
        return self.client.is_configured

    async def fetch_quotes(
        self, origin_ext_id: str, dest_ext_id: str, travel_date: str
    ) -> List[StandardizedQuote]:
        # Sem @collector_retry de propósito: cada tentativa pode ser cobrada.
        payload = await self.client.search_one_way(origin_ext_id, dest_ext_id, travel_date)
        return parse_ignav_one_way(
            payload,
            origin_iata=origin_ext_id.upper(),
            dest_iata=dest_ext_id.upper(),
            departure_date=travel_date,
            default_baggage_estimate_brl=settings.DEFAULT_FLIGHT_BAGGAGE_FEE_ESTIMATE_BRL,
            checkin_buffer_minutes=settings.AIRPORT_DOMESTIC_CHECKIN_BUFFER_MINUTES,
        )
