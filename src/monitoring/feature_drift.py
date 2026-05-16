"""Viikoittainen feature-jakaumien lokitus ja drift-hälytykset.

Miksi tämä on tärkeä:
  K1-vuoto (2026-05-10) olisi havaittu välittömästi jos tämä olisi ollut
  käytössä — atg_lifetime_starts:n viikkokeskiarvo olisi siirtynyt +7
  askelma/viikko (jokaiselle lähdölle päivitetty post-race). Sen sijaan
  bugi eli 3 viikkoa huomaamatta.

Aja sunnuntaisin klo 02:00 (crontab Hetznerillä).
Vertaa edelliseen viikkoon, varoita ±2σ-poikkeamista tai NaN-%:n +10pp-noususta.

Käyttö (CLI):
  python -m scripts.run_feature_drift
  python -m scripts.run_feature_drift --db /path/to/ravit.db --log-dir /path/to/logs

Käyttö (Python):
  from src.monitoring.feature_drift import run_weekly_drift_check
  anomalies = run_weekly_drift_check()
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.paths import DB_PATH, LOG_DIR

logger = logging.getLogger(__name__)

# Piirteet joita seurataan — numeerinen osajoukko FEATURE_COLS:ista.
# Kategoriset (distance_category, start_method jne.) seurataan NaN-%-tasolla.
_NUMERIC_FEATURES = [
    # Muotopiirteet
    "form_avg_finish_5",
    "form_win_rate_5",
    "form_top3_rate_5",
    "form_avg_km_time_5",
    "form_best_km_time_5",
    "form_market_avg_5",
    "form_days_since_last",
    "form_avg_finish_5_same_method",
    "form_avg_finish_5_same_dist",
    # ATG-aggregaatit
    "atg_lifetime_win_rate",
    "atg_lifetime_top3_rate",
    "atg_lifetime_starts",     # ← K1-tyyppiset vuodot näkyvät tässä
    "atg_best_km_for_this_setup",
    # Ohjastaja/valmentaja
    "driver_win_rate_365d",
    "driver_starts_365d",
    "driver_top3_rate_365d",
    "trainer_win_rate_365d",
    "trainer_top3_rate_365d",
    # Lähtöasetelma
    "inside_post",
    "back_row",
    "handicap_meters",
    "track_horse_starts",
    "track_horse_win_rate",
    # Lähdön luokka
    "race_min_earnings",
    "race_max_earnings",
    # Kengät ja sulky
    "shoes_changed_front",
    "shoes_changed_back",
    "sulky_changed",
    # Johdetut
    "barfota_law_active",
    "horse_age",
    # Ratarakenne
    "track_length_total",
    "track_home_stretch_m",
    "track_open_stretch",
    "track_angled_wing",
    "track_width_1",
    "track_width_2",
    "track_dosage",
]

_CATEGORICAL_FEATURES = [
    "distance_category",
    "start_method",
    "race_age_group",
    "track_condition",
    "sulky_type",
]

# Hälytysrajat
_ALERT_SIGMA = 2.0      # σ-poikkeama historiallisesta mean-vaihtelusta
_ALERT_NAN_PP = 10.0    # NaN-%-nousu prosenttiyksikköinä vs. edellinen viikko
_MIN_WEEKS_FOR_SIGMA = 3  # minimiviikkoja σ-laskentaan (alle tämän: vain raw-delta)


def _week_label(d: date | None = None) -> str:
    """Palauta ISO-viikkotunniste 'YYYY-WW', esim. '2026-20'."""
    d = d or date.today()
    iso = d.isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"


def _csv_path(log_dir: Path, week: str) -> Path:
    return log_dir / f"feature_drift_{week}.csv"


def _load_previous_csvs(log_dir: Path, current_week: str) -> list[pd.DataFrame]:
    """Lataa kaikki aiempien viikkojen drift-CSV:t kronologisessa järjestyksessä."""
    files = sorted(log_dir.glob("feature_drift_*.csv"))
    dfs = []
    for f in files:
        week = f.stem.replace("feature_drift_", "")
        if week == current_week:
            continue
        try:
            dfs.append(pd.read_csv(f))
        except Exception as e:
            logger.warning("Ei voitu ladata %s: %s", f, e)
    return dfs


def compute_feature_stats(features_df: pd.DataFrame) -> pd.DataFrame:
    """Laske jakaumatilastot per piirre.

    Returns:
        DataFrame jossa sarakkeet:
          feature, mean, std, p25, p50, p75, nan_pct, n_total, n_valid
    """
    rows = []
    all_monitored = _NUMERIC_FEATURES + _CATEGORICAL_FEATURES

    for feat in all_monitored:
        if feat not in features_df.columns:
            # Piirre puuttuu kokonaan — NaN 100 %
            rows.append({
                "feature": feat,
                "mean": np.nan,
                "std": np.nan,
                "p25": np.nan,
                "p50": np.nan,
                "p75": np.nan,
                "nan_pct": 100.0,
                "n_total": len(features_df),
                "n_valid": 0,
            })
            continue

        col = features_df[feat]
        n_total = len(col)
        n_valid = col.notna().sum()
        nan_pct = float((n_total - n_valid) / n_total * 100) if n_total else 100.0

        if feat in _NUMERIC_FEATURES:
            valid = pd.to_numeric(col, errors="coerce").dropna()
            rows.append({
                "feature": feat,
                "mean": float(valid.mean()) if len(valid) else np.nan,
                "std": float(valid.std()) if len(valid) > 1 else np.nan,
                "p25": float(valid.quantile(0.25)) if len(valid) else np.nan,
                "p50": float(valid.quantile(0.50)) if len(valid) else np.nan,
                "p75": float(valid.quantile(0.75)) if len(valid) else np.nan,
                "nan_pct": nan_pct,
                "n_total": n_total,
                "n_valid": int(n_valid),
            })
        else:
            # Kategoriset: seurataan vain NaN-% ja moodia
            mode_val = col.mode().iloc[0] if n_valid > 0 else None
            rows.append({
                "feature": feat,
                "mean": np.nan,       # ei sovellettavissa
                "std": np.nan,
                "p25": np.nan,
                "p50": np.nan,
                "p75": np.nan,
                "nan_pct": nan_pct,
                "n_total": n_total,
                "n_valid": int(n_valid),
            })

    return pd.DataFrame(rows)


def detect_anomalies(
    current: pd.DataFrame,
    history: list[pd.DataFrame],
    alert_sigma: float = _ALERT_SIGMA,
    alert_nan_pp: float = _ALERT_NAN_PP,
) -> list[dict[str, Any]]:
    """Vertaa tämän viikon tilastoja historiaan ja palauta anomaliat.

    Kaksi tarkistusta:
      1. NaN-% nousu > alert_nan_pp prosenttiyksikköä vs. viimeisin viikko
      2. mean tai p50 liikkuu yli alert_sigma × (historiallinen σ weekly-means)
         (vain jos history >= _MIN_WEEKS_FOR_SIGMA; muuten raw-delta > 20 %)

    Args:
        current: compute_feature_stats():n tulos tälle viikolle
        history: lista aiempien viikkojen compute_feature_stats()-tuloksista
                 kronologisessa järjestyksessä (vanhin ensin)

    Returns:
        Lista anomalia-dictionaryistä:
          {feature, check, old_val, new_val, delta, threshold, severity}
    """
    anomalies = []
    curr_idx = current.set_index("feature")

    if not history:
        logger.info("Ei aiempaa historiaa — ei vertailua, vain tallennus.")
        return anomalies

    prev = history[-1].set_index("feature")  # viimeisin viikko

    for feat in curr_idx.index:
        c_row = curr_idx.loc[feat]
        if feat not in prev.index:
            continue
        p_row = prev.loc[feat]

        # --- Tarkistus 1: NaN-% ---
        nan_delta = float(c_row["nan_pct"]) - float(p_row["nan_pct"])
        if nan_delta > alert_nan_pp:
            anomalies.append({
                "feature": feat,
                "check": "nan_pct",
                "old_val": round(float(p_row["nan_pct"]), 2),
                "new_val": round(float(c_row["nan_pct"]), 2),
                "delta": round(nan_delta, 2),
                "threshold": alert_nan_pp,
                "severity": "WARNING" if nan_delta < 30 else "CRITICAL",
            })

        # --- Tarkistus 2: mean ja p50 (vain numeeriset) ---
        if feat not in _NUMERIC_FEATURES:
            continue

        for metric in ("mean", "p50"):
            c_val = c_row[metric]
            p_val = p_row[metric]

            if pd.isna(c_val) or pd.isna(p_val):
                continue

            delta = float(c_val) - float(p_val)

            if len(history) >= _MIN_WEEKS_FOR_SIGMA:
                # Laske σ historian weekly-meaneista
                hist_vals = []
                for h in history:
                    h_idx = h.set_index("feature")
                    if feat in h_idx.index and not pd.isna(h_idx.loc[feat, metric]):
                        hist_vals.append(float(h_idx.loc[feat, metric]))

                if len(hist_vals) >= _MIN_WEEKS_FOR_SIGMA:
                    hist_std = float(np.std(hist_vals, ddof=1))
                    if hist_std > 0:
                        n_sigma = abs(delta) / hist_std
                        if n_sigma > alert_sigma:
                            anomalies.append({
                                "feature": feat,
                                "check": metric,
                                "old_val": round(p_val, 6),
                                "new_val": round(float(c_val), 6),
                                "delta": round(delta, 6),
                                "threshold": f"{alert_sigma}σ (hist_std={hist_std:.4f})",
                                "severity": "WARNING" if n_sigma < 3 else "CRITICAL",
                            })
                        continue

            # Ei riittävästi historiaa σ-laskentaan — käytä raw %-muutosta
            # Varoita jos muutos > 20 % nykyisestä arvosta
            if abs(p_val) > 1e-9:
                rel_change = abs(delta) / abs(p_val)
                if rel_change > 0.20:
                    anomalies.append({
                        "feature": feat,
                        "check": metric,
                        "old_val": round(p_val, 6),
                        "new_val": round(float(c_val), 6),
                        "delta": round(delta, 6),
                        "threshold": "20% raw (historia liian lyhyt σ:lle)",
                        "severity": "INFO",
                    })

    return anomalies


def log_feature_distributions(
    db_path: Path | str = DB_PATH,
    log_dir: Path | str = LOG_DIR,
    week: str | None = None,
    lookback_days: int = 365,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Pääfunktio: lataa DB, laske stats, tallenna CSV, vertaa aiempaan.

    Args:
        db_path: SQLite-tietokannan polku
        log_dir: hakemisto johon CSV:t tallennetaan
        week: ISO-viikkotunniste 'YYYY-WW' (oletus: kuluva viikko)
        lookback_days: montako päivää taaksepäin dataa otetaan (oletus 365)

    Returns:
        (stats_df, anomalies_list)
          stats_df: compute_feature_stats():n tulos tallennettuna CSV:hen
          anomalies_list: detect_anomalies():n tulos
    """
    db_path = Path(db_path)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    week = week or _week_label()

    logger.info("Feature drift -ajo viikolle %s (db=%s)", week, db_path)

    # --- Lataa data DB:stä ---
    con = sqlite3.connect(db_path)
    try:
        runners = pd.read_sql(
            """
            SELECT r.*, ra.race_date, h.birth_year
            FROM runners r
            JOIN races ra ON r.race_id = ra.race_id
            LEFT JOIN horses h ON r.horse_id = h.horse_id
            """,
            con,
        )
        races = pd.read_sql("SELECT * FROM races", con)
        horse_starts = pd.read_sql(
            "SELECT * FROM horse_starts "
            "WHERE (withdrawn IS NULL OR withdrawn != 1) "
            "  AND (finish_position IS NULL OR finish_position != 99)",
            con,
        )
        horses = pd.read_sql("SELECT * FROM horses", con)
        tracks = pd.read_sql("SELECT * FROM tracks", con)
    finally:
        con.close()

    logger.info(
        "DB ladattu: %d runneria, %d lähtöä, %d horse_startia",
        len(runners), len(races), len(horse_starts),
    )

    # --- Rakenna feature-matriisi ---
    # Importoidaan täällä jotta circular-import ei synny
    from src.features.build_features import build_feature_matrix, fill_finish_positions

    runners_filled = fill_finish_positions(runners)
    features = build_feature_matrix(
        runners_filled, races,
        horse_starts=horse_starts,
        horses=horses,
        tracks=tracks,
    )
    features["race_date"] = pd.to_datetime(features["race_date"])

    # Rajoita lookback-ikkunaan
    cutoff = pd.Timestamp.today() - pd.Timedelta(days=lookback_days)
    features_window = features[features["race_date"] >= cutoff].copy()
    logger.info(
        "Feature-matriisi: %d riviä (lookback %d pv, alkaen %s)",
        len(features_window), lookback_days, cutoff.date(),
    )

    # --- Laske stats ---
    stats = compute_feature_stats(features_window)
    stats["week"] = week
    stats["n_runners_total"] = len(features_window)

    # --- Tallenna CSV ---
    out_path = _csv_path(log_dir, week)
    stats.to_csv(out_path, index=False)
    logger.info("Tallennettu: %s", out_path)

    # --- Vertaa aiempaan ---
    history = _load_previous_csvs(log_dir, week)
    logger.info("Löytyi %d aiempaa drift-CSV:tä", len(history))
    anomalies = detect_anomalies(stats, history)

    if anomalies:
        logger.warning(
            "DRIFT-HÄLYTYS: %d anomaliaa havaittu viikolle %s:", len(anomalies), week
        )
        for a in anomalies:
            logger.warning(
                "  [%s] %s.%s: %.4f → %.4f (delta=%.4f, raja=%s)",
                a["severity"], a["feature"], a["check"],
                a["old_val"], a["new_val"], a["delta"], a["threshold"],
            )
    else:
        logger.info("Ei drift-anomalioita viikolle %s.", week)

    return stats, anomalies


def print_drift_report(
    stats: pd.DataFrame,
    anomalies: list[dict[str, Any]],
    week: str,
) -> None:
    """Tulosta ihmisluettava raportti stdoutiin."""
    print(f"\n{'='*65}")
    print(f"FEATURE DRIFT -RAPORTTI — viikko {week}")
    print(f"{'='*65}")
    print(f"Rivejä analysoitu: {stats['n_runners_total'].iloc[0] if len(stats) else '?'}\n")

    # Piirteet joissa NaN-% korkea
    high_nan = stats[stats["nan_pct"] > 30].sort_values("nan_pct", ascending=False)
    if len(high_nan):
        print("⚠️  Korkea NaN-% (>30%):")
        for _, r in high_nan.iterrows():
            print(f"   {r['feature']:40s} {r['nan_pct']:6.1f}%")
        print()

    # Kaikkien piirteiden tiivistelmä
    print(f"{'Piirre':<40} {'mean':>10} {'p50':>10} {'NaN%':>7} {'n_valid':>8}")
    print("-" * 80)
    for _, r in stats.iterrows():
        mean_s = f"{r['mean']:.4f}" if pd.notna(r["mean"]) else "  —"
        p50_s = f"{r['p50']:.4f}" if pd.notna(r["p50"]) else "  —"
        print(f"{r['feature']:<40} {mean_s:>10} {p50_s:>10} {r['nan_pct']:>6.1f}% {int(r['n_valid']):>8}")

    # Anomaliat
    print(f"\n{'='*65}")
    if anomalies:
        print(f"🚨 ANOMALIAT ({len(anomalies)} kpl):")
        for a in anomalies:
            sev_icon = "🔴" if a["severity"] == "CRITICAL" else "🟡"
            print(
                f"  {sev_icon} [{a['severity']}] {a['feature']}.{a['check']}: "
                f"{a['old_val']} → {a['new_val']} "
                f"(delta={a['delta']:+.4f}, raja={a['threshold']})"
            )
    else:
        print("✅ Ei anomalioita — kaikki piirteet normaalilla alueella.")
    print()
