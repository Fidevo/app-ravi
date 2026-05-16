# Travrondenspel.se API — per-runner-pilotti

> Päivitetty: 15.5.2026. Vaihe 1 (selvitys) tehty 14.5.2026.
> Vaihe 2 (rakentaminen) on **seuraava prioriteetti** — viikon sisällä.
>
> **Tila:** Vaihe 1 paljasti **start_interval_group**-kentän pace-arviona.
> Tämä on lähinnä pace-piirrettä mitä saadaan ilman manuaalista scrapingia
> ja tekee Vaihe 2:sta tärkeän — todennäköisesti **korvaa C3:n** (manuaalinen
> pace-pilotti) kokonaan.
>
> **ToS:** URL-polku sisältää literaalisti `/public/` — endpoint on
> suunniteltu julkiseksi. Rate limit 1 req/s, rehellinen User-Agent
> (`"ravit-edge research (jarkkom.lahde@gmail.com)"`).

---

## Vaihe 1 -tutkimustulokset (14.5.2026) ✅

Tutkittu **18 finished-kierrosta** (round_id 166000–171800), 40 runner-riviä
analysoitu. Kenttien todellinen täyttöaste:

### Pre-race-kentät — käyttökelpoisia ✅

| Kenttä | Täyttö% | Tyyppi | Hyödyllisyys |
|---|---|---|---|
| **`start_interval_group`** | **~60 %** | int (1/11/21/31) | ⭐⭐⭐ **asiantuntijan per-hevonen pace-arvio** |
| `is_first_after_castration` | ~100 % | bool | ⭐⭐ tunnettu prediktiivinen signaali |
| `is_first_new_driver` | ~100 % | bool | ⭐⭐ vaihtosignaali |
| `is_first_new_trainer` | ~100 % | bool | ⭐⭐ vaihtosignaali |
| `is_first_shoes` | ~100 % | bool | ⭐ varustemuutos |
| `is_first_carriage` | ~100 % | bool | ⭐ varustemuutos |
| `game_percent.ATG.V*` | ~100 % V-peleillä | int×100 | ⭐ markkinasentimentti |
| `horse.speed_records.{K,M,L}` | ~70 % | dict | ⭐⭐ rikkaampi kuin atg_best_km_for_this_setup |
| `expected_odds` | 47.5 % | int×100 | ⭐ markkina-arvio toiselta lähteeltä |

### Ei käyttökelpoisia ❌

| Kenttä | Tulos | Syy |
|---|---|---|
| **`speed`** | post-race km-aika | **LEAKAGE-RISKI** — `speed == speed_records.M.speed` samana päivänä |
| `comment` | post-race teksti | Jälkikommentti ("Ledn, släppte e 400...") — vain NLP-tarpeisiin |
| `rating` | 0 % täytetty | Ei saatavilla edes finished-kierroksilla |
| `interviews` | 0 % täytetty | Ei saatavilla |
| `ranking`, `preliminary_equipment` | 0 % täytetty | Ei saatavilla |

### `start_interval_group` -tulkinta vahvistettu (tulkinta c)

Sama hevonen voi saada **eri ryhmän eri kierroksilla** (esim. atg_id=764491:
group=1 kierroksella 171600, group=31 kierroksella 171100). Tämä **ei ole
hevosen kiinteä ominaisuus** — se on Travrondenin asiantuntijoiden
**per-hevonen, per-lähtö pace-arvio** joka huomioi lähtöradan, vastustajat,
distan ja muut tekijät.

Arvot: 1 (nopein) / 11 / 21 / 31 (hitain) — 4-portainen pace-luokitus.

**Tämä on lähinnä pace-piirre mitä saadaan ilman manuaalista raviraportti-
scrapingia.** Korvaa todennäköisesti C3-vaiheen.

### Kattavuus

Travrondenspel kattaa V-pelien kierrokset (V64, V75, V86, V5, V3, V4) —
**40–60 % päivän SE-lähdöistä**. LightGBM hoitaa NaN automaattisesti,
mutta pace-piirre toimii vain noissa lähdöissä.

### Riskit

- Dokumentoimaton kolmannen osapuolen API → voi muuttua varoittamatta
- ~40 % päivän lähdöistä saa NaN — ei kriittistä mutta pace-piirre
  toimii vain osalle
- `speed` ja `comment` ovat post-race — **ehdoton kielto piirteinä**

---

## 1. API:n rakenne — empiiriset löydökset

### 1.1 Endpoint-hierarkia

```
ROUND-taso (V-peli yhden päivän)
  GET /api/v1/public/round/{round_id}/                ← rich (13 KB)
  GET /api/v1/public/round/{round_id}/statistics/     ← rich (23 KB)

RACE-taso (yksi lähtö)
  GET /api/v1/public/race/{race_id}/                  ← VERY RICH (80 KB)

EI OLEMASSA (testattu, 404):
  /round/{id}/games/, /round/{id}/races/, /round/{id}/tips/
  /race/{id}/starts/, /race/{id}/tips/, /race/{id}/analysis/
```

### 1.2 round_id ↔ race_id -mappaus

`/round/{round_id}/.legs` palauttaa listan:
```json
[
  {"id": 1142591, "leg": 1, "race": 385514, "round": 171922, "meet_id": 60465, "race_number": 4},
  ...
]
```

`leg.race` on race_id jota tarvitaan `/race/{race_id}/`-kutsulle.

### 1.3 race ↔ ATG-yhdistäminen

`/race/{race_id}/.starts[i].horse.atg_id` on **ATG:n horse.id**.
Meidän `runners.horse_id`-sarake = ATG:n `horse.id` stringinä.
**Yhdistäminen on triviaali**: `str(start.horse.atg_id) == runners.horse_id`.

Race-tasolla **ei näy ATG race_id:tä suoraan**. Yhdistäminen
race-tasolla pitää tehdä `(race_date, track, race_number)`-avaimella:
- `/round/{id}/` antaa `round_date`, `track_key` (slug), ja `legs[i].race_number`
- Meidän `races`-taulussa on `race_date`, `track`, `race_number`

### 1.4 Per-runner-kentät jotka SAIMME (upcoming-kierros, 11.5.2026 V64 Färjestad)

Vahvistettu pyyntöllä `/race/385514/`:

| Kenttä | Tyyppi | Sample-arvo | Hyödyllisyys |
|---|---|---|---|
| `start_number` | int | 1 | päällekkäin meidän |
| `start_position` | int | 1 | päällekkäin |
| `start_interval_group` | int | 11, 31 | ❓ **tutki: pace?** |
| `is_first_new_driver` | bool | False | ✅ uusi piirre |
| `is_first_new_trainer` | bool | False | ✅ uusi piirre |
| `is_first_after_castration` | bool | False | ✅ **tunnettu signaali** |
| `is_first_shoes` | bool | False | ✅ rikkaampi kuin meidän shoes_changed_* |
| `is_first_carriage` | bool | False | ✅ uusi piirre |
| `game_percent.providers.ATG.V64.percent` | int×100 | 2498 = 24.98 % | ✅ markkinasentimentti |
| `odds.providers.ATG.V` | int×100 | 2103 = 21.03 | duplicate (meillä jo) |
| `horse.atg_id` | int | 785880 | ✅ **yhdistäjä** |
| `horse.totals.percentWin` | int | 27 | duplicate (atg_lifetime_win_rate) |
| `horse.totals.percent123` | int | 60 | duplicate (atg_lifetime_top3_rate) |
| `horse.speed_records.K/M/L` | dict | record_type, speed, date, distance, track | ✅ **paljon rikkaampi kuin atg_best_km_for_this_setup** |

### 1.5 Kentät jotka olivat None (upcoming-statuksessa)

Nämä olivat `None` testaamamme kierroksen kohdalla, mutta saattavat olla
täytettyjä kun "round.status" on `analysed` tai `finished`:

| Kenttä | Spekuloitu sisältö | Tärkeysarvo |
|---|---|---|
| `rating` | asiantuntijan rating (1–10 tai 1–100?) | ⭐⭐⭐ |
| `ranking` | asiantuntijan järjestys | ⭐⭐ |
| `speed` | **pace-arvio** | ⭐⭐⭐⭐ |
| `expected_odds` | Travrondenin ennustama kerroin | ⭐⭐ |
| `comment` | vapaateksti | ⭐ (NLP tarvitaan) |
| `preliminary_equipment` | varuste-ennakko | ⭐ |
| `interviews` (list) | kuski/valmentajahaastattelut | ⭐ |

**Ensimmäinen tehtävä koodarille: tutki vanha (status="finished") kierros
ja varmista mitkä näistä ovat täytetty.** Jos `speed` on numeerinen 1–5
tai vastaava, **se on pace-pilotin oikotie ilman manuaalista scrapingia**.

---

## 2. Riskit ja rajoitukset

### 2.1 ToS — tarkista ENNEN käyttöä

Travrondenspel.se on **yksityinen kaupallinen yritys** (Aller Mediakoncernen).
Heidän API:nsa on "public" -nimellinen, mutta:

- **Tarkista heidän käyttöehdot ennen mitään isompaa keräystä.**
  https://www.travrondenspel.se/villkor tai vastaava — etsi termit
  "scraping", "automated access", "API", "data mining".
- **Älä ota dataa kaupalliseen tarkoitukseen.** Tämä on henkilökohtainen
  tutkimusprojekti — sama linja kuin nykyisellä ATG/Travsport-keräyksellä.
- **Lähetä tunnistautuva User-Agent** joka kertoo projektin ja yhteystiedon:
  ```
  User-Agent: ravit-edge research project (jarkkom.lahde@gmail.com)
  ```
  Tämä on hyvää nettietikettiä — jos he haluavat estää, he tekevät sen
  helposti kasvottoman botin tilalla.
- **Älä piiloutua** UA-spoofingilla. Jos heitä häiritsee liikenne, he ottavat
  yhteyttä — käyttäjäystävällinen tapa.

### 2.2 Tekniset rajoitukset

- **Dokumentoimaton API** — kentät, polut, rakenteet voivat muuttua ilman
  varoitusta. Älä tee mallista riippuvaa tästä lähteestä — käytä sitä
  optional-täydennyksenä, jotta puute ei kaada koko mallia.
- **Kattavuus rajallinen** — vain V-pelien kierrokset. Arvioi etukäteen
  paljonko päivän lähdöistä on Travrondenissa: tämä on
  `LEFT JOIN`-piirre (kaikki ATG-lähdöt vasemmalle, Travronden täydentää
  jos saatavilla). LightGBM käsittelee NaN puuttuvina arvoina ilman ongelmaa.
- **Saatavuusaikataulu** — analyysikentät (`rating`, `speed`, `comment`)
  julkaistaan **n. 12–24 h ennen lähtöä**, eivät heti round-objektin
  luomisen yhteydessä. Pre-race-haku pitää aikatauluttaa oikein.

### 2.3 Data leakage -riskit

**Pre-race-osio:** `rating`, `speed`, `expected_odds`, `game_percent`,
`is_first_*`, `start_interval_group` — kaikki julkaistaan **ennen** lähtöä,
joten niitä saa käyttää piirteinä. **Ei vuotoa.**

**Post-race-osio:** `placement`, `finishing_position`, `dsq`, `result`,
`previous_start.placement` (mahdollisesti) — **älä käytä** treenipiirteinä,
ne ovat outcome-tietoa.

**Vaarallinen edge case:** `previous_start` ja `previous_starts` voivat
sisältää aiempia tuloksia. Jos haetaan **post-race**, voi sisältää
nykyisen lähdön tuloksen. **Haetaan vain pre-race** (n. T-15min tai
T-1h ennen lähtöä) jotta tämä on suljettu pois.

---

## 3. Strategia — vaiheittainen pilotti

### Vaihe 1 — Selvitys ✅ VALMIS (14.5.2026)

Tulokset ylhäällä otsikossa "Vaihe 1 -tutkimustulokset". `speed` osoittautui
post-race-kentäksi (leakage-riski), `rating`/`interviews` olivat 0 % täytettyjä,
mutta `start_interval_group` paljastui asiantuntijan pace-arvioksi.
Implementointiskripti: `scripts/travronden_vaihe1.py`.

### Vaihe 2 — Pilotti + polling-tuotantointegraatio 🟡 SEURAAVA

**Tehtävä:** Kerää 100 vanhaa lähtöä Travrondenista, rakenna polling-pohjainen
tuotantointegraatio ja vertaa treenitulokset piirteen kanssa ja ilman.
Vaihe 1 vahvisti että `start_interval_group` on asiantuntijan pace-arvio —
sen lisääminen on tämän pilotin pääarvo.

**Tekniset askeleet:**

#### 2A — Scraper + cache

1. Rakenna `src/data/scrapers/travronden.py` — client cache:lla
   - **Cache 30 vrk TTL**, rate limit 1 req/s, rehellinen User-Agent
   - Smart-skip: jos kierroksen kaikkien legien tracks-race-objektit on
     täytettyjä (`start_interval_group` ei-None), ohita uudelleenpyyntö
   - Tiedostot: `data/raw/travronden/round_<id>.json`, `race_<id>.json`

2. **Polling-aikataulu** (cron `run_forever`:iin, Stockholm-aika):

   | Päivä | Pollausajat | Tausta |
   |---|---|---|
   | Ma–Pe | 15:00, 17:00 | ATG-lähdöt 18:00–19:00 |
   | Lauantai | 09:00, 11:00, 13:00 | V75 alkaa usein 14:30 |
   | Sunnuntai | 10:00, 12:00 | V75 alkaa ~15:00 |

   APScheduler-konfiguraatio:
   ```python
   scheduler.add_job(
       poll_travronden_today,
       trigger=CronTrigger(
           day_of_week="mon-fri", hour="15,17",
           timezone=ATG_TZ,
       ),
       id="travronden_poll_weekday",
       misfire_grace_time=1800,
   )
   # + sat 9,11,13 + sun 10,12 erillisinä jobeina
   ```

3. **Pollauksen logiikka** (`poll_travronden_today`):
   ```python
   def poll_travronden_today(db_path: str = DB_PATH) -> dict:
       today = datetime.now(ATG_TZ).date()
       round_ids = discover_today_rounds(today)  # cache-haku tai pieni skannaus
       updated = 0
       for rid in round_ids:
           round_obj = client.get_round(rid)  # cache-aware
           if round_obj.get("status") in ("upcoming",):
               continue  # analyysit ei vielä julkaistu
           for leg in round_obj.get("legs", []):
               race_obj = client.get_race(leg["race"])
               # Tallenna tr_*-piirteet runners-tauluun
               updated += upsert_travronden_features(session, race_obj)
       return {"rounds": len(round_ids), "rows_updated": updated}
   ```

   `discover_today_rounds`:
   - Yritä ensin cachen `round_<id>.json`-tiedostoja → suodata `round_date == today`
   - Jos ei mitään → skannaa pieni alue tunnetusta uusimmasta round_id:stä
     (~5–10 pyyntöä per päivä)

#### 2B — Schema + feature-pipeline

4. **Schema-laajennus** (`src/data/schema.py`) — lisää `_COLUMN_MIGRATIONS["runners"]`:
   ```python
   ("tr_start_interval_group", "INTEGER"),
   ("tr_is_first_after_castration", "BOOLEAN"),
   ("tr_is_first_new_driver", "BOOLEAN"),
   ("tr_is_first_new_trainer", "BOOLEAN"),
   ("tr_is_first_shoes", "BOOLEAN"),
   ("tr_is_first_carriage", "BOOLEAN"),
   ("tr_speed_record_k", "REAL"),
   ("tr_speed_record_m", "REAL"),
   ("tr_speed_record_l", "REAL"),
   ("tr_expected_odds", "REAL"),
   ("tr_game_percent_v", "REAL"),
   ("is_v_race", "BOOLEAN"),   # V-pelilähdön tunnistin
   ```

5. **`src/features/travronden_features.py`** — pre-race-kentät runners:iin
   (kuten dokumentin osa 4.2 ehdottaa, mutta `tr_*`-piirteet ovat jo
   `runners`-taulussa → ei JOIN:ia, vain `FEATURE_COLS`-laajennus)

6. **`FEATURE_COLS`-laajennus** (`src/models/ranker.py`):
   ```python
   # Travronden pre-race-piirteet (V-pelilähdöt, ~60 % kattavuus)
   "tr_start_interval_group",
   "tr_is_first_after_castration",
   "tr_is_first_new_driver",
   "tr_is_first_new_trainer",
   "tr_is_first_shoes",
   "tr_is_first_carriage",
   "tr_speed_record_k",
   "tr_speed_record_m",
   "tr_speed_record_l",
   "tr_expected_odds",
   "tr_game_percent_v",
   ```
   LightGBM käsittelee NaN-arvot automaattisesti ei-V-pelilähdöissä.

#### 2C — Pilotti + A/B-vertailu

7. **Aja pilotti** 100 vanhalle round-kierrokselle (`scripts/travronden_pilot.py`):
   - Yksi kerää-ajo, ~3–5 min
   - Tallenna pilot-cache: `data/raw/travronden/`
   - Päivitä runners-taulu tr_*-arvoilla pilot-lähdöille

8. **A/B-vertailu** baseline-mallin (Brier 0.0818, rs=42) pohjalta:
   - A: nykyinen malli (41 piirrettä)
   - B: nykyinen + 11 tr_*-piirrettä (~52 piirrettä)
   - Vertaa **Brier-scorea** (ei NDCG — alkuvaiheen pieni n)
   - Suoritettavasti vain V-pelilähdöistä, mutta treeniaineisto kaikki

#### Päätös vaiheen 2 jälkeen

- **ΔBrier ≤ -0.005** (paranema) → pollaus jää tuotantoon, integroi
- **-0.005 < ΔBrier < 0** → kerää 2–4 vk lisää, vertaa uudelleen
- **ΔBrier ≥ 0** → hylkää, dokumentoi syyt (start_interval_group ei toiminut
  — liian harva tai asiantuntijat eivät osu oikein)

### Vaihe 3 — Lopputuotantointegraatio (kun Vaihe 2 onnistunut)

Vaihe 2 sisältää jo polling-cron:n `run_forever`:iin. Vaihe 3 vain:

- Vahvista että pollaus on tuotannossa Hetznerillä (logit, drift-monitorointi)
- Lisää CLI: `python -m src.data.scheduler poll-travronden [--date YYYY-MM-DD]`
  manuaalitestiä varten
- Dokumentoi ROADMAP:iin Vaihe D2 ✅

---

## 4. Tekninen suunnitelma — Vaiheen 2 toteutus

### 4.1 Uusi tiedosto: `src/data/scrapers/travronden.py`

```python
"""Travrondenspel.se API-asiakas.

Julkinen mutta dokumentoimaton API. Tarjoaa:
- Per-runner expert-arvioita (rating, speed, kommentti)
- Real-time pelijakaumat (game_percent)
- Equipment + driver/trainer change -signaalit
- Hevosen speed_records eri matkaluokille

ToS-huomautus: Travronden on yksityinen kaupallinen toimija. Käytetään
vain henkilökohtaiseen tutkimukseen. Aja tämä hidasta tahtia (1 req/s).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.paths import RAW_DIR

logger = logging.getLogger(__name__)

_BASE = "https://www.travrondenspel.se/api/v1/public"
_UA = "ravit-edge research (jarkkom.lahde@gmail.com)"
_DEFAULT_TTL_DAYS = 30  # data ei muutu jälkikäteen
_RATE_LIMIT_SEC = 1.0


class TravrondenAPIClient:
    def __init__(
        self,
        cache_dir: Path | None = None,
        cache_ttl_days: int = _DEFAULT_TTL_DAYS,
        rate_limit_seconds: float = _RATE_LIMIT_SEC,
    ):
        self.cache_dir = cache_dir or (RAW_DIR / "travronden")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_seconds = cache_ttl_days * 86400
        self.rate_limit_seconds = rate_limit_seconds
        self._client = httpx.Client(headers={"User-Agent": _UA}, timeout=30.0)
        self._last_request = 0.0

    def get_round(self, round_id: int, force_refresh: bool = False) -> dict:
        """Palauta /round/{id}/ -vastaus.

        Sisältää legs (race_id:t per leg), round_date, track_key, status.
        Cache 30 vrk koska data ei muutu kun status on 'finished'.
        """
        return self._cached_get(f"round_{round_id}", f"/round/{round_id}/", force_refresh)

    def get_race(self, race_id: int, force_refresh: bool = False) -> dict:
        """Palauta /race/{race_id}/ -vastaus.

        Sisältää starts[] (per-runner data: rating, speed, is_first_*, jne.).
        """
        return self._cached_get(f"race_{race_id}", f"/race/{race_id}/", force_refresh)

    def close(self) -> None:
        self._client.close()

    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    # --- internal ---
    def _cached_get(self, cache_key: str, path: str, force_refresh: bool) -> dict:
        cache_path = self.cache_dir / f"{cache_key}.json"
        if not force_refresh and self._cache_is_fresh(cache_path):
            return json.loads(cache_path.read_text(encoding="utf-8"))
        data = self._fetch(f"{_BASE}{path}")
        cache_path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def _cache_is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        age = time.time() - path.stat().st_mtime
        return age < self.cache_ttl_seconds

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self._last_request = time.time()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def _fetch(self, url: str) -> dict:
        self._rate_limit()
        logger.info("GET %s", url)
        r = self._client.get(url)
        r.raise_for_status()
        return r.json()
```

### 4.2 Uusi tiedosto: `src/features/travronden_features.py`

```python
"""Travrondenspel-piirteiden ekstrahointi.

Vain pre-race-kelpoiset kentät. Yhdistys runners-DataFrameen
horse_id (= ATG horse.id) -avaimella.

EI sisällä: placement, dsq, result (post-race-tietoa, ei piirteenä).
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def parse_travronden_starts(race_response: dict) -> pd.DataFrame:
    """Pura per-runner-piirteet yhden lähdön JSON-vastauksesta."""
    rows = []
    for s in race_response.get("starts", []) or []:
        horse = s.get("horse") or {}
        atg_id = horse.get("atg_id")
        if atg_id is None:
            continue
        game_pct = (
            (s.get("game_percent") or {})
            .get("providers", {})
            .get("ATG", {})
        )
        # game_percent on int×100 per V-peli (V64, V75, ...). Otetaan ensimmäinen
        # numeerinen arvo joka löytyy.
        gp_val = None
        for k, v in game_pct.items():
            if isinstance(v, dict) and "percent" in v:
                gp_val = v["percent"] / 100.0
                break

        rows.append({
            "horse_id": str(atg_id),  # yhdistäjä runners.horse_id:hen
            "tr_rating": s.get("rating"),
            "tr_ranking": s.get("ranking"),
            "tr_speed": s.get("speed"),               # mahdollinen pace
            "tr_expected_odds": s.get("expected_odds"),
            "tr_start_interval_group": s.get("start_interval_group"),
            "tr_is_first_new_driver": _bool(s.get("is_first_new_driver")),
            "tr_is_first_new_trainer": _bool(s.get("is_first_new_trainer")),
            "tr_is_first_after_castration": _bool(s.get("is_first_after_castration")),
            "tr_is_first_shoes": _bool(s.get("is_first_shoes")),
            "tr_is_first_carriage": _bool(s.get("is_first_carriage")),
            "tr_game_percent_v": gp_val,
            # Speed records: meidän atg_best_km_for_this_setup on heuristiikka,
            # speed_records antaa empiiriset record-ajat per matkaluokka
            "tr_speed_record_k": _record_speed(horse.get("speed_records"), "K"),
            "tr_speed_record_m": _record_speed(horse.get("speed_records"), "M"),
            "tr_speed_record_l": _record_speed(horse.get("speed_records"), "L"),
        })
    return pd.DataFrame(rows)


def _bool(v: Any) -> int | None:
    if v is None: return None
    return int(bool(v))


def _record_speed(recs: dict | None, code: str) -> float | None:
    if not recs: return None
    r = recs.get(code)
    if not isinstance(r, dict): return None
    sp = r.get("speed")
    if sp is None: return None
    # speed on int×100 (esim. 7520 = 1.15.20)
    return float(sp) / 100.0


def travronden_features(
    runners: pd.DataFrame, travronden_data: pd.DataFrame
) -> pd.DataFrame:
    """Liitä parse_travronden_starts():n tulos runners:iin.

    LEFT JOIN: jos hevosta ei ole Travronden-datassa (ei V-peli-kierrosta),
    kaikki tr_*-kentät NaN. LightGBM hoitaa NaN:t automaattisesti.
    """
    if travronden_data is None or len(travronden_data) == 0:
        return runners
    return runners.merge(
        travronden_data.drop_duplicates(subset=["horse_id"], keep="last"),
        on="horse_id", how="left",
    )
```

### 4.3 Pilotti-skripti: `scripts/travronden_pilot.py` (uusi tiedosto)

```python
"""Kerää 100 vanhaa lähtöä Travrondenista pilottia varten.

Aja kerran. Tallentaa caching kautta (~30 vrk). Tuottaa CSV:n josta
voi treenata vertailumallit.

Käyttö:
    python -m scripts.travronden_pilot --round-ids 171800,171500,...
"""
import argparse, time, logging
import pandas as pd
from src.data.scrapers.travronden import TravrondenAPIClient
from src.features.travronden_features import parse_travronden_starts

logging.basicConfig(level=logging.INFO)

def main(round_ids: list[int]):
    rows = []
    with TravrondenAPIClient() as tr:
        for rid in round_ids:
            try:
                rd = tr.get_round(rid)
                for leg in rd.get("legs", []):
                    race_id = leg.get("race")
                    if not race_id:
                        continue
                    race = tr.get_race(race_id)
                    df = parse_travronden_starts(race)
                    df["round_id"] = rid
                    df["race_id_travronden"] = race_id
                    df["race_date"] = rd.get("round_date")
                    df["race_number"] = leg.get("race_number")
                    df["track_key"] = rd.get("track_key")
                    rows.append(df)
            except Exception as e:
                logging.warning("round %s failed: %s", rid, e)
    if rows:
        out = pd.concat(rows, ignore_index=True)
        out.to_csv("data/travronden_pilot.csv", index=False)
        print(f"Saved {len(out)} rows to data/travronden_pilot.csv")
        # Kenttätilastot
        for col in ["tr_rating","tr_speed","tr_is_first_after_castration",
                    "tr_game_percent_v","tr_speed_record_m"]:
            if col in out.columns:
                pct = round(out[col].notna().mean()*100, 1)
                print(f"  {col} notna%: {pct}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--round-ids", required=True,
                    help="comma-separated round_id list")
    args = ap.parse_args()
    main([int(x) for x in args.round_ids.split(",")])
```

### 4.4 Treeniajo-vertailu (notebook tai skripti)

```python
import pandas as pd, sqlite3
from src.features.build_features import build_feature_matrix, fill_finish_positions
from src.features.travronden_features import travronden_features
from src.models.ranker import train_ranker, predict_win_probabilities, evaluate_calibration

con = sqlite3.connect("data/ravit.db")
runners = pd.read_sql("SELECT r.*, ra.race_date FROM runners r JOIN races ra ON r.race_id=ra.race_id", con)
races = pd.read_sql("SELECT * FROM races", con)
horse_starts = pd.read_sql("SELECT * FROM horse_starts WHERE withdrawn != 1", con)
horses = pd.read_sql("SELECT * FROM horses", con)

tr_data = pd.read_csv("data/travronden_pilot.csv")  # Travronden-piirteet

# Yhdistä tr_*-piirteet runners:iin AINOASTAAN niille runnereille joiden
# horse_id + race_date löytyy. Suodata vain pilottilähdöt jotta vertailu on
# reilu (sama lähtöjoukko molemmissa malleissa).
tr_keys = set(zip(tr_data["horse_id"].astype(str), tr_data["race_date"]))
runners["_in_pilot"] = runners.apply(
    lambda r: (str(r["horse_id"]), str(r["race_date"])) in tr_keys, axis=1
)
pilot_runners = runners[runners["_in_pilot"]].drop(columns=["_in_pilot"]).copy()

# A: malli ilman tr-piirteitä
feat_a = build_feature_matrix(fill_finish_positions(pilot_runners), races,
                              horse_starts=horse_starts, horses=horses)
# B: malli tr-piirteiden kanssa
pilot_with_tr = travronden_features(pilot_runners, tr_data)
feat_b = build_feature_matrix(fill_finish_positions(pilot_with_tr), races,
                              horse_starts=horse_starts, horses=horses)

# Walk-forward split (kuten ROADMAP)
split_date = "2026-05-04"  # tai mikä tahansa sopiva
train_a = feat_a[feat_a["race_date"] < split_date]
test_a  = feat_a[feat_a["race_date"] >= split_date]
train_b = feat_b[feat_b["race_date"] < split_date]
test_b  = feat_b[feat_b["race_date"] >= split_date]

# Lisää tr_*-piirteet manuaalisesti b:n feature_cols-listaan
from src.models.ranker import FEATURE_COLS
tr_cols = [c for c in feat_b.columns if c.startswith("tr_")]

model_a = train_ranker(train_a)
model_b = train_ranker(train_b, feature_cols=FEATURE_COLS + tr_cols)

pred_a = predict_win_probabilities(model_a, test_a)
pred_b = predict_win_probabilities(model_b, test_b, feature_cols=FEATURE_COLS + tr_cols)

# Vertaile NDCG@1, log-loss, kalibrointia
```

---

## 5. Mitä NIMENOMAAN EI saa tehdä

1. **Älä yritä pommittaa massalla.** Yksi pyyntö sekunnissa, ei oikoreittejä,
   ei rinnakkaiskutsuja. Cache kovasti — sama round_id ei tarvitse uutta
   pyyntöä 30 vrk:n sisään.
2. **Älä integroi tuotantoon ennen pilottia.** Tämä on optional-täydennys,
   ei kriittinen lähde. Jos siitä tulee blokkeri (API kaatuu, dataa puuttuu),
   jaopssa olevat lähdöt ovat ongelmissa.
3. **Älä spoofaa selaimena.** Käytä rehellistä UA:ta joka kertoo
   yhteystiedon. Jos Travronden haluaa estää, he tekevät sen helposti
   muuten — pelataan rehellisesti.
4. **Älä tallenna placement/result-kenttiä piirteiksi.** Post-race-data on
   outcome-tietoa, vuotoriski.
5. **Älä unohda ToS-tarkistusta.** Jos termit kieltävät automatisoidun
   pääsyn, älä käytä — etsi vaihtoehto (manuaalinen pace-pilotti C3).
6. **Älä laita tr_*-piirteitä pakollisiksi FEATURE_COLS:iin.** Käytä
   `_resolve_cols`-mekanismia kuten muutkin valinnaiset (esim. horse_age):
   puuttuu → ohitetaan ilman crashia.

---

## 6. Aikataulu — älä kiirehdi

```
Vaihe 1  (1–2 h):    Selvitä mitä on saatavilla vanhoilla kierroksilla
                     → Raportoi TASK_PROGRESS.md:hen "Travronden Vaihe 1"

(odottaa auditoijan vahvistusta)

Vaihe 2  (3–5 päivää): Rakenna client + parser + 100-lähdön pilotti
                     → Vertaa NDCG@1
                     → Raportoi TASK_PROGRESS.md:hen "Travronden Vaihe 2"

(odottaa auditoijan vahvistusta)

Vaihe 3  (1–2 vk, vain jos vaihe 2 onnistunut):
                     Tuotantointegraatio (uusi taulu, scheduler-jobi,
                     feature-pipeline)
```

**Tärkeää:** Tee tämä **paralleelisti** Vaihe 3:n (mallin treenaus
ATG/Travsport-dataan) kanssa, ei sen sijaan. Vaihe 3 voi alkaa heti
nykyisillä piirteillä — Travronden on incremental upgrade kun pilotti
on tehty.

---

## 7. Raportointi

Lisää `TASK_PROGRESS.md`:hen uusi osio nimellä:

```markdown
# VAIHE D — Travrondenspel-pilotti

## D1 · Vaiheen 1 selvitys

**Status:** _(täytä)_

**Vahvistetut kentät vanhoilla kierroksilla** (taulukko: kenttä, % notna,
tyyppi, sample-arvot):
...

**Päätös:** _(etene vaiheeseen 2 / hylkää)_

## D2 · 100-lähdön pilotti

...

## D3 · Tuotantointegraatio

...
```

---

## 8. Yhteenveto auditoijalta (päivitetty 15.5.2026)

**Onko tästä hyötyä? Kyllä, suuremmalla todennäköisyydellä kuin alunperin
arvioitiin.** Vaihe 1 -tutkimus paljasti että `start_interval_group` on
Travrondenin asiantuntijoiden per-hevonen, per-lähtö pace-arvio — **lähinnä
pace-piirre mitä saadaan ilman manuaalista raviraportti-scrapingia**.

Tämä on **C3-vaiheen oikotie** — manuaalista pace-pilottia ei luultavasti
tarvita.

**Realistiset odotukset Brier-paranemasta:** 0.005–0.020 (alalla tyypillinen
pace-piirteen vaikutus). Pohjana 0.0818 → mahdollinen tulos ~0.075–0.080.
Voittosignaali kasvaisi 0.0025 → 0.005–0.010 — 2–4× parannus mutta yhä
ei riittävä tuotantopelaamiseen 17 vrk:n datalla.

**Tärkeä rajoitus:** kattavuus ~60 % (vain V-pelilähdöt). 40 % lähdöistä saa
NaN. LightGBM hoitaa NaN automaattisesti, mutta pace-piirre toimii vain
osalle.

**Onko tämä nyt prioriteetti yli muiden vaiheiden?** Kyllä, viikon sisällä:
- ✅ Vaihe C1 (drift-monitorointi) on tehty
- ✅ Vaihe 3 baseline on treenattu — A/B-vertailu mahdollinen heti
- 🎯 Tämä Vaihe 2 on **seuraava prioriteetti** ennen Vaihe 4 (paperitestaus)

**Tämä ei sitouta mihinkään pysyvään.** Jos Vaihe 2 -pilotti ei näytä Brier-
paranemaa, hylätään ja suunnitellaan uudelleen. Cache (30 vrk TTL) säilyy
joka tapauksessa hyödyllisenä myöhempään tutkimukseen.

Onnea — käyttöehdot ja UA ovat jo tiedostossa.
