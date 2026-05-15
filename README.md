# Ravit Edge

Ruotsin ravien voittotodennäköisyyslaskin ja value-bet-detektori.
Rakentaa ML-mallin julkisista ATG-, Travsport- ja Travrondenspel-
rajapinnoista ilman autentikointia tai maksullisia datalähteitä.

**Pelistrategia:** fixed odds (Unibet / Betsson) + Betfair Exchange,
**single-win-markkinaan V-pelilähdöistä**. ATG-totoa eikä V-peliä
(multi-leg-tuotteita) pelata — 15–25 % takeout estää pitkän aikavälin tuoton.

**Strateginen fokus:** ennustetuotanto ja paperitestaus keskittyvät
**V-pelilähtöihin** (V64/V75/V86/V5/V4/V3) koska:
- Travrondenin pace-arvio (`start_interval_group`) saatavilla vain niissä
- Paras markkinaliikkuvuus ja kertoimet
- Paras likviditeetti Betfairissa

Datankeräys ja mallin treenaus käyttävät **kaikkia SE-trottilähtöjä**.

---

## Miten tämä toimii — lyhyesti

```
ATG REST API     Travsport WebAPI    Travrondenspel
      │                │                    │
      ▼                ▼                    ▼
  scheduler.py    travsport.py        travronden.py
  (4×/lähtö)      (hevoshistoria)    (V-pelit, 2-3×/päivä)
      │                │                    │
      └────────────────┼────────────────────┘
                       ▼
                 ravit.db (SQLite)
                 ├── races          (455 lähtöä, 18 pv)
                 ├── runners        (4 838 starttia + tr_*-piirteet)
                 ├── horses         (4 114 hevosta, sire/dam_sire)
                 ├── horse_starts   (115 824 starttia, koko ura)
                 ├── tracks         (30 rataa, rakennepiirteet)
                 └── odds_snapshots (T-15/10/5/2min + tulos)
                       │
                       ▼
              build_feature_matrix()
              fill_finish_positions()
                       │
                       ▼
              train_ranker()  ←  LightGBM LambdaRank
                       │                  (KAIKKI lähdöt)
                       ▼
              predict_win_probabilities()  →  softmax + isotonic/temperature
                       │                  (V-pelilähdöt päivittäin)
                       ▼
              detect_value_bets()  →  value bet jos P(voitto)×kerroin > 1.05
```

---

## ML-malli

**Algoritmi:** LightGBM LambdaRank (learning to rank, ei binääriluokittelu).

Hevoset eivät ole riippumattomia — kyseessä on kilpailu. LambdaRank oppii
järjestämään hevoset oikeaan järjestykseen saman lähdön sisällä, mikä on
oikeampi tapa lähestyä ongelmaa kuin yksittäisen hevosen binääriluokittelu.

**Kalibrointi:** raw-pisteet muunnetaan todennäköisyyksiksi softmaxilla per
lähtö. Tällöin todennäköisyydet summautuvat 1.0:aan per lähtö.

**Piirteet (tärkeimmät ryhmät):**

| Ryhmä | Esimerkkejä | Lähde |
|---|---|---|
| Hevosen muoto | `form_avg_finish_5`, `form_win_rate_5`, `form_days_since_last` | `horse_starts` (103k starttia) |
| ATG-aggregaatit | `atg_lifetime_win_rate`, `atg_best_km_for_this_setup` | ATG per startti |
| Ohjastaja/valmentaja | `atg_driver_win_pct`, `driver_win_rate_365d` | ATG + rolling |
| Lähtöasetelma | `inside_post`, `back_row`, `distance_category` | Lähtökortti |
| Lähdön luokka | `race_min_earnings`, `race_max_earnings`, `race_age_group` | ATG terms-parsinta |
| Varusteet | `shoes_changed_front`, `sulky_changed`, `sulky_type` | ATG per startti |
| Sukutaulu *(odottaa aktivointia ~7/2026)* | `sire`, `dam_sire` | ATG pedigree (sire 100 %, dam_sire 34 %) |
| Johdetut | `barfota_law_active`, `horse_age` | Laskettu |
| Ratarakenne | `track_length_home_stretch`, `track_open_stretch`, `track_dosage` | Travronden `round.tracks` (30 SE-rataa) |
| Kalibrointi | temperature scaling tai isotoninen regressio | Jälkikäteen validointidatasta |

**Tärkeä yksityiskohta — treeniesimerkit:**
ATG raportoi viralliset sijoitukset vain top 6–8 hevoselle per lähtö.
`fill_finish_positions()` täyttää loput km-ajan perusteella ennen treeniä.

> **Sire-piirteet:** kommentoitu pois FEATURE_COLS:ista (KNOWN_ISSUES #13)
> — empiirinen ablation näytti että ne eivät paranna mallia tällä datalla
> (Brier delta +0.0005). Palautetaan ~7/2026 kun 8+ vk:n data riittää
> luotettavaan arviointiin.

---

## Tietokanta — taulut

| Taulu | Rivejä (14.5.2026) | Kuvaus |
|---|---|---|
| `races` | 455 | Lähtöjen perustiedot, luokka, ratakunto |
| `runners` | 4 838 | Starttaavat hevoset, kertoimet, tulokset, kengät |
| `horses` | 4 114 | Hevosen perustiedot, syntymävuosi, isä, emänisä |
| `horse_starts` | 115 824 | Hevosen koko ura Travsportista (2014→) |
| `odds_snapshots` | ~18 000 | Pre-race kertoimet (4 snapshotia/lähtö) |
| `tracks` | 30 | Ratarakenne: pituudet, avosuora, kulmasiiveke, dosage |

Runners-taulu sisältää ATG:n valmiit aggregaatit (`atg_*`-sarakkeet) jotka
kattavat koko kuluvan vuoden — paljon kattavammat kuin meidän 14 päivän
keräyksestä lasketut rolling-tilastot.

---

## Stack

- **Python 3.12** (venv), ajetaan Hetzner CAX11 -palvelimella (Suomi)
- **SQLite WAL-mode** — riittää MVP:lle, Postgres harkinnassa jos DB > 500 MB
- **Datalähteet** (julkiset, ei autentikointia, rate limit 1 req/s):
  - ATG REST API: `https://www.atg.se/services/racinginfo/v1/api`
  - Travsport WebAPI: `https://api.travsport.se/webapi`
- **Riippuvuudet:** `httpx`, `tenacity`, `apscheduler`, `sqlalchemy`,
  `pandas`, `numpy`, `lightgbm`, `scikit-learn`, `streamlit`, `pytest`
  — täydellinen lista `requirements.txt`:ssä

---

## Projektin rakenne

```
ravit-edge/
├── src/
│   ├── paths.py                    PROJECT_ROOT-vakiot
│   ├── data/
│   │   ├── schema.py               SQLite-skeema + WAL-migraatio
│   │   ├── atg_client.py           ATG REST API -asiakas (SE-only whitelist)
│   │   ├── scheduler.py            Cron-ajot, snapshotit, result-haku,
│   │   │                           backfill-komennot
│   │   └── scrapers/
│   │       └── travsport.py        Hevoshistorian WebAPI-asiakas
│   ├── features/
│   │   └── build_features.py       Feature engineering -pipeline:
│   │                               form_features(), driver_trainer_features(),
│   │                               race_setup_features(), derived_features(),
│   │                               fill_finish_positions(),
│   │                               build_feature_matrix()
│   ├── models/
│   │   ├── ranker.py               LightGBM LambdaRank + predict + kelly +
│   │   │                           calibrate_temperature/isotonic + compute_nll
│   │   └── backtest.py             quarterly_walk_forward + rolling_walk_forward
│   │                               (14d) + edge_decay_analysis (Brier/ROI)
│   ├── monitoring/
│   │   └── feature_drift.py        Viikoittainen piirre-jakauma-monitorointi
│   │                               (K1-tyyppisten bugien havaitseminen)
│   └── betting/
│       ├── bankroll.py             Kelly-panostus + stop-loss
│       └── clv_tracker.py          CLV + devig-laskuri
├── scripts/                        Ad-hoc-skriptit (pilotit, ablation, drift)
├── tests/                          257 pytest-testiä (244 Hetzner)
├── data/                           ⚠ .gitignore — luodaan paikallisesti
│   ├── ravit.db                    SQLite-tietokanta (Hetznerillä ~22 MB)
│   ├── raw/travsport/              Travsport-cache (7 vrk TTL)
│   ├── raw/travronden_tracks/      Travronden round-stats-cache
│   ├── logs/scheduler.log          Scheduler-loki
│   ├── logs/feature_drift_*.csv    Viikoittaiset drift-raportit
│   └── model_baseline_*.lgb        Tallennetut mallit
├── ROADMAP.md                      Vaiheistus ja aikataulu
├── KNOWN_ISSUES.md                 Avoimet bugit ja aktivointimuistutukset
├── TASK_PROGRESS.md                Tämänhetkinen tila + avoimet tehtävät
├── docs/
│   ├── TASK_PLAN_FIXES.md          Vaihe 2B auditointikorjaukset
│   ├── TASK_TRACK_FEATURES.md      Vaihe 2.5 ratarakenne-piirteet
│   ├── TASK_TRAVRONDEN_INVESTIGATION.md  Vaihe D Travronden-pilotti
│   └── archive/                    Auditoinnit, vanha edistymishistoria,
│                                   ACTION_PLAN-versioit
└── requirements.txt
```

---

## Asennus uudelle koneelle

```bash
# 1. Kloonaa
git clone https://github.com/Fidevo/app-ravi.git ravit-edge
cd ravit-edge

# 2. Luo venv (Python 3.12+)
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

pip install -r requirements.txt

# 3. Alusta tietokanta
python -m src.data.schema

# 4. Testaa yhdellä päivällä
python -m src.data.scheduler run-once
```

> **Huom:** `data/`-hakemisto on `.gitignore`d. Tietokanta luodaan
> tyhjästä paikallisesti — scheduler täyttää sen ajamalla `run-once`
> tai `run-forever`.

---

## Schedulerin käyttö

```bash
# Manuaalinen ajo: hae tämän päivän lähdöt + tulokset
python -m src.data.scheduler run-once [--date YYYY-MM-DD]

# Tuotanto: pyörii ikuisesti (käytä tmux/screen/systemd)
python -m src.data.scheduler run-forever

# Yksittäisten operaatioiden manuaalitestit
python -m src.data.scheduler fetch-results --race-id 2026-04-28_8_5
python -m src.data.scheduler capture-snapshot --race-id 2026-04-28_8_5 --label T-15min

# Datan backfill (kertaluontoinen täydennys)
python -m src.data.scheduler backfill-race-class       # race_terms → race_min/max_earnings, race_age_group
python -m src.data.scheduler backfill-dam-sire         # horses.dam_sire ATG pedigree.grandfather
python -m src.data.scheduler backfill-atg-aggregates   # K1-vuotojen korjaus (ajettu kerran 10.5.)
python -m src.data.scheduler fetch-track-structures    # tracks-taulu Travrondenista (kertaluontoinen)
```

**Schedulerin aikataulu (Stockholm-aika):**

| Aika | Toiminto |
|---|---|
| Käynnistys | Hae tämän päivän lähdöt; iltapäivällä (≥18:00) myös huominen |
| 03:00 cron | Hae päivän lähtökortit, ajasta snapshot- ja result-jobit |
| T-15, T-10, T-5, T-2 min | Tallenna pre-race-kerroin (ATG vinnare-pool) |
| T+30 min | Hae lopulliset tulokset + finalOdds |
| 04:30 cron | Retry puuttuvat tulokset edelliseltä päivältä |
| Sunnuntai 02:00 cron | Feature drift -monitorointi (`scripts/run_feature_drift.py`) |
| Per lähtö, per hevonen | Päivitä Travsport-historia (cache 7 vrk) |

**Travrondenspel-pollaus (Vaihe D2, Stockholm-aika):**

| Päivä | Pollausajat | Tausta |
|---|---|---|
| Ma–Pe | 15:00, 17:00 | ATG-lähdöt klo 18:00–19:00 |
| Lauantai | 09:00, 11:00, 13:00 | V75 alkaa usein 14:30 |
| Sunnuntai | 10:00, 12:00 | V75 alkaa ~15:00 |

Pollaus discoveroi päivän V-pelien round_id:t, hakee jokaisen leg-racen ja
tallentaa `tr_*`-piirteet (`start_interval_group` jne.) runner-tauluun.
Smart-skip: jos kierroksen kaikki legit ovat täytetyt cachessa
(30 vrk TTL), uudelleenpyyntöjä ei tehdä. Käytännön rasitus ~50 pyyntöä/päivä
parhaillaan, 1 req/s rate-limit.

---

## Dashboard (visuaalinen näkymä)

Streamlit-dashboard päivän ennusteille:

```bash
streamlit run src/dashboard/app.py
# → http://localhost:8501
```

Näyttää päivän V-pelilähdöt, mallin win-todennäköisyydet ja edge-prosentit.  
Tutkimuskäyttöön — älä käytä rahapelipäätöksiin ennen Vaihe 5:n päätöskriteerit täyttyvät.

---

## Feature engineering -pipeline

Feature-pipeline kutsutaan ennen mallin treenausta tai ennustamista:

```python
from src.features.build_features import build_feature_matrix, fill_finish_positions

# Treenidata: täytä puuttuvat sijoitukset ensin
runners_filled = fill_finish_positions(runners_with_race_date)
feature_matrix = build_feature_matrix(runners_filled, races, horse_starts=horse_starts)
# → train_ranker(feature_matrix)

# Ennustaminen (tulevia lähtöjä, ei finish_position):
feature_matrix = build_feature_matrix(upcoming_runners, races, horse_starts=horse_starts)
# → predict_win_probabilities(model, feature_matrix)
```

**Miksi `horse_starts` on tärkeä:** runners-taulu sisältää vain ~18 päivää dataa
(~1–2 starttia per hevonen). `horse_starts` tuo Travsportista 115 000+ starttia
koko uran ajalta — muotopiirteiden NaN-% putoaa 95 %:sta 11 %:iin.

**Pakolliset parametrit `build_feature_matrix`:lle (täysi feature-set):**

```python
runners = pd.read_sql("""
    SELECT r.*, ra.race_date, h.birth_year
    FROM runners r
    JOIN races ra ON r.race_id = ra.race_id
    LEFT JOIN horses h ON r.horse_id = h.horse_id
""", con)
horses = pd.read_sql("SELECT * FROM horses", con)
tracks = pd.read_sql("SELECT * FROM tracks", con)

features = build_feature_matrix(
    fill_finish_positions(runners), races,
    horse_starts=horse_starts,   # vaaditaan: form, B1-track-history, segmentoidut
    horses=horses,               # vaaditaan: sire/dam_sire, horse_age
    tracks=tracks,               # vaaditaan: track-rakennepiirteet
)
```

Jos jokin parametri puuttuu, vastaavat piirteet ovat 100 % NaN.
`_resolve_cols` kirjaa varoituksen mutta ei kaadu.

---

## Testien ajaminen

```bash
# Kaikki testit (257 lokaalisti, 244 Hetzner)
PYTHONPATH=. python -m pytest

# Tiivis output
PYTHONPATH=. python -m pytest -q

# Vain tietty moduuli
PYTHONPATH=. python -m pytest tests/test_build_features.py -v
```

`tests/test_travsport.py` on tunnettu epäonnistumaan Hetznerillä (ympäristö-
riippuvuus, ei regressio).

---

## Monitorointi tuotannossa

**Feature drift -monitorointi** ajetaan viikoittain (sunnuntai 02:00):

```bash
python scripts/run_feature_drift.py
# → data/logs/feature_drift_YYYY-WW.csv
```

Raportti laskee jokaiselle FEATURE_COLS:n piirteelle mean/std/p25/p50/p75/NaN-%
ja vertaa edellisten viikkojen historiaan. Hälyttää jos:
- NaN-% nousee +10pp tai enemmän
- Mean tai p50 liikkuu yli 2σ historiallisesta (alle 3 vk historaa: raw 20 % raja)

Tämä havaitsee K1-tyyppiset bugit viikossa eikä kuukausissa.

---

## Rajoitukset (tietoinen valinta)

| Rajoitus | Syy |
|---|---|
| Vain Ruotsin radat (SE) | Ulkomaisten hevosten `horse.id` puuttuu ATG-vastauksesta |
| SQLite, ei Postgres | MVP-vaihe, riittää kunnes DB > 500 MB |
| ATG pari-mutuel kertoimina (ei Pinnacle) | Ruotsin ravien likviditeetti Betfairissa todennäköisesti liian ohut |
| Pace-piirre vain V-pelilähdöissä | `start_interval_group` Travrondenista — ~60 % kattavuus, ennustetuotanto rajoittuu siksi V-peleihin |
| Ennustetuotanto vain V-peleille | Strateginen valinta: paras pre-race-data, paras markkinaliikkuvuus. Yksittäisiä ei-V-pelilähtöjä voidaan ennustaa pyynnöstä (CLI-flag) |
| Ei pelata V-pelin multi-leg-tuotteita | Single-win-markkinat Unibet/Betsson/Betfair antavat paremman EV:n V-pelin takeoutin (~22 %) vs. single-winin ~5–8 % takeoutin sijaan |
| Ei automaattista restartia | Käytä systemd tai supervisord tuotannossa |

---

## Lisenssi

Henkilökohtainen tutkimuskäyttö. Älä rakenna liikennettä joka vahingoittaa
ATG:n tai Travsportin palveluita — molemmissa on rate limit 1 req/s ja
TTL-cache.
