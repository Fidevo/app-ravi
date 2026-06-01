"""Generoi start_position_win_rate -hakutaulu live-ennustukselle.

Ajettava kerran nykyisestä datasta. Tallentaa
data/model_baseline_20260526_spwr_lookup.csv jota check_todays_preds.py käyttää.
Korvautuu automaattisesti seuraavassa uudelleenkoulutuksessa (retrain_model.py
tallentaa uuden automaattisesti).
"""
import sys
sys.path.insert(0, "/home/ravi/app-ravi")
import sqlite3
import pandas as pd
from src.features.build_features import compute_start_position_lookup, fill_finish_positions

con = sqlite3.connect("/home/ravi/app-ravi/data/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date, ra.distance, ra.start_method AS race_start_method,"
    " h.birth_year FROM runners r"
    " JOIN races ra ON r.race_id = ra.race_id"
    " LEFT JOIN horses h ON r.horse_id = h.horse_id",
    con,
)
races = pd.read_sql("SELECT * FROM races", con)
con.close()

if "start_method" not in runners.columns:
    runners = runners.rename(columns={"race_start_method": "start_method"})
elif "race_start_method" in runners.columns:
    runners["start_method"] = runners["start_method"].fillna(runners["race_start_method"])
    runners = runners.drop(columns=["race_start_method"])

print(f"Runners: {len(runners)}, Races: {len(races)}")
runners_filled = fill_finish_positions(runners)
print("Lasketaan SPWR-hakutaulu...")
spwr_lookup = compute_start_position_lookup(runners_filled, races)
print(f"Hakutaulu: {len(spwr_lookup)} riviä")
print(f"  Ei-NaN rivejä: {spwr_lookup['start_position_win_rate'].notna().sum()}")
print(f"  Raidat: {sorted(spwr_lookup['track'].unique())}")

out = "/home/ravi/app-ravi/data/model_baseline_20260526_spwr_lookup.csv"
spwr_lookup.to_csv(out, index=False)
print(f"Tallennettu: {out}")
