"""Taxonomia de Classes de Serviço Rodoviário (Trivio Core).

Normaliza os nomes comerciais usados por operadoras, agregadores e pela própria
ANTT para o vocabulário interno do Trivio:

    CONVENCIONAL | EXECUTIVO | SEMI_LEITO | LEITO | LEITO_CAMA

Compartilhado entre os coletores rodoviários (ClickBus, ANTT) para que o
Termômetro de Preço compare tarifas da mesma categoria de conforto.
"""

import unicodedata
from typing import Dict

# Quanto mais específico o termo, maior a prioridade (ver ordenação em normalize_bus_service_class)
SERVICE_CLASS_MAPPING: Dict[str, str] = {
    "convencional": "CONVENCIONAL",
    "convencional com ar": "CONVENCIONAL",
    "convencional sem sanitario": "CONVENCIONAL",
    "convencional com sanitario": "CONVENCIONAL",
    "comum": "CONVENCIONAL",
    "executivo": "EXECUTIVO",
    "semi leito": "SEMI_LEITO",
    "semi-leito": "SEMI_LEITO",
    "semileito": "SEMI_LEITO",
    "leito": "LEITO",
    "leito turismo": "LEITO",
    "leito com ar": "LEITO",
    "leito cama": "LEITO_CAMA",
    "leito-cama": "LEITO_CAMA",
    "leitocama": "LEITO_CAMA",
    "cama": "LEITO_CAMA",
    "cama leito": "LEITO_CAMA",
    "prime": "LEITO_CAMA",
}


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def normalize_bus_service_class(raw_service: str) -> str:
    """Normaliza nomes comerciais de classes de ônibus para a taxonomia Trivio.

    Tolera acentuação e variações de grafia ('Semi-Leito', 'SEMILEITO', 'Leito Cama').
    Termos desconhecidos voltam em caixa alta com underscores, para não perder o dado.
    """
    if not raw_service:
        return "CONVENCIONAL"

    cleaned = _strip_accents(str(raw_service).strip().lower())

    # Termos mais longos primeiro: 'leito cama' deve vencer 'leito'
    for key in sorted(SERVICE_CLASS_MAPPING.keys(), key=len, reverse=True):
        if key in cleaned:
            return SERVICE_CLASS_MAPPING[key]

    return cleaned.upper().replace(" ", "_").replace("-", "_")
