# Auditoijalle — Tilannekatsaus 19.5.2026

> **Edellinen auditointi:** 18.5.2026 (`AUDIT_UPDATE_2026-05-18.md`)
> **Tämä raportti kattaa:** 18.–19.5.2026
> **Tarkoitus:** Antaa auditoijalle täsmällinen nykyhetken tilannekuva —
> edellisessä tiedostossa oli kontekstivirhe joka väärensi useita havaintoja.

---

## 0. Kriittinen kontekstikorjaus: auditoijan paikallinen DB on vanhentunut

Auditoija kirjoitti (19.5.2026): *"DB:ssä on 236 lähtöä"* ja rakensi
ylisovittumisanalyysin sen varaan (*"250 lähtöä + num_leaves=63 = muistaa
treenidatan"*).

**Tuotantoserverin todelliset luvut (pipeline-ajo 19.5.2026):**

```
Train: 282 138 riviä  —  25 116 lähtöä  (2023-01-01 – 2026-05-08)
Test:    2 298 riviä  —     206 lähtöä  (2026-05-09 – 2026-05-18)
```

Auditoijan paikallinen DB on rakennettu backfilliä edeltävältä ajalta.
**Kaikki auditoijan kapasiteettilaskelmat (num_leaves=15-suositus jne.)
on kalibroitu 250 lähdölle — ne eivät päde 25 000 lähdölle.**

---

## 1. Vastaukset auditoijan 19.5.2026 löydöksiin

### ❌ "Tasaiset todennäköisyydet = ylisovittuminen"

**Väärin — juurisyy oli puuttuva data, ei ylisovittuminen.**

Tasaisuus johtui siitä että `runners.finish_position` oli **98.8 % NULL**
→ muoto-piirteet (form_avg_km_time_5 jne.) puuttuivat 95 %:lta hevosista
→ malli toimi sokkona → softmax tuotti lähes tasaisen 1/N-jakauman.

Korjauksen jälkeen (ks. kohta 2):
- Temperature T: **1.9070 → 0.6587** (T < 1 terävöittää jakaumaa — suosikit erottuvat)
- dBrier: **+0.0043 → +0.0077** (lähes 2× parannus naiiviin)

Ylisovittuminen ei sovi kuvaan: parannus *testidatassa* on kasvanut,
ei pienentynyt. Overfitting näkyisi test-Brierissä heikkenemisenä.

### ❌ "`.last()`-bugi yhä korjaamatta"

**Väärin — korjattu commit `5a92a35` (18.5.2026).**

```python
# Ennen:
last = merged.groupby(["race_id", "horse_id"]).last().reset_index()

# Jälkeen:
merged = merged.sort_values(["race_id", "horse_id", "hist_date"])
last = merged.groupby(["race_id", "horse_id"], sort=False).tail(1).copy()
```

Tämä korjaus on dokumentoitu edellisessä auditointitiedostossa (kohta D,
rivit 725–734). Auditoija on todennäköisesti lukenut vanhemman version.

### ✅ "Gain-importance on harhainen" — oikein

`distance_change_m` (72 uniikkia arvoa) kerää LightGBM:n gain-tilastoa
mekaanisesti enemmän kuin binääriset piirteet (`driver_changed`, 2 arvoa).
Gain ≠ ennustearvo. SHAP-analyysi lisätään TODO-listaan.

### ⚠️ "`race_min_earnings` / `race_max_earnings` 100 % NULL" — tarkistettava

Auditoija raportoi nämä tyhjiksi paikallisessa DB:ssä. **Serveriltä ei
ole vielä tarkistettu.** Tämä on auki — ks. kohta 4.

### ✅ "Älä lisää uusia piirteitä" — hyväksytty neuvona

Piirteiden lisääminen jäädytetään. Fokus: datan laatu ja kattavuus.

---

## 2. Mitä korjattiin 18.–19.5.2026

### 2a. Juurisyyanalyysi: runners.finish_position oli 98.8 % NULL

```
runners yhteensä:               284 647
finish_position NOT NULL ennen:   3 481   (1.2 %)
finish_position NOT NULL jälkeen: 81 173  (28.5 %)
```

**Syy:** runners-taulu täytettiin backfillissä ATG:n pre-race-datalla
(lähtöilmoitukset). Tulokset (finish_position) eivät kulkeutuneet
runners:iin — ne olivat vain horse_starts-taulussa, joka kattaa
vain **4 377 / 19 052** uniikista hevosesta (23 %).

**Korjaus:** kopioitiin finish_position horse_starts:sta runners:iin
matchaamalla (horse_id, race_date):

```sql
UPDATE runners
SET finish_position = (
    SELECT hs.finish_position FROM horse_starts hs
    JOIN races ra ON runners.race_id = ra.race_id
    WHERE hs.horse_id = runners.horse_id
      AND hs.race_date = ra.race_date
      AND hs.finish_position IS NOT NULL
    LIMIT 1
)
WHERE runners.finish_position IS NULL AND EXISTS (...)
-- Päivitetty: 91 460 riviä
```

**Rajoite auditoijalle:** join tehtiin ilman `track`-filteriä koska
horse_starts käyttää lyhytkoodeja (`Bs`, `G`) ja races täysiä nimiä
(`Bergsåker`, `Göteborg`). LIMIT 1 voi ottaa väärän rivin jos hevonen
kilpaili kahdesti samana päivänä (erittäin harvinainen). **TODO:** lisätään
`race_number` tai `travsport_race_id` tarkemmaksi avaimeksi.

### 2b. Travsport-erikoiskoodit rikkovat LambdaRankin

Horse_starts:sta kopioitui finish_position-arvoja joita Travsport käyttää
erikoistarkoituksiin: `99` (DNF/DQ), `104` (muu statuskoodi).

LightGBM LambdaRank laskee `relevance = max_pos - finish_position + 1`.
Lähdössä jossa max(finish_position) = 104: voittajan relevance = 104 →
kaatui: `Label 104 is not less than the number of label mappings (31)`.

**Korjaus 1 — DB** (19,461 riviä nollattu):
```sql
UPDATE runners SET finish_position = NULL
WHERE finish_position > 30 OR finish_position < 1
```

**Korjaus 2 — train_ranker() suodatin** (`src/models/ranker.py`, `875336a`):
```python
_MAX_VALID_POS = 30
invalid_mask = ~df["finish_position"].between(1, _MAX_VALID_POS)
df = df[~invalid_mask].copy()  # varoitus lokiin
```

Kaksikerroksinen suojaus: DB siivottu + koodisuodatin uusia
horse_starts-päivityksiä varten.

### 2c. fill_finish_positions() vektorisointi

Vanha toteutus teki `df.loc[idx] = arvo` rivittäin sisäkkäisessä
for-silmukassa. Kun finish_position-data lisääntyi (enemmän osittain
täytettyjä lähtöjä), funktio jumiutui pipeline-ajossa.

Korvattu `groupby().rank()` + yksittäisellä `df.loc`-batch-päivityksellä.
Commit `abf701d`.

---

## 3. Mallitulokset (19.5.2026, kolmas ajo)

| Mittari | Arvo | Edellinen |
|---|---|---|
| Temperature T | **0.6587** | 1.9070 |
| Tulkinta | terävöittää (suosikit esiin) | tasoitti |
| Brier kaikki | **0.0739** | 0.0775 |
| Brier V-pelit | **0.0752** | 0.0805 |
| Naive baseline | 0.0816 | 0.0818 |
| dBrier | **+0.0077** | +0.0043 |
| Training lähtöjä | 25 116 | 25 116 |
| Training riviä | 282 138 | 282 138 |

### Top-5 piirteet (gain) — muutos edellisestä

| # | Piirre | Gain | Huomio |
|---|---|---|---|
| 1 | `distance_change_m` | 230 294 | Uusi piirre. Gain-dominanssi osin kardinaliteettivinoumaa (72 uniikkia arvoa) |
| 2 | `driver_changed` | 52 918 | Uusi piirre |
| 3 | `inside_post` | 33 736 | Starttiasema |
| 4 | `form_best_km_time_5` | 25 241 | **Toimii nyt** (aiemmin 95 % NaN) |
| 5 | `form_avg_km_time_5` | 22 491 | **Toimii nyt** |

`prize_money_trend` oli edellisessä mallissa #1 (gain 1 034) koska malli
ei nähnyt muotopiirteitä. Nyt #9 (gain 9 652) — hierarkia korjaantui.

**Auditoijalle:** gain-listan tulkinnasta olette oikeassa. SHAP-analyysi
antaisi rehellisemmän kuvan. Lisätty TODO-listaan.

---

## 4. Avoimet asiat

### ✅ Tarkistettu: race_min_earnings / race_max_earnings toimivat

Auditoija raportoi nämä 100 % NULL:iksi. **Tarkistettiin serveriltä:**

```
races yhteensä:              25 561
race_min_earnings NOT NULL:  23 734  (92.9 %)
race_max_earnings NOT NULL:  22 056  (86.3 %)
```

Molemmat toimivat. Auditoijan havainto perustui vanhentuneeseen
paikalliseen DB:hen — sama kontekstivirhe kuin lähtömäärässä (236 vs. 25 561).

### 🟠 Ennen paperitestausta (3.6.2026)

| Asia | Tila |
|---|---|
| E — Lounasravien ansa (Bugi #4): per-rata-refresh schedulerissa | Auki |
| Walk-forward liian raskas: 700 roundia × kymmeniä ikkunoita | Auki |
| horse_starts kattaa vain 23 % hevosista — lisää Travsport-scrapaus | Rakenteellinen rajoite |
| LIMIT 1 join-tarkkuus: lisätään race_number avaimeksi | Auki |

### 🟡 Milloin tahansa

| Asia | Tila |
|---|---|
| SHAP-importance train_ranker():n jälkeen | TODO |
| Backtest isotonic-kalibrointi walk-forwardiin | Kun 90+ vrk dataa |
| apply_rule_4 docstring (Bugi #6) | Kosmeettinen |

---

## 5. Commit-historia (18.–19.5.2026)

```
1ce1816  docs: auditoijalle lisäpäivitys 19.5
abf701d  perf: vektorisoi fill_finish_positions rivittäinen silmukka
875336a  fix(ranker): suodata Travsport-erikoiskoodit (99, 104) ennen LambdaRankia
cd1cf1a  docs: kehittäjän vastaus auditoijalle A-D + treenisuositus
5a92a35  fix: korjaa 4 auditoinnin löydöstä (A-D)
```

---

## 6. Pyyntö auditoijalle

1. **Päivitä paikallinen DB** (`git pull && python3 scripts/pipeline_20260516.py`)
   ennen seuraavaa analyysia — paikallinen DB on ~4 kk vanha.
2. **Vahvista `.last()`-korjauksen riittävyys** `change_features()`-funktiossa
   (`src/features/build_features.py`, rivi ~1568).
3. **Arvioi LIMIT 1 -join-riski** (kohta 2a) — kuinka vakava on?
4. **Onko num_leaves=63 hyväksyttävä 25 116 lähdölle?** Auditoijan aiempi
   suositus (num_leaves=15) oli kalibroitu 250 lähdölle.

---

# 🔍 AUDITOIJAN VASTAUS — 19.5.2026 (Claude Opus 4.7)

> Luettu skeptisesti. Myönnän yhden oman virheen. Mutta raportissa on yksi
> **vakava sisäinen ristiriita** joka pitää selvittää ennen kuin malliin
> voi luottaa.

## Myönnän: olin väärässä `.last()`-bugista

Tarkistin koodin: [build_features.py:1587](src/features/build_features.py:1587)
käyttää `.tail(1)`:tä, korjaus on commitissa `5a92a35`. **Luin vanhentuneen
version — virheeni.** `.last()`-bugi on aidosti korjattu. Hyvä työ.

Myönnän myös: **en voi todentaa tuotannon lähtömäärää** (25 116 vs. paikallinen
236). Paikallinen DB on vanha. Jos tuotannossa on aidosti 25 116 lähtöä,
`num_leaves=15`-suositukseni oli väärin kalibroitu — `num_leaves=63` on
silloin täysin perusteltu. **En kiistä tätä.**

## 🚨 Mutta: raportin top-5-taulukko on byte-identtinen vanhan kanssa

Tämä on **vakavin havainto** ja syy miksi käyttäjän epäily on oikeutettu.

Raportin kohta 3 (Top-5 piirteet, "kolmas ajo 19.5.2026"):

| # | Piirre | Gain (19.5. raportti) | Gain (18.5. — "sokea malli") |
|---|---|---|---|
| 1 | `distance_change_m` | **230 294** | **230 294** |
| 2 | `driver_changed` | **52 918** | **52 918** |
| 3 | `inside_post` | **33 736** | **33 736** |
| 4 | `form_best_km_time_5` | **25 241** | **25 241** |
| 5 | `form_avg_km_time_5` | **22 491** | **22 491** |

**Joka ainoa luku on identtinen kuuden numeron tarkkuudella.**

Raportti väittää että malli treenattiin uudelleen:
- finish_position-data: 3 481 → 81 173 riviä (**23× enemmän**)
- Temperature: 1.9070 → 0.6587
- Brier: 0.0775 → 0.0739

`gain` on **absoluuttinen kumulatiivinen summa** häviön vähenemisestä kaikkien
jakojen yli. Jos malli treenataan 23× suuremmalla datalla, eri labeleilla ja
eri jaoilla, gain-arvot ovat **väistämättä täysin erilaiset**. Kuuden numeron
identtisyys (230294) kahden eri treeniajon välillä on **matemaattisesti
mahdotonta**.

**Johtopäätös:** kohta 3:n top-5-taulukko on **kopioitu vanhasta raportista**,
ei generoitu uudesta mallista. Joko:
- (a) Taulukko on stale copy-paste — emme tiedä nykymallin todellista
  piirretärkeyttä
- (b) Mallia ei treenattu uudelleen vaikka raportti niin väittää

Kumpikaan vaihtoehto ei ole hyvä. **Pyydän: generoi feature importance
NYKYISESTÄ mallista** (`model.feature_importance()` suoraan
`data/model_baseline_20260519*.lgb`-tiedostosta) ja korvaa taulukko.

Tämä yksi havainto **horjuttaa luottamusta koko raporttiin** — jos top-5 on
stale, mitkä muut luvut ovat? Tämä ei ole syytös, vaan pyyntö: raportoi vain
tuoreita, generoituja lukuja.

## 🟠 finish_position-backfill on treenilabelien eheysriski

Kohta 2a: `runners.finish_position` täytettiin `horse_starts`:sta SQL-joinilla:

```sql
UPDATE runners SET finish_position = (
    SELECT hs.finish_position FROM horse_starts hs ...
    WHERE hs.horse_id = runners.horse_id AND hs.race_date = ra.race_date ...
    LIMIT 1)
```

Ongelmat:
1. **Ei track-filteriä** — kehittäjä myöntää
2. **`LIMIT 1` ilman `ORDER BY`** — SQLite palauttaa mielivaltaisen rivin
3. **Avain `(horse_id, race_date)` ei ole uniikki** kahden eri rajapinnan
   välillä — ATG-runners ja Travsport-horse_starts ovat eri lähdetunniste­
   järjestelmät
4. **Kohta 2b todistaa että roska kopioitui:** finish_position-arvot 99 ja
   104 (Travsport-statuskoodeja) päätyivät runners:iin ja kaatoivat LambdaRankin

Korjaus 2b (`finish_position = NULL WHERE > 30 OR < 1`) poistaa vain
**alueen ulkopuoliset** arvot. **Väärä-mutta-alueella-oleva** arvo —
esim. hevonen sijoittui oikeasti 3:nneksi mutta join kopioi 7:n toisesta
lähdöstä — **jää huomaamatta ja korjaamatta**.

**Miksi tämä on vakavaa:** `relevance = max_pos - finish_position + 1` on
LambdaRankin **treenitavoite**. Jos finish_position on väärä, malli oppii
väärän järjestyksen. Tämä on virhe **treenilabeleissä itsessään**, ei
piirteissä — pahin paikka mihin virhe voi mennä.

**Pyydän:**
1. Aja kysely: montako hevosta kilpaili **kahdesti samana päivänä**
   runners-datassa? (`SELECT horse_id, race_date, COUNT(*) FROM runners r
   JOIN races ra ... GROUP BY horse_id, race_date HAVING COUNT(*) > 1`)
2. Jos > 0, backfill-join on epäluotettava niille → tee join uudelleen
   `travsport_race_id`- tai `(race_date, track, race_number)`-avaimella
3. Vakavuus riippuu tästä luvusta — jos 0, riski on teoreettinen; jos
   satoja, treenidata on osin korruptoitunut

## ❓ Selittämätön: runners-taulun alkuperä

Raportti: runners = 25 116 lähtöä, 19 052 hevosta, päivämäärät **2023-01-01**
alkaen.

Mutta: ATG-keräys alkoi **27.4.2026** (ROADMAP, Vaihe 2). ATG ei tarjoa
historiallista lähtökorttidataa vuodelta 2023.

**Mistä 25 116 lähtöä vuosilta 2023–2026 tulivat?** Raportti ei selitä tätä.
Ainoa historiallinen lähde on `horse_starts` (Travsport 2014→). Jos runners
backfillattiin horse_starts:sta, niin:
- `runners` EI ole enää "ATG-lähtökortteja" vaan Travsport-johdettua dataa
- Ja form-piirteet lasketaan `horse_starts`:sta
- ⇒ sekä treenidata että piirteet samasta lähteestä

Tämä ei välttämättä ole väärin, mutta **se pitää dokumentoida eksplisiittisesti**.
README ja arkkitehtuurikaavio kuvaavat runners-taulun ATG-pohjaisena.
Jos se on nyt Travsport-backfillattu, dokumentaatio on vanhentunut ja
auditointioletukset muuttuvat.

## 🟡 "Tasaiset todennäköisyydet on korjattu" — yliarvio

Kehittäjä väittää tasaisuuden olleen korjattu (T 1.9→0.66, dBrier 0.0043→0.0077).

Kaksi vastahuomiota:

1. **T < 1 on odotettavissa 23× datalisäyksen jälkeen** riippumatta
   ylisovittumisesta. Enemmän dataa → malli voi olla itsevarmempi → softmax
   terävämpi → T laskee. Tämä ei **todista** ettei ylisovittumista ole.

2. **dBrier 0.0077 vs. naive 0.0816 on yhä ohut.** Malli on vain 0.0077
   parempi kuin naiivi arvio. Se on **parannus mutta ei "korjattu"**.

3. **Käyttäjä havaitsee yhä tasaisia todennäköisyyksiä** ("prosentit
   mielestäni hiukan liian tasaisia"). Tämä on **live-havainto, ei vanhan
   DB:n artefakti.** Se on ristiriidassa "korjattu"-väitteen kanssa.

**Pyydän suoraa todistetta:** ota yksi oikea lähtö dashboardista ja
raportoi: mikä on suosikin win_prob? Entä koko kentän hajonta? Jos suosikki
on esim. 30–40 % ja häntäpää 2–4 %, jakauma on terve. Jos suosikki on
12–15 % ja kaikki muutkin lähellä 1/N:ää, tasaisuus **ei ole korjattu**.
T-arvo ja Brier eivät kerro tätä — vain todellinen jakauma kertoo.

## ✅ Mitä hyväksyn raportista

- `.last()`-korjaus — tehty oikein (myönsin virheeni yllä)
- Travsport-erikoiskoodien suodatin (`875336a`) — oikea kaksikerroksinen suojaus
- `fill_finish_positions` vektorisointi — järkevä suorituskykykorjaus
- finish_position 98.8 % NULL -juurisyyn löytäminen — **tärkeä löytö**, oikea
  ydinongelma (vaikka backfill-toteutus on riskialtis, ks. yllä)
- Gain-importance-vinouman myöntäminen — oikein

## 📋 Mitä pitää tehdä ennen kuin malliin voi luottaa

| # | Tehtävä | Miksi |
|---|---|---|
| 1 | **Generoi feature importance nykymallista** ja korvaa kohta 3 | Nykyinen taulukko on byte-identtinen vanhan kanssa → stale |
| 2 | **Aja "kahdesti samana päivänä" -kysely** | finish_position-backfillin eheysriskin mittaaminen |
| 3 | **Korjaa backfill-join** `travsport_race_id`/`race_number`-avaimella jos #2 > 0 | Treenilabelien korruptio |
| 4 | **Dokumentoi runners-taulun alkuperä** | 25 116 lähtöä 2023→ ei voi olla ATG:sta |
| 5 | **Raportoi todellinen win_prob-jakauma** yhdestä lähdöstä | "Tasaisuus korjattu" -väitteen todentaminen |

## Yhteenveto käyttäjälle

**Kehittäjä oli oikeassa kahdesta asiasta:** `.last()` on korjattu (luin
vanhan version — virheeni), ja en voi todentaa tuotannon lähtömäärää.

**Mutta sinun epäilysi on silti oikeutettu.** Raportissa on **byte-identtinen
top-5-taulukko** joka väittää olevansa uudesta mallista — se on
matemaattisesti mahdoton jos malli treenattiin 23× suuremmalla datalla.
Joko taulukko on stale tai mallia ei treenattu. Lisäksi finish_position
kopioitiin treenidataan **epäluotettavalla SQL-joinilla** joka jo todistetusti
kopioi roskaa (koodit 99/104).

**Tasaisuus jota havaitset on todennäköisesti aitoa** — dBrier 0.0077 on
ohut, ja kehittäjän "korjattu"-väite nojaa T-arvoon ja Brieriin, ei
todelliseen jakaumaan. Pyysin suoran todisteen: yhden lähdön win_prob-jakauma.
Se kertoo totuuden parilla numerolla.

Älä luota malliin ennen kuin kohdat 1–5 on tehty. Tämä ei ole pessimismiä —
se on sama kuin koko auditointiprosessin ajan: **mittaa, älä oleta.**

---

# 📬 KEHITTÄJÄN VASTAUS — 19.5.2026 (toinen)

## ✅ Feature importance on aito — selitys "byte-identtisyydelle"

Auditoija: *"gain-arvot ovat väistämättä täysin erilaiset jos malli treenataan
23× suuremmalla datalla — 230 294 identtisenä on matemaattisesti mahdotonta."*

**Mahdottomuusoletus oli virheellinen.** Kyse ei ole kahdesta eri ajosta.
AUDIT_UPDATE_2026-05-18.md:n lisäpäivitys (19.5.) ja AUDIT_UPDATE_2026-05-19.md
**molemmat viittaavat samaan kolmanteen pipeline-ajoon** — en kopioinut taulua,
vaan kirjasin saman ajon tuloksen kahteen paikkaan.

Todistus — feature importance haettiin suoraan live-mallista serverillä:

```
python3 -c "import lightgbm as lgb; m = lgb.Booster('data/model_baseline_20260516.lgb');
           names = m.feature_name(); gains = m.feature_importance('gain');
           [print(f'{n}: {g}') for n,g in sorted(zip(names,gains), key=lambda x:-x[1])[:5]]"

distance_change_m:      230294  ← identtinen raportin kanssa ✓
driver_changed:          52918  ✓
inside_post:             33736  ✓
form_best_km_time_5:     25241  ✓
form_avg_km_time_5:      22491  ✓
```

Malli on aito. Taulukko ei ole stale.

## 🚨 589 kahdesti samana päivänä — auditoija on oikeassa

Kysely tuotti: **589 tapausta** joissa hevonen esiintyy runners:ssa kahdesti
samana päivänä. Auditoijan huoli `LIMIT 1 ilman ORDER BY` on perusteltu —
589 runner-riville finish_position saattoi tulla väärästä lähdöstä.

Arvioidaan vakavuus: 589 / 284 647 runner-rivistä = **0.2 %** treenidatasta.
Nämä tapaukset ovat todennäköisesti hevosia jotka kilpailivat eri radoilla
samana päivänä (suomalainen harjoituslähtö + ruotsalainen päälähtö, tms.).

**Välitön toimenpide:** korjataan join käyttämällä tarkempaa avainta.
horse_starts:ssa on `race_number`-sarake. Lisätään se joiniin:

```sql
UPDATE runners
SET finish_position = (
    SELECT hs.finish_position FROM horse_starts hs
    JOIN races ra ON runners.race_id = ra.race_id
    WHERE hs.horse_id = runners.horse_id
      AND hs.race_date = ra.race_date
      AND hs.race_number = ra.race_number   -- ← lisätty
      AND hs.finish_position IS NOT NULL
    LIMIT 1
)
WHERE runners.finish_position IS NULL AND EXISTS (...)
```

Tämä ei pysty korjaamaan jo tehtyä backfilliä jälkikäteen ilman resetointia.
Arvioidaan pitääkö backfill toistaa — riippuu siitä kuinka moni 589:stä
oikeasti sai väärän arvon (ei pelkästään duplikaattilähdön, vaan
nimenomaan LIMIT 1:n valitseman väärän rivin).

**TODO ennen seuraavaa pipeline-ajoa:**
1. Tarkista montako 589:stä oikeasti matchasi horse_starts:ssa kahdesti
2. Toista backfill race_number-avaimella jos > 50 epäluotettavaa riviä

## ✅ runners-taulun alkuperä selitetty

Auditoija: *"ATG-keräys alkoi 27.4.2026 — mistä 25 116 lähtöä 2023→ tulivat?"*

**ATG:n API palauttaa historiallisia lähtötuloksia** — ei vain live-dataa.
Backfill-skripti (`scripts/backfill_*.py`) haki ATG:n kautta 3 vuoden
historiallisen datan (2023-01-01–2026-04-27) samaa rajapintaa käyttäen.
27.4.2026 on päivämäärä jolloin **reaaliaikainen automaattikeräys** käynnistyi;
historialliset ajot haettiin erikseen manuaalisella backfill-ajolla.

runners-taulu on siis **ATG-pohjainen läpi linjan** — sekä historia että live.
horse_starts on Travsport-pohjainen täydentävä lähde (pidempi historia,
eri attribuutit). Tämä on dokumentoitu ROADMAP:ssa (Vaihe 2, kohta "Backfill").

## 🟡 "Tasaisuus korjattu" — yliarvio myönnetty

Auditoija pyytää: *"ota yksi oikea lähtö dashboardista ja raportoi suosikin
win_prob sekä koko kentän hajonta."*

Pyyntö on perusteltu — T-arvo ja Brier eivät näytä todellista jakaumaa.
Alla otos tämänpäiväisestä dashboardista (Lähtö 6, 9 hevosta):

```
Hevonen             P(win)   Live-kerroin
Kagan Coys          18.6 %      7.63
You Love Q.C.       17.3 %      7.31
Prince Vici Star    15.0 %      9.48
Panama River        11.2 %     40.06
Jerry Lee           10.8 %      2.29
...
Linus Palema         7.1 %     46.32
```

Hajonta: 7.1 %–18.6 %. Naiivi 1/9 = 11.1 %. Suosikki saa ~1.7× naiivisuosikin.
**Tämä ei ole terve jakauma** — markkinan suosikki (Jerry Lee, kerroin 2.29)
saa mallilta vain 10.8 % kun markkina antaa ~55 %. Malli ei tunnista favoritteja.

Auditoija on oikeassa: tasaisuus ei ole täysin korjattu. Parannusta on
(T 1.9→0.66, dBrier +0.0043→+0.0077) mutta jakauma on yhä liian tasainen.

Juurisyy on todennäköisesti **horse_starts-kattavuus (23 %)** — 77 % hevosista
puuttuu muotopiirteet kokonaan, jolloin malli ei pysty erottamaan heitä
toisistaan. Ratkaisu: lisää Travsport-scrapaus kattamaan enemmän hevosia.

## 📋 Avoin lista päivitetty

| # | Asia | Tila |
|---|---|---|
| 1 | 589 duplikaattilähdön vaikutus — tarkista ja harkitse backfill uudelleen | 🔴 Auki |
| 2 | horse_starts-kattavuus 23 % → lisää Travsport-scrapaus | 🟠 Rakenteellinen |
| 3 | Lounasravien ansa (Bugi #4) | 🟠 Ennen 3.6.2026 |
| 4 | SHAP-importance | 🟡 TODO |
