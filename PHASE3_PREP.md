# Vaihe 3 — Esivalmistelu

> Kirjoitettu 10.5.2026 koodianalyysin pohjalta.
> Kaikki löydökset on tarkistettu suoraan tiedostoista.
> Käsitellään ennen 8.6.2026.

---

## Onko meillä turhaa työtä?

**Lyhyt vastaus: osittain kyllä, osittain ei — mutta oikea ongelma on että
teemme oman laskennan HUONOMMASTA datasta vaikka parempi on jo olemassa.**

Konkreettinen esimerkki:

`driver_trainer_features()` laskee ohjastajatilastot **meidän omasta
runners-taulustamme**, jossa on ~14 päivää dataa. Tyypillinen ohjastaja
on siinä 2–5 kertaa.

ATG tallentaa jokaiseen starttiin `atg_driver_win_pct`-kentän, joka on
ohjastajan **koko kuluvan vuoden voitto-% kaikista Ruotsin ravilähdöistä**
— tuhansista starteista. Me kuitenkin käytämme omaa noisy-versiota ja
jätämme ATG:n version kokonaan käyttämättä.

Sama toistuu usealle piirteelle. Alla koko tilanne.

---

## Ongelmat prioriteettijärjestyksessä

---

### 1 🔴 BLOKKERI — FEATURE_COLS-nimet eivät täsmää

**Tiedostot:** `src/models/ranker.py` ↔ `src/features/build_features.py`

`ranker.py` odottaa nämä sarakkeet:

```python
"driver_is_win_mean"    # ei ole olemassa
"driver_is_win_count"   # ei ole olemassa
"driver_is_top3_mean"   # ei ole olemassa
"trainer_is_win_mean"   # ei ole olemassa
"trainer_is_top3_mean"  # ei ole olemassa
```

`build_features.py` tuottaa oikeasti:

```python
"driver_win_rate_365d"
"driver_starts_365d"
"driver_top3_rate_365d"
"trainer_win_rate_365d"
"trainer_top3_rate_365d"
```

Backtest kaatuu `KeyError`:iin heti kun `lgb.Dataset(X)` yrittää hakea
olemattomia sarakkeita. Ei tarvita dataa — riittää testaukseen heti.

**Korjaus:** muuta `FEATURE_COLS` ranker.py:ssä vastaamaan oikeita nimiä.

---

### 2 🟠 form_features() käyttää väärää taulua — 14 pv vs. koko ura

**Tiedostot:** `src/features/build_features.py`, `horse_starts`-taulu

`form_features()` laskee hevosen 5 viimeksi ajetun startin tilastot
**runners-taulusta**. Ongelma:

- Runners-taulussa on dataa **27.4.2026 alkaen** (~14 pv)
- Suurimmalla osalla hevosista on siellä **0–2 omaa starttia**
- `min_periods=1` palauttaa piirteen jopa yhdestä startista — tilastollisesti merkityksetön

Meillä on kuitenkin `horse_starts`-taulu jossa on **103 747 starttia**
Travsportista, kattaa koko elinikäisen uran kaikille hevosille joita olemme
nähneet. Tätä ei käytetä missään.

Tulos vaiheessa 3: ~90 % muotopiirteistä on kohinaa 0–2 startin
"keskiarvoista". Malli ei voi oppia muotopiirteistä mitään merkityksellistä.

**Korjaus:** `form_features()` ottaa sisään myös `horse_starts` DataFramen,
yhdistää sen runners-dataan (horse_id + race_date -avaimella), ja laskee
tilastot täydestä historiasta.

**Miksi ei ole redundanttia keräämistä:** ATG ei anna starttikohtaisia
historiallisia tuloksia ollenkaan — se antaa vain aggregaatteja (lifetime
win rate). Travsport antaa jokaisen yksittäisen startin, mikä mahdollistaa
viime 5 startin, viime 30 pv:n, tai eri olosuhteiden muotopiirteet.
`horse_starts` on ainutlaatuista dataa, ei duplikaattia.

---

### 3 🟠 ATG:n valmiit aggregaatit — kerätty, ei käytetä

**Tiedostot:** `src/data/schema.py` (runners-taulu) → `src/models/ranker.py`

Nämä sarakkeet ovat jo jokaisessa runners-rivillä mutta eivät
FEATURE_COLS:issa:

| Sarake | Mitä kuvaa | Kattavuus |
|--------|------------|-----------|
| `atg_driver_win_pct` | Ohjastajan voitto-% | Koko kuluva vuosi |
| `atg_driver_starts` | Ohjastajan startit | Koko kuluva vuosi |
| `atg_trainer_win_pct` | Valmentajan voitto-% | Koko kuluva vuosi |
| `atg_trainer_starts` | Valmentajan startit | Koko kuluva vuosi |
| `atg_lifetime_win_rate` | Hevosen voitto-% | Koko ura |
| `atg_lifetime_top3_rate` | Hevosen top-3-% | Koko ura |
| `atg_lifetime_starts` | Hevosen startit | Koko ura |
| `atg_current_year_win_rate` | Hevosen voitto-% | Kuluva vuosi |
| `atg_best_km_for_this_setup` | Paras km-aika | Sama matka+starttimuoto |

`atg_best_km_for_this_setup` on erityisen arvokas: se on hevosen paras
km-aika juuri tällä starttimuoto- ja matkakombinaatiolla. Paljon
informatiivisempi kuin meidän `form_best_km_time_5` joka ottaa viimeiset
5 starttia riippumatta olosuhteista — ja joita meillä on 0–2.

**Onko tämä redundanttia keräämistä?** Ohjastaja- ja valmentajapiirteiden
osalta kyllä, meidän oma `driver_trainer_features()` laskee päällekkäisen
asian huonommasta datasta. ATG:n versio on parempi Phase 3:ssa. Molemmat
kannattaa silti pitää: ATG:n for Phase 3 (riittävä data heti), oma rolling
for Phase 4+ (kattaa myös ATG:n ulkopuoliset trendit kuten muutokset
kauden aikana).

**Korjaus:** lisää kaikki yllä olevat sarakkeet FEATURE_COLS:iin — ne tulevat
suoraan runners-taulusta, ei vaadi build_features.py:hen mitään muutosta.

---

### 4 🟡 Race-luokka ei ole piirteissä — data juuri kerätty

**Tiedostot:** `src/features/build_features.py` → `races`-taulu

Viime sessiossa lisättiin races-tauluun neljä uutta saraketta kaikille
356 lähdölle. Yhtäkään ei käytetä piirteenä:

| Sarake | Arvo mallille |
|--------|--------------|
| `race_min_earnings` | Lähdön luokka-alaraja — kertoo tasosta objektiivisesti |
| `race_max_earnings` | Luokka-yläraja — yhdessä min:n kanssa tarkka luokkaikkuna |
| `race_age_group` | Ikärajaus — 2yo eri dynamiikka kuin 5yo+ |
| `track_condition` | Radan kunto — "light"/"heavy" — vaikuttaa eri hevosten km-aikoihin eri tavalla |

Race-luokka on erityisen tärkeä: hevosen kyky voittaa on hyvin
kontekstuaalinen. Sama hevonen joka voittaa "högst 30 000 kr" -lähdöissä
ei välttämättä menesty "lägst 500 000 kr" -lähdössä. Ilman tätä piirrettä
malli oppii voittajia mutta ei kontekstia.

**Onko tämä redundanttia keräämistä?** Ei — tätä ei saa muualta. ATG:n
terms-kenttä on raakaa tekstiä, jonka parsimme itse (race-luokka). Ei ole
olemassa "race class API:a".

**Korjaus:** lisää sarakkeet `race_setup_features()`:iin races-mergen
yhteydessä ja FEATURE_COLS:iin.

---

### 5 🟡 Johdetut piirteet — suunniteltu, ei toteutettu

**Tiedosto:** `src/features/build_features.py`

Kaksi piirrettä on ollut suunnitelmassa mutta puuttuu kokonaan koodista:

**`horse_age`** — `race_date.year - horses.birth_year`

Ikä on merkittävä piirre ravihevosten suorituskyvyn ennustamisessa.
2-vuotiailla kehityskäyrä on erilainen kuin 7-vuotiailla. ATG tallentaa
hevosen iän (`age`-kenttä) mutta emme tallenna sitä runners-tauluun
suoraan — birth_year on horses-taulussa, race_date on runners-taulussa.
Vaatii join:in tai erillisen laskennan.

**`barfota_law_active`** — boolean, True jos race_date on 1.12.–28.2.

Talvikielto: ATG ei raportoi barfota-tietoa 1.12.–28.2. välisenä aikana.
Kaikki `shoes_*`-sarakkeet ovat NULL talvella. Ilman tätä piirrettä malli
ei tiedä onko shoes = NULL siksi että "hevosella ei ole kenkiä" vai siksi
että "talvikielto on voimassa". Jos tätä ei eroteta, shoes-piirteet tuottavat
epäsäännöllistä kohinaa.

**Onko tämä redundanttia keräämistä?** Ei — nämä ovat laskennallisia
piirteitä, ei datankeräystä.

**Korjaus:** lisää `derived_features()`-funktio build_features.py:hyn joka
laskee molemmat suoraan olemassa olevasta datasta.

---

### 6 🟡 shoes/sulky-muutokset käyttämättä — vahva signaali

**Tiedostot:** `src/data/schema.py` (runners-taulu) → `src/models/ranker.py`

Nämä sarakkeet ovat jo runners-taulussa mutta eivät FEATURE_COLS:issa:

| Sarake | Mitä kuvaa |
|--------|-----------|
| `shoes_changed_front` | Etukengitys muuttui edellisestä startista |
| `shoes_changed_back` | Takakengitys muuttui |
| `sulky_changed` | Kärry tai kärryväri muuttui |
| `shoes_front` | Onko etukengät (vs. barfota) |
| `shoes_back` | Onko takakengät |
| `sulky_type` | Kärrytyyppi: VA (vanlig) / AM (amerikansk) |

`shoes_changed_*` ja `sulky_changed` ovat erityisen arvokkaita:
ne heijastavat valmentajan tarkoituksellista päätöstä muuttaa varustetta,
mikä usein ennakoi parantuneita suorituksia. Ammattilaisravien handicapping-
kirjallisuudessa tämä on yksi tunnetuimmista signaaleista.

**Onko tämä redundanttia keräämistä?** Ei — tämä on ainutlaatuista dataa
jota ATG tarjoaa ainoana lähteenä.

**Korjaus:** lisää sarakkeet FEATURE_COLS:iin — ne tulevat suoraan
runners-taulusta, ei vaadi build_features.py:hen muutosta.

---

## Yhteenveto — mitä on turhaa ja mitä ei

| Komponentti | Tilanne |
|-------------|---------|
| `driver_trainer_features()` rolling-laskenta | **Osittain redundantti** — ATG:n versio on parempi Phase 3:ssa; pidä molemmat mutta lisää ATG:n versio |
| `form_best_km_time_5` runners-taulusta | **Heikko** — ATG:n `atg_best_km_for_this_setup` on parempi; molemmat käyttöön |
| `horse_starts`-taulu | **Ei redundantti** — ainutlaatuista dataa jota ATG ei anna; pitää ottaa käyttöön form_features():ssä |
| Kengät/sulky-keräys | **Ei redundantti** — ainutlaatuista; pitää ottaa piirteiksi |
| Race-luokka (terms-parsinta) | **Ei redundantti** — ei API:a tälle; pitää ottaa piirteiksi |

---

## Korjausjärjestys

Voidaan tehdä vaihe vaiheelta, jokainen on itsenäinen:

| # | Muutos | Tiedosto | Työmäärä | Vaikutus |
|---|--------|----------|----------|----------|
| 1 | FEATURE_COLS-nimet oikein | `ranker.py` | 5 min | Blokkeri — pakko |
| 2 | ATG-aggregaatit FEATURE_COLS:iin | `ranker.py` | 15 min | Korkea — 9 uutta piirrettä |
| 3 | Shoes/sulky FEATURE_COLS:iin | `ranker.py` | 10 min | Korkea — 6 uutta piirrettä |
| 4 | Race-luokka `race_setup_features()`:iin | `build_features.py` + `ranker.py` | 30 min | Korkea — 4 uutta piirrettä |
| 5 | `derived_features()` — horse_age, barfota | `build_features.py` + `ranker.py` | 30 min | Keski |
| 6 | `form_features()` käyttämään `horse_starts` | `build_features.py` | 2–3 h | Korkea — ratkaisee Phase 3 datan ohuuden |

**Muutokset 1–5** ovat pieniä ja voidaan tehdä yhdellä kertaa.
**Muutos 6** on isoin (vaatii uuden funktion, testit, ja datan validoinnin)
ja kannattaa tehdä omana sessiona.

---

*Tiedosto on käsitelty kun kaikki 6 korjausta on tehty ja testattu.*
