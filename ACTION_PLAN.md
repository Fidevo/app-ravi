# Toimintasuunnitelma — ennen Vaihetta 3

> Laadittu 10.5.2026 auditoinnin (AUDIT_FINDINGS.md) pohjalta.
> Kaikki kohdat on priorisoitu sen mukaan mitä täytyy tehdä ennen
> kuin mallin treenaukseen voidaan luottaa.

---

## Vaihe 0 — Empiirinen vahvistus (tee ensin, 15 min)

Ennen kuin korjataan mitään, selvitetään onko K1-vuoto todellinen.

```sql
-- Aja ENNEN lähtöä (esim. T-15min snapshotin jälkeen):
SELECT runner_id, atg_lifetime_starts, atg_lifetime_win_rate,
       atg_current_year_win_rate, atg_driver_win_pct, atg_trainer_win_pct
FROM runners
WHERE race_id = '<jokin tänään ajettava lähtö>';

-- Aja JÄLKEEN (seuraavana aamuna T+24h):
-- sama kysely, sama race_id
-- Jos luvut eroavat → K1 on todellinen ja kriittinen
-- Jos luvut ovat samat → K1 on teoreettinen riski, ei käytännön ongelma
```

**Tulos vaikuttaa kaikkeen:** jos K1 on todellinen, se on tärkein asia
koko projektissa ennen treeniä. Jos K1 on teoreettinen, voidaan edetä
suoraan muihin korjauksiin.

---

## Vaihe A — Kriittiset korjaukset (K1 + M1)

### A1 · K1 — Jaa `_upsert_runner` kahteen funktioon

**Tiedosto:** `src/data/scheduler.py`
**Työmäärä:** ~3 h (toteutus + testit)
**Riippuvuus:** vahvista ensin Vaihe 0

Nykyinen `_upsert_runner()` kirjoittaa sekä pre-race-piirteet (atg_*, shoes,
sulky) että post-race-tulokset (finish_position, km_time, win_odds_final)
samalla kutsulla. `fetch_results()` kutsuu tätä T+30min, jolloin
atg_*-aggregaatit voivat saada post-race-päivitetyt arvot.

**Toteutus:**

```python
# Nykyinen _upsert_runner → uudelleennimetään:
def _upsert_runner_pre_race(session, race, start) -> tuple[bool, bool]:
    """Kaikki kentät: atg_*, shoes, sulky, handicap. Kutsutaan vain pre-race."""
    # ... nykyinen toteutus ...

def _upsert_runner_results(session, race_id: str, start: dict) -> None:
    """Vain tulosriippuvaiset kentät. Kutsutaan fetch_results():sta.
    EI kosketa atg_*-aggregaatteja, shoes/sulky tai muita pre-race-piirteitä.
    """
    runner = session.get(Runner, runner_id)
    if runner is None:
        return  # cold-start: pre-race-haku puuttui — ei kirjoiteta
    runner.finish_position    = start.get("finalPositionNumber")
    runner.kilometer_time_seconds = _km_seconds(start)
    runner.win_odds_final     = _final_odds(start)
    session.flush()
```

**Kutsujärjestys:**
- `run_once()` / `run_forever()` pre-race-lataus → `_upsert_runner_pre_race()`
- `fetch_results()` → `_upsert_runner_results()`
- `retry_incomplete_results()` → `_upsert_runner_results()` (sama kuin fetch_results)

**Testit kirjoitettava:**
```python
def test_fetch_results_does_not_overwrite_atg_aggregates():
    # 1. Upsert pre-race: atg_lifetime_starts=10
    # 2. Kutsu _upsert_runner_results post-race-datalla jossa starts=11
    # 3. Assert: runner.atg_lifetime_starts == 10 (ei muuttunut)
```

---

### A2 · M1 — Defensiivinen None-suoja upserteissa

**Tiedosto:** `src/data/scheduler.py`, `_upsert_race()` ja `_upsert_runner_pre_race()`
**Työmäärä:** ~1 h

Lisää helper joka ei ylikirjoita olemassa olevaa arvoa None:lla:

```python
def _set_if_not_none(obj, field: str, value) -> None:
    """Kirjoita arvo vain jos value ei ole None.
    Suojaa olemassa olevaa dataa retry/refresh-ajoilta.
    """
    if value is not None:
        setattr(obj, field, value)

# Käyttö upsertissa:
_set_if_not_none(obj, "purse_sek", race.get("prize") if isinstance(race.get("prize"), int) else None)
_set_if_not_none(obj, "track_condition", race.get("condition"))
# jne.
```

**Poikkeus:** `finish_position`, `kilometer_time_seconds`, `win_odds_final` — nämä
**saavat** ylikirjoittua None:lla jos tulos peruuntuu tai korjaantuu.

---

## Vaihe B — Mallin parannukset ennen Vaihetta 3

Nämä eivät ole bugikorjauksia vaan merkittäviä parannuksia mallin
piirteiden laatuun. Toteutettavissa 1–2 päivässä.

---

### B1 · track_horse_win_rate horse_starts-datasta (KORKEA PRIORITEETTI)

**Tiedosto:** `src/features/build_features.py`, `race_setup_features()`
**Työmäärä:** ~2 h
**Vaikutus:** NaN 97.5 % → arviolta ~15 % (horse_starts kattaa koko uran)

Nykyinen `track_horse_win_rate` lasketaan vain runners-taulun 14 päivästä →
97.5 % NaN. `horse_starts`-taulussa on `track`-sarake ja 103 747 starttia —
täsmälleen sama laskenta voidaan tehdä pidemmältä ajalta.

**Toteutus:** lisää `horse_starts`-parametri `race_setup_features()`:iin
(kuten `form_features()` jo tekee). Pool-laskenta:
1. Yhdistä runners + horse_starts track-historiatiedot (dedup: runners voittaa)
2. Laske `track_horse_starts` ja `track_horse_wins_cum` koko historiapoolin yli
3. Palauta arvot runners-riveille mergellä

```python
def race_setup_features(
    runners: pd.DataFrame,
    races: pd.DataFrame,
    horse_starts: pd.DataFrame | None = None,  # uusi parametri
) -> pd.DataFrame:
```

**build_feature_matrix()** välittää `horse_starts` myös `race_setup_features()`:lle.

---

### B2 · Segmentoidut muotopiirteet (starttimuoto + matkaluokka)

**Tiedosto:** `src/features/build_features.py`, `form_features()`
**Työmäärä:** ~2 h
**Vaikutus:** 2 uutta informatiiivisempaa piirrettä

`form_avg_finish_5` käyttää kaikkia starttimuotoja ja matkoja sekaisin.
Hevonen joka on erinomainen volttilähdöissä voi näyttää "keskinkertaiselta"
kun autostartit sekoittuvat mukaan.

**Lisättävät piirteet:**
- `form_avg_finish_5_same_method`: rolling 5 viimeisestä startista,
  **vain sama starttimuoto** (auto/volt) kuin nykyisessä lähdössä
- `form_avg_finish_5_same_dist`: rolling 5 viimeisestä startista,
  **vain sama matkaluokka** (sprint/middle/long)

```python
# Poolissa: laske per (horse_id, start_method) ja per (horse_id, dist_bucket)
combined["_dist_bucket"] = pd.cut(combined["distance"], bins=[0,1640,2140,5000],
                                   labels=["sprint","middle","long"])
grouped_method = combined.groupby(["horse_id", "start_method"], group_keys=False)
combined["form_avg_finish_5_same_method"] = grouped_method["finish_position"].transform(
    lambda s: s.shift(1).rolling(5, min_periods=1).mean()
)
```

Tämä vaatii `start_method`- ja `distance`-sarakkeet `horse_starts`-poolissa
(molemmat löytyvät `horse_starts`-taulusta jo nyt).

---

### B3 · Temperature scaling — softmaxin kalibrointi

**Tiedosto:** `src/models/ranker.py`, `predict_win_probabilities()`
**Työmäärä:** ~2 h
**Vaikutus:** kalibrointitarkkuus paranee merkittävästi

Nykyinen softmax: `exp(score) / sum(exp(scores))` — tämä on ei-kalibroitu.
LambdaRankin raw-pisteiden skaala on mielivaltainen ja softmax voi
ali/ylikalibroida systemaattisesti.

**Temperature scaling** oppii yhden parametrin `T` validointijoukolta:

```python
def calibrate_temperature(
    predictions: pd.DataFrame,  # sisältää score + finish_position
) -> float:
    """Löydä optimaalinen lämpötilakerroin minimoimalla NLL."""
    from scipy.optimize import minimize_scalar

    def neg_log_likelihood(T):
        scaled = predictions.copy()
        scaled["score"] = scaled["score"] / T
        scaled["win_prob"] = (
            scaled.groupby("race_id")["score"]
            .transform(lambda s: np.exp(s - s.max()) / np.exp(s - s.max()).sum())
        )
        actual_win = (scaled["finish_position"] == 1).astype(float)
        return -np.sum(actual_win * np.log(scaled["win_prob"].clip(1e-9)))

    result = minimize_scalar(neg_log_likelihood, bounds=(0.1, 10.0), method="bounded")
    return result.x

# Käyttö:
T = calibrate_temperature(val_predictions)
# Tallenna T mallin mukana, käytä ennustamisessa:
out["score"] = out["score"] / T
```

**Lisää FEATURE_COLS:iin:** `T` tallennetaan mallin metadata-tiedostoon
(`save_model` laajennettava).

---

### B4 · Pienet hygieniakorjaukset (P2, P3)

**Työmäärä:** ~30 min

**P2** — `derived_features()` mutatoi DataFramea in-place:
```python
def derived_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()  # lisää tämä
    ...
```

**P3** — `_resolve_cols()` logittaa joka kutsulla:
```python
# Muuta: logita vain jos puuttuvien joukko on muuttunut edellisestä kutsusta
# Yksinkertainen ratkaisu: nosta WARNING vain treeniajossa (train_ranker),
# ei predict_win_probabilities -kutsuissa
if missing_feat or missing_cat:
    if log_missing:  # uusi parametri, default True train_ranker:ssa, False predict:ssa
        logger.warning(...)
```

---

### B5 · Puuttuvat testit (auditoijan aukot)

**Työmäärä:** ~1 h

1. **K1-suojatesti** — `_upsert_runner_results` ei muuta atg_*-kenttiä
2. **Aikavyöhyke DST-rajalla** — `_parse_atg_datetime` 28.3. klo 02:00–03:00
3. **`_parse_terms` ruotsalainen tuhaterotin** — "10.000" vs "10000"
   *(huom: testit löytyivät test_scheduler.py:stä — auditoija ei löytänyt
   niitä, tarkistetaan ovatko ne kattavia)*

---

## Prioriteettijärjestys ja aikataulu

```
TÄNÄÄN (15 min):
  ├── Vaihe 0: Empiirinen K1-vahvistus (SQL-kysely pre/post)

SEURAAVAT 1-2 PÄIVÄÄ:
  ├── A1: _upsert_runner jako (jos K1 vahvistettu) ← BLOKKERI
  ├── A2: Defensiivinen None-suoja
  ├── B4: Pienet hygieniakorjaukset (30 min)
  └── B5: Puuttuvat testit

ENNEN VAIHETTA 3 (2-3 päivää):
  ├── B1: track_horse_win_rate horse_starts-datasta ← KORKEA ARVO
  ├── B2: Segmentoidut muotopiirteet ← KORKEA ARVO
  └── B3: Temperature scaling (voidaan tehdä myös V3:n alussa)

VAIHEEN 3 ALUSSA:
  └── B3: Temperature scaling (jos ei tehty aiemmin)

MYÖHEMMIN / JATKUVASTI:
  ├── M2: Saman päivän kaksoisstartti — dokumentoi rajoitus, ei korjausta nyt
  ├── P1, P5: Koodihygienia
  ├── Devigged odds piirteenä (kun enemmän snapshot-dataa)
  ├── Walk-forward: odota 8+ viikkoa ennen kuin luotat validointimittareihin
  ├── Pace-piirteet (erillinen tutkimustehtävä)
  └── Sukutaulupiirteet Travsportista
```

---

## M2 — Dokumentoitu rajoitus (ei korjata nyt)

**Saman päivän kaksoisstartti** `form_features()`:ssä:
Jos sama hevonen ajaa kahdesti saman kalenteripäivän aikana, molemmat
runner-rivit saavat identtiset form-piirteet (drop_duplicates poistaa toisen
poolista). Empiirinen riski on vähäinen — Ruotsin raveissa ei tyypillisesti
ajeta kahdesti päivässä. Dokumentoitu tässä, ei korjata ennen kuin ilmenee
käytännön ongelmana.

---

## Yhteenveto — mitä tarvitaan ennen Vaihetta 3

| # | Tehtävä | Kriittisyys | Arvio |
|---|---|---|---|
| 0 | Empiirinen K1-vahvistus | **Pakollinen** | 15 min |
| A1 | _upsert_runner jako | **Pakollinen jos K1 vahvistettu** | 3 h |
| A2 | Defensiivinen None-suoja | Tärkeä | 1 h |
| B1 | track_horse_win_rate horse_starts:sta | Korkea arvo | 2 h |
| B2 | Segmentoidut muotopiirteet | Korkea arvo | 2 h |
| B3 | Temperature scaling | Korkea arvo | 2 h |
| B4 | Pienet hygieniakorjaukset | Matala | 30 min |
| B5 | Puuttuvat testit | Tärkeä | 1 h |

**Yhteensä: ~11–12 h työtä.** Realistisesti 2–3 päivää ennen kuin
Vaihe 3 voidaan aloittaa luotettavalla pohjalla.
