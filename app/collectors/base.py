"""Contrato abstrato de coletores, resiliência (Tenacity) e DTO padronizado (Trivio Core).

Todos os coletores (aéreos e rodoviários) seguem este contrato:
1. Retornam instâncias de StandardizedQuote com assinatura determinística de deduplicação.
2. Utilizam políticas de retentativa inteligente com Tenacity (exponential backoff com jitter).
3. Usam HTTPX assíncrono; recusas de acesso encerram a coleta, sem navegador headless
   nem contorno (ver app/collectors/compliance.py).
"""

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.collectors.compliance import NotAuthorizedError, RobotsDisallowedError, compliant_headers
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

logger = logging.getLogger("trivio.collectors")

# ==============================================================================
# 1. Hierarquia de Exceções dos Coletores
# ==============================================================================

class CollectorError(Exception):
    """Exceção base para qualquer falha em coletores Trivio."""
    pass


class CollectorNetworkError(CollectorError):
    """Falha de rede ou conectividade com a API/plataforma."""
    pass


class CollectorTimeoutError(CollectorError):
    """Timeout esgotado durante a requisição ao provedor."""
    pass


class CollectorRateLimitError(CollectorError):
    """Bloqueio temporário por excesso de requisições (HTTP 429)."""
    pass


class CollectorNotAuthorizedError(CollectorError):
    """O site recusou explicitamente o acesso automatizado (HTTP 401/403).

    Negativa definitiva do operador: o coletor para por aqui. Não há retentativa
    nem escalonamento para navegador headless — contornar a recusa seria evasão.
    A liberação é comercial (API oficial / programa de parceiros).
    """
    pass


class CollectorParsingError(CollectorError):
    """Estrutura de resposta inesperada ou alteração na API do provedor."""
    pass


# ==============================================================================
# 2. Resiliência e Retentativas (Tenacity Config)
# ==============================================================================

def is_transient_error(exc: BaseException) -> bool:
    """Verifica se uma exceção é transitória e elegível para retentativa."""
    if isinstance(exc, (CollectorNetworkError, CollectorTimeoutError, CollectorRateLimitError)):
        return True

    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError)):
        return True

    if isinstance(exc, httpx.HTTPStatusError):
        # 429 Too Many Requests e 5xx Server Errors são transitórios
        status = exc.response.status_code
        if status == 429 or 500 <= status <= 599:
            return True

    return False


def _log_retry_attempt(retry_state: RetryCallState) -> None:
    """Log detalhado de cada retentativa antes de aguardar o backoff."""
    fn_name = retry_state.fn.__name__ if retry_state.fn else "collector_call"
    attempt = retry_state.attempt_number
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    sleep_time = retry_state.next_action.sleep if retry_state.next_action else 0.0

    logger.warning(
        f"[{fn_name}] Tentativa {attempt} falhou ({type(exc).__name__}: {exc}). "
        f"Aguardando {sleep_time:.2f}s antes da próxima tentativa..."
    )


def collector_retry(
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 8.0,
):
    """Decorador de resiliência padrão para métodos de coleta com exponential backoff e jitter."""
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_random_exponential(min=min_wait, max=max_wait),
        retry=retry_if_exception(is_transient_error),
        before_sleep=_log_retry_attempt,
        reraise=True,
    )


# ==============================================================================
# 3. HTTP Client Padrão com Rotação de Headers
# ==============================================================================

def get_default_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Cabeçalhos de um cliente automatizado que se identifica honestamente.

    O Trivio NÃO se passa por navegador: o User-Agent declara o robô e oferece
    contato, conforme `app.collectors.compliance.build_user_agent`.
    """
    return compliant_headers(extra)


def get_default_async_client(
    timeout: float = 15.0,
    headers: Optional[Dict[str, str]] = None,
) -> httpx.AsyncClient:
    """Cliente HTTPX assíncrono identificado, para APIs oficiais e autenticadas.

    Não verifica robots.txt. Use-o apenas para APIs contratadas/credenciadas
    (ex: Ignav). Para raspagem de sites públicos use
    `app.collectors.compliance.CompliantHTTPClient`, que valida o robots.txt,
    respeita o Crawl-delay e trata 401/403 como negativa definitiva.
    """
    return httpx.AsyncClient(
        headers=get_default_headers(headers),
        timeout=httpx.Timeout(timeout, connect=5.0),
        follow_redirects=True,
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
    )


# ==============================================================================
# 4. Assinatura e DTO StandardizedQuote
# ==============================================================================

def generate_dedup_signature(
    modal: str,
    carrier_name: str,
    origin_id: str,
    destination_id: str,
    departure_time: datetime | str,
    service_class: str = "CONVENCIONAL",
) -> str:
    """Gera chave única SHA-256 (32 caracteres) para identificar a viagem física.
    
    A mesma viagem física comercializada por canais diferentes (ex.: a mesma partida em
    dois distribuidores) produz a mesma assinatura.
    """
    normalized_modal = str(modal).strip().upper()
    normalized_carrier = str(carrier_name).strip().upper()
    normalized_origin = str(origin_id).strip().upper()
    normalized_dest = str(destination_id).strip().upper()
    normalized_service = str(service_class or "CONVENCIONAL").strip().upper()

    if isinstance(departure_time, datetime):
        dep_str = departure_time.strftime("%Y-%m-%dT%H:%M")
    else:
        dep_str = str(departure_time).strip()
        if len(dep_str) >= 16 and "T" in dep_str:
            dep_str = dep_str[:16]

    raw_key = f"{normalized_modal}|{normalized_carrier}|{normalized_origin}|{normalized_dest}|{dep_str}|{normalized_service}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]


@dataclass
class StandardizedQuote:
    """DTO padronizado para cotações coletadas de qualquer provedor."""

    origin_external_id: str
    destination_external_id: str
    modal: str                         # 'AIR' ou 'ROAD'
    provider_source: str               # Quem forneceu o dado (ex: 'ignav', 'antt_projection', 'gtfs_artesp')
    carrier_name: str                  # Quem opera o transporte (ex: 'Viação Guanabara', 'Gol', 'Azul')
    service_class: str                 # 'ECONOMY', 'SEMI_LEITO', 'LEITO', 'CONVENCIONAL'
    departure_time: datetime
    arrival_time: datetime
    duration_minutes: int
    base_price: float
    taxes_price: float = 0.0
    baggage_fee_estimate: float = 0.0
    transfer_cost_estimate: float = 0.0
    door_to_door_duration_minutes: int = 0
    trivio_score: float = 0.0
    canonical_origin_id: Optional[str] = None
    canonical_destination_id: Optional[str] = None
    route_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_effective_price(self) -> float:
        """Custo real total porta a porta (Passagem + Taxas + Bagagem + Transfer Urbano)."""
        return round(
            self.base_price
            + self.taxes_price
            + self.baggage_fee_estimate
            + self.transfer_cost_estimate,
            2,
        )

    @property
    def dedup_signature(self) -> str:
        """Gera chave única da viagem para agrupar duplicatas vindas de múltiplos brokers.
        
        Prioriza os identificadores canônicos de terminal (ex: 'TIETE_SP', 'GRU') quando
        disponíveis, permitindo que múltiplos brokers (ex: ClickBus 'sao-paulo-tiete-sp'
        e DeÔnibus 'sao-paulo-tiete') agrupem perfeitamente a mesma viagem física.
        """
        origin = self.canonical_origin_id or self.origin_external_id
        dest = self.canonical_destination_id or self.destination_external_id
        return generate_dedup_signature(
            modal=self.modal,
            carrier_name=self.carrier_name,
            origin_id=origin,
            destination_id=dest,
            departure_time=self.departure_time,
            service_class=self.service_class,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a cotação em dicionário primitivo."""
        return {
            "dedup_signature": self.dedup_signature,
            "origin_external_id": self.origin_external_id,
            "destination_external_id": self.destination_external_id,
            "canonical_origin_id": self.canonical_origin_id,
            "canonical_destination_id": self.canonical_destination_id,
            "modal": self.modal,
            "provider_source": self.provider_source,
            "carrier_name": self.carrier_name,
            "service_class": self.service_class,
            "departure_time": self.departure_time.isoformat(),
            "arrival_time": self.arrival_time.isoformat(),
            "duration_minutes": self.duration_minutes,
            "door_to_door_duration_minutes": self.door_to_door_duration_minutes,
            "base_price": self.base_price,
            "taxes_price": self.taxes_price,
            "baggage_fee_estimate": self.baggage_fee_estimate,
            "transfer_cost_estimate": self.transfer_cost_estimate,
            "total_effective_price": self.total_effective_price,
            "trivio_score": self.trivio_score,
            "route_metadata": deepcopy(self.route_metadata),
        }


# ==============================================================================
# 5. BaseCollector Abstrato
# ==============================================================================

class BaseCollector(ABC):
    """Interface abstrata obrigatória para todos os coletores de cotação."""

    provider_name: str
    modal: str                          # 'ROAD' ou 'AIR'
    timeout_seconds: float = 15.0
    max_retries: int = 3
    # Marcado quando o provedor recusa acesso automatizado (401/403 ou robots.txt).
    # Sinaliza que o desbloqueio é comercial, não técnico.
    access_denied: bool = False

    @abstractmethod
    async def fetch_quotes(
        self, origin_ext_id: str, dest_ext_id: str, travel_date: str
    ) -> List[StandardizedQuote]:
        """Recebe os códigos externos do provedor e a data (YYYY-MM-DD).

        Retorna uma lista de StandardizedQuote.
        Deve ser decorado com @collector_retry nas implementações filhas.
        """
        pass

    async def safe_fetch(
        self, origin_ext_id: str, dest_ext_id: str, travel_date: str
    ) -> List[StandardizedQuote]:
        """Executa a coleta capturando métricas de latência e protegendo contra falhas fatais.

        Se o provedor recusar o acesso automatizado (401/403) ou o robots.txt proibir
        a URL, o coletor registra a negativa e devolve lista vazia. Não há contorno.
        """
        start_time = time.perf_counter()
        logger.info(
            f"[{self.provider_name}] Iniciando busca de {origin_ext_id} -> {dest_ext_id} em {travel_date}"
        )

        try:
            quotes = await self.fetch_quotes(origin_ext_id, dest_ext_id, travel_date)
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)

            for q in quotes:
                if "collector_stats" not in q.route_metadata:
                    q.route_metadata["collector_stats"] = {}
                q.route_metadata["collector_stats"].update({
                    "provider": self.provider_name,
                    "tier": "tier_1_httpx",
                    "latency_ms": elapsed_ms,
                })

            logger.info(
                f"[{self.provider_name}] Concluído com sucesso: {len(quotes)} cotações em {elapsed_ms}ms"
            )
            return quotes

        except (CollectorNotAuthorizedError, NotAuthorizedError) as denied:
            # Recusa explícita do operador do site. Respeitamos e paramos aqui.
            self.access_denied = True
            logger.error(
                f"[{self.provider_name}] Acesso automatizado NEGADO pelo provedor: {denied} "
                "Nenhuma tentativa de contorno será feita. Para obter dados deste provedor, "
                "solicite acesso à API oficial ou ao programa de parceiros."
            )
            return []

        except RobotsDisallowedError as robots_err:
            self.access_denied = True
            logger.error(
                f"[{self.provider_name}] Requisição não realizada por proibição no robots.txt: {robots_err}"
            )
            return []

        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
            logger.error(
                f"[{self.provider_name}] Falha na coleta após retentativas ({type(exc).__name__}: {exc}) "
                f"em {elapsed_ms}ms"
            )
            return []
