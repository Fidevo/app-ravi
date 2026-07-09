"""A/B-vertailu: baseline-malli vs. baseline + Travronden-piirteet.

Vertaa kahta mallia identtisellä train/test-splitillä:
  A) Baseline: FEATURE_COLS ilman tr_*-piirteitä (nykyinen tuotantomalli)
  B) TR-malli: FEATURE_COLS + 10 tr_*-piirrettä (Travronden Vaihe 2)

Päätösraja auditoijan määrittämä:
  Brier-paranema ≥ 0.001 → lisätty signaali vahvistettu (dokumentoi)
  Brier-paranema ≥ 0.005 → integroi tuotantoon

Lisäksi raportoi erikseen V-pelilähdöt (is_v_race=True) joilla
tr_*-data on käytettävissä — tämä näyttää parhaan mahdollisen parannuksen.

Käyttö (Hetznerillä):
    python scripts/travronden_ab_test.py
    python scripts/travronden_ab_test.py --split-date 2026-05-08 --rs 42
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.features.travronden_features import TRAVRONDEN_FEATURE_COLS
from src.models.ranker import (
    CATEGORICAL_COLS,
    FEATURE_COLS,
    compute_nll,
    predict_win_probabilities,
    train_ranker,
)
from src.paths import DB_PATH

# Piirteet ilman tr_* = baseline
_BASELINE_FEATURE_COLS = [c for c in FEATURE_COLS if not c.startswith("tr_")]
# Kaikki tr_*-piirteet PAITSI tr_expected_odds (< 30% notna)
# KNOWN_ISSUES #14 aktivointiehto 2: A/B ILMAN tr_game_percent_v — se on
# Copycat-riski (kopioi markkinasentimentin) ja vinouttaisi mittauksen.
_TR_FEATURE_COLS = [
    c for c in TRAVRONDEN_FEATURE_COLS
    if c not in ("tr_expected_odds", "tr_game_percent_v")
]
# TR-malli: baseline + tr_*
_TR_MODEL_COLS = _BASELINE_FEATURE_COLS + _TR_FEATURE_COLS


def _brier(merged: pd.DataFrame) -> float:
    m = merged.dropna(subset=["win_prob", "finish_position"])
    actual = (m["finish_position"] == 1).astype(float)
    return float(((m["win_prob"] - actual) ** 2).mean())


def _run_model(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    feature_cols: list[str],
    label: str,
    rs: int,
) -> dict:
    print(f"\n  [{label}] {len(feature_cols)} piirrettä | train={len(train_df)} test={len(test_df)}")
    model = train_ranker(train_df, feature_cols=feature_cols, random_state=rs)
    preds = predict_win_probabilities(model, test_df, feature_cols=feature_cols)
    merged = test_df.merge(preds[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"])

    brier = _brier(merged)
    nll = compute_nll(merged)

    # Feature importance top-5
    fi = pd.Series(
        model.feature_importance(importance_type="gain"),
        index=model.feature_name(),
    ).sort_values(ascending=False)
    top5 = list(fi.head(5).index)

    # tr_*-piirteiden rankit
    tr_ranks = {
        f: (list(fi.index).index(f) + 1 if f in fi.index else None)
        for f in _TR_FEATURE_COLS
    }

    print(f"  [{label}] Brier={brier:.4f}  NLL={nll:.2f}")
    print(f"  [{label}] Top-5: {top5}")
    if any(v is not None for v in tr_ranks.values()):
        top_tr = {k: v for k, v in tr_ranks.items() if v is not None}
        print(f"  [{label}] TR-piirteiden rankit: {top_tr}")

    return {
        "label": label,
        "n_features": len(feature_cols),
        "brier": brier,
        "nll": nll,
        "top5": top5,
        "tr_ranks": tr_ranks,
        "n_test": len(merged),
        "n_races": merged["race_id"].nunique(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Travronden A/B-vertailu")
    ap.add_argument("--db", type=str, default=str(DB_PATH))
    ap.add_argument("--split-date", type=str, default="2026-05-08",
                    help="Train/test -raja (test >= split_date)")
    ap.add_argument("--rs", type=int, default=42,
                    help="LightGBM random_state (toistettavuus)")
    args = ap.parse_args()

    print(f"=== Travronden A/B -vertailu ===")
    print(f"DB: {args.db}")
    print(f"Split: train < {args.split_date}, test >= {args.split_date}")

    # --- Lataa data ---
    con = sqlite3.connect(args.db)
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
        "  AND (finish_position IS NULL OR finish_position != 99) "
        "  AND (race_date IS NULL OR race_date >= '2024-01-01')",
        con,
    )
    horses = pd.read_sql("SELECT * FROM horses", con)
    tracks = pd.read_sql("SELECT * FROM tracks", con)
    con.close()

    print(f"\nData ladattu: {len(runners)} runneria, {runners['race_id'].nunique()} lähtöä")

    # TR-kattavuus
    tr_avail = runners["tr_start_interval_group"].notna().sum()
    tr_pct = 100.0 * tr_avail / len(runners)
    v_race_count = (runners["is_v_race"] == 1).sum() if "is_v_race" in runners.columns else 0
    print(f"TR-data saatavilla: {tr_avail}/{len(runners)} ({tr_pct:.1f}%)")
    print(f"is_v_race=True: {v_race_count}")

    # --- Rakenna feature-matriisi ---
    # OOM-torjunta (9.7.2026: ajo kuoli 3.6 GB RSS:ään): float64 -> float32
    # ennen buildia, kuten retrain_20260704.py:ssä.
    for _df in (runners, horse_starts):
        for _c in _df.select_dtypes(include="float64").columns:
            _df[_c] = _df[_c].astype("float32")

    runners_filled = fill_finish_positions(runners)
    features = build_feature_matrix(
        runners_filled, races,
        horse_starts=horse_starts,
        horses=horses,
        tracks=tracks,
    )
    features["race_date"] = pd.to_datetime(features["race_date"])

    # Varmista tr_*-sarakkeet ovat mukana
    for col in _TR_FEATURE_COLS:
        if col not in features.columns and col in runners.columns:
            features = features.merge(
                runners[["runner_id", col]], on="runner_id", how="left"
            )

    # Tarkista myös tr_*-sarakkeet suoraan runners:ista
    tr_cols_in_runners = [c for c in _TR_FEATURE_COLS if c in runners.columns]
    tr_cols_missing = [c for c in _TR_FEATURE_COLS if c not in features.columns]
    if tr_cols_missing:
        # Merge tr_*-sarakkeet runners:ista features:iin
        features = features.merge(
            runners[["runner_id"] + tr_cols_in_runners],
            on="runner_id",
            how="left",
        )
        print(f"TR-sarakkeet mergetty features:iin: {tr_cols_in_runners}")

    for _c in features.select_dtypes(include="float64").columns:
        features[_c] = features[_c].astype("float32")
    del runners_filled, horse_starts

    # --- Train/test -split ---
    split = pd.Timestamp(args.split_date)
    train_df = features[features["race_date"] < split].copy()
    test_df = features[features["race_date"] >= split].copy()
    print(f"\nSplit: train={len(train_df)} ({train_df['race_id'].nunique()} lähtöä) "
          f"/ test={len(test_df)} ({test_df['race_id'].nunique()} lähtöä)")

    # TR-kattavuus testidatassa
    if "tr_start_interval_group" in test_df.columns:
        test_tr_pct = test_df["tr_start_interval_group"].notna().mean() * 100
        print(f"TR-kattavuus testidatassa: {test_tr_pct:.1f}%")

    # --- Suorita molemmat mallit ---
    print("\n--- Malli A: Baseline (ei Travronden-piirteitä) ---")
    res_a = _run_model(train_df, test_df, _BASELINE_FEATURE_COLS, "Baseline", args.rs)

    print("\n--- Malli B: Baseline + Travronden (10 tr_*-piirrettä) ---")
    res_b = _run_model(train_df, test_df, _TR_MODEL_COLS, "TR-malli", args.rs)

    # --- V-pelilähdöt erikseen ---
    if "is_v_race" in test_df.columns:
        v_test = test_df[test_df["is_v_race"] == 1].copy()
        v_train = train_df.copy()  # Treeni kaikilla lähdöillä
        if len(v_test) > 10:
            print(f"\n--- V-pelilähdöt erikseen (n={len(v_test)} runneria, "
                  f"{v_test['race_id'].nunique()} lähtöä) ---")
            res_a_v = _run_model(v_train, v_test, _BASELINE_FEATURE_COLS, "Baseline(V)", args.rs)
            res_b_v = _run_model(v_train, v_test, _TR_MODEL_COLS, "TR-malli(V)", args.rs)

            delta_v = res_a_v["brier"] - res_b_v["brier"]
            print(f"\n  V-pelilähdöt Brier-paranema: {delta_v:+.4f}")

    # --- Yhteenveto ---
    delta = res_a["brier"] - res_b["brier"]
    print(f"\n{'='*55}")
    print(f"TRAVRONDEN A/B -VERTAILU — YHTEENVETO")
    print(f"{'='*55}")
    print(f"  Malli A (Baseline):  Brier={res_a['brier']:.4f}  NLL={res_a['nll']:.2f}")
    print(f"  Malli B (TR-malli):  Brier={res_b['brier']:.4f}  NLL={res_b['nll']:.2f}")
    print(f"  Brier-paranema:      {delta:+.4f}  (+ = parempi TR-mallilla)")
    print()

    if delta >= 0.005:
        print(f"  ✅ INTEGROI TUOTANTOON — paranema ≥ 0.005")
    elif delta >= 0.001:
        print(f"  🟡 LISÄTTY SIGNAALI — paranema {delta:.4f} < 0.005 (dokumentoi, älä vielä integroi)")
    elif delta >= 0.0:
        print(f"  🟡 MARGINAALINEN PARANEMA — harkitse lisää dataa ja toista testi")
    else:
        print(f"  ❌ EI PARANNUSTA — TR-piirteet eivät auta ({delta:.4f})")
        print(f"     Mahdolliset syyt: TR-data kattaa vain {tr_pct:.1f}% → "
              f"riittämätön signaali, tai piirteet eivät ole informatiivisia")

    print(f"\n  TR-datan kattavuus treenidatassa: {tr_pct:.1f}%")
    print(f"  HUOM: Jos kattavuus < 30%, toista A/B sen jälkeen kun scheduler")
    print(f"  on kerännyt tr_*-dataa ~4+ viikkoa tuotannossa.")
    print(f"{'='*55}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
