# Travrondenspel.se API — per-runner-analytiikkapilotti

> Auditoija: Claude (Opus 4.7), 10.5.2026 (päivitetty samana päivänä)
> Tilanne: empiirinen tutkimus tehty, API on käyttökelpoinen
>
> **⚠ PRIORITEETTIMUUTOS:** Tämä tiedosto käsittelee per-runner-analytiikkaa
> (rating/speed/comment). Käyttäjän palautteen perusteella **rata-piirteet
> ovat tärkeämmät** — tee ensin **[TASK_TRACK_FEATURES.md](TASK_TRACK_FEATURES.md)**.
>
> Tämä tiedosto jää kakkostasoiseksi pilotiksi joka tehdään Vaiheen 3
> ensimmäisten treenausten jälkeen, kun nähdään mitä mallista vielä puuttuu.
>
> **ToS-tarkennus:** URL-polku sisältää literaalisti `/public/` — endpoint
> on suunniteltu julkiseksi. Aiempi ohjeen ToS-paranoia oli liiallinen.
> Käytetään rehellistä User-Agentia, 1 req/s, ei piiloutumista. Sama linja
> kuin nykyisellä ATG/Travsport-keräyksellä.

---

## TL;DR

Travrondenspel.se:n julkinen API tarjoaa per-hevoselle dataa, jota nykyiset
ATG- ja Travsport-rajapinnat eivät anna:

**Varmasti saatavilla (vahvistettu live-pyynnöllä):**
- `is_first_new_driver`, `is_first_new_trainer` — ohjastajan/valmentajan vaihtosignaali
- `is_first_after_castration` — 1. startti kuohitsemisen jälkeen (tunnettu prediktiivinen signaali)
- `is_first_shoes`, `is_first_carriage` — varustevaihtosignaali "first time"-semantiikalla
- `start_interval_group` — mystinen intervalliryhmä (todennäköisesti pace-luokitus, vahvistettava)
- `game_percent` — reaaliaikainen pelijakauma per V-peli (esim. V64-prosentti)
- `horse.speed_records.{K,M,L}` — kilometriaikaennätykset eri matkaluokille
- `horse.totals` — koko uran tilastot (starts, place1/2/3, percentWin, earnings)
- Mahdollinen yhdistäjä: `horse.atg_id` ↔ meidän `horse_id`

**Mahdollisesti saatavilla — tarkistettava vanhalla kierroksella:**
- `rating` — Travrondenin asiantuntijaluokitus
- `speed` — pace/lähtövauhti-arvio (jos tämä on int 1–5 tai vastaava, tämä on **#1 odotettu piirre**)
- `comment` — vapaateksti-kommentti
- `interviews` — kuski/valmentajahaastattelut

Yllä olevat olivat `None` testaamamme **upcoming**-statuksen kierroksen
kohdalla — todennäköisesti täytetty vasta kun Travrondenin asiantuntijat
ovat julkaisseet analyysinsa (esim. 24 h ennen lähtöä).

**Kattavuus:** Travrondenspel kattaa vain V-pelien kierrokset (V64, V75,
V86, V5, V3, V4). Arviolta 40–60 % päivän SE-lähdöistä. **Ei sovellu
ensisijaiseksi lähteeksi** mutta voi täydentää.

**Riskit:** dokumentoimaton kolmannen osapuolen API → voi muuttua tai
kaatua varoittamatta. ToS-tarkistus tehtävä ennen käyttöä.

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

**Älä integroi tuotantoon ennen vaiheita 1–3.** Resurssit eivät riitä
debuggaamaan kahta integraatiota yhtä aikaa.

### Vaihe 1 — Selvitä mikä on todella saatavilla (1–2 h, ~20 API-kutsua)

**Tehtävä:** Hae 3–5 **vanhaa, päättynyttä** round_id:tä ja vahvista:
1. Onko `rating`, `speed`, `comment`, `interviews` täytetty? Mitä tyyppiä?
2. Mikä on `start_interval_group`:n skaalan tarkoitus?
3. Onko `previous_starts` rakenne haittakelpoinen vai pre-race-vaarallinen?
4. Mikä on `round.status`-arvojen elinkaari (`upcoming → analysed → finished`)?

**Miten löytää vanhoja round_id:tä:**
- Devtoolsista: navigoi travrondenspel.se → mene vanhaan päivään → katso
  XHR-pyynnöt. Round_id näkyy URL:eissa.
- Tai inkrementaalisesti: 171922 on 11.5.2026 V64 Färjestad. Aja peräkkäisiä
  alempia ID-arvoja (171800, 171000) varovaisesti — joka 5. ID toimii
  todennäköisesti.

**Tehtäväkonteksti:**
```bash
# rate limit 1 req/s — 20 kutsua = 20 s
python -c "
import requests, json, time
hdrs = {'User-Agent': 'ravit-edge research (jarkkom.lahde@gmail.com)'}
for rid in [171800, 171500, 171000, 170500, 170000]:
    r = requests.get(f'https://www.travrondenspel.se/api/v1/public/round/{rid}/', headers=hdrs, timeout=10)
    if r.status_code != 200:
        print(f'{rid}: {r.status_code}'); continue
    d = r.json()
    print(f'round={rid} status={d.get(\"status\")} date={d.get(\"round_date\")} legs={len(d.get(\"legs\",[]))}')
    if d.get('status') in ('analysed','finished') and d['legs']:
        rcid = d['legs'][0]['race']
        time.sleep(1)
        rr = requests.get(f'https://www.travrondenspel.se/api/v1/public/race/{rcid}/', headers=hdrs).json()
        if rr.get('starts'):
            s = rr['starts'][0]
            print(f'  race={rcid} start[0] rating={s.get(\"rating\")} speed={s.get(\"speed\")} comment={(s.get(\"comment\") or \"\")[:60]!r} interviews={len(s.get(\"interviews\") or [])}')
    time.sleep(1)
"
```

**Päätös vaiheen 1 jälkeen:**
- Jos `speed` on numeerinen ja `rating` täytetty vanhoilla kierroksilla → **jatka vaiheeseen 2** (rakenna pilotti).
- Jos kaikki ovat None myös valmiilla kierroksilla → **vain "varmasti saatavilla" -kentät** ovat hyötyä (luettelo 1.4). Päätä erikseen onko se yksin tarpeeksi.

### Vaihe 2 — Pilotti: 100 lähdön data + treeniaja vertailu (1 viikko)

**Tehtävä:** Kerää 100 vanhaa lähtöä Travrondenista ja vertaa treenitulokset
piirteen kanssa ja ilman.

**Tekniset askeleet:**
1. Lisää `src/data/scrapers/travronden.py` — yksinkertainen client cache:lla
2. Lisää `data/raw/travronden/round_<id>.json` ja `race_<id>.json` cacheen
3. Aja kerääjä 100 round-kierrokselle (~10 lähtöä/kierros = 1 000 runneria)
4. Lisää `travronden_features()` `build_features.py`:hyn
5. Treenaa kaksi mallia: (a) ilman travronden-piirteitä, (b) niiden kanssa
6. Vertaa NDCG@1, NDCG@3, log-loss

**Päätös vaiheen 2 jälkeen:**
- ΔNDCG@1 > 0.02 → integroi tuotantoon (vaihe 3)
- 0.005 < ΔNDCG@1 < 0.02 → kerää lisää, älä vielä integroi
- ΔNDCG@1 < 0.005 → hylkää, dokumentoi, siirry pace-pilottiin (C3)

### Vaihe 3 — Tuotantointegraatio (vain jos vaihe 2 on positiivinen)

Lisää travronden-haku scheduler-jobiin. Aika: T-1h ennen lähtöä (analyysit
julkaistu) tai T-15min (varmuusmarginaali). Lisää uusi taulu
`runner_travronden_stats`. Vaiheittainen rollout.

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

## 8. Yhteenveto auditoijalta

**Onko tästä hyötyä?** Kyllä, mutta epävarmuus on suuri kunnes vaihe 1
on tehty. Varmasti saatavilla olevat kentät (`is_first_*`, `game_percent`,
`speed_records`) **antavat marginaalista lisäarvoa nykyiseen featuristoon**.
Jos `speed`, `rating`, `comment` osoittautuvat täytetyiksi vanhoilla
kierroksilla, hyöty kasvaa merkittävästi — `speed` voisi olla
**pace-pilotin oikotie**.

**Onko tämä prioriteetti yli Vaiheen 3?** Ei. Vaihe 3 (mallin treenaus)
on edelleen seuraava askel. Travronden-pilotti tehdään **rinnakkain**
Vaihe 3:n ensimmäisten treenausajojen kanssa, jotta voimme verrata heti
"baseline" vs. "baseline + Travronden" -mallit.

**Tämä on tutkimusta, ei käännekohta.** Jos pilotti ei näytä paranemaa,
hylätään ilman katumusta ja siirrytään pace-pilottiin (C3) tai
sukutaulu-iterointiin.

Onnea matkaan — ja tarkista käyttöehdot ennen kuin alat keräämään.
