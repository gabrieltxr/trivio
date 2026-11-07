"""Testes da camada de conformidade de coleta (robots.txt, identificação e recusas)."""

from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.collectors.compliance import (
    CompliantHTTPClient,
    NotAuthorizedError,
    PolitenessLimiter,
    RobotsDisallowedError,
    RobotsPolicy,
    build_user_agent,
    compliant_headers,
    parse_retry_after,
)
from app.config import settings


# robots.txt real da ClickBus (consultado em 07/11/2026), reduzido ao grupo '*'
CLICKBUS_ROBOTS = """User-agent: *
Allow: /
Disallow: /checkout/
Disallow: /cliente/
Disallow: /login
Disallow: /cadastro
Disallow: /admin/
Disallow: /*?filtro=
Disallow: /*?page=
Disallow: /*?departureDate=

Sitemap: https://www.clickbus.com.br/sitemap/indice.xml
"""


class _Resp:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("erro", request=None, response=None)


def _mock_robots(monkeypatch, status_code=200, text=CLICKBUS_ROBOTS, boom=False):
    async def mock_get(self, url, *args, **kwargs):
        if boom:
            raise httpx.ConnectError("sem rede")
        return _Resp(status_code, text)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)


# ==============================================================================
# 1. Identificação Honesta
# ==============================================================================

def test_user_agent_identifies_the_bot_and_never_impersonates_a_browser():
    ua = build_user_agent()
    assert ua.startswith(settings.BOT_NAME)
    for token in ("Mozilla", "Chrome", "Safari", "AppleWebKit", "Gecko"):
        assert token not in ua, f"O User-Agent não pode imitar navegador ({token})"


def test_user_agent_includes_contact_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "BOT_INFO_URL", "https://trivio.com.br/bot")
    monkeypatch.setattr(settings, "BOT_CONTACT_EMAIL", "bot@trivio.com.br")

    ua = build_user_agent()
    assert "+https://trivio.com.br/bot" in ua
    assert "bot@trivio.com.br" in ua
    assert compliant_headers()["From"] == "bot@trivio.com.br"


def test_headers_omit_contact_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "BOT_CONTACT_EMAIL", "")
    assert "From" not in compliant_headers()


# ==============================================================================
# 2. Interpretação do robots.txt
# ==============================================================================

@pytest.mark.asyncio
async def test_clickbus_rules_are_applied_with_longest_match(monkeypatch):
    """A regra mais específica vence: 'Allow: /' no topo não anula os Disallow.

    Este é exatamente o caso em que `urllib.robotparser` da stdlib erra, por usar
    a primeira regra que casa — liberando até /login.
    """
    _mock_robots(monkeypatch)
    policy = RobotsPolicy(user_agent="TrivioBot/1.0")
    base = "https://www.clickbus.com.br"

    assert await policy.can_fetch(f"{base}/onibus/curitiba-pr/porto-alegre-rs") is True
    assert await policy.can_fetch(f"{base}/sitemap/indice.xml") is True

    # A página de resultados de busca é explicitamente proibida
    assert await policy.can_fetch(f"{base}/onibus/curitiba-pr/porto-alegre-rs?departureDate=2026-11-14") is False
    assert await policy.can_fetch(f"{base}/login") is False
    assert await policy.can_fetch(f"{base}/checkout/sucesso") is False


@pytest.mark.asyncio
async def test_sitemaps_are_discovered(monkeypatch):
    _mock_robots(monkeypatch)
    policy = RobotsPolicy(user_agent="TrivioBot/1.0")
    assert await policy.sitemaps("https://www.clickbus.com.br/") == [
        "https://www.clickbus.com.br/sitemap/indice.xml"
    ]


@pytest.mark.asyncio
async def test_missing_robots_allows_crawling(monkeypatch):
    _mock_robots(monkeypatch, status_code=404, text="")
    policy = RobotsPolicy(user_agent="TrivioBot/1.0")
    assert await policy.can_fetch("https://exemplo.com.br/qualquer") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 500, 503])
async def test_unreadable_robots_fails_closed(monkeypatch, status_code):
    """robots.txt restrito ou indisponível: assume-se proibição, não permissão."""
    _mock_robots(monkeypatch, status_code=status_code, text="")
    policy = RobotsPolicy(user_agent="TrivioBot/1.0")
    assert await policy.can_fetch("https://exemplo.com.br/qualquer") is False


@pytest.mark.asyncio
async def test_network_failure_on_robots_fails_closed(monkeypatch):
    _mock_robots(monkeypatch, boom=True)
    policy = RobotsPolicy(user_agent="TrivioBot/1.0")
    assert await policy.can_fetch("https://exemplo.com.br/qualquer") is False


@pytest.mark.asyncio
async def test_robots_is_fetched_once_per_host(monkeypatch):
    calls = []

    async def counting_get(self, url, *args, **kwargs):
        calls.append(url)
        return _Resp(200, CLICKBUS_ROBOTS)

    monkeypatch.setattr(httpx.AsyncClient, "get", counting_get)
    policy = RobotsPolicy(user_agent="TrivioBot/1.0")

    for _ in range(4):
        await policy.can_fetch("https://www.clickbus.com.br/onibus/a/b")

    assert len(calls) == 1


# ==============================================================================
# 3. Retry-After
# ==============================================================================

def test_parse_retry_after_seconds():
    assert parse_retry_after("120") == 120.0


def test_parse_retry_after_http_date():
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    parsed = parse_retry_after(format_datetime(future))
    assert parsed is not None and 30 <= parsed <= 90


def test_parse_retry_after_invalid():
    assert parse_retry_after(None) is None
    assert parse_retry_after("amanhã") is None


# ==============================================================================
# 4. Cliente em Conformidade
# ==============================================================================

@pytest.mark.asyncio
async def test_client_refuses_disallowed_url_without_requesting(monkeypatch):
    """URL proibida pelo robots.txt: a requisição nem chega a sair."""
    _mock_robots(monkeypatch)
    emitted = []

    async def tracking_get(self, url, *args, **kwargs):
        if url.endswith("/robots.txt"):
            return _Resp(200, CLICKBUS_ROBOTS)
        emitted.append(url)
        return _Resp(200, "{}")

    monkeypatch.setattr(httpx.AsyncClient, "get", tracking_get)

    async with CompliantHTTPClient() as client:
        with pytest.raises(RobotsDisallowedError):
            await client.get(
                "https://www.clickbus.com.br/onibus/curitiba-pr/porto-alegre-rs",
                params={"departureDate": "2026-11-14"},
            )

    assert emitted == []


@pytest.mark.asyncio
async def test_client_turns_403_into_final_denial(monkeypatch):
    async def mock_get(self, url, *args, **kwargs):
        if url.endswith("/robots.txt"):
            return _Resp(200, CLICKBUS_ROBOTS)
        return _Resp(403, "Forbidden")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    async with CompliantHTTPClient() as client:
        with pytest.raises(NotAuthorizedError, match="parceiros"):
            await client.get("https://www.clickbus.com.br/api/v4/trips")


@pytest.mark.asyncio
async def test_politeness_limiter_spaces_requests():
    import time

    limiter = PolitenessLimiter()
    url = "https://exemplo.com.br/a"

    start = time.monotonic()
    await limiter.wait(url, 0.0)
    await limiter.wait(url, 0.25)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.2


# ==============================================================================
# 5. Proibição explícita x robots.txt ilegível
# ==============================================================================

# robots.txt real do portal da ANTT (consultado em 07/11/2026)
ANTT_ROBOTS = """User-agent: *
Disallow: /dataset/rate/
Disallow: /revision/
Disallow: /dataset/*/history
Disallow: /api/
Crawl-Delay: 10
"""

ANTT_DATASET_PAGE = "https://dados.antt.gov.br/dataset/monitriip-bilhetes-de-passagem"
ANTT_DOWNLOAD = (
    "https://dados.antt.gov.br/dataset/989414bc/resource/f3eb0796/download/venda_passagem_09_2024.csv"
)
ANTT_API = "https://dados.antt.gov.br/api/3/action/package_show"


@pytest.mark.asyncio
async def test_antt_allows_dataset_page_and_downloads_but_not_api(monkeypatch):
    """Descoberta e download são permitidos; a API CKAN não é."""
    _mock_robots(monkeypatch, text=ANTT_ROBOTS)
    policy = RobotsPolicy(user_agent="TrivioBot/1.0")

    assert await policy.can_fetch(ANTT_DATASET_PAGE) is True
    assert await policy.can_fetch(ANTT_DOWNLOAD) is True
    assert await policy.can_fetch(ANTT_API) is False
    assert await policy.crawl_delay(ANTT_DATASET_PAGE) == 10.0


@pytest.mark.asyncio
async def test_evaluate_distinguishes_disallowed_from_unreadable(monkeypatch):
    _mock_robots(monkeypatch, text=ANTT_ROBOTS)
    policy = RobotsPolicy(user_agent="TrivioBot/1.0")

    assert await policy.evaluate(ANTT_DATASET_PAGE) == (True, "allowed")
    assert await policy.evaluate(ANTT_API) == (False, "disallowed")


@pytest.mark.asyncio
async def test_evaluate_reports_unreadable_when_robots_fails(monkeypatch):
    _mock_robots(monkeypatch, boom=True)
    policy = RobotsPolicy(user_agent="TrivioBot/1.0")

    assert await policy.evaluate("https://exemplo.com.br/x") == (False, "unreadable")


@pytest.mark.asyncio
async def test_client_error_names_the_real_cause(monkeypatch):
    """Instabilidade de rede não pode ser diagnosticada como proibição do site."""
    from app.collectors.compliance import RobotsUnavailableError

    async def mock_get(self, url, *args, **kwargs):
        if url.endswith("/robots.txt"):
            return _Resp(200, ANTT_ROBOTS)
        return _Resp(200, "{}")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    async with CompliantHTTPClient() as client:
        with pytest.raises(RobotsDisallowedError, match="proíbe o agente") as exc:
            await client.get(ANTT_API)
        assert not isinstance(exc.value, RobotsUnavailableError)

    _mock_robots(monkeypatch, boom=True)
    async with CompliantHTTPClient() as client:
        with pytest.raises(RobotsUnavailableError, match="Não foi possível ler o robots.txt"):
            await client.get("https://exemplo.com.br/x")
