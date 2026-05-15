"""
Ravit Edge — Streamlit-dashboard

Käyttö:  streamlit run src/dashboard/app.py
Tarkoitus: visuaalinen näkymä päivän V-pelilähdöille tutkimuskäyttöön.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import lightgbm as lgb
import pandas as pd
import streamlit as st

from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import predict_win_probabilities
from src.paths import DB_PATH

_MODEL_GLOB = "data/model_*.lgb"
_DEFAULT_EDGE_THRESHOLD = 5.0

st.set_page_config(page_title="Ravit Edge", layout="wide", page_icon="🏇")


@st.cache_resource
def load_model() -> lgb.Booster | None:
    models = sorted(Path(".").glob(_MODEL_GLOB))
    if not models:
        return None
    return lgb.Booster(model_file=str(models[-1]))


@st.cache_data(ttl=300)
def load_predictions(target_date: date, db_path: str) -> pd.DataFrame | None:
    try:
        con = sqlite3.connect(db_path)
        runners = pd.read_sql("""
            SELECT r.*, ra.race_date, ra.track, ra.race_number,
                   ra.distance, ra.start_method, ra.race_age_group,
                   h.birth_year, h.name as horse_name
            FROM runners r
            JOIN races ra ON r.race_id = ra.race_id
            LEFT JOIN horses h ON r.horse_id = h.horse_id
            WHERE ra.race_date = ?
        """, con, params=(str(target_date),))
        if len(runners) == 0:
            con.close()
            return None
        races = pd.read_sql("SELECT * FROM races WHERE race_date = ?", con, params=(str(target_date),))
        horse_starts = pd.read_sql("SELECT * FROM horse_starts WHERE withdrawn != 1", con)
        horses = pd.read_sql("SELECT * FROM horses", con)
        tracks = pd.read_sql("SELECT * FROM tracks", con)
        con.close()
    except Exception as e:
        st.error(f"DB-virhe: {e}")
        return None

    try:
        features = build_feature_matrix(
            fill_finish_positions(runners), races,
            horse_starts=horse_starts, horses=horses, tracks=tracks,
        )
    except Exception as e:
        st.error(f"Feature-virhe: {e}")
        return None

    model = load_model()
    if model is None:
        st.warning("Mallia ei löydy data/-hakemistosta.")
        return None

    try:
        preds = predict_win_probabilities(model, features)
        return features.merge(preds[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"], how="left")
    except Exception as e:
        st.error(f"Ennustevirhe: {e}")
        return None


def main() -> None:
    st.title("🏇 Ravit Edge — Päivän ennusteet")

    with st.sidebar:
        st.header("Asetukset")
        selected_date = st.date_input("Päivä", value=date.today())
        only_v_races = st.checkbox("Vain V-pelilähdöt", value=True)
        edge_threshold = st.slider("Value bet kynnys (%)", 1.0, 15.0, _DEFAULT_EDGE_THRESHOLD, 0.5)
        st.divider()
        st.caption("⚠️ Tutkimuskäyttöön. Älä käytä rahapelipäätöksiin.")

    db_path = str(DB_PATH)

    data = load_predictions(selected_date, db_path)
    if data is None:
        st.info(f"Ei dataa päivälle {selected_date}.")
        return

    if only_v_races and "is_v_race" in data.columns:
        v_data = data[data["is_v_race"] == 1]
        data = v_data if len(v_data) > 0 else data

    odds_col = "win_odds_final" if "win_odds_final" in data.columns else None
    if odds_col and "win_prob" in data.columns:
        data = data.copy()
        data["edge_pct"] = (data["win_prob"] * data[odds_col].fillna(1.0) - 1.0) * 100
    else:
        data = data.copy()
        data["edge_pct"] = float("nan")

    n_value = (data["edge_pct"] > edge_threshold).sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("Lähtöjä", data["race_id"].nunique())
    c2.metric("Hevosia", len(data))
    c3.metric(f"Value bets (>{edge_threshold:.0f} %)", int(n_value))
    st.divider()

    for race_id, rdf in data.groupby("race_id", sort=False):
        track = rdf["track"].iloc[0] if "track" in rdf.columns else str(race_id)
        rnum = rdf["race_number"].iloc[0] if "race_number" in rdf.columns else ""
        st.subheader(f"{track} — Lähtö {rnum}")

        name_col = "horse_name" if "horse_name" in rdf.columns else "horse_id"
        cols = ["start_number", name_col, "win_prob", odds_col, "edge_pct"]
        cols = [c for c in cols if c and c in rdf.columns]
        disp = rdf[cols].copy().sort_values("start_number", na_position="last")

        if "win_prob" in disp.columns:
            disp["win_prob"] = disp["win_prob"].map(lambda x: f"{x*100:.1f} %" if pd.notna(x) else "—")
        if "edge_pct" in disp.columns:
            disp["edge_pct"] = disp["edge_pct"].map(
                lambda x: (f"⭐ {x:+.1f} %" if x > edge_threshold else f"{x:+.1f} %") if pd.notna(x) else "—"
            )

        rename = {"start_number": "#", "horse_id": "Hevonen", "horse_name": "Hevonen", "win_prob": "P(win)", odds_col: "Odds", "edge_pct": "Edge"}
        disp = disp.rename(columns={k: v for k, v in rename.items() if k in disp.columns})
        st.dataframe(disp, hide_index=True, width="stretch")


if __name__ == "__main__":
    main()
