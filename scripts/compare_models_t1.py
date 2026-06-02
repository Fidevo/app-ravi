"""Vertaa 56- ja 46-piirteen malleja T=1.0:lla tänäiseen dataan.

Tavoite: selvittää onko tasaisuusero piirteiden vai T:n aiheuttama.
"""
import sys; sys.path.insert(0, "/home/ravi/app-ravi")
import sqlite3, json, glob
import pandas as pd, numpy as np
import lightgbm as lgb
from src.features.build_features import build_feature_matrix
from src.models.ranker import predict_win_probabilities

TARGET_DATE = "2026-06-01"
DATA_DIR = "/home/ravi/app-ravi/data"

con = sqlite3.connect(f"{DATA_DIR}/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, ra.distance, ra.start_method AS race_start_method,"
    " h.birth_year FROM runners r JOIN races ra ON r.race_id = ra.race_id"
    " LEFT JOIN horses h ON r.horse_id = h.horse_id"
    f" WHERE ra.race_date = '{TARGET_DATE}'", con)
races     = pd.read_sql(f"SELECT * FROM races WHERE race_date='{TARGET_DATE}'", con)
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

spwr_files = sorted(glob.glob(f"{DATA_DIR}/model_baseline_*_spwr_lookup.csv"))
spwr_lookup = pd.read_csv(spwr_files[-1]) if spwr_files else None

features = build_feature_matrix(runners, races, horse_starts=hs, horses=horses, tracks=tracks,
                                  spwr_lookup=spwr_lookup, all_races=races_all)
print(f"Features: {len(features)} riviä, {features['race_id'].nunique()} lähtöä")

models = [
    ("46-piirteen (20260601)", f"{DATA_DIR}/model_baseline_20260601.lgb"),
    ("56-piirteen (20260526)", f"{DATA_DIR}/model_baseline_20260526.lgb"),
]

for label, path in models:
    try:
        m = lgb.Booster(model_file=path)
    except Exception as e:
        print(f"\n{label}: VIRHE — {e}")
        continue

    for T in [1.0, 0.63]:
        preds = predict_win_probabilities(m, features, temperature=T)
        race_stds = preds.groupby("race_id")["win_prob"].std()
        flat = int((race_stds < 0.04).sum())
        n = len(race_stds)
        print(f"\n{label} | T={T:.2f}")
        print(f"  Median std: {race_stds.median():.4f}  Mean: {race_stds.mean():.4f}")
        print(f"  Tasaisia (std<0.04): {flat}/{n}  ({100*flat/n:.0f}%)")
        print(f"  p25={race_stds.quantile(.25):.4f}  p75={race_stds.quantile(.75):.4f}  max={race_stds.max():.4f}")
