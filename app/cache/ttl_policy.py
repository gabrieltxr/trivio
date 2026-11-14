"""Trivio Dynamic Cache TTL Policy.

Calcula o TTL (Time-To-Live) em segundos para cotações de passagens com base na
proximidade da data da viagem.

Regras de Negócio (docs/algoritmos.md, seção 1):
- Viagem em <= 2 dias: TTL = 30 minutos (1.800s)
- Viagem entre 3 e 7 dias: TTL = 2 horas (7.200s)
- Viagem entre 8 e 21 dias: TTL = 6 horas (21.600s)
- Viagem em > 21 dias: TTL = 24 horas (86.400s)
- Viagem no passado: TTL = 0 (expirado imediatamente)
"""

from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Union


class TTLTier(str, Enum):
    EXPIRED = "EXPIRED"
    IMMINENT = "IMMINENT"    # <= 2 dias
    SHORT = "SHORT"          # 3 a 7 dias
    MEDIUM = "MEDIUM"        # 8 a 21 dias
    LONG = "LONG"            # > 21 dias


# Constantes de TTL em segundos
TTL_IMMINENT_SECONDS: int = 1_800    # 30 minutos
TTL_SHORT_SECONDS: int = 7_200       # 2 horas
TTL_MEDIUM_SECONDS: int = 21_600     # 6 horas
TTL_LONG_SECONDS: int = 86_400       # 24 horas


def _parse_date(target: Union[date, datetime, str]) -> date:
    """Converte date, datetime ou string ISO (YYYY-MM-DD) para date."""
    if isinstance(target, datetime):
        return target.date()
    if isinstance(target, date):
        return target
    if isinstance(target, str):
        target_str = target.strip()
        if "T" in target_str:
            target_str = target_str.split("T")[0]
        elif " " in target_str:
            target_str = target_str.split(" ")[0]
        return date.fromisoformat(target_str)
    raise TypeError(f"Tipo inválido para data: {type(target)}. Esperado date, datetime ou str.")


def get_ttl_tier(
    travel_date: Union[date, datetime, str],
    reference_date: Union[date, datetime, str, None] = None,
) -> TTLTier:
    """Retorna o TTLTier correspondente à proximidade da viagem."""
    t_date = _parse_date(travel_date)
    ref_date = _parse_date(reference_date) if reference_date is not None else date.today()

    delta_days = (t_date - ref_date).days

    if delta_days < 0:
        return TTLTier.EXPIRED
    if delta_days <= 2:
        return TTLTier.IMMINENT
    if delta_days <= 7:
        return TTLTier.SHORT
    if delta_days <= 21:
        return TTLTier.MEDIUM
    return TTLTier.LONG


def calculate_ttl(
    travel_date: Union[date, datetime, str],
    reference_date: Union[date, datetime, str, None] = None,
) -> int:
    """Calcula o tempo de vida (TTL) em segundos para o cache da cotação.

    Args:
        travel_date: Data da viagem (date, datetime ou string 'YYYY-MM-DD').
        reference_date: Data de referência para o cálculo (default: data de hoje).

    Returns:
        int: TTL em segundos (0, 1800, 7200, 21600 ou 86400).
    """
    tier = get_ttl_tier(travel_date, reference_date)

    tier_to_seconds = {
        TTLTier.EXPIRED: 0,
        TTLTier.IMMINENT: TTL_IMMINENT_SECONDS,
        TTLTier.SHORT: TTL_SHORT_SECONDS,
        TTLTier.MEDIUM: TTL_MEDIUM_SECONDS,
        TTLTier.LONG: TTL_LONG_SECONDS,
    }

    return tier_to_seconds[tier]


def calculate_expiration_datetime(
    travel_date: Union[date, datetime, str],
    reference_datetime: Union[datetime, None] = None,
) -> datetime:
    """Calcula a data e hora exatas de expiração da cotação.

    Args:
        travel_date: Data da viagem (date, datetime ou string 'YYYY-MM-DD').
        reference_datetime: Momento de coleta da cotação (default: UTC now).

    Returns:
        datetime: Momento de expiração da cotação no cache.
    """
    if reference_datetime is None:
        ref_dt = datetime.now(timezone.utc)
    else:
        ref_dt = reference_datetime

    ref_date = ref_dt.date() if isinstance(ref_dt, datetime) else ref_dt
    ttl_seconds = calculate_ttl(travel_date, reference_date=ref_date)

    return ref_dt + timedelta(seconds=ttl_seconds)
