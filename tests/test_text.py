"""strip_accents substituiu quatro cópias da mesma regra: a saída tem de continuar idêntica.

As implementações antigas ficam copiadas aqui de propósito. `normalize_place` gera a
`route_key` das tarifas da ANTT já importadas; se a regra mudar, este teste quebra antes
das chaves.
"""

import re
import unicodedata

import pytest

from app.collectors.antt.monitriip import build_route_key, normalize_column_name, normalize_place
from app.collectors.road.taxonomy import normalize_bus_service_class
from app.geo.entity_resolver import normalize_text
from app.text import strip_accents

DECOMPOSED_SAO = "São Paulo"   # 'ã' já decomposto (a + til combinante)

SAMPLES = [
    "",
    "São Paulo",
    DECOMPOSED_SAO,
    "Florianópolis",
    "Brasília",
    "Goiânia",
    "Maceió",
    "Vitória da Conquista",
    "Foz do Iguaçu",
    "Embu-Guaçu",
    "Santa Bárbara d'Oeste",
    "Olho-d'Água das Flores",
    "Herval d’Oeste",            # apóstrofo tipográfico
    "Itaú de Minas",
    "Pôrto Alegre",
    "ÁÉÍÓÚ ÂÊÔ ÃÕ À Ç Ü",
    "  Rio   de\tJaneiro \n",
    "São José",              # espaço não separável
    "Nª Sª da Glória",           # ª (forma de compatibilidade)
    "1º de Maio",
    "ﬁnal",                      # ligadura
    "Semi-Leito",
    "Leito Cama",
    "Executivo Diurno",
    "Município de Origem",
    "Preço Médio (R$)",
    "data/viagem",
]


def legacy_strip(text):
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def legacy_normalize_place(name):
    stripped = unicodedata.normalize("NFKD", str(name))
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return " ".join(stripped.upper().split())


def legacy_normalize_column_name(name):
    stripped = unicodedata.normalize("NFKD", str(name))
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    cleaned = stripped.strip().lower()
    for ch in (" ", "-", ".", "/"):
        cleaned = cleaned.replace(ch, "_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def legacy_normalize_text(text):
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in normalized if not unicodedata.combining(c))
    cleaned = re.sub(r"[^\w\s]", " ", stripped).lower()
    return re.sub(r"\s+", " ", cleaned).strip()


@pytest.mark.parametrize("sample", SAMPLES)
def test_outputs_are_identical_to_the_old_copies(sample):
    assert strip_accents(sample) == legacy_strip(sample)   # era o _strip_accents da taxonomia
    assert normalize_place(sample) == legacy_normalize_place(sample)
    assert normalize_column_name(sample) == legacy_normalize_column_name(sample)
    assert normalize_text(sample) == legacy_normalize_text(sample)


def test_route_keys_stay_fixed():
    """Valores gravados em route_monthly_fares: mudar qualquer um exige reimportar a ANTT."""
    assert build_route_key("São Paulo", "SP", "Florianópolis", "SC") == "SAO PAULO-SP|FLORIANOPOLIS-SC"
    assert build_route_key(DECOMPOSED_SAO, "SP", "Brasília", "DF") == "SAO PAULO-SP|BRASILIA-DF"
    assert build_route_key("Santa Bárbara d'Oeste", "SP", "Embu-Guaçu", "SP") == (
        "SANTA BARBARA D'OESTE-SP|EMBU-GUACU-SP"
    )


def test_strip_accents_keeps_case_and_punctuation():
    assert strip_accents("Santa Bárbara d'Oeste-SP") == "Santa Barbara d'Oeste-SP"
    assert strip_accents("Nª Sª") == "Na Sa"


def test_service_class_still_tolerates_accents():
    assert normalize_bus_service_class("Semi-Léito") == normalize_bus_service_class("Semi-Leito") == "SEMI_LEITO"
