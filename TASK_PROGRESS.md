# Ravit Edge — Edistymisraportti

> **Tarkoitus:** projektin tämänhetkinen tila + seuraavat avoimet tehtävät.
> Yksityiskohtainen historia (auditoinnit, koodariraportit, päätökset
> 10.–14.5.2026) on arkistossa: [`docs/archive/TASK_PROGRESS_2026-05_history.md`](docs/archive/TASK_PROGRESS_2026-05_history.md).
> Päivitetty: 15.5.2026 (D2-pilottitulokset lisätty).

---

## Pikakatsaus

| Vaihe | Tila | Päiväys |
|---|---|---|
| Vaihe 1 — Infrastruktuuri | ✅ valmis | 4/2026 |
| Vaihe 2 — Datankeräys + features | ✅ valmis | 10.5.2026 |
| Vaihe 2B — Auditointikorjaukset (K1, M1, B1, B2) | ✅ valmis | 10.5.2026 |
| Vaihe 2.5 — Ratarakenne-piirteet | ✅ valmis | 11.5.2026 |
| Vaihe 3 — Mallin baseline + ablation | ✅ valmis | 14.5.2026 |
| Vaihe 3.6 — Sire-ablation + LOO-korjaus | ✅ valmis | 14.5.2026 |
| Vaihe C1 — Drift-monitorointi | ✅ valmis | 14.5.2026 |
| Vaihe D1 — Travronden Vaihe 1 -selvitys | ✅ valmis | 14.5.2026 |
| **Vaihe D2 — Travronden Vaihe 2 -pilotti** | 🟡 **KÄYNNISSÄ** (vaiheet 1–4 ✅) | viikon sisällä |
| Vaihe C2 — Walk-forward-dokumentointi | 🟡 avoin | ~30 min, halpa |
| Vaihe 4 — Backtest + paperitestaus | ⏸ odottaa V3-tuloksia + lisädataa | ~3.6.2026 |
| Vaihe 5 — Päätöspiste | ⏸ vaatii 8+ viikkoa dataa | ~7.7.2026 |

**Tärkein nyt:** Travronden Vaihe 2 — pace-piirre löydetty (`start_interval_group`),
A/B-vertailu osoittaa lisääkö se Brier-tarkkuutta.

---

## Mallin nykytila (14.5.2026)

| Mittari | Arvo | Selitys |
|---|---|---|
| Treenidata | 2 966 runneria, 281 lähtöä | Apr 27 – May 7 (11 vrk) |
| Testidata | 1 872 runneria, 174 lähtöä | May 8 – May 14 (7 vrk) |
| Brier-score (rs=42) | **0.0818** | Uniform-baseline 0.0843 |
| **Voittosignaali vs. uniform** | **0.0025** | Pieni mutta positiivinen |
| Kalibrointi (0–16 % alue) | Erinomainen | Juuri value-pelien alue |
| FEATURE_COLS määrä | 41 | Sire (4) + K1-pollutoidut (5) odottavat |
| Top-3 piirrettä | `form_market_avg_5`, `atg_lifetime_top3_rate`, `form_avg_finish_5` | |

**Tärkein johtopäätös:** malli on terve mutta voittosignaali on **pieni**.
Lisäksi datan määrä (455 lähtöä / 17 vrk) on liian vähän tuotantopäätökselle.
Odota 8+ viikkoa ennen stop/go-päätöstä (C2-vaatimus).

---

## Strateginen fokus (15.5.2026 alkaen)

Projekti keskittyy **V-pelilähtöihin** ennustetuotannossa koska niissä on
Travrondenin pace-arvio ja paras markkina. Datapuoli säilyy kattavana:

| Asia | Laajuus |
|---|---|
| Datankeräys (scheduler) | Kaikki SE-trottilähdöt |
| Mallin treenaus | Kaikki lähdöt (NaN-tolerantti) |
| Drift-monitorointi | Kaikki lähdöt |
| **Ennustetuotanto (päivittäinen)** | **Vain V-pelilähdöt** |
| Pelaaminen | Single-win V-pelilähdöistä (Unibet/Betsson + Betfair) |

V-pelilähdöistä ei pelata V-peliä — pelataan **single-win-markkinaa** koska
sen takeout on ~5–8 % vs. V-pelin ~22 %.

---

## Avoimet tehtävät

### 🎯 Vaihe D2 — Travrondenspel pre-race-piirteet (TÄRKEIN)

**Status:** 🟡 KÄYNNISSÄ — vaiheet 1–4 valmis, vaihe 5–7 avoimena
**Tausta:** D1-tutkimus löysi `start_interval_group` -kentän (asiantuntijan
per-hevonen, per-lähtö pace-arvio). Korvaa todennäköisesti C3:n.

#### ✅ Valmistuneet vaiheet (15.5.2026)

**Vaihe 1:** `src/data/scrapers/travronden.py` — `TravrondenAPIClient`
- Cache 30 vrk TTL, 1 req/s, retry 3× exponential-backoff, context manager
- 30 testiä, kaikki läpäisty

**Vaihe 2:** `src/features/travronden_features.py`
- `parse_travronden_race()`: 11 pre-race-piirrettä, EI leakage-vaarallisia (`speed`, `comment` poissuljettu)
- `merge_travronden_features()`: LEFT JOIN horse_id:llä, NaN tolerantti, dedup keep="last"
- 30 testiä, kaikki läpäisty

**Vaihe 3 (schema-laajennus):** AVOIN — odottaa auditoijan päätöstä arkkitehtuurista ⬇️

**Vaihe 4:** `scripts/travronden_pilot.py` — **pilotti ajettu Hetznerillä 15.5.2026**

#### 📊 Pilottitulokset (Hetzner, 15.5.2026)

| Mittari | Arvo |
|---|---|
| Kierroksia haettu | 90 (85 uniikkia) |
| Lähtöjä | 522 |
| Runner-rivejä | **4 927** |
| Päivämääräväli | 2023-02-13 – 2026-05-07 |

**Piirteiden kattavuus:**

| Piirre | Kattavuus | Arvio |
|---|---|---|
| `tr_start_interval_group` | **91.5 %** ✅ | ⭐⭐⭐ Erinomainen |
| `tr_is_first_after_castration` | **100 %** ✅ | ⭐⭐ |
| `tr_is_first_new_driver` | **100 %** ✅ | ⭐⭐ |
| `tr_is_first_new_trainer` | **100 %** ✅ | ⭐ |
| `tr_is_first_shoes` | **100 %** ✅ | ⭐ |
| `tr_is_first_carriage` | **100 %** ✅ | ⭐ |
| `tr_speed_record_k` | 37.0 % 🟡 | ⭐⭐ matala kattavuus |
| `tr_speed_record_m` | **73.4 %** ✅ | ⭐⭐ |
| `tr_speed_record_l` | 35.1 % 🟡 | ⭐⭐ matala kattavuus |
| `tr_expected_odds` | **23.8 % ❌** | ⭐ ei juurikaan käytettävissä |
| `tr_game_percent_v` | **81.1 %** ✅ | ⭐ |

**Johtopäätös pilottidatasta:**
- `tr_start_interval_group` (91.5 %) ja kaikki is_first_*-piirteet (100 %) ovat
  tuotantokelpoiset — LightGBM käsittelee 8.5 % NaN:t.
- `tr_expected_odds` (23.8 %) on liian harva tuotantoon sellaisenaan —
  harkitse jättämistä pois tai käyttöä vain kun saatavilla.
- Speed records K ja L (35–37 %) voidaan sisällyttää — NaN-tolerantti malli.

#### ❓ Avoin arkkitehtuurikysymys — auditoijalle (Vaihe 3)

**Kysymys:** `tr_*`-sarakkeet `runners`-tauluun vai erilliseen `runner_travronden_stats`-tauluun?

**Vaihtoehto A — sarakkeet `runners`-tauluun** (yksinkertaisempi):
- Pro: suora LEFT JOIN fetch_race_snapshot():ssa, ei extra-taulua, `_COLUMN_MIGRATIONS` hoitaa
- Con: 11 uutta saraketta `runners`-tauluun (jo 40+ sarakkeet), turhia NULL:ja ei-V-pelilähdöillä

**Vaihtoehto B — erillinen `runner_travronden_stats`-taulu** (siistimpi):
- Pro: selkeä separaatio, runner-taulu pysyy siistinä, JOIN vain tarvittaessa
- Con: extra JOIN pipeline-koodiin, monimutkaisempi schema-hallinta

**Suositus koodarilta:** Vaihtoehto A yksinkertaisuuden vuoksi. Datapipeline on jo
NaN-tolerantti, ja 11 saraketta runners-taulussa (~45→56) on hallittavissa.

#### Avoimet vaiheet

- **Vaihe 3:** Schema-laajennus (odottaa auditoijan vastausta yllä olevaan kysymykseen)
- **Vaihe 5:** A/B-vertailu — baseline vs. baseline + Travronden → Brier-paranema
  - Päätös: jos paranema ≥ 0.005, integroi tuotantoon
- **Vaihe 6:** Pollaus-cron `run_forever`:iin (Ma–Pe 15/17, La 9/11/13, Su 10/12)
- **Vaihe 7:** V-pelilähtöjen tunnistus (`runners.is_v_race` tai näkymä)

**Aikabudjetti jäljellä:** ~2–3 päivää

### 🟡 Vaihe C2 — Walk-forward-dokumentointi

**Status:** avoin, ~30 min
**Tehtävä:** päivitä ROADMAP.md:n Vaihe 5 selvyydellä: **stop/go-päätöstä ei tehdä
alle 8 viikon yhteistuloksesta**, vähintään `n ≥ 200` paperipeliä. Lisää
"D-Liian vähän dataa" -kategoria.

### ⏰ Aikataulutetut muistutukset

| Päiväys | Tehtävä |
|---|---|
| ~2026-06-08 | `rolling_walk_forward` ajetaan, vaatii 42+ vrk dataa |
| ~2026-07-01 | `train_window_days` 28 vs. 56 -ablation, vaatii 56+ vrk dataa |
| ~2026-07-07 | **Sire-piirteiden palautus** FEATURE_COLS:iin (KNOWN_ISSUES #13) |
| ~2026-07-07 | **8 vk:n stop/go-päätös** (C2-vaatimus täyttyy) |
| ~2026-09-01 | **K1-pollutoitujen kenttien palautus** (KNOWN_ISSUES #11) |

### ⏸ Suunnitellut myöhempään

- **Vaihe C3 — Pace-pilotti** — todennäköisesti **ohitettavissa kokonaan** jos
  Travronden Vaihe 2 tuottaa hyvän pace-piirteen
- **Vaihe 4 — Backtest + paperitestaus** — 2–4 vk:n päässä Vaihe 3:n
  jatkokokeista
- **Vaihe 5 — Päätöspiste** — ~7.7.2026 (8 vk dataa)

---

## Infrastruktuuri tuotannossa

- ✅ **Drift-monitorointi** — sunnuntaisin 02:00 (Hetzner cron),
  raportti `data/logs/feature_drift_YYYY-WW.csv`. K1-tyyppiset bugit
  havaitaan viikossa.
- ✅ **Scheduler** — datankeräys jatkuu (4×snapshot/lähtö + tulokset T+30min
  + retry 04:30). Cron 03:00 hakee päivän lähdöt.
- ✅ **Backupit** — päivittäinen DB-backup Hetzneriltä.
- ✅ **Testit** — 302 passing (lokaalisti, sis. uudet D2-testit); 244 passing (Hetzner; `test_travsport.py`
  on tunnettu ympäristöongelma, ei regressio).

---

## Tiedostohakemisto

### Aktiiviset MD-tiedostot (juuressa)

- **`README.md`** — projektin esittely, asennus, käyttö
- **`ROADMAP.md`** — vaiheet, aikataulu, päätösehdot
- **`KNOWN_ISSUES.md`** — avoimet bugit, aktivointimuistutukset
- **`TASK_PROGRESS.md`** *(tämä)* — tämänhetkinen tila + avoimet tehtävät

### Tehtäväohjeet (`docs/`)

- **`docs/TASK_PLAN_FIXES.md`** — Vaihe 2B/2.5 auditointikorjaukset
- **`docs/TASK_TRACK_FEATURES.md`** — Vaihe 2.5 ratarakenne-piirteet
- **`docs/TASK_TRAVRONDEN_INVESTIGATION.md`** — Vaihe D Travronden-pilotti

### Arkisto (`docs/archive/`)

- **`AUDIT_REQUEST.md`**, **`AUDIT_FINDINGS.md`** — alkuperäinen auditointi (10.5.2026)
- **`ACTION_PLAN.md`**, **`ACTION_PLAN_v1.md`** — vanhat korjaussuunnitelmat
- **`TASK_PROGRESS_2026-05_history.md`** — yksityiskohtainen päivittäinen
  edistymishistoria (10.–14.5.2026), auditoijan tarkistukset, koodariraportit,
  ablation-tulokset
