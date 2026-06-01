"""NaN-diagnostiikka tämän päivän lähdöille — mikä feature aiheuttaa tasaisuuden."""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")
import sqlite3
import pandas as pd
import numpy as np
from src.features.build_features import build_feature_matrix
from src.models.ranker import FEATURE_COLS

TARGET_DATE = "2026-06-01"
con = sqlite3.connect("/home/ravi/app-ravi/data/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, ra.distance, ra.start_method AS race_start_method,"
    " h.birth_year FROM runners r"
    " JOIN races ra ON r.race_id = ra.race_id"
    " LEFT JOIN horses h ON r.horse_id = h.horse_id"
    " WHERE ra.race_date = ?", con, params=(TARGET_DATE,)
)
races  = pd.read_sql("SELECT * FROM races WHERE race_date=?", con, params=(TARGET_DATE,))
hs     = pd.read_sql(
    "SELECT * FROM horse_starts"
    " WHERE (withdrawn IS NULL OR withdrawn != 1)"
    "   AND (finish_position IS NULL OR finish_position != 99)"
    "   AND (race_date IS NULL OR race_date >= '2024-01-01')", con
)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()

if "start_method" not in runners.columns:
    runners = runners.rename(columns={"race_start_method": "start_method"})
elif "race_start_method" in runners.columns:
    runners["start_method"] = runners["start_method"].fillna(runners["race_start_method"])
    runners = runners.drop(columns=["race_start_method"])

features = build_feature_matrix(runners, races, horse_starts=hs, horses=horses, tracks=tracks)
KEY_COLS = [c for c in FEATURE_COLS if c in features.columns]

print(f"Lähtöjä: {features['race_id'].nunique()}, hevosia: {len(features)}, featuria: {len(KEY_COLS)}")

# 1. Globaali NaN-prosentti per feature
nan_rates = features[KEY_COLS].isna().mean() * 100
print("\n=== Features joissa > 30% NaN (kaikki lähdöt) ===")
bad_global = nan_rates[nan_rates > 30].sort_values(ascending=False)
if bad_global.empty:
    print("  (ei yhtään)")
for col, v in bad_global.items():
    print(f"  {col:<45} {v:.0f}%")

# 2. NaN per raita
print("\n=== start_position_win_rate NaN per raita ===")
for track in sorted(features["track"].dropna().unique()):
    sub = features[features["track"] == track]
    if "start_position_win_rate" in sub.columns:
        nan_pct = sub["start_position_win_rate"].isna().mean() * 100
    else:
        nan_pct = 100.0
    n_races = sub["race_id"].nunique()
    print(f"  {track:<20} {nan_pct:>5.0f}% NaN  ({len(sub)} hevosta, {n_races} lähtöä)")

# 3. horse_starts pool koko per raita ja start_method
print("\n=== horse_starts pool per (raita, start_method) ===")
if "track" in hs.columns and "start_method" in hs.columns:
    pool = hs.groupby(["track", "start_method"], dropna=False).size().reset_index(name="n")
    for _, row in pool.sort_values(["track", "start_method"]).iterrows():
        print(f"  {str(row['track']):<20} {str(row.get('start_method','?')):<10} n={row['n']}")
else:
    print("  track tai start_method puuttuu horse_starts:sta")

# 4. Onko score-hajonta parempi ennen lämpötilaskalausta?
print("\n=== Score-hajonta: raakapisteet vs. win_prob ===")
import lightgbm as lgb, json
meta  = json.load(open("/home/ravi/app-ravi/data/model_baseline_20260526_meta.json"))
T     = meta["temperature"]
model = lgb.Booster(model_file="/home/ravi/app-ravi/data/model_baseline_20260526.lgb")
from src.models.ranker import predict_win_probabilities
preds_t1  = predict_win_probabilities(model, features, temperature=1.0)
preds_cal = predict_win_probabilities(model, features, temperature=T)
race_stats_t1  = preds_t1.groupby("race_id")["win_prob"].std().reset_index(name="std_T1")
race_stats_cal = preds_cal.groupby("race_id")["win_prob"].std().reset_index(name="std_cal")
merged = race_stats_t1.merge(race_stats_cal, on="race_id")
flat_t1  = (merged["std_T1"]  < 0.04).sum()
flat_cal = (merged["std_cal"] < 0.04).sum()
n = len(merged)
print(f"  T=1.0:   median_std={merged['std_T1'].median():.4f}  tasaisia={flat_t1}/{n}")
print(f"  T={T:.3f}: median_std={merged['std_cal'].median():.4f}  tasaisia={flat_cal}/{n}")
