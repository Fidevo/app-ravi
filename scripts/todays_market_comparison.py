"""Tämän päivän ennusteet vs. markkina (win_odds_final) per lähtö.

Näyttää: hevosen nimi, mallin win_prob, markkinan implied_prob (1/odds),
edge = win_prob - implied_prob (positiivinen = malli uskoo enemmän kuin markkina).
"""
import sys; sys.path.insert(0, "/home/ravi/app-ravi")
import sqlite3, json, glob
import pandas as pd, numpy as np
import lightgbm as lgb
from src.features.build_features import build_feature_matrix
from src.models.ranker import predict_win_probabilities

TARGET_DATE = "2026-06-01"
DATA_DIR    = "/home/ravi/app-ravi/data"

con = sqlite3.connect(f"{DATA_DIR}/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, ra.distance, ra.start_method AS race_start_method,"
    " h.birth_year, h.name AS horse_name"
    " FROM runners r"
    " JOIN races ra ON r.race_id = ra.race_id"
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

model_files = sorted(glob.glob(f"{DATA_DIR}/model_baseline_*.lgb"))
latest_lgb  = model_files[-1]
meta   = json.load(open(latest_lgb.replace(".lgb", "_meta.json")))
T      = meta["temperature"]
model  = lgb.Booster(model_file=latest_lgb)
print(f"Malli: {latest_lgb.split('/')[-1]}  T={T:.4f}\n")

preds = predict_win_probabilities(model, features, temperature=T)

# Yhdistä nimien, kertoimien ja ennusteiden kanssa
name_odds = runners[["race_id", "horse_id", "horse_name", "win_odds_final",
                       "start_number", "finish_position"]].copy()
preds = preds.merge(name_odds, on=["race_id", "horse_id"], how="left")

# Implied probability markkinalta
preds["market_prob"] = 1.0 / preds["win_odds_final"].replace(0, np.nan)
preds["edge"] = preds["win_prob"] - preds["market_prob"]

has_odds  = preds["market_prob"].notna().any()
has_names = preds["horse_name"].notna().any()

race_ids = sorted(preds["race_id"].unique())
print(f"Lähtöjä: {len(race_ids)}\n")

for race_id in race_ids:
    r = preds[preds["race_id"] == race_id].sort_values("win_prob", ascending=False)
    ri = races[races["race_id"] == race_id].iloc[0]
    track   = ri.get("track", "")
    rnum    = ri.get("race_number", "")
    dist    = ri.get("distance", "")
    smethod = ri.get("start_method", "")
    std     = float(r["win_prob"].std())
    flag    = "  ⚠️ TASAINEN" if std < 0.04 else ""

    print(f"Lähtö {rnum:>2} | {track:<12} | {dist}m {smethod:<5} | std={std:.4f}{flag}")

    for _, row in r.iterrows():
        name = str(row.get("horse_name") or row["horse_id"])[:24]
        wp   = row["win_prob"]
        mp   = row.get("market_prob")
        fp   = row.get("finish_position")
        result = f"  ✓{int(fp)}" if pd.notna(fp) and fp == 1 else (f"  pos={int(fp)}" if pd.notna(fp) else "")

        if pd.notna(mp) and mp > 0:
            edge  = wp - mp
            odds  = row["win_odds_final"]
            edge_str = f"  edge={edge:+.1%}" if abs(edge) > 0.01 else ""
            print(f"  #{int(row['start_number']):<2} {name:<24} malli={wp:>6.1%}  markkina={mp:>5.1%} ({odds:.1f}){edge_str}{result}")
        else:
            print(f"  #{int(row['start_number']):<2} {name:<24} malli={wp:>6.1%}{result}")
    print()

# Parhaat valuebetit (edge > 5%)
value_bets = preds[(preds["edge"] > 0.05) & preds["market_prob"].notna()].sort_values("edge", ascending=False)
if len(value_bets) > 0:
    print("=" * 60)
    print(f"VALUE-BETIT (malli > markkina yli 5%):")
    for _, row in value_bets.head(10).iterrows():
        ri = races[races["race_id"] == row["race_id"]].iloc[0]
        name = str(row.get("horse_name") or row["horse_id"])[:24]
        print(f"  {ri['track']:<12} L{ri['race_number']:>2} #{int(row['start_number']):<2} {name:<24}"
              f"  malli={row['win_prob']:>5.1%}  kerroin={row['win_odds_final']:.1f}"
              f"  edge={row['edge']:+.1%}")
else:
    print("Ei markkinakertoimi saatavilla tai ei selviä value-bettejä.")
