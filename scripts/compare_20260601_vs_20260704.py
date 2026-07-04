"""Vertaa vanha (20260601) ja uusi (20260704) malli samalla OOS-ikkunalla."""
import sys; sys.path.insert(0, "/home/ravi/app-ravi")
import sqlite3, json
import pandas as pd, numpy as np
import lightgbm as lgb
from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import predict_win_probabilities, blend_with_market, CATEGORICAL_COLS

EVAL_START, EVAL_END = "2026-06-25", "2026-07-05"
DATA_DIR = "/home/ravi/app-ravi/data"

con = sqlite3.connect(f"{DATA_DIR}/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, ra.distance, ra.start_method AS race_start_method,"
    " h.birth_year FROM runners r JOIN races ra ON r.race_id = ra.race_id"
    " LEFT JOIN horses h ON r.horse_id = h.horse_id"
    " WHERE ra.race_date >= ? AND ra.race_date < ?", con, params=(EVAL_START, EVAL_END))
races = pd.read_sql("SELECT * FROM races WHERE race_date >= ? AND race_date < ?", con, params=(EVAL_START, EVAL_END))
races_all = pd.read_sql("SELECT * FROM races", con)
hs = pd.read_sql(
    "SELECT * FROM horse_starts WHERE (withdrawn IS NULL OR withdrawn != 1)"
    " AND (finish_position IS NULL OR finish_position != 99)"
    " AND (race_date IS NULL OR race_date >= '2024-01-01')", con)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)
con.close()

if "start_method" not in runners.columns:
    runners = runners.rename(columns={"race_start_method": "start_method"})
elif "race_start_method" in runners.columns:
    runners["start_method"] = runners["start_method"].fillna(runners["race_start_method"])
    runners = runners.drop(columns=["race_start_method"])

features = build_feature_matrix(
    fill_finish_positions(runners), races,
    horse_starts=hs, horses=horses, tracks=tracks, all_races=races_all)

odds = runners[["race_id", "horse_id", "win_odds_final"]]

def evaluate(tag, model_file):
    meta = json.load(open(f"{DATA_DIR}/{model_file}_meta.json"))
    model = lgb.Booster(model_file=f"{DATA_DIR}/{model_file}.lgb")
    T = meta["temperature"]
    mf = model.feature_name()
    fcols = [c for c in mf if c not in CATEGORICAL_COLS]
    ccols = [c for c in CATEGORICAL_COLS if c in mf]
    preds = predict_win_probabilities(model, features, feature_cols=fcols, categorical_cols=ccols, temperature=T)
    df = preds.merge(features[["race_id", "horse_id", "finish_position"]], on=["race_id", "horse_id"])
    df = df.merge(odds, on=["race_id", "horse_id"], how="left")
    has_result = df.groupby("race_id")["finish_position"].transform(lambda s: (s == 1).any())
    df = df[has_result].copy()
    df["actual_win"] = (df["finish_position"] == 1).astype(int)
    brier = float(((df["win_prob"] - df["actual_win"]) ** 2).mean())
    idx = df.groupby("race_id")["win_prob"].idxmax()
    top1 = float(df.loc[idx, "actual_win"].mean())
    rs = df.groupby("race_id")["win_prob"].agg(["std", "max"])
    # blendi mallin omalla alphalla
    alpha = meta.get("blend_alpha", 0.16)
    b = blend_with_market(df.rename(columns={"win_odds_final": "win_odds"}), alpha=alpha)
    brier_bl = float(((b["win_prob_blend"] - b["actual_win"]) ** 2).mean())
    idxb = b.groupby("race_id")["win_prob_blend"].idxmax()
    top1_bl = float(b.loc[idxb, "actual_win"].mean())
    n_races = df["race_id"].nunique()
    print(f"{tag}: T={T:.3f} α={alpha:.3f} | {n_races} lähtöä")
    print(f"  malli:  Brier={brier:.4f} top1={top1:.1%} med_std={rs['std'].median():.4f} med_top1={rs['max'].median():.4f}")
    print(f"  blendi: Brier={brier_bl:.4f} top1={top1_bl:.1%}")
    return df

df = evaluate("VANHA 20260601", "model_baseline_20260601")
df = evaluate("UUSI  20260704", "model_baseline_20260704")

# markkina-baseline
m = df[df["win_odds_final"].notna()].copy()
m["mkt"] = 1.0 / m["win_odds_final"]
m["mkt"] = m["mkt"] / m.groupby("race_id")["mkt"].transform("sum")
brier_m = float(((m["mkt"] - m["actual_win"]) ** 2).mean())
idx = m.groupby("race_id")["mkt"].idxmax()
print(f"MARKKINA: Brier={brier_m:.4f} top1={float(m.loc[idx,'actual_win'].mean()):.1%}")
