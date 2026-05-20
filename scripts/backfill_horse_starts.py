"""
Kertaluontoinen backfill: hae Travsport-starttihistoria kaikille hevosille
joilta se puuttuu horse_starts-taulusta.

Käyttö:
    python scripts/backfill_horse_starts.py
    python scripts/backfill_horse_starts.py --limit 1000  # testaa pienellä erällä
    python scripts/backfill_horse_starts.py --rate 1.5    # hidas jos haluaa varmuutta

Turvallinen ajaa scheduler.py:n rinnalla — käyttää samaa välimuistia,
SQLite WAL-mode kestää samanaikaisen kirjoituksen.
Oletusviive 1.2 s/req jotta scheduler + backfill yhdessä pysyvät alle 2 req/s.
"""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")

import argparse
import logging
import sqlite3
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.data.scheduler import _upsert_horse_starts
from src.data.scrapers.travsport import TravsportAPIClient

DB_PATH = "/home/ravi/app-ravi/data/ravit.db"
LOG_PATH = "/home/ravi/app-ravi/data/logs/backfill_horse_starts.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("backfill_horse_starts")


def get_missing_horse_ids(db_path: str) -> list[str]:
    con = sqlite3.connect(db_path)
    rows = con.execute("""
        SELECT DISTINCT r.horse_id
        FROM runners r
        WHERE r.horse_id IS NOT NULL
          AND r.horse_id NOT IN (SELECT DISTINCT horse_id FROM horse_starts)
        ORDER BY r.horse_id
    """).fetchall()
    con.close()
    return [row[0] for row in rows]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Max hevosia (0=kaikki)")
    parser.add_argument("--rate", type=float, default=1.2, help="Viive sekunteina req:ien välillä")
    parser.add_argument("--db", default=DB_PATH)
    args = parser.parse_args()

    missing = get_missing_horse_ids(args.db)
    total = len(missing)
    if args.limit:
        missing = missing[:args.limit]

    logger.info(
        "Backfill käynnistyy: %d hevosta puuttuu, ajetaan %d (viive %.1fs/req)",
        total, len(missing), args.rate,
    )
    logger.info("Arvioitu kesto: %.1f min", len(missing) * args.rate / 60)

    inserted_total = 0
    skipped_total = 0
    errors = 0
    t0 = time.time()

    engine  = create_engine(f"sqlite:///{args.db}", connect_args={"timeout": 30})
    Session_ = sessionmaker(bind=engine)

    with TravsportAPIClient(rate_limit_seconds=args.rate) as travsport:
        with Session_() as session:
            for i, horse_id in enumerate(missing, 1):
                try:
                    stats = _upsert_horse_starts(session, horse_id, travsport)
                    session.commit()
                    inserted_total += stats.get("inserted", 0)
                    skipped_total  += stats.get("skipped", 0)
                except Exception as exc:
                    session.rollback()
                    logger.warning("horse_id=%s epäonnistui: %s", horse_id, exc)
                    errors += 1

                if i % 100 == 0 or i == len(missing):
                    elapsed   = time.time() - t0
                    remaining = (len(missing) - i) * args.rate / 60
                    logger.info(
                        "Progress %d/%d (%.1f%%) | +%d starts | skip=%d | err=%d | "
                        "kestänyt %.0f min | jäljellä ~%.0f min",
                        i, len(missing), 100 * i / len(missing),
                        inserted_total, skipped_total, errors,
                        elapsed / 60, remaining,
                    )

    logger.info(
        "Backfill valmis: %d hevosta, %d starttiä lisätty, %d skipattu, %d virhettä",
        len(missing), inserted_total, skipped_total, errors,
    )


if __name__ == "__main__":
    main()
