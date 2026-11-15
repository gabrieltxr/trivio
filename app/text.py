"""Utilitários de texto compartilhados."""

import unicodedata


def strip_accents(text: str) -> str:
    """Remove acentos e outras marcas diacríticas ('São Paulo' -> 'Sao Paulo').

    Usa NFKD, que também decompõe formas de compatibilidade ('ª' -> 'a', 'ﬁ' -> 'fi').
    Não muda caixa nem pontuação: cada normalizador aplica as suas regras depois.

    ATENÇÃO: `normalize_place` (app/collectors/antt/monitriip.py) usa esta função para
    gerar a `route_key` das tarifas da ANTT já importadas. Mudar a regra aqui muda essas
    chaves e deixa as linhas existentes inacessíveis até reimportar tudo.
    tests/test_text.py fixa a saída.
    """
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))
