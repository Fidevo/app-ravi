"""Opeta malli uudelleen nykyisellä FEATURE_COLS:lla ja tallenna."""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")

import os, sqlite3
import pandas as pd
from src.features.build_features import build_feature_matrix, fill_finish_positions
import json
from src.models.ranker import train_ranker, predict_win_probabilities, compute_nll, calibrate_temperature, FEATURE_COLS

def mem_mb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS"):
                return int(line.split()[1]) // 1024
    return 0

print(f"[0] Aloitetaan, RAM={mem_mb()} MB", flush=True)
con = sqlite3.connect("/home/ravi/app-ravi/data/ravit.db")
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
    "  AND (finish_position IS NULL OR finish_position != 99) "
    "  AND (race_date IS NULL OR race_date >= '2024-01-01')", con
)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()
print(f"[1] Data ladattu, RAM={mem_mb()} MB | runners={len(runners)} hs={len(horse_starts)}", flush=True)

features = build_feature_matrix(
    fill_finish_positions(runners), races,
    horse_starts=horse_starts, horses=horses, tracks=tracks,
)
print(f"[2] Features rakennettu, RAM={mem_mb()} MB | features={len(features)}", flush=True)
features["race_date"] = pd.to_datetime(features["race_date"])

split_date = "2026-04-01"
train_df = features[features["race_date"] < split_date].copy()
test_df  = features[features["race_date"] >= split_date].copy()
print(f"Train: {len(train_df)} riviä | Test: {len(test_df)} riviä")
print(f"Piirteitä: {len(FEATURE_COLS)}")

print(f"[3] Train split valmis, RAM={mem_mb()} MB", flush=True)
model = train_ranker(train_df, random_state=42)
print(f"[4] Malli koulutettu, RAM={mem_mb()} MB", flush=True)
out = "/home/ravi/app-ravi/data/model_baseline_20260522.lgb"
model.save_model(out)
print(f"Malli tallennettu: {out}")

# Evaluointi ilman kalibrointia (T=1.0)
preds_raw = predict_win_probabilities(model, test_df)
merged = test_df.merge(preds_raw[["race_id", "horse_id", "win_prob", "score"]], on=["race_id", "horse_id"])
merged["actual_win"] = (merged["finish_position"] == 1).astype(int)
brier_raw = float(((merged["win_prob"] - merged["actual_win"]) ** 2).mean())
print(f"Brier (T=1.0) = {brier_raw:.4f}  NLL={compute_nll(merged):.2f}")

# Temperature-kalibrointi testidatalta
temperature = calibrate_temperature(merged)
print(f"Optimaalinen temperature: {temperature:.4f}")

# Evaluointi kalibroinnin jälkeen
preds_cal = predict_win_probabilities(model, test_df, temperature=temperature)
merged_cal = test_df.merge(preds_cal[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"])
merged_cal["actual_win"] = (merged_cal["finish_position"] == 1).astype(int)
brier_cal = float(((merged_cal["win_prob"] - merged_cal["actual_win"]) ** 2).mean())
print(f"Brier (T={temperature:.4f}) = {brier_cal:.4f}  NLL={compute_nll(merged_cal):.2f}")

# Tallennetaan meta-tiedosto dashboardia varten
meta = {"temperature": temperature, "brier": brier_cal, "brier_uncal": brier_raw,
        "split_date": split_date, "train_rows": len(train_df), "test_rows": len(test_df)}
meta_path = out.replace(".lgb", "_meta.json")
with open(meta_path, "w") as f:
    json.dump(meta, f, indent=2)
print(f"Meta tallennettu: {meta_path}")
