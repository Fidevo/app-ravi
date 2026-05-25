"""Tarkista tämän päivän ennusteet — tasaisuusdiagnostiikka per lähtö."""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")
import sqlite3, json
import pandas as pd
import numpy as np
import lightgbm as lgb

from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import predict_win_probabilities

TARGET_DATE = "2026-05-25"

con = sqlite3.connect("/home/ravi/app-ravi/data/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, ra.distance, ra.start_method AS race_start_method,"
    " h.birth_year FROM runners r"
    " JOIN races ra ON r.race_id = ra.race_id"
    " LEFT JOIN horses h ON r.horse_id = h.horse_id"
    f" WHERE ra.race_date = '{TARGET_DATE}'",
    con,
)
races  = pd.read_sql(f"SELECT * FROM races WHERE race_date='{TARGET_DATE}'", con)
hs     = pd.read_sql(
    "SELECT * FROM horse_starts"
    " WHERE (withdrawn IS NULL OR withdrawn != 1)"
    "   AND (finish_position IS NULL OR finish_position != 99)"
    "   AND (race_date IS NULL OR race_date >= '2024-01-01')",
    con,
)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()

if "start_method" not in runners.columns:
    runners = runners.rename(columns={"race_start_method": "start_method"})
elif "race_start_method" in runners.columns:
    runners["start_method"] = runners["start_method"].fillna(runners["race_start_method"])
    runners = runners.drop(columns=["race_start_method"])

print(f"Runners: {len(runners)}, Races: {len(races)}", flush=True)

features = build_feature_matrix(runners, races, horse_starts=hs, horses=horses, tracks=tracks)
features["race_date"] = pd.to_datetime(features["race_date"])
print(f"Features rakennettu: {len(features)} riviä", flush=True)

meta  = json.load(open("/home/ravi/app-ravi/data/model_baseline_20260524_meta.json"))
T     = meta["temperature"]
model = lgb.Booster(model_file="/home/ravi/app-ravi/data/model_baseline_20260524.lgb")

preds = predict_win_probabilities(model, features, temperature=T)
name_map = features[["race_id", "horse_id", "horse_name"]].drop_duplicates() \
    if "horse_name" in features.columns else None
if name_map is not None:
    preds = preds.merge(name_map, on=["race_id", "horse_id"], how="left")

# Näytä kaikki lähdöt
race_ids = preds["race_id"].unique()
print(f"\nLähtöjä yhteensä: {len(race_ids)}\n")

stds = []
for race_id in race_ids:
    r = preds[preds["race_id"] == race_id].sort_values("win_prob", ascending=False)
    ri = races[races["race_id"] == race_id].iloc[0]
    track   = ri.get("track", "")
    rnum    = ri.get("race_number", "")
    dist    = ri.get("distance", "")
    smethod = ri.get("start_method", "")
    std = float(r["win_prob"].std())
    top1 = float(r["win_prob"].max())
    stds.append(std)

    flag = "  ⚠️ TASAINEN" if std < 0.04 else ""
    print(f"Lähtö {rnum:>2} | {track:<12} | {dist}m {smethod:<5} | "
          f"top1={top1:.1%}  std={std:.4f}  n={len(r)}{flag}")
    for _, row in r.iterrows():
        name = str(row.get("horse_name", row["horse_id"]))[:28]
        print(f"         {name:<28} {row['win_prob']:>7.1%}  score={row['score']:>7.3f}")

print(f"\nMedian std: {np.median(stds):.4f}")
print(f"Tasaisia (std<0.04): {sum(s < 0.04 for s in stds)}/{len(stds)}")
