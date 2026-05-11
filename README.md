# Ravit Edge

Ruotsin ravien voittotodennäköisyyslaskin ja value-bet-detektori.
Rakentaa ML-mallin julkisista ATG- ja Travsport-rajapinnoista ilman
autentikointia tai maksullisia datalähteitä.

**Pelistrategia:** fixed odds (Unibet / Betsson) + Betfair Exchange.
ATG-totoa ei pelata — 15–25 % takeout estää pitkän aikavälin tuoton.

---

## Miten tämä toimii — lyhyesti

```
ATG REST API          Travsport WebAPI
      │                      │
      ▼                      ▼
  scheduler.py          travsport.py
  (4×/lähtö)           (hevoshistoria)
      │                      │
      └──────────┬───────────┘
                 ▼
           ravit.db (SQLite)
           ├── races          (356 lähtöä)
           ├── runners        (3 757 starttia, 14 pv)
           ├── horse_starts   (103 747 starttia, koko ura)
           └── odds_snapshots (T-15/10/5/2min + tulos)
                 │
                 ▼
        build_feature_matrix()
        fill_finish_positions()
                 │
                 ▼
        train_ranker()  ←  LightGBM LambdaRank
                 │
                 ▼
        predict_win_probabilities()  →  softmax per lähtö
                 │
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
| Sukutaulu | `sire`, `dam_sire` | ATG pedigree (88 % notna) |
| Johdetut | `barfota_law_active`, `horse_age` | Laskettu |
| Ratarakenne *(tulossa)* | `track_length_home_stretch`, `track_open_stretch`, `track_dosage` | Travronden-scraper |

**Tärkeä yksityiskohta — treeniesimerkit:**
ATG raportoi viralliset sijoitukset vain top 6–8 hevoselle per lähtö.
`fill_finish_positions()` täyttää loput km-ajan perusteella ennen treeniä
(3 685 koulutuskelpoista riviä 3 757:stä).

---

## Tietokanta — taulut

| Taulu | Rivejä | Kuvaus |
|---|---|---|
| `races` | 356 | Lähtöjen perustiedot, luokka, ratakunto |
| `runners` | 3 757 | Starttaavat hevoset, kertoimet, tulokset, kengät |
| `horses` | ~3 500 | Hevosen perustiedot, syntymävuosi, isä, emänisä |
| `horse_starts` | 103 747 | Hevosen koko ura Travsportista (2014→) |
| `odds_snapshots` | 14 758 | Pre-race kertoimet (4 snapshotia/lähtö) |
| `tracks` | *(täytetään)* | Ratarakenne: pituudet, avosuora, kulmasiiveke, dosage |

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
│   │   ├── ranker.py               LightGBM LambdaRank + predict + kelly
│   │   └── backtest.py             Walk-forward evaluointi
│   └── betting/
│       ├── bankroll.py             Kelly-panostus + stop-loss
│       └── clv_tracker.py         CLV + devig-laskuri
├── tests/                          192 pytest-testiä
├── data/                           ⚠ .gitignore — luodaan paikallisesti
│   ├── ravit.db                    SQLite-tietokanta (Hetznerillä ~19 MB)
│   ├── raw/travsport/              Travsport-cache (7 vrk TTL)
│   └── logs/scheduler.log          Pyörivä loki
├── ROADMAP.md                      Vaiheistus ja aikataulu
├── KNOWN_ISSUES.md                 Avoimet bugit ja tunnetut rajoitukset
├── TASK_PROGRESS.md                Koodari ↔ auditoija -seurantadokumentti
├── docs/
│   ├── TASK_PLAN_FIXES.md          Korjaustehtävät (B1–B3)
│   ├── TASK_TRACK_FEATURES.md      Ratarakenne-piirteet (Vaihe 2.5)
│   ├── TASK_TRAVRONDEN_INVESTIGATION.md  Travronden-scraper-tutkimus
│   └── archive/                    Valmiit/vanhentuneet suunnitelmat
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
python -m src.data.scheduler backfill-race-class    # Täyttää race_terms → race_min/max_earnings, race_age_group
python -m src.data.scheduler backfill-dam-sire      # Täyttää horses.dam_sire ATG pedigree-kutsusta (grandfather)
```

**Schedulerin aikataulu (Stockholm-aika):**

| Aika | Toiminto |
|---|---|
| Käynnistys | Hae tämän päivän lähdöt; iltapäivällä (≥18:00) myös huominen |
| 03:00 cron | Hae päivän lähtökortit, ajasta snapshot- ja result-jobit |
| T-15, T-10, T-5, T-2 min | Tallenna pre-race-kerroin (ATG vinnare-pool) |
| T+30 min | Hae lopulliset tulokset + finalOdds |
| 04:30 cron | Retry puuttuvat tulokset edelliseltä päivältä |
| Per lähtö, per hevonen | Päivitä Travsport-historia (cache 7 vrk) |

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

**Miksi `horse_starts` on tärkeä:** runners-taulu sisältää vain ~14 päivää dataa
(~0–2 starttia per hevonen). `horse_starts` tuo Travsportista 103 747 starttia
koko uran ajalta — muotopiirteiden NaN-% putoaa 95 %:sta 11 %:iin.

---

## Testien ajaminen

```bash
# Kaikki testit (192 kpl)
PYTHONPATH=. python -m pytest

# Tiivis output
PYTHONPATH=. python -m pytest -q

# Vain tietty moduuli
PYTHONPATH=. python -m pytest tests/test_build_features.py -v
```

---

## Rajoitukset (tietoinen valinta)

| Rajoitus | Syy |
|---|---|
| Vain Ruotsin radat (SE) | Ulkomaisten hevosten `horse.id` puuttuu ATG-vastauksesta |
| SQLite, ei Postgres | MVP-vaihe, riittää kunnes DB > 500 MB |
| ATG pari-mutuel kertoimina (ei Pinnacle) | Ruotsin ravien likviditeetti Betfairissa todennäköisesti liian ohut |
| Ei pace-piirteitä | ATG `/races/{id}` eikä Travsport `/results` sisällä position-dataa. Vaatisi erillisen tutkimuksen |
| Ei automaattista restartia | Käytä systemd tai supervisord tuotannossa |

---

## Lisenssi

Henkilökohtainen tutkimuskäyttö. Älä rakenna liikennettä joka vahingoittaa
ATG:n tai Travsportin palveluita — molemmissa on rate limit 1 req/s ja
TTL-cache.
