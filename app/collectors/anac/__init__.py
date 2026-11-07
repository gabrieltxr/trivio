"""Integração com os microdados de tarifas aéreas da ANAC (Trivio Core).

Fonte **oficial e pública**: as companhias aéreas brasileiras registram todo mês
na ANAC as tarifas das passagens domésticas vendidas ao público adulto
(Resolução ANAC nº 437/2017), e a agência publica os microdados desde 2002.

O que ela entrega e o que não entrega:

- ✅ Tarifas REAIS vendidas, por companhia, aeroporto de origem e destino, com a
  quantidade de assentos em cada valor. Cobre LATAM, Azul, GOL e as regionais.
- ✅ Série mensal desde 2002, publicada com cerca de um mês de defasagem.
- ❌ Taxa de embarque e serviços opcionais (não fazem parte da tarifa registrada).
- ❌ Horário, voo, cabine e disponibilidade: não serve para montar cotação, só
  para dizer se um preço é caro ou barato para a rota.

Uso no Trivio: régua do Termômetro de Preço para o modal aéreo.
"""

from app.collectors.anac.airfares import (
    ANAC_ALL_CLASSES,
    ANAC_COLUMN_ALIASES,
    ANAC_SOURCE,
    ANACSchema,
    AirRouteMonthlyAggregate,
    aggregate_air_monthly_fares,
    detect_anac_schema,
)
from app.collectors.anac.airports import IATA_TO_ICAO, ICAO_TO_IATA, to_iata, to_icao
from app.collectors.anac.portal import (
    ANACOpenDataClient,
    ANACResource,
    parse_monthly_files,
    parse_year_directories,
)

__all__ = [
    "ANAC_ALL_CLASSES",
    "ANAC_COLUMN_ALIASES",
    "ANAC_SOURCE",
    "ANACSchema",
    "AirRouteMonthlyAggregate",
    "aggregate_air_monthly_fares",
    "detect_anac_schema",
    "IATA_TO_ICAO",
    "ICAO_TO_IATA",
    "to_iata",
    "to_icao",
    "ANACOpenDataClient",
    "ANACResource",
    "parse_monthly_files",
    "parse_year_directories",
]
