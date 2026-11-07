"""Integração com os Dados Abertos da ANTT (Trivio Core).

Diferente dos coletores de cotação, esta é uma fonte **oficial e pública**: o
Portal de Dados Abertos da ANTT (https://dados.antt.gov.br) publica os arquivos
sob licença Creative Commons Attribution.

O acesso é feito pelos caminhos que o `robots.txt` do portal autoriza — a página
do conjunto e as URLs de download. A API CKAN em `/api/` é proibida por lá, e por
isso não é usada (ver `portal.py`).

O que ela entrega e o que não entrega:

- ✅ Tarifas históricas REAIS (Monitriip: bilhetes efetivamente comercializados no
  transporte rodoviário interestadual e internacional, CSV mensal desde 2019).
- ✅ Cadastro de linhas, horários e empresas autorizadas.
- ❌ Disponibilidade e preço de compra em tempo real — isso é inventário comercial
  e exige contrato com um distribuidor.

Uso no Trivio: alimentar o Termômetro de Preço com percentis reais por corredor,
em vez de depender de um histórico que ainda não existe no banco.
"""

from app.collectors.antt.portal import (
    ANTTOpenDataClient,
    ANTTResource,
    MONITRIIP_TICKETS_DATASET,
    parse_resource_links,
)
from app.collectors.antt.monitriip import (
    MONITRIIP_COLUMN_ALIASES,
    MonitriipSchema,
    detect_monitriip_schema,
    read_csv_header,
)
from app.collectors.antt.monthly import (
    RouteMonthlyAggregate,
    TARIFA_NORMAL,
    aggregate_monthly_fares,
    check_date_consistency,
)

__all__ = [
    "ANTTOpenDataClient",
    "ANTTResource",
    "MONITRIIP_TICKETS_DATASET",
    "parse_resource_links",
    "MONITRIIP_COLUMN_ALIASES",
    "MonitriipSchema",
    "detect_monitriip_schema",
    "read_csv_header",
    "RouteMonthlyAggregate",
    "TARIFA_NORMAL",
    "aggregate_monthly_fares",
    "check_date_consistency",
]
