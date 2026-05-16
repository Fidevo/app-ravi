"""Evaluoi malli erikseen kaikilla lähdöillä ja V-pelilähdöillä."""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")

import sqlite3
import pandas as pd
import lightgbm as lgb
from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import predict_win_probabilities, compute_nll, FEATURE_COLS

MODEL_PATH = "/home/ravi/app-ravi/data/model_baseline_20260515.lgb"
DB_PATH = "/home/ravi/app-ravi/data/ravit.db"
TEST_START = "2026-05-08"

con = sqlite3.connect(DB_PATH)
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, h.birth_year FROM runners r "
    "JOIN races ra ON r.race_id = ra.race_id "
    "LEFT JOIN horses h ON r.horse_id = h.horse_id",
    con,
)
races = pd.read_sql("SELECT * FROM races", con)
horse_starts = pd.read_sql(
    "SELECT * FROM horse_starts "
    "WHERE (withdrawn IS NULL OR withdrawn != 1) "
    "  AND (finish_position IS NULL OR finish_position != 99)", con
)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()

features = build_feature_matrix(
    fill_finish_positions(runners), races,
    horse_starts=horse_starts, horses=horses, tracks=tracks,
)
features["race_date"] = pd.to_datetime(features["race_date"])
test_df = features[features["race_date"] >= TEST_START].copy()

model = lgb.Booster(model_file=MODEL_PATH)
preds = predict_win_probabilities(model, test_df)
merged = test_df.merge(preds[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"])
merged["actual_win"] = (merged["finish_position"] == 1).astype(int)

def evaluate(df, label):
    brier = float(((df["win_prob"] - df["actual_win"]) ** 2).mean())
    nll = compute_nll(df)
    n_races = df["race_id"].nunique()
    n_runners = len(df)
    print(f"\n{label}")
    print(f"  Brier:    {brier:.4f}")
    print(f"  NLL:      {nll:.2f}")
    print(f"  Lähtöjä:  {n_races}")
    print(f"  Runnereita: {n_runners}")
    return brier

# Kaikki lähdöt
b_all = evaluate(merged, "Kaikki lähdöt")

# V-pelilähdöt
if "is_v_race" in merged.columns:
    v_merged = merged[merged["is_v_race"] == 1]
    if len(v_merged) > 0:
        b_v = evaluate(v_merged, "Vain V-pelilähdöt (is_v_race=1)")
    else:
        print("\nVain V-pelilähdöt: ei dataa (is_v_race=0 kaikilla)")
else:
    print("\nVain V-pelilähdöt: is_v_race-sarake puuttuu")

print(f"\nMallitiedosto: {MODEL_PATH}")
print(f"Piirteitä: {len(FEATURE_COLS)} (FEATURE_COLS) / {len(model.feature_name())} (mallin opetuspiirteet)")
