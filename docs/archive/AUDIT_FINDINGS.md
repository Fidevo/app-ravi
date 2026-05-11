# Auditoinnin löydökset — Ravit Edge

> Auditoija: Claude (Opus 4.7)
> Päivämäärä: 2026-05-10
> Auditoidut tiedostot:
> - `src/features/build_features.py`
> - `src/models/ranker.py`
> - `src/data/scheduler.py` (upsertit, terms-parsinta, aikavyöhyke, retry)
> - `tests/test_build_features.py` (testikattavuuden silmäily)
> Ohitettu KNOWN_ISSUES.md:n kohdat #2, #3, #4, #7, #9, #10, #12, sekä
> `clv_tracker.py` ja `ui/`. Lue rinnan KNOWN_ISSUES.md ja README.md
> ymmärtääksesi kontekstin.

---

## Kriittiset löydökset (korjattava ennen treeniä)

### K1 · `fetch_results()` ylikirjoittaa `atg_*`-aggregaatit post-race-statseilla → mahdollinen data leakage

**Tiedosto:** `src/data/scheduler.py`, `fetch_results()`, rivit 880–891

```python
race = client.get_race(race_id)        # T+30min response
...
_upsert_race(session, race)
for s in race.get("starts") or []:
    horse = s.get("horse") or {}
    ...
    _upsert_runner(session, race, s)   # ← kirjoittaa kaikki atg_*-kentät uudelleen
    runner.finish_position = ...
```

`_upsert_runner()` ei aseta vain finish_positionia/km-aikaa/odds:ia, vaan
ajaa `_atg_aggregates()` ja `_person_aggregates()` jotka lukevat
`horse.statistics.life`, `horse.statistics.years.<vuosi>`, `driver.statistics`
ja `trainer.statistics` ATG:n /races/{id}-vastauksesta. Tämä vastaus haetaan
**T+30 minuuttia lähdön jälkeen**.

Jos ATG päivittää nämä statistiikat lähdön jälkeen (esim. yön yli tai useamman
tunnin viiveellä), DB-rivin `atg_lifetime_win_rate`, `atg_lifetime_top3_rate`,
`atg_lifetime_starts`, `atg_current_year_win_rate`, `atg_driver_win_pct`,
`atg_trainer_win_pct` jne. saavat **kyseisen lähdön tuloksen sisältäviä
arvoja**. Nämä piirteet ovat kaikki FEATURE_COLS:issa (`ranker.py`, rivit
45–54) → malli oppisi tulevaisuudesta menneisyyteen vuotavasta datasta.

Sama vaikutus syntyy `retry_incomplete_results`:in kautta — jokainen retry
kutsuu `fetch_results`:ia joka taas upsertaa kaikki kentät.

**Miksi tämä on kriittinen, vaikka ATG:n päivitysrytmi onkin osittain
empiirinen kysymys:**
- Jos statsit päivittyvät edes joillekin lähdöille T+30…T+24h ikkunassa,
  vuoto saastuttaa juuri sen treenidatan jonka pitäisi olla puhdasta.
- Vuoto on **subtiili**: validointi voi näyttää epäilyttävän hyvältä
  NDCG@1:llä, mutta tuotannossa malli kaatuu kun atg_*-kentät ovat
  pre-race-tilassa eikä post-race.
- Tämä on tismalleen klassinen "feature lookahead" -bugi joka on vaikein
  havaita ilman selvää testimenetelmää.

**Vahvistus:** vertaa pre- ja post-race -dump:ia samasta runner-rivistä:

```sql
-- Aja kaksi kertaa: pre-race (esim. T-15min) ja post-race (T+24h)
SELECT runner_id, atg_lifetime_starts, atg_lifetime_win_rate,
       atg_current_year_win_rate, atg_driver_win_pct
FROM runners
WHERE race_id = '<jokin valmistunut lähtö>';
```

Jos arvot eroavat, vuoto on vahvistettu.

**Korjausehdotus (yksinkertaisin):**

`fetch_results()` saisi koskea vain tulosriippuvaisiin kenttiin. Jaa
`_upsert_runner` kahteen osaan, esim.:

```python
def _upsert_runner_pre_race(session, race, start) -> tuple[bool, bool]:
    # nykyinen _upsert_runner — atg_*, shoes, sulky, jne.

def _upsert_runner_results(session, race, start) -> None:
    # vain finish_position, kilometer_time_seconds, win_odds_final
    # EI atg_*-aggregaatteja, EI shoes/sulky-päivitystä
```

Vaihtoehto: lisää `_upsert_runner`:iin parametri `update_aggregates: bool =
True`, ja kutsu `fetch_results`:sta arvolla False.

**Cold-start-suoja (kommentti rivillä 884) säilyy:** `_upsert_race` +
`_upsert_horse` voivat jäädä paikalleen, ne eivät kosketa mallin piirteitä.

**Prioriteetti:** kriittinen — vaaditaan ennen ensimmäistäkään treenausajoa.
Empiirinen vahvistus (yllä) tarvitaan luokituksen lopulliseksi
päättämiseksi, mutta vuotoriski on ilmeinen koodista.

---

## Merkittävät löydökset (korjattava ennen tuotantoa)

### M1 · `_upsert_race`/`_upsert_runner` ylikirjoittavat olemassa olevia kenttiä Nonella

**Tiedosto:** `src/data/scheduler.py`, `_upsert_race()` rivit 418–443,
`_upsert_runner()` rivit 464–493.

Molemmat kirjoittavat jokaisen kentän aina uudelleen. Esim:

```python
obj.purse_sek = race.get("prize") if isinstance(race.get("prize"), int) else None
```

Jos myöhempi ATG-kutsu palauttaa `prize=None` tai eri tyypin, jo tallennettu
hyvä arvo tuhotaan. Sama koskee `track_condition`, `race_terms`, `driver`,
`trainer`, `handicap_meters` jne.

ATG:n /races/{id}-vastaus on yleensä monotonisesti rikastuva (myöhempi haku
sisältää enemmän tietoa kuin aikaisempi), joten tämä ei käytännössä
todennäköisesti aiheuta ongelmia, mutta puolustautumismekanismi puuttuu.
Esimerkiksi capture_odds_snapshot ei kosketa runners:ia, mutta retry- ja
refresh-jobit kutsuvat kaikkia upserttejä uudelleen.

**Korjausehdotus:** "älä kirjoita Nonea jos olemassa oleva arvo on ei-None"
-pattern: `obj.purse_sek = new_value if new_value is not None else obj.purse_sek`.
Idiomaattisempi vaihtoehto: helper `_set_if_present(obj, "purse_sek", value)`.

**Prioriteetti:** merkittävä, koska riski on pieni mutta korjaus halpa ja
tekee data-pipelinesta huomattavasti turvallisemman tulevia muutoksia varten.

---

### M2 · `form_features`: rolling-laskenta käyttää `win_odds_final`-saraketta tulevista lähdöistä

**Tiedosto:** `src/features/build_features.py`, rivit 86–108.

`combined`-pool sisältää sekä historian että nykyisen runner-rivin. Rolling
käyttää `shift(1)` joten ei vuoda omaan piirteeseensä — tämä on oikein.

Mutta: `combined["_market_prob"] = 1.0 / combined["win_odds_final"]`. Kun
runners-DataFrame sisältää **tulevia** lähtöjä joiden win_odds_final on
NULL (tulokset eivät vielä ole tulleet), niiden arvot eivät vuoda nykyisten
piirteisiin (shift(1) suojaa). **Mutta** kun lasketaan piirteitä lähdölle X
ja runners sisältää lähdön Y > X, joka on samalle hevoselle, ja Y on jo
ajettu (esim. backtestissä myöhempänä päivänä), Y:n win_odds_final voi olla
DB:ssä jo kirjattuna ja vuotaa X:n piirteisiin **vain jos X tulee Y:n
jälkeen sortauksessa** — sortaus on `(horse_id, race_date)` joten vuoto
edellyttäisi että Y:n race_date < X:n race_date, mikä on pois suljettua.

**Mutta** edge case: jos sama hevonen ajaa **kahdesti samana päivänä**
(harvinainen mutta mahdollinen ravipäivä, esim. eliittisarja + V75),
molemmat rivit jakavat saman race_date:n. Sortauksessa ne tulevat
peräkkäin, mutta kumpi tulee ensin on epämääräinen (sort_values:n stable
ja sekundääriavain `_is_runner` rikkoo tasapelin vain kun toinen on
historia-rivi, ei kun molemmat ovat runner-rivejä).

`drop_duplicates(subset=["horse_id","race_date"], keep="last")` poistaa
toisen rivin → vain yksi `_is_runner=True`-rivi jää lähtöön per päivä.
`runner_form` sisältää siis yhden rivin per (horse, päivä). Lopullinen
`df.merge(runner_form, ...)` antaa **molemmille saman päivän runnereille
identtiset form-piirteet**, mukaan lukien sen joka piti olla "ensimmäinen"
päivän aikana → toinen rivi saa virheellisesti omat tulokset osana
piirteitä (oma km-aika tai sijoitus voi olla "viimeisen 5 startin"
keskiarvossa).

**Riski tuotannossa:** vähäinen — sama hevonen ajaa harvoin kahdesti
päivässä Ruotsin ravissa. Ei mainintaa runners-taulun nykyisestä datasta
(3 757 starttia / 14 vrk → tuskin yhtään kaksoisstarttia havaittu).

**Korjausehdotus:** käytä groupbyn sekundääriavaimena `(horse_id, race_id)`
tai aikaleimaa eikä pelkkää race_datea. Tai dokumentoi rajoitus ja jätä —
empiirisesti ei käytännössä esiinny.

**Prioriteetti:** merkittävä logiikan kannalta, mutta **vähäisen riski**
tuotantokontekstissa. Suositus: dokumentoi rajoitus, älä korjaa nyt.

---

## Pienet havainnot (koodihygienia, ei tuotantovaikutusta)

### P1 · `fill_finish_positions`: synteettinen sijoitus voi olla epäjatkuva, jos viralliset eivät ole 1..N

**Tiedosto:** `src/features/build_features.py`, rivi 337.

```python
next_pos = int(group["finish_position"].max()) + 1
```

Jos viralliset sijoitukset ovat esim. {1, 2, 3, 5} (4. hylätty mutta jää
sijoitukseen 5), `next_pos` aloittaisi 6:sta vaikka aukko on kohdassa 4.
Aukko ei ole virhe — relevance-laskenta ranker.py:ssä käyttää
`max_pos - finish_position + 1` joten epäjatkuvuus säilyy mallissa
kohinana. Empiirisesti ATG ei näytä tuottavan tällaista — sijoitukset
ovat dense top-K. Ei tuotantovaikutusta.

### P2 · `derived_features` mutatoi `df`-parametriä in-place

**Tiedosto:** `src/features/build_features.py`, rivit 281–290.

Funktio kirjoittaa suoraan `df["barfota_law_active"]` ja `df["horse_age"]`
ilman `.copy()`-kutsua. Kutsuva `build_feature_matrix` on toistaiseksi
tämän ainoa kutsuja ja se ei välitä mutatoitumisesta. Silti tyypillinen
pandas-pattern olisi palauttaa kopio. Pieni hygieniajuttu.

### P3 · `_resolve_cols` logittaa varoituksen joka kerta kutsuttaessa

**Tiedosto:** `src/models/ranker.py`, rivit 99–109.

Jokainen `predict_win_probabilities`-kutsu logittaa varoitukset puuttuvista
piirteistä. Live-tuotannossa (jokainen lähtö, lokitus jokaiselle) tämä
täyttää lokit. Vaihtoehto: cache resolved cols ensimmäisellä kutsulla, tai
nosta varoitus vain kun `set` on muuttunut.

Toinen havainto: `missing_feat`/`missing_cat` ohitetaan aina hiljaisesti
treenausajossa. Jos `horse_age` puuttuu tärkeästä syystä (esim. JOIN
unohtui), malli treenataan ilman sitä eikä kaadu — tämä on tarkoituksellista
joustavuutta valinnaisille piirteille mutta tekee testaamisen vaikeaksi.

**Suositus:** lisää eksplisiittinen "required vs. optional" -ero, esim.
required_feat list joka KAATAA jos puuttuu, ja optional_feat list joka
saa puuttua varoituksella.

### P4 · `predict_win_probabilities` ei pysty käsittelemään yhden hevosen lähtöä

**Tiedosto:** `src/models/ranker.py`, rivit 188–192.

Yksi hevonen lähdössä → P=1.0, edge=odds-1. Käytännössä Ruotsin ravissa
ei näin tapahdu (8–12 hevosta/lähtö). Mutta ennustusrajapinta voisi
defensiivisesti tarkistaa group-koon tai poistaa tällaiset lähdöt.

### P5 · `softmax`-laskenta laskee `np.exp(s - s.max())` kahdesti

**Tiedosto:** `src/models/ranker.py`, rivi 191.

Pieni numeerinen mikro-optimointi (cache helper-array). Ei vaikuta
tarkkuuteen tai performanssiin merkittävästi — pieni hygieniahavainto.

### P6 · `_parse_terms` regex ei tunnista numeroita ilman pistettä

**Tiedosto:** `src/data/scheduler.py`, rivit 385–399.

`r"([\d\. ]+)\s*-\s*([\d\. ]+)\s*kr"` kattaa "10.000 - 50.000 kr". Mutta
empiirisesti pienet summat ("5.000 kr") kelpaavat. Ei nähtävillä
puutteita — silmäilyhuomio.

### P7 · Galloppisuojan duplikointi

**Tiedosto:** `src/data/scheduler.py`, rivit 82–86 ja `atg_client.get_calendar_day`.

Kommentin mukaan atg_client suodattaa galloppi-radat jo calendar-tasolla.
Jos näin on, `GALLOP_TRACKS`-lista `scheduler.py`:ssä on backup-suoja olemassa
oleville riveille — eli tarpeellinen. Listan ylläpidettävyys on kuitenkin
ongelma jos uusi galoppirata ilmestyy ATG-kalenteriin: vaatii koodimuutoksen
useassa paikassa. Pieni huomio. Vaihtoehto: lisää schema-tasolle `sport`-
sarake ja suodata sen perusteella.

---

## Testikattavuus — arvio

**Vahvuudet:**
- `test_build_features.py` sisältää aitoja leakage-testejä:
  `test_no_leakage_first_race_has_nan_stats`,
  `test_no_cross_track_leakage_alternating_tracks`,
  `test_no_leakage_future_history_excluded`. Nämä ovat juuri sitä mitä
  tarvitaan.
- `fill_finish_positions`:lle on kattavasti edge-case-testejä (withdrawn,
  future-only, multiple races, all-unique).
- `test_dedup_runners_take_priority_over_horse_starts` testaa form_features
  -dedupin oikeellisuuden.

**Aukot:**
1. **K1-vuoto ei ole testattu.** Ei ole testiä joka varmistaisi että
   `fetch_results` EI muuta atg_*-kenttiä. Tämä on suora aukko juuri sille
   bugille jonka epäilen. Esim:

   ```python
   def test_fetch_results_does_not_overwrite_atg_aggregates():
       # 1. Setup: aja _upsert_runner pre-race -datalla, atg_lifetime_starts=10
       # 2. Aja fetch_results post-race-datalla jossa horse.statistics.life.starts=11
       # 3. Assertoi: runner.atg_lifetime_starts == 10 (ei 11)
   ```

2. **`form_features`-saman päivän kaksoisstartti** (M2) ei ole testattu.
   `test_no_row_explosion_when_driver_races_twice_on_same_day` testaa vain
   driver-piirrettä, ei horse-form-piirrettä.

3. **`_parse_terms`** ei näytä olevan kattavasti testattu —
   ruotsinkielinen tuhaterotin ("10.000"), `lägst`/`högst`-kombo,
   ikäluokat. Ainakin yksi käyttämäni grep ei löytänyt
   `test_parse_terms`-testejä.

4. **Aikavyöhyke / DST-rajat:** `_parse_atg_datetime` käsittelee Stockholm
   → UTC -konversion, mutta DST-rajoja (esim. 28.3.2026 klo 03:00) ei ole
   eksplisiittistä testiä. Empiirisesti ATG:n startTime ei sisällä
   ambiguiteettia näinä päivinä, mutta testi olisi turvallisuuden kannalta
   hyvä.

5. **`retry_incomplete_results`:n GALLOP_TRACKS-suodatus** ei ole testattu
   (haku ei sisällä SQL-suodatusta). Voisi olla integration-tyyppinen
   testi.

**Kokonaisarvio:** kattavuus on hyvä leakagen ja form-piirteiden osalta.
Heikoin kohta on scheduler-puolella: aggregate-vuodon testaus puuttuu, ja
terms-parserin reuna-ehdot ovat luultavasti aukolla. 104 testiä on hyvä
määrä mutta painopiste on feature-engineering-puolella.

---

## Ideat ja ehdotukset mallin kehittämiseen

**Vapaata pohdintaa AUDIT_REQUEST.md:n pyynnöistä:**

### Datan laatu

- **`form_avg_finish_5` ilman segmentointia matkaa/starttimuotoa pitkin
  on tunnetusti melko karkea piirre.** Lyhyt vs. pitkä matka, autostart
  vs. voltti — hevoset käyttäytyvät eri tavoin. Suositus: tee kaksi
  rinnakkaista rolling-aggregaattia, esim. `form_avg_finish_5_same_method`
  ja `form_avg_finish_5_same_distance_bucket`. Voi auttaa erityisesti
  isojen lähtöjen luokituksessa.

- **Synteettiset sijoitukset (top 6–8 jälkeen):** käytännössä mallit oppivat
  järjestämään myös "hidas vs. nopea unplaced" -gradientin, mikä on
  hyödyllinen signaali. Mutta jos LambdaRank antaa NDCG@1:lle ehdottomasti
  suurimman painon, alaosan järjestys ei vaikuta paljoa. Suositus: testaa
  kaksi mallia rinnan — toinen ilman synteettisiä rivejä — ja vertaa
  NDCG@1, NDCG@3 ja kalibrointi.

- **`win_odds_final` (pari-mutuel ~20% takeout) vs. sharp odds:** Pinnacle/
  Betfairin tarjouksen löytäminen Ruotsin raveille on kysymysmerkki, mutta
  jos haetaan, olisi devig-korjattu close-line-arvo (`devigged_win_odds`
  on jo skeemassa) huomattavasti informatiivisempi piirre kuin raaka
  win_odds_final. Suositus: lisää `pre_race_market_prob_devigged`
  feature_cols:iin kunhan T-2min-snapshotteja on tarpeeksi.

### Piirteet

- **Lähtöpaikka × starttimuoto -interaktio:** post 1 voltissa eroaa post
  1 autossa. LightGBM:n päätöspuumallit eivät tarvitse eksplisiittisiä
  interaktioita, mutta mahdollinen yhdistetty kategoria voisi auttaa
  vähäisellä datalla. Sama pätee hevosen ikä × lähdön taso.

- **`track_horse_win_rate` 97.5% NaN:** `horse_starts`-taulu (103 747
  starttia) sisältää `track`-sarakkeen — käyttämällä sitä voisi laskea
  paljon pidemmän historian rata-spesifin win raten. Suositus:
  laajenna `race_setup_features` käyttämään `horse_starts`:ia samaan
  tapaan kuin `form_features` jo tekee.

- **Barfota-NULL-imputointi talvella:** `barfota_law_active`-piirre erottaa
  "ei tietoa" vs. "ei kenkiä", mutta pitkäaikaista ratkaisua varten:
  imputoi `shoes_*=0` kun `barfota_law_active=1` ja mallin pitäisi oppia
  tämän interaktion automaattisesti `barfota_law_active`-piirteen kanssa.
  Tällä hetkellä NULL-arvot voivat olla LightGBM:lle tulkinnanvaraisia.

### Mallin arkkitehtuuri

- **LambdaRank vs. vaihtoehdot:** LambdaRank on hyvä startti mutta sen
  kalibrointi on tunnetusti ongelma — se optimoi rank-järjestyksen,
  ei todennäköisyyksien absoluuttisia arvoja. Pari vaihtoehtoa:
  - **Plackett-Luce:** suora todennäköisyysmalli järjestykselle, parempi
    kalibrointiin mutta opetus on kalliimpaa.
  - **Pairwise XGBoost** + isotonic post-calibration: yhdistelmä joka
    tasapainottaa rank-laadun ja kalibroinnin.

- **Softmax-kalibrointi per lähtö:** se mitä `predict_win_probabilities`
  tekee, on softmax-pohjainen normalisointi mutta ei aitoa kalibrointia
  — softmaxin "lämpötila" (kerroin ennen exp:iä) on implicit 1.0 ja
  voi olla aivan väärä. Suositus: opettele lämpötilakerroin
  validointijoukolta minimoimalla NLL ennen softmaxia. Tämä yhdellä
  parametrilla parantaa kalibrointia merkittävästi ilman että rank
  kärsii. Tunnetaan myös nimellä "temperature scaling".

- **Walk-forward 14 päivää:** liian lyhyt validi evaluointi-ikkunaksi.
  Trotissa vuodenajan-kaltainen kausivaihtelu (kelit, talvi vs. kesä,
  rata-asfalttilämpötila) vaatisi vähintään 8–12 viikon ikkunan.
  Suositus: odota dataa kunnes on 8+ viikkoa ennen kuin rakentaa luottamusta
  validointimittareille. Ennen sitä mallia voi ajaa, mutta tulokset
  ovat indikatiivisia.

### Yleiset havainnot

- **Pace-piirteet:** AUDIT_REQUEST.md mainitsee tämän rajoituksena.
  Empiirisesti pace (asema 800m) on tärkeimpiä yksittäisiä piirteitä
  raveissa. Vaikka APIa ei ole, manuaalinen scrapaus Travsportin
  ravinetistä (tulosten "raviraportit") tai TV-tallenteista voisi olla
  arvokas tulevaisuuden suunta — vaikkapa pelkkä "nopea/hidas avaus"
  -kategoria.
- **Sukutaulupiirteet:** isäoriin / emänisän win rate on tunnettu
  prediktoiva piirre. Travsportista löytyy data, mutta se vaatii erillistä
  hakua per oriin — voi olla hidas, mutta yksittäin laskettava.
- **Validointi feature_drift:lle:** kun mallia ajetaan tuotannossa,
  monitoroi ATG-aggregaattien jakaumat (esim. atg_lifetime_win_rate)
  yli ajan. Jos jakauma "siirtyy", se on signaali joko datakontaminaatiosta
  (vrt. K1) tai ATG:n datamuutoksista.

---

## Yhteenveto

Löydösten kokonaismäärä: **1 kriittinen, 2 merkittävää, 7 pientä**.

| Luokka | Löydös | Todennäköinen vaikutus |
|---|---|---|
| Kriittinen | K1: fetch_results vuotaa atg_*-aggregaatit | Mahdollinen data leakage, vaatii empiirisen vahvistuksen |
| Merkittävä | M1: upsertit voivat ylikirjoittaa Nonella | Pieni riski, korjaus halpa |
| Merkittävä | M2: form_features saman päivän kaksoisstartti | Vähäinen tuotantoriski mutta koodi-virhe |

**Tärkein toimenpide ennen Vaihetta 3:** vahvista K1 empiirisesti
(pre/post-race vertailu samasta runner-rivistä) ja jaa `_upsert_runner`
kahteen funktioon jotta `fetch_results` ei kosketa atg_*-kenttiä. Ilman
tätä mallin treenitulokset ovat epäluotettavia.

**Ei löydöksiä seuraavilla alueilla** (positiivinen löydös):

- `fill_finish_positions()`:n perustoiminta on oikein. Edge-caset
  (vetäytyneet, kaikki NULL, useita rotuja) ovat käsitelty ja testattu.
- `driver_trainer_features()` rolling+closed="left"+drop_duplicates
  -patterni on oikein, eikä row-räjähdystä esiinny.
- `race_setup_features()`:n bugi #8 -korjaus (transform-versio) on
  oikein toteutettu — testit `test_no_cross_track_leakage_alternating_tracks`
  varmistavat tämän.
- `softmax`-laskenta `predict_win_probabilities`:ssa on numeerisesti vakaa.
- ATG/Travsport-asiakkaiden idempotenttius-design (per-race commit + upsert)
  on oikea valinta single-thread-schedulerille — kuten KNOWN_ISSUES.md:n
  yhteydessä on jo päädytty.
- Aikavyöhyketrukit (`_parse_atg_datetime`, ATG_TZ → UTC) on tehty oikein
  ja konsistentisti.

Auditointi keskittyi nimenomaisesti AUDIT_REQUEST.md:n ensisijaisiin
alueisiin (data leakage, treenilogiikka, päättely, scheduler). KNOWN_ISSUES.md:n
listaamat asiat ohitettiin pyynnön mukaisesti.
