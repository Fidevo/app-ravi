# Frontend-päätös — Streamlit vai Astro?

> Auditoija: Claude (Opus 4.7), 15.5.2026
> Käyttäjä kysyi: "Kannattaisiko tässä vaiheessa tehdä frontend esim Astrolla
> niin että voin katsoa visuaalisesti tulevia mallin arvioita. Tämä voisi
> olla joku kohtuu yksinkertainen toteutus."

---

## TL;DR

**Suositus: Streamlit, ~1 työpäivä, tee nyt.**

Astro on **liian iso** tähän käyttötarpeeseen alkuvaiheessa. Streamlit antaa
saman lopputuloksen huomattavasti vähemmällä työllä ja sopii projektin
nykyiseen Python-pinoon. ROADMAP.md jo mainitsee Streamlitin Vaihe 6:ssa
— se kannattaa rakentaa nyt prototyyppinä joka laajenee Vaihe 6:n yhteyteen.

---

## Mihin tämä vastaa

**Käyttäjän tarve:**
- Katsoa visuaalisesti tulevia mallin arvioita
- "Kohtuu yksinkertainen toteutus"
- 1 käyttäjä (henkilökohtainen tutkimus)
- Ei tarvetta autentikointiin eikä julkiseen internet-saatavuuteen

**Käyttöskenaario:**
- Ravipäivän aamuna: avaa selain → näe päivän V-pelilähdöt
- Per lähtö: hevoset, mallin ennuste, markkina-kerroin, edge-prosentti
- Highlight: value-pelit (edge > 5 %)
- (Vaihe 4:ssa lisättävissä: paperitestaus-näkymä, CLV-historia)

---

## Streamlit vs. Astro — vertailu

| Aspekti | Streamlit | Astro |
|---|---|---|
| **Työmäärä** | ~1 työpäivä prototyyppi | 3–5 päivää (vaatii API-rajapinnan) |
| **Stack** | Python-natiivi, sama venv | Node + TypeScript + Python-API |
| **Datapääsy** | `pd.read_sql()` suoraan DB:hen | Vaatii FastAPI-välikerroksen |
| **Mallin lataus** | `lgb.Booster(model_file=...)` suoraan | API-endpoint |
| **Deploy** | `streamlit run app.py` | Build + static-host + API-host |
| **Julkinen saatavuus** | localhost vain (helppo) | Helppoa julkaista |
| **UI-kauneus** | Funktionaalinen, ei räätälöinti | Modernimpi, brändättävissä |
| **Mobiili** | Toimii mutta ei optimoitu | Responsive luonnostaan |
| **Tuotantokäyttö** | OK 1–10 käyttäjälle | Skaalautuu 1000+ käyttäjään |

### Streamlit-edut tähän käyttöön

1. **Sama Python-pino** — käyttää suoraan `build_features.py`, `ranker.py`,
   `predict_win_probabilities()` ilman API:a
2. **1 päivä työtä** — yksi tiedosto `src/dashboard/app.py`
3. **Ei build-vaihetta** — `streamlit run` käynnistää heti
4. **Sopii alkuvaiheen tutkimukseen** — voi muuttaa nopeasti kun mallia kehitetään
5. **Jo ROADMAPissa** (V6) — tämä ei poikkea suunnitelmasta

### Astro-edut (myöhemmin, jos)

1. **Mobiili-friendly** — jos haluat katsoa puhelimella ravipäivänä
2. **Julkaisu internetissä** — jos haluat näyttää mallin tuloksia muille
3. **TypeScript-robusti** — paremmin testattavissa kuin Streamlit
4. **Erottelu front + back** — Python-API pysyy puhtaana, frontend skaalautuu

---

## Käyttäjälle: realistinen suositus

### Faasi 1 — NYT: Streamlit-prototyyppi (1 työpäivä)

Tee yksinkertainen "päivän ennusteet" -näkymä:

```
┌─────────────────────────────────────────────────┐
│ Ravit Edge — Päivän ennusteet                   │
├─────────────────────────────────────────────────┤
│ Päivä: [2026-05-15 ▼]   Vain V-pelilähdöt: ☑   │
│                                                 │
│ ─── Solvalla V64 ───                            │
│ Lähtö 4 (V64-1) — 18:35                         │
│ ┌─────┬──────────────┬────────┬─────┬──────────┐│
│ │  #  │ Hevonen      │ P(win) │ Odds│ Edge %   ││
│ ├─────┼──────────────┼────────┼─────┼──────────┤│
│ │  1  │ Pearl Boko   │ 18.2 % │ 4.5 │  -18 %   ││
│ │  2  │ Brigo        │ 24.1 % │ 3.5 │  -16 %   ││
│ │  3  │ I Choose You │ 8.4 %  │ 12.0│   ⭐ +1 %││← value bet
│ │ ... │              │        │     │          ││
│ └─────┴──────────────┴────────┴─────┴──────────┘│
│                                                 │
│ ─── Solvalla V64 ───                            │
│ Lähtö 5 (V64-2) — 19:00                         │
│ ...                                             │
└─────────────────────────────────────────────────┘
```

**Tekninen toteutus:**

`src/dashboard/app.py` (~150 riviä):

```python
import streamlit as st
import pandas as pd
import sqlite3
import lightgbm as lgb
from datetime import date, timedelta

from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.models.ranker import predict_win_probabilities, apply_isotonic, calibrate_isotonic
from src.paths import DB_PATH

st.set_page_config(page_title="Ravit Edge", layout="wide")

# Sidebar — päivän valinta
selected_date = st.sidebar.date_input("Päivä", value=date.today())
only_v_races = st.sidebar.checkbox("Vain V-pelilähdöt", value=True)
edge_threshold = st.sidebar.slider("Value bet kynnys (%)", 1.0, 10.0, 5.0)

# Lataa malli
@st.cache_resource
def load_model():
    return lgb.Booster(model_file="data/model_baseline_20260514.lgb")

# Lataa data
@st.cache_data(ttl=300)  # 5 min cache
def load_predictions(target_date: date):
    con = sqlite3.connect(DB_PATH)
    runners = pd.read_sql(f"""
        SELECT r.*, ra.race_date, h.birth_year
        FROM runners r
        JOIN races ra ON r.race_id = ra.race_id
        LEFT JOIN horses h ON r.horse_id = h.horse_id
        WHERE ra.race_date = '{target_date}'
    """, con)
    races = pd.read_sql("SELECT * FROM races WHERE race_date = ?", con, params=(str(target_date),))
    horse_starts = pd.read_sql("SELECT * FROM horse_starts WHERE withdrawn != 1", con)
    horses = pd.read_sql("SELECT * FROM horses", con)
    tracks = pd.read_sql("SELECT * FROM tracks", con)
    con.close()

    if len(runners) == 0:
        return None

    features = build_feature_matrix(
        fill_finish_positions(runners), races,
        horse_starts=horse_starts, horses=horses, tracks=tracks,
    )
    model = load_model()
    preds = predict_win_probabilities(model, features)
    # Yhdistä ennustetiedot + kertoimet + lähdön metadata
    return features.merge(preds[["race_id","horse_id","win_prob"]], on=["race_id","horse_id"])

# Pää-UI
st.title("Ravit Edge — Päivän ennusteet")
data = load_predictions(selected_date)
if data is None:
    st.warning(f"Ei lähtöjä päivälle {selected_date}")
else:
    # Suodata V-pelilähdöt jos valittu
    if only_v_races and "is_v_race" in data.columns:
        data = data[data["is_v_race"] == 1]

    # Laske edge (käyttää win_odds_final jos tulokset jo, muuten T-2min)
    data["expected_value"] = data["win_prob"] * data["win_odds_final"].fillna(data["win_odds_final"].mean())
    data["edge_pct"] = (data["expected_value"] - 1.0) * 100
    data["is_value_bet"] = data["edge_pct"] > edge_threshold

    # Ryhmittele lähtöittäin
    for race_id, race_group in data.groupby("race_id"):
        track = race_group["track"].iloc[0]
        race_number = race_group["race_number"].iloc[0]
        st.subheader(f"{track} — Lähtö {race_number}")

        display = race_group[["start_number", "horse_id", "win_prob", "win_odds_final", "edge_pct"]].copy()
        display["win_prob"] = display["win_prob"].map(lambda x: f"{x*100:.1f} %")
        display["edge_pct"] = display["edge_pct"].map(lambda x: f"{'⭐ ' if x > edge_threshold else ''}{x:+.1f} %")
        display = display.rename(columns={
            "start_number": "#", "horse_id": "Hevonen",
            "win_prob": "P(win)", "win_odds_final": "Odds", "edge_pct": "Edge",
        })
        st.dataframe(display, hide_index=True, use_container_width=True)
```

**Käynnistys:**
```bash
streamlit run src/dashboard/app.py
# → http://localhost:8501
```

### Faasi 2 — MYÖHEMMIN (Vaihe 6+): Astro jos haluat

Jos myöhemmin haluat:
- Mobiilikäytön (puhelimella ravipäivänä)
- Julkaisun internetissä (näyttää muille)
- Brändätyn UI:n

Silloin tee Astro-frontend + FastAPI-backend. Mutta tämä ei ole alkuvaiheen
prioriteetti.

---

## Aikataulu

```
NYT (1 työpäivä):
  • src/dashboard/app.py — Streamlit-prototyyppi
  • requirements.txt: lisää streamlit
  • README.md: lisää dashboard-osio

VAIHE 4 (~3.6.2026):
  • Laajenna dashboardia paperitestauksen näkymillä
  • CLV-historia, value-bet-tracking

VAIHE 6+ (jos malli tuottava):
  • Harkitse Astro-frontend julkista käyttöä varten
  • FastAPI-backend
```

---

## Yksi tärkeä varoitus

**Älä tee streamlitistä monimutkaista.** Yhden tiedoston, ~150 rivin
prototyyppi riittää alkuvaiheessa. Tärkein arvo on **nopea visuaalinen
takaisinsyöttö** mallin ennusteista — ei kaunis UI.

Lisäksi muista: malli on edelleen **prototyyppi**. Brier-paranema 0.0023 vs.
uniform on **pieni**. Älä luota mallin ennusteisiin rahapelipuolella ennen
kuin 8+ viikkoa dataa on kerätty ja Vaihe 5:n päätöskriteerit täyttyvät.

Streamlit-dashboardin tarkoitus on **tutkia mallia visuaalisesti**, ei tehdä
peliä siitä.

---

## Päätös: tee Streamlit nyt

**Aikabudjetti:** 1 työpäivä (helppo skaalata vaikeammaksi myöhemmin).

**Hyöty:** näet päivittäin missä malli on samaa mieltä kuin markkina ja missä
se on eri mieltä. Tämä on **kallisarvoista debug-tietoa** Vaihe 3:n ja D2:n
ymmärtämiseen.

**Riski:** ei mitään olennaista. Ei lisää API-rasitusta, ei muuta tuotanto­
koodia, ei vaikuta mallin treenaukseen.

**Suositus koodarille:** Tee Streamlit-prototyyppi heti Vaihe 3 -parannusten
(#7 + #8 + #9) jälkeen samalla viikolla. ~1 työpäivä, käytä yllä olevaa
koodirunkoa pohjana.
