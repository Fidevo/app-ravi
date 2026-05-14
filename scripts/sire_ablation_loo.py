"""
Vaihe 3.7 — Sire-ablation LOO-korjatulla koodilla

Vertailu:
  - Täydellinen malli (sire-piirteet LOO-korjattu, random_state=42)
  - Ilman sire-piirteitä (random_state=42)

Tulkinta auditoijan kaavion mukaan:
  Delta Brier > +0.005  → sire aidosti informatiivinen (LOO-korjaus auttoi)
  Delta Brier > +0.001  → sire hyödyllinen mutta ei dominantti
  Delta Brier ~ 0       → sire silti merkityksetön tällä datamäärällä
"""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")

import sqlite3
import pandas as pd
import numpy as np
from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import (
    FEATURE_COLS,
    train_ranker, predict_win_probabilities, compute_nll,
)

con = sqlite3.connect("/home/ravi/app-ravi/data/ravit.db")
runners = pd.read_sql(
    """
    SELECT r.*, ra.race_date, h.birth_year
    FROM runners r
    JOIN races ra ON r.race_id = ra.race_id
    LEFT JOIN horses h ON r.horse_id = h.horse_id
    """,
    con,
)
races        = pd.read_sql("SELECT * FROM races", con)
horse_starts = pd.read_sql(
    "SELECT * FROM horse_starts WHERE withdrawn != 1 AND finish_position != 99",
    con,
)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()

runners_filled = fill_finish_positions(runners)
features = build_feature_matrix(
    runners_filled, races,
    horse_starts=horse_starts,
    horses=horses,
    tracks=tracks,
)
features["race_date"] = pd.to_datetime(features["race_date"])

split_date = "2026-05-08"
train_df = features[features["race_date"] < split_date].copy()
test_df  = features[features["race_date"] >= split_date].copy()
print(f"Train: {len(train_df)} | Test: {len(test_df)}")

sire_cols_present = [c for c in features.columns if "sire" in c and "lifetime" in c]
print("Sire-sarakkeet featureissa:")
for c in sire_cols_present:
    pct = features[c].notna().mean() * 100
    print(f"  {c}: {pct:.1f}% notna")


def evaluate(model, test, feat_cols):
    preds  = predict_win_probabilities(model, test, feature_cols=feat_cols)
    merged = test.merge(preds[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"])
    merged["actual_win"] = (merged["finish_position"] == 1).astype(int)
    brier = float(((merged["win_prob"] - merged["actual_win"]) ** 2).mean())
    nll   = compute_nll(merged)
    return brier, nll


# --- Täydellinen malli (LOO-korjatut sire-piirteet) ---
print("\nTraining full model (LOO-corrected sire, rs=42)...")
model_full = train_ranker(train_df, random_state=42)
brier_full, nll_full = evaluate(model_full, test_df, FEATURE_COLS)
print(f"Full (LOO, rs=42): Brier={brier_full:.4f}  NLL={nll_full:.2f}")

fi_full = pd.Series(
    model_full.feature_importance(importance_type="gain"),
    index=model_full.feature_name(),
).sort_values(ascending=False)
print("Top-10 (full):", fi_full.head(10).index.tolist())
for check in ["sire_lifetime_win_rate", "dam_sire_lifetime_starts", "track_home_stretch_m"]:
    r = list(fi_full.index).index(check) + 1 if check in fi_full.index else "puuttuu"
    print(f"  {check}: #{r}/{len(fi_full)}")

# --- Ilman sire-piirteitä ---
no_sire_cols = [c for c in FEATURE_COLS if "sire" not in c]
print(f"\nTraining no-sire model ({len(no_sire_cols)} features, rs=42)...")
model_no_sire = train_ranker(train_df, feature_cols=no_sire_cols, random_state=42)
brier_no_sire, nll_no_sire = evaluate(model_no_sire, test_df, no_sire_cols)
print(f"No-sire (rs=42): Brier={brier_no_sire:.4f}  NLL={nll_no_sire:.2f}")

# --- Yhteenveto ---
delta_b = brier_no_sire - brier_full
delta_n = nll_no_sire - nll_full

print("\n=== LOO SIRE-ABLATION YHTEENVETO ===")
print(f"Full (LOO-sire):   Brier={brier_full:.4f}  NLL={nll_full:.2f}")
print(f"Ilman sire:        Brier={brier_no_sire:.4f}  NLL={nll_no_sire:.2f}")
print(f"Delta Brier (no_sire - full): {delta_b:+.4f}  (pos=sire auttaa, neg=ei auta)")
print(f"Delta NLL   (no_sire - full): {delta_n:+.2f}")

if delta_b > 0.005:
    verdict = "SIRE AIDOSTI INFORMATIIVINEN — LOO-korjaus poisti leakagen, sire on hyodyllinen"
elif delta_b > 0.001:
    verdict = "SIRE HYODYLLINEN MUTTA EI DOMINANTTI — LOO auttoi, pita piirteet"
elif delta_b > -0.001:
    verdict = "SIRE MARGINAALINEN — LOO-korjaus ei riittanyt, data liian vahan"
else:
    verdict = "SIRE EDELLEEN EI MERKITSEVA — harkitse poistamista FEATURE_COLS:ista"
print(f"Verdict: {verdict}")
