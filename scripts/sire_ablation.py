"""
Vaihe 3.6 — Sire-ablation + dam_sire-kattavuustutkimus + random_state-vakaus

Auditoijan suositukset (14.5.2026):
  1. Poista sire-piirteet tilapäisesti, aja sama split -> Brier-vertailu
     Tulkinta:
       Brier ~sama (~0.075)   -> sire-gain on artefakti, form-piirteissä sama info
       Brier heikkenee >0.080 -> sire aidosti informatiivinen, #2 perusteltu
       Brier hieman heikompi  -> sire hyödyllinen mutta ei dominantti
  2. dam_sire-kattavuus: miksi 88% -> 25%?
     SQL-kysely horses-tauluun: kuinka moni horse_id:llä on dam_sire != NULL
  3. random_state=42 molemmissa malleissa -> tarkista Briervaihtelun syy
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

# ---------------------------------------------------------------------------
# 1. Lataa data
# ---------------------------------------------------------------------------
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
    "SELECT * FROM horse_starts "
    "WHERE (withdrawn IS NULL OR withdrawn != 1) "
    "  AND (finish_position IS NULL OR finish_position != 99)",
    con,
)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)

# ---------------------------------------------------------------------------
# 2. dam_sire-kattavuustutkimus (SQL suoraan)
# ---------------------------------------------------------------------------
print("=== DAM_SIRE KATTAVUUS ===")
dam_sql = """
SELECT
    COUNT(*)                                          AS total_horses,
    COUNT(dam_sire)                                   AS with_dam_sire,
    ROUND(COUNT(dam_sire) * 100.0 / COUNT(*), 1)      AS dam_sire_pct,
    COUNT(sire)                                       AS with_sire,
    ROUND(COUNT(sire) * 100.0 / COUNT(*), 1)          AS sire_pct
FROM horses
"""
dam_stats = pd.read_sql(dam_sql, con)
print(dam_stats.to_string(index=False))

# Onko uusilla hevosilla (joita ei ollut B2-backfill-hetkellä) dam_sire?
new_horse_sql = """
SELECT
    COUNT(*)                                              AS new_horses,
    COUNT(dam_sire)                                       AS with_dam_sire,
    ROUND(COUNT(dam_sire) * 100.0 / COUNT(*), 1)          AS dam_sire_pct
FROM horses
WHERE horse_id NOT IN (
    SELECT DISTINCT horse_id FROM horse_starts WHERE race_date < '2026-05-11'
)
"""
try:
    new_stats = pd.read_sql(new_horse_sql, con)
    print("\nUudet hevoset (ei horse_starts ennen 11.5.2026):")
    print(new_stats.to_string(index=False))
except Exception as e:
    print(f"Uusi-hevonen-kysely epäonnistui: {e}")

con.close()

# ---------------------------------------------------------------------------
# 3. Rakenna piirteet
# ---------------------------------------------------------------------------
runners_filled = fill_finish_positions(runners)
features = build_feature_matrix(
    runners_filled, races,
    horse_starts=horse_starts,
    horses=horses,
    tracks=tracks,
)
features["race_date"] = pd.to_datetime(features["race_date"])

sire_cols_in_features = [c for c in features.columns if "sire" in c]
print(f"\nSire-sarakkeet features-DataFramessa: {sire_cols_in_features}")
for c in sire_cols_in_features:
    pct = features[c].notna().mean() * 100
    print(f"  {c}: {pct:.1f}% notna")

split_date = "2026-05-08"
train_df = features[features["race_date"] < split_date].copy()
test_df  = features[features["race_date"] >= split_date].copy()
print(f"\nTrain: {len(train_df)} | Test: {len(test_df)}")

# ---------------------------------------------------------------------------
# 4. Apufunktio arvioinnille
# ---------------------------------------------------------------------------
def evaluate(model, test, feat_cols):
    preds  = predict_win_probabilities(model, test, feature_cols=feat_cols)
    merged = test.merge(preds[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"])
    merged["actual_win"] = (merged["finish_position"] == 1).astype(int)
    brier = float(((merged["win_prob"] - merged["actual_win"]) ** 2).mean())
    nll   = compute_nll(merged)
    return brier, nll

# ---------------------------------------------------------------------------
# 5. Täydellinen malli WITH random_state=42
# ---------------------------------------------------------------------------
print("\n=== TÄYDELLINEN MALLI (random_state=42) ===")
model_full = train_ranker(train_df, random_state=42)
brier_full, nll_full = evaluate(model_full, test_df, FEATURE_COLS)
print(f"Full (rs=42): Brier={brier_full:.4f}  NLL={nll_full:.2f}")

fi_full = pd.Series(
    model_full.feature_importance(importance_type="gain"),
    index=model_full.feature_name(),
).sort_values(ascending=False)
print("Top-10 (full):", fi_full.head(10).index.tolist())
for check in ["sire_lifetime_win_rate", "dam_sire_lifetime_starts", "track_home_stretch_m"]:
    r = list(fi_full.index).index(check) + 1 if check in fi_full.index else "puuttuu"
    print(f"  {check}: #{r}/{len(fi_full)}")

# ---------------------------------------------------------------------------
# 6. Sire-ablation WITH random_state=42
# ---------------------------------------------------------------------------
print("\n=== SIRE-ABLATION (ilman sire-piirteet, random_state=42) ===")
no_sire_cols = [c for c in FEATURE_COLS if "sire" not in c]
print(f"Piirteitä ablation-mallissa: {len(no_sire_cols)} (poistettu {len(FEATURE_COLS)-len(no_sire_cols)})")
print(f"Poistetut: {[c for c in FEATURE_COLS if 'sire' in c]}")

model_no_sire = train_ranker(train_df, feature_cols=no_sire_cols, random_state=42)
brier_no_sire, nll_no_sire = evaluate(model_no_sire, test_df, no_sire_cols)
print(f"No-sire (rs=42): Brier={brier_no_sire:.4f}  NLL={nll_no_sire:.2f}")

fi_no_sire = pd.Series(
    model_no_sire.feature_importance(importance_type="gain"),
    index=model_no_sire.feature_name(),
).sort_values(ascending=False)
print("Top-10 (no-sire):", fi_no_sire.head(10).index.tolist())

# ---------------------------------------------------------------------------
# 7. Yhteenveto
# ---------------------------------------------------------------------------
print("\n=== SIRE-ABLATION YHTEENVETO ===")
print(f"Täydellinen malli:  Brier={brier_full:.4f}  NLL={nll_full:.2f}")
print(f"Ilman sire-piirt.:  Brier={brier_no_sire:.4f}  NLL={nll_no_sire:.2f}")
delta_b = brier_no_sire - brier_full
delta_n = nll_no_sire - nll_full
print(f"Delta Brier (no_sire - full): {delta_b:+.4f}  (positiivinen = sire-piirteet auttavat)")
print(f"Delta NLL   (no_sire - full): {delta_n:+.2f}")

if delta_b > 0.005:
    verdict = "SIRE AIDOSTI INFORMATIIVINEN — #2 ranking perusteltu"
elif delta_b > 0.001:
    verdict = "SIRE HYODYLLINEN MUTTA EI DOMINANTTI — gain yliarvio, oikea vaikutus top-10 alue"
else:
    verdict = "SIRE EI MERKITSEVA — gain on artefakti, form-piirteissa sama info"
print(f"Verdict: {verdict}")
