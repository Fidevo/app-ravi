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

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
import lightgbm as lgb
import pandas as pd
import streamlit as st

from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import predict_win_probabilities, FEATURE_COLS, CATEGORICAL_COLS
from src.paths import DB_PATH

_MODEL_GLOB = "data/model_*.lgb"
_DEFAULT_EDGE_THRESHOLD = 5.0
_ATG_URL = "https://www.atg.se/travochgalopp/hast/{horse_id}"
_SNAPSHOT_PRIORITY = ["T-2min", "T-5min", "T-10min", "T-15min"]
_V_GAME_TYPES = {"V75", "V86", "V64", "V65", "V5", "V4", "V3"}
_ATG_CALENDAR_URL = "https://www.atg.se/services/racinginfo/v1/api/calendar/day/{date}"


@st.cache_data(ttl=300)
def get_v_race_ids(target_date: date) -> set[str]:
    """Hae V-pelilähtöjen race_id:t ATG-kalenterista."""
    try:
        url = _ATG_CALENDAR_URL.format(date=target_date.isoformat())
        r = httpx.get(url, timeout=10)
        games = r.json().get("games", {})
        v_ids: set[str] = set()
        for game_type, game_list in games.items():
            if game_type in _V_GAME_TYPES:
                for game in game_list:
                    v_ids.update(game.get("races", []))
        return v_ids
    except Exception:
        return set()

st.set_page_config(page_title="Ravit Edge", layout="wide", page_icon="🏇")


# ---------------------------------------------------------------------------
# Resurssien lataus
# ---------------------------------------------------------------------------

@st.cache_data
def _load_model_cached(model_path: str, _mtime: float) -> tuple[lgb.Booster, float]:
    """Välimuistitettu malli — invalidoituu automaattisesti kun tiedosto muuttuu.

    Palauttaa (model, temperature) -tuplen. Lukee temperature pipeline-ajon
    tallentamasta _meta.json-tiedostosta. Jos metaa ei löydy, T=1.0 (ei skaalausta).
    """
    booster = lgb.Booster(model_file=model_path)
    meta_path = model_path.replace(".lgb", "_meta.json")
    temperature = 1.0
    try:
        with open(meta_path) as _f:
            meta = json.load(_f)
            temperature = float(meta.get("temperature", 1.0))
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass  # Vanha malli ilman meta-tiedostoa — käytä T=1.0
    return booster, temperature


def load_model() -> tuple[lgb.Booster, float] | tuple[None, float]:
    """Lataa uusin malli data/-hakemistosta. Käyttää mtime-pohjaista välimuistia:
    jos model-tiedosto päivitetään (uusi treeni), uusi malli ladataan automaattisesti
    ilman palvelimen uudelleenkäynnistystä.

    Palauttaa (model, temperature) -tuplen. temperature=1.0 jos ei meta-tiedostoa.
    """
    models = sorted(Path(".").glob(_MODEL_GLOB))
    if not models:
        return None, 1.0
    path = str(models[-1])
    mtime = models[-1].stat().st_mtime
    return _load_model_cached(path, mtime)


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
              AND (r.withdrawn IS NULL OR r.withdrawn = 0)
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
        # HUOM: fill_finish_positions() on VAIN koulutusaineistolle — älä kutsu
        # sitä ennusteputkessa. Ennustetaan päivän lähtöjä joilla finish_position=NULL.
        features = build_feature_matrix(
            runners, races,
            horse_starts=horse_starts, horses=horses, tracks=tracks,
        )
    except Exception as e:
        st.error(f"Feature-virhe: {e}")
        return None

    model, temperature = load_model()
    if model is None:
        st.warning("Mallia ei löydy data/-hakemistosta.")
        return None

    # M1: Täytä market_implied_prob live-kertoimilla niille lähdöille
    # joilla win_odds_final=NULL (päivän tulevat lähdöt).
    # Treenidata käyttää closing-line win_odds_final:ia, ennustuksessa
    # käytetään odds_snapshots-taulun live-kertoimia.
    features = _inject_live_market_odds(features, db_path)

    try:
        preds = predict_win_probabilities(model, features, temperature=temperature)
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
# M1: Live-market-odds injektio ennusteputkeen
# ---------------------------------------------------------------------------

_SNAPSHOT_PRIORITY_MAP = {label: i for i, label in enumerate(_SNAPSHOT_PRIORITY)}


def _inject_live_market_odds(features: pd.DataFrame, db_path: str) -> pd.DataFrame:
    """Täytä market_implied_prob live-kertoimilla niille runnereille joilla se on NaN.

    Treenidata: win_odds_final saatavilla → market_implied_prob laskettu build_features:ssa.
    Ennustus (päivän lähdöt): win_odds_final=NULL → NaN → tämä funktio täyttää
    odds_snapshots-taulun live-kertoimilla (T-2min > T-5min > T-10min > T-15min).

    Devig lasketaan per lähtö jotta todennäköisyydet summautuvat 1.0:aan.
    Epäonnistuminen (ei kertoimia, ei saraketta) on hiljainen — malli käyttää NaN:ia.
    """
    if "market_implied_prob" not in features.columns:
        return features
    if not features["market_implied_prob"].isna().any():
        return features  # Kaikilla on jo arvo (esim. historiallinen data)

    try:
        con = sqlite3.connect(db_path)
        odds_df = pd.read_sql("""
            SELECT runner_id,
                   COALESCE(devigged_win_odds, win_odds) AS raw_odds,
                   snapshot_label
            FROM odds_snapshots
            WHERE snapshot_label IN ('T-2min', 'T-5min', 'T-10min', 'T-15min')
        """, con)
        con.close()
    except Exception:
        return features

    if odds_df.empty:
        return features

    # Pura race_id ja start_number runner_id:stä (format: "{race_id}_{start_number}")
    split = odds_df["runner_id"].str.rsplit("_", n=1, expand=True)
    odds_df["_race_id"] = split[0]
    odds_df["_start_number"] = split[1]

    # Suodata vain päivän lähdöt
    day_race_ids = features["race_id"].astype(str).unique()
    odds_df = odds_df[odds_df["_race_id"].isin(day_race_ids)]

    if odds_df.empty:
        return features

    # Paras snapshot per runner (T-2min > T-5min > T-10min > T-15min)
    odds_df["_priority"] = odds_df["snapshot_label"].map(_SNAPSHOT_PRIORITY_MAP).fillna(99).astype(int)
    best = (
        odds_df[odds_df["raw_odds"] > 1.0]
        .sort_values("_priority")
        .drop_duplicates("runner_id", keep="first")
        .copy()
    )

    if best.empty:
        return features

    # Devig per lähtö
    best["_raw_prob"] = 1.0 / best["raw_odds"]
    race_vig = best.groupby("_race_id")["_raw_prob"].sum().rename("_race_vig").reset_index()
    best = best.merge(race_vig, on="_race_id", how="left")
    best["_live_mip"] = best["_raw_prob"] / best["_race_vig"]

    # Merge features ← best via (race_id, start_number)
    features = features.copy()
    features["_race_id_str"] = features["race_id"].astype(str)
    features["_start_number_str"] = features["start_number"].astype(str)

    lookup = best.set_index(["_race_id", "_start_number"])["_live_mip"]

    mask = features["market_implied_prob"].isna()
    keys = list(zip(features.loc[mask, "_race_id_str"], features.loc[mask, "_start_number_str"]))
    live_values = [lookup.get(k, float("nan")) for k in keys]
    features.loc[mask, "market_implied_prob"] = live_values

    features = features.drop(columns=["_race_id_str", "_start_number_str"])
    return features


# ---------------------------------------------------------------------------
# Datan laatu -pisteytysjärjestelmä
# ---------------------------------------------------------------------------

# Piirteet järjestetty laskevaan tärkeydenjärjestykseen (feature importance
# 20260520-mallista). Nämä ovat tyypillisimmin NULL uusilla tai harvoilla lähdöillä.
_DQ_WEIGHTS: dict[str, float] = {
    "form_avg_finish_5":            0.63,
    "form_avg_km_time_5":           0.53,
    "form_best_km_time_5":          0.49,
    "form_market_avg_5":            0.41,
    "atg_best_km_for_this_setup":   0.32,
    "form_avg_finish_5_same_method":0.25,
    "trainer_top3_rate_365d":       0.24,
    "form_avg_finish_5_same_dist":  0.23,
    "driver_win_pct_365d":          0.18,
    "form_last_km_time":            0.16,
}
_DQ_MAX = sum(_DQ_WEIGHTS.values())


def _data_quality_label(row: pd.Series) -> str:
    """Palauttaa datan luotettavuustason hevoselle (0–100 %, 5 tasoa)."""
    score = sum(
        w for feat, w in _DQ_WEIGHTS.items()
        if feat in row.index and pd.notna(row[feat])
    ) / _DQ_MAX
    if score >= 0.80:
        return "✅ Vahva"
    if score >= 0.55:
        return "🟢 Hyvä"
    if score >= 0.30:
        return "🟡 Kohtalainen"
    if score >= 0.10:
        return "🔴 Heikko"
    return "⚫ Ei dataa"


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

    # V-race-suodatus: hae race_id:t ATG-kalenterista
    if only_v_races:
        v_ids = get_v_race_ids(selected_date)
        if v_ids:
            v_data = data[data["race_id"].isin(v_ids)]
            data = v_data if len(v_data) > 0 else data

    # Datan laatu per hevonen
    data["data_quality"] = data.apply(_data_quality_label, axis=1)

    # Edge-% laskenta (win_odds_final jos saatavilla)
    # TÄRKEÄ: lasketaan VAIN kun kerroin on olemassa — fillna(1.0) antaisi
    # harhaajohtavan -99% kun kertoimia ei ole vielä saatavilla.
    odds_col: Optional[str] = "win_odds_final" if "win_odds_final" in data.columns else None
    data = data.copy()
    if odds_col and "win_prob" in data.columns:
        # Odds = 0 on sama kuin NULL — tallennusvirhe, ei oikea kerroin
        valid_odds = data[odds_col].where(data[odds_col] > 1.0, other=float("nan"))
        data["edge_pct"] = (
            (data["win_prob"] * valid_odds - 1.0) * 100
        ).where(valid_odds.notna(), other=float("nan"))
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

    model, _temperature = load_model()

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

                    # Korjaus D1: päivitä edge_pct live-kertoimilla kun saatavilla.
                    # Aiemmin edge laskettiin vain win_odds_final:sta (koko data-tasolla),
                    # mutta live-kerroin on tuoreempi ja informatiivisempi ennen lähtöä.
                    if "win_prob" in rdf.columns and "edge_pct" in rdf.columns:
                        live_valid = rdf["live_odds"].where(
                            rdf["live_odds"] > 1.0, other=float("nan")
                        )
                        live_edge = (rdf["win_prob"] * live_valid - 1.0) * 100
                        # Käytä live-kerrointa kun saatavilla; fallback win_odds_final-edgeen
                        rdf["edge_pct"] = live_edge.where(
                            live_valid.notna(), other=rdf["edge_pct"]
                        )
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
                show_cols.append("data_quality")

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
                    "data_quality": "Data",
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
