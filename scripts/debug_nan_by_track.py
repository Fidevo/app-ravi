"""Tarkista NaN-kattavuus raidekohtaisesti tänään (2026-05-25)."""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")
import sqlite3
import pandas as pd
import numpy as np
from src.features.build_features import build_feature_matrix
from src.models.ranker import FEATURE_COLS

TARGET_DATE = "2026-05-25"
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

print(f"horse_starts raidat: {sorted(hs['track'].dropna().unique())[:20]}")
print(f"races raidat tänään: {sorted(races['track'].dropna().unique())}")
print()

for track_name in ["Mantorp", "Farjestad", "Färjestad"]:
    sub = features[features["track"].str.lower().str.contains(track_name.lower(), na=False)]
    if sub.empty:
        print(f"{track_name}: ei dataa features-taulukossa")
        continue
    nan_rates = sub[KEY_COLS].isna().mean() * 100
    bad = nan_rates[nan_rates >= 80].sort_values(ascending=False)
    print(f"\n=== {track_name} ({len(sub)} hevosta, {sub['race_id'].nunique()} lähtöä) ===")
    if bad.empty:
        print("  Ei featuria >= 80% NaN")
    else:
        print("Features joissa >= 80% NaN:")
        for col, v in bad.items():
            print(f"  {col:<45} {v:.0f}%")

# Tarkista start_position_win_rate erikseen
print("\n--- start_position_win_rate NaN per raita ---")
for track_name in features["track"].dropna().unique():
    sub = features[features["track"] == track_name]
    spwr_nan = sub["start_position_win_rate"].isna().mean() * 100 if "start_position_win_rate" in sub.columns else -1
    print(f"  {track_name:<20} start_position_win_rate NaN: {spwr_nan:.0f}%  ({len(sub)} hevosta)")

# horse_starts pool koko per raita
print("\n--- horse_starts pool per raita (auto/volte) ---")
pool_tracks = hs.groupby(["track", "start_method"]).size().reset_index(name="n")
for _, row in pool_tracks.sort_values("track").iterrows():
    print(f"  {str(row['track']):<20} {str(row.get('start_method','?')):<10} n={row['n']}")
