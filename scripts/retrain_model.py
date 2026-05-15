"""Opeta malli uudelleen nykyisellä FEATURE_COLS:lla ja tallenna."""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")

import sqlite3
import pandas as pd
from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import train_ranker, predict_win_probabilities, compute_nll, FEATURE_COLS

con = sqlite3.connect("/home/ravi/app-ravi/data/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, h.birth_year FROM runners r "
    "JOIN races ra ON r.race_id = ra.race_id "
    "LEFT JOIN horses h ON r.horse_id = h.horse_id",
    con,
)
races = pd.read_sql("SELECT * FROM races", con)
horse_starts = pd.read_sql(
    "SELECT * FROM horse_starts WHERE withdrawn != 1 AND finish_position != 99", con
)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()

features = build_feature_matrix(
    fill_finish_positions(runners), races,
    horse_starts=horse_starts, horses=horses, tracks=tracks,
)
features["race_date"] = pd.to_datetime(features["race_date"])

split_date = "2026-05-08"
train_df = features[features["race_date"] < split_date].copy()
test_df  = features[features["race_date"] >= split_date].copy()
print(f"Train: {len(train_df)} riviä | Test: {len(test_df)} riviä")
print(f"Piirteitä: {len(FEATURE_COLS)}")

model = train_ranker(train_df, random_state=42)
out = "/home/ravi/app-ravi/data/model_baseline_20260515.lgb"
model.save_model(out)
print(f"Malli tallennettu: {out}")

preds = predict_win_probabilities(model, test_df)
merged = test_df.merge(preds[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"])
merged["actual_win"] = (merged["finish_position"] == 1).astype(int)
brier = float(((merged["win_prob"] - merged["actual_win"]) ** 2).mean())
nll = compute_nll(merged)
print(f"Brier={brier:.4f}  NLL={nll:.2f}")
