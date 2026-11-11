"""Importa a grade de horários das linhas interestaduais de ônibus (ANTT, dados abertos).

Fonte: Portal de Dados Abertos da ANTT (https://dados.antt.gov.br), conjunto
`gerenciamento-de-autorizacoes`, recurso mensal "Horários - SIGMA" (CSV, licença CC-BY).
O `robots.txt` do portal permite os caminhos `/dataset/.../download/` e pede 10 s entre
requisições; a API (`/api/`) é proibida. Baixe o CSV pela página do recurso, por exemplo:

    https://dados.antt.gov.br/dataset/a8729428-f382-430c-abe5-6e5f85aa9a03/resource/
        560fba7e-9139-41b1-807a-177bf642bb1e/download/04-2026_horarios_sigma.csv

Uso:

    python scripts/import_antt_schedules.py --file caminho/04-2026_horarios_sigma.csv
    python scripts/import_antt_schedules.py --file arquivo.csv --period 2026-04

Reimportar o mesmo período substitui as linhas dele. A busca usa só o período mais recente.
"""

import argparse
import os
import re
import sys
from collections import Counter
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collectors.antt.schedules import read_schedule_csv  # noqa: E402
from app.database import SessionLocal, ensure_schema  # noqa: E402
from app.models import BusSchedule  # noqa: E402


def period_from_filename(path: str) -> Optional[str]:
    """'04-2026_horarios_sigma.csv' -> '2026-04'."""
    match = re.search(r"(\d{2})-(\d{4})", os.path.basename(path))
    if not match:
        return None
    month, year = match.groups()
    return f"{year}-{month}"


def import_schedules(path: str, period: str) -> int:
    records, skipped = read_schedule_csv(path)
    ensure_schema()
    with SessionLocal() as session:
        session.query(BusSchedule).filter(
            BusSchedule.source_period == period, BusSchedule.feed.is_(None)
        ).delete(synchronize_session=False)   # só as linhas da ANTT (feed NULL)
        session.add_all(BusSchedule(source_period=period, **record.__dict__) for record in records)
        session.commit()

    by_class = Counter(r.service_class for r in records)
    print(f"Período {period}: {len(records)} partidas em {len({r.route_key for r in records})} sentidos de rota.")
    print("Por classe:", dict(by_class))
    print("Linhas descartadas:", {k: v for k, v in skipped.items() if v})
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, metavar="CSV", help="CSV 'Horários - SIGMA' já baixado.")
    parser.add_argument("--period", help="Período do arquivo (AAAA-MM), se não der para inferir do nome.")
    args = parser.parse_args()

    period = args.period or period_from_filename(args.file)
    if not period or not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", period):
        parser.error("Informe --period AAAA-MM (não foi possível inferir do nome do arquivo).")
    import_schedules(args.file, period)


if __name__ == "__main__":
    main()
