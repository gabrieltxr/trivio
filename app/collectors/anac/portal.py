"""Cliente dos microdados de tarifas aéreas domésticas da ANAC.

ONDE ESTÃO OS ARQUIVOS
----------------------
A página institucional (gov.br/anac/.../microdados-de-tarifas-aereas-comercializadas)
fica atrás de um desafio anti-bot do gov.br e não é usada: o Trivio não contorna
esse tipo de barreira. Os arquivos em si são servidos por uma listagem de
diretórios pública do servidor de estatísticas da ANAC:

    https://sas.anac.gov.br/sas/tarifadomestica/            -> um diretório por ano
    https://sas.anac.gov.br/sas/tarifadomestica/2026/       -> 202601.csv, 202602.csv...

O host não publica robots.txt (404, verificado em 07/11/2026), o que pela
convenção libera o acesso. Mesmo assim tudo passa pelo `CompliantHTTPClient`,
que se identifica e respeita o intervalo mínimo entre requisições.

Cada arquivo mensal tem ~25 MB e sai com cerca de um mês de defasagem
(setembro/2026 foi publicado em 21/10/2026). Alguns nomes fogem do padrão
(ex: '202605 (1).csv') e alguns meses também têm uma versão .xlsx, ignorada.
"""

from dataclasses import dataclass
from datetime import date
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import unquote, urljoin

from app.collectors.compliance import CompliantHTTPClient

logger = logging.getLogger("trivio.collectors.anac")

ANAC_SAS_BASE_URL = "https://sas.anac.gov.br"
DOMESTIC_FARES_PATH = "/sas/tarifadomestica/"

_YEAR_DIR_RE = re.compile(r"""href=["'](?P<url>[^"']*/tarifadomestica/(?P<year>\d{4})/?)["']""", re.IGNORECASE)
_FILE_RE = re.compile(r"""href=["'](?P<url>[^"']+\.csv)["']""", re.IGNORECASE)
# 202609.csv, 202605 (1).csv -> (2026, 09)
_PERIOD_RE = re.compile(r"(?P<year>(19|20)\d{2})(?P<month>0[1-9]|1[0-2])")


@dataclass
class ANACResource:
    """Um arquivo mensal de tarifas publicado pela ANAC."""

    url: str

    @property
    def filename(self) -> str:
        return unquote(self.url.rsplit("/", 1)[-1])

    @property
    def reference_period(self) -> Optional[str]:
        """Mês de referência (venda) inferido do nome do arquivo, ex: '2026-09'."""
        match = _PERIOD_RE.search(self.filename)
        if not match:
            return None
        return f"{match.group('year')}-{match.group('month')}"

    @property
    def period_date(self) -> Optional[date]:
        period = self.reference_period
        if not period:
            return None
        year, month = period.split("-")
        return date(int(year), int(month), 1)

    @property
    def is_canonical_name(self) -> bool:
        """'202609.csv' é o nome padrão; '202605 (1).csv' é uma variação."""
        return bool(re.fullmatch(r"\d{6}\.csv", self.filename, re.IGNORECASE))

    def __repr__(self) -> str:
        return f"<ANACResource({self.filename!r}, período={self.reference_period})>"


def parse_year_directories(html: str, base_url: str = ANAC_SAS_BASE_URL) -> List[int]:
    """Anos disponíveis na listagem raiz, do mais recente para o mais antigo."""
    years = {int(match.group("year")) for match in _YEAR_DIR_RE.finditer(html)}
    return sorted(years, reverse=True)


def parse_monthly_files(html: str, base_url: str = ANAC_SAS_BASE_URL) -> List[ANACResource]:
    """Arquivos CSV mensais de uma listagem de ano, um por período.

    Quando um mesmo período aparece em mais de um arquivo, fica o de nome padrão.
    """
    by_period: Dict[str, ANACResource] = {}
    for match in _FILE_RE.finditer(html):
        resource = ANACResource(url=urljoin(base_url, match.group("url")))
        period = resource.reference_period
        if not period:
            continue
        current = by_period.get(period)
        if current is None or (resource.is_canonical_name and not current.is_canonical_name):
            by_period[period] = resource

    return sorted(by_period.values(), key=lambda r: r.period_date or date.min, reverse=True)


class ANACOpenDataClient:
    """Descoberta e download dos microdados de tarifas domésticas da ANAC."""

    def __init__(self, base_url: str = ANAC_SAS_BASE_URL, timeout_seconds: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def index_url(self) -> str:
        return f"{self.base_url}{DOMESTIC_FARES_PATH}"

    async def _get_html(self, client: CompliantHTTPClient, url: str) -> str:
        response = await client.get(url)
        response.raise_for_status()
        return response.text

    async def list_monthly_files(self, months: Optional[int] = None) -> List[ANACResource]:
        """Arquivos mensais do mais recente para o mais antigo.

        Com `months`, para de listar anos assim que já tem arquivos suficientes,
        evitando uma requisição por ano desde 2002.
        """
        resources: List[ANACResource] = []
        async with CompliantHTTPClient(timeout=60.0, headers={"Accept": "text/html"}) as client:
            years = parse_year_directories(await self._get_html(client, self.index_url), self.base_url)
            for year in years:
                year_url = f"{self.index_url}{year}/"
                resources.extend(parse_monthly_files(await self._get_html(client, year_url), self.base_url))
                if months and len(resources) >= months:
                    break

        resources.sort(key=lambda r: r.period_date or date.min, reverse=True)
        logger.info(f"[anac] {len(resources)} arquivos mensais encontrados.")
        return resources[:months] if months else resources

    async def download_resource(self, resource: ANACResource, destination: str) -> str:
        """Baixa um arquivo mensal para o caminho informado e devolve o caminho."""
        logger.info(f"[anac] Baixando {resource.filename} -> {destination}")
        async with CompliantHTTPClient(timeout=self.timeout_seconds) as client:
            response = await client.get(resource.url)
            response.raise_for_status()
            with open(destination, "wb") as handle:
                handle.write(response.content)
        return destination
