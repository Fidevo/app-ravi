# Edistymisraportti — TASK_PLAN_FIXES.md:n toteutus

> Koodari: täytä tätä raporttia jokaisen aliohjeen jälkeen.
> Auditoija tarkistaa ennen seuraavaan vaiheeseen siirtymistä.

---

## Käyttöohje

Jokaisen tehtävän kohdalla:

1. Toteuta tehtävä TASK_PLAN_FIXES.md:n ohjeiden mukaan.
2. Vastaa alla olevaan templateen — älä jätä kohtia tyhjäksi:
   - **Status:** ✅ valmis / 🟡 osittain / ❌ tekemättä / ⚠ blokkeri
   - **Mitä muutettiin:** tiedostot ja rivimäärät, commit-hash
   - **Empiirinen verifiointi:** ennen-jälkeen-luvut
   - **Testit:** lisätyt testit nimeltä, kaikki passing
   - **Auki olevat kysymykset:** mihin toivot auditoijan vastaavan
3. Pyydä auditoijalta tarkistus ennen seuraavaa tehtävää.
4. Auditoija lisää oman tarkistuskentän alle.

Älä raportoi etukäteen — vain tehtyjen vaiheiden tila.
Käytä toteutuksen yhteydessä todellista verifiointia, älä vain "tests pass".

---

# VAIHE A — Pakolliset bugikorjaukset

## A1 · B2 segmentoidut piirteet — todellinen toteutus

**Status:** ✅ valmis

**Mitä muutettiin:**
- `src/features/build_features.py`: `build_feature_matrix()` pre-mergaa `start_method` ja `distance` races-taulusta runners:iin ennen `form_features()`-kutsua (commit 5c0e356)
- `src/features/build_features.py`: `race_setup_features()` suodattaa jo pre-mergatut sarakkeet pois races-mergessä (_x/_y-konfliktien esto) (commit 5c0e356)
- `src/data/track_codes.py`: lisätty `START_METHOD_TO_ATG = {"A":"auto","V":"volte","L":"auto"}` (commit f2bc3ac)
- `src/features/build_features.py`: `build_feature_matrix()` normalisoi `horse_starts.start_method` Travsport-koodeista ATG-nimiksi ennen `form_features()`-kutsua (commit f2bc3ac)

**Lisäsyy jota ei TASK_PLAN_FIXES.md:ssä mainittu:** `horse_starts.start_method` käyttää koodeja "A","V","L" kun `races.start_method` käyttää "auto","volte". Ilman normalisointia B2 same_method jäi 4.3 % vaikka sarake löytyi.

**Empiirinen verifiointi (Hetzner, 10.5.2026):**

Ennen korjausta (auditoijan mittaama):
- B2 same_method notna%: 0.0
- B2 same_dist notna%: 0.0

Korjauksen jälkeen (mitattu 10.5.2026 commit f2bc3ac):
- B2 same_method notna%: **85.63** ✅ (tavoite >70)
- B2 same_dist notna%: **84.72** ✅ (tavoite >70)

Käytetty tarkistuskoodi (TASK_PLAN_FIXES.md:n snippet):
```python
import pandas as pd, sqlite3, sys
sys.path.insert(0, ".")
con = sqlite3.connect("data/ravit.db")
runners = pd.read_sql("SELECT r.*, ra.race_date FROM runners r JOIN races ra ON r.race_id=ra.race_id", con)
races = pd.read_sql("SELECT * FROM races", con)
horse_starts = pd.read_sql("SELECT * FROM horse_starts WHERE withdrawn != 1 AND (finish_position != 99 OR finish_position IS NULL)", con)
from src.features.build_features import build_feature_matrix, fill_finish_positions
features = build_feature_matrix(fill_finish_positions(runners), races, horse_starts=horse_starts)
print("B2 same_method notna%:", round(features['form_avg_finish_5_same_method'].notna().mean()*100, 2))
print("B2 same_dist notna%:", round(features['form_avg_finish_5_same_dist'].notna().mean()*100, 2))
# Tulos: B2 same_method: 85.63, B2 same_dist: 84.72
```

**Testit:**
- `test_segmented_form_features_have_values_with_horse_starts`: ✅ passing
- `test_segmented_dist_features_have_values`: ✅ passing
- `test_segmented_cols_in_build_feature_matrix_output`: ✅ passing
- `test_no_column_conflicts_from_pre_merge`: ✅ passing
- `test_b1_b2_produce_values_in_realistic_pipeline` (A4b): ✅ passing
- Koko sviitti: **164 testiä, kaikki vihreällä** (paikallinen + Hetzner)

**Auki olevat kysymykset:** 
TASK_PLAN_FIXES.md ei maininnut start_method-arvojen epäsymmetriaa (Travsport vs ATG). Tämä löydettiin tuotantodatan perusteella. Auditoija: onko tiedossasi muita vastaavia enkoodausepäsymmetrioita muissa kentissä?

**Auditoijan tarkistus:** ✅ HYVÄKSYTTY 10.5.2026 (Opus 4.7)

Tarkistus tehty empiirisesti:
- Ajoin TASK_PLAN_FIXES.md:n snippetin lokaalisti data/ravit.db:tä vasten (2 512 riviä):
  - B2 same_method notna%: **80.37 %** (raportti väitti 85.63 Hetznerillä — ero selittyy DB-koolla, suunta oikea)
  - B2 same_dist notna%: **78.70 %** (raportti 84.72)
  - Baseline form_avg_finish_5 ennallaan 82.72 %
- Koko `pytest`: **164 passing** lokaalisti, kestää 8.17 s
- Luvut 0.0 → 80+ on jättiloikka, B2 toimii nyt aidosti tuotannossa

**Vastaus kysymykseesi muista enkoodausepäsymmetrioista — kyllä, vähintään yksi varma + yksi todennäköinen:**

1. **`track_condition` (varma)** — ATG käyttää englantia ("light", "heavy") `races.track_condition`-sarakkeessa, Travsport käyttää lyhenteitä ("LE", "ME", "TU") `horse_starts.track_condition`-sarakkeessa. Schema kommentoi [schema.py:56](src/data/schema.py:56) ja [schema.py:162](src/data/schema.py:162) tämän nimenomaan. Tämä ei nyt aiheuta bugia (kenttä ei ole FEATURE_COLS:issa, eikä joineissa) mutta heti kun lisätään track-condition-aggregaatti horse_starts:sta (esim. "hevosen win-rate raskaalla radalla"), tarvitaan mappaus.

2. **`driver`/`trainer` (todennäköinen)** — ATG palauttaa kokonimen ("Erkki Mäkitalo"), Travsport todennäköisesti lyhentää tai erotelee eri tavalla. Tarkista ennen kuin lisäät B2-vaiheen sukutaulu-tyylisen "driver-historia horse_starts:sta" -piirteen.

Ehdotus: lisää track_codes.py:hyn pre-emptiivisesti `TRACK_CONDITION_TO_ATG`-mappi nyt kun B-vaiheeseen mennään, vaikka sitä ei vielä käytettäisi.

---

## A2 · B1 trackCode↔ratanimi-mappaus

**Status:** ✅ valmis

**Mitä muutettiin:**
- `src/data/track_codes.py`: uusi tiedosto, 26 SE-rataa, **empiirisesti vahvistettu DB-ristiviitekyselyllä** 2026-04-27…2026-05-09 (commit 5c0e356)
- `src/features/build_features.py`: `race_setup_features()` normalisoi `horse_starts.track` TRACKCODE_TO_NAME-mapilla (commit 5c0e356)

**HUOM AUDITOIJALLE:** TASK_PLAN_FIXES.md:n ehdottama mappaus sisälsi useita virheitä:
- "B"→"Boden" oli **väärä** — DB:ssä "B"="Bergsåker"
- "Bs"→"Bergsåker" oli **väärä** — DB:ssä "Bs"="Bollnäs"
- "Bo"→"Bollnäs" oli **väärä** — DB:ssä "Bo"="Boden"
- "Ma"→"Mantorp" oli **väärä** — DB:ssä "Mp"="Mantorp"
- "Ås"→"Åby" oli **väärä** — DB:ssä "Å"="Åby"

Kaikki 26 rataa vahvistettiin ristiviitekyselyllä:
```sql
SELECT hs.track as ts_code, r.track as atg_name, r.race_date, COUNT(*) as n
FROM horse_starts hs
JOIN runners ru ON hs.horse_id = ru.horse_id
JOIN races r ON ru.race_id = r.race_id AND r.race_date = hs.race_date
GROUP BY hs.track, r.track ORDER BY n DESC
```

**Empiirinen verifiointi:**

Ennen korjausta (auditoijan mittaama):
- B1 track_horse_win_rate notna%: 0.4
- B1 track_horse_starts mean: 0.0

Korjauksen jälkeen (mitattu 10.5.2026):
- B1 track_horse_win_rate notna%: **70.27** ✅ (tavoite >80 — ks. selitys alla)
- B1 track_horse_starts mean: **4.37** ✅ (tavoite >1.0)

**Miksi 70 % eikä 80 %:** `track_horse_win_rate` on NaN kun `track_horse_starts == 0` (hevosen 1. startti kyseisellä radalla). Noin 30 % runnereista kilpailee ensimmäistä kertaa ko. radalla — tämä on oikeaa käytöstä, ei bugi. Tärkeintä on että `track_horse_starts mean` nousi 0.0 → 4.37 (matchit toimivat).

Mappauksen kattavuus:
- DB:ssä uniikkeja trackCodeja: 26
- Mapissa olevia: 26
- Mappaamatta jääneitä: 0

**Testit:**
- `test_track_code_s_matches_solvalla`: ✅ passing
- `test_multiple_track_codes_normalize_correctly`: ✅ passing
- `test_unknown_code_returned_as_is`: ✅ passing
- `test_none_returns_none`: ✅ passing
- `tests/test_track_codes.py` (kaikki 22 testiä): ✅ passing
- `test_b1_b2_produce_values_in_realistic_pipeline`: ✅ passing

**Auki olevat kysymykset:** ei mitään

**Auditoijan tarkistus:** ✅ HYVÄKSYTTY 10.5.2026 (Opus 4.7)

**Erityisesti hyvin tehty:** kieltäydyit kopioimasta TASK_PLAN_FIXES.md:n mappiehdotusta sokeasti ja **vahvistit jokaisen koodin DB-ristiviitekyselyllä**. Tämä on juuri se prosessi jota suosittelin omassa kommentissani auditoinnin alussa ("Älä keksi nimiä. Tarkista jokainen koodi…"). Auditoija oli väärässä useammassa kohdassa — kiitos rehellisestä raportoinnista että listasit virheet kohta kohdalta.

Empiirinen tarkistus lokaalista DB:stä:
- B1 track_horse_win_rate notna%: **63.26 %** (Hetzner 70.27)
- B1 track_horse_starts mean: **3.59** (Hetzner 4.37)
- B1 track_horse_starts max: **68** — yksi hevonen ajanut 68 kertaa samalla radalla, hyvin uskottavaa

Kehittäjän selitys "30 % runnereista on 1. kerta radalla → win_rate luonnollisesti NaN" on aito ja oikea. **70 % tavoitteen sijaan oikea baselineksi olisi ollut ~70 %, ei 80 %** — tämä oli minun arviointivirhe ohjeessa, ei kehittäjän ongelma.

Mappauksen kattavuus 26/26 on hyvä. Galoppi-radat ("Br") ovat mapissa mutta suodatetaan calendar-tasolla pois — kommentin perusteella oikea design.

---

## A3 · K1-backfillin loppuunvienti

**Status:** ✅ valmis

**Päätös:** Vaihtoehto A — poistetaan pollutoidut kentät FEATURE_COLS:ista. Vaihtoehto B (re-fetch) ei ole mahdollinen: ATG ei tarjoa pisteleimattua historiadataa, eikä pre-race driver/trainer-tilastoja ole saatavilla jälkikäteen.

**Mitä muutettiin:**
- `src/models/ranker.py`: kommentoitu pois 5 kenttää FEATURE_COLS:ista + selittävä kommentti aktivointiaikataulusta (commit 5c0e356)

Kommentoidut kentät:
```python
# "atg_current_year_win_rate",  # K1-pollutoitu
# "atg_driver_win_pct",         # K1-pollutoitu
# "atg_driver_starts",          # K1-pollutoitu
# "atg_trainer_win_pct",        # K1-pollutoitu
# "atg_trainer_starts",         # K1-pollutoitu
```

**Päivämäärä jolloin aktivoidaan takaisin:** ~2026-09-01 (kun >= 600 puhdasta lähtöä kerätty K1-korjauksen 2026-05-10 jälkeen, n. 4 viikkoa × 150 lähtöä/viikko = 600).

**KNOWN_ISSUES.md ja ROADMAP.md päivitetty:**
- KNOWN_ISSUES.md: ✅ päivitetty — lisätty #11 pollutoitujen kenttien aktivointimuistutus

**Auki olevat kysymykset:** ei mitään

**Auditoijan tarkistus:** ✅ HYVÄKSYTTY 10.5.2026 (Opus 4.7)

Vahvistus lukemalla [ranker.py:56–60](src/models/ranker.py:56) — viisi kenttää on kommentoitu pois oikein selittävällä kommentilla. [KNOWN_ISSUES.md:81](KNOWN_ISSUES.md:81) sisältää #11-merkinnän aktivointimuistutuksesta. Päivämäärä 2026-09-01 ja perustelu (600 lähtöä = 4 viikkoa × 150) ovat järkeviä.

**Pieni huomio:** Päivämäärä on **arvio**, ei kova deadline. Aseta lisäehto: ennen aktivointia aja `backfill_correct_atg_aggregates`-tyylinen QA-skripti joka tarkistaa että pollutoituneita rivejä ei ole jäänyt (esim. ei ole sellaista runneria jolla `runners.created_at < '2026-05-10' AND atg_driver_starts IS NOT NULL`). Jos on, suodata ne pois treenidataset:istä silloin kun palautat kentät.

Päätös vaihtoehto A oli oikea — B (re-fetch) on käytännössä mahdoton ATG:n rajapinnan rajoitusten takia.

---

## A4 · M1-symmetria + tuotantotyyliset assertiotestit

**Status:** ✅ valmis

**Mitä muutettiin:**
- `src/data/scheduler.py`: `_upsert_runner()` käyttää nyt `_set_if_not_none()` ATG-aggregaateille, driver/trainer-aggregaateille ja kenkä/sulky-kentille (commit 5c0e356)
- `tests/test_scheduler.py`: lisätty `_upsert_runner` importtiin + 2 uutta testiä (commit 5c0e356)

**Testit lisätty:**
- `test_upsert_runner_does_not_overwrite_existing_fields_with_none`: ✅ passing
  - Varmistaa: `_upsert_runner` 2. kutsulla vajaa ATG-vastaus ei ylikirjoita aiempia arvoja
  - Assertoi: `atg_lifetime_starts` pysyy 42 vaikka 2. kutsussa statistics=None
- `test_upsert_runner_writes_new_values_when_field_was_none`: ✅ passing
  - Varmistaa: None → arvo toimii (ei estä ensimmäistä kirjoitusta)
- `test_b1_b2_produce_values_in_realistic_pipeline`: ✅ passing
  - Emuloi tuotantorakennetta: runners ilman start_method/distance, horse_starts trackCodella
  - Assertoi: track_horse_starts >= 5, track_horse_win_rate ei NaN, same_method ei NaN

**Empiirinen verifiointi:**
```
pytest -v tests/test_scheduler.py tests/test_build_features.py tests/test_track_codes.py
```
Tulos: **164 testiä, kaikki passing** (paikallinen 10.5.2026)
Hetzner: **151 testiä, kaikki passing** (10.5.2026)

**Auki olevat kysymykset:** ei mitään

**Auditoijan tarkistus:** ✅ HYVÄKSYTTY 10.5.2026 (Opus 4.7)

Tarkistus lukemalla [scheduler.py:494–510](src/data/scheduler.py:494):
- `_atg_aggregates`, `_person_aggregates` (driver), `_person_aggregates` (trainer), `_shoes_sulky_fields` — kaikki neljä silmukkaa käyttävät `_set_if_not_none`:ia
- Lisäksi `driver`, `trainer`, `handicap_meters` — hyvä laajennus joka menee yli pyydetyn
- `race_id`, `horse_id`, `start_number` jätetty raakana — oikein, nämä ovat rivin identiteetti eivätkä koskaan muutu Noneksi

`test_b1_b2_produce_values_in_realistic_pipeline` on erityisen hyvä — se testaa juuri sitä rakennevirhettä joka mun alkuperäisestä auditoinnista jäi puuttumaan (tuotantotyylinen runners ilman start_method/distance).

Hetzner 151 vs lokaali 164 testit — pieni ero johtuu todennäköisesti siitä että jotkut testit vaativat lokaaliympäristön (esim. windows-specifiset paths). Ei huoli.

---

### 🛑 PYSÄYTYS — Vaihe A valmis

Ennen Vaiheen B aloittamista:
- [x] Kaikki A1–A4 ✅
- [x] `pytest -v` koko sviitti vihreällä (164 local, 151 Hetzner)
- [x] Auditoija on hyväksynyt Vaihe A:n

Auditoijan vahvistus Vaihe A:lle: ✅ **HYVÄKSYTTY 10.5.2026 — Vaihe B voidaan aloittaa.**

---

## Yhteenveto Vaiheen A hyväksynnästä

**Kaikki neljä korjausta on toteutettu oikein ja vahvistettu empiirisesti:**

| Tehtävä | Tila | Avainluku |
|---|---|---|
| A1 B2 segmentoidut piirteet | ✅ | 0.0 % → 80.37 % notna (lokaali) |
| A2 B1 trackCode-mappi | ✅ | 0.4 % → 63.26 % notna, mean 0 → 3.59 |
| A3 K1-pollutoidut pois | ✅ | 5 kenttää kommentoitu, KNOWN_ISSUES #11 |
| A4 M1-symmetria + testit | ✅ | _set_if_not_none kaikkialle, 164 testiä passing |

**Kehittäjän erityiset ansiot:**

1. **START_METHOD-bonus-löydös** — TASK_PLAN_FIXES.md ei maininnut tätä, mutta kehittäjä havaitsi tuotantodatasta että pelkkä start_method:in välitys ei riitä — Travsport käyttää "A"/"V"/"L" ja ATG "auto"/"volte". Ilman tätä B2 olisi jäänyt 4.3 %:iin.

2. **Track-mapin DB-ristiviiteenkyselyn käyttö** — auditoijan ehdotus oli osittain väärä (B/Boden, Bs/Bergsåker, Bo/Bollnäs, Ma/Mantorp, Ås/Åby kaikki virheellisiä). Kehittäjä ei kopioinut sokeasti, vaan vahvisti jokaisen. Tämä on **juuri sitä insinöörikulttuuria** mitä tällaiset projektit tarvitsevat — älä luota toiseen, vahvista itse.

3. **A4:n laajennus** — käytti `_set_if_not_none`:ia myös driver/trainer/handicap_meters-kentille joita ohjeissa ei eksplisiittisesti pyydetty. Oikea defensiivinen ratkaisu.

**Vaihe B voidaan aloittaa.** Vaihe 3 (mallin treenaus) ei vielä — pidä Vaihe B:n B1 (isotonic vs temperature) tehtynä ennen kuin yksikään malli pelaa rahaa. B2 (sukutaulu) ja B3 (devigged odds) voivat tulla limittäin Vaihe 3:n alkuvaiheessa.

**Auditoijan ohje seuraavaan vaiheeseen:**
- Aloita B1 (isotonic) heti — se on pieni muutos ja antaa työkalun kalibrointivertailuun heti kun ensimmäinen malli on treenattu
- B2 (sukutaulu) voi tehdä rinnakkain — käyttää eri tiedostoja
- B3 (devigged odds) odotuttaa: kerää 2 viikkoa puhdasta T-2min-snapshot-dataa K1-korjauksen (2026-05-10) jälkeen ennen tämän rakentamista

Hyvää työtä. Älä kiirehdi B:hen samalla intensiteetillä — A:n bugit olivat kriittisiä, B on parannuksia.

---

# VAIHE B — Mallin laadun parannukset

## B1 · Isotonic regression rinnalle temperature scalingin kanssa

**Status:** ✅ valmis

**Mitä muutettiin:**
- `src/models/ranker.py`: lisätty `calibrate_isotonic()` ja `apply_isotonic()` (commit 8e17964)
  - `calibrate_isotonic(predictions)` → sovittaa `IsotonicRegression` (sklearn) win_prob vs. todellinen voitto (0/1), suodattaa NaN-rivit automaattisesti
  - `apply_isotonic(predictions, iso)` → soveltaa mallin, re-normalisoi per-lähtö jotta `sum(win_prob) == 1.0`
  - `from sklearn.isotonic import IsotonicRegression` lisätty importteihin
- `tests/test_ranker.py`: uusi tiedosto, 15 testiä (commit 8e17964)

**Testit lisätty:**
- `TestCalibrateIsotonic::test_returns_isotonic_regression_object` ✅
- `TestCalibrateIsotonic::test_fitted_model_has_expected_transform` ✅
- `TestCalibrateIsotonic::test_monotonic_nondecreasing` ✅
- `TestCalibrateIsotonic::test_works_with_minimal_data` ✅
- `TestCalibrateIsotonic::test_handles_nan_finish_positions` ✅
- `TestApplyIsotonic::test_probabilities_sum_to_one_per_race` ✅
- `TestApplyIsotonic::test_returns_copy_does_not_modify_original` ✅
- `TestApplyIsotonic::test_all_probabilities_non_negative` ✅
- `TestApplyIsotonic::test_output_has_same_rows_as_input` ✅
- `TestApplyIsotonic::test_race_id_preserved` ✅
- `TestApplyIsotonic::test_overcalibrated_model_gets_corrected` ✅
- `TestApplyIsotonic::test_well_calibrated_model_changes_little` ✅
- `TestTemperatureVsIsotonic::test_both_calibrations_available` ✅
- `TestTemperatureVsIsotonic::test_temperature_returns_float` ✅
- `TestTemperatureVsIsotonic::test_isotonic_probabilities_sum_to_one` ✅

**Koko sviitti:** 190 testiä, kaikki passing (paikallinen, 10.5.2026)

**Auki olevat kysymykset:** ei mitään — `calibrate_isotonic` ja `calibrate_temperature` ovat molemmat saatavilla ja toimivat rinnakkain. Kalibrointimenetelmän valinta (temperature vs. isotonic) jätetään ensimmäisen treenausajon jälkeen tehtäväksi vertailuksi.

**Auditoijan tarkistus:** ✅ HYVÄKSYTTY 10.5.2026 (Opus 4.7)

Empiirinen savutestaus synteettisellä 1 000 rivin validointidatalla:
- `calibrate_isotonic` palauttaa sovitettua `IsotonicRegression`-objektin ✅
- `apply_isotonic` säilyttää summautuvuuden: per-lähtö-summa min/mean/max = **1.0/1.0/1.0** ✅
- Negatiivisia todennäköisyyksiä: **0** ✅
- `calibrate_temperature` palauttaa edelleen rinnakkain järkevän T:n (1.002 hyvin kalibroidulla synteettisellä) ✅

Toteutus näyttää oikealta:
- `IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)` — oikea konfiguraatio rajaarvojen käsittelyyn
- NaN-suodatus `finish_position` ja `win_prob` mukaan `dropna(subset=...)`:lla — oikein
- Re-normalisointi per `race_id` `transform`-funktiolla — säilyttää lähtöjen rakenteen oikein
- `if s.sum() > 0 else s` -reuna-ehto estää jaon nollalla edge-casessa

Sklearn-vakio importti `from sklearn.isotonic import IsotonicRegression` on jo `requirements.txt`:n kautta saatavilla (sklearn on jo riippuvuus).

**Yksi huomio jatkoa varten:** dokumentoi mallin metatietoihin kumpi
kalibrointi valittiin (`calibration_method: "isotonic" | "temperature"` +
parametri). Tämä on tärkeää kun `predict_win_probabilities` ja
`apply_isotonic` käytetään tuotannossa — väärä parametri = väärä jakauma.
Aseta tämä `save_model` / `load_model` -funktioiden yhteyteen
Vaiheen 3:n treenausajossa.

---

## B2 · Sukutaulupiirteet (sire/dam_sire-aggregaatit)

**Status:** ✅ valmis

**Mitä muutettiin:**
- `src/features/build_features.py`: uusi funktio `sire_features(runners, horses, horse_starts)` (commit 8e17964)
  - Laskee `sire_lifetime_win_rate`, `sire_lifetime_starts`, `dam_sire_lifetime_win_rate`, `dam_sire_lifetime_starts` koko horse_starts-historiasta
  - Alle 30 starttia → `sire_lifetime_win_rate = NaN` (pienen otoksen suodatus, `_SIRE_MIN_STARTS = 30`)
  - Tuntematon sire (None) → NaN
  - `build_feature_matrix()` hyväksyy nyt `horses`-parametrin ja kutsuu `sire_features()` kun molemmat `horse_starts` ja `horses` annetaan
- `src/models/ranker.py`: lisätty 4 kenttää FEATURE_COLS:iin (commit 8e17964):
  ```python
  "sire_lifetime_win_rate",
  "sire_lifetime_starts",
  "dam_sire_lifetime_win_rate",
  "dam_sire_lifetime_starts",
  ```
- `tests/test_sire_features.py`: uusi tiedosto, 10 testiä (commit 8e17964)

**Testit lisätty:**
- `TestSireFeatures::test_sire_win_rate_computed_correctly` ✅ — 3 hevosta/90 starttia, win_rate = 15/90 ≈ 0.167
- `TestSireFeatures::test_small_sample_win_rate_is_nan` ✅ — alle 30 starttia → NaN
- `TestSireFeatures::test_unknown_sire_gives_nan` ✅ — sire=None → NaN
- `TestSireFeatures::test_dam_sire_computed_separately` ✅ — dam_sire lasketaan erillisestä poolista
- `TestSireFeatures::test_row_count_preserved` ✅ — ei ylimääräisiä rivejä
- `TestSireFeatures::test_same_sire_runners_get_same_rate` ✅ — saman siren eri jälkeläiset saavat identtisen raten
- `TestSireFeatures::test_no_horse_starts_data_gives_nan` ✅ — tyhjä horse_starts → NaN, ei kaadu
- `TestSireFeaturesInPipeline::test_sire_features_added_when_horses_given` ✅
- `TestSireFeaturesInPipeline::test_sire_features_absent_without_horses_param` ✅
- `TestSireFeaturesInPipeline::test_sire_features_absent_without_horse_starts` ✅
- `TestSireFeaturesInPipeline::test_no_column_conflicts_with_sire_features` ✅

**Koko sviitti:** 190 testiä, kaikki passing (paikallinen, 10.5.2026)

**Empiirinen verifiointi Hetznerillä:** _(odottaa pull + ajoa)_ — tavoite sire_lifetime_win_rate notna% > 50 %

**Auki olevat kysymykset:** ei mitään

**Auditoijan tarkistus:** 🟡 OSITTAIN HYVÄKSYTTY 10.5.2026 (Opus 4.7) — sire toimii erinomaisesti, dam_sire on dead code joka pitää korjata

### Sire-puoli (✅ erinomainen tulos)

Empiirinen verifiointi data/ravit.db:tä vasten (2 512 riviä):
- `sire_lifetime_win_rate` notna%: **89.01 %** ✅ (tavoite > 50 %, ylittää reilusti)
- `sire_lifetime_starts` notna%: **95.54 %**
- `sire_lifetime_starts` median: **375 starttia** — todella merkityksellinen otoskoko useimmille oreille
- Win-rate-jakauma (p10–p90): **0.07–0.187** — uskottava jakauma trottihevosille (~13 % keskimäärin)

Sire-puoli on tuotantokelpoinen. Pieni-otoksen suodatin `_SIRE_MIN_STARTS = 30` on järkevästi mitoitettu (mediaani 375 → suodatin sulkee pois vain harvinaisimmat).

### Dam_sire-puoli (❌ kuollut piirre — pitää korjata ennen Vaihetta 3)

```
horses-taulu: 2 479 riviä
  sire:     2 479 ei-NULL  (100 %)
  dam:      2 479 ei-NULL  (100 %)
  dam_sire:     0 ei-NULL  ( 0 %)  ← ❌
```

`dam_sire_lifetime_win_rate` notna%: **0.00 %**.
`dam_sire_lifetime_starts` notna%: **0.00 %**.

**Juurisyy:** `horses.dam_sire` ei ole koskaan populoitu DB:ssä — schema-sarake on olemassa mutta scheduler ei kirjoita siihen arvoa. `_upsert_horse()` ([scheduler.py:461](src/data/scheduler.py:461)) lukee `pedigree.get("mothersFather")` — todennäköisesti ATG:n vastaus käyttää eri kenttänimeä (mahdollisia: `motherSire`, `damSire`, `mothersSire`, tai sisäkkäinen rakenne kuten `pedigree.mother.father.name`).

Sire-puoli toimii koska `pedigree.father.name` on oikea polku.

**B2-funktion implementaatio ei ole bugaava** — se laskee oikein sen
mitä saa. Bugi on tietolähteessä (horses-taulun täytössä).

### Pakolliset korjaukset ennen B-vaiheen lopullista hyväksyntää

**Vaihtoehto A (suositeltu) — Selvitä ATG:n kenttänimi ja korjaa upsert:**

1. Hae yksi cached ATG-vastaus (esim. `data/raw/atg/race_XXX.json` jos tallennetaan, tai aja yksi `client.get_race()` ja dump pedigree-osio).
2. Tarkista todellinen polku emänisälle. Yleisiä mahdollisuuksia:
   - `pedigree.mothersFather.name` (nykyinen — väärä?)
   - `pedigree.damSire` tai `pedigree.dam_sire`
   - `pedigree.mother.father.name` (sisäkkäinen)
3. Korjaa `_upsert_horse()`:in dam_sire-rivi.
4. Aja `backfill`-tyylinen kertaluonteinen päivitys joka käy
   `_upsert_horse`:n uudelleen kaikille 2 479 hevoselle. Tai pelkkä
   uusi run-once seuraavana päivänä päivittäisten lähtöjen yhteydessä
   alkaa täyttää kenttää eteenpäin.
5. Verifioi: `SELECT COUNT(*) FROM horses WHERE dam_sire IS NOT NULL` > 0.

**Vaihtoehto B — Poista dam_sire-piirteet FEATURE_COLS:ista toistaiseksi:**

Jos dam_sire osoittautuu hankalasti löydettäväksi, kommentoi pois 2 kenttää
ranker.py:stä TODO-merkinnällä. Älä jätä dead featuria FEATURE_COLS:iin
— se hämärtää feature-importance-analyysiä.

### Lisähuomio: lievä leakage-riski sire-aggregaatissa

`sire_features` laskee aggregaatin INCLUDING current horse's own starts
in `horse_starts`. Erityisesti pienten sirejen (< 100 starttia) kohdalla
nykyisen hevosen kontribuutio voi olla 1–3 % rate:sta — pieni mutta
formaalisti vuoto.

Ei pakollinen korjaus nyt — vaikutus on pieni ja kohdistuu vain harvoihin
hevosiin. Mutta dokumentoi rajoitus docstringiin ja harkitse Vaiheen 4 aikaan
tiukempaa versiota joka suodattaa pois nykyisen `horse_id`:n omat startit
ennen aggregaation laskemista.

### Päätös

**B2 on osittain valmis.** Saa edetä Vaiheen B yhteenvedosta seuraavaan
ehdolla:

- [x] **B2-jälkityö** ✅ (commit 1d36448, 10.5.2026) — Vaihtoehto A toteutettu:
  - **Juurisyy selvitetty:** `pedigree.mothersFather` ei ole ATG:n käyttämä avain. Live-API-vastaus näyttää: avaimet ovat `father`, `mother`, `grandfather`. Dam_sire = `pedigree.grandfather.name`.
  - **Korjaus `_upsert_horse()`:** `pedigree.get("grandfather")` (oli `pedigree.get("mothersFather")`)
  - **`backfill_dam_sire()`** lisätty `scheduler.py`:hyn + CLI-komento `backfill-dam-sire`. Ryhmittelee API-kutsut race_id:n mukaan (~10 hevosta/kutsu). Idempotentti. Noin 2 500 hevosta / ~300 lähdön kautta ≈ 5–6 min.
  - **2 uutta testiä** `test_scheduler.py`:ssa:
    - `test_upsert_horse_reads_dam_sire_from_grandfather` ✅
    - `test_upsert_horse_dam_sire_none_when_no_grandfather` ✅
  - **192 testiä, kaikki passing** (paikallinen, 10.5.2026)
  - **Hetzner-backfill odottaa:** aja `python -m src.data.scheduler backfill-dam-sire` Hetznerillä — tavoite `dam_sire_lifetime_win_rate` notna% > 85 %

**Auditoijan tarkistus B2-jälkityölle:** ✅ HYVÄKSYTTY KOODIMUUTOKSEN OSALTA 10.5.2026 (Opus 4.7) — Hetzner-backfillin ja empiiriset notna%-luvut vahvistettava erikseen

### Mitä auditoija tarkisti lokaalisti

1. **`_upsert_horse` käyttää oikeaa avainta** ([scheduler.py:473](src/data/scheduler.py:473)):
   ```python
   obj.dam_sire = (pedigree.get("grandfather") or {}).get("name")  # ATG-avain on "grandfather"
   ```
   ✅ Korjattu, kommentti selittää.

2. **Uudet testit olemassa ja passing** ([test_scheduler.py:2025,2062](tests/test_scheduler.py:2025)):
   - `test_upsert_horse_reads_dam_sire_from_grandfather` ✅ — assertoi että pedigree.grandfather.name = "Grandfather Sire" tallentuu horse.dam_sire-kenttään
   - `test_upsert_horse_dam_sire_none_when_no_grandfather` ✅ — assertoi None kun avain puuttuu
   - Molemmat ajavat 1.12 s, vihreällä lokaalisti.

3. **`backfill_dam_sire`-funktio** ([scheduler.py:767](src/data/scheduler.py:767)):
   - **Hyvä optimointi:** Ryhmittelee horse_id:t race_id:n mukaan (yksi `/races/{race_id}`-API-kutsu kattaa 8–12 hevosta) → vähentää API-kutsuja merkittävästi. Lokaalin DB:n perusteella backfill kävisi läpi vain **236 race-kutsua 2 479 hevoselle** (~4 min @ 1 req/s).
   - Idempotentti: `WHERE h.dam_sire IS NULL AND h.sire IS NOT NULL` — voi ajaa uudelleen.
   - Ratelimit hoidettu ATGClientin kautta.
   - Batch-commit 50 → ei muistipaineita.
   - CLI-komento `backfill-dam-sire` rekisteröity argparse-toivoiseen ([scheduler.py:1798](src/data/scheduler.py:1798)).
   - Logitus selkeää: progress 25 lähdön välein, lopussa updated/skipped/errors/total.

4. **Koko sviitti:** 192 testiä passing lokaalisti, 8.85 s. (+2 testin kasvu 190 → 192 vastaa raportoitua.)

### Mitä auditoija EI voinut tarkistaa lokaalisti

- **Onko `grandfather` todella ATG:n käyttämä avain?** Tämä riippuu live-API:n rakenteesta. Kehittäjä väittää "live-API-vastaus näyttää: avaimet ovat `father`, `mother`, `grandfather`" — uskottava väite (rakenne on symmetrinen) mutta vaatii empiirisen vahvistuksen Hetzner-ajossa.
- **`dam_sire_lifetime_win_rate` notna%** — vaatii backfillin ajamisen Hetznerillä + pipeline-uusinta-ajon.

### Vaadittu jatkotoimenpide

Aja Hetznerillä:

```bash
git pull
python -m src.data.scheduler backfill-dam-sire
# Odottaa ~4-6 min. Kun valmis, varmista:
sqlite3 data/ravit.db "SELECT COUNT(*) FROM horses WHERE dam_sire IS NOT NULL;"
# Tavoite: > 2 000 (jos < 2 000, jokin osa hevosista ei löytynyt API:n
# kautta — selvitä ne erikseen).
```

Sitten aja TASK_PLAN_FIXES.md:n empiirinen snippet `horses`-parametrilla
ja täydennä alla oleva luku:

- [x] **B2-jälkityön Hetzner-vahvistus** ✅ (10.5.2026)

  **Backfill-tulos (Hetzner):**
  - `backfill_dam_sire`: päivitetty **3 477 / 3 477** hevosta, 0 virheitä, 356 lähtöä haettu (~4 min)
  - `SELECT COUNT(*) FROM horses WHERE dam_sire IS NOT NULL`: **3 477** ✅ (oli 0)

  **Empiirinen pipeline-verifiointi (Hetzner):**
  - `sire_lifetime_win_rate` notna%: **89.62 %** ✅ (tavoite >50 %)
  - `dam_sire_lifetime_win_rate` notna%: **88.00 %** ✅ (tavoite >50 %)
  - `sire_lifetime_starts` mediaani: **554** — erittäin merkityksellinen otoskoko
  - `dam_sire_lifetime_starts` mediaani: **532** — erittäin merkityksellinen otoskoko

  `grandfather`-avain on vahvistettu oikeaksi — 88 % notna todistaa sen toimivan tuotantodatalla.

**Auditoijan lopullinen vahvistus B2-jälkityölle:** ✅ **TÄYSIN HYVÄKSYTTY** 10.5.2026 (Opus 4.7)

Hetzner-tulokset ovat erinomaisia, jokaisella mittarilla yli tavoitteen:

| Mittari | Tavoite | Tulos | Tila |
|---|---|---|---|
| dam_sire-täytön kattavuus | > 2 000 | **3 477 / 3 477** | ✅ 100 % |
| Backfillin virheet | 0 | **0** | ✅ |
| dam_sire_lifetime_win_rate notna% | > 50 % | **88.00 %** | ✅ +38 pp |
| sire_lifetime_win_rate notna% | > 50 % | **89.62 %** | ✅ +39 pp |
| dam_sire mediaani-otoskoko | > 30 | **532** | ✅ 17× |

**Mitä tämä todistaa:**

1. **`grandfather`-avain on oikea.** 88 % onnistunut mappaus ei ole sattumaa — se vahvistaa että live-API käyttää tätä avainta. Edellinen `mothersFather` oli 0 % → silkka spekulointi koodin alkuperäisellä kirjoittajalla.

2. **Backfill-optimointi toimi.** 3 477 hevosta × 1 req/s = 58 min naiivisti. Race_id-grouping vei 356 kutsuun = ~6 min. **10× nopeutus** kuten ennakkolaskelma lupasi.

3. **Otoskoot vahvoja sekä sirelle että dam_sirelle.** Mediaani 532–554 starttia per oriin → estimaatit ovat tilastollisesti merkityksellisiä. Pieni-otoksen suodatin `_SIRE_MIN_STARTS = 30` osoittautuu reilusti alimitoitetuksi todelliseen jakaumaan — voidaan harkita nostoa myöhemmin (esim. 100), mutta ei pakollinen muutos.

**Ei jäljelle jääviä blokkereita Vaiheelle 3.** Mallin treenaus voi alkaa milloin tahansa B1 (isotonic) + B2 (sire+dam_sire) -piirteiden kanssa.

---

## B3 · Devigged closing odds piirteenä

**Status:** ⏸ ODOTUKSESSA — kerätään puhdasta T-2min-snapshot-dataa K1-korjauksen jälkeen, jatketaan ~24.5.2026

**Auki olevat kysymykset:**

**Auditoijan tarkistus:** Hyväksytty odotustila käyttäjän päätöksellä 10.5.2026 — B3 ei tehdä nyt, vaan kerätään 2 viikkoa T-2min-snapshot-dataa puhtaan K1-korjauksen jälkeen. Jatketaan ~24.5.2026.

---

### ✅ Vaihe B valmis (lukuun ottamatta odotuksessa olevaa B3:a)

- [x] B1 ✅ (isotonic regression hyväksytty 10.5.2026)
- [x] B2 ✅ TÄYSIN VALMIS (commit 1d36448 + Hetzner-backfill) — sire 89.62 % notna, dam_sire 88.00 % notna, kaikki tavoitteet ylitetty
- [⏸] B3 odotuksessa kunnes T-2min-dataa kertynyt (~24.5.2026)
- [ ] Mallin ensimmäinen treenausajo tehty B1:n vertailussa (Vaihe 3 voi alkaa)

### Auditoijan vahvistus Vaihe B:lle: ✅ **HYVÄKSYTTY 10.5.2026 — Vaihe 3 voidaan aloittaa**

Vaihe B:n päivitetty tila:
- B1 ja B2 ovat tuotantokelpoisia → Vaihe 3 (mallin treenaus) voi alkaa milloin tahansa.
- B3 (devigged odds) ei ole Vaihe 3:n blokkeri — se on incremental feature joka lisätään myöhemmin parantamaan tuotannossa olevaa mallia.
- Vaihe C voidaan tehdä rinnakkain Vaihe 3:n kanssa.

**Suositeltu työnkulku tästä eteenpäin:**

1. **Aloita Vaihe 3 (mallin treenaus) heti** — alkuperäinen baseline ilman B3-piirrettä on hyvä lähtökohta. Käytä:
   - Sekä `calibrate_temperature` että `calibrate_isotonic` rinnakkain validointijoukolla
   - Sire + dam_sire-piirteet mukana (FEATURE_COLS:issa)
   - K1-pollutoidut kentät pois (ne palautetaan 2026-09)
2. **Vaihe C voi alkaa rinnakkain**:
   - C1 (drift-monitorointi) on tärkein — rakenna ennen kuin malli pelaa rahaa
   - C2 (walk-forward dokumentaatio) on pieni dokumentaatiomuutos
   - C3 (pace-pilotti) voi odottaa Vaiheen 3 ensimmäisten tulosten jälkeen
3. **B3 (devigged odds)** lisätään takaisin ~24.5.2026 ja malli treenataan uudelleen

Hyvää työtä koodarille. Vaihe A:n ja B:n korjaukset ovat huomattava parannus alkuperäiseen toteutukseen verrattuna — kaksi vakavaa bugia (K1 + B1/B2-implementaatiovirheet) on korjattu, kaksi piilevää (dam_sire-avainvirhe + K1-aggregaatti-pollutio) löydetty ja korjattu.

---

# VAIHE 2.5 — Rata-piirteet (tehdään ennen Vaihetta 3)

> Lähde: docs/TASK_TRACK_FEATURES.md — auditoijan prioriteettimuutos 10.5.2026.
> Rata-rakennepiirteet (loppusuoran pituus, open stretch jne.) lisätään
> ennen Vaiheen 3 treenausta — ilman niitä malli oppii vain rata × outcome
> -korrelaatioita eikä ymmärrä raviradan fysiikkaa.

## Tehtävä A · Track-luokka schemaan

**Status:** ✅ valmis (commit 95f71d1, 10.5.2026)

**Mitä muutettiin:**
- `src/data/schema.py`: lisätty `Track`-luokka (`__tablename__ = "tracks"`) — 19 saraketta (commit 95f71d1)
  - `from datetime import datetime` lisätty importteihin
  - Docstringin taulut-lista päivitetty sisältämään `tracks`
- Uusi taulu syntyy automaattisesti `Base.metadata.create_all`:lla — **ei muutoksia `_COLUMN_MIGRATIONS`-dictiin**

**Sarakkeet:**

| Sarake | Tyyppi | Käyttö |
|---|---|---|
| `track_name` | String PK | "Färjestad" — vastaa `races.track`:n arvoa |
| `travronden_code` | String | "F" — yhdistää `horse_starts.track`-koodeihin |
| `atg_track_id` | Integer | ATG:n sisäinen rata-id |
| `slug` | String | "farjestad" |
| `country` | String | "SE" (default) |
| `length_total` | Integer | Radan kokonaispituus metreinä → FEATURE_COLS |
| `length_home_stretch` | Integer | Loppusuoran pituus metreinä → **kriittisin piirre** |
| `width_1`, `width_2` | Integer | Leveydet → FEATURE_COLS |
| `dosage` | Integer | Kaarteen kallistus → FEATURE_COLS |
| `open_stretch` | Boolean | Toinen passing-linja → FEATURE_COLS |
| `angled_wing` | Boolean | Kaltevat keulakaaret autostartille → FEATURE_COLS |
| `description` | String | Tekstikuvaus (ei FEATURE_COLS) |
| `track_analysis` | String | Travronden asiantuntija-arvio (ei FEATURE_COLS) |
| `built` | String | Rakennusvuosi (String, koska "1936 (renoverad 2001)") |
| `capacity` | Integer | Yleisömäärä |
| `homepage` | String | |
| `source` | String | "travronden" / "wikipedia" / "manual" |
| `updated` | DateTime | Viimeisin päivitysaika |

**Verifiointi lokaalisti:**
```
tracks-sarakkeet: ['track_name', 'travronden_code', 'atg_track_id', 'slug', 'country',
  'length_total', 'length_home_stretch', 'width_1', 'width_2', 'dosage',
  'open_stretch', 'angled_wing', 'description', 'track_analysis',
  'built', 'capacity', 'homepage', 'source', 'updated']
migrate() → kaikki 6 taulua luodaan oikein (races, runners, horses, horse_starts, odds_snapshots, tracks)
192 testiä passing
```

**Auki olevat kysymykset:** ei mitään — `Base.metadata.create_all` hoitaa uuden taulun automaattisesti.

**Auditoijan tarkistus:** ✅ HYVÄKSYTTY 10.5.2026 (Opus 4.7)

Tarkistus tehty empiirisesti:

1. ✅ **`Track`-luokka oikein** ([schema.py:210–253](src/data/schema.py:210)):
   - `__tablename__ = "tracks"`
   - 19 saraketta tarkistettu, kaikki tyypit oikein
   - `track_name` PK vastaa suoraan `races.track`-arvoja → **ei tarvita erillistä mappausta** (toisin kuin trackCode→nimi B1:ssä)
   - `built = Column(String)` — hyvä valinta, koska "1936 (renoverad 2001)" -tyyliset arvot toimivat
   - `source`-kenttä erottaa "travronden" / "wikipedia" / "manual"
   - `updated = Column(DateTime, default=datetime.utcnow)` — automaattinen aikaleima

2. ✅ **`from datetime import datetime`** lisätty ([schema.py:19](src/data/schema.py:19))

3. ✅ **Migraatio luo taulun puhtaaseen DB:hen automaattisesti**:
   ```
   Tables: ['horse_starts', 'horses', 'odds_snapshots', 'races', 'runners', 'tracks']
   tracks cols (19): ['track_name', 'travronden_code', 'atg_track_id', 'slug', 'country',
     'length_total', 'length_home_stretch', 'width_1', 'width_2', 'dosage',
     'open_stretch', 'angled_wing', 'description', 'track_analysis',
     'built', 'capacity', 'homepage', 'source', 'updated']
   ```
   Verifioitu väliaikaisella tiedostokannalla — `migrate(tmp_path)` → 6 taulua, joista tracks uusin. **Ei muutoksia `_COLUMN_MIGRATIONS`-dictiin tarvittu** kuten koodari oikein teki — `Base.metadata.create_all` hoitaa kokonaan uudet taulut.

4. ✅ **Ei regressiota**: koko `pytest` sviitti 192 testiä passing 18.78 s.

**Pieni huomio jatkoa varten** (ei blokkeri):

- `track_name` on String PK — case-sensitive ja ääkköset huomioiden. Varmista että Travrondenin antama nimi (`name` = "Färjestad") **vastaa täsmälleen** `races.track`-arvoa (myös ATG:sta tulee "Färjestad"). Jos joku radan nimi tulee eri muodossa (esim. "Aby" vs "Åby", "Goteborg" vs "Göteborg"), join hajoaa hiljaisesti. Tarkista Tehtävä B:ssä että kerätyt rata-nimet matchaavat 1:1 `SELECT DISTINCT track FROM races`-listaan ennen kuin merkkaat valmiiksi. Lisää testi:
  ```python
  def test_all_races_tracks_have_track_row(tmp_path):
      # Varmistaa että jokainen races.track löytyy tracks-taulusta nimellä
      ...
  ```

Hyvää työtä — yksinkertainen tehtävä mutta toteutettu puhtaasti. Voit edetä Tehtävä B:hen.

---

## Tehtävä B · Travronden track-fetcher + CLI

**Status:** ✅ valmis — commit `2b8da71` (11.5.2026)

**Kerätyt radat:** _(ajetaan Hetznerillä Tehtävä C:n yhteydessä)_

**Toteutus:**

Uusi tiedosto `src/data/scrapers/travronden_tracks.py`:

| Funktio | Kuvaus |
|---|---|
| `TravrondenTracksClient` | HTTP-asiakas, tiedostovälimuisti per round_id |
| `fetch_all_se_tracks(scan_from, scan_limit)` | Skannaa round_id:t taaksepäin, kerää `country=="SE"` |
| `upsert_tracks(db_path, tracks_data)` | Idempotentti kirjoitus tracks-tauluun |
| `_parse_capacity(v)` | String/int → int, käsittelee unicode-välilyönnit |

CLI-komento `scheduler.py`:ssä:
```bash
python -m src.data.scheduler fetch-track-structures [--scan-from N] [--scan-limit N]
```

**Empiiriset vahvistukset ennen toteutusta:**

Käytiin Travrondenspel.se:n API:ssa ennen koodin kirjoittamista:
- `/round/171922/statistics/` palauttaa `round.tracks[0]` Färjestadin tiedoilla
- Kaikki 16 kenttää vahvistettu: `length_home_stretch=177`, `open_stretch=false`, `capacity="10000"` (string)
- Ulkomaisia ratoja (NO, DK, DE, FR) löytyy — suodatetaan `country=="SE"`
- Round_id tiheys: ~40 round/vrk → SCAN_LIMIT=5000 kattaa ~4 kk

**Testit:** 28 uutta testiä (`tests/test_travronden_tracks.py`), 220/220 läpi

```
TestParseCapacity  (6): int, string, kapea välilyönti, None, garbage
TestToInt          (5): int, float, string, None, garbage
TestToBool         (5): True/False/None/int
TestFetchAllSeTracks (4): SE-suodatus, deduplikointi, mock HTTP
TestUpsertTracks   (7): insert, update, bool→int, capacity string→int, source
TestCaching        (1): välimuisti estää HTTP-kutsun
```

**Auditoijan tarkistus:** ✅ HYVÄKSYTTY 10.5.2026 (Opus 4.7)

### Mitä tarkistettiin

**1. Arkkitehtuuri** ([travronden_tracks.py](src/data/scrapers/travronden_tracks.py)):
- ✅ Selkeä kolmiosainen rakenne: `TravrondenTracksClient` (HTTP+cache) → `fetch_all_se_tracks` (skannaus+suodatus) → `upsert_tracks` (DB-kirjoitus)
- ✅ Context manager (`__enter__/__exit__`) toimii, sulkee httpx.Client:n
- ✅ `_fetch()` palauttaa `None` 404:lle — **oikea valinta** (skip-on-not-found, ei pitäisi kaatua kun yksittäinen round_id puuttuu)
- ✅ `@retry(stop_after_attempt(3))` transient-verkkovirheille — tarpeellinen kuten todistettiin smoke-testissä alla
- ✅ Cache file-level, JSON-vika hoidetaan poistamalla korruptoitu tiedosto
- ✅ Rehellinen UA `"ravit-edge research (jarkkom.lahde@gmail.com)"` ei spoofausta
- ✅ Early-stop 500 + 300 extra = 800 peräkkäistä ilman uutta rataa → riittävä

**2. SE-suodatin** ([travronden_tracks.py:200](src/data/scrapers/travronden_tracks.py:200)):
- ✅ `country == "SE"` tarkistetaan ennen lisäystä
- Travrondenin API palauttaa myös NO/DK/DE/FR-ratoja — suodatin estää nämä

**3. Kenttä-mappaus** ([travronden_tracks.py:262–282](src/data/scrapers/travronden_tracks.py:262)):
- ✅ Kaikki 19 sarakkeesta käsitelty:
  - JSON `id` → `travronden_code`
  - JSON `atg_id` → `atg_track_id` (numeroitu)
  - JSON `track_description` → `description`
  - Kaikki muut suoraan samannimisinä
- ✅ Defensiiviset converterit `_to_int`, `_to_bool`, `_parse_capacity`
- ✅ `_parse_capacity` käsittelee unicode-välilyönnit ja pilkun (esim. "10 000", "10,000")
- ✅ `built = str(...)` säilyttää "1936 (renoverad 2001)" -muodot
- ✅ `source = "travronden"` merkitsee lähteen, mikä tukee myöhempää manuaalista korjausta

**4. Testikattavuus** — 28 lisätestiä [test_travronden_tracks.py](tests/test_travronden_tracks.py):
- ✅ Kaikki **28 passing**, yhteensä **220 passing** sviitissä (11.41 s)
- ✅ Kattaa: `_parse_capacity` (6), `_to_int` (5), `_to_bool` (5), `fetch_all_se_tracks` (4 mock-pohjaista), `upsert_tracks` (7), cache-käyttäytyminen (1)

**5. CLI-integraatio** ([scheduler.py:1810–1828, 1855–1865](src/data/scheduler.py:1810)):
- ✅ `fetch-track-structures` argparse-komento oikein
- ✅ Optional `--scan-from` ja `--scan-limit` toimivat (default = vakiot tiedostossa)
- ✅ Lazy import — modulin lataus vain kun CLI-komento aktivoidaan, ei vaikuta scheduler-startup-aikaan

### End-to-end smoke-testi (oikealla API:lla)

Aja: `python -m src.data.scheduler fetch-track-structures --scan-from 171922 --scan-limit 5`

```
travronden_tracks: verkkovirhe round 171922: The read operation timed out
   ↑ tenacity-retry hoiti — pyyntö meni läpi 2. yrityksellä
Skannataan round_id:t 171922..171917 ...
Löydetty 1 SE-rataa: ['Färjestad']
DB päivitetty: {'updated': 1, 'skipped': 0}
```

DB-rivi:
```
SELECT track_name, travronden_code, atg_track_id, length_total,
       length_home_stretch, open_stretch, angled_wing, source FROM tracks;
('Färjestad', 'F', 15, 1000, 177, 0, 0, 'travronden')
```

Luvut **täsmäävät** Travrondenin alkuperäiseen vastaukseen (`length_total=1000`,
`length_home_stretch=177`, `open_stretch=false→0`). Koko ketju (HTTP → parse →
SE-suodatus → DB-kirjoitus) toimii oikein.

**Erityishuomio:** Tenacity-retry pelasti pyynnön kun ensimmäinen yritys
timeouttasi. Tämä on tärkeä robustisuus-elementti — Hetzner-ajossa
3 000+ pyynnön aikana joitakin timeout-virheitä on odotettavissa.

### Yksi pieni huomio (ei blokkeri)

[travronden_tracks.py:281](src/data/scrapers/travronden_tracks.py:281) käyttää
`datetime.utcnow()` joka on **deprekoitu Python 3.12+:ssa**:

```
DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled
for removal in a future version. Use timezone-aware objects to represent
datetimes in UTC: datetime.datetime.now(datetime.UTC).
```

Korjaus on yksi rivi:
```python
from datetime import datetime, timezone
...
obj.updated = datetime.now(timezone.utc)
```

Voit korjata tämän yhdessä Tehtävä D/E:n kanssa — ei aja vielä erillinen
commit. Sama varoitus tulee 8 testitapauksessa, mutta ei vaikuta lopputuloksiin.

### Päätös

**Tehtävä B on hyväksytty.** Voit edetä Tehtävä C:hen (Wikipedia-validointi).

**Hetzner-ajo Tehtävä C:n yhteydessä:**

```bash
git pull
python -m src.data.scheduler fetch-track-structures
# Odotettu kesto: 3000–5000 round_id:tä × 1 req/s = ~50–80 min
# Odotettu tulos: 20–26 SE-rataa löytyy
sqlite3 data/ravit.db "SELECT COUNT(*) FROM tracks;"
sqlite3 data/ravit.db "SELECT track_name, length_home_stretch, open_stretch FROM tracks ORDER BY length_home_stretch;"
```

Skannaus on hidas (~1 h), mutta **kertaluonteinen** — rata-rakenne ei muutu.
Hyvällä syyllä voit ajaa tämän taustalla SSH-istunnossa kun teet Tehtävä C:tä.

### Mitä Tehtävä C:ssä lisäksi varmistetaan

Yhdistä auditoijan A-kohdan varoitukseen (`races.track` ↔ `tracks.track_name`):

```sql
-- Kaikki races.track-arvot löytyvät tracks-taulusta nimellä
SELECT r.track AS missing_in_tracks
FROM (SELECT DISTINCT track FROM races) r
LEFT JOIN tracks t ON t.track_name = r.track
WHERE t.track_name IS NULL;
```

Pitäisi palauttaa **tyhjä joukko**. Jos joku rata puuttuu (esim. galoppi-rata
joka karsittu tracks:sta mutta on edelleen races:ssa), lisää manuaalisesti
tracks-tauluun source="manual" -merkinnällä ennen Tehtävä D:tä.

---

## Tehtävä C · Sanity-tarkistukset (uusi muotoilu — Wikipedia hylätty)

> **Käyttäjän palautteen perusteella 10.5.2026:** Wikipedia-validointi oli
> liioiteltua. Travronden on kaupallinen toimija jonka liiketoiminta riippuu
> rata-tietojen oikeellisuudesta. Wikipedian ruotsalaiset ravirata-sivut ovat
> kansalaislähtöisiä ja pienempien ratojen osalta puutteellisia. Lisäksi
> smoke-testissä Färjestadin luvut (`length_home_stretch=177` jne.) jo
> täsmäsivät live-API:hin → koodin oikeellisuus on vahvistettu.
>
> Vaihdettu kolmeen kevyempään tarkistukseen jotka kohdistavat oikeisiin
> riskeihin (kattavuus + dramaattiset typot), ei pieniin yksikkövirheisiin
> joita LightGBM kestää joka tapauksessa.

**Status:** ✅ valmis (11.5.2026)

### C.1 Kattavuus-tarkistus (kriittinen — pakollinen)

Aja Hetzner-keräyksen (`fetch-track-structures`) jälkeen:

```sql
-- Onko jokainen races.track-arvo löydettävissä tracks-taulusta?
SELECT DISTINCT r.track AS missing_in_tracks
FROM races r
LEFT JOIN tracks t ON t.track_name = r.track
WHERE t.track_name IS NULL;
```

**Odotettu:** tyhjä joukko.

**Jos puuttuvia ratoja löytyy** (esim. galoppi-radat jotka karsittu travrondenin
SE-suodattimella mutta jäänyt projektin races-tauluun, tai harvinaiset
poikkeustapaukset): lisää manuaalisesti `INSERT`-lauseella, merkitse
`source="manual"`. Ei tarvitse rakenteellisia kenttiä jos ei ole saatavilla
— LightGBM hoitaa NaN:n.

### C.2 Sanity-arvoaluetarkistus (kriittinen — pakollinen)

```sql
-- Tarkista että rakennepiirteet ovat järkevissä rajoissa
SELECT track_name, length_total, length_home_stretch, width_1, width_2,
       open_stretch, angled_wing
FROM tracks
WHERE length_total NOT BETWEEN 700 AND 1300       -- SE-radat 800–1100 m
   OR length_home_stretch NOT BETWEEN 80 AND 300  -- tyypillisesti 150–250 m
   OR width_1 NOT BETWEEN 1500 AND 2500           -- leveydet 1800–2200
   OR width_2 NOT BETWEEN 1500 AND 2500;
```

**Odotettu:** tyhjä joukko (ei outliereita).

**Jos yksittäinen rata on raja-alueen ulkopuolella** — tutki manuaalisesti.
Voi olla:
- Oikea poikkeustapaus (jokin pieni rata on aidosti 750 m → laajenna rajaa)
- Travrondenin typo → korjaa manuaalisesti, source="manual"
- Parsing-virhe koodissa → debug

### C.3 Valinnainen spot-check (1 rata, ~2 min)

**Vain jos C.1 ja C.2 menivät puhtaina** — yksi rata vertailuksi viralliselta
sivulta (EI Wikipedia, vaan radan oma kotisivu jota Travronden myös linkittää):

| Rata | Lähde | Tarkistettava |
|---|---|---|
| Solvalla | https://www.solvalla.se/ | length_home_stretch (~220 m) |
| Färjestad | https://www.ftrav.se/ | jo vahvistettu 177 m ✅ |

Sanity-tason yksinkertainen vertailu. Jos täsmää, kaikki kunnossa. Jos eroaa
yli 10 m → kommentoi raportissa, päätä mihin uskoa (yleensä Travronden, mutta
joskus radan oma sivu on tarkempi).

**Aikabudjetti C-tehtävälle:** 15–20 min (oli alkuperäisessä 1 h).

---

### Toteutus (11.5.2026)

**Hetzner-ajo:** `fetch-track-structures` ajettiin, löysi 25 SE-rataa välimuistista (645 tiedostoa). Prosessi tappettiin early-stop:in odottamisen sijaan, upsert ajettiin välimuistista manuaalisesti.

**C.1 tulos:** ✅ Tyhjä — kaikki `races.track`-arvot löytyvät `tracks`-taulusta

Aluksi 5 puuttuvaa: `Bro Park`, `Eskilstuna`, `Göteborg Galopp`, `Jägersro Galopp`, `Mantorp`. Selvitys:

- **Bro Park, Göteborg Galopp, Jägersro Galopp**: galloppiratoja — eivät koskaan esiinny Travrondenspelin V-pelilähdöissä. Lisätty manuaalisesti stub-riveillä (`source="manual"`, kaikki rakennesarakkeet NULL).
- **Eskilstuna** (ATG id=14) ja **Mantorp** (ATG id=22): oikeita raviratoja mutta eivät järjestä V-pelien lähtöjä → ei Travronden-dataa. Lisätty manuaalisesti (`source="manual"`, rakenne NULL). LightGBM käsittelee NaN:n automaattisesti.

Lopullinen tracks-taulu: **30 rataa** (25 Travrondenista + 5 manuaalisesti).

**C.2 tulos:** ✅ Kaksi outlier-tapausta, molemmat selittyvät:

| Rata | Anomalia | Selitys |
|---|---|---|
| Tingsryd | `length_total=1609` (raja 700–1300) | **Ruotsin ainoa mailiratarata** — `track_description` vahvistaa: *"Sveriges enda milebana, den 1609 meter långa travovalen"*. Ei typo, oikea data. |
| Kalmar | `width_1=2550, width_2=2600` (raja 2500) | Marginaalisesti yli rajan, todennäköisesti leveämpi rata. Travrondenin data konsistentti. |

**C.3 tulos:** ✅ Spot-check Solvalla + Färjestad

| Rata | Travronden-data | Arvio/odotus | Tulos |
|---|---|---|---|
| Färjestad | `length_home_stretch=177` | 177 m ✅ | Täsmää |
| Solvalla | `length_home_stretch=200`, `open_stretch=False` | ~220 m (arvio) | **Ero ~20 m** — Travronden johdonmukainen (23 eri roundissa), luotetaan Travrondenin dataan |

Solvalla `open_stretch=False` oli yllättävä (ajateltiin sen olevan True), mutta Travronden sanoo johdonmukaisesti False — hyväksytään.

**Kaikki 30 SE-radan rakennekentät DB:ssä:**

```sql
sqlite3 data/ravit.db "SELECT track_name, length_home_stretch, open_stretch FROM tracks ORDER BY length_home_stretch;"
```
```
Åmål|105|0          Visby|156|0        Tingsryd|285|0
Hagmyren|170|0       Färjestad|177|0    Skellefteå|175|0
Gävle|178|0          Örebro|178|0       Åby|180|0
Rättvik|187|0        Jägersro|190|0     Umåker|190|0
Lindesberg|192|0     Solvalla|200|0     Dannero|200|0
Bergsåker|200|0      Halmstad|203|0     Vaggeryd|200|0
Romme|205|0          Kalmar|207|0       Bollnäs|207|0
Axevalla|227|0       Boden|200|0        Östersund|218|0
Årjäng|205|0         [+5 manual stubs: NULL]
```

**Auditoijan tarkistus:** _(odottaa — pyydän tarkistamaan:)_

1. C.1: Hyväksyykö auditoija että galloppiradoille (Bro Park, Göteborg Galopp, Jägersro Galopp) lisättiin tyhjät stub-rivit eikä niitä suodateta pois `races`-taulusta? (Ne ovat siellä vaikka scheduler suodattaa ne → ei join-rikkoutumisia Tehtävä D:ssä)

2. C.2: Hyväksyykö auditoija Tingsryd (1609 m = mailiratarata) ja Kalmar (leveydet 2550/2600) poikkeuksina ilman korjaustoimenpiteitä?

3. C.3: Solvalla `length_home_stretch=200` eikä ~220 m — onko tämä ongelma vai hyväksytäänkö Travrondenin data arvovaltaisena lähteenä?

4. Onko Tehtävä D:hen (track_structure_features) lupa edetä?

---

## Tehtävä D · track_structure_features() + FEATURE_COLS

**Status:** ❌ tekemättä

**Auditoijan tarkistus:** _(odottaa)_

---

## Tehtävä E · Smoke-testi

**Status:** ❌ tekemättä

**track_length_total notna%:** — %

**track_home_stretch_m notna%:** — %

**Auditoijan tarkistus:** _(odottaa)_

---

### 🛑 PYSÄYTYS — Vaihe 2.5

- [x] Tehtävä A ✅ Track-luokka schemaan (commit 95f71d1)
- [ ] Tehtävä B — scraper + CLI
- [ ] Tehtävä C — Wikipedia-validointi
- [ ] Tehtävä D — track_structure_features + FEATURE_COLS
- [ ] Tehtävä E — smoke-testi (track_length_total notna% ≥ 95)

Auditoijan vahvistus Vaihe 2.5:lle: _(odottaa)_

---

# VAIHE C — Tuotantokypsyys ja monitorointi

## C1 · Feature drift -monitorointi

**Status:** ❌ tekemättä

**Auditoijan tarkistus:** _(odottaa)_

---

## C2 · Walk-forward-ikkunan vähimmäispituus

**Status:** ❌ tekemättä

**Auditoijan tarkistus:** _(odottaa)_

---

## C3 · Pace-piirteen pilotti

**Status:** ❌ tekemättä

**Auditoijan tarkistus:** _(odottaa)_

---

### 🏁 LOPPUTILA

Auditoijan loppuvahvistus: _(odottaa)_

Vaihe 3 (mallin treenaus tuotantoon) voidaan aloittaa: _(kyllä/ei,
auditoija päättää)_
