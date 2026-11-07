"""Coletor Rodoviário para ClickBus (Trivio Core).

ACESSO EM CONFORMIDADE
----------------------
Este coletor acessa a ClickBus como um robô identificado e obediente:

- O User-Agent declara o Trivio e oferece contato (nunca imitamos um navegador).
- Toda URL passa pela verificação do robots.txt antes da requisição.
- Respeitamos o Crawl-delay e o cabeçalho Retry-After.
- HTTP 401/403 é tratado como negativa definitiva: sem retentativa e sem
  navegador headless. Contornar a recusa seria evasão de detecção de bot.

Situação atual (verificada em 07/11/2026)
-----------------------------------------
O `robots.txt` da ClickBus permite `/onibus/<origem>/<destino>`, mas PROÍBE
qualquer URL com `?departureDate=` (`Disallow: /*?departureDate=`), que é
justamente a página de resultados de busca. Além disso, o WAF (Azion) responde
HTTP 403 às requisições automatizadas ao endpoint interno `/api/v4/trips`.

Conclusão: hoje a ClickBus NÃO autoriza coleta automatizada de tarifas. O
caminho correto é comercial. A ClickBus mantém uma API B2B usada por parceiros
(ex: Envision Tecnologia, VaideBus); o acesso se solicita ao time de parcerias.
Enquanto não houver autorização, o coletor registra a negativa e devolve lista
vazia, e o Trivio exibe estimativas claramente rotuladas como simulação.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from app.collectors.base import (
    BaseCollector,
    CollectorNetworkError,
    CollectorParsingError,
    CollectorRateLimitError,
    CollectorTimeoutError,
    StandardizedQuote,
    collector_retry,
)
from app.collectors.road.taxonomy import (
    SERVICE_CLASS_MAPPING as _SERVICE_CLASS_MAPPING,
    normalize_bus_service_class,
)
from app.collectors.compliance import (
    CompliantHTTPClient,
    NotAuthorizedError,
    RobotsDisallowedError,
    parse_retry_after,
)

logger = logging.getLogger("trivio.collectors.clickbus")


# ==============================================================================
# 1. Normalização de Classes de Serviço Rodoviário (Resoluções ANTT)
# ==============================================================================

# A taxonomia de classes vive em app/collectors/road/taxonomy.py para ser
# compartilhada com os demais coletores rodoviários (ex: ANTT).
# Reexportado aqui por compatibilidade com imports existentes.
SERVICE_CLASS_MAPPING = _SERVICE_CLASS_MAPPING


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

CLICKBUS_SLUG_MAP = {
    "SAO": "sao-paulo-sp",
    "CGH": "sao-paulo-sp",
    "GRU": "sao-paulo-sp",
    "TIETE": "sao-paulo-tiete-sp",
    "BARRA_FUNDA": "sao-paulo-barra-funda-sp",
    "RIO": "rio-de-janeiro-rj",
    "SDU": "rio-de-janeiro-rj",
    "GIG": "rio-de-janeiro-rj",
    "NOVO_RIO": "rio-de-janeiro-novo-rio-rj",
    "CWB": "curitiba-pr",
    "RODO_CWB": "curitiba-pr",
    "POA": "porto-alegre-rs",
    "ROD_POA": "porto-alegre-rs",
    "VCP": "campinas-sp",
    "ROD_CAMPINAS": "campinas-sp",
    "PET": "pelotas-rs",
    "ROD_PELOTAS": "pelotas-rs",
    "RIG": "rio-grande-rs",
    "ROD_RIO_GRANDE": "rio-grande-rs",
    "BHZ": "belo-horizonte-mg",
    "CNF": "belo-horizonte-mg",
    "ROD_BH": "belo-horizonte-mg",
    "BSB": "brasilia-df",
    "ROD_BSB": "brasilia-df",
    "FLN": "florianopolis-sc",
    "ROD_FLN": "florianopolis-sc",
    "SSA": "salvador-ba",
    "FOR": "fortaleza-ce",
    "REC": "recife-pe",
}


class ClickBusCollector(BaseCollector):
    """Coletor assíncrono para o ecossistema ClickBus."""

    provider_name = "clickbus"
    modal = "ROAD"

    def __init__(
        self,
        base_api_url: str = "https://www.clickbus.com.br/api/v4",
        timeout_seconds: float = 12.0,
        api_token: Optional[str] = None,
    ):
        self.base_api_url = base_api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        # Credencial da API de parceiros, quando houver autorização formal.
        self.api_token = api_token

    @collector_retry(max_attempts=3, min_wait=1.0, max_wait=5.0)
    async def fetch_quotes(
        self, origin_ext_id: str, dest_ext_id: str, travel_date: str
    ) -> List[StandardizedQuote]:
        """Consulta viagens na ClickBus respeitando robots.txt, ritmo e recusas.

        Levanta NotAuthorizedError se o site recusar (401/403) e RobotsDisallowedError
        se o robots.txt proibir a URL. Em ambos os casos o `safe_fetch` encerra a
        coleta sem contorno.
        """
        orig_slug = CLICKBUS_SLUG_MAP.get(origin_ext_id.upper(), origin_ext_id.lower())
        dest_slug = CLICKBUS_SLUG_MAP.get(dest_ext_id.upper(), dest_ext_id.lower())

        client_headers = {"Accept": "application/json"}
        if self.api_token:
            client_headers["Authorization"] = f"Bearer {self.api_token}"

        search_url = f"{self.base_api_url}/trips"
        params = {
            "from": orig_slug,
            "to": dest_slug,
            "departure": travel_date,
        }

        try:
            async with CompliantHTTPClient(
                timeout=self.timeout_seconds, headers=client_headers
            ) as client:
                # O cliente verifica o robots.txt, aplica o Crawl-delay e converte
                # 401/403 em NotAuthorizedError antes de chegar aqui.
                response = await client.get(search_url, params=params)

                if response.status_code == 429:
                    retry_after = parse_retry_after(response.headers.get("Retry-After"))
                    suffix = f" Retry-After: {retry_after:.0f}s." if retry_after else ""
                    if retry_after:
                        await asyncio.sleep(min(retry_after, 60.0))
                    raise CollectorRateLimitError(
                        f"A ClickBus pediu para reduzir o ritmo (HTTP 429).{suffix}"
                    )

                if response.status_code >= 500:
                    response.raise_for_status()

                if response.status_code == 404:
                    logger.info(f"Nenhuma rota encontrada na ClickBus: {orig_slug} -> {dest_slug}")
                    return []

                response.raise_for_status()
                data = response.json()
                return parse_clickbus_trips(data, origin_ext_id, dest_ext_id)

        except (httpx.ConnectTimeout, httpx.ReadTimeout):
            raise CollectorTimeoutError(f"Timeout ao consultar ClickBus para {orig_slug}->{dest_slug}")
        except httpx.NetworkError as net_err:
            raise CollectorNetworkError(f"Erro de conexão com ClickBus: {net_err}")
        except json.JSONDecodeError as json_err:
            raise CollectorParsingError(f"Resposta JSON inválida da ClickBus: {json_err}")
