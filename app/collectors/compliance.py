"""Camada de Conformidade para Coleta Web (Trivio Core).

Garante que todo acesso automatizado do Trivio a sites de terceiros seja:

1. **Identificado**: o User-Agent declara honestamente que é o robô do Trivio e
   oferece uma URL/e-mail de contato. Nunca nos passamos por um navegador.
2. **Autorizado pelo robots.txt**: cada URL é verificada antes da requisição,
   usando `protego` (mesmo parser do Scrapy, que implementa a especificação do
   Google: a regra MAIS ESPECÍFICA vence).

   Atenção: `urllib.robotparser` da stdlib NÃO serve aqui. Ele aplica a primeira
   regra que casa, então um `Allow: /` no topo do arquivo anula todos os
   `Disallow` seguintes — inclusive `/login` e `/checkout/`.
3. **Educado**: respeita `Crawl-delay`, mantém um intervalo mínimo entre
   requisições ao mesmo host e honra o cabeçalho `Retry-After` em HTTP 429.
4. **Obediente a recusas**: HTTP 401/403 é tratado como negativa explícita e
   definitiva do operador do site. Não há retentativa, não há navegador headless,
   não há contorno. O coletor desiste e reporta que precisa de autorização.

Quando um provedor recusa acesso automatizado, o caminho correto é comercial
(API oficial, programa de parceiros/afiliados), não técnico.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import httpx
from protego import Protego

from app.config import settings

logger = logging.getLogger("trivio.collectors.compliance")

# Tempo de vida do cache de robots.txt (não refazer o download a cada requisição)
ROBOTS_CACHE_TTL_SECONDS = 3600.0
ROBOTS_FETCH_TIMEOUT_SECONDS = 10.0


# ==============================================================================
# 1. Exceções de Conformidade
# ==============================================================================

class ComplianceError(Exception):
    """Falha base de conformidade: a requisição não deve ser realizada."""


class RobotsDisallowedError(ComplianceError):
    """A URL é proibida pelo robots.txt do site. A requisição não é feita."""


class RobotsUnavailableError(RobotsDisallowedError):
    """O robots.txt não pôde ser lido, então assumimos proibição por prudência.

    Subclasse de RobotsDisallowedError (o efeito é o mesmo: não requisitar), mas
    com causa distinta — aqui o problema é de rede/servidor, não uma regra do site.
    Separar as duas evita diagnosticar indisponibilidade como proibição.
    """


class NotAuthorizedError(ComplianceError):
    """O site recusou explicitamente o acesso (HTTP 401/403).

    Negativa definitiva: não retentar, não escalar para navegador headless.
    Resolver pela via comercial (API oficial / parceria).
    """


# ==============================================================================
# 2. Identificação Honesta (User-Agent)
# ==============================================================================

def build_user_agent() -> str:
    """Monta um User-Agent que identifica o Trivio e oferece canal de contato.

    Formato convencional de robôs bem-comportados:
        TrivioBot/1.0 (+https://exemplo.com/bot; bot@exemplo.com)
    """
    name = settings.BOT_NAME.strip() or "TrivioBot"
    version = settings.BOT_VERSION.strip() or "1.0"

    contacts: List[str] = []
    if settings.BOT_INFO_URL.strip():
        contacts.append(f"+{settings.BOT_INFO_URL.strip()}")
    if settings.BOT_CONTACT_EMAIL.strip():
        contacts.append(settings.BOT_CONTACT_EMAIL.strip())

    if contacts:
        return f"{name}/{version} ({'; '.join(contacts)})"
    return f"{name}/{version}"


def compliant_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Cabeçalhos padrão de um cliente automatizado identificado."""
    headers = {
        "User-Agent": build_user_agent(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "From": settings.BOT_CONTACT_EMAIL.strip(),
    }
    if not headers["From"]:
        headers.pop("From")
    if extra:
        headers.update(extra)
    return headers


# ==============================================================================
# 3. Política de robots.txt (cache por host)
# ==============================================================================

@dataclass
class _RobotsEntry:
    parser: Optional[Protego]
    allow_all: bool
    deny_all: bool
    fetched_at: float

    def is_fresh(self) -> bool:
        return (time.monotonic() - self.fetched_at) < ROBOTS_CACHE_TTL_SECONDS


class RobotsPolicy:
    """Busca, interpreta e cacheia o robots.txt de cada host."""

    def __init__(self, user_agent: Optional[str] = None):
        self._user_agent = user_agent or build_user_agent()
        self._cache: Dict[str, _RobotsEntry] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlparse(url)
        return urlunparse((parts.scheme, parts.netloc, "", "", "", ""))

    def _lock_for(self, origin: str) -> asyncio.Lock:
        if origin not in self._locks:
            self._locks[origin] = asyncio.Lock()
        return self._locks[origin]

    async def _load(self, origin: str) -> _RobotsEntry:
        """Baixa e interpreta o robots.txt de um host, seguindo a convenção padrão.

        - 200: aplica as regras do arquivo.
        - 404/410: sem restrições publicadas, tudo liberado.
        - 401/403: o próprio robots.txt é restrito, assume-se proibição total.
        - 5xx / erro de rede: proibição temporária (falha fechada, por prudência).
        """
        robots_url = f"{origin}/robots.txt"
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": self._user_agent},
                timeout=ROBOTS_FETCH_TIMEOUT_SECONDS,
                follow_redirects=True,
            ) as client:
                resp = await client.get(robots_url)
        except Exception as exc:
            logger.warning(
                f"[robots] Não foi possível obter {robots_url} ({type(exc).__name__}: {exc}). "
                "Assumindo proibição temporária por prudência."
            )
            return _RobotsEntry(None, allow_all=False, deny_all=True, fetched_at=time.monotonic())

        if resp.status_code in (401, 403):
            logger.warning(f"[robots] {robots_url} retornou {resp.status_code}: proibição total assumida.")
            return _RobotsEntry(None, allow_all=False, deny_all=True, fetched_at=time.monotonic())

        if resp.status_code in (404, 410):
            logger.info(f"[robots] {robots_url} inexistente ({resp.status_code}): sem restrições publicadas.")
            return _RobotsEntry(None, allow_all=True, deny_all=False, fetched_at=time.monotonic())

        if resp.status_code >= 500:
            logger.warning(f"[robots] {robots_url} indisponível ({resp.status_code}): proibição temporária.")
            return _RobotsEntry(None, allow_all=False, deny_all=True, fetched_at=time.monotonic())

        parser = Protego.parse(resp.text)
        logger.info(f"[robots] Regras de {origin} carregadas para o agente '{self._user_agent}'.")
        return _RobotsEntry(parser, allow_all=False, deny_all=False, fetched_at=time.monotonic())

    async def _entry_for(self, url: str) -> _RobotsEntry:
        origin = self._origin(url)
        cached = self._cache.get(origin)
        if cached and cached.is_fresh():
            return cached

        async with self._lock_for(origin):
            cached = self._cache.get(origin)
            if cached and cached.is_fresh():
                return cached
            entry = await self._load(origin)
            self._cache[origin] = entry
            return entry

    async def evaluate(self, url: str) -> Tuple[bool, str]:
        """Avalia uma URL devolvendo (permitido, motivo).

        Motivos: 'allowed', 'disallowed' (regra explícita do site) ou
        'unreadable' (robots.txt inacessível — proibição por prudência).
        """
        if not settings.RESPECT_ROBOTS_TXT:
            return True, "allowed"
        entry = await self._entry_for(url)
        if entry.deny_all:
            return False, "unreadable"
        if entry.allow_all or entry.parser is None:
            return True, "allowed"
        if entry.parser.can_fetch(url, self._user_agent):
            return True, "allowed"
        return False, "disallowed"

    async def can_fetch(self, url: str) -> bool:
        """Informa se o robots.txt do host autoriza o nosso agente a acessar a URL."""
        allowed, _ = await self.evaluate(url)
        return allowed

    async def crawl_delay(self, url: str) -> float:
        """Intervalo mínimo entre requisições, conforme o robots.txt ou o padrão do Trivio."""
        entry = await self._entry_for(url)
        if entry.parser is not None:
            declared = entry.parser.crawl_delay(self._user_agent)
            if declared:
                return float(declared)
        return float(settings.DEFAULT_CRAWL_DELAY_SECONDS)

    async def sitemaps(self, url: str) -> List[str]:
        """Sitemaps publicados pelo host (canal de descoberta oferecido pelo próprio site)."""
        entry = await self._entry_for(url)
        if entry.parser is None:
            return []
        return list(entry.parser.sitemaps or [])


# ==============================================================================
# 4. Limitador de Ritmo por Host
# ==============================================================================

class PolitenessLimiter:
    """Garante um intervalo mínimo entre requisições consecutivas ao mesmo host."""

    def __init__(self):
        self._last_request: Dict[str, float] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def _lock_for(self, host: str) -> asyncio.Lock:
        if host not in self._locks:
            self._locks[host] = asyncio.Lock()
        return self._locks[host]

    async def wait(self, url: str, min_interval_seconds: float) -> None:
        host = urlparse(url).netloc
        async with self._lock_for(host):
            previous = self._last_request.get(host)
            now = time.monotonic()
            if previous is not None:
                elapsed = now - previous
                if elapsed < min_interval_seconds:
                    pause = min_interval_seconds - elapsed
                    logger.debug(f"[polidez] Aguardando {pause:.2f}s antes de acessar {host} novamente.")
                    await asyncio.sleep(pause)
            self._last_request[host] = time.monotonic()


# ==============================================================================
# 5. Cliente HTTP em Conformidade
# ==============================================================================

# Instâncias compartilhadas por processo (cache de robots e ritmo entre coletores)
_shared_robots = RobotsPolicy()
_shared_limiter = PolitenessLimiter()


def get_robots_policy() -> RobotsPolicy:
    return _shared_robots


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Interpreta o cabeçalho Retry-After em segundos ou como data HTTP."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


class CompliantHTTPClient:
    """Cliente HTTP que só emite requisições permitidas e identificadas.

    Antes de cada requisição verifica o robots.txt e respeita o ritmo do host.
    Uma recusa explícita do servidor (401/403) vira NotAuthorizedError e encerra
    a tentativa: nada de retentar ou escalar para navegador headless.
    """

    def __init__(
        self,
        timeout: float = 15.0,
        headers: Optional[Dict[str, str]] = None,
        robots: Optional[RobotsPolicy] = None,
        limiter: Optional[PolitenessLimiter] = None,
    ):
        self._timeout = timeout
        self._extra_headers = headers or {}
        self._robots = robots or _shared_robots
        self._limiter = limiter or _shared_limiter
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "CompliantHTTPClient":
        self._client = httpx.AsyncClient(
            headers=compliant_headers(self._extra_headers),
            timeout=httpx.Timeout(self._timeout, connect=5.0),
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _evaluate_robots(self, url: str) -> Tuple[bool, str]:
        """Compatível com políticas simplificadas (ex: stubs de teste com apenas can_fetch)."""
        evaluate = getattr(self._robots, "evaluate", None)
        if evaluate is not None:
            return await evaluate(url)
        return (await self._robots.can_fetch(url)), "disallowed"

    async def get(self, url: str, params: Optional[Dict[str, object]] = None) -> httpx.Response:
        if self._client is None:
            raise RuntimeError("Use CompliantHTTPClient como context manager (async with).")

        # A verificação do robots.txt precisa considerar a URL final, com query string
        final_url = str(httpx.URL(url, params=params)) if params else url

        allowed, reason = await self._evaluate_robots(final_url)
        if not allowed:
            if reason == "unreadable":
                raise RobotsUnavailableError(
                    f"Não foi possível ler o robots.txt do host para autorizar {final_url}. "
                    "Pode ser instabilidade de rede ou do servidor; o Trivio assume proibição "
                    "por prudência. Tente novamente mais tarde."
                )
            raise RobotsDisallowedError(
                f"O robots.txt do site proíbe o agente '{build_user_agent()}' de acessar: {final_url}"
            )

        delay = await self._robots.crawl_delay(final_url)
        await self._limiter.wait(final_url, delay)

        response = await self._client.get(url, params=params)

        if response.status_code in (401, 403):
            raise NotAuthorizedError(
                f"O servidor recusou o acesso automatizado (HTTP {response.status_code}) em {final_url}. "
                "Essa é uma negativa explícita do operador do site: o acesso precisa ser liberado "
                "por via comercial (API oficial ou programa de parceiros), não por meios técnicos."
            )

        return response
