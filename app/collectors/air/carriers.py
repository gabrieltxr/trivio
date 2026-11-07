"""Nomes canônicos das companhias aéreas, pelo código IATA de 2 letras.

O provedor pode devolver o nome localizado ou com variações ('LATAM', 'Latam
Airlines Brasil'). Usar o nome canônico mantém estável a assinatura de
deduplicação (que inclui o nome da companhia) entre provedores e buscas.
"""

from typing import Optional

CARRIER_NAMES = {
    "LA": "LATAM Airlines",
    "JJ": "LATAM Airlines",
    "G3": "Gol Linhas Aéreas",
    "AD": "Azul Linhas Aéreas",
    "2Z": "Voepass Linhas Aéreas",
    "TP": "TAP Air Portugal",
    "AA": "American Airlines",
    "DL": "Delta Air Lines",
    "UA": "United Airlines",
    "AF": "Air France",
    "KL": "KLM",
    "CM": "Copa Airlines",
    "AV": "Avianca",
    "AR": "Aerolíneas Argentinas",
}


def canonical_carrier_name(code: Optional[str], fallback: Optional[str] = None) -> str:
    """Nome canônico pelo código; senão o nome informado; senão 'Companhia XX'."""
    clean = (code or "").strip().upper()
    if clean in CARRIER_NAMES:
        return CARRIER_NAMES[clean]
    if fallback and fallback.strip():
        return fallback.strip()
    return f"Companhia {clean}" if clean else "Companhia desconhecida"
