# Ravit Edge — Roadmap

> Päivitetty 16.5.2026.
> Vaiheet 1–3, D1, D3 (dashboard) + auditointikorjaukset + drift-monitorointi VALMIIT.
> D2 (Travronden) vaiheet 1–5 ✅, 6–7 avoimena. D4 (backfill) käynnissä Hetznerillä.
> Tämänhetkinen tila ja avoimet tehtävät: [`TASK_PROGRESS.md`](TASK_PROGRESS.md).

---

## Strateginen fokus (15.5.2026 alkaen)

Projekti keskittyy **V-pelilähtöihin** (V64, V75, V86, V5, V4, V3) koska
niissä on Travrondenin pace-arvio (`start_interval_group`) ja paras
markkinaliikkuvuus. Erottelu datapuolen ja ennustustuotannon välillä on
olennainen:

| Asia | Laajuus | Syy |
|---|---|---|
| **Datankeräys** | Kaikki SE-trottilähdöt | Treenidata, vakaampi malli |
| **Mallin treenaus** | Kaikki lähdöt | Maksimoi otoskoko, LightGBM kestää NaN:n |
| **Drift-monitorointi** | Kaikki lähdöt | Havaitsee bugit kaikissa lähteissä |
| **Ennustetuotanto** | Vain V-pelilähdöt + pyynnöstä yksittäiset | Paras pre-race-data, paras markkina |
| **Pelaaminen** | Single-win V-pelilähdöistä | Unibet/Betsson + Betfair Exchange |

V-pelilähdöistä **ei pelata V-peliä** — pelataan single-win-markkinaa samoissa
lähdöissä. Travronden tarjoaa vain paremman pre-race-näkemyksen.

---

## Nykytila (15.5.2026)

Datankeräys on pyörinyt tuotannossa 4.5.2026 alkaen (18 vrk). Auditointi-
korjaukset (K1 data leakage, M1, B1, B2) ovat tehtynä. Mallin baseline on
treenattu ja drift-monitorointi tuotannossa. Vaihe D2 (Travronden pace-
piirteet) on seuraava prioriteetti.

**Dataset (14.5.2026):**

| Mittari | Arvo |
|---|---|
| Keräyspäiviä | 18 vrk reaaliaikainen (27.4 → 16.5.2026) + backfill 2023→ käynnissä |
| Trot-lähtöjä | **455** (reaaliaikainen) + backfill lisää ~3 000–5 000 |
| Trot-runnereita | **4 838** (reaaliaikainen) |
| Hevoshistoriastartteja | **115 824** (Travsport) |
| Hevosia (`horses`) | **4 114** (sire 100 %, dam_sire 34 %) |
| Ratoja (`tracks`) | **30** (25 Travronden-rikkaita + 5 manuaalista stub) |
| Testejä | **363** (lokaali + Hetzner) |
| FEATURE_COLS | **45** aktiivista (+ 6 CATEGORICAL_COLS) |
| Mallin Brier (rs=42) | **0.0818** (vs. uniform 0.0843) — uudelleentreenaus backfillin jälkeen |
| Voittosignaali | 0.0025 — pieni, **vaatii lisädataa** |

---

## Vaihe 1 — Infrastruktuuri ✅ VALMIS (4/2026)

- ATG REST API -asiakas + Travsport WebAPI -asiakas
- SQLite WAL-mode + skeemamigraatio
- Scheduler: 4-vaiheinen snapshot-ajo per lähtö (T-15/10/5/2min)
- Result-haku T+30min + päivittäinen retry 04:30
- CLV-tracker ja bankroll management
- Hetzner CAX11, Helsinki + päivittäinen DB-backup, UFW, fail2ban
- GitHub-versionhallinta

---

## Vaihe 2 — Datankeräys + feature engineering ✅ VALMIS (10.5.2026)

- Shoes/sulky-piirteet (6 saraketta runners-tauluun)
- Gallop-suodatus (Bro Park, Göteborg Galopp, Jägersro Galopp pois)
- Race-luokka ATG:n terms-parsinta (min/max_earnings, age_group)
- Track-condition Travsportista
- Feature pipeline: form_features (103k starttia), driver_trainer_features,
  race_setup_features, derived_features

---

## Vaihe 2B — Auditointikorjaukset ✅ VALMIS (10.5.2026)

Auditoija (Claude Opus 4.7) löysi neljä merkittävää bugia. Kaikki korjattu.

| Bugi | Vaikutus | Korjaus |
|---|---|---|
| K1 | `fetch_results` ylikirjoitti ATG-aggregaatit post-race → data leakage | `_ensure_runner_exists()` + backfill 3 589 riviä |
| M1 | `_upsert_race`/`_upsert_runner` ylikirjoittivat Nonella | `_set_if_not_none()` molempiin |
| B1 | `race_setup_features`: Travsport-trackCode (`"S"`) ≠ ATG-nimi (`"Solvalla"`) | `track_codes.py` -mappi, 26 SE-rataa |
| B2 | `form_features`: segmentoidut piirteet 100 % NaN tuotannossa | Pre-merge start_method+distance + Travsport-koodin normalisointi |

Lisäksi: `pedigree.grandfather → horses.dam_sire` (oli `mothersFather`, väärä
avain), `backfill_dam_sire()` täytti 3 477 hevosen dam_sire-kentän.

Yksityiskohdat: [`docs/TASK_PLAN_FIXES.md`](docs/TASK_PLAN_FIXES.md).

---

## Vaihe 2.5 — Ratarakenne-piirteet ✅ VALMIS (11.5.2026)

Travrondenspel-API:n `round.tracks`-objektista 7 staattista rakennepiirrettä:

| Tehtävä | Tila |
|---|---|
| A: `Track`-luokka schemaan (19 saraketta) | ✅ |
| B: Travronden-scraper + CLI (`fetch-track-structures`) | ✅ |
| C: Sanity-tarkistukset (kattavuus + arvoalueet, Wikipedia-validointi hylätty liiallisena) | ✅ |
| D: `track_structure_features()` + 7 piirrettä FEATURE_COLS:iin | ✅ |
| E: Smoke-testi (`track_length_total notna% = 90.0 %`) | ✅ |

Yksityiskohdat: [`docs/TASK_TRACK_FEATURES.md`](docs/TASK_TRACK_FEATURES.md).

---

## Vaihe 3 — Mallin baseline ✅ VALMIS (14.5.2026)

**Workflow vakiintunut:**

```python
runners = pd.read_sql("""
    SELECT r.*, ra.race_date, h.birth_year
    FROM runners r
    JOIN races ra ON r.race_id = ra.race_id
    LEFT JOIN horses h ON r.horse_id = h.horse_id
""", con)
features = build_feature_matrix(
    fill_finish_positions(runners), races,
    horse_starts=horse_starts, horses=horses, tracks=tracks,
)
model = train_ranker(train_df, random_state=42)
```

**Tärkeimmät tutkimustulokset:**

- **Sire-piirteet eivät paranna mallia** (Brier delta +0.0005 LOO-korjauksen
  jälkeen) → kommentoitu pois FEATURE_COLS:ista, palautetaan ~7.7.2026
- **`form_market_avg_5`-ablation** vahvisti markkina-arvio sisältää aitoa
  signaalia (Brier +0.0003 ilman sitä) — ei pelkkä korrelointi muiden kanssa
- **Brier 0.0818 vs. uniform 0.0843** → voittosignaali 0.0025, pieni mutta
  positiivinen — **älä tee tuotantopäätöstä alle 8 vk:n datalla**

**Vaiheen 3 jälkitehtävät tehty:**
- `compute_nll()`, `calibrate_isotonic()`, `apply_isotonic()` lisätty
- `rolling_walk_forward()` 14 vrk -ikkunalla
- `edge_decay_analysis()` tukee Brier-scorea (ei vain ROI)
- `random_state`-parametri `train_ranker`:iin (reproducibility)

Mallitiedosto: `data/model_baseline_20260514.lgb` (Hetzner).

---

## Vaihe C1 — Drift-monitorointi ✅ VALMIS (14.5.2026)

Tuotannossa: sunnuntaisin 02:00 (Hetzner cron) ajetaan `scripts/run_feature_drift.py`.

- Per-piirre mean/std/p25/p50/p75/NaN-% kaikille FEATURE_COLS:n piirteille
- Vertaa edelliseen historiaan, hälytys jos NaN-% +10pp tai mean/p50 yli 2σ
- Alle 3 vk historaa: raw 20 % raja
- K1-tyyppinen leakage havaitaan **viikossa** (testattu yksikkötesteillä)
- 15 testiä, kaikki passing

Lokit: `data/logs/feature_drift_YYYY-WW.csv` + `drift_cron.log`.

---

## Vaihe D1 — Travronden Vaihe 1 -selvitys ✅ VALMIS (14.5.2026)

Tutkittu 18 finished-kierrosta. Kriittiset löydökset:

- **`speed`-kenttä on POST-RACE km-aika**, ei pre-race ennuste → ei käytetä
  piirteenä (leakage-riski)
- **`rating`-kenttä 0 % täytetty** → ei saatavilla
- **`start_interval_group {1, 11, 21, 31}`** — asiantuntijan per-hevonen, per-
  lähtö **pace-arvio**. Tämä on **lähinnä pace-piirrettä mitä saadaan ilman
  manuaalista scrapingia**. C3:n korvike.

Yksityiskohdat: [`docs/TASK_TRAVRONDEN_INVESTIGATION.md`](docs/TASK_TRAVRONDEN_INVESTIGATION.md).

---

## Vaihe D2 — Travronden pre-race-piirteet 🟡 KÄYNNISSÄ (vaiheet 1–5 ✅, 6–7 avoimena)

Pace-piirteen integrointi V-pelilähtöihin.

### Tekninen toteutus

1. **`src/data/scrapers/travronden.py`** — HTTP-asiakas
   - Cache `data/raw/travronden/{round,race}_{id}.json`, 30 vrk TTL
   - Rate limit 1 req/s, rehellinen User-Agent
   - Smart-skip: jos kaikki kierroksen legit ovat täytetyt cachessa, ei uudelleenpyyntöä

2. **`src/features/travronden_features.py`** — vain pre-race-kentät
   - `tr_start_interval_group` (pace-arvio 1/11/21/31) ⭐⭐⭐
   - `tr_is_first_after_castration/new_driver/new_trainer/shoes/carriage`
   - `tr_speed_record_k/m/l` (rikkaampi kuin atg_best_km_for_this_setup)
   - `tr_expected_odds`, `tr_game_percent_v`
   - **EI:** `speed` ja `comment` (post-race, leakage-riski)

3. **Pollaus-aikataulu** — scheduler-cron, Stockholm-aika:

   | Päivä | Ajat | Perustelu |
   |---|---|---|
   | Ma–Pe | 15:00, 17:00 | ATG-lähdöt alkavat 18:00–19:00 |
   | Lauantai | 09:00, 11:00, 13:00 | V75 alkaa usein 14:30 |
   | Sunnuntai | 10:00, 12:00 | V75 alkaa ~15:00 |

   Pollaus discoveroi päivän round_id:t, hakee jokaisen kierroksen legit,
   tallentaa `start_interval_group`:n DB:hen. Cache estää uudelleenpyynnöt.

4. **100-kierroksen pilotti** — A/B-vertailu Vaihe 3:n baseline-malliin
   - A: nykyinen malli (41 piirrettä, `random_state=42`, Brier 0.0818)
   - B: nykyinen + tr_*-piirteet (~48 piirrettä)

5. **Päätös:** paranema **ΔBrier ≤ -0.005** → tuotantointegraatio. Pieni
   paranema → kerää lisää. Negatiivinen → hylkää, dokumentoi syyt.

Aikabudjetti: 3–5 päivää.

### V-pelilähtöjen tunnistus

Travrondenin `round.legs[].race` → ATG race_id mappaus tehdään
`(race_date, track, race_number)`-avaimella. Tallenna `runners.is_v_race`
boolean tai erillinen `v_pool_races`-näkymä päivittäin päivittyvänä.

---

## Vaihe 4 — Backtest + paperitestaus ⏸ (~3.6.2026, kun 42+ vrk dataa)

- `rolling_walk_forward()` 14 vrk -ikkunalla
- Paperitestauksen aloitus **V-pelilähdöistä** (ei rahaa)
  - Kirjaa value-pelit, älä pelaa
  - Tallenna T-2min kerroin pelihetkenä, vertaa closing odds:iin
- CLV-mittaus ATG-devig-laskennalla
- Tavoite: vähintään 100 paperipeliä **V-pelilähdöistä** ennen päätöstä

> **Huom:** paperitestaus rajataan V-pelilähtöihin koska niissä on paras
> pre-race-data (Travronden) ja paras likviditeetti pelivaiheessa.

---

## Vaihe 5 — Päätöspiste ⏸ (~7.7.2026)

**Vaatii vähintään 8 viikkoa walk-forward-dataa** ennen mitään stop/go-päätöstä.
Trotissa on kausivaihtelua (talvi/kesä, ratakelit) joka ei näy lyhyemmässä
ikkunassa.

| Lopputulos | CLV | n | Toimenpide |
|---|---|---|---|
| **A: Edge todistettu** | +3 % tai enemmän | ≥ 200 | Siirry V6 pienillä rahoilla |
| **B: Edge epäselvä** | -2 % – +3 % | ≥ 200 | Lisää 4 vk dataa, treenaa uudelleen |
| **C: Ei edgea** | alle -2 % | ≥ 200 | Pysähdy, tutki bugit, älä pelaa |
| **D: Liian vähän dataa** | mikä tahansa | < 200 | **Älä tee päätöstä — odota** |

Useimmat ML-vedonlyöntiprojektit eivät pääse tähän vaiheeseen positiivisella
lopputuloksella — rehellinen näkymä, ei pessimismiä.

---

## Vaihe 6 (vain jos edge todistettu) — Pelaaminen pienillä rahoilla

- Streamlit-dashboard **valmis** (`src/dashboard/app.py`) — Vaihe D3 tehty ✅
  - V-pelilähdöt default-näkymässä, track-ryhmittely, live-kertoimet, SHAP, ATG-linkit
- Manuaalinen pelaaminen 1–5 € panoksiin single-win-markkinaan
- 4–8 viikkoa CLV-seurantaa oikealla rahalla
- 200–300+ peliä tilastollisesti merkittävään lopputulokseen
- Korjattava ennen V6: `correlated_kelly_adjust` (KNOWN_ISSUES.md #7)

V-pelilähdöistä ei pelata V-peliä (multi-leg-tuotteita) — pelataan
yksittäisten lähtöjen voittajamarkkinaa fixed-odds-vedonvälittäjillä.

---

## Vaihe 7 (vain jos pelaaminen tuottavaa) — Skaalaus

- Betfair Exchange -integraatio (tutki likviditeetti ensin)
- Persistentit job-storet (`SQLAlchemyJobStore`) scheduler-restartteja varten
- Sharp-markkinakertoimet (Pinnacle/Betfair) CLV-vertailuun — skeema valmis
- Telegram/email-alert **vain V-pelilähdöistä** kun value-peli löytyy
- Tuotantotreenissä **ensemble 5–10 random_state-seedillä** vähentää
  yksittäisen ajon kohinaa

---

## Aikataulutetut muistutukset

| Päiväys | Tehtävä | Lähde |
|---|---|---|
| Heti | Vaihe D2 vaiheet 6–7 (pollaus-cron, is_v_race scheduler) | TASK_PROGRESS.md |
| ~26.5.2026 | **Backfill valmis** — `retrain_model.py` + `evaluate_model.py` | D4 |
| ~6.6.2026 | `rolling_walk_forward` ajo, 42+ vrk dataa kerätty | Vaihe 3 |
| ~1.7.2026 | `train_window_days` 28 vs. 56 -ablation | Vaihe 3 |
| ~7.7.2026 | **Sire-piirteiden palautus** + uusi ablation | KNOWN_ISSUES #13 |
| ~7.7.2026 | **Vaihe 5 päätöspiste** (jos n ≥ 200) | Vaihe 5 |
| ~1.9.2026 | **K1-pollutoitujen kenttien palautus** | KNOWN_ISSUES #11 |

---

## Pitkän tähtäimen visio

- **Pace-piirre** — ratkaistu Travronden `start_interval_group`-kentällä
  (Vaihe D2 vahvistaa). C3-manuaaliscraping ei enää tarvittavissa.
- **V-pelilähtöjen fokus** — pace-piirre kattaa vain V-pelilähdöt;
  laajempi pace-piirre vaatisi manuaalisen scrapingin Travsportin
  raviraporteista (suunniteltu vain jos V-pelipelaaminen tuottava)
- **Sää-integraatio:** Open-Meteo — rata × sade × hevosen rata-kokemus
- **Sukutaulupiirteet** — `horses.sire` + `horses.dam_sire` 100 %/34 % notna;
  aktivoidaan ~7.7.2026 jos ablation osoittaa parannuksen
- **Conditional logit / Plackett-Luce** trifecta-todennäköisyyksille
- **Postgres** jos DB kasvaa yli 500 MB
- **Ensemble-treenaus** Vaihe 7 — useita random_state-seedejä → keskiarvoennusteet
