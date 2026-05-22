"""
Sparsity-diagnostiikka segmentoitujen piirteiden kandidaateille.

Ajaa palvelimella:
  cd /home/ravi/app-ravi
  PYTHONPATH=/home/ravi/app-ravi .venv/bin/python scripts/sparsity_diagnostics.py

Päätösmatriisi (auditoijan suositus 22.5.2026):
  SHAP >= 0.05 JA sparse% > 30 → KYNNYS (piirre on hyödyllinen mutta kohinainen)
  SHAP < 0.02              → POISTA (kohinaa, ei kannata pelastaa)
  notna < 40 %             → TARKISTA manuaalisesti
  muuten                   → OK (jätä rauhaan)

Periaate: segmentoidut piirteet tarvitsevat kynnyksen vain jos SHAP on korkea.
Peruspiirteitä (form_avg_km_time_5 jne.) ei kosketa — ei fallbackia.
"""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")

import sqlite3
import pandas as pd
import numpy as np

from src.features.build_features import build_feature_matrix, fill_finish_positions

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
con = sqlite3.connect("/home/ravi/app-ravi/data/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, h.birth_year FROM runners r "
    "JOIN races ra ON r.race_id = ra.race_id "
    "LEFT JOIN horses h ON r.horse_id = h.horse_id",
    con,
)
races  = pd.read_sql("SELECT * FROM races", con)
hs     = pd.read_sql(
    "SELECT * FROM horse_starts "
    "WHERE (withdrawn IS NULL OR withdrawn != 1) "
    "  AND (finish_position IS NULL OR finish_position != 99) "
    "  AND (race_date IS NULL OR race_date >= '2024-01-01')",
    con,
)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()
print("Data ladattu.", flush=True)

features = build_feature_matrix(
    fill_finish_positions(runners), races,
    horse_starts=hs, horses=horses, tracks=tracks,
)
features["race_date"] = pd.to_datetime(features["race_date"])
train = features[features["race_date"] < "2026-04-01"].copy()
print(f"Train rows: {len(train)}", flush=True)

# dist-bucket tarvitaan same_dist-segmentille
dist_bins   = [0, 1600, 2100, 9_999]
dist_labels = ["sprint", "middle", "long"]
train["_dist_bucket"] = pd.cut(
    train["distance"], bins=dist_bins, labels=dist_labels, right=True
)

# ---------------------------------------------------------------------------
# SHAP-arvot (22.5.2026, 58-piirremalli, 2000 testiriviä)
# ---------------------------------------------------------------------------
SHAP = {
    "form_avg_finish_5_same_method": 0.1041,
    "form_avg_finish_5_same_dist":   0.0823,
    "track_horse_win_rate":          0.0328,
    "driver_win_rate_60d":           0.0256,
    "driver_top3_rate_60d":          0.0277,
    "trainer_win_rate_60d":          0.0226,
    "trainer_top3_rate_60d":         0.0282,
    "driver_track_win_rate_60d":     0.0116,
    "trainer_track_win_rate_60d":    0.0134,
}

# ---------------------------------------------------------------------------
# Segmenttianalyysifunktio
# ---------------------------------------------------------------------------
def seg_stats(df: pd.DataFrame, group_keys: list[str], col: str):
    """Palauttaa (mediaani, n<=3%, Q1) segmentin koolle tai None jos avain puuttuu."""
    avail = [k for k in group_keys if k in df.columns]
    if len(avail) < len(group_keys):
        return None, None, None
    counts = df.dropna(subset=[col]).groupby(avail, observed=True).size()
    if len(counts) == 0:
        return None, None, None
    return float(counts.median()), float((counts <= 3).mean() * 100), float(counts.quantile(0.25))


# ---------------------------------------------------------------------------
# Kandidaatit
# ---------------------------------------------------------------------------
CANDIDATES = [
    # (piirre, segmenttiavaimet)
    ("form_avg_finish_5_same_method", ["horse_id", "start_method"]),
    ("form_avg_finish_5_same_dist",   ["horse_id", "_dist_bucket"]),
    ("track_horse_win_rate",          ["horse_id", "track"]),
    ("driver_win_rate_60d",           ["driver_name"]),
    ("driver_top3_rate_60d",          ["driver_name"]),
    ("trainer_win_rate_60d",          ["trainer_name"]),
    ("trainer_top3_rate_60d",         ["trainer_name"]),
    ("driver_track_win_rate_60d",     ["driver_name", "track"]),
    ("trainer_track_win_rate_60d",    ["trainer_name", "track"]),
]

# ---------------------------------------------------------------------------
# Tulosta
# ---------------------------------------------------------------------------
print()
print("=" * 100)
print(f"{'Piirre':<42} {'notna%':>7} {'SHAP':>6} {'seg_med':>8} {'n≤3%':>6} {'Q1':>5}  Suositus")
print("-" * 100)

for col, keys in CANDIDATES:
    if col not in train.columns:
        print(f"  {col:<42} PUUTTUU")
        continue

    notna   = train[col].notna().mean() * 100
    shap    = SHAP.get(col, 0.0)
    med, sp3, q1 = seg_stats(train, keys, col)

    med_s = f"{med:.1f}" if med is not None else " N/A"
    sp3_s = f"{sp3:.1f}%" if sp3 is not None else "  N/A"
    q1_s  = f"{q1:.1f}" if q1  is not None else " N/A"

    # Päätöslogiikka auditoijan matriisista
    if shap < 0.020:
        rec = "POISTA"
    elif shap >= 0.050 and sp3 is not None and sp3 > 30:
        rec = "KYNNYS?"
    elif notna < 40.0:
        rec = "TARKISTA"
    else:
        rec = "OK"

    print(f"  {col:<42} {notna:>6.1f}% {shap:>6.4f} {med_s:>8} {sp3_s:>6} {q1_s:>5}  {rec}")

print()
print("Muistutus — EI kosketeta (peruspiirteet, ei fallbackia):")
no_touch = [
    "form_avg_km_time_5", "form_best_km_time_5", "form_ewm_km_time",
    "form_avg_finish_5", "form_win_rate_5", "form_top3_rate_5",
    "atg_lifetime_win_rate", "atg_lifetime_starts",
]
for col in no_touch:
    if col in train.columns:
        notna = train[col].notna().mean() * 100
        print(f"  {col:<42} {notna:>6.1f}%  (peruspiirre — jätä rauhaan)")

print()
print("Valmis.")
