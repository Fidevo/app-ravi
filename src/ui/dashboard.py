"""
Streamlit-dashboard: päivän lähdöt, value-pelit, EV-laskelma.

Aja: streamlit run src/ui/dashboard.py
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from src.data.atg_client import ATGClient
from src.models.ranker import kelly_fraction, load_model

st.set_page_config(page_title="Ravit Edge", layout="wide")

st.title("🐴 Ravit Edge - Value-detector")
st.caption("Ruotsin ravien todennäköisyyslaskuri ja value-pelien tunnistin")

# ---- Sidebar ----
with st.sidebar:
    st.header("Asetukset")
    selected_date = st.date_input("Päivämäärä", date.today())
    edge_threshold = st.slider("Min. edge (%)", 0.0, 30.0, 5.0, 0.5) / 100
    bankroll = st.number_input("Pelikassa (SEK)", min_value=0, value=10000, step=500)
    kelly_frac = st.slider("Kelly-fraktio", 0.05, 1.0, 0.25, 0.05)

# ---- Päivän lähdöt ----
st.subheader(f"📅 Lähdöt {selected_date}")

@st.cache_data(ttl=300)
def fetch_calendar(d: date) -> pd.DataFrame:
    with ATGClient() as atg:
        data = atg.get_calendar_day(d)
    rows = []
    for track in data.get("tracks", []):
        for race in track.get("races", []):
            rows.append({
                "Rata": track.get("name"),
                "Lähtö": race.get("number"),
                "Klo": race.get("startTime", "")[:16],
                "Matka (m)": race.get("distance"),
                "Lähtötapa": race.get("startMethod"),
                "race_id": race.get("id"),
            })
    return pd.DataFrame(rows)

try:
    calendar_df = fetch_calendar(selected_date)
    if calendar_df.empty:
        st.info("Ei lähtöjä valittuna päivänä.")
    else:
        st.dataframe(calendar_df, use_container_width=True, hide_index=True)

        race_options = calendar_df["race_id"].tolist()
        selected_race = st.selectbox(
            "Valitse lähtö analysoitavaksi",
            options=race_options,
            format_func=lambda rid: (
                f"{calendar_df.loc[calendar_df['race_id']==rid, 'Rata'].iloc[0]} "
                f"L{calendar_df.loc[calendar_df['race_id']==rid, 'Lähtö'].iloc[0]} "
                f"klo {calendar_df.loc[calendar_df['race_id']==rid, 'Klo'].iloc[0]}"
            ),
        )

        # Tähän kohtaan tulee mallin ennuste kun se on treenattu:
        # 1. Hae race-data ATG:lta
        # 2. Rakenna feature-matriisi
        # 3. Ennusta win_prob per hevonen
        # 4. Yhdistä bookkerin kertoimiin
        # 5. Näytä value-pelit ja Kelly-suositus

        st.info(
            "👉 Mallin ennustenäkymä aktivoituu kun treenaat mallin "
            "(notebooks/01_train_model.ipynb)."
        )

except Exception as e:
    st.error(f"Datan haku epäonnistui: {e}")

# ---- Selitykset ----
with st.expander("ℹ️ Miten tätä luetaan"):
    st.markdown("""
    **Edge** = mallin P(voitto) × kerroin - 1. Esim. P=0.20, kerroin=6.0
    → edge = 0.20 × 6.0 - 1 = 0.20 = **+20%**.

    **Kelly-suositus** kertoo paljonko pelikassasta panostetaan. Käytä
    fraktioitua Kellyä (0.25-0.5) etenkin alussa - täysi Kelly olettaa
    että mallisi on täydellisen kalibroitu, eikä se ole.

    **Value-kynnys 5%** on minimi jolla maksu on järkevä ATG:n marginaalin
    ja varianssin huomioiden. Jos paperitestauksessa haet ROI:n, voit
    laskea kynnystä; jos häviät, nosta sitä.
    """)
