"""Travronden-piirteiden takaisinpäivitys runners-tauluun.

Lukee data/travronden_pilot.csv:n (tai muun --csv-polun) ja päivittää
tr_*-piirteet olemassa olevaan runners-tauluun horse_id + race_date -avaimella.

Lisäksi merkitsee is_v_race=True kaikille riveille joille tr_*-data löytyy
(ne ovat olleet Travrondenin V-pelistä).

Käyttö:
    # Perusajo — päivitä kaikki
    python scripts/travronden_backfill.py

    # Vain tietyltä aikaväliltä (A/B-testiä varten)
    python scripts/travronden_backfill.py --from-date 2026-04-27

    # Kuiva-ajo (ei kirjoita DB:hen)
    python scripts/travronden_backfill.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.paths import DATA_DIR, DB_PATH
from src.features.travronden_features import TRAVRONDEN_FEATURE_COLS

logger = logging.getLogger(__name__)

_TR_COLS = TRAVRONDEN_FEATURE_COLS  # 11 sarakkeen lista
_UPDATE_COLS = ["is_v_race"] + _TR_COLS


def backfill(
    csv_path: Path,
    db_path: str,
    from_date: str | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Yhdistä pilot-CSV runners-tauluun.

    Returns:
        dict jossa rows_matched, rows_updated, rows_skipped
    """
    # --- Lue pilot-CSV ---
    df = pd.read_csv(csv_path)
    logger.info("Luettu %d riviä tiedostosta %s", len(df), csv_path)

    if from_date:
        df = df[df["race_date"] >= from_date]
        logger.info("Rajaus %s jälkeen: %d riviä", from_date, len(df))

    if len(df) == 0:
        logger.warning("Ei dataa — tarkista --from-date ja CSV-polku")
        return {"rows_matched": 0, "rows_updated": 0, "rows_skipped": 0}

    # horse_id merkkijonona
    df["horse_id"] = df["horse_id"].astype(str)

    # --- Hae race_id:t DB:stä race_date-avaimella ---
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        races_df = pd.read_sql("SELECT race_id, race_date FROM races", conn)

    races_df["race_date"] = races_df["race_date"].astype(str)

    # --- Hae runners DB:stä ---
    with engine.connect() as conn:
        runners_df = pd.read_sql(
            "SELECT runner_id, horse_id, race_id FROM runners", conn
        )
    runners_df["horse_id"] = runners_df["horse_id"].astype(str)

    # Yhdistä races_df runners_df:ään race_date:n saamiseksi
    runners_with_date = runners_df.merge(races_df, on="race_id", how="left")

    # Yhdistä pilot-data runner_id:n löytämiseksi
    # Avain: horse_id + race_date
    merged = df.merge(
        runners_with_date[["runner_id", "horse_id", "race_date"]],
        on=["horse_id", "race_date"],
        how="inner",
    )

    logger.info(
        "Matchattu: %d / %d pilot-riveä löysi runner_id:n",
        len(merged), len(df),
    )

    if len(merged) == 0:
        logger.warning(
            "Ei matcheja — tarkista horse_id ja race_date "
            "(treenidata: %s – %s, pilot: %s – %s)",
            runners_with_date["race_date"].min(),
            runners_with_date["race_date"].max(),
            df["race_date"].min(),
            df["race_date"].max(),
        )
        return {"rows_matched": 0, "rows_updated": 0, "rows_skipped": 0}

    # Deduplikoi: jos sama runner_id esiintyy useammin (pitäisi olla harvinaista)
    before = len(merged)
    merged = merged.drop_duplicates(subset=["runner_id"], keep="last")
    if len(merged) < before:
        logger.warning("Deduplikoitu: %d → %d riviä", before, len(merged))

    if dry_run:
        logger.info("DRY-RUN: ei kirjoiteta DB:hen. Matchattu %d riviä.", len(merged))
        return {"rows_matched": len(merged), "rows_updated": 0, "rows_skipped": 0}

    # --- Päivitä DB ---
    updated = 0
    skipped = 0

    with engine.begin() as conn:
        for _, row in merged.iterrows():
            runner_id = row["runner_id"]
            set_parts = ["is_v_race = :is_v_race"]
            params: dict = {"runner_id": runner_id, "is_v_race": True}

            for col in _TR_COLS:
                val = row.get(col)
                # pandas NaN → None
                if pd.isna(val) if not isinstance(val, str) else val == "":
                    val = None
                set_parts.append(f"{col} = :{col}")
                params[col] = val

            sql = f"UPDATE runners SET {', '.join(set_parts)} WHERE runner_id = :runner_id"
            result = conn.execute(text(sql), params)
            if result.rowcount > 0:
                updated += 1
            else:
                skipped += 1

    logger.info(
        "Valmis: %d matchattu, %d päivitetty, %d ohitettu",
        len(merged), updated, skipped,
    )
    return {"rows_matched": len(merged), "rows_updated": updated, "rows_skipped": skipped}


def print_coverage_after(db_path: str, from_date: str | None = None) -> None:
    """Tulosta kattavuustilastot päivityksen jälkeen."""
    engine = create_engine(f"sqlite:///{db_path}")
    date_filter = f"AND rc.race_date >= '{from_date}'" if from_date else ""
    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN tr_start_interval_group IS NOT NULL THEN 1 ELSE 0 END) AS with_tr,
                SUM(CASE WHEN is_v_race = 1 THEN 1 ELSE 0 END) AS v_race_count
            FROM runners r
            JOIN races rc USING (race_id)
            WHERE 1=1 {date_filter}
        """))
        row = result.fetchone()

    total, with_tr, v_race = row
    pct = 100.0 * with_tr / total if total > 0 else 0.0
    period = f"(alkaen {from_date})" if from_date else "(koko DB)"
    print(f"\nKattavuus {period}:")
    print(f"  Runnereita yhteensä:   {total}")
    print(f"  TR-data saatavilla:    {with_tr} ({pct:.1f} %)")
    print(f"  is_v_race=True:        {v_race}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Travronden backfill runners-tauluun")
    ap.add_argument(
        "--csv", type=Path,
        default=DATA_DIR / "travronden_pilot.csv",
        help="Pilot-CSV:n polku"
    )
    ap.add_argument("--db", type=str, default=str(DB_PATH))
    ap.add_argument("--from-date", type=str, default=None,
                    help="Päivämäärärajaus (YYYY-MM-DD), esim. 2026-04-27")
    ap.add_argument("--dry-run", action="store_true",
                    help="Ei kirjoiteta DB:hen — vain tilastot")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.csv.exists():
        logger.error("CSV ei löydy: %s", args.csv)
        return 1

    stats = backfill(args.csv, args.db, args.from_date, args.dry_run)
    logger.info("Tilastot: %s", stats)

    if not args.dry_run:
        print_coverage_after(args.db, args.from_date)

    return 0


if __name__ == "__main__":
    sys.exit(main())
