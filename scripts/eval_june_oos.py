"""Ratkaiseva testi: onko mallissa informaatiota jota markkina ei jo hinnoittele?

Kolme mallia toukokuun 718 lähdöllä:
  A: pelkkä market_implied_prob (devigattu odds → normalisoitu per lähtö)
  B: nykyinen 46-piirteen malli (ei markkinasignaalia)
  AB: optimaalinen log-odds -blendi A:sta ja B:sta

Tulkinta:
  AB ≈ A → B ei lisää mitään markkinan päälle → ei ortogonaalista signaalia
  AB << A → B lisää jotain → signaali on olemassa (mutta pieni tai iso)
  B << A → malli on selvästi heikompi kuin pelkkä markkina (jo tiedämme tämän)

Metriikat: Brier, NLL (log-loss), top-1 accuracy.
"""
import sys; sys.path.insert(0, "/home/ravi/app-ravi")
import sqlite3, json, glob
import pandas as pd, numpy as np
import lightgbm as lgb
from scipy.optimize import minimize_scalar
from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import predict_win_probabilities

EVAL_START = "2026-06-01"
EVAL_END   = "2026-07-01"
DATA_DIR   = "/home/ravi/app-ravi/data"

# ── Lataa data ──────────────────────────────────────────────────────────────
con = sqlite3.connect(f"{DATA_DIR}/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, ra.distance, ra.start_method AS race_start_method,"
    " h.birth_year FROM runners r JOIN races ra ON r.race_id = ra.race_id"
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
meta = json.load(open(latest_lgb.replace(".lgb", "_meta.json")))
T    = meta["temperature"]
model = lgb.Booster(model_file=latest_lgb)
print(f"Malli: {latest_lgb.split('/')[-1]}  T={T:.4f}")

preds_b = predict_win_probabilities(model, features, temperature=T)

# Yhdistä kertoimet
odds_df = runners[["race_id", "horse_id", "win_odds_final"]].copy()
df = preds_b.merge(odds_df, on=["race_id", "horse_id"], how="left")
feat_cols = ["race_id", "horse_id", "finish_position"]
df = df.merge(features[[c for c in feat_cols if c in features.columns]], on=["race_id", "horse_id"], how="left")
df["actual_win"] = (df["finish_position"] == 1).astype(int)

# Vain lähdöt joissa kertoimet JA tulos
has_result = df.groupby("race_id")["actual_win"].apply(lambda x: (x==1).any())
has_odds   = df.groupby("race_id")["win_odds_final"].apply(lambda x: x.notna().any())
valid      = has_result[has_result].index.intersection(has_odds[has_odds].index)
df = df[df["race_id"].isin(valid) & df["win_odds_final"].notna()].copy()
print(f"Evaluointidata: {len(valid)} lähtöä, {len(df)} hevosta\n")

# ── Malli A: devigattu + normalisoitu markkina ──────────────────────────────
# Yksinkertaisin devigointimenetelmä: normalisoi 1/kerroin summaan 1 per lähtö
def normalize_per_race(grp):
    raw = 1.0 / grp
    return raw / raw.sum()

df["market_raw"] = 1.0 / df["win_odds_final"]
df["model_A"]    = df.groupby("race_id")["market_raw"].transform(
    lambda x: x / x.sum()
)

# ── Metriikat ────────────────────────────────────────────────────────────────
EPS = 1e-9

def brier(df, prob_col):
    return float(((df[prob_col] - df["actual_win"]) ** 2).mean())

def nll(df, prob_col):
    return -float(np.log(df[prob_col].clip(EPS, 1-EPS) * df["actual_win"] +
                          (1 - df[prob_col]).clip(EPS, 1-EPS) * (1-df["actual_win"])).mean())

def top1_acc(df, prob_col):
    idx = df.groupby("race_id")[prob_col].idxmax()
    return float(df.loc[idx, "actual_win"].mean())

# ── Malli AB: log-odds -blendi (optimoitu α) ────────────────────────────────
# p_AB = sigmoid(α * logit(model_B) + (1-α) * logit(model_A))
# per-lähtö normalisointi
def logit(p, eps=EPS):
    p = np.clip(p, eps, 1-eps)
    return np.log(p / (1-p))

def blend_probs(df, alpha):
    """Blendi: alpha * B + (1-alpha) * A logit-tilassa, sitten softmax per lähtö."""
    raw_logit = alpha * logit(df["win_prob"]) + (1-alpha) * logit(df["model_A"])
    # Softmax per lähtö (numerinen stabiliteetti)
    df2 = df.copy()
    df2["raw_logit"] = raw_logit
    def softmax_grp(grp):
        ex = np.exp(grp - grp.max())
        return ex / ex.sum()
    df2["blend_prob"] = df2.groupby("race_id")["raw_logit"].transform(softmax_grp)
    return df2["blend_prob"]

def nll_for_alpha(alpha):
    probs = blend_probs(df, alpha)
    return -np.log(probs.clip(EPS, 1-EPS) * df["actual_win"] +
                   (1-probs).clip(EPS, 1-EPS) * (1-df["actual_win"])).mean()

res = minimize_scalar(nll_for_alpha, bounds=(0, 1), method="bounded")
opt_alpha = res.x
df["model_AB"] = blend_probs(df, opt_alpha)

# ── Tulostus ─────────────────────────────────────────────────────────────────
print("=" * 62)
print("MALLI A  = pelkkä markkina (devigattu + normalisoitu per lähtö)")
print("MALLI B  = 46-piirteen LightGBM (ei markkinasignaalia)")
print(f"MALLI AB = optimaalinen log-odds-blendi (α={opt_alpha:.3f}×B + {1-opt_alpha:.3f}×A)")
print("=" * 62)
print(f"\n{'':25} {'Brier':>8} {'NLL':>8} {'Top-1%':>8}")
print("-" * 55)
for label, col in [("A  (pelkkä markkina)", "model_A"),
                    ("B  (malli, ei markk.)", "win_prob"),
                    (f"AB (blendi α={opt_alpha:.2f})", "model_AB")]:
    b = brier(df, col)
    n = nll(df, col)
    t = top1_acc(df, col)
    print(f"{label:<25} {b:>8.4f} {n:>8.4f} {t:>7.1%}")

# ── Tulkinta ─────────────────────────────────────────────────────────────────
b_A  = brier(df, "model_A")
b_B  = brier(df, "win_prob")
b_AB = brier(df, "model_AB")
n_A  = nll(df, "model_A")
n_B  = nll(df, "win_prob")
n_AB = nll(df, "model_AB")

brier_gain = b_A - b_AB          # positiivinen = AB parempi kuin A
nll_gain   = n_A - n_AB
brier_pct  = 100 * brier_gain / b_A
nll_pct    = 100 * nll_gain / n_A

print("\n" + "=" * 62)
print("TULKINTA")
print("=" * 62)
print(f"Brier: A={b_A:.4f}  B={b_B:.4f}  AB={b_AB:.4f}")
print(f"  AB vs A: {brier_gain:+.4f} ({brier_pct:+.1f}%)")
print(f"NLL:   A={n_A:.4f}  B={n_B:.4f}  AB={n_AB:.4f}")
print(f"  AB vs A: {nll_gain:+.4f} ({nll_pct:+.1f}%)")
print()

if brier_pct > 1.0 and nll_pct > 1.0:
    print("✓ Blendi AB PARANTAA markkinaa (>1%) molemmilla mittareilla.")
    print("  → Mallissa on ortogonaalista signaalia markkinan päälle.")
    print(f"  → Optimaali α={opt_alpha:.2f}: {opt_alpha:.0%} mallin painoa blendiä varten.")
    print("  → Jatkosuunta: markkinapiirre + isotonic-kalibrointi + CLV-seuranta.")
elif abs(brier_pct) <= 1.0 and abs(nll_pct) <= 1.0:
    print("~ AB ≈ A (< 1% ero). Malli ei lisää merkittävää signaalia.")
    print("  → Mallin piirteet eivät sisällä tietoa markkinan ulkopuolelta.")
    print("  → Vaihtoehtoinen suunta: muut datalähteet tai tavoitteen uudelleenmäärittely.")
else:
    print("✗ AB ei paranna A:ta merkittävästi tai heikentää.")
    print("  → Malli ei lisää informaatiota markkinan päälle.")
    print("  → Faktapohjainen päätös: itsenäinen vetomalli ei tällä datalla toimi.")

print()
print(f"Muistutus: blendi-α={opt_alpha:.2f} = kuinka paljon mallin signaalin painoa")
print(f"optimaaliblendi haluaa. α→0 = malli arvoton, α→1 = malli korvaa markkinan.")
