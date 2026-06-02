"""Train/serve-skew -vahti: vertaa FEATURE_COLS:in NaN-%:a treeni- vs. live-syötteellä.

Tausta (auditointi 1.6.2026): build_feature_matrix() ajetaan treenissä
runners=koko historia, mutta livessä runners=vain tämän päivän lähdöt. Osa
piirteistä lasketaan pelkästä runners-rolling-ikkunasta → ne ovat populoituja
treenissä mutta NaN livessä (train/serve-skew). Tämä litistää live-jakauman
eikä näy treeni/test-Brierissä.

Tämä skripti pyydystää skew'n automaattisesti:
  1. Rakentaa piirteet KOKO datalla (kuten retrain_model.py).
  2. Rakentaa piirteet yhden päivän viipaleella (kuten check_todays_preds.py:
     runners=yksi päivä, horse_starts=koko historia, spwr_lookup + all_races).
  3. Vertaa per-piirteen NaN-%:a. Piirre joka on ~täynnä dataa treenissä mutta
     ~kokonaan NaN livessä = skew-epäilty.

Exit-koodi 1 jos skew-epäiltyjä löytyy (sopii CI/pre-deploy-vahdiksi).

Aja:  python scripts/check_serve_skew.py
Ympäristö: RAVI_DATA_DIR (oletus /home/ravi/app-ravi/data). Paikallista testiä
varten: RAVI_DATA_DIR=data python scripts/check_serve_skew.py
"""
import os
import sys
import glob
import sqlite3

import pandas as pd

# Salli ajo sekä palvelimella (/home/ravi/app-ravi) että repo-juuresta paikallisesti.
sys.path.insert(0, "/home/ravi/app-ravi")
sys.path.insert(0, os.getcwd())

from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import FEATURE_COLS

DATA_DIR = os.environ.get("RAVI_DATA_DIR", "/home/ravi/app-ravi/data")
DB_PATH = f"{DATA_DIR}/ravit.db"

# Kynnykset skew-epäillylle: piirre on selvästi populoitu treenissä mutta
# lähes kokonaan NaN livessä.
LIVE_NAN_MIN = 0.90   # livessä >= 90 % NaN
SKEW_MIN = 0.50       # live_nan - train_nan >= 0.50 (eli paljon huonompi livessä)

_HS_FILTER = (
    "SELECT * FROM horse_starts"
    " WHERE (withdrawn IS NULL OR withdrawn != 1)"
    "   AND (finish_position IS NULL OR finish_position != 99)"
    "   AND (race_date IS NULL OR race_date >= '2024-01-01')"
)

_RUNNERS_SELECT = (
    "SELECT r.*, ra.race_date, ra.distance, ra.start_method AS race_start_method,"
    " h.birth_year FROM runners r"
    " JOIN races ra ON r.race_id = ra.race_id"
    " LEFT JOIN horses h ON r.horse_id = h.horse_id"
)


def _fix_start_method(runners: pd.DataFrame) -> pd.DataFrame:
    """Sama start_method-yhdistys kuin retrain/check_todays_preds-skripteissä."""
    if "start_method" not in runners.columns:
        return runners.rename(columns={"race_start_method": "start_method"})
    if "race_start_method" in runners.columns:
        runners["start_method"] = runners["start_method"].fillna(runners["race_start_method"])
        runners = runners.drop(columns=["race_start_method"])
    return runners


def main() -> int:
    con = sqlite3.connect(DB_PATH)
    runners_all = _fix_start_method(pd.read_sql(_RUNNERS_SELECT, con))
    races_all = pd.read_sql("SELECT * FROM races", con)
    hs = pd.read_sql(_HS_FILTER, con)
    horses = pd.read_sql("SELECT * FROM horses", con)
    tracks = pd.read_sql("SELECT * FROM tracks", con)
    con.close()

    # Uusin spwr_lookup (kuten check_todays_preds.py)
    spwr_files = sorted(glob.glob(f"{DATA_DIR}/model_baseline_*_spwr_lookup.csv"))
    spwr_lookup = pd.read_csv(spwr_files[-1]) if spwr_files else None

    # --- 1. Treeni-build: koko data (kuten retrain_model.py) ---
    feat_train = build_feature_matrix(
        fill_finish_positions(runners_all), races_all,
        horse_starts=hs, horses=horses, tracks=tracks,
    )

    # --- 2. Live-build: viimeisin päivä jolla on runnereita ---
    live_date = runners_all["race_date"].max()
    runners_live = runners_all[runners_all["race_date"] == live_date].copy()
    races_live = races_all[races_all["race_date"] == live_date].copy()
    feat_live = build_feature_matrix(
        runners_live, races_live,
        horse_starts=hs, horses=horses, tracks=tracks,
        spwr_lookup=spwr_lookup, all_races=races_all,
    )

    print(f"Treeni-build: {len(feat_train)} riviä | "
          f"Live-build ({live_date}): {len(feat_live)} riviä | "
          f"FEATURE_COLS={len(FEATURE_COLS)}")
    if spwr_lookup is None:
        print("HUOM: spwr_lookup ei löytynyt — start_position_win_rate näkyy NaN:nä "
              "(ei aito skew, vaan puuttuva lookup).")

    present = [c for c in FEATURE_COLS if c in feat_train.columns and c in feat_live.columns]
    rows = []
    for c in present:
        tr = float(feat_train[c].isna().mean())
        lv = float(feat_live[c].isna().mean())
        rows.append((c, tr, lv, lv - tr))
    rows.sort(key=lambda r: r[3], reverse=True)

    print(f"\n{'piirre':<34} {'treeni-NaN':>10} {'live-NaN':>10} {'skew':>8}")
    print("-" * 66)
    for c, tr, lv, sk in rows:
        mark = "  <== SKEW" if (lv >= LIVE_NAN_MIN and sk >= SKEW_MIN) else ""
        print(f"{c:<34} {tr*100:9.0f}% {lv*100:9.0f}% {sk*100:7.0f}%{mark}")

    suspects = [c for c, tr, lv, sk in rows if lv >= LIVE_NAN_MIN and sk >= SKEW_MIN]
    print()
    if suspects:
        print(f"[VAROITUS] Train/serve-skew-epäiltyjä: {len(suspects)} piirrettä")
        print(f"   {suspects}")
        print("   Nämä ovat populoituja treenissä mutta ~kokonaan NaN livessä.")
        print("   Korjaa joko (a) tee piirre live-symmetriseksi (horse_starts/lookup)")
        print("   tai (b) poista se FEATURE_COLS:ista (ranker.py).")
        return 1
    print("[OK] Ei train/serve-skew-epäiltyjä FEATURE_COLS:issa.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
