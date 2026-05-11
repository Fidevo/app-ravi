# Ravit Edge — Roadmap

## Nykytila (11.5.2026 — Vaihe 2 valmis + korjaukset, Vaihe 2.5 käynnissä)

Datankeräys on pyörinyt tuotannossa 4.5.2026 alkaen. Feature engineering
-pipeline on valmis ja validoitu oikealla datalla. Mallin treenaukselle ei
ole teknisiä esteitä.

**Dataset (10.5.2026):**

| Mittari | Arvo |
|---|---|
| Keräyspäiviä | 14 vrk (27.4 → 10.5.2026) |
| Trot-lähtöjä | **356** (galloppi suodatettu) |
| Trot-runnereita | **3 757** |
| Koulutuskelpoiset rivit | **3 685** (fill_finish_positions jälkeen) |
| Hevoshistoriastartteja | **103 747** (Travsport, 2014→) |
| Odds-snapshotteja | **14 758** (T-15/10/5/2min + tulos) |
| Testejä | **192** (kaikki vihreällä) |
| Keräysvauhti | ~23 trot-lähtöä/vrk (lauantait ~35+) |

**Piirteiden laatu (validoitu 10.5.2026):**

| Piirre | NaN ilman horse_starts | NaN horse_starts kanssa |
|---|---|---|
| form_avg_finish_5 | 95 % | **11 %** |
| form_win_rate_5 | 93 % | **11 %** |
| atg_lifetime_win_rate | — | 4.5 % |
| atg_driver_win_pct | — | 7.5 % |

---

## Vaihe 1: Infrastruktuuri ✅ VALMIS

- ATG REST API -asiakas + Travsport WebAPI -asiakas
- SQLite WAL-mode + skeemamigraatio
- Scheduler: 4-vaiheinen snapshot-ajo per lähtö
- Result-haku T+30min + päivittäinen retry 04:30
- CLV-tracker ja bankroll management
- Hetzner CAX11, Helsinki + päivittäinen DB-backup, UFW, fail2ban
- GitHub-versionhallinta

---

## Vaihe 2: Datankeräys + feature engineering ✅ VALMIS

### Datankeräysjakso (27.4 – 10.5.2026)

- Shoes/sulky-piirteet (6 saraketta runners-tauluun)
- Gallop-suodatus (Bro Park, Göteborg Galopp, Jägersro Galopp pois)
- `retry_incomplete_results` cron 04:30 — km-ajat täydentyvät yön yli
- Track condition Travsportista + race-luokka ATG:sta kaikille 356 lähdölle
  (`race_min_earnings`, `race_max_earnings`, `race_age_group`, `track_condition`)

### Feature engineering -pipeline (10.5.2026)

Kaikki tehtiin ennen Vaihetta 3, testit jokaiselle muutokselle:

| Muutos | Tiedosto | Vaikutus |
|---|---|---|
| FEATURE_COLS-nimet korjattu (blokkeri) | `ranker.py` | Olisi kaatunut KeyError:iin |
| ATG-aggregaatit FEATURE_COLS:iin (9 piirrettä) | `ranker.py` | Koko vuoden ohjastaja/hevostilastot |
| Shoes/sulky FEATURE_COLS:iin | `ranker.py` | Varustemutossignaali |
| Race-luokka `race_setup_features()`:iin | `build_features.py` | Lähdön tason konteksti |
| `derived_features()`: horse_age + barfota_law | `build_features.py` | Talvikiellon erottelu |
| `form_features()` käyttää horse_starts (103k) | `build_features.py` | NaN 95 % → 11 % |
| `fill_finish_positions()` — treeniesimerkit | `build_features.py` | 2 332 → 3 685 koulutuskelp. riviä |
| Bugit #1, #5, #6, #8 korjattu | scheduler + build_features | Ks. KNOWN_ISSUES.md |

---

## Vaihe 2B: Korjaukset (11.5.2026) ✅ VALMIS

Ennen Vaihetta 3 korjattu auditoijan löytämät ongelmat:

| Korjaus | Tiedosto | Tulos |
|---|---|---|
| B1: Isotoninen regressio kalibroinnissa | `ranker.py` | Kalibrointi nyt monotoninen |
| B2: `pedigree.grandfather` → `horses.dam_sire` | `scheduler.py` | 88 % notna (oli 0 %) |
| B2: `backfill_dam_sire()` — ryhmästrategia | `scheduler.py` | 3 477 hevosta, 356 race-kutsua |

Ks. tarkemmin: `docs/TASK_PLAN_FIXES.md` ja `TASK_PROGRESS.md`.

---

## Vaihe 2.5: Ratarakenne-piirteet 🟡 KÄYNNISSÄ

Track-piirteet antavat mallin oppia ratafysiikan vaikutukset automaattisesti.

| Tehtävä | Status | Kuvaus |
|---|---|---|
| A: `Track`-luokka schemaan | ✅ VALMIS | 19 saraketta, `tracks`-taulu |
| B: Travronden-scraper | ⬜ | `src/data/scrapers/travronden_tracks.py` |
| C: Validointi (Wikipedia) | ⬜ | 3–5 rataa vertaillaan |
| D: `track_structure_features()` | ⬜ | Lisätään `build_features.py` + `FEATURE_COLS` |
| E: Smoke test | ⬜ | `track_length_total notna% >= 95` |

Ks. tarkemmin: `docs/TASK_TRACK_FEATURES.md` ja `TASK_PROGRESS.md` (VAIHE 2.5).

---

## Vaihe 3: Mallin prototyyppi 🟡 VOI ALOITTAA NYT

Ei odoteta kesäkuuhun — data riittää prototyyppiin jo nyt.

**Workflow:**

```python
# 1. Lataa data
runners  = pd.read_sql("SELECT r.*, ra.race_date FROM runners r JOIN races ra ...", con)
races    = pd.read_sql("SELECT * FROM races", con)
horse_starts = pd.read_sql(
    "SELECT * FROM horse_starts WHERE withdrawn != 1 AND finish_position != 99 ...", con
)

# 2. Esikäsittely
runners_filled = fill_finish_positions(runners)
features = build_feature_matrix(runners_filled, races, horse_starts=horse_starts)

# 3. Walk-forward split (ei random — data leakage)
train = features[features["race_date"] < "2026-05-05"]
test  = features[features["race_date"] >= "2026-05-05"]

# 4. Treenaa
model = train_ranker(train)

# 5. Arvioi
predictions = predict_win_probabilities(model, test)
# → calibration table, NDCG@1, NDCG@3
```

**Tavoitteet vaiheessa 3:**

- [ ] Walk-forward treenaus + evaluointi (NDCG, kalibrointitaulu)
- [ ] Piirteiden tärkeysjärjestys (LightGBM feature importance)
- [ ] `horse_age` — lisää birth_year JOIN runners-dataan (horses-taulu)
- [ ] Mallin tallennus ja lataus (`save_model` / `load_model`)
- [ ] Ennuste tuleville lähdöille + value-bet-detektio

**Tunnetut rajoitukset nyt:**

- 356 lähtöä on vähän — prototyyppi, ei tuotantomalli
- `track_horse_win_rate` on 97.5 % NaN (vain 14 pv dataa samalta radalta)
- `driver_win_rate_365d` on 35 % NaN (ATG:n valmis aggregaatti on parempi tässä vaiheessa)
- Malli paranee automaattisesti kun keräys jatkuu

---

## Vaihe 4: Backtest + paperitestaus (2–4 viikkoa V3:n jälkeen)

- Walk-forward backtest viimeisten viikkojen datalla
- Paperitestauksen aloitus elävillä lähdöillä (ei rahaa)
  - Kirjaa value-pelit, älä pelaa
  - Tallenna T-2min kerroin pelihetkenä, vertaa closing odds:iin
- CLV-mittaus ATG-devig-laskennalla
- Tavoite: vähintään 100 paperipeliä ennen päätöstä

---

## Vaihe 5: Päätöspiste (~8 viikkoa V3:n käynnistymisestä)

| Lopputulos | CLV | Toimenpide |
|---|---|---|
| **A: Edge todistettu** | +3 % tai enemmän, n>100 | Siirry V6 pienillä rahoilla |
| **B: Edge epäselvä** | -2 % – +3 %, kohinaa | Lisää 4 vk dataa, treenaa uudelleen |
| **C: Ei edgea** | alle -2 % | Pysähdy, tutki bugit, älä pelaa |

Useimmat ML-vedonlyöntiprojektit eivät pääse tähän vaiheeseen
positiivisella lopputuloksella — rehellinen näkymä, ei pessimismiä.

---

## Vaihe 6 (vain jos edge todistettu): Pelaaminen pienillä rahoilla

- Streamlit-dashboard päivän lähdöistä
- Manuaalinen pelaaminen 1–5 € panoksiin
- 4–8 viikkoa CLV-seurantaa oikealla rahalla
- 200–300+ peliä tilastollisesti merkittävään lopputulokseen
- Korjattava ennen V6: `correlated_kelly_adjust` (ks. KNOWN_ISSUES.md #7)

---

## Vaihe 7 (vain jos pelaaminen tuottavaa): Skaalaus

- Betfair Exchange -integraatio (tutki likviditeetti ensin)
- Persistentit job-storet (`SQLAlchemyJobStore`) scheduler-restartteja varten
- Sharp-markkinakertoimet (Pinnacle/Betfair) CLV-vertailuun — skeema valmis
- Telegram/email-alert kun value-peli löytyy

---

## Pitkän tähtäimen visio

- **Pace-piirteet:** position_at_800m ei saada ATG:n eikä Travsportin API:sta
  nykymuodossaan. Vaatii erillisen tutkimuksen tai web-scrapingin.
- **Sää-integraatio:** Open-Meteo — rata × sade × hevosen rata-kokemus
- **Conditional logit / Plackett-Luce** trifecta-todennäköisyyksille
- **Postgres** jos DB kasvaa yli 500 MB
- **Sukutaulupiirteet** (isä/emänisä kerätty — `horses.sire` + `horses.dam_sire` 88 % notna; tilastoanalyysi tulevaisuudessa)
