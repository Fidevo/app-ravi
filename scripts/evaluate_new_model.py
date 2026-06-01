"""Evaluoi model_baseline_20260601 toukokuun 2026 datalla ja kalibroi T uudelleen.

Ongelma: retrain_model.py ajettiin split_date=2026-06-01 → test-setti = vain
tänään (391 hevosta, ei tuloksia) → Brier=0.0295, T=0.6797 EPÄKELPO.

Tämä skripti evaluoi uuden mallin toukokuun datalla (oikea OOS-testi) ja
tallentaa päivitetyn meta.json:n oikeilla arvoilla.
"""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")
import sqlite3, json, glob, os
import pandas as pd
import lightgbm as lgb

from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import predict_win_probabilities, compute_nll, calibrate_temperature

EVAL_START = "2026-05-01"
EVAL_END   = "2026-06-01"
MODEL_PATH = "/home/ravi/app-ravi/data/model_baseline_20260601.lgb"
META_PATH  = MODEL_PATH.replace(".lgb", "_meta.json")

print(f"Evaluoidaan {MODEL_PATH}")
print(f"Testiperiodi: {EVAL_START} – {EVAL_END}")

con = sqlite3.connect("/home/ravi/app-ravi/data/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, ra.distance, ra.start_method AS race_start_method,"
    " h.birth_year FROM runners r"
    " JOIN races ra ON r.race_id = ra.race_id"
    " LEFT JOIN horses h ON r.horse_id = h.horse_id"
    " WHERE ra.race_date >= ? AND ra.race_date < ?",
    con, params=(EVAL_START, EVAL_END),
)
races     = pd.read_sql("SELECT * FROM races WHERE race_date >= ? AND race_date < ?",
                        con, params=(EVAL_START, EVAL_END))
races_all = pd.read_sql("SELECT * FROM races", con)
hs = pd.read_sql(
    "SELECT * FROM horse_starts"
    " WHERE (withdrawn IS NULL OR withdrawn != 1)"
    "   AND (finish_position IS NULL OR finish_position != 99)"
    "   AND (race_date IS NULL OR race_date >= '2024-01-01')", con)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()

if "start_method" not in runners.columns:
    runners = runners.rename(columns={"race_start_method": "start_method"})
elif "race_start_method" in runners.columns:
    runners["start_method"] = runners["start_method"].fillna(runners["race_start_method"])
    runners = runners.drop(columns=["race_start_method"])

print(f"Evaluointidata: {len(runners)} hevosta, {runners['race_id'].nunique()} lähtöä")

spwr_files = sorted(glob.glob("/home/ravi/app-ravi/data/model_baseline_*_spwr_lookup.csv"))
spwr_lookup = pd.read_csv(spwr_files[-1]) if spwr_files else None
print(f"SPWR-lookup: {os.path.basename(spwr_files[-1]) if spwr_files else 'ei loydy'}", flush=True)

features = build_feature_matrix(
    fill_finish_positions(runners), races,
    horse_starts=hs, horses=horses, tracks=tracks,
    spwr_lookup=spwr_lookup, all_races=races_all,
)
print(f"Features: {len(features)} rivita", flush=True)

model = lgb.Booster(model_file=MODEL_PATH)

preds_raw = predict_win_probabilities(model, features, temperature=1.0)
merged = features.merge(preds_raw[["race_id", "horse_id", "win_prob", "score"]], on=["race_id", "horse_id"])
merged["actual_win"] = (merged["finish_position"] == 1).astype(int)

# Vain lahdot joissa on tulokset (yksi voittaja per lahto)
has_results = merged.groupby("race_id")["actual_win"].sum()
valid_races = has_results[has_results == 1].index
merged = merged[merged["race_id"].isin(valid_races)]
print(f"Lahtoja tuloksilla: {len(valid_races)} / {features['race_id'].nunique()}")

brier_raw = float(((merged["win_prob"] - merged["actual_win"]) ** 2).mean())
print(f"Brier (T=1.0) = {brier_raw:.4f}  NLL={compute_nll(merged):.2f}")

temperature = calibrate_temperature(merged)
print(f"Optimaalinen temperature: {temperature:.4f}")

preds_cal = predict_win_probabilities(model, features, temperature=temperature)
m2 = features.merge(preds_cal[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"])
m2["actual_win"] = (m2["finish_position"] == 1).astype(int)
m2 = m2[m2["race_id"].isin(valid_races)]

brier_cal = float(((m2["win_prob"] - m2["actual_win"]) ** 2).mean())
print(f"Brier (T={temperature:.4f}) = {brier_cal:.4f}  NLL={compute_nll(m2):.2f}")

race_stats = m2.groupby("race_id")["win_prob"].agg(["std", "max"]).reset_index()
median_std  = float(race_stats["std"].median())
mean_std    = float(race_stats["std"].mean())
median_top1 = float(race_stats["max"].median())
mean_top1   = float(race_stats["max"].mean())
flat_pct    = float((race_stats["std"] < 0.03).mean() * 100)
print(f"Tasaisuus: median_std={median_std:.4f}  median_top1={median_top1:.4f}  tasaisia={flat_pct:.1f}%")

# Paivita meta.json
with open(META_PATH) as f:
    meta = json.load(f)
meta.update({
    "temperature": temperature,
    "brier": brier_cal,
    "brier_uncal": brier_raw,
    "eval_period": f"{EVAL_START}..{EVAL_END}",
    "eval_races": int(len(valid_races)),
    "eval_rows": int(len(merged)),
    "flatness": {
        "median_std": median_std, "mean_std": mean_std,
        "median_top1": median_top1, "mean_top1": mean_top1,
        "flat_pct": flat_pct,
    },
})
with open(META_PATH, "w") as f:
    json.dump(meta, f, indent=2)
print(f"Meta paivitetty: {META_PATH}")
