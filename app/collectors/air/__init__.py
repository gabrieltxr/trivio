"""Módulo de coletores aéreos do Trivio.

Preço ao vivo: Ignav Flight Prices API (`ignav.py`), sempre consumida através do
`app.services.air_quotes.AirQuoteService`, que cuida de cache e orçamento.

O coletor Amadeus Self-Service foi removido: a Amadeus desativou o portal e as
chaves Self-Service em 17/07/2026.
"""

from app.collectors.air.carriers import CARRIER_NAMES, canonical_carrier_name
from app.collectors.air.ignav import IgnavClient, IgnavCollector, parse_ignav_one_way

__all__ = [
    "CARRIER_NAMES",
    "canonical_carrier_name",
    "IgnavClient",
    "IgnavCollector",
    "parse_ignav_one_way",
]
