"""
Historiallinen backfill: hae ATG:stä ravilähdöt päivä kerrallaan ja tallenna DB:hen.

Käyttö:
  python scripts/backfill_history.py
  python scripts/backfill_history.py --start 2023-01-01 --end 2024-12-31
  python scripts/backfill_history.py --start 2023-01-01 --end 2023-06-30 --db /polku/ravit.db

Suunniteltu ajettavaksi kerran yön yli (esim. Hetznerillä) 2–3 vuoden
historian keräämiseen. Rate limiting (1 req/sek) on jo ATGClient:ssa —
ei tarvita ylimääräistä sleeppiä.

Idempotenssi: jo DB:ssä olevat race_date-arvot ohitetaan kokonaan.
Uudelleenajo on turvallista — jatkaa siitä mihin jäi.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Lisää projektin juurihakemisto sys.path:iin jotta src-importit toimivat
# riippumatta siitä mistä hakemistosta skripti ajetaan.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import create_engine, text

from src.data.scheduler import fetch_daily_races
from src.paths import DB_PATH as _DEFAULT_DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_history")


def _parse_args() -> argparse.Namespace:
    yesterday = date.today() - timedelta(days=1)
    parser = argparse.ArgumentParser(
        description="Hae ATG:stä historiallisia ravilähtöjä päivä kerrallaan."
    )
    parser.add_argument(
        "--start",
        default="2023-01-01",
        metavar="YYYY-MM-DD",
        help="Ensimmäinen haettava päivä (oletus: 2023-01-01)",
    )
    parser.add_argument(
        "--end",
        default=yesterday.isoformat(),
        metavar="YYYY-MM-DD",
        help=f"Viimeinen haettava päivä, inklusiivinen (oletus: eilen, {yesterday})",
    )
    parser.add_argument(
        "--db",
        default=str(_DEFAULT_DB_PATH),
        metavar="PATH",
        help=f"SQLite-tietokannan polku (oletus: {_DEFAULT_DB_PATH})",
    )
    return parser.parse_args()


def _load_existing_dates(db_path: str) -> set[date]:
    """Hae jo DB:ssä olevat race_date-arvot races-taulusta."""
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT race_date FROM races WHERE race_date IS NOT NULL")
        ).fetchall()
    return {date.fromisoformat(str(row[0])) for row in rows}


def _date_range(start: date, end: date) -> list[date]:
    """Palauta lista päivistä start..end inklusiivisesti."""
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def main() -> None:
    args = _parse_args()

    try:
        start_date = date.fromisoformat(args.start)
        end_date = date.fromisoformat(args.end)
    except ValueError as exc:
        logger.error("Virheellinen päivämääräformaatti: %s", exc)
        sys.exit(1)

    if start_date > end_date:
        logger.error(
            "--start (%s) ei voi olla myöhemmin kuin --end (%s)",
            start_date,
            end_date,
        )
        sys.exit(1)

    db_path = args.db
    all_days = _date_range(start_date, end_date)
    total_days = len(all_days)

    logger.info(
        "Backfill käynnistyy: %s → %s (%d päivää), db=%s",
        start_date,
        end_date,
        total_days,
        db_path,
    )

    # Hae jo olemassa olevat päivät — ohitetaan kokonaan
    try:
        existing_dates = _load_existing_dates(db_path)
    except Exception as exc:
        logger.warning(
            "Ei voitu lukea olemassa olevia päiviä DB:stä: %s — "
            "oletetaan tietokanta tyhjäksi.",
            exc,
        )
        existing_dates = set()

    skipped_already = [d for d in all_days if d in existing_dates]
    to_fetch = [d for d in all_days if d not in existing_dates]

    logger.info(
        "Ohitetaan %d päivää (jo DB:ssä). Haetaan %d päivää.",
        len(skipped_already),
        len(to_fetch),
    )

    # Yhteenvetolaskurit
    fetched_days = 0
    skipped_days = len(skipped_already)
    total_races = 0
    error_days: list[tuple[date, str]] = []

    for idx, day in enumerate(all_days, start=1):
        if day in existing_dates:
            # Ohitetaan hiljaisesti — ei printtausta per päivä (saattaa olla tuhansia)
            continue

        try:
            stats = fetch_daily_races(target_date=day, db_path=db_path)
            races_today = stats.get("races_processed", 0)
            errors_today = stats.get("errors", [])

            total_races += races_today
            fetched_days += 1

            # Hae järjestysnumero kaikkien päivien joukossa (ei pelkästään haettavien)
            print(
                f"{day}: {races_today} lähtöä haettu "
                f"({idx}/{total_days} päivää)"
                + (f" [{len(errors_today)} virhe(ttä)]" if errors_today else "")
            )

            if errors_today:
                for err in errors_today:
                    logger.warning("%s: race-tason virhe: %s", day, err)

        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            error_days.append((day, error_msg))
            logger.error("%s: päivän haku epäonnistui: %s", day, error_msg, exc_info=True)
            print(f"{day}: VIRHE — {error_msg}")
            # Jatketaan seuraavaan päivään

    # Yhteenveto
    print()
    print("=" * 60)
    print("BACKFILL VALMIS")
    print("=" * 60)
    print(f"  Haettu:            {fetched_days} päivää")
    print(f"  Lähtöjä lisätty:   {total_races}")
    print(f"  Ohitettu (jo DB):  {skipped_days} päivää")
    print(f"  Virheet:           {len(error_days)} päivää")

    if error_days:
        print()
        print("Virheelliset päivät:")
        for err_day, err_msg in error_days:
            print(f"  {err_day}: {err_msg}")

    print("=" * 60)


if __name__ == "__main__":
    main()
