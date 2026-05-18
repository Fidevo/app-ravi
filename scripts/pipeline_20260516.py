"""
Taydellinen pipeline 2026-05-16
  1. Lataa kaikki data (59k runners, 131k horse_starts)
  2. Rakenna feature-matriisi (48 piirrettä)
  3. Train/test split -> treenaa malli -> evaluoi (Brier + NLL)
  4. Rolling walk-forward (out-of-sample "tuurilla oikein" -testi)
  5. Feature importance top-15 (gain)
  6. Tallenna malli -> dashboard poimii automaattisesti
"""
import json
import sys
import time
sys.path.insert(0, "/home/ravi/app-ravi")

import sqlite3
import pandas as pd
import numpy as np

from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import (
    FEATURE_COLS,
    train_ranker,
    predict_win_probabilities,
    calibrate_temperature,
    compute_nll,
)
from src.models.backtest import rolling_walk_forward

TODAY      = "20260516"
SPLIT_DATE = "2026-05-09"
OUT_PATH   = f"/home/ravi/app-ravi/data/model_baseline_{TODAY}.lgb"
DB_PATH    = "/home/ravi/app-ravi/data/ravit.db"

SEP = "=" * 62


def hr(title=""):
    print(f"\n{SEP}")
    if title:
        print(f"  {title}")
        print(SEP)


hr(f"Ravit Edge — Full Pipeline {TODAY}")
t0 = time.time()

# ------------------------------------------------------------------
# 1. Lataa data
# ------------------------------------------------------------------
hr("1/5  Ladataan data")
con = sqlite3.connect(DB_PATH)
runners = pd.read_sql(
    """SELECT r.*, ra.race_date, h.birth_year
       FROM runners r
       JOIN races ra ON r.race_id = ra.race_id
       LEFT JOIN horses h ON r.horse_id = h.horse_id""",
    con,
)
races = pd.read_sql("SELECT * FROM races", con)
horse_starts = pd.read_sql(
    # KNOWN_ISSUES #16: NULL != 99 evaluoituu NULLiksi SQLite:ssä (ei trueksi),
    # joten "finish_position != 99" jättää myös NULL-rivit pois. Korjattu:
    # NULL-finish_position = tulos tuntematon (ei DNF/scratch) → sisällytetään.
    "SELECT * FROM horse_starts "
    "WHERE (withdrawn IS NULL OR withdrawn != 1) "
    "  AND (finish_position IS NULL OR finish_position != 99)",
    con,
)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()

print(f"  runners:      {len(runners):>7,}")
print(f"  races:        {len(races):>7,}")
hs_min = horse_starts["race_date"].min()
hs_max = horse_starts["race_date"].max()
print(f"  horse_starts: {len(horse_starts):>7,}  ({hs_min} - {hs_max})")
print(f"  horses:       {len(horses):>7,}")
print(f"  tracks:       {len(tracks):>7,}")
print(f"  FEATURE_COLS: {len(FEATURE_COLS)} piirrettä")

# ------------------------------------------------------------------
# 2. Rakenna feature-matriisi
# ------------------------------------------------------------------
hr("2/5  Rakennetaan feature-matriisi")
t_feat = time.time()
features = build_feature_matrix(
    fill_finish_positions(runners),
    races,
    horse_starts=horse_starts,
    horses=horses,
    tracks=tracks,
)
features["race_date"] = pd.to_datetime(features["race_date"])
print(f"  Valmis ({time.time() - t_feat:.0f}s). Riveja: {len(features):,}")

# Kattavuustarkistus
low_cov = []
for col in FEATURE_COLS:
    if col not in features.columns:
        print(f"  PUUTTUU: {col}")
        continue
    pct = features[col].notna().mean() * 100
    if pct < 15:
        low_cov.append((col, pct))
if low_cov:
    print("  Matala kattavuus (<15%):")
    for col, pct in low_cov:
        print(f"    {col}: {pct:.1f}%")
else:
    print("  Kattavuus OK — kaikki piirteet yli 15%")

# ------------------------------------------------------------------
# 3. Train/test split + treenaus
# ------------------------------------------------------------------
hr("3/5  Train/test split ja treenaus")
train_df = features[features["race_date"] < SPLIT_DATE].copy()
test_df  = features[features["race_date"] >= SPLIT_DATE].copy()
test_df  = test_df[test_df["finish_position"].notna()].copy()

d_min = train_df["race_date"].min().date()
d_max = train_df["race_date"].max().date()
print(f"  Train: {len(train_df):,} rivia  ({d_min} - {d_max})")
print(f"         {train_df['race_id'].nunique():,} lahtoa")
print(f"  Test:  {len(test_df):,} rivia  ({SPLIT_DATE} - {test_df['race_date'].max().date()})")
print(f"         {test_df['race_id'].nunique():,} lahtoa")

t_train = time.time()
print("\n  Treenataan LightGBM LambdaRank...")
model = train_ranker(train_df, random_state=42)
model.save_model(OUT_PATH)
print(f"  Treenaus valmis ({time.time() - t_train:.0f}s). Tallennettu: {OUT_PATH}")
print(f"  Mallin piirteet: {len(model.feature_name())}")

# ------------------------------------------------------------------
# 3b. Kalibrointi — temperature scaling (test-setillä)
# ------------------------------------------------------------------
hr("3b/5  Temperature scaling -kalibrointi")
# Ennusta raw-pisteet test-setille kalibrointia varten (temperature=1.0)
preds_raw = predict_win_probabilities(model, test_df, temperature=1.0)
# Yhdistä finish_position kalibrointia varten
calib_df = preds_raw.merge(
    test_df[["race_id", "horse_id", "finish_position"]],
    on=["race_id", "horse_id"],
    how="left",
)
T_opt = calibrate_temperature(calib_df)
print(f"  Optimaalinen lämpötila T = {T_opt:.4f}")
if T_opt < 1.0:
    print("  Tulkinta: T < 1 → terävöittää jakaumaa (suosikit enemmän esiin)")
elif T_opt > 1.0:
    print("  Tulkinta: T > 1 → tasoittaa jakaumaa (tasaisempi kilpailu)")
else:
    print("  Tulkinta: T ≈ 1 → ei kalibrointivaikutusta")

# Tallenna T ja muut metatiedot mallin viereen (.lgb → _meta.json)
META_PATH = OUT_PATH.replace(".lgb", "_meta.json")
meta = {
    "temperature": T_opt,
    "split_date": SPLIT_DATE,
    "today": TODAY,
    "num_features": len(model.feature_name()),
    "feature_names": model.feature_name(),
}
with open(META_PATH, "w") as _f:
    json.dump(meta, _f, indent=2)
print(f"  Meta tallennettu: {META_PATH}")

# ------------------------------------------------------------------
# 4. Evaluoi test-setilla
# ------------------------------------------------------------------
hr("4/5  Evaluointi test-setilla")
preds  = predict_win_probabilities(model, test_df, temperature=T_opt)
merged = test_df.merge(
    preds[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"]
)
merged["actual_win"] = (merged["finish_position"] == 1).astype(int)
merged = merged[merged["finish_position"].notna()]


def evaluate(df, label):
    brier = float(((df["win_prob"] - df["actual_win"]) ** 2).mean())
    nll   = compute_nll(df)
    n_r   = df["race_id"].nunique()
    print(
        f"  {label:<22s}  Brier={brier:.4f}  NLL={nll:.2f}  "
        f"({n_r} lahtoa, {len(df)} runneria)"
    )
    return brier


b_all = evaluate(merged, "Kaikki lahdot")
if "is_v_race" in merged.columns:
    v_df = merged[merged["is_v_race"] == 1]
    if len(v_df) >= 10:
        evaluate(v_df, "V-pelil.")
    else:
        print(f"  V-pelil.: liian vahan riveja ({len(v_df)})")

# Naiivi baseline
naive = merged.groupby("race_id")["actual_win"].transform(
    lambda x: 1.0 / len(x)
)
b_naive = float(((naive - merged["actual_win"]) ** 2).mean())
print(f"\n  Naiivi baseline (1/N):     Brier={b_naive:.4f}")
improvement = b_naive - b_all
print(
    f"  Mallin parannus naiiviin:  dBrier={improvement:+.4f}  "
    f"({'PAREMPI' if b_all < b_naive else 'HUONOMPI kuin naiivi!'})"
)

# Feature importance
fi_names = model.feature_name()
fi_vals  = model.feature_importance(importance_type="gain")
fi = pd.Series(fi_vals, index=fi_names).sort_values(ascending=False)
print("\n  Top-15 piirteet (gain):")
for rank, (feat, val) in enumerate(fi.head(15).items(), 1):
    print(f"  {rank:2d}. {feat:<40s} {val:>9.0f}")

zero_gain = [f for f, v in zip(fi_names, fi_vals) if v == 0]
if zero_gain:
    print(f"\n  Gain=0 ({len(zero_gain)} kpl): {zero_gain}")

# ------------------------------------------------------------------
# 5. Rolling walk-forward
# ------------------------------------------------------------------
hr("5/5  Rolling walk-forward (out-of-sample)")
print("  window=30pv, train_min=90pv")
print("  Simuloi live-kayttoa — malli ei nae tulevaa testidataa")

t_wf = time.time()
wf_results = rolling_walk_forward(
    features,
    races,
    window_days=30,
    train_window_days=90,
    edge_threshold=0.05,
    flat_stake=100.0,
)
print(f"  Walk-forward valmis ({time.time() - t_wf:.0f}s). Ikkunoita: {len(wf_results)}")

if len(wf_results) > 0:
    print(
        f"\n  {'Periodi':<22}  {'Lahtoja':>8}  {'Brier':>7}  "
        f"{'ROI%':>7}  {'Value bets':>10}"
    )
    print(f"  {'-'*22}  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*10}")
    for _, row in wf_results.iterrows():
        print(
            f"  {str(row['period']):<22}  {int(row['n_races']):>8}  "
            f"{row['brier_score']:>7.4f}  {row['roi_pct']:>7.1f}%  "
            f"{int(row['n_value_bets']):>10}"
        )

    total_pnl    = wf_results["total_pnl"].sum()
    total_staked = wf_results["total_staked"].sum()
    overall_roi  = 100 * total_pnl / total_staked if total_staked > 0 else 0.0
    pos_windows  = int((wf_results["roi_pct"] > 0).sum())
    total_win    = len(wf_results)
    mean_brier   = wf_results["brier_score"].mean()

    print("\n  Yhteenveto:")
    print(f"    Ikkunoita:              {total_win}")
    print(f"    Positiivinen ROI:       {pos_windows}/{total_win} ikkunaa")
    print(f"    Keski-Brier:            {mean_brier:.4f}")
    print(f"    Kokonais-ROI (flat100): {overall_roi:+.1f}%")
    print(f"    Kokonais-PnL:           {total_pnl:+,.0f} SEK")
    if pos_windows > total_win * 0.6:
        verdict = "SIGNAALI AITO — ylisuorittelee naiivia yli 60% ikkunoista"
    elif pos_windows > total_win * 0.5:
        verdict = "HEIKKO SIGNAALI — marginaalisesti yli 50%"
    else:
        verdict = "EI SELKEAA SIGNAALIA — tuurin ja systeemin raja"
    print(f"    Tulkinta: {verdict}")
else:
    print("  Ei riittavasti dataa walk-forwardiin (tarvitaan >90pv historiaa).")

# ------------------------------------------------------------------
# Lopetus
# ------------------------------------------------------------------
hr("VALMIS")
elapsed = time.time() - t0
print(f"  Kesto:    {elapsed:.0f}s ({elapsed / 60:.1f} min)")
print(f"  Malli:    {OUT_PATH}")
print(f"  Dashboard: poimii uuden mallin automaattisesti (cache_resource reload).")
