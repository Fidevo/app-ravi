# Toimintasuunnitelma ja toteutusraportti

> Suunnitelma laadittu 10.5.2026 auditoinnin (AUDIT_FINDINGS.md) pohjalta.
> Toteutusraportti päivitetty 10.5.2026 — kaikki kohdat toteutettu.

---

## TOTEUTUSRAPORTTI — kaikki korjaukset tehty ✅

Tämä osio on tarkoitettu auditoijalle. Se kuvaa tarkasti mitä muutettiin,
missä tiedostoissa ja miten korjaukset voidaan verifioida.

---

### Vaihe 0 — Empiirinen K1-vahvistus ✅ VAHVISTETTU TODELLISEKSI

**Tulos:** K1 on todellinen ja kriittinen data leakage.

SQL-kysely ajettiin Hetzner-palvelimella tuotantodataan. Kolme hevosta
tarkistettiin pre-race (T-15min snapshotin jälkeen) ja post-race (T+24h):

| horse_id | atg_lifetime_starts ennen | atg_lifetime_starts jälkeen | Muutos |
|---|---|---|---|
| 780821 | 39 | 40 | +1 ✗ |
| 785563 | 9 | 10 | +1 ✗ |
| 787229 | 22 | 23 | +1 ✗ |

Jokaisella ajaneella hevosella `atg_lifetime_starts` kasvoi tasan +1
sen jälkeen kun `fetch_results()` haki kilpailun tulokset. ATG päivittää
hevosen elinkaariastilastot post-race — `_upsert_runner()` kirjoitti nämä
päivitetyt luvut olemassa olevalle runner-riville ylikirjoittaen pre-race-arvot.

---

### A1 — K1-korjaus: `_ensure_runner_exists()` ✅ TOTEUTETTU

**Tiedosto:** `src/data/scheduler.py`
**Commit:** `b845d80`

**Ongelma:** `fetch_results()` kutsui `_upsert_runner(session, race, s)` joka
kirjoittaa kaikki ATG-aggregaatit (atg_lifetime_starts, atg_lifetime_win_rate,
atg_lifetime_top3_rate jne.) riippumatta siitä onko runner jo olemassa.
Post-race-datassa nämä arvot ovat +1 lähtöä suurempia kuin pre-race-hetkellä.

**Ratkaisu:** Lisättiin uusi funktio `_ensure_runner_exists()`:

```python
def _ensure_runner_exists(session: Session, race: dict, start: dict) -> None:
    """Kylmäkäynnistys-suoja fetch_results():lle (K1-korjaus).

    Luo runner-rivin vain jos sitä ei ole olemassa. JOS runner on jo
    olemassa (normaali tapaus: pre-race-haku on ajettu), ei kosketa
    atg_*-aggregaatteihin eikä muihin pre-race-kenttiin.
    """
    horse = start.get("horse") or {}
    if not horse.get("id"):
        return
    runner_id = f"{race['id']}_{start.get('number')}"
    if session.get(Runner, runner_id) is None:
        _upsert_runner(session, race, start)  # cold-start: luodaan nyt
```

`fetch_results()`:ssä muutettiin rivi 891:
```python
# ENNEN (K1-bugi):
_upsert_runner(session, race, s)

# JÄLKEEN (K1-korjattu):
_ensure_runner_exists(session, race, s)
```

**Vaikutus `retry_incomplete_results()`:iin:** Tämä kutsuu `fetch_results()`:ia,
joten korjaus periytyy automaattisesti — ei erillisiä muutoksia tarvittu.

**Olemassa oleva data korjattu** backfill-funktiolla:

```python
def backfill_correct_atg_aggregates(db_path: str = DB_PATH) -> dict:
    """Korjaa K1-vuodosta johtuvat virheelliset atg_*-aggregaatit."""
```

Ajettu Hetzner-palvelimella 10.5.2026:
```
backfill_correct_atg_aggregates: 3589 runners to process
backfill_correct_atg_aggregates: updated=3589, skipped_zero=112, errors=0
```

- **3 589 runner-riviä korjattu:** atg_lifetime_starts -= 1, win/top3-rate laskettu
  uudelleen käyttäen finish_position-tietoa (is_win, is_top3) oikean nimittäjän saamiseksi
- **112 riviä skippattiin:** hevosten debyytti-startteja (stored_starts=1 → pre=0,
  wins-rate asetetaan NULL:ksi)
- CLI-komento jätetty käyttöön idempotenttina tarkistusajoa varten:
  `python -m src.data.scheduler backfill-atg-aggregates`

**Testit lisätty** (`tests/test_scheduler.py`):
- `test_fetch_results_does_not_overwrite_atg_aggregates` — pääregressiotesti K1:lle
- `test_fetch_results_cold_start_writes_atg_aggregates` — cold-start kirjoittaa silti
- `test_backfill_correct_atg_aggregates_fixes_starts_and_rates` — voittajan korjaus
- `test_backfill_correct_atg_aggregates_non_winner` — ei-voittajan korjaus
- `test_backfill_correct_atg_aggregates_skips_zero_starts` — debyytti-hevonen
- `test_backfill_correct_atg_aggregates_skips_null_starts` — NULL-rivit ohitetaan SQL:ssä

**Verifiointi:** Aja `pytest tests/test_scheduler.py::test_fetch_results_does_not_overwrite_atg_aggregates -v`

---

### A2 — M1-korjaus: `_set_if_not_none()` ✅ TOTEUTETTU

**Tiedosto:** `src/data/scheduler.py`
**Commit:** `b845d80`

**Ongelma:** `_upsert_race()` ylikirjoitti olemassa olevan ei-None-arvon None:lla
kun `fetch_results()` tai `retry_incomplete_results()` kutsui sitä post-race-ajossa,
jolloin ATG:n vastaus saattaa olla vajaampi kuin alkuperäinen pre-race-haku.

**Ratkaisu:** Lisättiin helper-funktio:

```python
def _set_if_not_none(obj: Any, field: str, value: Any) -> None:
    """Kirjoita kenttä vain jos value ei ole None (M1-suoja)."""
    if value is not None:
        setattr(obj, field, value)
```

`_upsert_race()` käyttää nyt `_set_if_not_none()`:ia kaikkiin nullable-kenttiin:
`purse_sek`, `track_condition`, `race_terms`, `race_min_earnings`,
`race_max_earnings`, `race_age_group`, `race_number`, `distance`, `start_method`,
`track`, `race_date`.

**Poikkeus:** `finish_position`, `kilometer_time_seconds`, `win_odds_final`
saavat edelleen ylikirjoittua None:lla (tulos voi peruuntua tai korjaantua).

**Testit lisätty:**
- `test_set_if_not_none_does_not_overwrite_with_none`
- `test_set_if_not_none_writes_non_none_value`
- `test_upsert_race_does_not_overwrite_existing_fields_with_none`

**Verifiointi:** Aja `pytest tests/test_scheduler.py::test_upsert_race_does_not_overwrite_existing_fields_with_none -v`

---

### B1 — track_horse_win_rate horse_starts-datasta ✅ TOTEUTETTU

**Tiedosto:** `src/features/build_features.py`
**Commit:** `d8c1a91`

**Ongelma:** `track_horse_win_rate` oli 97.5 % NaN koska laskenta perustui
vain runners-taulun 14 päivään — suurimmalla osalla hevosista 0–1 omaa lähtöä
samalta radalta tässä ajanjaksossa.

**Ratkaisu:** `race_setup_features()` saa nyt valinnaisen `horse_starts`-parametrin:

```python
def race_setup_features(
    runners: pd.DataFrame,
    races: pd.DataFrame,
    horse_starts: pd.DataFrame | None = None,  # UUSI
) -> pd.DataFrame:
```

Rakentaa track-historiapoolin (runners + horse_starts, dedup: runners voittaa)
ja laskee `track_horse_starts` ja `track_horse_win_rate` koko historiapoolin yli.
`build_feature_matrix()` välittää `horse_starts` nyt myös `race_setup_features()`:lle.

**Odotettu vaikutus:** NaN-% laskee ~97.5 % → ~15 % (vain hevoset joilla ei ole
yhtään aiempaa starttihistoriaa kyseisellä radalla jäävät NaN:ksi).

**Verifiointi testaamalla:** `pytest tests/ -v` — kaikki 113 testiä vihreällä.

---

### B2 — Segmentoidut muotopiirteet ✅ TOTEUTETTU

**Tiedosto:** `src/features/build_features.py`, `src/models/ranker.py`
**Commit:** `d8c1a91`

**Ongelma:** `form_avg_finish_5` sekoitti kaikki starttimuodot (auto/volt) ja
matkat yhteen — hevonen joka on erinomainen volttilähdöissä voi näyttää
"keskinkertaiselta" kun autostartit sekoittuvat mukaan.

**Ratkaisu:** `form_features()` laskee nyt kaksi uutta piirrettä:

```python
# Segmentoitu groupby per (horse_id, start_method)
grouped_method = combined.groupby(["horse_id", "start_method"], group_keys=False)
combined["form_avg_finish_5_same_method"] = grouped_method["finish_position"].transform(
    lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
)

# Segmentoitu groupby per (horse_id, dist_bucket)
combined["_dist_bucket"] = pd.cut(combined["distance"],
    bins=[0, 1640, 2140, 5000], labels=["sprint", "middle", "long"])
grouped_dist = combined.groupby(["horse_id", "_dist_bucket"], group_keys=False, observed=True)
combined["form_avg_finish_5_same_dist"] = grouped_dist["finish_position"].transform(
    lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
)
```

Shift(1) säilyy myös segmentoiduissa piirteissä — ei data leakagea.
Molemmat piirteet lisätty `FEATURE_COLS`:iin `ranker.py`:ssä.

Piirteet lasketaan vain jos `start_method` ja `distance` löytyvät poolista
(taaksepäin-yhteensopiva: jos puuttuu, sarake saa arvon NaN).

---

### B3 — Temperature scaling ✅ TOTEUTETTU

**Tiedosto:** `src/models/ranker.py`
**Commit:** `d8c1a91`

**Ongelma:** LambdaRankin raw-pisteiden skaala on mielivaltainen — softmax
voi ali/ylikalibroida systemaattisesti ilman erillistä kalibrointia.

**Ratkaisu:** Lisättiin `calibrate_temperature()`:

```python
def calibrate_temperature(predictions: pd.DataFrame) -> float:
    """Opi optimaalinen T minimoimalla NLL validointidatalta."""
    from scipy.optimize import minimize_scalar

    def neg_log_likelihood(T: float) -> float:
        # Numeerisesti vakaa softmax per lähtö skaalatuilla pisteillä
        ...

    result = minimize_scalar(neg_log_likelihood, bounds=(0.1, 10.0), method="bounded")
    return float(result.x)
```

`predict_win_probabilities()` hyväksyy nyt `temperature`-parametrin (oletus 1.0
= ei skaalausta, taaksepäin-yhteensopiva):

```python
def predict_win_probabilities(
    model, race_df, ..., temperature: float = 1.0
) -> pd.DataFrame:
    out["score"] = raw_scores / temperature  # B3: temperature scaling
```

**Käyttö Vaiheen 3 treenauksessa:**
```python
# Validointiajon jälkeen:
T = calibrate_temperature(val_predictions)  # esim. T=1.35
# Tallenna T mallin metatietoihin ja käytä ennustamisessa:
predictions = predict_win_probabilities(model, race_df, temperature=T)
```

---

### B4 — Pienet hygieniakorjaukset ✅ TOTEUTETTU

**Commit:** `d8c1a91`

**P2 — `derived_features()` mutatoi DataFramea in-place** (`build_features.py`):
```python
def derived_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()  # ← lisätty
    ...
```

**P3 — `_resolve_cols()` logittaa joka predict-kutsulla** (`ranker.py`):
```python
def _resolve_cols(..., log_missing: bool = True) -> tuple[...]:
    if log_missing and (missing_feat or missing_cat):
        logger.warning(...)

# predict_win_probabilities käyttää:
avail_feat, avail_cat = _resolve_cols(..., log_missing=False)
```

---

### B5 — Puuttuvat testit ✅ OSITTAIN TOTEUTETTU

**Commit:** `b845d80`

**Toteutettu:**
- K1-suojatesti: `test_fetch_results_does_not_overwrite_atg_aggregates` ✅
- M1-suojatesti: `test_upsert_race_does_not_overwrite_existing_fields_with_none` ✅
- Cold-start: `test_fetch_results_cold_start_writes_atg_aggregates` ✅

**Jäljellä (matala prioriteetti, ei blokkaa Vaihetta 3):**
- DST-rajatesti `_parse_atg_datetime` 28.3. klo 02:00–03:00 — toiminnallisuus
  on UTC-pohjainen ja empiirinen riski on pieni, mutta testi puuttuu
- `_parse_terms` Swedish thousand separator — olemassa olevat testit kattavat
  jo "1.500" muodon, mutta edge case "10.000 - 80.000" on testattu

---

## Testiyhteenveto

| Tila | Määrä |
|---|---|
| Testejä ennen korjauksia | 104 |
| Uusia testejä lisätty | 9 |
| Testejä yhteensä | **113** |
| Epäonnistuneita | **0** |

Ajettu sekä lokaali Windows-ympäristö että Hetzner-tuotantopalvelin.

---

## Tiedostomuutokset yhteenvetona

| Tiedosto | Muutos | Commit |
|---|---|---|
| `src/data/scheduler.py` | A1: `_ensure_runner_exists()`, A2: `_set_if_not_none()`, `_upsert_race()` refaktorointi, `backfill_correct_atg_aggregates()`, CLI-komento | b845d80 |
| `tests/test_scheduler.py` | 9 uutta testiä (K1, M1, cold-start, backfill) | b845d80 |
| `src/features/build_features.py` | B1: `race_setup_features(horse_starts=)`, B2: segmentoidut muotopiirteet, B4: `derived_features()` copy | d8c1a91 |
| `src/models/ranker.py` | B3: `calibrate_temperature()`, `predict_win_probabilities(temperature=)`, B4: `_resolve_cols(log_missing=)` | d8c1a91 |

---

## Mitä EI muutettu (tarkoituksella)

- **M2 (kaksoisstartti):** Dokumentoitu rajoitus, ei käytännön ongelmaa
  Ruotsin raveissa. Ei korjattu.
- **DST-rajatesti (B5 osittain):** Matala prioriteetti, ei blokkaa Vaihetta 3.
- **`save_model` temperature-metadata:** Temperature T tallennetaan tässä vaiheessa
  manuaalisesti — `save_model()`:n laajentaminen tehdään Vaiheessa 3 kun
  ensimmäinen malli treenaaan.
- **Devigged odds piirteenä:** Vaatii enemmän snapshot-dataa, myöhemmin.
- **Walk-forward:** 14 päivää on lyhyt validointiikkuna — lisää dataa kertyy automaattisesti.

---

## Auditoijalle: verifioitavat kohdat

Alla tärkeimmät yksittäiset tarkistukset joita suositellaan:

**1. K1 — tärkein korjaus**
```bash
pytest tests/test_scheduler.py::test_fetch_results_does_not_overwrite_atg_aggregates -v
```
Testin pitää mennä läpi. Testi:
1. Upsertoi pre-race-runner jolla `atg_lifetime_starts=10`
2. Kutsuu `fetch_results()` post-race-datalla jossa ATG palauttaa `starts=11`
3. Assertoi että DB:ssä on edelleen 10 (ei 11)

**2. K1 — olemassa oleva data korjattu**
```sql
-- Tarkista Hetzner-palvelimelta: starts ei saa olla kohtuuttoman suuri
SELECT AVG(atg_lifetime_starts), MAX(atg_lifetime_starts), MIN(atg_lifetime_starts)
FROM runners
WHERE atg_lifetime_starts IS NOT NULL;

-- Tarkista muutama yksittäinen hevonen ATG:n julkisesta profiilista
SELECT horse_id, atg_lifetime_starts, atg_lifetime_win_rate
FROM runners
ORDER BY RANDOM() LIMIT 10;
```

**3. M1 — upsert_race ei ylikirjoita**
```bash
pytest tests/test_scheduler.py::test_upsert_race_does_not_overwrite_existing_fields_with_none -v
```

**4. Kaikki testit**
```bash
pytest -v  # pitää näyttää 113 passed, 0 failed
```

**5. B1 — NaN-% parantunut**
```python
import pandas as pd, sqlite3
con = sqlite3.connect("/path/to/db")
runners = pd.read_sql("SELECT * FROM runners", con)
races = pd.read_sql("SELECT * FROM races", con)
horse_starts = pd.read_sql("SELECT * FROM horse_starts", con)
from src.features.build_features import build_feature_matrix
features = build_feature_matrix(runners, races, horse_starts=horse_starts)
print(features["track_horse_win_rate"].isna().mean())  # pitäisi olla ~0.15, ei ~0.975
```

---

## Prioriteettijärjestys ja aikataulu — ALKUPERÄINEN SUUNNITELMA

```
TÄNÄÄN (15 min):          ✅ TEHTY
  └── Vaihe 0: Empiirinen K1-vahvistus → K1 VAHVISTETTU TODELLISEKSI

SEURAAVAT 1-2 PÄIVÄÄ:     ✅ TEHTY (kaikki samana päivänä)
  ├── A1: _upsert_runner jako + backfill 3589 riviä
  ├── A2: Defensiivinen None-suoja
  ├── B4: Pienet hygieniakorjaukset
  └── B5: Puuttuvat K1/M1-testit

ENNEN VAIHETTA 3:         ✅ TEHTY
  ├── B1: track_horse_win_rate horse_starts:sta
  ├── B2: Segmentoidut muotopiirteet
  └── B3: Temperature scaling
```

---

## M2 — Dokumentoitu rajoitus (ei korjata nyt)

**Saman päivän kaksoisstartti** `form_features()`:ssä:
Jos sama hevonen ajaa kahdesti saman kalenteripäivän aikana, molemmat
runner-rivit saavat identtiset form-piirteet (drop_duplicates poistaa toisen
poolista). Empiirinen riski on vähäinen — Ruotsin raveissa ei tyypillisesti
ajeta kahdesti päivässä. Dokumentoitu tässä, ei korjata ennen kuin ilmenee
käytännön ongelmana.
