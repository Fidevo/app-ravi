"""
SHAP-analyysi + A/B-vertailu piirrekarsinnalle.

Ajaa kolme skenaariota ja vertaa Brier-pisteitä:
  A) Kaikki 50 piirrettä (baseline)
  B) Top-20 SHAP-piirrettä
  C) Top-30 SHAP-piirrettä

Tulostaa:
  1. SHAP-ranking kaikille piirteille (mean |SHAP|)
  2. Brier A vs B vs C
  3. Go/no-go suositus karsinnalle
"""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")

import sqlite3
import numpy as np
import pandas as pd
import shap
import lightgbm as lgb
from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import (
    train_ranker, predict_win_probabilities, FEATURE_COLS, CATEGORICAL_COLS,
)

SPLIT_DATE = "2026-05-08"
MODEL_PATH = "/home/ravi/app-ravi/data/model_baseline_20260516.lgb"

# ── Data ────────────────────────────────────────────────────────────────────
print("Ladataan data...", flush=True)
con = sqlite3.connect("/home/ravi/app-ravi/data/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, h.birth_year FROM runners r "
    "JOIN races ra ON r.race_id = ra.race_id "
    "LEFT JOIN horses h ON r.horse_id = h.horse_id", con)
races       = pd.read_sql("SELECT * FROM races", con)
horse_starts = pd.read_sql(
    "SELECT * FROM horse_starts "
    "WHERE (withdrawn IS NULL OR withdrawn != 1) "
    "  AND (finish_position IS NULL OR finish_position != 99)", con)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()

features = build_feature_matrix(
    fill_finish_positions(runners), races,
    horse_starts=horse_starts, horses=horses, tracks=tracks,
)
features["race_date"] = pd.to_datetime(features["race_date"])
train_df = features[features["race_date"] < SPLIT_DATE].copy()
test_df  = features[features["race_date"] >= SPLIT_DATE].copy()
print(f"Train {len(train_df)} | Test {len(test_df)}", flush=True)


def brier(model, df, feature_cols=FEATURE_COLS):
    preds  = predict_win_probabilities(model, df, feature_cols=feature_cols)
    merged = df.merge(preds[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"])
    merged["actual_win"] = (merged["finish_position"] == 1).astype(int)
    return float(((merged["win_prob"] - merged["actual_win"]) ** 2).mean())


# ── A: Baseline — kaikki piirteet ───────────────────────────────────────────
print("\n=== A: Kaikki piirteet ===", flush=True)
model_a = lgb.Booster(model_file=MODEL_PATH)
brier_a = brier(model_a, test_df)
print(f"Brier A = {brier_a:.4f}", flush=True)

# ── SHAP-analyysi ────────────────────────────────────────────────────────────
print("\nLasketaan SHAP-arvot (TreeExplainer)...", flush=True)
from src.models.ranker import _resolve_cols

feat_cols, cat_cols = _resolve_cols(train_df, FEATURE_COLS, CATEGORICAL_COLS, log_missing=True)

# SHAP pred_contrib ei toimi LightGBM-kategorioiden kanssa (SHAP 0.51 bug).
# Kategorisia sarakkeita on 6/50 — jätetään pois SHAP-analyysistä,
# arvioidaan gain-importancella erikseen alla.
# Kaikki 56 piirrettä (num + cat) — kategoriat int-koodeiksi numpy-arrayta varten.
# LightGBM pred_contrib vaatii saman piirremäärän kuin koulutuksessa.
# Numpy-array ohittaa kategoriatyyppitarkistuksen.
all_cols = feat_cols + [c for c in cat_cols if c not in feat_cols]
X_test = test_df[all_cols].copy()
for c in cat_cols:
    if c in X_test.columns:
        X_test[c] = X_test[c].astype("category").cat.codes.astype(float).replace(-1, np.nan)

print(f"SHAP: {len(all_cols)} piirrettä ({len(cat_cols)} kategorista → int-koodeina)", flush=True)

X_test_np = X_test.to_numpy(dtype=np.float64, na_value=np.nan)
explainer   = shap.TreeExplainer(model_a)
shap_values = explainer.shap_values(X_test_np)

mean_abs = np.abs(shap_values).mean(axis=0)
shap_df = (
    pd.DataFrame({"feature": all_cols, "mean_abs_shap": mean_abs})
    .sort_values("mean_abs_shap", ascending=False)
    .reset_index(drop=True)
)

# Gain-importance kategorisille (ei SHAP:ia niille)
gain = model_a.feature_importance(importance_type="gain")
gain_names = model_a.feature_name()
gain_dict = dict(zip(gain_names, gain))
if cat_cols:
    print("\n─── Kategoristen piirteiden gain-importance ───")
    for c in sorted(cat_cols, key=lambda x: gain_dict.get(x, 0), reverse=True):
        print(f"  {c:<42}  gain={gain_dict.get(c, 0):.1f}")

print("\n─── SHAP-ranking (kaikki piirteet) ───")
print(f"{'Rank':>4}  {'Piirre':<42}  {'mean|SHAP|':>10}")
print("─" * 62)
for i, row in shap_df.iterrows():
    print(f"{i+1:>4}  {row['feature']:<42}  {row['mean_abs_shap']:>10.5f}")

# ── B: Top-20 piirrettä ──────────────────────────────────────────────────────
TOP_B = 20
TOP_C = 30

top20 = shap_df["feature"].head(TOP_B).tolist()
top30 = shap_df["feature"].head(TOP_C).tolist()

print(f"\n=== B: Top-{TOP_B} SHAP-piirrettä ===", flush=True)
model_b = train_ranker(train_df, feature_cols=top20, random_state=42)
brier_b = brier(model_b, test_df, feature_cols=top20)
print(f"Brier B = {brier_b:.4f}  (delta {brier_b - brier_a:+.4f})", flush=True)

print(f"\n=== C: Top-{TOP_C} SHAP-piirrettä ===", flush=True)
model_c = train_ranker(train_df, feature_cols=top30, random_state=42)
brier_c = brier(model_c, test_df, feature_cols=top30)
print(f"Brier C = {brier_c:.4f}  (delta {brier_c - brier_a:+.4f})", flush=True)

# ── Yhteenveto ────────────────────────────────────────────────────────────────
print("\n─── Yhteenveto ───────────────────────────────────────────")
print(f"  A) Kaikki {len(feat_cols):>2} piirrettä  → Brier {brier_a:.4f}")
print(f"  B) Top-{TOP_B:<2} piirrettä       → Brier {brier_b:.4f}  ({brier_b - brier_a:+.4f})")
print(f"  C) Top-{TOP_C:<2} piirrettä       → Brier {brier_c:.4f}  ({brier_c - brier_a:+.4f})")

THRESHOLD = 0.002
best = min([(brier_a, "A"), (brier_b, "B"), (brier_c, "C")])
if best[1] == "A":
    print(f"\nSuositus: Pidä kaikki piirteet — karsinta ei paranna mallia.")
elif best[0] < brier_a - THRESHOLD:
    print(f"\nSuositus: Vaihda {best[1]} ({best[0]:.4f}) — parannus >{THRESHOLD:.3f}.")
else:
    print(f"\nSuositus: Ero alle {THRESHOLD:.3f} — karsinnasta ei merkittävää hyötyä.")

# Piirteet joiden SHAP on alle 5 % maksimista
threshold_shap = shap_df["mean_abs_shap"].max() * 0.05
weak = shap_df[shap_df["mean_abs_shap"] < threshold_shap]["feature"].tolist()
if weak:
    print(f"\nHeikot piirteet (mean|SHAP| < 5 % maksimista) — harkitse poistoa:")
    for f in weak:
        v = shap_df.loc[shap_df["feature"] == f, "mean_abs_shap"].values[0]
        print(f"  {f:<42}  {v:.5f}")
