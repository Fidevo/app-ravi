"""
Täydellinen baseline-treenaus kaikilla parametreilla:
  horse_starts, horses (sire-piirteet!), tracks, birth_year JOIN

Korjaus: aiemmista ajoista puuttui horses=horses -> sire-piirteet eivät laskettu.
"""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")

import sqlite3
import pandas as pd
import numpy as np
from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import (
    FEATURE_COLS, CATEGORICAL_COLS,
    train_ranker, predict_win_probabilities, compute_nll,
)

con = sqlite3.connect("/home/ravi/app-ravi/data/ravit.db")

# birth_year JOIN + horses-taulu erikseen sire-piirteitä varten
runners = pd.read_sql(
    """
    SELECT r.*, ra.race_date, h.birth_year
    FROM runners r
    JOIN races ra ON r.race_id = ra.race_id
    LEFT JOIN horses h ON r.horse_id = h.horse_id
    """,
    con,
)
races = pd.read_sql("SELECT * FROM races", con)
horse_starts = pd.read_sql(
    "SELECT * FROM horse_starts WHERE withdrawn != 1 AND finish_position != 99",
    con,
)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()

by_col = "birth_year"
sire_col = "sire"
print(f"birth_year notna: {runners[by_col].notna().mean()*100:.1f}%")
print(f"horses rows: {len(horses)} | sire notna: {horses[sire_col].notna().mean()*100:.1f}%")

runners_filled = fill_finish_positions(runners)

# Kaikki parametrit mukana
features = build_feature_matrix(
    runners_filled, races,
    horse_starts=horse_starts,
    horses=horses,
    tracks=tracks,
)
features["race_date"] = pd.to_datetime(features["race_date"])

ha_col = "horse_age"
ha_pct = features[ha_col].notna().mean() * 100 if ha_col in features.columns else 0.0
print(f"horse_age notna: {ha_pct:.1f}%")

sire_check_cols = [
    "sire_lifetime_starts", "sire_lifetime_win_rate",
    "dam_sire_lifetime_starts", "dam_sire_lifetime_win_rate",
]
print("Sire-piirteiden notna%:")
for c in sire_check_cols:
    pct2 = features[c].notna().mean() * 100 if c in features.columns else -1.0
    print(f"  {c}: {pct2:.1f}%")

split_date = "2026-05-08"
train_df = features[features["race_date"] < split_date].copy()
test_df  = features[features["race_date"] >= split_date].copy()
print(f"\nTrain: {len(train_df)} rows | Test: {len(test_df)} rows")

print("\nTraining full model (horse_starts + horses + tracks + birth_year)...")
model = train_ranker(train_df)

preds  = predict_win_probabilities(model, test_df)
merged = test_df.merge(preds[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"])
merged["actual_win"] = (merged["finish_position"] == 1).astype(int)
brier = float(((merged["win_prob"] - merged["actual_win"]) ** 2).mean())
nll   = compute_nll(merged)
print(f"Brier={brier:.4f}  NLL={nll:.2f}")

fi_names = model.feature_name()
fi_vals  = model.feature_importance(importance_type="gain")
fi = pd.Series(fi_vals, index=fi_names).sort_values(ascending=False)

print("\nTop-15 piirteet (gain):")
for rank, (feat, val) in enumerate(fi.head(15).items(), 1):
    print(f"  {rank:2d}. {feat} ({val:.0f})")

check_feats = [
    "track_home_stretch_m", "horse_age",
    "sire_lifetime_win_rate", "dam_sire_lifetime_starts",
]
print("\nKohdennettuja tarkistuksia:")
for check in check_feats:
    r = list(fi.index).index(check) + 1 if check in fi.index else "puuttuu"
    print(f"  {check}: #{r}/{len(fi)}")

print("\n=== LOPPUTULOS ===")
print(f"Brier={brier:.4f}  NLL={nll:.2f}")
print("Odotus: sire_lifetime_win_rate ja dam_sire_lifetime_starts top-15:ssa")
