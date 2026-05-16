"""
Ravit Edge — Streamlit-dashboard

Käyttö:  streamlit run src/dashboard/app.py
Tarkoitus: visuaalinen näkymä päivän V-pelilähdöille tutkimuskäyttöön.

Ominaisuudet:
  - Päivän ennusteet hevosten voittotodennäköisyydelle (LGBMRanker)
  - Live-kertoimet odds_snapshots-taulusta (T-2min > T-5min > T-10min > T-15min)
  - ATG-hevosprofiililinkit (klikattavat)
  - SHAP-piirreanalyysi lähtökohtaisesti (valinnainen, vaatii shap-paketin)
  - Lähdöt ratakohtaisesti ryhmiteltyinä (expander per rata, järjestyksessä)
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

import lightgbm as lgb
import pandas as pd
import streamlit as st

from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import predict_win_probabilities, FEATURE_COLS, CATEGORICAL_COLS
from src.paths import DB_PATH

_MODEL_GLOB = "data/model_*.lgb"
_DEFAULT_EDGE_THRESHOLD = 5.0
_ATG_URL = "https://www.atg.se/hp/startlista/hast/{horse_id}"
_SNAPSHOT_PRIORITY = ["T-2min", "T-5min", "T-10min", "T-15min"]

st.set_page_config(page_title="Ravit Edge", layout="wide", page_icon="🏇")


# ---------------------------------------------------------------------------
# Resurssien lataus
# ---------------------------------------------------------------------------

@st.cache_resource
def load_model() -> lgb.Booster | None:
    models = sorted(Path(".").glob(_MODEL_GLOB))
    if not models:
        return None
    return lgb.Booster(model_file=str(models[-1]))


@st.cache_data(ttl=60)
def load_predictions(target_date: date, db_path: str) -> pd.DataFrame | None:
    """Lataa runners + features + ennusteet annetulle päivälle."""
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
              AND (r.withdrawn IS NULL OR r.withdrawn != 1)
        """, con, params=(str(target_date),))
        if len(runners) == 0:
            con.close()
            return None
        races = pd.read_sql(
            "SELECT * FROM races WHERE race_date = ?", con, params=(str(target_date),)
        )
        horse_starts = pd.read_sql(
            "SELECT * FROM horse_starts WHERE withdrawn != 1", con
        )
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
        return features.merge(
            preds[["race_id", "horse_id", "win_prob"]],
            on=["race_id", "horse_id"],
            how="left",
        )
    except Exception as e:
        st.error(f"Ennustevirhe: {e}")
        return None


@st.cache_data(ttl=60)
def load_live_odds(race_id: str, db_path: str) -> pd.DataFrame:
    """
    Hakee viimeisimmät pre-race-kertoimet odds_snapshots-taulusta.
    Prioriteetti: T-2min > T-5min > T-10min > T-15min.
    Palauttaa DataFramen sarakkeilla: runner_id, live_odds.
    """
    try:
        con = sqlite3.connect(db_path)
        df = pd.read_sql("""
            SELECT runner_id,
                   COALESCE(devigged_win_odds, win_odds) AS raw_odds,
                   snapshot_label,
                   captured_at
            FROM odds_snapshots
            WHERE snapshot_label IN ('T-2min', 'T-5min', 'T-10min', 'T-15min')
              AND runner_id LIKE ?
            ORDER BY captured_at DESC
        """, con, params=(f"{race_id}_%",))
        con.close()
    except Exception:
        return pd.DataFrame(columns=["runner_id", "live_odds"])

    if df.empty:
        return pd.DataFrame(columns=["runner_id", "live_odds"])

    # Valitse paras snapshot per runner_id (prioriteettijärjestyksessä)
    priority_map = {label: i for i, label in enumerate(_SNAPSHOT_PRIORITY)}
    df["priority"] = df["snapshot_label"].map(priority_map).fillna(99).astype(int)
    best = df.sort_values("priority").drop_duplicates("runner_id", keep="first")
    return best[["runner_id", "raw_odds"]].rename(columns={"raw_odds": "live_odds"})


# ---------------------------------------------------------------------------
# SHAP-analyysi
# ---------------------------------------------------------------------------

def render_shap_section(model: lgb.Booster, rdf: pd.DataFrame) -> None:
    """Näyttää SHAP-piirreanalyysin valitun lähdön hevosille."""
    try:
        import shap  # noqa: PLC0415
    except ImportError:
        st.info("SHAP-analyysi ei käytössä (asenna: pip install shap).")
        return

    # Käytä täsmälleen niitä sarakkeita joilla malli opetettiin
    # (sama logiikka kuin ranker.py:n train_ranker / predict_win_probabilities)
    model_features = model.feature_name()  # 42 piirrettä
    missing = [c for c in model_features if c not in rdf.columns]
    if len(missing) == len(model_features):
        st.warning("SHAP: piirresarakkeita ei löydy DataFramesta.")
        return

    # Rakenna X samalla tavalla kuin predict_win_probabilities:
    # kategoriset sarakkeet muutetaan category-tyypiksi
    cat_set = set(CATEGORICAL_COLS)
    X = rdf.reindex(columns=model_features)
    for col in model_features:
        if col in cat_set and col in X.columns:
            X[col] = X[col].astype("category")
        elif col not in rdf.columns:
            X[col] = 0  # puuttuva sarake täytetään nollalla

    try:
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X)
        # shap_values shape: (n_runners, n_features)
        shap_df = pd.DataFrame(shap_values, columns=model_features)
        horse_labels = rdf["horse_name"].fillna(rdf["horse_id"].astype(str)).values
        shap_df.index = horse_labels

        # Top-10 tärkeimmät piirteet (abs. keskiarvo yli lähdön)
        mean_abs = shap_df.abs().mean().sort_values(ascending=False).head(10)
        st.markdown("**Top-10 piirrettä (lähtö)**")
        st.bar_chart(mean_abs)

        # Per-hevonen SHAP-taulukko
        with st.expander("Hevoskohtaiset SHAP-arvot"):
            st.dataframe(
                shap_df[mean_abs.index].round(4),
                use_container_width=True,
            )
    except Exception as e:
        st.warning(f"SHAP-laskenta epäonnistui: {e}")


# ---------------------------------------------------------------------------
# Pääohjelma
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("🏇 Ravit Edge — Päivän ennusteet")

    with st.sidebar:
        st.header("Asetukset")
        selected_date = st.date_input("Päivä", value=date.today())
        only_v_races = st.checkbox("Vain V-pelilähdöt", value=True)
        edge_threshold = st.slider(
            "Value bet kynnys (%)", 1.0, 15.0, _DEFAULT_EDGE_THRESHOLD, 0.5
        )
        show_shap = st.checkbox("Näytä SHAP-analyysi", value=False)
        st.divider()
        if st.button("🔄 Päivitä nyt", help="Tyhjentää välimuistin ja hakee tuoreimman datan"):
            st.cache_data.clear()
            st.rerun()
        st.caption("⚠️ Tutkimuskäyttöön. Älä käytä rahapelipäätöksiin.")

    db_path = str(DB_PATH)

    data = load_predictions(selected_date, db_path)
    if data is None:
        st.info(f"Ei dataa päivälle {selected_date}.")
        return

    # V-race-suodatus
    if only_v_races and "is_v_race" in data.columns:
        v_data = data[data["is_v_race"] == 1]
        data = v_data if len(v_data) > 0 else data

    # Edge-% laskenta (win_odds_final jos saatavilla)
    odds_col: Optional[str] = "win_odds_final" if "win_odds_final" in data.columns else None
    data = data.copy()
    if odds_col and "win_prob" in data.columns:
        data["edge_pct"] = (data["win_prob"] * data[odds_col].fillna(1.0) - 1.0) * 100
    else:
        data["edge_pct"] = float("nan")

    # Ylätason metriikat
    n_value = (data["edge_pct"] > edge_threshold).sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("Lähtöjä", data["race_id"].nunique())
    c2.metric("Hevosia", len(data))
    c3.metric(f"Value bets (>{edge_threshold:.0f} %)", int(n_value))
    st.divider()

    # -----------------------------------------------------------------------
    # Ratakohtainen ryhmittely
    # -----------------------------------------------------------------------
    track_col = "track" if "track" in data.columns else None
    if track_col:
        tracks_sorted = sorted(data[track_col].dropna().unique())
    else:
        tracks_sorted = ["(tuntematon)"]

    model = load_model()

    for track_name in tracks_sorted:
        if track_col:
            track_data = data[data[track_col] == track_name].copy()
        else:
            track_data = data.copy()

        n_races = track_data["race_id"].nunique()
        n_vbets = (track_data["edge_pct"] > edge_threshold).sum()
        expander_label = (
            f"🏟️ **{track_name}** — {n_races} lähtöä"
            + (f"  ⭐ {int(n_vbets)} value bet" if n_vbets > 0 else "")
        )

        with st.expander(expander_label, expanded=True):
            # Lähdöt race_number-järjestyksessä
            race_order = (
                track_data.groupby("race_id")["race_number"]
                .first()
                .sort_values()
                .index.tolist()
            )

            for race_id in race_order:
                rdf = track_data[track_data["race_id"] == race_id].copy()
                rnum = rdf["race_number"].iloc[0] if "race_number" in rdf.columns else "?"

                st.markdown(f"#### Lähtö {rnum}")

                # Live-kertoimet odds_snapshots-taulusta
                live_odds = load_live_odds(str(race_id), db_path)
                if not live_odds.empty:
                    # runner_id = f"{race_id}_{start_number}"
                    rdf["runner_id"] = (
                        str(race_id) + "_" + rdf["start_number"].astype(str)
                    )
                    rdf = rdf.merge(live_odds, on="runner_id", how="left")
                    live_odds_col = "live_odds"
                else:
                    live_odds_col = None

                # Sarakkeet näyttöä varten
                name_col = "horse_name" if "horse_name" in rdf.columns else "horse_id"

                # ATG-linkki
                if "horse_id" in rdf.columns:
                    rdf["atg_link"] = rdf["horse_id"].apply(
                        lambda hid: _ATG_URL.format(horse_id=hid)
                    )
                    has_atg = True
                else:
                    has_atg = False

                # Muodosta näytettävien sarakkeiden lista
                show_cols = ["start_number", name_col]
                if has_atg:
                    show_cols.append("atg_link")
                show_cols.append("win_prob")
                if live_odds_col:
                    show_cols.append(live_odds_col)
                if odds_col:
                    show_cols.append(odds_col)
                show_cols.append("edge_pct")

                disp = rdf[[c for c in show_cols if c in rdf.columns]].copy()
                disp = disp.sort_values("start_number", na_position="last")

                # Formatointi
                if "win_prob" in disp.columns:
                    disp["win_prob"] = disp["win_prob"].map(
                        lambda x: f"{x*100:.1f} %" if pd.notna(x) else "—"
                    )
                if "edge_pct" in disp.columns:
                    disp["edge_pct"] = disp["edge_pct"].map(
                        lambda x: (
                            f"⭐ {x:+.1f} %" if x > edge_threshold else f"{x:+.1f} %"
                        ) if pd.notna(x) else "—"
                    )
                if live_odds_col and live_odds_col in disp.columns:
                    disp[live_odds_col] = disp[live_odds_col].map(
                        lambda x: f"{x:.2f}" if pd.notna(x) else "—"
                    )

                # Uudelleennimeäminen
                rename_map: dict[str, str] = {
                    "start_number": "#",
                    "horse_id": "Hevonen",
                    "horse_name": "Hevonen",
                    "win_prob": "P(win)",
                    "live_odds": "Live-kerroin",
                    "edge_pct": "Edge",
                }
                if odds_col:
                    rename_map[odds_col] = "Odds (final)"
                disp = disp.rename(
                    columns={k: v for k, v in rename_map.items() if k in disp.columns}
                )

                # Sarakkeiden konfiguraatio (ATG-linkki klikattavaksi)
                col_config: dict = {}
                if has_atg and "atg_link" in disp.columns:
                    disp = disp.rename(columns={"atg_link": "ATG"})
                    col_config["ATG"] = st.column_config.LinkColumn(
                        "ATG", display_text="🔗 ATG"
                    )

                st.dataframe(disp, hide_index=True, column_config=col_config or None)

                # SHAP-analyysi (valinnainen)
                if show_shap and model is not None:
                    with st.expander(f"SHAP — Lähtö {rnum}"):
                        render_shap_section(model, rdf)

                st.divider()


if __name__ == "__main__":
    main()
