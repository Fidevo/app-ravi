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
| **Vaihe D2 — Travronden Vaihe 2 -pilotti** | 🟡 **KÄYNNISSÄ** (vaiheet 1–5 ✅, 6–7 avoin) | viikon sisällä |
| Vaihe C2 — Walk-forward-dokumentointi | ✅ valmis | 15.5.2026 (ROADMAP.md jo ok) |
| Vaihe 4 — Backtest + paperitestaus | ⏸ odottaa V3-tuloksia + lisädataa | ~3.6.2026 |
| Vaihe 5 — Päätöspiste | ⏸ vaatii 8+ viikkoa dataa | ~7.7.2026 |

**Tärkein nyt:** Travronden Vaihe 2 — A/B-testi ajettu uudelleen korjatuilla koodeilla (3 kriittistä bugia korjattu 15.5.2026). Korjattu delta: +0.0003 (kaikki lähdöt), +0.0039 (V-pelilähdöt). Odottaa auditoijan integraatiopäätöstä.

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

**Auditoijan päätös (15.5.2026):** ✅ **Vaihtoehto A** — kuten suositit.

Perustelu:
- Yhdenmukainen projektin nykyisen tyylin kanssa: `atg_*` (12 saraketta), `shoes_*`/`sulky_*` (6 saraketta), tulokset, kertoimet — kaikki `runners`-taulussa
- Datankeräys jatkuu kaikista lähdöistä (ROADMAP) — ei-V-pelilähdöt eivät ole "turhia", ne ovat **ennustetavissa pyynnöstä** (kun käyttäjä haluaa)
- ~40 → ~51 saraketta on SQLite:lle täysin hallittavissa
- Yksi SQL-haku riittää treenausnotebookissa — `build_feature_matrix` saa runners-DataFramen joka sisältää jo kaiken
- 25MB lisätallennustila vuodessa on triviaali

Lisäksi: **lisää myös `is_v_race` (BOOLEAN)** `runners`-tauluun samalla migraatiolla. Pollaus merkitsee `True` kun lähtö löytyy Travrondenin V-pelistä. Mahdollistaa myöhemmin yksinkertaisen `WHERE is_v_race = 1` -kyselyn ennustetuotannolle ilman erillistä näkymää.

#### 📋 Auditoijan päätökset piirre-kohtaisesti (FEATURE_COLS)

**Sisällytä `FEATURE_COLS`:iin** (suoraan tuotantoon):
- `tr_start_interval_group` ✅ 91.5 % kattavuus, pace-arvio
- `tr_is_first_after_castration`, `tr_is_first_new_driver`, `tr_is_first_new_trainer`, `tr_is_first_shoes`, `tr_is_first_carriage` ✅ 100 % kattavuus
- `tr_speed_record_k`, `tr_speed_record_m`, `tr_speed_record_l` ✅ 35–73 % kattavuus
  - Vaikka K (37 %) ja L (35 %) ovat matalat, **data-aukko on signaali** — hevoset spesialisoituvat matkaluokkiin. Kun K on saatavilla → K-spesialisti. LightGBM oppii tämän automaattisesti.
- `tr_game_percent_v` ✅ 81.1 % kattavuus

**EI vielä `FEATURE_COLS`:iin** (tallenna DB:hen mutta jätä kommentoituna pois):
- `tr_expected_odds` 🟡 23.8 % kattavuus — **liian harva luotettavalle oppimiselle**
  - LightGBM tarvitsee yleensä > 30–40 % kattavuuden piirteelle joka ei ole "puuttuminen on signaali" -tyyppinen
  - Travrondenin kerroinennuste on vapaaehtoisesti annettu, ei systemaattinen
  - Säilytä DB:ssä (mahdollinen tutkimuskäyttö myöhemmin), aktivoi jos kattavuus paranee tuotantopollauksessa
  - Kommentoi `FEATURE_COLS`:issa: `# "tr_expected_odds",  # 23.8 % notna, aktivoi jos kattavuus > 40 %`

#### ⚠️ Tarkennettava ennen Vaihetta 5 (A/B-vertailu)

**Pilot-datan ja treenidatan yhteensopivuus:**

Pilot haki **2023-02-13 – 2026-05-07** kierroksia (3 vuoden ajalta). Mutta treenidatasi
on **2026-04-27 – 2026-05-14**.

Mitä A/B-vertailu tarvitsee:
1. Tr_*-arvot **runners-tauluun MERGE:tään** pilot-cachen kautta sille osajoukolle
   missä `runners.race_date` ja Travrondenin kierroksen päivämäärä matchaavat
2. Kuinka monta runner-riviä nykyisestä treenidatasta (2 966 + 1 872 = 4 838) saa
   tr_*-arvoja? Tämä määrittää A/B-vertailun otoksen.

Ennen A/B-vertailua suorita:

```sql
-- Kuinka monta runners-riviä saa tr_*-arvoja pilot-cachesta?
SELECT
    COUNT(*) AS total_runners,
    SUM(CASE WHEN tr_start_interval_group IS NOT NULL THEN 1 ELSE 0 END) AS with_tr_data,
    100.0 * SUM(CASE WHEN tr_start_interval_group IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*) AS pct
FROM runners
WHERE race_date >= '2026-04-27';
```

**Jos kattavuus < 30 % treenidatasta**, harkitse pilot-keräyksen täydentämistä:
hae erikseen vain 2026-04-27 → 2026-05-14 -aikajakson V-pelien kierrokset.
Tämä on ~15–20 V-pelipäivää × ~2–3 kierrosta = ~50 kierrosta lisää,
korkeintaan 15 min Travrondenista (1 req/s rate-limit).

#### ✅ Pieni lisähuomio: scraper-yksikkötestit

Huomasin että `tests/test_travronden_features.py` (30 testiä) testaa `parse_travronden_race()` ja
`merge_travronden_features()`, mutta `TravrondenAPIClient` (HTTP-asiakas, cache, retry) **ei
ole yksikkötestattu**. Pilotti todisti toiminnan käytännössä (90 kierrosta, 0 virhettä), mutta
muodollisten yksikkötestien puuttuminen on pieni puute.

**Ei blokkeri**, mutta vahva suositus: lisää `tests/test_travronden_scraper.py` (tai sama
nimi) joka mockaa httpx.Client:n ja testaa:
- Cache toimii (file-level, JSON-vika hoidetaan)
- Rate-limit toimii (testaa pseudoaikaa, `monkeypatch.setattr("time.time", ...)`)
- Retry tenacityllä toimii 5xx-vastauksille
- 404 → None, ei kaata

Samanlainen rakenne kuin `tests/test_travronden_tracks.py` (28 testiä).

#### ✅ Vaihe 3: Schema-laajennus valmis (15.5.2026)

`runners`-tauluun lisätty migraatiolla (`_COLUMN_MIGRATIONS`):
- `is_v_race BOOLEAN` — True = Travrondenin V-pelilähtö
- 11 × `tr_*`-sarakkeet (INTEGER/REAL)

Migraatio ajettu Hetznerillä onnistuneesti.

#### ✅ Vaihe 5: A/B-vertailu valmis (15.5.2026, korjattu 15.5.2026)

**Backfill:** 2739 runner-riviä päivitetty Travrondenin dense-skannauksesta
(97 kierrosta, 2026-04-15 – 2026-05-13, step=10 kattava skannaus).

**TR-data kattavuus:** 2500/5154 = **48.5 %** treenidatasta (ylittää 30 % kynnyksen ✅)

---

⚠️ **KORJAUS (15.5.2026 ilta):** Alkuperäiset A/B-tulokset olivat virheellisiä.
AUDIT_FINDINGS_2026-05-15.md tunnisti 3 kriittistä bugia:
- Bugi #1: LambdaRank-ryhmät eivät järjestetty race_id:n mukaan
- Bugi #2: `tr_start_interval_group` käsitelty numeerisena, ei kategorisena
- Bugi #3: isotonic-kalibrointi puuttui backtest.py:stä

Kaikki 3 bugia korjattu commitissa `56823b4` + `3121b12`. A/B-testi ajettu
uudelleen korjatuilla koodeilla. 327 testiä läpäisee.

---

**A/B-tulokset — ALKUPERÄISET (virheelliset, 3 bugin aiheuttama):**

| Malli | Brier | NLL | Muutos |
|---|---|---|---|
| Baseline | 0.0846 | 394.58 | — |
| TR-malli | 0.0796 | 366.56 | −0.0050 |
| Baseline (V-peli) | 0.0824 | 161.50 | — |
| TR-malli (V-peli) | 0.0734 | 136.07 | −0.0090 |

**A/B-tulokset — KORJATUT (15.5.2026 ilta, 3 bugia korjattu):**

| Malli | Brier | NLL | Muutos |
|---|---|---|---|
| Baseline (37 piirrettä, ei TR) | 0.0820 | 387.07 | — |
| **TR-malli (47 piirrettä + tr_*)** | **0.0818** | **386.29** | **+0.0003** |
| Baseline vain V-pelilähdöt (72 lähtöä) | 0.0772 | 144.72 | — |
| **TR-malli vain V-pelilähdöt** | **0.0733** | **134.58** | **+0.0039** |

**Bugi-korjauksen vaikutus:** alkuperäiset tulokset yliarvioivat parannuksen merkittävästi
(+0.009 → +0.0039 V-pelilähdöissä). Korjattu delta +0.0003 kaikilla lähdöillä on
auditoijan 🟡 MARGINAALINEN PARANEMA -luokassa (< 0.001 kynnys).

**Korjatut Feature Importance -sijoitukset:**
- `tr_game_percent_v` = **#1** (säilyy huippuna ⭐⭐⭐)
- `tr_speed_record_m` = **#6** (nousi 14→6, kategorinen käsittely paransi)
- `tr_start_interval_group` = **#40** (laski 34→40, kategorisena ei parantunut)
- `tr_is_first_new_driver` = #37
- `tr_speed_record_k` = #31, `tr_speed_record_l` = #33

**Koodarin päätösehdotus auditoijalle (korjattu):**
Kokonaisparanema +0.0003 on marginaalinen. V-pelilähdöissä +0.0039 on auditoijan
"LISÄTTY SIGNAALI" -alueella (0.001–0.005). Edellinen suositus "INTEGROI" oli
virhellisten A/B-tulosten perusteella — perutaan. Auditoijan päätettäväksi.

---

#### ⚠️ Auditoijan analyysi 15.5.2026 — KAKSI HUOLTA ENNEN INTEGRAATIOPÄÄTÖSTÄ

**Käyttäjä nosti esiin kaksi tärkeää huolta. Olen samaa mieltä molemmista.**
Päätös integraatiosta lykätään kunnes ulkopuolinen auditoija on tarkastanut
tilanteen.

---

##### Huoli 1: `tr_game_percent_v` #1 — Copycat-riski (KÄYTTÄJÄ ON OIKEASSA)

Käyttäjä epäili: *"`tr_game_percent_v` on koko mallin tärkein piirre — eikö
tämä ole riski? Mallista tulee 'markkinan peili' (Copycat)."*

**Lyhyt vastaus:** kyllä, tämä on aito ja vakava riski. **A/B-tulos on
todennäköisesti yliarvioitu** koska pilot-data käytti closing-line-arvoja,
mutta tuotanto pollaisi early-line-arvoja.

###### Mikä `tr_game_percent_v` käytännössä on

Travrondenin `game_percent.providers.ATG.V*.percent` on **kollektiivisen yleisön
panostusjakauma** V-pelipoolissa. Esim. V64.percent = 24.98 % tarkoittaa että
24.98 % V64-tikettien yhdistelmistä sisältää tämän hevosen tällä legillä.

Tämä on **kymmenien tuhansien pelaajien yhteinen arvio voittotodennäköisyydestä**.
Ruotsin V-pelin pelivolyymi tekee siitä **erittäin tehokkaan markkinan**.

###### Miksi tämä on Copycat-riski

LightGBM löysi tämän piirteen tärkeimmäksi koska:
1. Markkinaprosentti **on jo erittäin tarkka voittotodennäköisyyden arvio**
2. Muut piirteet (form, sukutaulu, kuski, rata) ovat **redundantteja** — markkina hinnoittelee ne kaikki jo sisään
3. Yksinkertaisin tapa minimoida Brier-virhettä on **kopioida markkinaa**

**Lopputulos:** jos mallin ennuste = markkinan ennuste, **odotusarvo on −takeout**.
- Pari-mutuel V-peli: takeout ~22 % → odotusarvo −22 %
- Single-win Unibet/Betsson: takeout ~5–8 % → odotusarvo −5..−8 %

**Edge syntyy vain niistä lähdöistä joissa malli on eri mieltä kuin markkina.**
Jos malli on lähes-aina samaa mieltä → ei edgeä.

###### Lisäksi: pilot-data on closing-line, tuotanto olisi early-line

Tämä on **kriittinen tarkennus** koodarin tuloksesta:

| Pilot-data (2023–2026 finished kierrokset) | Tuotanto-pollaus (15:00/17:00) |
|---|---|
| `game_percent` = **closing-line proxy** | `game_percent` = **early/mid-day live** |
| Sisältää sharp-rahan vaikutuksen | Vain harrastajat ja varhaiset panostajat |
| Vahva markkinasentimentti | Heikko markkinasentimentti |
| Brier-paranema 0.009 | Brier-paranema **luultavasti pienempi**, ehkä 0.002–0.005 |

**A/B-tulos +0.009 V-pelilähdöissä ei välttämättä toistu tuotannossa.** Pilot
tehokkaasti käytti "tulevaisuusvuotanutta" closing-line-tietoa.

Käyttäjä huomautti: *"Suurimman hyödyn saisimme esim viimeisen tunnin
peliprosenteista kun 'viisas raha' astuu peliin."* **Tämä on oikein.** Mutta
tuotantopollaus klo 15:00/17:00 ei saa tätä.

###### Vaihtoehdot — mitä tehdään?

**A) Hyväksy Copycat-riski, integroi tuotantoon, mittaa empiirisesti.**
- Risk: malli toimii teoriassa mutta ei tuota edgeä
- Reward: jos `tr_start_interval_group` (pace-piirre) + muut tarjoavat lisäarvoa
  markkinan päälle, edge syntyy noiden kautta
- Vaatii **tiukan paperitestauksen** — älä siirry V6 ennen kuin CLV on positiivinen

**B) Poista `tr_game_percent_v` mallista, säilytä DB:ssä.**
- Risk: menetetään aito markkinasignaali (sharp-vahvistaminen)
- Reward: malli ei voi olla Copycat, sen on löydettävä signaalia muualta
- Tämä on **tieteellisesti puhtaampi** lähestymistapa edgen mittaamiseen

**C) Pollaa game_percent useassa aikapisteessä, käytä myöhäistä.**
- Lisää pollauksia T-2h, T-1h, T-15min
- Käytä piirteenä **T-15min** (viisas raha jo paikalla, sharperit)
- Vaatii: paljon enemmän API-pyyntöjä, scheduler-monimutkaisuus
- Mutta lähinnä **closing-line-paranemaa** tuotannossa

**D) Pollaa game_percent useassa pisteessä, käytä DELTA.**
- Piirre: `tr_game_percent_v_delta = late_percent - early_percent`
- Tämä on **"sharp money signal"** — mihin viisaat panostajat menivät
- **Tämä on AIDOSTI itsenäinen signaali markkinasta** — kertoo missä info tuli
- Vaihtoehtoa D on yleisesti käytetty professional sports betting -tutkimuksessa

###### Auditoijan suositus

Suosittelen yhdistelmää **B + valmistautuminen C/D:hen**:

1. **Heti**: jätä `tr_game_percent_v` **pois FEATURE_COLS:ista** alkuintegraation aikana
   - Tarvitsemme **clean baseline** ilman markkina-peilausta
   - Jos malli löytää edgeä ilman tr_game_percent_v:tä → siellä on **aitoa
     mallin omaa edgeä**
   - Jos ei löydä → tiedämme että koko TR-paranema oli markkina-peilausta

2. **Pollaus tallentaa game_percent silti DB:hen** (sarake `tr_game_percent_v`)
   - Säilyy historiassa tutkimuskäyttöön
   - Aktivointi myöhemmin jos saadaan delta-piirre toimimaan

3. **Vaihe D2.5 (myöhempi)**: rakenna multi-snapshot-pollaus
   - Pollaa esim. T-2h, T-1h, T-15min
   - Laske delta-piirre `tr_game_percent_v_delta`
   - Tämä on aidosti uusi signaali (sharp money movement) — ei Copycat

**Tämä on todennäköisesti ulkopuolisen auditoijan päätösalue** — odotetaan
hänen analyysiään ennen lopullista päätöstä.

---

##### Huoli 2: `tr_start_interval_group` #34 — outo sijoitus

Käyttäjä ihmetteli: *"Lisäksi ihmettelin miksi tr_start_interval_group jäi
sijalle #34."*

**Lyhyt vastaus:** sijoitus #34 on **pettymys mutta selittyvissä**. Syyt
voivat olla useita, ja se ei tarkoita että piirre on hyödytön.

###### Miksi #34 on yllättävän alhainen

D1-tutkimus paljasti että `start_interval_group` on:
- Asiantuntijoiden per-hevonen, per-lähtö pace-arvio
- 4-portainen luokitus (1/11/21/31)
- 91.5 % kattavuus pilot-datassa

Odotus oli että tämä olisi **top-10** piirteissä — pace on alalla yksi
tärkeimmistä yksittäisistä prediktoreista.

###### Mahdolliset syyt #34-sijoitukselle

1. **Kategoriallinen koodaus puuttuu**
   - Arvot 1/11/21/31 ovat järjestysluokat, **ei lineaarisia**
   - Jos LightGBM kohtelee niitä numeerisina, "ero 1→11" = 10 ja "ero 11→21" = 10 — mutta tämä ei kuvasta todellista pace-eroa
   - **Kokeile:** lisää `tr_start_interval_group` `CATEGORICAL_COLS`:iin eikä `FEATURE_COLS`:iin
   - Vaihtoehtoinen koodaus: 1→4 (nopein), 11→3, 21→2, 31→1 (hitain) tai one-hot

2. **Multikollineaarisuus markkina-arvioiden kanssa**
   - `tr_game_percent_v` (#1) ja `form_market_avg_5` sisältävät jo pace-tietoa
   - Markkina hinnoittelee pace-edge:n kertoimiin
   - Gain-mittari jakaa tärkeyttä korreloivien piirteiden kesken — #34 voi
     olla *"markkinan jälkeen jäljellä oleva signaali"*

3. **Asiantuntijat eivät osu paremmin kuin malli muutoinkin**
   - Travrondenin asiantuntijat ovat hyviä mutta eivät täydellisiä
   - Yhdistettynä kaikkien muiden piirteiden kanssa, marginaalinen lisäarvo

4. **Liian vähän dataa**
   - 4 838 runneria, joista vain 48.5 % on V-pelilähdössä → ~2 350 tr_*-riviä
   - 4-portainen kategoriajakauma → ~590 esimerkkiä per pace-luokka
   - LightGBM tarvitsee enemmän dataa luotettavasti oppiakseen interaktioita

###### Diagnostinen ehdotus ulkopuoliselle auditoijalle

Ennen kuin tuomitsemme `tr_start_interval_group`:n, tehdään **3 koetta**:

a) **Ablation testi** — kouluta malli ilman `tr_game_percent_v` (käyttäjän huoli #1)
   ja katso miten `tr_start_interval_group` ranking muuttuu
   - Jos nousee top-10:een → kyseessä multicollinearity markkinan kanssa
   - Jos pysyy #34 → asiantuntijaarvio ei tuo lisäarvoa

b) **Categorical encoding** — siirrä `tr_start_interval_group` `CATEGORICAL_COLS`:iin
   - Aja sama A/B-vertailu
   - Jos ranking ja Brier paranevat → koodaus oli ongelma

c) **SHAP-analyysi** — vaikka gain on #34, SHAP voi paljastaa että piirre
   vaikuttaa erityisesti **tiettyjen hevosten** ennusteisiin
   - LightGBM-malli + `shap.TreeExplainer` → top-shap-arvot piirreittäin
   - Erilainen kuva kuin gain-mittari

---

##### Yhteenveto auditoijalta (15.5.2026)

**Päätös: ÄLÄ INTEGROI TUOTANTOON VIELÄ.** Odota ulkopuolisen auditoijan
analyysi käyttäjän pyynnön mukaisesti.

**Mitä on selvitettävä ennen tuotantointegraatiota:**

1. Onko `tr_game_percent_v` #1 Copycat-ilmiö?
   - Aja ablation ilman tr_game_percent_v → tutki Brier-paranema
   - Jos paranema putoaa 0.009 → 0.002, A/B-tulos oli pääosin markkina-peilausta

2. Onko `tr_start_interval_group` #34 oikea sijoitus?
   - Aja samalla ablation muutoksilla (categorical encoding)
   - SHAP-analyysi tarkempaa kuvausta varten

3. Pilot-data closing-line vs. tuotannon early-line
   - Tämä on **rakenteellinen ongelma** A/B-vertailussa
   - Korjaus vaatii: multi-snapshot-pollausstrategian (yllä vaihtoehto C/D)

**Älä anna koodarille uusia tehtäviä vielä.** Käyttäjä on pyytänyt ulkopuolisen
auditoijan tarkistuksen tärkeimpiin tiedostoihin — odotamme sitä ennen jatkoa.

#### ❓ Avoimet vaiheet — auditoijalle (Vaihe 5 päätös + jatko)

**Vaihe 6:** Pollaus-cron `run_forever`:iin
- Scheduler hakee Travronden-datan V-pelilähdöille ennen lähtöjä
- Ma–Pe 15:00/17:00, La 09:00/11:00/13:00, Su 10:00/12:00

**Vaihe 7:** `is_v_race`-kenttä toiminnassa
- Schema ja migraatio: jo tehty ✅
- Scheduler pitää merkitä `is_v_race=True` kun V-pelilähtö löytyy Travrondenista

**Aikabudjetti jäljellä:** ~1–2 päivää (vaiheet 6–7)

---

### 🟡 Vaihe 3 — Parannukset #7 + #8 + #9 (auditoijan korjauslista)

**Status:** 🟡 avoin, ~1 h yhteensä
**Tausta:** Vaihe 2 (kriittiset bugit #1–#6) hyväksytty 15.5.2026. Parannukset
#7–#9 ovat matalan prioriteetin mutta hyödyllisiä tehdä yhdessä committilla.

Ohjeet: [`AUDIT_FINDINGS_2026-05-15.md`](AUDIT_FINDINGS_2026-05-15.md) →
"KORJAUSLISTA — Vaihe 3 parannukset"

| Parannus | Tiedosto | Aika |
|---|---|---|
| #7 — Distance bucket -rajat 1640/2140 → 1999/2599 | `build_features.py` (rivit 167, 294) | 10 min + testit |
| #8 — `edge_decay_analysis` suodattaa tyhjät viikot ROI-modessa | `backtest.py` | 15 min + testit |
| #9 — `renormalize_after_scratch` TODO Vaihe 6 docstring | `scratch_handler.py` | 5 min |

Sitten Hetzner-deploy: `git pull` + restart-scheduler.

---

### 🆕 Vaihe D3 — Streamlit-dashboard (visuaalinen näkymä)

**Status:** 🟡 uusi tehtävä, ~1 työpäivä
**Tausta:** käyttäjän pyynnöstä 15.5.2026 — visuaalinen näkymä päivän
ennusteille. Päätösperusteet ja koodirunko: [`docs/FRONTEND_DECISION.md`](docs/FRONTEND_DECISION.md).

**Suositus:** Streamlit, ei Astro (alkuvaiheessa). Astro Vaihe 6:lle jos
halutaan julkinen sivu.

| Tehtävä | Aika |
|---|---|
| Lisää `streamlit` requirements.txt:hen | 1 min |
| `src/dashboard/app.py` — perusrunko (~150 riviä, runko FRONTEND_DECISION.md:ssä) | 4–5 h |
| Testaus: `streamlit run src/dashboard/app.py` lokaalisti | 30 min |
| README.md: dashboard-osio | 15 min |
| Smoke-testi Hetzner-datalla | 30 min |

**UI-spesifikaatio:**

- Sidebar: päivän valinta, V-pelilähdöt-checkbox, edge-kynnys-slider
- Päänäkymä per V-pelikierros, per lähtö → taulukko:
  - `# | Hevonen | P(win) % | Odds | Edge %`
  - Value-pelit korostettuna värillä/⭐
- Cache: `@st.cache_data(ttl=300)` ja `@st.cache_resource` mallin lataukseen

**Tärkeä huomio:** dashboard on **tutkimuskäyttöön**, ei pelaamiseen.
Mallin voittosignaali on edelleen 0.0023 vs. uniform — älä luota
ennusteisiin rahapelipäätöksiin ennen Vaihe 5:n päätöskriteerit täyttyvät.

---

### ✅ Vaihe C2 — Walk-forward-dokumentointi

**Status:** ✅ valmis (15.5.2026)
**Tarkistus:** ROADMAP.md:ssä Vaihe 5 sisältää jo kaikki vaaditut kohdat:
- "Vaatii vähintään 8 viikkoa walk-forward-dataa" ✅
- D-kategoria: "Liian vähän dataa | n < 200 → Älä tee päätöstä" ✅
- n ≥ 200 -vaatimus ✅

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
