"""Referência histórica rodoviária (ANTT): faixa de preço de bilhetes vendidos por rota e classe.

Não é uma oferta. A série da ANTT traz médias mensais por rota, classe e mês de viagem,
sem empresa, horário, data exata nem link de compra. Por isso estes valores são mostrados
como referência ("costuma custar entre X e Y") e nunca entram no ranking de cotações.
"""

from statistics import median
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.analyzers.price_thermometer import MIN_OFFICIAL_MONTHS
from app.analyzers.road_fare_projection import project_route_fare, projection_to_dict
from app.models import ModalType

# Classes de serviço da ANTT, na ordem em que aparecem na tela (do mais barato ao mais confortável).
REFERENCE_CLASSES = ("CONVENCIONAL", "EXECUTIVO", "SEMI_LEITO", "LEITO")


def _percentile(sorted_values: List[float], q: float) -> float:
    """Percentil por interpolação linear sobre valores já ordenados."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def build_road_references(
    db: Session,
    origin_city: str,
    origin_state: Optional[str],
    destination_city: str,
    destination_state: Optional[str],
    travel_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Faixa histórica por classe para a rota, com a média do mês da viagem quando existir."""
    from app.analyzers.price_thermometer import fetch_route_monthly_means

    travel_month = travel_date[:7] if travel_date else None
    references: List[Dict[str, Any]] = []

    for service_class in REFERENCE_CLASSES:
        series = fetch_route_monthly_means(
            db=db,
            origin_city=origin_city,
            origin_state=origin_state,
            destination_city=destination_city,
            destination_state=destination_state,
            service_class=service_class,
            modal=ModalType.ROAD,
        )
        if len(series) < MIN_OFFICIAL_MONTHS:
            continue

        means = sorted(mean for _, mean, _ in series)
        tickets = sum(sample for _, _, sample in series)
        month_mean = next((round(mean, 2) for month, mean, _ in series if month == travel_month), None)

        # Projeção do analisador para o mês da viagem: mesma conta do endpoint /road/projection.
        projection = None
        if travel_month:
            projection = projection_to_dict(project_route_fare(
                db, origin_city, origin_state, destination_city, destination_state,
                service_class, travel_month,
            ))

        references.append({
            "service_class": service_class,
            "reference_period": f"{series[0][0]} a {series[-1][0]}",
            "months": len(series),
            "tickets": int(tickets),
            "p25_brl": round(_percentile(means, 0.25), 2),
            "median_brl": round(median(means), 2),
            "p75_brl": round(_percentile(means, 0.75), 2),
            "travel_month_mean_brl": month_mean,
            "travel_month": travel_month,
            "projection": projection,
        })

    return references
