"""Retrain 4.7.2026 — korjaa kolme aiempien retrainien ongelmaa:

1. OOM (29.6. retrain kuoli 3.3 GB RSS:ään): float32-downcast feature-matriisille
   heti buildin jälkeen + vain tarvittavat runner-sarakkeet SQL:stä.
2. In-sample-kalibrointi: T sovitettiin aiemmin samaan test_df:ään josta Brier
   raportoitiin → optimistinen harha. Nyt 3-jakoinen split:
       train < CAL_START | kalibrointi CAL_START..TEST_START | testi TEST_START..
   T ja blend-α sovitetaan kalibrointi-ikkunaan, Brier raportoidaan testistä.
3. Uudet piirteet: driver/trainer *_365d_hs (horse_starts-pohjainen, serve-
   symmetrinen) — palauttaa 1.6. poistetun 365d-signaalin ilman train/serve-skewiä.

Aja palvelimella: /home/ravi/app-ravi/.venv/bin/python scripts/retrain_20260704.py
"""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")

import json
import sqlite3

import numpy as np
import pandas as pd

from src.features.build_features import (
    build_feature_matrix,
    compute_start_position_lookup,
    fill_finish_positions,
)
from src.models.ranker import (
    FEATURE_COLS,
    calibrate_temperature,
    compute_nll,
    fit_blend_alpha,
    predict_win_probabilities,
    train_ranker,
)

DATA_DIR = "/home/ravi/app-ravi/data"
OUT_PATH = f"{DATA_DIR}/model_baseline_20260704.lgb"
CAL_START = "2026-06-15"   # train < tämä
TEST_START = "2026-06-25"  # kalibrointi [CAL_START, TEST_START), testi >= tämä


def mem_mb() -> int:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS"):
                return int(line.split()[1]) // 1024
    return 0


print(f"[0] Aloitetaan, RAM={mem_mb()} MB", flush=True)
con = sqlite3.connect(f"{DATA_DIR}/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, ra.distance, ra.start_method AS race_start_method, "
    "h.birth_year FROM runners r "
    "JOIN races ra ON r.race_id = ra.race_id "
    "LEFT JOIN horses h ON r.horse_id = h.horse_id",
    con,
)
if "start_method" not in runners.columns:
    runners = runners.rename(columns={"race_start_method": "start_method"})
elif "race_start_method" in runners.columns:
    runners["start_method"] = runners["start_method"].fillna(runners["race_start_method"])
    runners = runners.drop(columns=["race_start_method"])
races = pd.read_sql("SELECT * FROM races", con)
horse_starts = pd.read_sql(
    "SELECT * FROM horse_starts "
    "WHERE (withdrawn IS NULL OR withdrawn != 1) "
    "  AND (finish_position IS NULL OR finish_position != 99) "
    "  AND (race_date IS NULL OR race_date >= '2024-01-01')", con
)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()
print(f"[1] Data ladattu, RAM={mem_mb()} MB | runners={len(runners)} hs={len(horse_starts)}", flush=True)

# OOM-torjunta ennen feature-buildia: pudota float64 → float32 lähtödatasta.
# build_feature_matrix perii dtypet → intermediate-groupbyt puolittuvat.
for _df in (runners, horse_starts):
    for c in _df.select_dtypes(include="float64").columns:
        _df[c] = _df[c].astype("float32")

# Kertoimet talteen blend-α:n sovitusta varten (build ei säilytä kaikkia sarakkeita)
odds_lookup = runners[["race_id", "horse_id", "win_odds_final"]].copy()

features = build_feature_matrix(
    fill_finish_positions(runners), races,
    horse_starts=horse_starts, horses=horses, tracks=tracks,
)
print(f"[2] Features rakennettu, RAM={mem_mb()} MB | features={len(features)}", flush=True)

for c in features.select_dtypes(include="float64").columns:
    features[c] = features[c].astype("float32")
del runners, horse_starts
print(f"[2b] float32-downcast, RAM={mem_mb()} MB", flush=True)

features["race_date"] = pd.to_datetime(features["race_date"])

train_df = features[features["race_date"] < CAL_START].copy()
cal_df = features[(features["race_date"] >= CAL_START) & (features["race_date"] < TEST_START)].copy()
test_df = features[features["race_date"] >= TEST_START].copy()
print(f"Train: {len(train_df)} | Cal: {len(cal_df)} | Test: {len(test_df)} riviä | {len(FEATURE_COLS)} piirrettä")

print(f"[3] Split valmis, RAM={mem_mb()} MB", flush=True)
model = train_ranker(train_df, random_state=42)
print(f"[4] Malli koulutettu, RAM={mem_mb()} MB", flush=True)
model.save_model(OUT_PATH)
print(f"Malli tallennettu: {OUT_PATH}")

spwr_lookup = compute_start_position_lookup(features, races)
spwr_path = OUT_PATH.replace(".lgb", "_spwr_lookup.csv")
spwr_lookup.to_csv(spwr_path, index=False)
print(f"SPWR-hakutaulu tallennettu: {spwr_path} ({len(spwr_lookup)} riviä)")

# ── Kalibrointi OOS-ikkunassa (EI testidatalla) ─────────────────────────────
cal_preds = predict_win_probabilities(model, cal_df)
cal_merged = cal_df[["race_id", "horse_id", "finish_position"]].merge(
    cal_preds[["race_id", "horse_id", "win_prob", "score"]], on=["race_id", "horse_id"]
)
temperature = calibrate_temperature(cal_merged)
print(f"Temperature (cal-ikkuna {CAL_START}..{TEST_START}): T={temperature:.4f}")

# Blend-α: realististen prosenttien tuloste (malli × markkina), sovitus cal-ikkunaan
cal_blend_in = cal_merged.merge(odds_lookup, on=["race_id", "horse_id"], how="left")
cal_blend_in = cal_blend_in.rename(columns={"win_odds_final": "win_odds"})
cal_preds_T = predict_win_probabilities(model, cal_df, temperature=temperature)
cal_blend_in = cal_blend_in.drop(columns=["win_prob"]).merge(
    cal_preds_T[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"]
)
blend_alpha = fit_blend_alpha(cal_blend_in, odds_col="win_odds")
print(f"Blend-α (cal-ikkuna): {blend_alpha:.4f}")

# ── Evaluointi testi-ikkunassa (täysin OOS: eri kuin treeni JA kalibrointi) ──
test_preds = predict_win_probabilities(model, test_df, temperature=temperature)
tm = test_df[["race_id", "horse_id", "finish_position"]].merge(
    test_preds[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"]
)
tm = tm[tm["finish_position"].notna()]
tm["actual_win"] = (tm["finish_position"] == 1).astype(int)
brier_test = float(((tm["win_prob"] - tm["actual_win"]) ** 2).mean())
nll_test = compute_nll(tm)
print(f"Testi-Brier (T={temperature:.4f}, {TEST_START}..): {brier_test:.4f}  NLL={nll_test:.2f}")

# Tasaisuusdiagnostiikka testissä
race_stats = tm.groupby("race_id")["win_prob"].agg(["std", "max"]).reset_index()
median_std = float(race_stats["std"].median())
mean_std = float(race_stats["std"].mean())
median_top1 = float(race_stats["max"].median())
mean_top1 = float(race_stats["max"].mean())
flat_pct = float((race_stats["std"] < 0.03).mean() * 100)
print("Tasaisuusdiagnostiikka (testi):")
print(f"  Median std/lähtö: {median_std:.4f}  (healthy: 0.05-0.12)")
print(f"  Median top-1 prob: {median_top1:.4f}  (healthy: 0.20-0.40)")
print(f"  Tasaisia lähtöjä (std<0.03): {flat_pct:.1f}%")

meta = {
    "temperature": temperature,
    "blend_alpha": blend_alpha,
    "brier": brier_test,
    "calibration": "OOS: T+alpha sovitettu cal-ikkunaan, Brier testi-ikkunasta",
    "cal_window": [CAL_START, TEST_START],
    "test_window_start": TEST_START,
    "train_rows": len(train_df),
    "cal_rows": len(cal_df),
    "test_rows": len(test_df),
    "n_features": len(FEATURE_COLS),
    "flatness": {
        "median_std": median_std, "mean_std": mean_std,
        "median_top1": median_top1, "mean_top1": mean_top1,
        "flat_pct": flat_pct,
    },
}
meta_path = OUT_PATH.replace(".lgb", "_meta.json")
with open(meta_path, "w") as f:
    json.dump(meta, f, indent=2)
print(f"Meta tallennettu: {meta_path}")
print(f"[5] Valmis, RAM={mem_mb()} MB", flush=True)
