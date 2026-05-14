"""CLI-ajuri viikoittaiselle feature drift -monitoroinnille.

Ajetaan sunnuntaisin klo 02:00 (crontab Hetznerillä):
  0 2 * * 0 /home/ravi/app-ravi/.venv/bin/python \
            /home/ravi/app-ravi/scripts/run_feature_drift.py \
            >> /home/ravi/app-ravi/data/logs/drift_cron.log 2>&1

Manuaalinen ajo:
  python scripts/run_feature_drift.py
  python scripts/run_feature_drift.py --db /path/to/ravit.db --log-dir /path/to/logs
  python scripts/run_feature_drift.py --week 2026-20   # tietty viikko
"""

import argparse
import logging
import sys
from pathlib import Path

# Varmista että src/ löytyy importtaessa (kun ajetaan suoraan, ei moduulina)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.monitoring.feature_drift import log_feature_distributions, print_drift_report
from src.paths import DB_PATH, LOG_DIR


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Viikoittainen feature drift -monitorointi"
    )
    ap.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"SQLite-tietokannan polku (oletus: {DB_PATH})",
    )
    ap.add_argument(
        "--log-dir",
        type=Path,
        default=LOG_DIR,
        help=f"Lokihakemisto CSV:ille (oletus: {LOG_DIR})",
    )
    ap.add_argument(
        "--week",
        type=str,
        default=None,
        help="ISO-viikko 'YYYY-WW' (oletus: kuluva viikko)",
    )
    ap.add_argument(
        "--lookback-days",
        type=int,
        default=365,
        help="Montako päivää taaksepäin dataa otetaan (oletus: 365)",
    )
    ap.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Näytä DEBUG-tason logit",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stats, anomalies = log_feature_distributions(
        db_path=args.db,
        log_dir=args.log_dir,
        week=args.week,
        lookback_days=args.lookback_days,
    )

    print_drift_report(stats, anomalies, week=args.week or "kuluva")

    # Palauta exit code: 0=ok, 1=varoituksia, 2=kriittisiä
    critical = any(a["severity"] == "CRITICAL" for a in anomalies)
    warnings = any(a["severity"] == "WARNING" for a in anomalies)
    return 2 if critical else (1 if warnings else 0)


if __name__ == "__main__":
    sys.exit(main())
