"""Pipeline de Processamento, Enriquecimento e Persistência de Cotações (Trivio Core).

Responsável por:
1. Resolução Canônica: Converte IDs externos de brokers (ex: 'sao-paulo-tiete-sp' e 'sao-paulo-tiete')
   para o Terminal oficial correspondente via ProviderMapping.
2. Injeção de Transfer Urbano Real: Adiciona o custo e tempo de deslocamento do centro
   urbano até os terminais de origem e destino na cotação porta a porta.
3. Unificação de Deduplicação: Atribui canonical_origin_id e canonical_destination_id
   ao StandardizedQuote, garantindo que o SHA-256 agrupe ofertas de múltiplos brokers.
4. Smart Cache TTL: Calcula a data e hora exatas de expiração (expires_at) de cada cotação.
5. Persistência e Upsert: Salva no banco de dados (FareQuote) selecionando sempre a melhor
   oferta e registrando o histórico de canais concorrentes (code-share).
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analyzers.route_evaluator import enrich_quote_with_evaluation
from app.cache.deduplicator import deduplicate_quotes
from app.cache.ttl_policy import calculate_expiration_datetime
from app.collectors.base import StandardizedQuote
from app.models import FareQuote, ModalType, ProviderMapping, Terminal

logger = logging.getLogger("trivio.services.quote_pipeline")


def find_terminal_by_external_id(
    session: Session,
    provider_name: str,
    external_id: str,
) -> Optional[Terminal]:
    """Busca o Terminal canônico através de seu mapeamento com o provedor."""
    if not external_id:
        return None

    # 1. Busca direta no ProviderMapping
    stmt = (
        select(Terminal)
        .join(ProviderMapping, ProviderMapping.terminal_id == Terminal.id)
        .where(
            ProviderMapping.provider_name == provider_name.strip().lower(),
            ProviderMapping.external_id == external_id.strip(),
        )
    )
    terminal = session.execute(stmt).scalars().first()
    if terminal:
        return terminal

    # 2. Fallback: Busca pelo código oficial (IATA ou sigla)
    clean_code = external_id.strip().upper()
    stmt_code = select(Terminal).where(Terminal.official_code == clean_code)
    terminal = session.execute(stmt_code).scalars().first()
    if terminal:
        return terminal

    return None


NON_PERSISTABLE_PROVIDERS = {"trivio_simulation", "trivio_interliner"}


def is_persistable_quote(quote: StandardizedQuote) -> bool:
    """Indica se a cotação representa um preço real de mercado que pode ir para o banco."""
    if quote.provider_source in NON_PERSISTABLE_PROVIDERS:
        return False
    meta = quote.route_metadata or {}
    return not (meta.get("is_simulated") or meta.get("is_virtual_interline"))


class QuotePipeline:
    """Orquestrador do pipeline de dados para cotações coletadas."""

    @classmethod
    def enrich_quote(
        cls,
        quote: StandardizedQuote,
        session: Session,
    ) -> Tuple[StandardizedQuote, Optional[Terminal], Optional[Terminal]]:
        """Resolve terminais canônicos, injeta custos reais de transfer e tempo porta a porta."""
        origin_term = find_terminal_by_external_id(
            session=session,
            provider_name=quote.provider_source,
            external_id=quote.origin_external_id,
        )
        dest_term = find_terminal_by_external_id(
            session=session,
            provider_name=quote.provider_source,
            external_id=quote.destination_external_id,
        )

        if origin_term:
            quote.canonical_origin_id = origin_term.official_code or str(origin_term.id)
            quote.route_metadata["origin_terminal_id"] = origin_term.id
            quote.route_metadata["origin_terminal_name"] = origin_term.name
            # Município: chave de busca nas estatísticas oficiais (ANTT trabalha por cidade)
            if origin_term.hub:
                quote.route_metadata["origin_city"] = origin_term.hub.name
                quote.route_metadata["origin_state"] = origin_term.hub.state

        if dest_term:
            quote.canonical_destination_id = dest_term.official_code or str(dest_term.id)
            quote.route_metadata["destination_terminal_id"] = dest_term.id
            quote.route_metadata["destination_terminal_name"] = dest_term.name
            if dest_term.hub:
                quote.route_metadata["destination_city"] = dest_term.hub.name
                quote.route_metadata["destination_state"] = dest_term.hub.state

        # Injeta custos e tempos de transfer urbano
        origin_transfer_cost = origin_term.estimated_transfer_cost_brl if origin_term else 0.0
        dest_transfer_cost = dest_term.estimated_transfer_cost_brl if dest_term else 0.0
        quote.transfer_cost_estimate = round(origin_transfer_cost + dest_transfer_cost, 2)

        origin_transfer_time = origin_term.estimated_transfer_minutes if origin_term else 0
        dest_transfer_time = dest_term.estimated_transfer_minutes if dest_term else 0
        checkin_buffer = 90 if quote.modal == "AIR" else 20

        quote.door_to_door_duration_minutes = (
            quote.duration_minutes + origin_transfer_time + dest_transfer_time + checkin_buffer
        )
        # Decomposição usada pelo interliner para somar corretamente o porta a porta de rotas híbridas
        quote.route_metadata["origin_transfer_minutes"] = origin_transfer_time
        quote.route_metadata["destination_transfer_minutes"] = dest_transfer_time
        quote.route_metadata["checkin_buffer_minutes"] = checkin_buffer

        # Enriquece com avaliação qualitativa de prós, contras e contratempos
        enrich_quote_with_evaluation(quote)

        return quote, origin_term, dest_term

    @classmethod
    def convert_to_fare_quote(
        cls,
        quote: StandardizedQuote,
        session: Session,
        origin_term: Optional[Terminal] = None,
        dest_term: Optional[Terminal] = None,
    ) -> Optional[FareQuote]:
        """Converte StandardizedQuote enriquecida em modelo ORM FareQuote com expiração de cache."""
        if origin_term is None or dest_term is None:
            _, origin_term, dest_term = cls.enrich_quote(quote, session)

        if not origin_term or not dest_term:
            logger.warning(
                f"Não foi possível converter cotação para FareQuote: terminais não encontrados "
                f"({quote.origin_external_id} -> {quote.destination_external_id})"
            )
            return None

        # ModalType
        try:
            m_type = ModalType[quote.modal.upper()]
        except KeyError:
            m_type = ModalType.ROAD if "ROAD" in quote.modal.upper() else ModalType.AIR

        # Smart Cache TTL Expiration
        travel_date = quote.departure_time.date()
        expires_at = calculate_expiration_datetime(travel_date=travel_date)

        fare_quote = FareQuote(
            dedup_signature=quote.dedup_signature,
            origin_terminal_id=origin_term.id,
            destination_terminal_id=dest_term.id,
            modal=m_type,
            provider_source=quote.provider_source,
            carrier_name=quote.carrier_name,
            service_class=quote.service_class,
            departure_time=quote.departure_time,
            arrival_time=quote.arrival_time,
            duration_minutes=quote.duration_minutes,
            door_to_door_duration_minutes=quote.door_to_door_duration_minutes,
            base_price=quote.base_price,
            taxes_price=quote.taxes_price,
            baggage_fee_estimate=quote.baggage_fee_estimate,
            transfer_cost_estimate=quote.transfer_cost_estimate,
            total_effective_price=quote.total_effective_price,
            trivio_score=quote.trivio_score,
            route_metadata=quote.route_metadata,
            expires_at=expires_at,
        )

        return fare_quote

    @classmethod
    def process_and_persist_quotes(
        cls,
        quotes: Sequence[StandardizedQuote],
        session: Session,
    ) -> List[FareQuote]:
        """Executa enriquecimento canônico, deduplicação em memória e upsert transacional no banco."""
        # Cotações simuladas e rotas sintetizadas pelo interliner não são preços reais
        # de mercado: persisti-las contaminaria o histórico do Termômetro de Preço.
        quotes = [q for q in quotes if is_persistable_quote(q)]
        if not quotes:
            return []

        # 1. Enriquecimento de Terminais Canônicos e Custos de Transfer
        enriched_quotes: List[StandardizedQuote] = []
        terminal_cache: Dict[str, Tuple[Optional[Terminal], Optional[Terminal]]] = {}

        for q in quotes:
            enriched, orig_t, dest_t = cls.enrich_quote(q, session)
            enriched_quotes.append(enriched)
            terminal_cache[enriched.dedup_signature] = (orig_t, dest_t)

        # 2. Deduplicação em memória (agrupando por assinatura canônica única)
        deduped_quotes = deduplicate_quotes(enriched_quotes, attach_sellers_info=True)

        # 3. Upsert no Banco de Dados
        saved_records: List[FareQuote] = []

        for quote in deduped_quotes:
            orig_t, dest_t = terminal_cache.get(quote.dedup_signature, (None, None))
            if not orig_t or not dest_t:
                continue

            # Verifica se já existe cotação com a mesma dedup_signature no banco
            existing_stmt = select(FareQuote).where(FareQuote.dedup_signature == quote.dedup_signature)
            existing_quote = session.execute(existing_stmt).scalars().first()

            if existing_quote:
                # Se a nova cotação for mais barata, atualiza a melhor oferta
                if quote.total_effective_price < existing_quote.total_effective_price:
                    # Atualiza todos os componentes para manter total = soma das partes
                    # e o Trivio Score coerente com o preço e o tempo porta a porta gravados.
                    existing_quote.base_price = quote.base_price
                    existing_quote.taxes_price = quote.taxes_price
                    existing_quote.baggage_fee_estimate = quote.baggage_fee_estimate
                    existing_quote.transfer_cost_estimate = quote.transfer_cost_estimate
                    existing_quote.total_effective_price = quote.total_effective_price
                    existing_quote.door_to_door_duration_minutes = quote.door_to_door_duration_minutes
                    existing_quote.trivio_score = quote.trivio_score
                    existing_quote.provider_source = quote.provider_source
                    existing_quote.route_metadata = quote.route_metadata
                    existing_quote.collected_at = datetime.now(timezone.utc)
                else:
                    # Se a existente já for a mais barata, apenas sincroniza a lista de vendedores
                    existing_meta = dict(existing_quote.route_metadata or {})
                    existing_meta["available_sellers"] = quote.route_metadata.get("available_sellers", [])
                    existing_meta["sellers_count"] = quote.route_metadata.get("sellers_count", 1)
                    existing_quote.route_metadata = existing_meta

                saved_records.append(existing_quote)
            else:
                fq = cls.convert_to_fare_quote(
                    quote=quote,
                    session=session,
                    origin_term=orig_t,
                    dest_term=dest_t,
                )
                if fq:
                    session.add(fq)
                    saved_records.append(fq)

        session.commit()
        return saved_records


def process_and_persist_quotes(
    quotes: Sequence[StandardizedQuote],
    session: Session,
) -> List[FareQuote]:
    """Função utilitária para processamento e persistência direta de cotações."""
    return QuotePipeline.process_and_persist_quotes(quotes, session)
