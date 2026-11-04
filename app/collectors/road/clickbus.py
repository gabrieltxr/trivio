"""Coletor Rodoviário para ClickBus (Trivio Core).

Estratégia de Duas Camadas:
- Tier 1 (Primário): Engenharia Reversa com HTTPX assíncrono consumindo diretamente
  as APIs internas de busca (JSON/XHR), com rotação de headers e sessão.
- Tier 2 (Fallback): Navegador Headless (Playwright) para contornar proteções
  WAF/Azion/Cloudflare quando o Tier 1 receber HTTP 403 / CollectorBlockedError.
"""

from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

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
    get_default_async_client,
)

logger = logging.getLogger("trivio.collectors.clickbus")


# ==============================================================================
# 1. Normalização de Classes de Serviço Rodoviário (Resoluções ANTT)
# ==============================================================================

SERVICE_CLASS_MAPPING = {
    "convencional": "CONVENCIONAL",
    "convencional com ar": "CONVENCIONAL",
    "executivo": "EXECUTIVO",
    "semi leito": "SEMI_LEITO",
    "semi-leito": "SEMI_LEITO",
    "semileito": "SEMI_LEITO",
    "leito": "LEITO",
    "leito turismo": "LEITO",
    "leito cama": "LEITO_CAMA",
    "leito-cama": "LEITO_CAMA",
    "cama": "LEITO_CAMA",
    "prime": "LEITO_CAMA",
}


def normalize_bus_service_class(raw_service: str) -> str:
    """Normaliza nomes comerciais de classes de ônibus para a taxonomia Trivio."""
    if not raw_service:
        return "CONVENCIONAL"
    cleaned = raw_service.strip().lower()
    # Ordena chaves por tamanho decrescente para dar prioridade a termos mais específicos (ex: 'leito cama' antes de 'leito')
    for key in sorted(SERVICE_CLASS_MAPPING.keys(), key=len, reverse=True):
        if key in cleaned:
            return SERVICE_CLASS_MAPPING[key]
    return cleaned.upper().replace(" ", "_")


# ==============================================================================
# 2. Parser de Respostas JSON da ClickBus
# ==============================================================================

def _parse_datetime(dt_str: str) -> datetime:
    """Converte strings de data/hora nos múltiplos formatos retornados pela API."""
    dt_str = dt_str.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(dt_str)
    except Exception as e:
        raise ValueError(f"Não foi possível converter data/hora: '{dt_str}': {e}")


def parse_clickbus_trips(
    data: Dict[str, Any],
    origin_ext_id: str,
    dest_ext_id: str,
) -> List[StandardizedQuote]:
    """Extrai viagens estruturadas a partir de um payload JSON da ClickBus.
    
    Suporta tanto a estrutura da API v4/v5 (items) quanto do frontend Next.js (trips).
    """
    quotes: List[StandardizedQuote] = []
    items = data.get("items") or data.get("trips") or data.get("results") or []

    if not isinstance(items, list):
        logger.warning(f"Payload ClickBus não contém lista de itens válida: {type(items)}")
        return []

    for item in items:
        try:
            # 1. Operadora (Viação)
            company_data = item.get("bus_company") or item.get("company") or {}
            carrier_name = (
                company_data.get("name")
                or item.get("carrier_name")
                or item.get("company_name")
                or "Viação Não Informada"
            ).strip()

            # 2. Classe de Serviço
            service_data = item.get("service") or item.get("service_class") or {}
            if isinstance(service_data, dict):
                raw_service = service_data.get("name") or service_data.get("type") or "Convencional"
            else:
                raw_service = str(service_data) or "Convencional"
            service_class = normalize_bus_service_class(raw_service)

            # 3. Horários e Duração
            dep_obj = item.get("departure") or {}
            arr_obj = item.get("arrival") or {}

            if isinstance(dep_obj, dict):
                dep_date = dep_obj.get("date") or ""
                dep_time = dep_obj.get("time") or ""
                if dep_date and dep_time:
                    dep_dt = _parse_datetime(f"{dep_date} {dep_time}")
                elif dep_obj.get("datetime"):
                    dep_dt = _parse_datetime(dep_obj["datetime"])
                else:
                    continue
            else:
                dep_dt = _parse_datetime(str(dep_obj))

            if isinstance(arr_obj, dict):
                arr_date = arr_obj.get("date") or dep_dt.strftime("%Y-%m-%d")
                arr_time = arr_obj.get("time") or ""
                if arr_date and arr_time:
                    arr_dt = _parse_datetime(f"{arr_date} {arr_time}")
                elif arr_obj.get("datetime"):
                    arr_dt = _parse_datetime(arr_obj["datetime"])
                else:
                    arr_dt = dep_dt
            else:
                arr_dt = _parse_datetime(str(arr_obj))

            duration_min = int((arr_dt - dep_dt).total_seconds() / 60)
            if duration_min <= 0:
                # Duração em string no item (ex: '06:00' ou em minutos)
                dur_raw = item.get("duration") or item.get("duration_minutes")
                if isinstance(dur_raw, int):
                    duration_min = dur_raw
                elif isinstance(dur_raw, str) and ":" in dur_raw:
                    parts = dur_raw.split(":")
                    duration_min = int(parts[0]) * 60 + int(parts[1])
                else:
                    duration_min = 60

            # 4. Preços e Taxas
            prices = item.get("price") or item.get("prices") or {}
            if isinstance(prices, dict):
                base_raw = prices.get("amount") or prices.get("base") or prices.get("fare") or 0.0
                tax_raw = prices.get("service_fee") or prices.get("taxes") or prices.get("fee") or 0.0
            else:
                base_raw = float(prices or 0.0)
                tax_raw = 0.0

            # Normalização de centavos (se a API retornar 12500 para R$ 125,00)
            base_price = float(base_raw)
            tax_price = float(tax_raw)
            if base_price > 5000.0:  # Provavelmente em centavos
                base_price = round(base_price / 100.0, 2)
                tax_price = round(tax_price / 100.0, 2)

            if base_price <= 0.0:
                continue

            # 5. Metadados e Amenities
            amenities = []
            for feat in item.get("features", []) or item.get("amenities", []):
                if isinstance(feat, dict):
                    feat_name = feat.get("name") or feat.get("type")
                    if feat_name:
                        amenities.append(str(feat_name).lower())
                elif isinstance(feat, str):
                    amenities.append(feat.lower())

            seats_available = item.get("available_seats") or item.get("seats_left")
            trip_id = str(item.get("id") or item.get("trip_id") or "")

            route_meta = {
                "trip_id": trip_id,
                "company_id": str(company_data.get("id", "")),
                "is_direct": True,
                "amenities": sorted(list(set(amenities))),
                "seats_available": seats_available,
                "booking_url": f"https://www.clickbus.com.br/onibus/{origin_ext_id}/{dest_ext_id}",
            }

            quote = StandardizedQuote(
                origin_external_id=origin_ext_id,
                destination_external_id=dest_ext_id,
                modal="ROAD",
                provider_source="clickbus",
                carrier_name=carrier_name,
                service_class=service_class,
                departure_time=dep_dt,
                arrival_time=arr_dt,
                duration_minutes=duration_min,
                door_to_door_duration_minutes=duration_min + 20,  # 20 min buffer rodoviário padrão
                base_price=round(base_price, 2),
                taxes_price=round(tax_price, 2),
                baggage_fee_estimate=0.0,  # Gratuita por regulamentação da ANTT (1 mala 30kg + 1 mão 5kg)
                transfer_cost_estimate=0.0,
                route_metadata=route_meta,
            )
            quotes.append(quote)

        except Exception as err:
            logger.debug(f"Falha ao processar item ClickBus: {err}")
            continue

    return quotes


# ==============================================================================
# 3. Coletor Principal ClickBus
# ==============================================================================

class ClickBusCollector(BaseCollector):
    """Coletor assíncrono para o ecossistema ClickBus."""

    provider_name = "clickbus"
    modal = "ROAD"
    supports_headless_fallback = True

    def __init__(
        self,
        base_api_url: str = "https://www.clickbus.com.br/api/v4",
        timeout_seconds: float = 12.0,
    ):
        self.base_api_url = base_api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @collector_retry(max_attempts=3, min_wait=1.0, max_wait=5.0)
    async def fetch_quotes(
        self, origin_ext_id: str, dest_ext_id: str, travel_date: str
    ) -> List[StandardizedQuote]:
        """Tier 1: Requisição assíncrona HTTPX contra as APIs JSON da ClickBus."""
        client_headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.clickbus.com.br",
            "Referer": f"https://www.clickbus.com.br/onibus/{origin_ext_id}/{dest_ext_id}",
        }

        search_url = f"{self.base_api_url}/trips"
        params = {
            "from": origin_ext_id,
            "to": dest_ext_id,
            "departure": travel_date,
        }

        try:
            async with get_default_async_client(
                timeout=self.timeout_seconds, headers=client_headers
            ) as client:
                response = await client.get(search_url, params=params)

                # Detecção de bloqueios anti-bot (Azion / Cloudflare)
                if response.status_code == 403:
                    raise CollectorBlockedError(
                        f"Bloqueio WAF/Azion na ClickBus (HTTP 403) para rota {origin_ext_id}->{dest_ext_id}"
                    )

                if response.status_code == 429:
                    raise CollectorRateLimitError(
                        f"Rate limit atingido na ClickBus (HTTP 429)"
                    )

                if response.status_code >= 500:
                    response.raise_for_status()

                if response.status_code == 404:
                    logger.info(f"Nenhuma rota encontrada na ClickBus: {origin_ext_id} -> {dest_ext_id}")
                    return []

                response.raise_for_status()
                data = response.json()
                return parse_clickbus_trips(data, origin_ext_id, dest_ext_id)

        except (httpx.ConnectTimeout, httpx.ReadTimeout):
            raise CollectorTimeoutError(f"Timeout ao consultar ClickBus para {origin_ext_id}->{dest_ext_id}")
        except httpx.NetworkError as net_err:
            raise CollectorNetworkError(f"Erro de conexão com ClickBus: {net_err}")
        except json.JSONDecodeError as json_err:
            raise CollectorParsingError(f"Resposta JSON inválida da ClickBus: {json_err}")

    async def fallback_fetch_quotes(
        self, origin_ext_id: str, dest_ext_id: str, travel_date: str
    ) -> List[StandardizedQuote]:
        """Tier 2: Contingência com Playwright em caso de bloqueio anti-bot severo no Tier 1."""
        logger.info(
            f"[clickbus-headless] Iniciando fallback Playwright para {origin_ext_id} -> {dest_ext_id} em {travel_date}"
        )
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("[clickbus-headless] Playwright não está instalado. Não é possível rodar o Tier 2.")
            return []

        search_url = (
            f"https://www.clickbus.com.br/onibus/{origin_ext_id}/{dest_ext_id}?departureDate={travel_date}"
        )
        intercepted_quotes: List[StandardizedQuote] = []

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=DEFAULT_HEADERS.get("User-Agent"),
                    locale="pt-BR",
                    viewport={"width": 1280, "height": 800},
                )
                page = await context.new_page()

                # Intercepta respostas XHR de viagens
                async def handle_response(response):
                    if "trips" in response.url and response.status == 200:
                        try:
                            json_data = await response.json()
                            parsed = parse_clickbus_trips(json_data, origin_ext_id, dest_ext_id)
                            intercepted_quotes.extend(parsed)
                        except Exception:
                            pass

                page.on("response", handle_response)

                await page.goto(search_url, wait_until="networkidle", timeout=20000)
                await browser.close()

            logger.info(f"[clickbus-headless] Sucesso: {len(intercepted_quotes)} cotações interceptadas via Playwright.")
            return intercepted_quotes

        except Exception as pw_err:
            logger.error(f"[clickbus-headless] Falha na execução do Playwright: {pw_err}")
            return []
