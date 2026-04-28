# Ravit Edge — Ruotsin ravien todennäköisyyslaskuri

Value-detector ja voittotodennäköisyyslaskin Ruotsin raveihin julkisista
ATG- ja Travsport-rajapinnoista.

**Pelistrategia:** fixed odds (Unibet / Betsson) + Betfair Exchange.
ATG-totoa ei pelata — takeout 15–25 % on liian iso este pitkän aikavälin
tuotolle.

Projekti on MVP-vaiheessa. Tällä hetkellä keskitytään luotettavaan
datankeräyspohjaan jonka päälle ML-malli rakennetaan. Scheduler kerää
päivittäin ATG-lähdöt, pre-race-kertoimet (4 nominaalista snapshotia
ennen lähtöä), tulokset ja Travsport-hevoshistorian.

## Stack

- **Python 3.14** (venv)
- **SQLite** (MVP, WAL-mode) → Postgres myöhemmin kun historiaa kertyy
- **Datalähteet** (julkiset, ei autentikointia):
  - ATG REST API: `https://www.atg.se/services/racinginfo/v1/api`
  - Travsport webapi: `https://api.travsport.se/webapi`
- **Riippuvuudet:** `httpx`, `tenacity`, `apscheduler`, `sqlalchemy`,
  `pandas`, `numpy`, `lightgbm`, `streamlit`, `pytest` — täydellinen lista
  `requirements.txt`:ssä

## Projektin rakenne

```
ravit-edge/
├── src/
│   ├── paths.py                    Absoluuttiset PROJECT_ROOT-polut
│   ├── data/
│   │   ├── atg_client.py           ATG-asiakas + SE-only whitelist
│   │   ├── schema.py               SQLite-skeema + WAL-migraatio
│   │   ├── scheduler.py            Cron-ajot, snapshotit, tulokset
│   │   └── scrapers/
│   │       └── travsport.py        Hevoshistorian webapi-asiakas
│   ├── features/                   ML-piirteiden rakennus (myöhemmin)
│   ├── models/                     LightGBM-rankki + backtest
│   ├── betting/
│   │   └── clv_tracker.py          CLV + devig-laskuri
│   └── ui/                         Streamlit (myöhemmin)
├── tests/                          pytest-testit (33/33)
├── data/                           ⚠ git-ignorea — luodaan paikallisesti
│   ├── ravit.db                    SQLite-tietokanta
│   ├── raw/travsport/              Travsport-vastausten cache
│   └── logs/scheduler.log          Pyörivä loki
└── requirements.txt
```

## Asennus uudelle koneelle

```bash
# 1. Kloonaa
git clone https://github.com/Fidevo/app-ravi.git
cd app-ravi

# 2. Luo venv ja asenna riippuvuudet (Python 3.14)
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt

# 3. Luo SQLite-tietokanta ja aja migraatiot
python -m src.data.schema

# 4. Hae yksi päivä testidataksi (manuaalitesti)
python -m src.data.scheduler run-once
```

> **Tietokanta ei ole repossa.** `data/`-hakemisto on `.gitignore`d.
> Uudella koneella aloitetaan tyhjästä DB:stä, scheduler täyttää sen
> ajamalla `run-once` tai `run-forever`.

## Schedulerin käyttö

```bash
# Manuaalinen ajo: hae annetun päivän lähdöt + tulokset
python -m src.data.scheduler run-once [--date YYYY-MM-DD]

# Yhden lähdön tulosten haku
python -m src.data.scheduler fetch-results --race-id 2026-04-28_8_5

# Yhden snapshotin manuaalitesti (pre-race kertoimet)
python -m src.data.scheduler capture-snapshot \
    --race-id 2026-04-28_8_5 --label T-15min

# Tuotanto: pyörii ikuisesti, blokkaa terminaalin
python -m src.data.scheduler run-forever
```

`run-forever` lukitsee terminaalin. Tuotannossa käytä **systemd**,
**supervisord**, **tmux** tai **screen** jotta ajo jatkuu SSH-session
sulkemisen jälkeen.

### Mitä scheduler tekee

| Aika (Stockholm-tz) | Toiminto |
|---|---|
| Käynnistys | Hae tämän päivän lähdöt; iltapäivällä (≥18:00) myös huominen |
| 03:00 cron | Hae päivän lähtökortit, ajasta snapshot- ja result-jobit |
| T-15, T-10, T-5, T-2 min | Tallenna pre-race-kerroin (`vinnare`-pool) |
| T+30 min | Hae lopulliset tulokset + finalOdds |
| Per päivä, per uniikki hevonen | Päivitä Travsport-historia (cache 7 vrk) |

## Testien ajaminen

```bash
python -m pytest          # kaikki testit
python -m pytest -q       # tiivis output
python -m pytest -k odds  # vain odds-aiheiset
```

## Tunnetut MVP-rajoitukset

- **Vain Ruotsin radat** (`countryCode == "SE"`) — ulkomaisten ratojen
  `horse.id` puuttuu ATG-vastauksesta, eikä malli ole kalibroitu niihin
- **Snapshot-lähde on ATG pari-mutuel** (vinnare-pool, takeout ~18–22 %).
  Step 4:ssä lisätään Pinnacle / Betfair Exchange sharp-kertoimina
- **Driver/trainer-aggregaatit ATG:n statistics-kentästä** (kuluvan vuoden
  W%) — Travsportilla ei ole julkista driver/trainer-endpointia
- **Ei automaattista restartia** kaatumisen jälkeen — käytä systemd/
  supervisord prod-deploymentissa

## Lisenssi

Henkilökohtainen tutkimuskäyttö. Älä rakenna liikennettä joka vahingoittaa
ATG:n tai Travsportin palveluita — molemmissa asiakkaissa on rate limit
1 req/s ja TTL-cache.
