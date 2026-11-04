"""Coletor Aéreo para Amadeus Self-Service Flight Offers Search API (Trivio Core).

Responsável por:
1. Autenticação OAuth 2.0 (Client Credentials) com cache de token em memória.
2. Consulta de voos diretos e com conexão via Flight Offers Search API (v2).
3. Normalização de tarifas aéreas, separando tarifa base de taxas ANAC (embarque).
4. Aplicação de heurísticas de bagagem:
   - Se a tarifa inclui bagagem despachada (23kg), custo adicional = R$ 0,00.
   - Se a tarifa é básica/light (padrão Brasil sem bagagem), projeta custo de R$ 110,00.
5. Inclusão da antecedência de embarque aeroportuário (90 min de check-in / raio-X)
   ao cálculo de tempo porta a porta (door_to_door_duration_minutes).
"""

from datetime import datetime, timezone
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.collectors.base import (
    BaseCollector,
    CollectorError,
    CollectorNetworkError,
    CollectorParsingError,
    CollectorRateLimitError,
    CollectorTimeoutError,
    StandardizedQuote,
    collector_retry,
    get_default_async_client,
)

logger = logging.getLogger("trivio.collectors.amadeus")

# ==============================================================================
# 1. Dicionários e De-Para de Companhias Aéreas Brasileiras
# ==============================================================================

BRAZILIAN_AIRLINES = {
    "LA": "LATAM Airlines",
    "JJ": "LATAM Airlines",
    "G3": "Gol Linhas Aéreas",
    "AD": "Azul Linhas Aéreas",
    "2Z": "Voepass Linhas Aéreas",
    "TP": "TAP Air Portugal",
    "AA": "American Airlines",
    "DL": "Delta Air Lines",
    "UA": "United Airlines",
    "AF": "Air France",
    "KL": "KLM",
    "CM": "Copa Airlines",
    "AV": "Avianca",
}

AIRCRAFT_MODELS = {
    "319": "Airbus A319",
    "320": "Airbus A320",
    "321": "Airbus A321",
    "32A": "Airbus A320 Sharklets",
    "32N": "Airbus A320neo",
    "32Q": "Airbus A321neo",
    "737": "Boeing 737",
    "738": "Boeing 737-800",
    "73G": "Boeing 737-700",
    "7M8": "Boeing 737 MAX 8",
    "E95": "Embraer E195",
    "295": "Embraer E195-E2",
    "E90": "Embraer E190",
    "AT7": "ATR 72",
}


def parse_iso_duration(duration_str: str) -> int:
    """Converte duração no formato ISO 8601 (ex: 'PT1H15M', 'PT50M', 'PT2H') em minutos."""
    if not duration_str:
        return 0

    hours = 0
    minutes = 0

    match_h = re.search(r"(\d+)H", duration_str)
    if match_h:
        hours = int(match_h.group(1))

    match_m = re.search(r"(\d+)M", duration_str)
    if match_m:
        minutes = int(match_m.group(1))

    return hours * 60 + minutes


# ==============================================================================
# 2. Parser de Respostas Amadeus Flight Offers Search v2
# ==============================================================================

def parse_amadeus_flight_offers(
    payload: Dict[str, Any],
    origin_ext_id: str,
    dest_ext_id: str,
    default_baggage_estimate_brl: float = 110.0,
    checkin_buffer_minutes: int = 90,
) -> List[StandardizedQuote]:
    """Extrai cotações aéreas padronizadas a partir do payload JSON da API Amadeus."""
    quotes: List[StandardizedQuote] = []
    offers = payload.get("data") or []

    if not isinstance(offers, list):
        logger.warning(f"Payload Amadeus não contém lista 'data': {type(offers)}")
        return []

    dictionaries = payload.get("dictionaries", {})
    dict_carriers = dictionaries.get("carriers", {})
    dict_aircraft = dictionaries.get("aircraft", {})

    for offer in offers:
        try:
            itineraries = offer.get("itineraries", [])
            if not itineraries:
                continue

            # Considera a primeira perna (ida ou trecho solicitado)
            itinerary = itineraries[0]
            segments = itinerary.get("segments", [])
            if not segments:
                continue

            first_segment = segments[0]
            last_segment = segments[-1]

            # 1. Operadora e Número de Voo
            carrier_code = first_segment.get("carrierCode", "").strip().upper()
            carrier_name = (
                dict_carriers.get(carrier_code)
                or BRAZILIAN_AIRLINES.get(carrier_code)
                or f"Companhia {carrier_code}"
            )
            flight_number = f"{carrier_code}{first_segment.get('number', '')}"

            # 2. Horários e Duração de Voo
            dep_at_str = first_segment.get("departure", {}).get("at", "")
            arr_at_str = last_segment.get("arrival", {}).get("at", "")

            dep_dt = datetime.fromisoformat(dep_at_str)
            arr_dt = datetime.fromisoformat(arr_at_str)

            # Duração total do itinerário (incluindo tempo de conexões)
            iso_dur = itinerary.get("duration", "")
            duration_minutes = parse_iso_duration(iso_dur)
            if duration_minutes <= 0:
                duration_minutes = int((arr_dt - dep_dt).total_seconds() / 60)
            if duration_minutes <= 0:
                duration_minutes = 60

            # 3. Conexões, Aeronaves e Detalhes de Layover (Tempo de Solo)
            stops_count = len(segments) - 1
            is_direct = (stops_count == 0)
            layover_airports = [s.get("arrival", {}).get("iataCode") for s in segments[:-1]]

            layover_details = []
            for i in range(len(segments) - 1):
                arr_seg = segments[i]
                next_seg = segments[i + 1]
                conn_airport = arr_seg.get("arrival", {}).get("iataCode", "").strip().upper()
                try:
                    seg_arr_dt = datetime.fromisoformat(arr_seg.get("arrival", {}).get("at", ""))
                    next_dep_dt = datetime.fromisoformat(next_seg.get("departure", {}).get("at", ""))
                    layover_min = int((next_dep_dt - seg_arr_dt).total_seconds() / 60)
                except Exception:
                    layover_min = 60

                layover_details.append({
                    "airport": conn_airport,
                    "arrival_time": arr_seg.get("arrival", {}).get("at"),
                    "departure_time": next_seg.get("departure", {}).get("at"),
                    "duration_minutes": layover_min,
                })

            aircraft_code = first_segment.get("aircraft", {}).get("code", "")
            aircraft_name = (
                dict_aircraft.get(aircraft_code)
                or AIRCRAFT_MODELS.get(aircraft_code)
                or aircraft_code
            )

            # 4. Preço e Decomposição de Taxas ANAC
            price_data = offer.get("price", {})
            total_price = float(price_data.get("total", 0.0))
            base_price = float(price_data.get("base", total_price))
            taxes_price = round(total_price - base_price, 2)
            if taxes_price < 0.0:
                taxes_price = 0.0

            # 5. Franquia de Bagagem Despachada (23kg)
            baggage_included = False
            traveler_pricings = offer.get("travelerPricings", [])
            service_class = "ECONOMY"

            if traveler_pricings:
                fare_details = traveler_pricings[0].get("fareDetailsBySegment", [])
                if fare_details:
                    service_class = fare_details[0].get("cabin", "ECONOMY").upper()
                    included_bags = fare_details[0].get("includedCheckedBags", {})
                    # Quantidade de malas inclusas ou peso
                    bag_qty = included_bags.get("quantity", 0)
                    bag_weight = included_bags.get("weight", 0)
                    if bag_qty > 0 or bag_weight >= 20:
                        baggage_included = True

            # Estimativa de custo real de bagagem no aéreo brasileiro:
            # Se não estiver inclusa na tarifa básica, projeta o custo médio de despacho
            baggage_fee_estimate = 0.0 if baggage_included else default_baggage_estimate_brl

            # 6. Metadados Enriquecidos da Rota
            all_flight_numbers = [f"{s.get('carrierCode')}{s.get('number')}" for s in segments]
            route_metadata = {
                "offer_id": str(offer.get("id", "")),
                "is_direct": is_direct,
                "stops_count": stops_count,
                "layover_airports": layover_airports,
                "layover_details": layover_details,
                "primary_flight_number": flight_number,
                "all_flight_numbers": all_flight_numbers,
                "aircraft": aircraft_name,
                "baggage_included": baggage_included,
                "origin_iata": origin_ext_id,
                "destination_iata": dest_ext_id,
                "booking_url": (
                    f"https://www.google.com/travel/flights?q=flights%20from%20{origin_ext_id}%20to%20{dest_ext_id}"
                ),
            }

            door_to_door = duration_minutes + checkin_buffer_minutes

            quote = StandardizedQuote(
                origin_external_id=origin_ext_id,
                destination_external_id=dest_ext_id,
                modal="AIR",
                provider_source="amadeus",
                carrier_name=carrier_name,
                service_class=service_class,
                departure_time=dep_dt,
                arrival_time=arr_dt,
                duration_minutes=duration_minutes,
                door_to_door_duration_minutes=door_to_door,
                base_price=round(base_price, 2),
                taxes_price=round(taxes_price, 2),
                baggage_fee_estimate=round(baggage_fee_estimate, 2),
                transfer_cost_estimate=0.0,
                route_metadata=route_metadata,
            )
            quotes.append(quote)

        except Exception as err:
            logger.debug(f"Falha ao processar oferta Amadeus: {err}")
            continue

    return quotes


# ==============================================================================
# 3. Coletor Principal Amadeus
# ==============================================================================

class AmadeusCollector(BaseCollector):
    """Coletor assíncrono para voos via Amadeus Self-Service API."""

    provider_name = "amadeus"
    modal = "AIR"
    supports_headless_fallback = False

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        env: Optional[str] = None,
        timeout_seconds: float = 15.0,
    ):
        self.client_id = client_id or settings.AMADEUS_CLIENT_ID
        self.client_secret = client_secret or settings.AMADEUS_CLIENT_SECRET
        self.env = env or settings.AMADEUS_ENV or "test"
        self.timeout_seconds = timeout_seconds

        if self.env.lower() == "production":
            self.base_url = "https://api.amadeus.com"
        else:
            self.base_url = "https://test.api.amadeus.com"

        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[float] = None

    @collector_retry(max_attempts=3, min_wait=1.0, max_wait=6.0)
    async def get_access_token(self) -> str:
        """Obtém token de autenticação OAuth 2.0 via Client Credentials."""
        now = time.time()
        # Se temos token com mais de 60s de validade restante, reutiliza
        if self._access_token and self._token_expires_at and (self._token_expires_at - now > 60):
            return self._access_token

        if not self.client_id or not self.client_secret:
            raise CollectorError(
                "Credenciais Amadeus ausentes (AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET). "
                "Configure no .env para buscas reais em tempo real."
            )

        token_url = f"{self.base_url}/v1/security/oauth2/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        try:
            async with get_default_async_client(timeout=self.timeout_seconds) as client:
                resp = await client.post(
                    token_url,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

                if resp.status_code == 429:
                    raise CollectorRateLimitError("Rate limit no endpoint de token Amadeus")

                resp.raise_for_status()
                data = resp.json()

                self._access_token = data.get("access_token")
                expires_in = data.get("expires_in", 1799)
                self._token_expires_at = time.time() + float(expires_in)

                logger.info("Token Amadeus OAuth 2.0 renovado com sucesso.")
                return self._access_token

        except (httpx.ConnectTimeout, httpx.ReadTimeout):
            raise CollectorTimeoutError("Timeout na autenticação OAuth Amadeus")
        except httpx.NetworkError as net_err:
            raise CollectorNetworkError(f"Falha de rede na autenticação Amadeus: {net_err}")
        except json.JSONDecodeError as json_err:
            raise CollectorParsingError(f"Resposta JSON inválida do token Amadeus: {json_err}")

    @collector_retry(max_attempts=3, min_wait=1.0, max_wait=6.0)
    async def fetch_quotes(
        self, origin_ext_id: str, dest_ext_id: str, travel_date: str
    ) -> List[StandardizedQuote]:
        """Consulta voos entre aeroportos IATA no Amadeus Flight Offers Search."""
        if not self.client_id or not self.client_secret:
            logger.info(
                f"[amadeus] Credenciais não configuradas. Pulando consulta real de {origin_ext_id}->{dest_ext_id}"
            )
            return []

        token = await self.get_access_token()

        search_url = f"{self.base_url}/v2/shopping/flight-offers"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        params = {
            "originLocationCode": origin_ext_id.upper(),
            "destinationLocationCode": dest_ext_id.upper(),
            "departureDate": travel_date,
            "adults": 1,
            "currencyCode": "BRL",
            "max": 50,
        }

        try:
            async with get_default_async_client(
                timeout=self.timeout_seconds, headers=headers
            ) as client:
                response = await client.get(search_url, params=params)

                if response.status_code == 429:
                    raise CollectorRateLimitError(
                        f"Rate limit atingido no Flight Offers da Amadeus (HTTP 429)"
                    )

                if response.status_code == 404:
                    logger.info(f"Nenhum voo encontrado no Amadeus: {origin_ext_id}->{dest_ext_id}")
                    return []

                response.raise_for_status()
                data = response.json()

                return parse_amadeus_flight_offers(
                    data,
                    origin_ext_id=origin_ext_id,
                    dest_ext_id=dest_ext_id,
                    default_baggage_estimate_brl=settings.DEFAULT_FLIGHT_BAGGAGE_FEE_ESTIMATE_BRL,
                    checkin_buffer_minutes=settings.AIRPORT_DOMESTIC_CHECKIN_BUFFER_MINUTES,
                )

        except (httpx.ConnectTimeout, httpx.ReadTimeout):
            raise CollectorTimeoutError(
                f"Timeout ao consultar voos Amadeus ({origin_ext_id}->{dest_ext_id})"
            )
        except httpx.NetworkError as net_err:
            raise CollectorNetworkError(f"Erro de conexão com API Amadeus: {net_err}")
        except json.JSONDecodeError as json_err:
            raise CollectorParsingError(f"Resposta JSON inválida da Amadeus: {json_err}")
