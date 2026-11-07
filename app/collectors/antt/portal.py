"""Cliente do Portal de Dados Abertos da ANTT (https://dados.antt.gov.br).

POR QUE NÃO USAMOS A API CKAN
------------------------------
O portal roda CKAN e teria uma Action API em `/api/3/action/...`, bem mais
confortável de consumir. Mas o `robots.txt` do site (verificado em 07/11/2026)
traz o padrão do CKAN:

    User-agent: *
    Disallow: /dataset/rate/
    Disallow: /revision/
    Disallow: /dataset/*/history
    Disallow: /api/
    Crawl-Delay: 10

`/api/` é proibido. Então a descoberta é feita pela **página pública do conjunto**
(`/dataset/<slug>`), que é permitida, e os arquivos são baixados pelas URLs de
recurso (`/dataset/<uuid>/resource/<uuid>/download/<arquivo>.csv`), também
permitidas. O `Crawl-Delay: 10` é respeitado automaticamente pelo
`CompliantHTTPClient`, o que deixa a importação lenta de propósito.

Os dados são publicados sob Creative Commons Attribution.
"""

from dataclasses import dataclass
from datetime import date
import logging
import re
from typing import List, Optional
from urllib.parse import urljoin

from app.collectors.compliance import CompliantHTTPClient

logger = logging.getLogger("trivio.collectors.antt")

ANTT_OPEN_DATA_BASE_URL = "https://dados.antt.gov.br"

# Bilhetes de passagem efetivamente comercializados (um CSV por mês, desde 2019)
MONITRIIP_TICKETS_DATASET = "monitriip-bilhetes-de-passagem"

# Links de download de recurso dentro da página do conjunto
_RESOURCE_LINK_RE = re.compile(
    r"""href=["'](?P<url>[^"']*?/dataset/[^"'/]+/resource/[^"'/]+/download/[^"']+?\.csv)["']""",
    re.IGNORECASE,
)

# venda_passagem_09_2024.csv -> (09, 2024)
_MONTHLY_FILE_RE = re.compile(r"(\d{2})[_-](\d{4})\.csv$", re.IGNORECASE)


@dataclass
class ANTTResource:
    """Um arquivo publicado pelo portal."""

    url: str
    name: str = ""
    format: str = "CSV"
    size_bytes: Optional[int] = None

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]

    @property
    def reference_period(self) -> Optional[str]:
        """Período inferido do nome do arquivo (ex: '2024-09')."""
        match = _MONTHLY_FILE_RE.search(self.url) or _MONTHLY_FILE_RE.search(self.name or "")
        if not match:
            return None
        month, year = match.group(1), match.group(2)
        return f"{year}-{month}"

    @property
    def period_date(self) -> Optional[date]:
        period = self.reference_period
        if not period:
            return None
        year, month = period.split("-")
        return date(int(year), int(month), 1)

    def __repr__(self) -> str:
        return f"<ANTTResource({self.filename!r}, período={self.reference_period})>"


def parse_resource_links(html: str, base_url: str = ANTT_OPEN_DATA_BASE_URL) -> List[ANTTResource]:
    """Extrai os links de download de CSV da página de um conjunto de dados."""
    seen = set()
    resources: List[ANTTResource] = []

    for match in _RESOURCE_LINK_RE.finditer(html):
        url = urljoin(base_url, match.group("url"))
        if url in seen:
            continue
        seen.add(url)
        resources.append(ANTTResource(url=url, name=url.rsplit("/", 1)[-1]))

    return resources


class ANTTOpenDataClient:
    """Leitura dos conjuntos de dados abertos da ANTT, pelos caminhos permitidos."""

    def __init__(
        self,
        base_url: str = ANTT_OPEN_DATA_BASE_URL,
        timeout_seconds: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def dataset_url(self, dataset_id: str = MONITRIIP_TICKETS_DATASET) -> str:
        return f"{self.base_url}/dataset/{dataset_id}"

    async def list_resources(
        self, dataset_id: str = MONITRIIP_TICKETS_DATASET
    ) -> List[ANTTResource]:
        """Lista os arquivos do conjunto lendo a página pública (sem tocar em /api/)."""
        url = self.dataset_url(dataset_id)
        async with CompliantHTTPClient(
            timeout=self.timeout_seconds, headers={"Accept": "text/html"}
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text

        resources = parse_resource_links(html, base_url=self.base_url)
        logger.info(f"[antt] {len(resources)} arquivos CSV encontrados em '{dataset_id}'.")
        return resources

    async def list_monthly_ticket_files(
        self,
        months: Optional[int] = None,
        dataset_id: str = MONITRIIP_TICKETS_DATASET,
    ) -> List[ANTTResource]:
        """Arquivos mensais de bilhetes, do período mais recente para o mais antigo."""
        resources = [r for r in await self.list_resources(dataset_id) if r.reference_period]
        resources.sort(key=lambda r: r.period_date or date.min, reverse=True)
        return resources[:months] if months else resources

    async def download_resource(self, resource: ANTTResource, destination: str) -> str:
        """Baixa um arquivo para o caminho informado e devolve o caminho."""
        logger.info(f"[antt] Baixando {resource.filename} -> {destination}")
        async with CompliantHTTPClient(timeout=self.timeout_seconds) as client:
            response = await client.get(resource.url)
            response.raise_for_status()
            with open(destination, "wb") as handle:
                handle.write(response.content)
        return destination
