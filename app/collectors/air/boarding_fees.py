"""Taxa de embarque doméstica por aeroporto (estimativa).

O provedor de preços ao vivo devolve o preço total do bilhete, com taxas. A
régua da ANAC mede só a tarifa, sem taxa de embarque. Para comparar, separamos
a taxa estimada do aeroporto de origem.

Os tetos são definidos por contrato de concessão e reajustados ao longo do ano;
em 2026 os valores domésticos publicados ficam entre ~R$ 33 e ~R$ 55. Na falta
de uma fonte única e confiável por aeroporto, usamos o valor padrão de
`settings.DEFAULT_DOMESTIC_BOARDING_FEE_BRL`. Preencha BOARDING_FEES_BRL com o
valor da tabela tarifária da concessionária quando quiser mais precisão.
"""

from typing import Dict, Optional

from app.config import settings

# IATA -> taxa de embarque doméstica em R$. Vazio de propósito: só entra valor conferido
# na tabela da concessionária ou da ANAC.
BOARDING_FEES_BRL: Dict[str, float] = {}


def boarding_fee_brl(origin_iata: Optional[str]) -> float:
    """Taxa de embarque doméstica estimada para o aeroporto de origem."""
    code = (origin_iata or "").strip().upper()
    return float(BOARDING_FEES_BRL.get(code, settings.DEFAULT_DOMESTIC_BOARDING_FEE_BRL))
