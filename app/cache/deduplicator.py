"""Módulo de Deduplicação de Cotações e Agrupamento Code-Share (Trivio Core).

No ecossistema intermodal (rodoviário e aéreo), a mesma viagem física é frequentemente
comercializada por múltiplos canais/brokers (ex: Viação Guanabara vendida na ClickBus,
DeÔnibus e site próprio; ou voo Gol vendido via Amadeus, Decolar e Kiwi).

A chave de assinatura SHA-256 (modal, operadora, origem, destino, horário de partida e classe)
permite agrupar essas ofertas idênticas, selecionar a opção de menor custo efetivo real
e anexar aos metadados o histórico de todos os vendedores com seus respectivos preços,
destacando a economia gerada para o viajante.
"""

from copy import copy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, TypeVar

from app.collectors.base import generate_dedup_signature

T = TypeVar("T")


@dataclass
class SellerOffer:
    """Representa a oferta de um canal/broker específico para uma viagem."""

    provider_source: str
    total_effective_price: float
    base_price: float
    taxes_price: float = 0.0
    service_class: str = ""
    deep_link: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "provider_source": self.provider_source,
            "total_effective_price": self.total_effective_price,
            "base_price": self.base_price,
            "taxes_price": self.taxes_price,
            "service_class": self.service_class,
        }
        if self.deep_link:
            result["deep_link"] = self.deep_link
        if self.extra:
            result["extra"] = self.extra
        return result


def _get_val(obj: Any, attr: str, default: Any = None) -> Any:
    """Extrai valor de dataclass, Pydantic, SQLAlchemy model ou dict."""
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _get_signature(quote: Any) -> str:
    """Obtém ou calcula a dedup_signature de uma cotação."""
    sig = _get_val(quote, "dedup_signature")
    if sig:
        return str(sig)

    # Se não tiver o atributo pronto, calcula via generate_dedup_signature
    modal = _get_val(quote, "modal", "")
    carrier = _get_val(quote, "carrier_name", "")
    origin = _get_val(quote, "origin_external_id", "") or _get_val(quote, "origin_terminal_id", "")
    dest = _get_val(quote, "destination_external_id", "") or _get_val(quote, "destination_terminal_id", "")
    dep_time = _get_val(quote, "departure_time", "")
    service = _get_val(quote, "service_class", "CONVENCIONAL")

    return generate_dedup_signature(
        modal=str(modal),
        carrier_name=str(carrier),
        origin_id=str(origin),
        destination_id=str(dest),
        departure_time=dep_time,
        service_class=str(service),
    )


def _get_effective_price(quote: Any) -> float:
    """Retorna o custo efetivo total da cotação."""
    price = _get_val(quote, "total_effective_price")
    if price is not None:
        return float(price)

    # Fallback calculando a soma
    base = float(_get_val(quote, "base_price", 0.0) or 0.0)
    taxes = float(_get_val(quote, "taxes_price", 0.0) or 0.0)
    baggage = float(_get_val(quote, "baggage_fee_estimate", 0.0) or 0.0)
    transfer = float(_get_val(quote, "transfer_cost_estimate", 0.0) or 0.0)
    return round(base + taxes + baggage + transfer, 2)


def _attach_metadata(quote: Any, metadata_update: Dict[str, Any]) -> None:
    """Atualiza o dicionário route_metadata do objeto quote com segurança."""
    if isinstance(quote, dict):
        if "route_metadata" not in quote or not isinstance(quote["route_metadata"], dict):
            quote["route_metadata"] = {}
        quote["route_metadata"].update(metadata_update)
    elif hasattr(quote, "route_metadata"):
        current_meta = getattr(quote, "route_metadata")
        if current_meta is None or not isinstance(current_meta, dict):
            setattr(quote, "route_metadata", {})
            current_meta = getattr(quote, "route_metadata")
        current_meta.update(metadata_update)


class QuoteDeduplicator:
    """Agrupador e deduplicador de cotações intermodais."""

    @staticmethod
    def group_by_signature(quotes: Sequence[T]) -> Dict[str, List[T]]:
        """Agrupa uma coleção de cotações por sua assinatura única de viagem."""
        groups: Dict[str, List[T]] = {}
        for quote in quotes:
            sig = _get_signature(quote)
            if sig not in groups:
                groups[sig] = []
            groups[sig].append(quote)
        return groups

    @classmethod
    def deduplicate(
        cls,
        quotes: Sequence[T],
        attach_sellers_info: bool = True,
        sort_by_price: bool = False,
    ) -> List[T]:
        """Filtra cotações duplicadas mantendo apenas a opção mais barata de compra.

        Args:
            quotes: Lista de cotações (StandardizedQuote, FareQuote ou dicts).
            attach_sellers_info: Se True, enriquece a cotação vencedora com a
                lista de todos os canais que comercializam a viagem e a economia calculada.
            sort_by_price: Se True, ordena a lista final pelo preço total efetivo.

        Returns:
            List[T]: Lista contendo apenas cotações únicas (com melhor preço selecionado).
        """
        if not quotes:
            return []

        grouped = cls.group_by_signature(quotes)
        deduplicated: List[T] = []

        for signature, group in grouped.items():
            # Ordena pelo custo efetivo total crescente (menor preço primeiro)
            sorted_group = sorted(group, key=_get_effective_price)
            cheapest = sorted_group[0]

            if attach_sellers_info:
                sellers_data = []
                for q in sorted_group:
                    provider = str(_get_val(q, "provider_source", "unknown"))
                    eff_price = _get_effective_price(q)
                    base_p = float(_get_val(q, "base_price", 0.0) or 0.0)
                    taxes_p = float(_get_val(q, "taxes_price", 0.0) or 0.0)
                    serv = str(_get_val(q, "service_class", ""))

                    deep_link = None
                    meta = _get_val(q, "route_metadata", {})
                    if isinstance(meta, dict):
                        deep_link = meta.get("deep_link") or meta.get("booking_url")

                    sellers_data.append(
                        {
                            "provider_source": provider,
                            "total_effective_price": eff_price,
                            "base_price": base_p,
                            "taxes_price": taxes_p,
                            "service_class": serv,
                            "deep_link": deep_link,
                        }
                    )

                cheapest_price = _get_effective_price(cheapest)
                highest_price = _get_effective_price(sorted_group[-1])
                savings_brl = round(highest_price - cheapest_price, 2)
                is_code_share = len(sorted_group) > 1

                comparison_metadata = {
                    "is_code_share": is_code_share,
                    "sellers_count": len(sorted_group),
                    "cheapest_provider": str(_get_val(cheapest, "provider_source", "")),
                    "cheapest_price": cheapest_price,
                    "highest_price": highest_price,
                    "savings_brl": savings_brl,
                    "available_sellers": sellers_data,
                }

                _attach_metadata(cheapest, comparison_metadata)

            deduplicated.append(cheapest)

        if sort_by_price:
            deduplicated.sort(key=_get_effective_price)

        return deduplicated


def deduplicate_quotes(
    quotes: Sequence[T],
    attach_sellers_info: bool = True,
    sort_by_price: bool = False,
) -> List[T]:
    """Função utilitária para execução direta de deduplicação."""
    return QuoteDeduplicator.deduplicate(
        quotes=quotes,
        attach_sellers_info=attach_sellers_info,
        sort_by_price=sort_by_price,
    )


# Alias de conveniência
Deduplicator = QuoteDeduplicator

