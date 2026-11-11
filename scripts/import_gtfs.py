"""Importa um feed GTFS (horários e tarifas por linha) para o banco do Trivio.

Fontes conhecidas ficam em app/collectors/gtfs/feeds.py. Exemplo com a ARTESP:

    python scripts/import_gtfs.py --feed artesp --file artesp_gtfs.zip

O período (AAAA-MM) vem de feed_info.txt (feed_version) ou de --period. Reimportar o mesmo
feed e período substitui só as linhas dele; outros feeds e a ANTT não são tocados.
"""

import argparse
import os
import re
import sys
from collections import Counter
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collectors.gtfs.feeds import FEEDS  # noqa: E402
from app.collectors.gtfs.generic import GtfsSource, parse_gtfs  # noqa: E402
from app.database import SessionLocal, ensure_schema  # noqa: E402
from app.models import BusSchedule, GtfsRouteFare  # noqa: E402


def period_from_feed(source: GtfsSource) -> Optional[str]:
    """feed_version '20261109' -> '2026-11'."""
    rows = list(source.rows("feed_info.txt"))
    version = (rows[0].get("feed_version") or "") if rows else ""
    match = re.match(r"^(\d{4})(\d{2})", version)
    return f"{match.group(1)}-{match.group(2)}" if match else None


def import_feed(path: str, feed: str, period: Optional[str] = None) -> dict:
    if feed not in FEEDS:
        raise SystemExit(f"Feed desconhecido: {feed}. Conhecidos: {', '.join(FEEDS)}")
    source = GtfsSource(path)
    try:
        period = period or period_from_feed(source)
        if not period or not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", period):
            raise SystemExit("Não foi possível obter o período: informe --period AAAA-MM.")
        result = parse_gtfs(source, feed=feed, default_state=FEEDS[feed]["default_state"])
    finally:
        source.close()

    ensure_schema()
    with SessionLocal() as session:
        session.query(BusSchedule).filter(
            BusSchedule.feed == feed, BusSchedule.source_period == period
        ).delete(synchronize_session=False)
        session.query(GtfsRouteFare).filter(
            GtfsRouteFare.feed == feed, GtfsRouteFare.source_period == period
        ).delete(synchronize_session=False)
        session.add_all(
            BusSchedule(source_period=period, feed=feed, **record.__dict__) for record in result.schedules
        )
        session.add_all(
            GtfsRouteFare(
                feed=fare.feed, source_period=period, route_id=fare.route_id, route_name=fare.route_name,
                route_key=fare.route_key, price_brl=fare.price_brl, currency=fare.currency,
            )
            for fare in result.fares
        )
        session.commit()

    print(f"Feed {feed} período {period}: {len(result.schedules)} partidas, {len(result.fares)} tarifas.")
    print("Partidas por classe:", dict(Counter(r.service_class for r in result.schedules)))
    print("Estatísticas:", result.stats)
    return {"period": period, "schedules": len(result.schedules), "fares": len(result.fares), **result.stats}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--feed", required=True, help=f"Chave do feed: {', '.join(FEEDS)}")
    parser.add_argument("--file", required=True, help="Arquivo .zip do GTFS ou pasta extraída.")
    parser.add_argument("--period", help="Período AAAA-MM (padrão: feed_info.txt).")
    args = parser.parse_args()
    import_feed(args.file, args.feed, args.period)


if __name__ == "__main__":
    main()
