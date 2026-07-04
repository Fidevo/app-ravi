"""Sovita T ja blend-α koko post-train OOS-ikkunaan (2026-06-15..07-05) ja
päivitä model_baseline_20260704_meta.json. Malli treenattu < 2026-06-15 →
koko ikkuna on OOS mallille. 10 pv cal-ikkuna (T=1.83) oli liian pieni:
testissä top-pick osui 23.7 % mutta median top1-prob oli vain 15.8 % →
alikalibroitu (liian tasainen)."""
import sys; sys.path.insert(0, "/home/ravi/app-ravi")
import sqlite3, json
import pandas as pd, numpy as np
import lightgbm as lgb
from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import (predict_win_probabilities, calibrate_temperature,
                               fit_blend_alpha)

START, END = "2026-06-15", "2026-07-05"
DATA_DIR = "/home/ravi/app-ravi/data"

con = sqlite3.connect(f"{DATA_DIR}/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, ra.distance, ra.start_method AS race_start_method,"
    " h.birth_year FROM runners r JOIN races ra ON r.race_id = ra.race_id"
    " LEFT JOIN horses h ON r.horse_id = h.horse_id"
    " WHERE ra.race_date >= ? AND ra.race_date < ?", con, params=(START, END))
races = pd.read_sql("SELECT * FROM races WHERE race_date >= ? AND race_date < ?", con, params=(START, END))
races_all = pd.read_sql("SELECT * FROM races", con)
hs = pd.read_sql(
    "SELECT * FROM horse_starts WHERE (withdrawn IS NULL OR withdrawn != 1)"
    " AND (finish_position IS NULL OR finish_position != 99)"
    " AND (race_date IS NULL OR race_date >= '2024-01-01')", con)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()

if "start_method" not in runners.columns:
    runners = runners.rename(columns={"race_start_method": "start_method"})
elif "race_start_method" in runners.columns:
    runners["start_method"] = runners["start_method"].fillna(runners["race_start_method"])
    runners = runners.drop(columns=["race_start_method"])

features = build_feature_matrix(
    fill_finish_positions(runners), races,
    horse_starts=hs, horses=horses, tracks=tracks, all_races=races_all)

model = lgb.Booster(model_file=f"{DATA_DIR}/model_baseline_20260704.lgb")
preds = predict_win_probabilities(model, features)  # T=1 → raw scoret talteen
m = preds.merge(features[["race_id", "horse_id", "finish_position"]], on=["race_id", "horse_id"])
m = m[m["finish_position"].notna()]
T = calibrate_temperature(m)
print(f"T (koko OOS {START}..{END}, {m['race_id'].nunique()} lähtöä): {T:.4f}")

preds_T = predict_win_probabilities(model, features, temperature=T)
mt = preds_T.merge(features[["race_id", "horse_id", "finish_position"]], on=["race_id", "horse_id"])
mt = mt.merge(runners[["race_id", "horse_id", "win_odds_final"]].rename(
    columns={"win_odds_final": "win_odds"}), on=["race_id", "horse_id"], how="left")
mt = mt[mt["finish_position"].notna()]
alpha = fit_blend_alpha(mt)
print(f"blend-α: {alpha:.4f}")

mt["actual_win"] = (mt["finish_position"] == 1).astype(int)
brier = float(((mt["win_prob"] - mt["actual_win"]) ** 2).mean())
rs = mt.groupby("race_id")["win_prob"].agg(["std", "max"])
print(f"Brier (T={T:.3f}): {brier:.4f} | med_std={rs['std'].median():.4f} med_top1={rs['max'].median():.4f} flat={float((rs['std']<0.03).mean()*100):.1f}%")

meta_path = f"{DATA_DIR}/model_baseline_20260704_meta.json"
meta = json.load(open(meta_path))
meta["temperature"] = T
meta["blend_alpha"] = alpha
meta["calibration"] = f"T+alpha refit koko OOS-ikkunaan {START}..{END} (malli treenattu < 2026-06-15); Brier-kenttä samalta ikkunalta"
meta["brier"] = brier
meta["flatness"] = {"median_std": float(rs["std"].median()), "mean_std": float(rs["std"].mean()),
                    "median_top1": float(rs["max"].median()), "mean_top1": float(rs["max"].mean()),
                    "flat_pct": float((rs["std"]<0.03).mean()*100)}
json.dump(meta, open(meta_path, "w"), indent=2)
print("Meta päivitetty:", meta_path)
