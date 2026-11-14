"""Limpeza do banco: dados de provedores que saíram do projeto e cache vencido.

Sem `--apply`, só mostra o que seria apagado. Com `--apply`, apaga, atualiza o esquema
(`ensure_schema`) e as estatísticas do otimizador (`ANALYZE`).

    python scripts/cleanup_legacy_data.py            # simulação
    python scripts/cleanup_legacy_data.py --apply    # apaga

O que sai:
- provider_mappings de provedores que o código não usa mais (Amadeus, Kiwi, DeÔnibus, Buser...);
- fare_quotes vencidas (expires_at no passado): ninguém as lê, nem o /quotes/cached por padrão.
  O histórico do Termômetro lê fare_quotes por `collected_at` dos últimos 90 dias, então
  `--keep-history` preserva as vencidas desse período que vieram de provedor ainda ativo;
- fare_quotes que nunca deveriam ter sido gravadas (conexões montadas pelo Trivio e
  estimativas: hoje o pipeline já as descarta antes de gravar);
- com `--prune-queries`, provider_queries vencidas e anteriores a AIR_REQUESTS_REMAINING_AS_OF:
  não servem de cache e não entram mais na conta do saldo do Ignav (que só soma as cobradas
  desde a leitura). Opcional porque essas linhas também são o registro de auditoria do gasto.

Faça backup do arquivo do banco antes de rodar com `--apply`.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import or_  # noqa: E402

from app.analyzers.price_thermometer import HISTORY_LOOKBACK_DAYS  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import SessionLocal, analyze_tables, ensure_schema  # noqa: E402
from app.models import FareQuote, ProviderMapping, ProviderQuery  # noqa: E402
from app.services.air_quotes import _parse_as_of  # noqa: E402  (mesma leitura do saldo)
from app.services.quote_pipeline import NON_PERSISTABLE_PROVIDERS  # noqa: E402

# Provedores com código no projeto: ignav (aéreo ao vivo) e clickbus (desligada até a API B2B).
ACTIVE_MAPPING_PROVIDERS = {"ignav", "clickbus"}
ACTIVE_QUOTE_PROVIDERS = {"ignav", "clickbus"}


def _now_naive_utc() -> datetime:
    # As colunas DateTime são gravadas sem fuso (UTC) no SQLite.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def build_queries(db, keep_history: bool, prune_queries: bool = True):
    now = _now_naive_utc()
    mappings = db.query(ProviderMapping).filter(ProviderMapping.provider_name.notin_(ACTIVE_MAPPING_PROVIDERS))

    expired = FareQuote.expires_at < now
    if keep_history:
        recent = FareQuote.collected_at >= now - timedelta(days=HISTORY_LOOKBACK_DAYS)
        expired = expired & ~(recent & FareQuote.provider_source.in_(ACTIVE_QUOTE_PROVIDERS))
    quotes = db.query(FareQuote).filter(or_(
        expired,
        FareQuote.provider_source.in_(NON_PERSISTABLE_PROVIDERS),
        FareQuote.provider_source.like("gtfs_%"),
    ))

    as_of = _parse_as_of(settings.AIR_REQUESTS_REMAINING_AS_OF).replace(tzinfo=None)
    queries = db.query(ProviderQuery).filter(
        ProviderQuery.requested_at < as_of,
        or_(ProviderQuery.expires_at.is_(None), ProviderQuery.expires_at < now),
    )
    selected = {"provider_mappings": mappings, "fare_quotes": quotes}
    if prune_queries:
        selected["provider_queries"] = queries
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Apaga de fato (sem ele, só simula).")
    parser.add_argument(
        "--keep-history", action="store_true",
        help=f"Mantém cotações vencidas dos últimos {HISTORY_LOOKBACK_DAYS} dias de provedor ativo.",
    )
    parser.add_argument(
        "--prune-queries", action="store_true",
        help="Também apaga provider_queries vencidas de antes da leitura do saldo (registro de auditoria).",
    )
    args = parser.parse_args()

    ensure_schema()
    with SessionLocal() as db:
        queries = build_queries(db, args.keep_history, args.prune_queries)
        for table, query in queries.items():
            print(f"{table}: {query.count()} linha(s) a apagar")
        mappings = queries["provider_mappings"].with_entities(ProviderMapping.provider_name).all()
        if mappings:
            by_provider = {}
            for (name,) in mappings:
                by_provider[name] = by_provider.get(name, 0) + 1
            print("  mapeamentos por provedor:", by_provider)

        if not args.apply:
            print("Simulação: nada foi apagado. Rode com --apply para apagar.")
            return
        for query in queries.values():
            query.delete(synchronize_session=False)
        db.commit()

    analyze_tables("route_monthly_fares", "fare_quotes", "provider_queries", "bus_schedules", "provider_mappings")
    print("Apagado. Esquema e estatísticas do otimizador atualizados.")


if __name__ == "__main__":
    main()
