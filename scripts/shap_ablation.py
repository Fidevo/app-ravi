"""
SHAP-analyysi ja ablation-vertailu: 37 vs. 58 piirrettä.

Ajaa palvelimella:
  cd /home/ravi/app-ravi
  PYTHONPATH=/home/ravi/app-ravi .venv/bin/python scripts/shap_ablation.py

Tulostaa:
  1. SHAP mean |value| top-20 piirtettä (rehellinen tärkeys, ei gain-biased)
  2. Ablation: 37- vs. 58-piirremallin Brier ja NLL samalla testisetillä
  3. Uusien 21 piirteen ryhmittäinen hyöty
"""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")

import json
import os
import sqlite3

import lightgbm as lgb
import numpy as np
import pandas as pd
import shap

from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import (
    CATEGORICAL_COLS,
    FEATURE_COLS,
    calibrate_temperature,
    compute_nll,
    predict_win_probabilities,
    train_ranker,
)

# ---------------------------------------------------------------------------
# 37-piirteen baseline (ennen B2 / C / D -sarjoja)
# ---------------------------------------------------------------------------
FEATURE_37: list[str] = [
    # Muoto (9 peruspiirrettä)
    "form_avg_finish_5",
    "form_win_rate_5",
    "form_top3_rate_5",
    "form_avg_km_time_5",
    "form_best_km_time_5",
    "form_ewm_km_time",
    "last_race_had_gallop",
    "form_market_avg_5",
    "form_days_since_last",
    # ATG-aggregaatit (4)
    "atg_lifetime_win_rate",
    "atg_lifetime_top3_rate",
    "atg_lifetime_starts",
    "atg_best_km_for_this_setup",
    # Kuski/valmentaja 365d (5)
    "driver_win_rate_365d",
    "driver_starts_365d",
    "driver_top3_rate_365d",
    "trainer_win_rate_365d",
    "trainer_top3_rate_365d",
    # Kuski/valmentaja 60d horse_starts (4)
    "driver_win_rate_60d",
    "driver_top3_rate_60d",
    "trainer_win_rate_60d",
    "trainer_top3_rate_60d",
    # Lähtöasetelma (6)
    "inside_post",
    "back_row",
    "handicap_meters",
    "post_pos_norm",
    "track_horse_starts",
    "track_horse_win_rate",
    # Lähdön luokka (3)
    "race_min_earnings",
    "race_max_earnings",
    "prev_prize_won",
    # Kengät/sulky/kuski (4)
    "shoes_changed_front",
    "shoes_changed_back",
    "sulky_changed",
    "driver_quality_signal",
    # Johdetut (2)
    "barfota_law_active",
    "horse_age",
]
assert len(FEATURE_37) == 37, f"Odotettiin 37, saatiin {len(FEATURE_37)}"

# Uudet 21 piirrettä ryhmittäin
NEW_FEATURE_GROUPS = {
    "B2 – segmentoitu muoto": [
        "form_avg_finish_5_same_method",
        "form_avg_finish_5_same_dist",
    ],
    "C2 – starttipaikan historia": ["start_position_win_rate"],
    "C3 – starttimuoto-diff": ["start_method_win_rate_diff"],
    "C4 – kuski×rata / valmentaja×rata": [
        "driver_track_win_rate_60d",
        "trainer_track_win_rate_60d",
    ],
    "C5 – trendit + rataolo": [
        "km_time_trend",
        "prize_money_trend",
        "track_condition_win_rate",
    ],
    "muutospiirteet": ["driver_changed", "distance_change_m"],
    "D – ratarakenne": [
        "track_length_total",
        "track_home_stretch_m",
        "track_open_stretch",
        "track_angled_wing",
        "track_width_1",
        "track_width_2",
        "track_dosage",
    ],
    "C6 – luokkakohtainen muoto": [
        "form_win_rate_5_same_class",
        "form_avg_finish_5_same_class",
        "form_avg_km_time_5_same_class",
    ],
}

# ---------------------------------------------------------------------------
# 1. Data
# ---------------------------------------------------------------------------
def mem_mb() -> int:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0

print(f"[0] Ladataan data  RAM={mem_mb()} MB", flush=True)
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
    "  AND (race_date IS NULL OR race_date >= '2024-01-01')",
    con,
)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()
print(f"[1] Data ladattu   RAM={mem_mb()} MB | runners={len(runners)} hs={len(horse_starts)}", flush=True)

# ---------------------------------------------------------------------------
# 2. Feature-matriisi
# ---------------------------------------------------------------------------
features = build_feature_matrix(
    fill_finish_positions(runners), races,
    horse_starts=horse_starts, horses=horses, tracks=tracks,
)
features["race_date"] = pd.to_datetime(features["race_date"])
print(f"[2] Features       RAM={mem_mb()} MB | {len(features)} riviä", flush=True)

SPLIT_DATE = "2026-04-01"
train_df = features[features["race_date"] < SPLIT_DATE].copy()
test_df  = features[features["race_date"] >= SPLIT_DATE].copy()
print(f"    Train {len(train_df)} | Test {len(test_df)}", flush=True)

# ---------------------------------------------------------------------------
# 3. Lataa nykyinen 58-piirremalli
# ---------------------------------------------------------------------------
MODEL_PATH = "/home/ravi/app-ravi/data/model_baseline_20260521.lgb"
META_PATH  = MODEL_PATH.replace(".lgb", "_meta.json")

model_58 = lgb.Booster(model_file=MODEL_PATH)
with open(META_PATH) as f:
    meta = json.load(f)
T_58 = meta["temperature"]
print(f"[3] 58-piirremalli ladattu  T={T_58:.4f}", flush=True)

# Evaluoi testisetillä
preds_58 = predict_win_probabilities(model_58, test_df, temperature=T_58)
merged_58 = test_df.merge(
    preds_58[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"]
)
merged_58["actual_win"] = (merged_58["finish_position"] == 1).astype(int)
brier_58 = float(((merged_58["win_prob"] - merged_58["actual_win"]) ** 2).mean())
nll_58   = compute_nll(merged_58)
print(f"    58-piirre: Brier={brier_58:.4f}  NLL={nll_58:.2f}", flush=True)

# ---------------------------------------------------------------------------
# 4. Kouluta 37-piirremalli samalla treenisetillä
# ---------------------------------------------------------------------------
print(f"[4] Koulutetaan 37-piirremalli  RAM={mem_mb()} MB", flush=True)
model_37 = train_ranker(train_df, feature_cols=FEATURE_37, random_state=42)
print(f"    Valmis  RAM={mem_mb()} MB", flush=True)

preds_37_raw = predict_win_probabilities(
    model_37, test_df, feature_cols=FEATURE_37, temperature=1.0
)
merged_37_raw = test_df.merge(
    preds_37_raw[["race_id", "horse_id", "win_prob", "score"]], on=["race_id", "horse_id"]
)
merged_37_raw["actual_win"] = (merged_37_raw["finish_position"] == 1).astype(int)
T_37 = calibrate_temperature(merged_37_raw)

preds_37 = predict_win_probabilities(
    model_37, test_df, feature_cols=FEATURE_37, temperature=T_37
)
merged_37 = test_df.merge(
    preds_37[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"]
)
merged_37["actual_win"] = (merged_37["finish_position"] == 1).astype(int)
brier_37 = float(((merged_37["win_prob"] - merged_37["actual_win"]) ** 2).mean())
nll_37   = compute_nll(merged_37)
print(f"    37-piirre: Brier={brier_37:.4f}  NLL={nll_37:.2f}  T={T_37:.4f}", flush=True)

# ---------------------------------------------------------------------------
# 5. SHAP – rehellinen piirretärkeys (58-piirremalli, max 2000 testiriviä)
# ---------------------------------------------------------------------------
print(f"[5] SHAP-analyysi  RAM={mem_mb()} MB", flush=True)

# Rakenna X testisetistä (sama logiikka kuin predict_win_probabilities)
from src.models.ranker import _resolve_cols

avail_feat, avail_cat = _resolve_cols(test_df, FEATURE_COLS, CATEGORICAL_COLS, log_missing=False)
_cat_set = set(avail_cat)
avail_feat_only = [c for c in avail_feat if c not in _cat_set]
X_test = test_df[avail_feat_only + avail_cat].copy()
for col in avail_cat:
    X_test[col] = X_test[col].astype("category")

# Rajoita 2000 riviin muistin säästämiseksi
rng = np.random.default_rng(42)
sample_idx = rng.choice(len(X_test), size=min(2000, len(X_test)), replace=False)
X_shap = X_test.iloc[sample_idx].reset_index(drop=True)

explainer   = shap.TreeExplainer(model_58)
shap_values = explainer.shap_values(X_shap)
print(f"    SHAP valmis  RAM={mem_mb()} MB  shape={shap_values.shape}", flush=True)

# mean |SHAP| per piirre
mean_abs_shap = np.abs(shap_values).mean(axis=0)
feature_names = avail_feat_only + avail_cat
shap_df = (
    pd.DataFrame({"feature": feature_names, "mean_abs_shap": mean_abs_shap})
    .sort_values("mean_abs_shap", ascending=False)
    .reset_index(drop=True)
)

# ---------------------------------------------------------------------------
# 6. Ryhmittäinen SHAP-hyöty uusille piirteille
# ---------------------------------------------------------------------------
# HUOM: _cls lisätään features-DataFrameen ENNEN train/test-slicejä jotta
# per-luokka-kattavuusanalyysi toimii train-subsetissä ilman KeyError.
_bins   = [0, 25_000, 75_000, 200_000, float("inf")]
_labels = ["low", "medium", "high", "elite"]
if "race_min_earnings" in features.columns:
    features["_cls"] = pd.cut(
        features["race_min_earnings"], bins=_bins, labels=_labels,
        right=True, include_lowest=True,
    )
    train_cls = features[features["race_date"] < SPLIT_DATE]
    c6_col = "form_avg_km_time_5_same_class"
    print(f"\n--- C6-kattavuusdiagnostiikka ---")
    print(f"  {c6_col} notna%: koko data {features[c6_col].notna().mean()*100:.1f}%"
          f"  | train {train_cls[c6_col].notna().mean()*100:.1f}%")
    seg_counts = (
        features.dropna(subset=["race_min_earnings"])
        .groupby(["horse_id", "_cls"], observed=True)
        .size()
    )
    print(f"  Segmenttikoko (horse×luokka): mediaani={seg_counts.median():.1f}"
          f"  Q1={seg_counts.quantile(0.25):.1f}  Q3={seg_counts.quantile(0.75):.1f}")
    print(f"  n=1: {(seg_counts==1).mean()*100:.1f}%"
          f"  | n≤3: {(seg_counts<=3).mean()*100:.1f}%"
          f"  | n≥5: {(seg_counts>=5).mean()*100:.1f}%")
    print(f"  {c6_col} notna% per luokka (train):")
    for lbl in _labels:
        sub = train_cls[train_cls["_cls"] == lbl]
        if len(sub) > 0:
            pct = sub[c6_col].notna().mean() * 100
            print(f"    {lbl:8s}: {pct:.1f}%  (n={len(sub)})")
    features = features.drop(columns=["_cls"])

group_shap: dict[str, float] = {}
for group_name, cols in NEW_FEATURE_GROUPS.items():
    vals = shap_df[shap_df["feature"].isin(cols)]["mean_abs_shap"].sum()
    group_shap[group_name] = float(vals)

# ---------------------------------------------------------------------------
# 7. Tulosta tulokset
# ---------------------------------------------------------------------------
print("\n" + "="*60)
print("ABLATION: 37 vs. 58 piirrettä")
print("="*60)
print(f"{'Malli':<20} {'Brier':>8} {'NLL':>10} {'T':>8}")
print(f"{'-'*50}")
print(f"{'37-piirre (baseline)':<20} {brier_37:>8.4f} {nll_37:>10.2f} {T_37:>8.4f}")
print(f"{'58-piirre (nykyinen)':<20} {brier_58:>8.4f} {nll_58:>10.2f} {T_58:>8.4f}")
print(f"{'Δ (58-37)':<20} {brier_58-brier_37:>+8.4f} {nll_58-nll_37:>+10.2f}")
print()

print("SHAP top-20 piirrettä (mean |SHAP|, 58-piirremalli):")
print(f"  {'#':<4} {'Piirre':<40} {'mean|SHAP|':>12}")
print(f"  {'-'*58}")
for i, row in shap_df.head(20).iterrows():
    marker = " ← NEW" if row["feature"] in set(
        f for g in NEW_FEATURE_GROUPS.values() for f in g
    ) else ""
    print(f"  {i+1:<4} {row['feature']:<40} {row['mean_abs_shap']:>12.4f}{marker}")

print()
print("SHAP ryhmittäin (uudet 21 piirrettä):")
print(f"  {'Ryhmä':<40} {'∑ mean|SHAP|':>14}")
print(f"  {'-'*56}")
for grp, val in sorted(group_shap.items(), key=lambda x: -x[1]):
    print(f"  {grp:<40} {val:>14.4f}")

print()
print("SHAP kaikki piirteet (laskeva):")
print(f"  {'#':<4} {'Piirre':<40} {'mean|SHAP|':>12}")
print(f"  {'-'*58}")
for i, row in shap_df.iterrows():
    marker = " ← NEW" if row["feature"] in set(
        f for g in NEW_FEATURE_GROUPS.values() for f in g
    ) else ""
    print(f"  {i+1:<4} {row['feature']:<40} {row['mean_abs_shap']:>12.4f}{marker}")

# ---------------------------------------------------------------------------
# 8. Tallenna JSON-tulos
# ---------------------------------------------------------------------------
result = {
    "split_date": SPLIT_DATE,
    "train_rows": len(train_df),
    "test_rows": len(test_df),
    "model_37": {"brier": brier_37, "nll": nll_37, "temperature": T_37, "n_features": 37},
    "model_58": {"brier": brier_58, "nll": nll_58, "temperature": T_58, "n_features": 58},
    "delta": {
        "brier": round(brier_58 - brier_37, 6),
        "nll": round(nll_58 - nll_37, 2),
    },
    "shap_top20": shap_df.head(20).to_dict(orient="records"),
    "shap_all": shap_df.to_dict(orient="records"),
    "shap_group_sum": group_shap,
}
out_path = "/home/ravi/app-ravi/data/shap_ablation_20260522.json"
with open(out_path, "w") as f:
    json.dump(result, f, indent=2)
print(f"\nTulos tallennettu: {out_path}")
print(f"Loppu RAM={mem_mb()} MB")
