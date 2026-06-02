"""Betting edge -diagnostiikka: mittaa onko mallissa aitoa markkinaedgeä.

Kolme mittaria:
  1. Kalibrointitaulu kerroinämpäreittäin — yliarvioiko malli pitkävetoja?
  2. Malli-suosikki vs. markkina-suosikki osumatarkkuus per lähtö
  3. Paperi-ROI + CLV eri edge-kynnyksillä (value-sääntöjä testaten)

Aja: python scripts/eval_betting_edge.py
Testiperiodi: toukokuu 2026 (718 lähtöä joilla tulokset)
"""
import sys; sys.path.insert(0, "/home/ravi/app-ravi")
import sqlite3, json, glob
import pandas as pd, numpy as np
import lightgbm as lgb
from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import predict_win_probabilities

EVAL_START = "2026-05-01"
EVAL_END   = "2026-06-01"
DATA_DIR   = "/home/ravi/app-ravi/data"

# ── Lataa data ──────────────────────────────────────────────────────────────
con = sqlite3.connect(f"{DATA_DIR}/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, ra.distance, ra.start_method AS race_start_method,"
    " h.birth_year, h.name AS horse_name"
    " FROM runners r JOIN races ra ON r.race_id = ra.race_id"
    " LEFT JOIN horses h ON r.horse_id = h.horse_id"
    " WHERE ra.race_date >= ? AND ra.race_date < ?",
    con, params=(EVAL_START, EVAL_END))
races     = pd.read_sql("SELECT * FROM races WHERE race_date >= ? AND race_date < ?",
                        con, params=(EVAL_START, EVAL_END))
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

features = build_feature_matrix(
    fill_finish_positions(runners), races,
    horse_starts=hs, horses=horses, tracks=tracks,
    spwr_lookup=spwr_lookup, all_races=races_all)

model_files = sorted(glob.glob(f"{DATA_DIR}/model_baseline_*.lgb"))
latest_lgb  = model_files[-1]
meta   = json.load(open(latest_lgb.replace(".lgb", "_meta.json")))
T      = meta["temperature"]
model  = lgb.Booster(model_file=latest_lgb)
print(f"Malli: {latest_lgb.split('/')[-1]}  T={T:.4f}")

preds = predict_win_probabilities(model, features, temperature=T)

# Yhdistä
name_odds = runners[["race_id", "horse_id", "horse_name", "win_odds_final"]].copy()
feat_cols = ["race_id", "horse_id", "finish_position"]
df = preds.merge(name_odds, on=["race_id", "horse_id"], how="left")
df = df.merge(features[[c for c in feat_cols if c in features.columns]], on=["race_id", "horse_id"], how="left")

# Vain lähdöt joissa on kertoimet JA tulos
has_result = df.groupby("race_id")["finish_position"].apply(lambda x: (x == 1).any())
has_odds   = df.groupby("race_id")["win_odds_final"].apply(lambda x: x.notna().any())
valid_races = has_result[has_result].index.intersection(has_odds[has_odds].index)
df = df[df["race_id"].isin(valid_races)].copy()
df["actual_win"]    = (df["finish_position"] == 1).astype(int)
df["market_prob"]   = 1.0 / df["win_odds_final"].replace(0, np.nan)
df["edge"]          = df["win_prob"] - df["market_prob"]
df = df[df["market_prob"].notna()].copy()

print(f"\nEvaluointidata: {len(valid_races)} lähtöä, {len(df)} hevosta (kertoimilla)")

# ─────────────────────────────────────────────────────────────────────────────
# 1. KALIBROINTITAULU KERROINÄMPÄREITTÄIN
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 62)
print("1. KALIBROINTITAULU (malli vs. toteutunut voitto-%)")
print("=" * 62)
bins   = [1.0, 2.0, 3.0, 5.0, 8.0, 15.0, 30.0, float("inf")]
labels = ["1.0-2.0", "2.0-3.0", "3.0-5.0", "5.0-8.0", "8.0-15.0", "15.0-30.0", "30+"]
df["odds_bin"] = pd.cut(df["win_odds_final"], bins=bins, labels=labels, right=True)

cal = df.groupby("odds_bin", observed=True).agg(
    n=("actual_win", "count"),
    wins=("actual_win", "sum"),
    model_mean=("win_prob", "mean"),
    market_mean=("market_prob", "mean"),
).reset_index()
cal["actual_win_pct"] = cal["wins"] / cal["n"]
cal["model_error"]    = cal["model_mean"] - cal["actual_win_pct"]

print(f"{'Kerroin':<12} {'N':>6} {'Voittoja':>8} {'Toteutunut%':>12} {'Malli%':>8} {'Markkina%':>10} {'Malli-vinouma':>14}")
for _, row in cal.iterrows():
    flag = " ← YLIARVIO" if row["model_error"] > 0.02 else (" ← aliarvio" if row["model_error"] < -0.02 else "")
    print(f"{str(row['odds_bin']):<12} {int(row['n']):>6} {int(row['wins']):>8}"
          f" {row['actual_win_pct']:>11.1%} {row['model_mean']:>7.1%} {row['market_mean']:>9.1%}"
          f" {row['model_error']:>+12.1%}{flag}")

# ─────────────────────────────────────────────────────────────────────────────
# 2. MALLI-SUOSIKKI VS. MARKKINA-SUOSIKKI OSUMATARKKUUS
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 62)
print("2. OSUMATARKKUUS: mallin top-1 vs. markkinan top-1")
print("=" * 62)

def race_accuracy(group):
    if group["actual_win"].sum() != 1:
        return None
    model_top  = group.loc[group["win_prob"].idxmax(), "actual_win"]
    market_top = group.loc[group["market_prob"].idxmax(), "actual_win"]
    return pd.Series({"model_win": model_top, "market_win": market_top})

acc = df.groupby("race_id").apply(race_accuracy).dropna()
n_races = len(acc)
model_acc  = acc["model_win"].mean()
market_acc = acc["market_win"].mean()
print(f"Lähtöjä (1 voittaja + kertoimet): {n_races}")
print(f"Mallin top-1 voittaa:   {model_acc:.1%}  ({int(acc['model_win'].sum())}/{n_races})")
print(f"Markkinan top-1 voittaa: {market_acc:.1%}  ({int(acc['market_win'].sum())}/{n_races})")
print(f"Ero: malli {model_acc-market_acc:+.1%} vs. markkina")
if model_acc > market_acc:
    print("  → Malli parempi kuin markkina suosikin löytämisessä ✓")
elif model_acc < market_acc:
    print("  → Markkina parempi kuin malli suosikin löytämisessä ✗")
else:
    print("  → Tasapeli")

# Vertailu random baselinen kanssa
field_sizes = df.groupby("race_id")["horse_id"].count()
random_acc = (1.0 / field_sizes).mean()
print(f"Random baseline (1/n):    {random_acc:.1%}")

# ─────────────────────────────────────────────────────────────────────────────
# 3. PAPERI-ROI + CLV ERI EDGE-KYNNYKSILLÄ
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 62)
print("3. PAPERI-ROI (1 yksikkö per veto, flat stake)")
print("=" * 62)

for min_edge in [0.05, 0.08, 0.10, 0.15, 0.20]:
    bets = df[df["edge"] >= min_edge].copy()
    if len(bets) == 0:
        print(f"Edge ≥ {min_edge:.0%}: ei vetoja")
        continue
    # Flat stake ROI: (kerroin-1 jos voittaja, -1 muuten)
    bets["pnl"] = np.where(bets["actual_win"] == 1,
                            bets["win_odds_final"] - 1.0,
                            -1.0)
    n_bets  = len(bets)
    n_wins  = int(bets["actual_win"].sum())
    total   = bets["pnl"].sum()
    roi     = total / n_bets
    win_pct = n_wins / n_bets
    # CLV: expected value jos malli on oikeassa
    ev = (bets["win_prob"] * (bets["win_odds_final"] - 1) - (1 - bets["win_prob"])).mean()
    avg_edge  = bets["edge"].mean()
    avg_odds  = bets["win_odds_final"].mean()
    print(f"Edge ≥ {min_edge:.0%}: {n_bets:>4} vetoa  voitto={win_pct:.1%}  ROI={roi:>+.1%}  "
          f"EV={ev:>+.3f}  avg_odds={avg_odds:.1f}  avg_edge={avg_edge:.1%}")

print()
print("Muistutus: ≥ 50–100 vetoa tarvitaan luotettavaan estimaattiin (CLV-ohje)")
print("Toukokuun 718 lähdöstä suodatetaan vain kertoimelliset → otanta pieni")

# ─────────────────────────────────────────────────────────────────────────────
# 4. LISÄ: top-1 kalibrointi suosikkivoimakkuuden mukaan
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 62)
print("4. MARKKINASUOSIKIN KERROIN VS. MALLIN SIJOITTUMINEN")
print("=" * 62)
# Per lähtö: onko markkinasuosikki myös mallin top-3?
def check_favorite_handling(group):
    if group["actual_win"].sum() != 1:
        return None
    fav_idx = group["market_prob"].idxmax()
    fav_model_rank = (group["win_prob"].rank(ascending=False).loc[fav_idx])
    fav_odds = group.loc[fav_idx, "win_odds_final"]
    return pd.Series({"fav_model_rank": fav_model_rank, "fav_odds": fav_odds})

fav_data = df.groupby("race_id").apply(check_favorite_handling).dropna()
fav_in_top1 = (fav_data["fav_model_rank"] == 1).mean()
fav_in_top3 = (fav_data["fav_model_rank"] <= 3).mean()
print(f"Markkinasuosikki on mallin top-1: {fav_in_top1:.1%}")
print(f"Markkinasuosikki on mallin top-3: {fav_in_top3:.1%}")
print(f"Mallin mediaani rank markkinasuosikille: {fav_data['fav_model_rank'].median():.1f}")

# Kerroin-bucket-jako
fav_data["odds_bin"] = pd.cut(fav_data["fav_odds"], bins=[1,2,3,5,float("inf")],
                               labels=["1-2","2-3","3-5","5+"])
fav_by_odds = fav_data.groupby("odds_bin", observed=True).agg(
    n=("fav_model_rank","count"),
    top1=("fav_model_rank", lambda x: (x==1).mean()),
    top3=("fav_model_rank", lambda x: (x<=3).mean()),
    median_rank=("fav_model_rank","median")
).reset_index()
print(f"\n{'Suosikki-kerroin':<18} {'N':>5} {'Malli-top1':>11} {'Malli-top3':>11} {'Med.rank':>9}")
for _, row in fav_by_odds.iterrows():
    print(f"{str(row['odds_bin']):<18} {int(row['n']):>5} {row['top1']:>10.1%} {row['top3']:>10.1%} {row['median_rank']:>9.1f}")
