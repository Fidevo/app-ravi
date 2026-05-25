"""Tarkistaa NaN-kattavuuden tasaisissa lähdöissä."""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")
import sqlite3
import pandas as pd
import numpy as np

from src.features.build_features import build_feature_matrix
from src.models.ranker import FEATURE_COLS

FLAT_RACES = ["2026-05-25_15_7", "2026-05-25_22_8"]
TARGET_DATE = "2026-05-25"

con = sqlite3.connect("/home/ravi/app-ravi/data/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, ra.distance, ra.start_method AS race_start_method,"
    " h.birth_year FROM runners r"
    " JOIN races ra ON r.race_id = ra.race_id"
    " LEFT JOIN horses h ON r.horse_id = h.horse_id"
    " WHERE ra.race_date = ?",
    con, params=(TARGET_DATE,),
)
races  = pd.read_sql("SELECT * FROM races WHERE race_date=?", con, params=(TARGET_DATE,))
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

features = build_feature_matrix(runners, races, horse_starts=hs, horses=horses, tracks=tracks)
print(f"Features: {len(features)} riviä\n")

KEY_COLS = [c for c in FEATURE_COLS if c in features.columns]

for rid in FLAT_RACES:
    sub = features[features["race_id"] == rid][KEY_COLS]
    if sub.empty:
        print(f"{rid}: EI DATAA")
        continue

    nan_per_horse = sub.isna().mean(axis=1) * 100
    nan_per_feat  = sub.isna().mean() * 100

    print(f"=== {rid} | {len(sub)} hevosta | {len(KEY_COLS)} piirrettä ===")
    print(f"Avg NaN% per hevonen: {nan_per_horse.mean():.1f}%"
          f"  min={nan_per_horse.min():.1f}%  max={nan_per_horse.max():.1f}%")

    print("\nPiirteet joissa >50% hevosista puuttuu:")
    bad = nan_per_feat[nan_per_feat > 50].sort_values(ascending=False)
    if bad.empty:
        print("  (ei yhtään)")
    for col, v in bad.items():
        print(f"  {col:<42} {v:.0f}%")

    print("\nPiirteet joissa kaikki sama arvo (nolla erottelu):")
    same = [c for c in KEY_COLS if sub[c].nunique(dropna=False) <= 1]
    if not same:
        print("  (ei yhtään)")
    for c in same:
        print(f"  {c}")
    print()
