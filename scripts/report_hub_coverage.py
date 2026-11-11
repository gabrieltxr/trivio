"""Relatório de cobertura por cidade: quais cidades têm rota com dados até os hubs atuais.

Cruza todas as fontes de rodoviário que estão no banco:
- ANTT tarifas (série mensal de vendas, route_monthly_fares, modal ROAD);
- ANTT horários (bus_schedules com feed NULL);
- GTFS (bus_schedules com feed preenchido, por feed).

Uso (grava CSV e imprime o resumo):

    python scripts/report_hub_coverage.py --out relatorio_hubs.csv

Cidade = candidata a hub: aparece em alguma rota e ainda não é hub. Uma rota conta quando
o par (cidade, hub atual) tem dados em alguma fonte. Aeroporto não é avaliado aqui porque
os voos são consultados por aeroporto no Ignav, não por série.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from typing import Dict, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collectors.antt.monitriip import build_route_key, normalize_place  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import BusSchedule, Hub, RouteMonthlyFare, ModalType  # noqa: E402

City = Tuple[str, str]   # (CIDADE normalizada, UF)


def _split_key(route_key: str) -> Tuple[City, City]:
    left, right = route_key.split("|")
    lc, ls = left.rsplit("-", 1)
    rc, rs = right.rsplit("-", 1)
    return (lc, ls), (rc, rs)


def build_report(db) -> Dict[City, dict]:
    hubs: Set[City] = {(normalize_place(h.name), h.state.upper()) for h in db.query(Hub)}

    # rotas por fonte: rota (par de cidades) -> departures (ou None para tarifa sem horário)
    sources: Dict[str, Dict[Tuple[City, City], int]] = {
        "antt_tarifa": {},
        "antt_horario": {},
    }
    for route_key, count in db.query(RouteMonthlyFare.route_key, RouteMonthlyFare.sample_size).filter(
        RouteMonthlyFare.modal == ModalType.ROAD
    ):
        pair = _split_key(route_key)
        sources["antt_tarifa"][pair] = sources["antt_tarifa"].get(pair, 0) + int(count or 0)

    latest_antt = db.query(BusSchedule.source_period).filter(BusSchedule.feed.is_(None)).order_by(
        BusSchedule.source_period.desc()).first()
    gtfs_feeds = {f for (f,) in db.query(BusSchedule.feed).filter(BusSchedule.feed.isnot(None)).distinct()}
    for route_key, source_period, feed in db.query(
        BusSchedule.route_key, BusSchedule.source_period, BusSchedule.feed
    ):
        if feed is None:
            if not latest_antt or source_period != latest_antt[0]:
                continue
            name = "antt_horario"
        elif feed in gtfs_feeds:
            name = f"gtfs_{feed}"
        else:
            continue
        bucket = sources.setdefault(name, {})
        pair = _split_key(route_key)
        bucket[pair] = bucket.get(pair, 0) + 1

    cities: Dict[City, dict] = defaultdict(lambda: {"sources": set(), "hubs": set(), "routes": 0,
                                                     "departures": defaultdict(int)})
    for name, routes in sources.items():
        for (a, b), value in routes.items():
            for city, other in ((a, b), (b, a)):
                if city in hubs or other not in hubs:
                    continue
                info = cities[city]
                info["sources"].add(name)
                info["hubs"].add(other)
                info["routes"] += 1
                if name.startswith(("antt_horario", "gtfs_")):
                    info["departures"][name] += value

    return cities


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="Caminho do CSV de saída.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        cities = build_report(db)
        hub_names = {f"{normalize_place(h.name)}-{h.state.upper()}" for h in db.query(Hub)}
    finally:
        db.close()

    rows = []
    for (name, uf), info in cities.items():
        rows.append({
            "cidade": name,
            "uf": uf,
            "hubs_atuais_cobertos": ", ".join(sorted(f"{h[0]}-{h[1]}" for h in info["hubs"])),
            "qtd_hubs_cobertos": len(info["hubs"]),
            "qtd_rotas": info["routes"],
            "fontes": ", ".join(sorted(info["sources"])),
            "partidas_antt_horario": info["departures"].get("antt_horario", 0),
            "partidas_gtfs": sum(v for k, v in info["departures"].items() if k.startswith("gtfs_")),
            "aeroporto_cadastrado": "não",
        })
    rows.sort(key=lambda r: (-r["qtd_hubs_cobertos"], -r["qtd_rotas"], r["cidade"]))

    with open(args.out, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["cidade"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Hubs atuais: {len(hub_names)} | cidades candidatas com alguma rota: {len(rows)}")
    print("Top 15 por hubs cobertos e rotas:")
    for r in rows[:15]:
        print(f"  {r['cidade']}-{r['uf']:2}  hubs={r['qtd_hubs_cobertos']}  rotas={r['qtd_rotas']}  "
              f"antt_hor={r['partidas_antt_horario']}  gtfs={r['partidas_gtfs']}  -> {r['hubs_atuais_cobertos']}")
    print(f"CSV: {args.out}")


if __name__ == "__main__":
    main()
