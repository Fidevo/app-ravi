# Ravit Edge — Tunnetut ongelmat

> Päivitetty 16.5.2026.
> Vain avoimet ongelmat — korjatut bugit löytyvät tiedoston lopusta.
> Tämänhetkinen tila ja avoimet tehtävät: [`TASK_PROGRESS.md`](TASK_PROGRESS.md).

---

## Avoimet — korjattava ennen Vaihetta 6

### #7 · `correlated_kelly_adjust` ei säädä panoksia korrelaatiolle

**Tiedosto:** `src/betting/bankroll.py`, rivit 112–135

```python
def correlated_kelly_adjust(bets_in_race, config):
    total_prob = sum(p for p, _ in bets_in_race)
    raw = [kelly_stake(p, o, 1.0, config) for p, o in bets_in_race]

    if total_prob >= 1.0:
        return [r * 0.5 for r in raw]
    return raw  # ← normaalitilanteessa ei mitään säätöä
```

Kun `total_prob < 1.0` (normaali tilanne), panokset palautetaan ilman
korrelaatiosäätöä. Oikea toteutus: `[r * total_prob for r in raw]`.

**Prioriteetti:** ei vaikuta tuotantoon nyt — relevantti vasta V6.

---

## Avoimet — korjattava ennen seuraavaa uudelleentreenauksia

*(Ei avoimia — kaikki seuraavaan treeniin vaikuttavat korjattu 16.5.2026)*

---

## Korjattu (16.5.2026)

### #15 · Kuski/valmentaja-nimiformaatti ei täsmää: ATG "Etunimi Sukunimi" vs. Travsport "Sukunimi Etunimi"

**Havainto (16.5.2026):** `driver_win_rate_60d`, `driver_top3_rate_60d`, `trainer_win_rate_60d`,
`trainer_top3_rate_60d`, `driver_track_win_rate_60d`, `trainer_track_win_rate_60d` — kaikki
**0,0 % kattavuus** ja gain=0 pipeline_20260516.py-ajon jälkeen.

**Syy:**
```
ATG runners.driver_name:     "Adam Ivarsson"    (Etunimi Sukunimi)
Travsport horse_starts.driver: "Kontio Jorma"   (Sukunimi Etunimi)
```
`driver_trainer_hs_features()` tekee mergen `df["driver_name"] == hs["driver"]` — koska
nimiformaatit ovat eri järjestyksessä, lähes kaikki matchaukset epäonnistuvat.

**Korjaus:** normalisoi Travsport-nimet ATG-formaattiin ennen mergeä:
```python
# src/features/build_features.py, driver_trainer_hs_features()
def _normalize_name(name: str) -> str:
    """Sukunimi Etunimi → Etunimi Sukunimi"""
    parts = str(name).strip().split()
    return " ".join(parts[1:] + parts[:1]) if len(parts) >= 2 else name

hs = hs.copy()
hs["driver"] = hs["driver"].map(_normalize_name)
hs["trainer"] = hs["trainer"].map(_normalize_name)
```

**Vaikutus:** 6 piirrettä on tällä hetkellä täysin hyödyttömiä. Korjaus lisäisi
merkittävää signaalia kuski- ja valmentajatilastoihin.

**Korjattu:** 16.5.2026, commit `e4b2266` — `_normalize_driver_name()` + per-driver-iteraatio OOM-räjähdyksen estämiseksi.

---

### #16 · `horse_starts`-SQL-suodatin jättää NULL finish_position -rivit pois

**Tiedosto:** `scripts/pipeline_20260516.py` + kaikki muut scriptit jotka lukevat horse_starts

```sql
SELECT * FROM horse_starts WHERE withdrawn != 1 AND finish_position != 99
```

SQLite:ssä `NULL != 99` evaluoituu NULL:ksi (ei trueksi) → rivit joilla `finish_position IS NULL`
suodatetaan pois. Tämä vähentää horse_starts:n 131 891 → 78 435 riviin (28 040 rivi katoaa).

**Vaikutus:** driver/trainer-tilastot lasketaan pienemmästä aineistosta → heikompi kattavuus.

**Korjaus:**
```sql
WHERE (withdrawn IS NULL OR withdrawn != 1)
  AND (finish_position IS NULL OR finish_position != 99)
```

**Korjattu:** 16.5.2026, commit `e4b2266` — kaikki 7 scriptiä päivitetty.

---

## Avoimet — koodihygienia (ei tuotantovaikutusta)

### #2 · `_km_seconds` ei validoi arvoaluetta

**Tiedosto:** `src/data/scheduler.py`

`seconds`-kenttää ei validoida `< 60`. ATG:n data on empiirisesti puhdasta
eikä tämä ole käytännön riski, mutta virheellinen syöte ei kaatuisi ääneen.

### #3 · `birth_year`-laskenta vuodenvaihteessa

**Tiedosto:** `src/data/scheduler.py`, `_upsert_horse()`

```python
obj.birth_year = (date.today().year - age) if isinstance(age, int) else None
```

Manuaalisissa `run-once`-ajoissa tammikuussa voi tulla ±1 vuoden heitto.
Ei ongelma reaaliaikaisessa tuotantokeräyksessä.

### #4 · `_kilometer_time_from_sort` reuna-ehto arvolle < 1000

**Tiedosto:** `src/data/scrapers/travsport.py`

Arvo `900` laskettaisiin `0:90,0`:ksi (sekunnit = 90). Validit
sortValue-arvot alkavat aina ≥ 1000 — ei esiinny tuotantodatassa.

### #9 · Loggaus-aukko

`src.data.atg_client.logger` ja `src.data.scrapers.travsport.logger`
eivät kulje `scheduler.log`-tiedostoon — vain stderriin / journalctl:iin.
Korjaus: lisää loggerit `setup_logging()`:hin. Matala prioriteetti.

### #10 · Odds-sentinelien kommentointi hajallaan

**Tiedosto:** `src/data/scrapers/travsport.py`

`_INVALID_KM_TIME = 9990` ja `_INVALID_ODDS = 9998` viittaavat eri kenttiin
eri normalisoijissa — ei ristiriita, mutta selkeämpi kommentointi auttaisi.

### #12 · Stop-loss lasketaan `starting_bankroll`:sta

**Tiedosto:** `src/betting/bankroll.py`

Stop-loss-kynnys on kiinteä absoluuttinen summa. Kasvaneella bankrollilla
prosentuaalinen raja aktivoituu myöhemmin kuin docstring lupaa.
Mahdollisesti tarkoituksellinen design — ei relevantti ennen V6.

---

## Avoimet — K1-pollutoidut piirteet (aktivoidaan 2026-09)

### #11 · K1-pollutoidut ATG-piirteet pois FEATURE_COLS:ista

Seuraavat piirteet on väliaikaisesti kommentoitu pois `ranker.py`:n `FEATURE_COLS`:ista
koska ATG päivitti ne post-race (K1-vuoto ennen 2026-05-10). Backfill korjasi
vain lifetime-kentät — driver/trainer/current-year-kentät eivät ole korjattavissa
koska pre-race-arvojen nimittäjää ei tunneta.

Aktivoidaan takaisin kun >= 600 puhdasta lähtöä kerätty K1-korjauksen jälkeen
(n. 2026-09-01):
- `atg_current_year_win_rate`
- `atg_driver_win_pct`
- `atg_driver_starts`
- `atg_trainer_win_pct`
- `atg_trainer_starts`

**TODO:** Kommentoi irti 2026-09-01 (tai kun DB:ssä on >= 600 lähtöä post-2026-05-10).

---

## Avoimet — sire-piirteet (aktivoidaan ~2026-07)

### #13 · Sire/dam_sire-piirteet kommentoitu pois FEATURE_COLS:ista

Seuraavat piirteet on väliaikaisesti kommentoitu pois `ranker.py`:n `FEATURE_COLS`:ista.
Empiirinen ablation (Vaihe 3.7, 14.5.2026) osoitti että ne eivät paranna mallia
edes LOO-korjauksen jälkeen: Brier delta = +0.0005, NLL delta = +3. Syy: liian
vähän dataa (455 lähtöä / 17 vrk) ja dam_sire-kattavuus runners:ssa on vain ~24 %.

Aktivoidaan takaisin kun **kaikki** ehdot täyttyvät:
1. DB:ssä on >= 8 viikkoa puhdasta dataa (n. 2026-07-07)
2. dam_sire-kattavuus runners:ssa > 60 %
3. Uusi `sire_ablation_loo.py`-ajo näyttää Brier-parannuksen selvästi
4. **Point-in-time-laskenta toteutettu** — aggregaatti lasketaan vain
   `horse_starts WHERE race_date < runner.race_date` per runner-rivi
   (Auditoija #5, AUDIT_FINDINGS_2026-05-15.md: globaali aggregaatti sisältää
   tulevaisuuden startteja — ei vaikuta nyt, mutta estää aktivoinnin ilman korjausta)

Piirteet:
- `sire_lifetime_win_rate`
- `sire_lifetime_starts`
- `dam_sire_lifetime_win_rate`
- `dam_sire_lifetime_starts`

**TODO:** Aktivoi ~2026-07-07 — toteuta point-in-time-laskenta, aja ablation,
tarkista kaikki 4 ehtoa.

---

## Avoimet — Travronden D2 -piirteet (aktivoidaan ~2026-07)

### #14 · Travronden tr_*-piirteet kommentoitu pois FEATURE_COLS:ista ja CATEGORICAL_COLS:ista

Travronden pre-race -piirteet (scraper + schema + pilot valmis 15.5.2026) kommentoitu
pois mallista A/B-testin tulosten perusteella.

**Corrected A/B results (15.5.2026, 3 kriittistä bugia korjattu):**
- Δ Brier kaikki lähdöt: +0.0003 (alle 0.001-kynnyksen → marginaalinen)
- Δ Brier V-pelilähdöt: +0.0039 (0.001–0.005 välissä → lisätty signaali mutta ei integraatiokynnyksen yli)

**Syyt lykkäykselle:**
1. `tr_game_percent_v` (#1 feature) on Copycat-riski — kopioi markkinasentimentin
   joka on jo `form_market_avg_5`:ssä
2. `tr_start_interval_group` (#40) ei parantunut edes kategorisena koodauksena
3. Pilot-data käytti closing-line-arvoja, tuotanto pollaisi early-line → reaalinen
   paranema todennäköisesti pienempi kuin A/B-tulos antaa ymmärtää

**Infrastruktuuri on paikallaan — mitään ei tarvitse rakentaa uudelleen:**
- Schema-laajennus: `tr_*`-sarakkeet `runners`-taulussa ✅
- Scraper: `src/data/scrapers/travronden.py` ✅
- Feature-laskenta: `src/features/travronden_features.py` ✅
- Pilot-data: DB:ssä ~5 000 runner-riviä (2023–2026) ✅

Aktivoidaan kun **kaikki** ehdot täyttyvät:
1. DB:ssä on >= 8 viikkoa puhdasta dataa (~2026-07-07)
2. Uusi A/B-vertailu **ilman `tr_game_percent_v`** osoittaa muiden TR-piirteiden
   todellisen arvon (poistetaan Copycat-mittaushäiriö)
3. Δ Brier V-pelilähdöissä ≥ 0.005 uudessa A/B-vertailussa

Piirteet (kommentoitu pois `ranker.py` FEATURE_COLS + CATEGORICAL_COLS):
- `tr_start_interval_group` (CATEGORICAL_COLS)
- `tr_is_first_after_castration`, `tr_is_first_new_driver`, `tr_is_first_new_trainer`
- `tr_is_first_shoes`, `tr_is_first_carriage`
- `tr_speed_record_k`, `tr_speed_record_m`, `tr_speed_record_l`
- `tr_game_percent_v` (aktivoi vain multi-snapshot delta-piirteen kanssa)
- `tr_expected_odds` (odottaa > 40 % kattavuutta)

**TODO:** Aktivoi ~2026-07-07 — aja A/B ilman tr_game_percent_v ensin, tarkista
kaikki 3 ehtoa.

---

## Korjattu

| # | Kuvaus | Korjattu |
|---|---|---|
| **#1** | `fetch_results` naive `datetime.now()` → `datetime.now(timezone.utc)` | 10.5.2026 |
| **#5** | `driver_trainer_features` MultiIndex-merge kaatui tai räjäytti rivimäärän | 10.5.2026 |
| **#6** | `backtest.py` `if False` — kvartti-labeli ei koskaan toiminut | 10.5.2026 |
| **#8** | `track_horse_wins_cum` globaali `shift(1)` — data leakage yli ryhmärajojen | 10.5.2026 |
| **K1** | `fetch_results` kirjoitti post-race ATG-aggregaatit — backfill korjasi 3 589 runner-riviä | 10.5.2026 |
| **M1** | `_upsert_race` + `_upsert_runner` ylikirjoittivat olemassa olevat arvot Nonella | 10.5.2026 |
| **B1** | `race_setup_features`: Travsport-trackCodeit eivät matchanneet ATG-ratanimiä | 10.5.2026 |
| **B2** | `form_features`: segmentoidut piirteet olivat 100 % NaN (start_method/distance puuttuivat runners:ista) | 10.5.2026 |
| **dam_sire** | `_upsert_horse` luki `pedigree.mothersFather` — ATG-avain on `pedigree.grandfather`. Backfill täytti 3 477 hevosta | 10.5.2026 |
| **sire-leakage** | `sire_features()` sisällytti hevosen omat startit aggregaattiin → leave-one-out -korjaus | 14.5.2026 |
| **backtest race_date -kollissio** | `rolling_walk_forward` ja `quarterly_walk_forward` kaatuivat KeyError:iin kun race_date oli jo features-DataFramessa | 14.5.2026 |
| **test_travsport fixture** | `sample_792729_*.json` fixture-tiedostot `data/raw/` (gitignored) → siirretty `tests/fixtures/travsport/` (gitattu). 6 testiä kaatui Hetznerillä. | 16.5.2026 |
