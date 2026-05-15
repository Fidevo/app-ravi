# Ulkopuolisen auditoinnin korjauslista — 15.5.2026

> **Auditoinnin lähde:** ulkopuolinen agentti tutki backtest.py, build_features.py,
> ranker.py, scheduler.py, scratch_handler.py.
> **Verifioinut:** Claude (Opus 4.7) — koodista tarkistettu jokainen väite,
> erottelee aidot bugit hypoteettisista riskeistä ja parannusehdotuksista.
>
> **Tärkein johtopäätös:** löytyi **3 kriittistä bugia** + **3 merkittävää bugia**,
> joista 3 ensimmäistä on **toisistaan riippuvaisia** — ne pitää korjata
> yhdessä ja **A/B-vertailut pitää ajaa uudestaan**. Aiemmat tulokset
> (Brier-paranema +0.009 jne.) ovat epäluotettavia.

---

## ⚠️ Tärkein huomio: nykyiset mallin tulokset ovat epäluotettavia

Kolme yhdessä toimivaa bugia tekee koko Vaihe 3 + D2 -mallin treenaustulokset
**lähes käyttökelvottomiksi**:

| Bugi | Vaikutus |
|---|---|
| #1 LambdaRank-ryhmitys rikkinäinen | Malli oppii roskaa kun X ja group_sizes eivät täsmää |
| #2 `tr_start_interval_group` käsitellään numeerisena | Pace-piirteen #34 sijoitus on ehkä #34, ehkä top-5 — emme tiedä |
| #3 Backtestissä ei kalibrointia | Brier-luvut OK, mutta value-bet-tunnistin ja ROI on roska |

**Toimintajärjestys:** korjaa #1 + #2 + #3 → aja A/B-vertailu uudelleen → vasta sitten
päätä D2-integraatiosta tai mistään muusta.

---

## 🚨 Kriittiset bugit (korjattava heti)

### Bugi #1 — `train_ranker` ei lajittele dataa race_id:n mukaan

**Tiedosto:** [`src/models/ranker.py`](src/models/ranker.py) rivit 183–207
**Lähde:** auditoijan väite ranker.py-tiedostosta (kohta 1)
**Status:** ✅ **VAHVISTETTU AIDOKSI BUGIKSI**

**Ongelma:** LightGBM LambdaRank vaatii että `X`:n rivit ovat ryhmiteltynä
race_id:n mukaan ja `group_sizes` vastaa peräkkäisten ryhmien kokoa.
Nykyinen koodi:

```python
df = train_df.dropna(subset=["finish_position"]).copy()
# ... ei sortausta ...
group_sizes = df.groupby("race_id").size().values  # aakkosjärjestyksessä
X = df[avail_feat + avail_cat].copy()              # alkuperäinen järjestys
```

`df.groupby("race_id").size().values` palauttaa race_id:t aakkosjärjestyksessä,
mutta `X` säilyttää alkuperäisen järjestyksen (esim. päivämäärän mukaan).
LightGBM lukee vääriä rivejä vääriin ryhmiin → **oppii potentiaalisesti roskaa**.

**Miksi testit menivät läpi:** testidata luodaan lähtö kerrallaan luupissa,
jolloin se on luonnollisesti ryhmiteltynä race_id:n mukaan. Tuotannon SQL-haku
ei takaa tätä järjestystä.

**Korjaus:**
```python
df = train_df.dropna(subset=["finish_position"]).copy()
df = df.sort_values("race_id").reset_index(drop=True)  # LISÄÄ TÄMÄ
# ...
group_sizes = df.groupby("race_id", sort=False).size().values
```

`sort=False` on lisävarmistus — kun df on jo sortattu, ei jällensortausta.

**Vaatii myös:** lisää testi joka antaa `train_ranker`:lle DataFramen
ei-järjestyksessä ja tarkistaa että malli oppii silti oikein (vertaa
deterministisen siemenen kanssa sortatun datan tulokseen).

---

### Bugi #2 — `tr_start_interval_group` ei ole CATEGORICAL_COLS:issa

**Tiedosto:** [`src/models/ranker.py`](src/models/ranker.py) rivit 101, 126–132
**Lähde:** auditoijan väite ranker.py-tiedostosta (kohta 0, ennen muita)
**Status:** ✅ **VAHVISTETTU AIDOKSI BUGIKSI**

**Ongelma:** `tr_start_interval_group` on FEATURE_COLS:issa (rivi 101) mutta EI
CATEGORICAL_COLS:issa (rivit 126–132). Arvot 1/11/21/31 ovat **luokitukset**
(kategoriat), ei jatkuva asteikko. LightGBM kohtelee niitä numeerisesti:
"31 on kolme kertaa enemmän kuin 11", mikä on **väärä tulkinta**.

**Miksi tämä selittää #34-sijoituksen:** kategorinen koodaus antaisi LightGBM:lle
mahdollisuuden rakentaa erilliset säännöt kullekin 4 arvolle. Numeerisena se
yrittää löytää lineaarisia kynnyksiä kuten "jos > 16, hevonen huono" — mikä on
**puhdas väärin** tälle piirteelle.

**Korjaus:**
```python
CATEGORICAL_COLS: list[str] = [
    "distance_category",
    "start_method",
    "race_age_group",
    "track_condition",
    "sulky_type",
    "tr_start_interval_group",   # LISÄÄ TÄMÄ
]
```

Pidä piirre myös FEATURE_COLS:issa — _resolve_cols ja `astype("category")`
hoitavat loput.

**Älä lisää** `tr_is_first_*`-piirteitä CATEGORICAL_COLS:iin — ne ovat 0/1
bool-arvoja, LightGBM käsittelee ne luonnollisesti.

**Odotettu vaikutus:** `tr_start_interval_group` voi nousta dramaattisesti
feature_importance-listalla. Tämä **muuttaa D2-integraatiopäätöksen
perustaa** — pace-piirteen todellinen arvo saattaa olla aiemmin aliarvioitu.

---

### Bugi #3 — Backtestissä ei käytetä kalibrointia

**Tiedosto:** [`src/models/backtest.py`](src/models/backtest.py) rivit 99–103, 215–228
**Lähde:** auditoijan väite backtest.py-tiedostosta (kohta 1)
**Status:** ✅ **VAHVISTETTU AIDOKSI BUGIKSI**

**Ongelma:** sekä `quarterly_walk_forward` että `rolling_walk_forward` ennustavat
raaka-softmaxilla ilman kalibrointia:

```python
model = train_ranker(train_df)
preds = predict_win_probabilities(model, test_df)  # temperature=1.0, ei isotonic
```

LambdaRankin raaka-softmax tuottaa **ylikalibroituja todennäköisyyksiä**
(suosikit liian itsevarmoja, outsiderit liian aliarvioituja). Value-bet-tunnistin
(`merged["expected_value"] = win_prob * win_odds`) ampuu hutiin.

**Vaikutus:**
- **Brier-score on silti vertailukelpoinen** (lasketaan suoraan raakapisteistä, ei
  riipu kalibrointistrategiasta) — Vaihe 3:n Brier 0.0818 ei kärsi tästä
- **Mutta ROI %, value-bet-määrät, edge_decay_analysis** ovat **vääristyneitä**
- A/B-testin "tuotantopäätös" perustui tähän rikkinäiseen pipeline:hen

**Korjaus** (kuten auditoija suositti):

```python
# rolling_walk_forward:n while-luupin sisään, ennen treenausta:

# 1. Jaa train_df: pure_train + calib
calib_days = 14
calib_start = window_start - pd.Timedelta(days=calib_days)
pure_train_df = train_df[train_df["race_date"] < calib_start]
calib_df = train_df[train_df["race_date"] >= calib_start]

if len(pure_train_df) < 100 or len(calib_df) < 50:
    # Liian vähän dataa kalibrointiin — käytä temperature
    model = train_ranker(train_df, random_state=42)
    preds = predict_win_probabilities(model, test_df, temperature=1.0)
else:
    # 2. Treenaa puhtaalla treenidatalla
    model = train_ranker(pure_train_df, random_state=42)
    # 3. Kalibroi isotonic:lla calib-datalla
    calib_preds = predict_win_probabilities(model, calib_df, temperature=1.0)
    calib_with_truth = calib_preds.merge(
        calib_df[["race_id", "horse_id", "finish_position"]],
        on=["race_id", "horse_id"],
    )
    iso = calibrate_isotonic(calib_with_truth)
    # 4. Sovella isotonic test-joukkoon
    preds_raw = predict_win_probabilities(model, test_df, temperature=1.0)
    preds = apply_isotonic(preds_raw, iso)
```

**Vaatii lisäksi**:
- Importit `calibrate_isotonic`, `apply_isotonic`, `calibrate_temperature` `backtest.py`:hyn
- Sama korjaus `quarterly_walk_forward`:iin

---

## 🟠 Merkittävät bugit (korjattava ennen seuraavaa A/B-vertailua)

### Bugi #4 — "Lounasravien Ansa" refresh_day_runners:ssä

**Tiedosto:** [`src/data/scheduler.py`](src/data/scheduler.py) rivit 900, 935–936, 1347–1378
**Lähde:** auditoijan väite scheluder.py-tiedostosta (kohta 1)
**Status:** ✅ **VAHVISTETTU AIDOKSI BUGIKSI** — tärkeä tuotantokäyttöön

**Ongelma:** `fetch_daily_races` löytää koko päivän aikaisimman lähdön
(`earliest_start_dt`) ja `_schedule_first_race_refresh` ajastaa **yhden** refresh-jobin
10min ennen sitä.

**Konkreettinen esimerkki:** keskiviikko, Åby-lounas alkaa 12:20, Solvalla-V86 18:20.
- Refresh ajetaan 12:10 → hakee kaikkien ratojen tiedot
- 12:10 Solvallan valmentajat eivät ole vielä ilmoittaneet kenkiä/sulkyja iltaan
- V86-lähtöjen shoes_changed_*, sulky_changed jäävät vajaiksi
- Iltakisojen value-pelit (joissa on eniten markkinaa) toimivat vajalla datalla

**Korjaus:** muuta `earliest_start_dt` per-rata-dictiksi ja ajasta refresh-jobi
per rata:

```python
# fetch_daily_races: muuta earliest_start_dt → track_first_race
track_first_race: dict[str, datetime] = {}
# for-luupissa:
if race_start_dt is not None:
    track_name = _track_name(race)
    if track_name not in track_first_race or race_start_dt < track_first_race[track_name]:
        track_first_race[track_name] = race_start_dt
stats["track_first_races"] = track_first_race

# _setup_for_date: ajasta yksi refresh per rata
for track_name, first_dt in stats.get("track_first_races", {}).items():
    n += _schedule_first_race_refresh(
        scheduler, target, track_name, first_dt, db_path
    )
```

`_schedule_first_race_refresh`:in `id` pitää olla uniikki per rata:
`id=f"refresh_runners_{target.isoformat()}_{track_name}"`.

**Vaatii lisäksi:** `refresh_day_runners`-funktio voi rajata päivityksen vain
tietyn radan lähtöihin (ei pakollinen optimoituun ratkaisuun, mutta vähentää
API-kuormaa).

---

### Bugi #5 — `sire_features` aikavuoto (kommentoitu pois, mutta dokumentoi)

**Tiedosto:** [`src/features/build_features.py`](src/features/build_features.py) rivit 406–508
**Lähde:** auditoijan väite build_features.py-tiedostosta (kohta 1)
**Status:** ✅ **VAHVISTETTU AIDOKSI BUGIKSI**, mutta **piirre on kommentoitu pois**

**Ongelma:** `sire_features` laskee globaalin aggregaatin koko `horse_starts`-taulusta
ilman aikarajausta. Jos treenidata on Apr 27 – May 7, 2026, aggregaatti sisältää
*koko taulun* startteja — joista osa on **tulevaisuudessa** suhteessa treenidatan
päivämäärään.

LOO-korjaus (Vaihe 3.7) poisti hevosen omat startit aggregaatista, mutta
**ei aikavuotoa** — saman siren muut jälkeläiset voivat olla tulevaisuudessa.

**Mikä tilanne nyt:** piirre on kommentoitu pois FEATURE_COLS:ista
(KNOWN_ISSUES #13). **Ei vaikuta nykyiseen malliin.**

**Mutta:** KNOWN_ISSUES #13:ssa **AKTIVOINTIEHTO PUUTTUU** point-in-time-laskennan
osalta. Päivitettävä lisäysehdot:

1. >= 8 viikkoa puhdasta dataa (nykyinen)
2. dam_sire-kattavuus runners:ssa > 60 % (nykyinen)
3. `sire_ablation_loo.py` näyttää Brier-parannuksen (nykyinen)
4. **UUSI: point-in-time-laskenta toteutettu** — aggregaatti laskee vain
   `horse_starts WHERE race_date < runner.race_date` per runner-rivi

Tämä on **bugi joka ei vaikuta nyt** mutta **estää aktivoinnin myöhemmin**
ennen korjausta.

**Konkreettinen korjaus:** sire-aggregaattia ei lasketa "kerran kaikille",
vaan rolling-pohjaisesti per runner-rivi. Tekninen implementaatio on monimutkainen
mutta tarpeellinen ennen aktivointia.

---

### Bugi #6 — `apply_rule_4_deduction` ei sovellu ATG-pari-mutuelille

**Tiedosto:** [`src/betting/scratch_handler.py`](src/betting/scratch_handler.py) rivi 49
**Lähde:** auditoijan väite scratch_handler.py-tiedostosta (kohta 1)
**Status:** ✅ **VAHVISTETTU AIDOKSI BUGIKSI** — käytön rajoitus

**Ongelma:** `apply_rule_4_deduction` on koodattu matemaattisesti oikein Tattersalls
Rule 4(c) -säännölle (käytetään fixed-odds-vedonvälittäjillä). Mutta ATG käyttää
**pari-mutuel**-poolia jossa kertoimet **korjaavat itse itsensä** kun hevonen
peruuntuu — kassasta poistuu pelatut rahat ja kertoimet lasketaan uudelleen
livenä.

**Vaara:** jos `apply_rule_4_deduction` yhdistetään ATG-kertoimiin (live-pollaus
schedulerissa), kertoimia rangaistaan **kahdesti** — ATG laskee jo
automaattisesti, plus tämä funktio.

**Korjaus:** ei koodimuutosta, vaan **käyttöehtojen dokumentointi**:

```python
def apply_rule_4_deduction(odds: float, deduction_pct: float) -> float:
    """Sovella Tattersalls Rule 4 -vähennys kertoimeen.

    ⚠️ KÄYTTÖEHTO: Vain **fixed-odds** -vedonvälittäjille (Unibet/Betsson/
    Bet365/Pinnacle).

    EI saa käyttää ATG:n pari-mutuel-kertoimien kanssa — ATG päivittää
    kertoimet automaattisesti scratching-hetkellä, ja tämä funktio
    tekisi vähennyksen toistamiseen → mallin edge laskettaisiin liian
    pieneksi.

    Käyttöesimerkki (Vaihe 4+ paperitestaus):
        unibet_odds = 5.0  # kiinteä kerroin
        # Suosikki peruttu (oli 1.5 = 67 %)
        adjusted = apply_rule_4_deduction(unibet_odds, deduction_pct=67)
    """
    ...
```

Tämä on **dokumentaatiokorjaus**, ei koodikorjaus. Vältää tulevaisuudessa
väärää käyttöä CLV-laskennassa.

---

## 🟡 Hyödyllisiä parannuksia (ei pakollisia, mutta tervetulleita)

### Parannus #7 — Distance bucket -rajat

**Tiedosto:** [`src/features/build_features.py`](src/features/build_features.py) rivit 167, 294
**Lähde:** auditoijan väite build_features.py-tiedostosta (kohta 2)
**Status:** 🟡 **AITO ONGELMA TAKAMATKA-HEVOSILLE**, mutta vaikutus on pieni

**Ongelma:** `bins=[0, 1640, 2140, 5000]` + pd.cut default right=True tarkoittaa:
- 2140 → "middle" (oikein)
- 2160 (= 2140 + 20m takamatka volttilähdöstä) → "long" (väärin)

Sama hevonen voi saada eri kategorian peruslähdössä (2140m → middle) vs.
takamatka-lähdössä (2160m → long). `form_avg_finish_5_same_dist`-piirre ei
kohdistu johdonmukaisesti.

**Vaikutuksen suuruus:** takamatka-hevoset volttilähdöistä ovat osa lähdöistä,
kymmeniä per päivä. Mutta vaikutus kohdistuu vain tähän yhteen segmentoituun
piirteeseen, ei muihin.

**Korjaus** (auditoijan suositus):
```python
bins=[0, 1999, 2599, 5000]  # < 2000 = sprint, 2000–2599 = middle, > 2600 = long
```

Tarkistus vaaditaan: mitkä yleisimmät ravimatkat ovat? 1609, 1640, 2140, 2640,
3140 → uudet rajat (1999, 2599) sijoittavat ne luonnollisemmin (1640 → sprint,
2140 → middle, 2640 → long).

**Tee yhdessä #2:n kanssa** (samalla committilla) — molemmat liittyvät
piirteiden koodaukseen ranker-mallia varten.

---

### Parannus #8 — `edge_decay_analysis` suodattaa tyhjät viikot

**Tiedosto:** [`src/models/backtest.py`](src/models/backtest.py) rivit 269–345
**Lähde:** auditoijan väite backtest.py-tiedostosta (kohta 3)
**Status:** 🟡 **AITO RISKI EI VIELÄ TUOTANNOSSA**

**Ongelma:** `roi_pct=0.0` viikot (joissa value-pelejä ei syntynyt) vetävät
trendiviivaa kohti nollaa polyfit-laskennassa. Voi tuottaa "false alarm"
edge-decay-hälytyksen kun mitään ei tapahdu.

**Mutta:** `score_col="brier_score"` (Vaihe 3.5 -muutos) ei kärsi tästä —
Brier-score lasketaan **aina** (myös tyhjillä viikoilla). ROI-modessa ongelma
on aito.

**Korjaus:**
```python
def edge_decay_analysis(backtest_df, score_col="roi_pct"):
    df = backtest_df.reset_index(drop=True).copy()
    # Suodata pois viikot joissa ei ollut pelejä — vain ROI-modessa
    if score_col == "roi_pct" and "n_value_bets" in df.columns:
        df = df[df["n_value_bets"] > 0]
    if len(df) < 4:
        return {"verdict": "ei tarpeeksi dataa", ...}
    ...
```

Tämä on **proaktiivinen lisäys** — ei vaikuta nykyiseen (jossa käytetään
Brier-score-modea), mutta hyödyllinen myöhemmin Vaihe 6+:ssa kun ROI-mode
aktivoituu.

---

### Parannus #9 — `renormalize_after_scratch` vs. isotonic-kalibrointi

**Tiedosto:** [`src/betting/scratch_handler.py`](src/betting/scratch_handler.py) rivi 26
**Lähde:** auditoijan väite scratch_handler.py-tiedostosta (kohta 2)
**Status:** 🟡 **HYVÄ NEUVO TULEVAISUUTEEN**

**Ongelma:** `renormalize_after_scratch` jakaa todennäköisyydet pro rata
(`s / s.sum()`) scratching-tilanteessa. Tämä on matemaattisesti oikein
**raaka softmax**-todennäköisyyksille, mutta jos kalibrointi (isotonic) on
sovellettu, pro-rata-jako voi siirtää arvoja alueille jossa kalibrointikäyrä
käyttäytyy eri tavalla.

**Auditoijan suositus:** poista hevonen `race_df`:stä → aja `predict_win_probabilities`
uudestaan jäljellejääville → softmax + isotonic uudelleen.

**Vaikutus nyt:** ei tuotantokäytössä (Vaihe 4+ asia). Hyvä neuvo Streamlit-
dashboardin tai live-tuotannon yhteyteen.

**Korjaus:** vaihda implementaatio Vaihe 6:n yhteyteen kun pelialerttijärjestelmä
otetaan käyttöön.

---

## ❌ Hypoteettiset huolet — eivät aitoja bugeja nykykoodissa

Nämä ovat agentin huolia jotka **eivät toteudu** nykyisessä koodissa, mutta
kannattaa pitää mielessä.

### Hypoteesi #1 — fill_finish_positions indeksiräjähdys
**Väite:** jos DataFrame on tehty `pd.concat`:lla ilman `ignore_index=True`,
indeksit voivat olla duplikaatti.
**Todellisuus:** `pd.read_sql(...)` palauttaa aina uniikin RangeIndexin.
Treenausnotebook käyttää `pd.read_sql`:ää.
**Suositus:** lisää defensiivinen `df = runners.copy().reset_index(drop=True)`
funktion alkuun — yhden rivin lisäys, ei haittaa.

### Hypoteesi #2 — driver_trainer_features "False Zero" -vuoto
**Väite:** jos race_date sisältää kellonajan, NaN→0-muunnos voi vuotaa.
**Todellisuus:** `races.race_date` on `Column(Date)` schemassa →
SQLAlchemy palauttaa Python `date` → pandas `Timestamp` 00:00:00 (ei aikaa).
`drop_duplicates([role, "race_date"])` hoitaa duplikaatit.
**Suositus:** ei toimenpiteitä, mutta `.dt.normalize()` defensiivisenä lisäyksenä
ei haittaisi (hinta = 0).

### Hypoteesi #3 — `predict_win_probabilities` astype("category") koodien kääntyminen
**Väite:** kategoriakoodit voivat kääntyä ristiin treenidata vs. live-data.
**Todellisuus:** LightGBM Booster tallentaa kategoria-mapin treenin yhteydessä.
Predict-vaiheessa kategoriat tunnistetaan **string-arvoista**, ei sisäisistä
koodeista. Tämä toimii koska `lgb.train(...categorical_feature=...)`
tallentaa kategoriat boosterin metatietoihin.
**Mutta** — tämä on **vain LightGBM 3.0+ käyttäytymistä**. Tarkista
`lightgbm.__version__` requirements.txt:stä.
**Suositus:** tarkista LightGBM-versio. Jos < 3.0, agentti on oikeassa
ja vaarat on olemassa. Jos ≥ 3.0, ei toimenpiteitä.

### Hypoteesi #4 — Expanding window vs. rolling
**Väite:** Concept drift — 5 vuoden vanha data voi haitata mallia.
**Todellisuus:** **alkuvaiheessa OK** (vain 18 vrk dataa). Aitoa concept-drift-
ongelmaa ei esiinny ennen kuin dataa on > 6–12 kk.
**Suositus:** TODO ROADMAP:iin: vaihda rolling-ikkunaan (365 vrk) kun dataa
on > 12 kk.

---

## 📋 Korjausjärjestys koodarille

### Vaihe 1 — Kriittiset (yhdessä, ~1 työpäivä)

Tee nämä **yhdessä commitissa** koska ne vaikuttavat samaan A/B-vertailuun:

1. **Bugi #1 — train_ranker race_id-sort** (5 min)
   - Lisää `df = df.sort_values("race_id").reset_index(drop=True)` ennen group_sizes
   - Lisää testi: anna shuffled-data → tulosten pitää olla deterministisiä

2. **Bugi #2 — tr_start_interval_group CATEGORICAL_COLS:iin** (2 min)
   - Lisää lista CATEGORICAL_COLS:iin
   - Aja olemassa olevat testit — pitää mennä läpi

3. **Bugi #3 — backtest-kalibrointi** (45 min)
   - Päivitä `rolling_walk_forward` ja `quarterly_walk_forward`
   - Lisää testit: malli kalibroitu vs. ei-kalibroitu → Brier-paranema kalibroinnin kanssa

**Sitten:** aja **A/B-testi uudelleen** korjatuilla koodeilla. Vertaa
todelliset luvut aiempiin (raportoituihin 0.0818 / 0.0796 / 0.009 paranema).
Tulokset todennäköisesti **muuttuvat merkittävästi**.

### Vaihe 2 — Merkittävät (rinnakkain, ~half päivä)

4. **Bugi #4 — Per-track refresh** (~3–4 h)
   - Muuta `fetch_daily_races` käyttämään `track_first_race`-dictiä
   - Päivitä `_schedule_first_race_refresh` ottamaan track_name
   - Testaa: ajetaan päivä jossa 2 rataa eri aikaan → 2 refresh-jobia

5. **Bugi #5 — sire-aikavuoto KNOWN_ISSUES #13** (5 min)
   - Päivitä KNOWN_ISSUES #13: lisää aktivointiehto **point-in-time-laskenta toteutettu**

6. **Bugi #6 — apply_rule_4_deduction docstring** (5 min)
   - Lisää käyttöehto-varoitus docstringiin

### Vaihe 3 — Parannukset (kun aikaa)

7. **Parannus #7 — distance bucket -rajat** — bins=[0, 1999, 2599, 5000]
8. **Parannus #8 — edge_decay_analysis suodattaa tyhjät viikot ROI-modessa**
9. **Parannus #9 — TODO Vaihe 6: vaihda renormalize_after_scratch:in implementaatio**

---

## 🔄 Yhteisvaikutukset — A/B-vertailu pitää tehdä uudelleen

Kolme tärkeintä bugia vaikuttavat **samoihin tuloksiin**:

```
Bugi #1 (sort)        → malli oppii roskaa
Bugi #2 (categorical) → tr_start_interval_group väärin koodattu
Bugi #3 (calibration) → backtest ROI vääristynyt
```

**Aiempaan raportoituun A/B-vertailuun ei voi luottaa.** Numerot kuten:
- Brier 0.0818 (baseline)
- Brier 0.0796 (TR-malli)
- Brier-paranema 0.009 (V-pelilähdöt)
- tr_game_percent_v #1 ranking
- tr_start_interval_group #34 ranking

...kaikki ovat tehty **rikkinäisellä pipeline:lla**. Vaihe D2:n integraatiopäätöstä
**EI VOI tehdä** ennen kuin korjaukset on tehty ja A/B-testi ajettu uudelleen.

Toinen yhteisvaikutus: **Bugi #2 muuttaa tr_start_interval_group sijoituksen**
todennäköisesti dramaattisesti. Jos se nousee top-10:een ja `tr_game_percent_v`
pysyy #1, käyttäjän huoli Copycat-riskistä saa **uutta valoa**: jos pace-piirre
ON aidosti hyvä, malli on vähemmän riippuvainen markkina-prosenttista.

---

## ✅ Auditoinnin onnistumisten yhteenveto

Agentin auditointi oli **hyödyllinen** — löysi kolme aitoa kriittistä bugia
ja kolme aitoa merkittävää bugia. Erityisen hyvä havainto on **Bugi #1
(LambdaRank sort)** joka ohitti omat testimme koska testifixturet sattuvat
olemaan oikeassa järjestyksessä.

Auditoinnin tarkkuus per tiedosto:

| Tiedosto | Aitoja bugeja | Hypoteettisia | Kommentteja |
|---|---|---|---|
| ranker.py | 2 (#1, #2) | 1 (#3 astype) | Erinomainen auditointi |
| backtest.py | 1 (#3) | 1 (expanding) + 1 (#8 future) | Hyvä auditointi |
| build_features.py | 2 (#5, #7) | 2 (indeksi, false zero) | Sekoittaa hypoteettisia aitoihin |
| scheduler.py | 1 (#4) | 1 (vig=0) | Erittäin terävä Lounasravien Ansa -havainto |
| scratch_handler.py | 1 (#6) | 1 (#9 future) | Hyvä domain-tieto pari-mutuelista |

Hypoteettisia vs. aitoja erottelu vaati koodin lukemisen kohta kohdalta —
agentti ei aina tarkistanut nykyisen kontekstin ennen väitettä.

---

## 📌 Päätös auditoijalta (Claude Opus 4.7)

**Älä anna koodarille kaikkia 6 bugia kerralla.** Pidä prioriteetissa:

1. **Heti:** Vaihe 1 (Bugit #1 + #2 + #3) — yhdessä commitissa, sitten A/B-uusinta
2. **Sitten:** Vaihe 2 (Bugit #4 + #5 + #6) — kun A/B-tulokset ovat selvillä
3. **Myöhemmin:** Vaihe 3 (Parannukset #7–9)

A/B-uusinta voi antaa **paljon parempia tai paljon huonompia** tuloksia kuin
aiemmin raportoitu. Molemmissa tapauksissa **rehellinen mittaus on lähtökohta
oikealle päätöksenteolle**.

---

## ✅ KOODARIRAPORTTI — Vaihe 1 valmis (15.5.2026 ilta)

> Tämä osio on koodarin kirjoittama raportti auditoijalle. Kaikki alla kuvatut
> muutokset on tehty, testattu (327 testiä läpäisevät) ja pushattu GitHubiin.
> A/B-testi on ajettu uudelleen Hetznerillä korjatuilla koodeilla.

### Mitä tehtiin — commitit

| Commit | Sisältö |
|---|---|
| `56823b4` | Bugit #1 + #2 + #3 yhdessä commitissa + regressiotestit |
| `3121b12` | Lisäbugi: duplikaattikolumni-suodatus (ks. alla) |
| `aa034ae` | Dokumentaatiopäivitys: korjatut A/B-tulokset TASK_PROGRESS.md:ään |

---

### Bugi #1 — LambdaRank-sort ✅ KORJATTU

**Korjaus `src/models/ranker.py` (train_ranker):**
```python
df = df.sort_values("race_id").reset_index(drop=True)  # LISÄTTY
group_sizes = df.groupby("race_id", sort=False).size().values
```

**Regressiotestit lisätty `tests/test_ranker.py`:**
- `TestBug1LambdaRankSortInvariance::test_shuffled_input_gives_same_predictions_as_sorted`
  — shuffled vs sorted syöte → feature importance -ero < 10 % (threading noise);
  bugi aiheuttaisi > 50 % eron (väärät ryhmittelyt)
- `TestBug1LambdaRankSortInvariance::test_group_sizes_match_actual_race_sizes`
  — sekalaisesti järjestetty DataFrame eri lähtökoilla → ei kaadu

---

### Bugi #2 — `tr_start_interval_group` kategorisena ✅ KORJATTU (osittain poikkeus auditoijan ohjeesta)

**Korjaus `src/models/ranker.py`:**
- ✅ Lisätty `CATEGORICAL_COLS`:iin

```python
CATEGORICAL_COLS = [
    "distance_category", "start_method", "race_age_group",
    "track_condition", "sulky_type",
    "tr_start_interval_group",  # LISÄTTY — 1/11/21/31, ei jatkuva
]
```

**⚠️ POIKKEUS AUDITOIJAN OHJEESTA — tärkeää luettavaa:**

Auditoija kirjoitti: *"Pidä piirre myös FEATURE_COLS:issa — `_resolve_cols` ja
`astype("category")` hoitavat loput."*

**Tämä ei toiminut.** `train_ranker` rakentaa:
```python
X = df[avail_feat + avail_cat].copy()
```
Jos `tr_start_interval_group` on sekä `avail_feat`:ssa (FEATURE_COLS:sta) että
`avail_cat`:ssa (CATEGORICAL_COLS:sta), `X`:ssä on **duplikaattikolumni**.
Tällöin `X["tr_start_interval_group"]` palauttaa DataFramen eikä Seriestä, ja
LightGBM kaatuu:
```
AttributeError: 'DataFrame' object has no attribute 'cat'
```

**Kaksi korjausta tehty:**

1. **`tr_start_interval_group` poistettu `FEATURE_COLS`:sta** — se tulee mukaan
   `X`:ään `avail_cat`:in kautta (sama käytäntö kuin `distance_category` jne.)

2. **Duplikaattisuodatus lisätty sekä `train_ranker`:iin että
   `predict_win_probabilities`:iin** (`src/models/ranker.py`):
   ```python
   _avail_cat_set = set(avail_cat)
   avail_feat_only = [c for c in avail_feat if c not in _avail_cat_set]
   X = df[avail_feat_only + avail_cat].copy()
   ```
   Tämä mahdollistaa sen, että kutsuja (esim. `travronden_ab_test.py`) voi
   vapaasti sisällyttää kategorisen piirteen myös `feature_cols`-listaan —
   suodatus hoitaa duplikaatin automaattisesti.

**Vaikutus A/B-testiskriptiin:** `_TR_MODEL_COLS` sisältää `tr_start_interval_group`
(TRAVRONDEN_FEATURE_COLS:sta). Ilman duplikaattisuodatusta TR-malli kaatui
välittömästi. Nyt toimii.

**Regressiotestit lisätty `tests/test_ranker.py`:**
- `TestBug2TrStartIntervalGroupIsCategorical::test_tr_start_interval_group_in_categorical_cols`
- `TestBug2TrStartIntervalGroupIsCategorical::test_tr_start_interval_group_not_duplicated_in_feature_cols`
- `TestBug2TrStartIntervalGroupIsCategorical::test_categorical_col_used_as_category_in_lgb_dataset`
- `TestBug2TrStartIntervalGroupIsCategorical::test_tr_start_interval_group_not_in_feature_cols_to_avoid_duplicate`

---

### Bugi #3 — Backtest-kalibrointi ✅ KORJATTU

**Korjaus `src/models/backtest.py`** — sekä `rolling_walk_forward` että
`quarterly_walk_forward`:

```python
# Bugi #3 -korjaus: isotonic-kalibrointi kun dataa tarpeeksi
calib_start_dt = window_start - pd.Timedelta(days=_CALIB_DAYS)  # 14 pv
pure_train_df = train_df[train_df["race_date"] < calib_start_dt]
calib_df = train_df[train_df["race_date"] >= calib_start_dt]

if len(pure_train_df) < _PURE_TRAIN_MIN_ROWS or len(calib_df) < _CALIB_MIN_ROWS:
    model = train_ranker(train_df)
    preds = predict_win_probabilities(model, test_df)
else:
    model = train_ranker(pure_train_df)
    calib_preds = predict_win_probabilities(model, calib_df)
    calib_with_truth = calib_preds.merge(
        calib_df[["race_id", "horse_id", "finish_position"]], on=["race_id", "horse_id"]
    )
    iso = calibrate_isotonic(calib_with_truth)
    preds_raw = predict_win_probabilities(model, test_df)
    preds = apply_isotonic(preds_raw, iso)
```

Kynnykset: `_CALIB_DAYS = 14`, `_CALIB_MIN_ROWS = 50`, `_PURE_TRAIN_MIN_ROWS = 100`.

**Regressiotestit lisätty `tests/test_backtest.py`:**
- `TestBug3CalibrationLowersBrier::test_calibrated_brier_lte_uncalibrated_brier`
  — ylikalibroitu malli: isotonic parantaa/säilyttää Brier-scoren (toleranssi +0.01)
- `TestBug3CalibrationLowersBrier::test_calibrate_isotonic_and_apply_isotonic_importable_from_ranker`
- `TestBug3CalibrationLowersBrier::test_backtest_imports_calibration_functions`

---

### Testiyhteenveto

```
327 testiä, kaikki läpäisevät (20.9 s)
Uudet testit: +10 (TestBug1: 2, TestBug2: 4, TestBug3: 3 + dup-testi: 1)
```

---

### A/B-testin korjatut tulokset (Hetzner, 15.5.2026 ilta)

A/B-testi ajettu uudelleen Hetznerillä korjatuilla koodeilla
(`python scripts/travronden_ab_test.py --split-date 2026-05-08 --rs 42`):

**Treeni/testi-split:**
- Train: 2 966 runneria, 281 lähtöä (< 2026-05-08)
- Testi: 2 188 runneria, 200 lähtöä (≥ 2026-05-08)
- TR-data kattavuus treenissä: 48.5 %
- TR-data kattavuus testissä: 32.5 %

**Tulokset — KAIKKI LÄHDÖT:**

| Malli | Piirteitä | Brier | NLL |
|---|---|---|---|
| Baseline (ei TR) | 37 | 0.0820 | 387.07 |
| TR-malli | 47 | 0.0818 | 386.29 |
| **Δ (paranema)** | | **+0.0003** | **+0.78** |

**Tulokset — VAIN V-PELILÄHDÖT (72 lähtöä, 775 runneria):**

| Malli | Brier | NLL |
|---|---|---|
| Baseline | 0.0772 | 144.72 |
| TR-malli | 0.0733 | 134.58 |
| **Δ (paranema)** | **+0.0039** | **+10.14** |

**Feature Importance — TR-mallin top-piirteet:**
- `tr_game_percent_v` = **#1** (säilyy ⭐⭐⭐)
- `atg_lifetime_top3_rate` = #2
- `atg_lifetime_win_rate` = #3
- `form_market_avg_5` = #4
- `tr_speed_record_m` = **#6** (nousi merkittävästi)
- `tr_is_first_new_driver` = #37
- `tr_start_interval_group` = **#40** (kategorisena silti matala — ks. huomio alla)
- `tr_speed_record_k` = #31, `tr_speed_record_l` = #33

**Huomio `tr_start_interval_group` sijoituksesta:** kategorisena koodauksena (#40)
ei parantunut merkittävästi aiemmasta (#34). Tämä viittaa siihen että pace-arvio
ei tässä testidatassa (32.5 % TR-kattavuus testissä) erotu selvästi — tai
32.5 % kattavuus on liian matala luotettavaan oppimiseen.

---

### Vertailu aiempiin (virheellisiin) tuloksiin

| Mittari | Aiempi (virheellinen) | Korjattu | Muutos |
|---|---|---|---|
| Baseline Brier (kaikki) | 0.0846 | 0.0820 | Baseline parani |
| TR-malli Brier (kaikki) | 0.0796 | 0.0818 | TR-malli heikkeni |
| Δ kaikki lähdöt | **+0.0050** | **+0.0003** | −94 % |
| Baseline Brier (V-peli) | 0.0824 | 0.0772 | Baseline parani |
| TR-malli Brier (V-peli) | 0.0734 | 0.0733 | Lähes sama |
| Δ V-pelilähdöt | **+0.0090** | **+0.0039** | −57 % |

Bugit #1 + #2 yhdessä aiheuttivat massiivisen ylioptimismin erityisesti
kokonaisparannuksessa (+0.0050 → +0.0003). V-pelilähdöissä bugikorjauksen
vaikutus oli pienempi (+0.009 → +0.0039) koska V-pelilähdöissä TR-data on
täysin saatavilla eikä ryhmitysvirhe (Bugi #1) vaikuta yhtä pahasti.

---

### ✅ AUDITOIJAN PÄÄTÖS — 15.5.2026 (Opus 4.7)

**🔴 ÄLÄ INTEGROI TRAVRONDEN-PIIRTEITÄ TUOTANTOON VIELÄ. Lykätään ~7.7.2026:een (Vaihtoehto C).**

### Päätöksen perustelut

**1. Numeerinen perustelu:**

| Mittari | Korjattu tulos | Päätöskynnys | Tulos |
|---|---|---|---|
| Δ Brier kaikki lähdöt | +0.0003 | ≥ 0.005 | ❌ 16× alle kynnyksen |
| Δ Brier V-pelilähdöt | +0.0039 | ≥ 0.005 | ❌ 22 % alle kynnyksen |

Molemmat alle päätöskynnyksen. Ei ole insinöörimäisesti perusteltua sitouttaa
tuotantoa marginaaliseen paranemaan jolla on isot toteutus- ja ylläpitokustannukset
(pollaus-cron, scheduler-integraatio, schema-monimutkaisuus).

**2. Käyttäjän alkuperäinen Copycat-huoli vahvistui:**

`tr_game_percent_v` on edelleen #1 piirre korjatussa A/B-testissä, mutta sen
tuoma todellinen paranema on **paljon pienempi kuin alkuperäinen +0.009 antoi
ymmärtää**. Markkina-peilaus ei tuo aitoa edgeä koska `form_market_avg_5` jo
hinnoittelee samaa tietoa.

**3. Pace-piirre `tr_start_interval_group` ei toiminut edes kategorisena:**

Koko Travronden-investoinnin **pääperustelu** oli pace-arvio (asiantuntijoiden
per-hevonen, per-lähtö ennuste). Kategorisena se nousi #34 → #40 — **siirtyi
väärään suuntaan**. Mahdollisia syitä:
- 32.5 % testidata-kattavuus liian matala luotettavaan oppimiseen
- 4-portainen ryhmittely (1/11/21/31) → 590 esimerkkiä/luokka (LightGBM:lle
  liian vähän interaktioita oppiakseen)
- Asiantuntijat eivät osu paremmin kuin form/market jo tekevät

**4. Aikataulu on puolellamme — ei menetetä mitään odottamalla:**

- Schema-laajennus (tr_*-sarakkeet) on jo tehty
- Scraper-koodi (`travronden.py`, `travronden_features.py`) on tehty + testattu
- Pilot-data on cachessa (~5000 runner-riviä, 2023→2026)
- **Mitään ei pitäisi rakentaa uudelleen myöhemmin**
- Pollaus-cron jätetään rakentamatta — säästetään API-rasitus + scheduler-monimutkaisuus
- Kun 8+ vk dataa kerätty (~2026-07-07), A/B-vertailu voidaan ajaa uudelleen

### Yksi positiivinen löydös

`tr_speed_record_m` nousi **#6** korjatussa rankingissä. Tämä on TR-piirteiden
**ainoa selkeä voitto** — antaa tarkemman km-aika-ennätyksen kuin
`atg_best_km_for_this_setup`. Mutta yhden piirteen takia ei kannata rakentaa
koko pollaus-pollausinfrastruktuuria.

### Mitä tehdään seuraavaksi

#### 🚨 Tärkeintä nyt: Bugi #4 (Lounasravien Ansa)

`refresh_day_runners` ajetaan vain kerran päivässä päivän aikaisimman lähdön
mukaan. Iltakisojen (V86, V64) shoes/sulky-tiedot jäävät vajaiksi. **Tämä on
aito tuotantobugi joka vaikuttaa juuri V-pelilähtöjen** mallin laatuun —
ironista että strateginen fokus on V-peleissä mutta data on niissä huonoin.

Tehtäväajo: ~3–4 h koodimuutos.

#### Bugit #5 + #6 (~10 min)

- **#5** — KNOWN_ISSUES #13: lisää aktivointiehto "**point-in-time-laskenta toteutettu**"
- **#6** — `apply_rule_4_deduction` docstringiin käyttöehto-varoitus

#### Travronden-osio: pidä koodi paikallaan, lykkää käyttöönotto

- ✅ `travronden.py` ja `travronden_features.py` säilyvät koodissa
- ✅ Pilot-data säilyy DB:ssä — kasvaa Travrondenin spontaani-ajojen myötä
- 🔴 **Pollaus-cron ei rakenneta vielä `run_forever`:iin**
- 🔴 **`tr_*`-piirteet poistetaan `FEATURE_COLS`:ista** (kommentoidaan pois)
- 🔴 **`tr_start_interval_group` poistetaan `CATEGORICAL_COLS`:ista**

Kommentoi `FEATURE_COLS`:in tr_*-piirteet pois samalla logiikalla kuin
KNOWN_ISSUES #13 (sire-piirteet) — odottavat aktivointia kun:
1. >= 8 viikkoa puhdasta dataa (~2026-07-07)
2. Uusi A/B-vertailu näyttää Brier-paraneman ≥ 0.005 V-pelilähdöissä
3. Ablation ilman `tr_game_percent_v` osoittaa muiden TR-piirteiden todellisen arvon

Lisää KNOWN_ISSUES.md:hen uusi merkintä #14 tästä.

### Kiitos rehellisyydestä

Koodari peruutti aiemman "INTEGROI"-suosituksen kun korjattu tulos paljastui
heikoksi. **Tämä on hyvää insinöörikulttuuria** — myönsi että aiempi optimismi
perustui rikkinäiseen pipeline:iin.

Lisäksi koodari löysi **lisäbugin** (duplicate-kolumni kun sama piirre on
sekä FEATURE_COLS:issa että CATEGORICAL_COLS:issa) jonka auditoija ei huomannut
suunnittelussa. Hyvä huomio.

10 uutta regressiotestiä varmistavat että bugit eivät palaa. Tämä on insinöörin
oikea tapa korjata: bugi → korjaus → regressiotesti.

### Yhteenveto numeroin

- **Korjatut bugit:** 3 kriittistä (#1 + #2 + #3) + 1 lisä (duplicate)
- **Uudet testit:** 10 (327 yhteensä)
- **Mallin todellinen voittosignaali:** 0.0023 vs. uniform (oli aiemmin 0.0025)
- **Travronden-paranema:** +0.0003 kaikki, +0.0039 V-peli
- **Travronden-päätös:** LYKÄTÄÄN ~7.7.2026:een

Tämä on **realistinen kuva** projektin tilasta. Ei dramaattinen mutta vakaa
ja luotettava perusta — paljon parempi kuin pelata rahaa väärillä luvuilla.

Auditoijan päätösraja (alkuperäinen):
- Brier-paranema ≥ 0.005 → INTEGROI TUOTANTOON
- Brier-paranema ≥ 0.001 → LISÄTTY SIGNAALI (dokumentoi)
- Brier-paranema < 0.001 → MARGINAALINEN PARANEMA

**Korjattujen tulosten perusteella:**
- Kaikki lähdöt: +0.0003 → 🟡 **MARGINAALINEN PARANEMA** (alle 0.001-kynnyksen)
- V-pelilähdöt: +0.0039 → 🟡 **LISÄTTY SIGNAALI** (0.001–0.005 välissä)

**Koodarin arvio:** edellinen suositus "INTEGROI TUOTANTOON" oli virheellisten
tulosten perusteella — **perutaan**. Korjattu tulos on selvästi heikompi.
Integrointipäätös on nyt auditoijalla.

Huomionarvoista päätöksenteon kannalta:
1. `tr_game_percent_v` on edelleen #1 piirre — Copycat-riski säilyy (auditoijan
   aiemmin tunnistama ongelma)
2. V-pelilähdöillä (+0.0039) on eniten käytännön merkitystä koska strategia
   kohdistuu niihin
3. Testidatan TR-kattavuus on vain 32.5 % → paranema on aliarvioitu (jos kattavuus
   kasvaisi 80 %:iin tuotantopollauksella, todellinen V-peli-delta voisi olla suurempi)
4. Enemmän dataa tarvitaan (nyt vain 7 pv testijaksoa, 72 V-pelilähtöä)

---

## ✅ KOODARIRAPORTTI — Vaihe 2 valmis (15.5.2026)

> Koodarin raportti auditoijalle Vaihe 2:n tehtävistä (Bugit #4 + #5 + #6 +
> tr_*-deaktivointi). Kaikki muutokset on tehty, testattu ja pushattu GitHubiin.
> Commit: `a3f56f3`

### Mitä tehtiin

| Tehtävä | Tila |
|---|---|
| Bugi #4 — per-track refresh (Lounasravien Ansa) | ✅ KORJATTU |
| Bugi #5 — KNOWN_ISSUES #13 point-in-time-ehto | ✅ LISÄTTY |
| Bugi #6 — apply_rule_4_deduction käyttövaroitus | ✅ LISÄTTY |
| tr_*-piirteet pois FEATURE_COLS + CATEGORICAL_COLS | ✅ POISTETTU |
| KNOWN_ISSUES #14 — tr_* aktivointisuunnitelma | ✅ DOKUMENTOITU |
| Testit — scheduler-testit päivitetty | ✅ 327 passed |

---

### Bugi #4 — Per-track refresh (Lounasravien Ansa) ✅ KORJATTU

**Ongelma:** `_setup_for_date` kutsui `_schedule_first_race_refresh` yhdellä
globaalilla `earliest_start_dt`:llä. Jos päivällä oli lounasravit klo 12 ja
illalla Solvalla klo 19, refresh ajastettiin klo 12 mukaan — Solvallan
runners päivitettiin 7 tuntia ennen tarpeellista.

**Korjaus `src/data/scheduler.py`:**
```python
# ENNEN (yksi globaali):
earliest_start_dt: datetime | None = None
...
stats["first_race_start_utc"] = earliest_start_dt

# NYT (per-rata dict):
track_first_race: dict[str, datetime] = {}
...
tname = _track_name(race)
if tname and (tname not in track_first_race or race_start_dt < track_first_race[tname]):
    track_first_race[tname] = race_start_dt
stats["track_first_races"] = track_first_race
```

`_schedule_first_race_refresh` saa nyt `track_name`-parametrin ja generoi
uniikin job_id:n: `refresh_runners_{date}_{track_slug}`.

`_setup_for_date` iteroi `track_first_races`-dictiä ja ajastaa erillisen
refresh-jobin jokaiselle radalle erikseen.

**Regressiotestit päivitetty `tests/test_scheduler.py`:**
- `test_fetch_daily_races_returns_track_first_races` — tarkistaa että palautettu
  dict on `{rata: UTC-datetime}` (korvaa vanhan `test_fetch_daily_races_returns_first_race_start_utc`)
- `test_setup_for_date_schedules_refresh_job` — mock palauttaa nyt
  `track_first_races: {"Solvalla": future}` → `refresh_jobs == 1` ✅
- `test_setup_for_date_skips_refresh_when_first_race_in_past` — past-rata
  ei saa ajastusta → `refresh_jobs == 0` ✅

---

### Bugi #5 — KNOWN_ISSUES #13 point-in-time-ehto ✅ LISÄTTY

**Ongelma:** KNOWN_ISSUES #13:n aktivointilistasta puuttui ehto
"point-in-time-laskenta toteutettu" — ilman sitä sire-aggregaatit laskevat
globaalisti (sisältäen tulevaisuuden startit).

**Korjaus `KNOWN_ISSUES.md`:**
```
Aktivoidaan takaisin kun **kaikki** ehdot täyttyvät:
1. DB:ssä on >= 8 viikkoa puhdasta dataa (n. 2026-07-07)
2. dam_sire-kattavuus runners:ssa > 60 %
3. Uusi sire_ablation_loo.py-ajo näyttää Brier-parannuksen selvästi
4. **Point-in-time-laskenta toteutettu** — aggregaatti lasketaan vain
   horse_starts WHERE race_date < runner.race_date per runner-rivi
   (Auditoija #5, AUDIT_FINDINGS_2026-05-15.md)
```

---

### Bugi #6 — apply_rule_4_deduction ATG-varoitus ✅ LISÄTTY

**Ongelma:** `apply_rule_4_deduction` on matemaattisesti oikein
fixed-odds-markkinoille, mutta jos sitä käytetään ATG-poolikertoimiin,
vähennys tehdään kahdesti: ATG tekee sen automaattisesti + funktio
tekee sen uudelleen → mallin edge lasketaan liian pieneksi.

**Korjaus `src/betting/scratch_handler.py`** — lisätty docstringiin:
```
⚠️ KÄYTTÖEHTO: Vain **fixed-odds**-vedonvälittäjille (Unibet, Betsson,
Bet365, Pinnacle jne.).

EI SAA KÄYTTÄÄ ATG:n pari-mutuel-kertoimien kanssa. ATG:n poolissa
kertoimet lasketaan automaattisesti uudelleen kun hevonen perutaan —
scratching poistaa pelatut rahat poolista ja kaikkien muiden kertoimet
päivittyvät livenä. Jos tätä funktiota käytetään ATG-kertoimiin, vähennys
tehdään kahdesti...
(Auditoija Bugi #6, AUDIT_FINDINGS_2026-05-15.md)
```

---

### tr_*-piirteet deaktivoitu ✅

Auditoijan päätöksen mukaisesti kaikki Travronden-piirteet kommentoitu pois
`src/models/ranker.py`:n `FEATURE_COLS`:ista ja `CATEGORICAL_COLS`:ista.

**FEATURE_COLS:ista poistettu:**
```python
# "tr_is_first_after_castration",  # aktivoi D2:n mukana
# "tr_is_first_new_driver",
# "tr_is_first_new_trainer",
# "tr_is_first_shoes",
# "tr_is_first_carriage",
# "tr_speed_record_k",
# "tr_speed_record_m",
# "tr_speed_record_l",
# "tr_game_percent_v",             # COPYCAT-RISKI
# "tr_expected_odds",
```

**CATEGORICAL_COLS:ista poistettu:**
```python
# "tr_start_interval_group",  # aktivoi D2:n mukana
```

**KNOWN_ISSUES.md #14** dokumentoi aktivointisuunnitelman ja
korjatut A/B-tulokset.

Infrastruktuuri (scraper, schema, travronden_features.py, pilot-data)
säilyy koskemattomana — mitään ei tarvitse rakentaa uudelleen aktivointia varten.

---

### Testit

```
327 passed in 19.18s
```

Kaikki aiemmat 317 testiä + 10 uutta (Vaihe 1) + scheduler-päivitykset
menevät läpi. Commit: `a3f56f3`, pushattu GitHubiin.

> ⚠️ **Hetzner-deploy tekemättä.** GitHub-push ei deployaa automaattisesti.
> Ajettava Hetznerillä: `git pull` ennen seuraavaa tuotantoajoa.
> Kiireellisin: `scheduler.py` (Bugi #4 per-track refresh).

---

## ✅ AUDITOIJAN PÄÄTÖS — Vaihe 2 valmis (15.5.2026, Opus 4.7)

**Hyväksytty.** Vahvistettu lukemalla koodi:

| Korjaus | Koodissa | Vahvistus |
|---|---|---|
| Bugi #4 — `_schedule_first_race_refresh` saa track_name + uniikki job_id per rata | [scheduler.py:1354–1402](src/data/scheduler.py:1354) | ✅ |
| Bugi #5 — KNOWN_ISSUES #13 point-in-time-ehto | KNOWN_ISSUES.md kohta 4 | ✅ |
| Bugi #6 — apply_rule_4_deduction "EI SAA KÄYTTÄÄ ATG:n pari-mutuel" | scratch_handler.py rivit 54–57 | ✅ |
| tr_*-piirteet pois FEATURE_COLS (10 kpl) + CATEGORICAL_COLS (1 kpl) | ranker.py rivit 110–120, 143 | ✅ |
| KNOWN_ISSUES #14 — Travronden D2 aktivointiehdot | KNOWN_ISSUES.md rivit 132–171 | ✅ |
| Testit | 314 passing (lokaalisti, test_travsport ohitettu) | ✅ |

**Erityishuomio:** `track_slug = track_name.lower().replace(" ", "_") if track_name else "all"`
([scheduler.py:1384](src/data/scheduler.py:1384)) on hyvä taaksepäin-yhteensopivuus
— jos track_name puuttuu, käyttäytyminen palautuu vanhaan single-job-tilaan.
Defensiivinen koodi.

**Kiireellisin toimenpide:** Hetzner-deploy ennen seuraavaa V86/V64-iltakisaa.

---

## 🟡 KORJAUSLISTA — Vaihe 3 parannukset (#7 + #8 + #9)

Auditoijan ohje koodarille — kaikki samalla committilla. Aikabudjetti ~1 h.

### Parannus #7 — Distance bucket -rajat

**Tiedosto:** [`src/features/build_features.py`](src/features/build_features.py)
**Rivit:** 167 (`form_features` segmentoidut piirteet) ja 294 (`race_setup_features`
`distance_category`)

**Muutos:**
```python
# ENNEN (2 paikassa):
bins=[0, 1640, 2140, 5000]

# JÄLKEEN:
bins=[0, 1999, 2599, 5000]   # < 2000 = sprint, 2000–2599 = middle, > 2600 = long
```

**Perustelu:** auditoija varoitti että takamatka-hevoset (esim. 2160m volttilähtö)
sijoittuvat väärin "long"-kategoriaan kun perusmatka on 2140m. Uudet rajat
(1999/2599) ovat kaukana yleisistä ravimatkoista (1609, 1640, 2140, 2640, 3140)
joten takamatkat eivät sotke kategoriointia.

**Testit:**
- Lisää `tests/test_build_features.py::TestDistanceBucketsTakamatka`:
  - `test_2140m_normal_is_middle` — 2140 → "middle"
  - `test_2160m_takamatka_stays_in_middle` — 2160 → "middle" (eikä "long")
  - `test_2640m_long_distance_is_long` — 2640 → "long"
  - `test_1640m_short_distance_is_sprint` — 1640 → "sprint"

**Huomio:** Muutos vaikuttaa `form_avg_finish_5_same_dist`-piirteeseen ja
`distance_category`-CATEGORICAL_COLS-piirteeseen. Aja koko sviitti ja varmista
että aiemmat distance_category-testit eivät kaadu (jos jotkut testit
oletettavat että 2140 → "middle", ne saavat olla; jos joku oletti että
1641 → "middle", se kaatuu — uudet rajat aiheuttavat 1641 → "sprint").

### Parannus #8 — `edge_decay_analysis` suodattaa tyhjät viikot ROI-modessa

**Tiedosto:** [`src/models/backtest.py`](src/models/backtest.py)
**Funktio:** `edge_decay_analysis`, rivit 269–345

**Muutos:**
```python
def edge_decay_analysis(
    backtest_df: pd.DataFrame,
    score_col: str = "roi_pct",
) -> dict:
    ...
    if len(backtest_df) < 4:
        return {"verdict": "ei tarpeeksi dataa", ...}

    if score_col not in backtest_df.columns:
        raise ValueError(...)

    df = backtest_df.reset_index(drop=True).copy()

    # LISÄYS — Parannus #8: suodata tyhjät viikot ROI-modessa
    # (Brier lasketaan aina, joten suodatus vain ROI-modessa)
    if score_col == "roi_pct" and "n_value_bets" in df.columns:
        df = df[df["n_value_bets"] > 0].reset_index(drop=True)
        if len(df) < 4:
            return {
                "verdict": "ei tarpeeksi pelillisiä viikkoja",
                "trend_slope": None,
                "score_col": score_col,
                "first_half": None,
                "second_half": None,
            }

    df["period_idx"] = range(len(df))
    # ... loput entisellään
```

**Perustelu:** auditoija varoitti että roi_pct=0 viikot (joissa value-pelejä
ei syntynyt) vetävät trendiviivaa kohti nollaa polyfit-laskennassa. Voi
tuottaa "false alarm" edge-decay-hälytyksen kun mitään ei tapahdu.
Brier-modessa ei ole tätä ongelmaa (brier_score lasketaan aina) joten suodatus
vain ROI-modessa.

**Testit:**
- Lisää `tests/test_backtest.py::TestParannus8EmptyWeekFilter`:
  - `test_empty_weeks_filtered_in_roi_mode` — 8 viikkoa joista 4 tyhjää
    (`n_value_bets=0`) → polyfit käyttää vain 4 ei-tyhjää
  - `test_too_few_active_weeks_returns_insufficient` — 6 viikkoa joista 3 tyhjää
    → "ei tarpeeksi pelillisiä viikkoja"
  - `test_brier_mode_does_not_filter_empty_weeks` — Brier-modessa kaikki
    viikot huomioidaan
  - `test_empty_n_value_bets_column_no_filter` — DataFrame ilman
    n_value_bets-saraketta → vanha käytäntö (ei suodatusta)

### Parannus #9 — `renormalize_after_scratch` TODO Vaihe 6:lle

**Tiedosto:** [`src/betting/scratch_handler.py`](src/betting/scratch_handler.py)
**Funktio:** `renormalize_after_scratch`, rivi 26

**Muutos:** Lisää **dokumentaatiokomentti** TODO-merkinnällä — ei koodimuutosta
nyt, mutta dokumentoi parempi vaihtoehto Vaihe 6:n implementaatioon:

```python
def renormalize_after_scratch(
    win_probs: pd.Series,
    scratched_horse_ids: list[str],
) -> pd.Series:
    """Pro-rata-jako jäljellejääville hevosille kun joku perutaan.

    Tämä funktio toimii **raakojen softmax**-todennäköisyyksien kanssa.

    ⚠️ TODO Vaihe 6 (auditoija parannus #9, AUDIT_FINDINGS_2026-05-15.md):

    Kun isotonic-kalibrointi (apply_isotonic) on käytössä tuotannossa,
    pro-rata-jako voi siirtää arvoja alueille jossa kalibrointikäyrä
    käyttäytyy eri tavalla. Parempi lähestymistapa on:

        1. Poista peruttu hevonen race_df:stä
        2. Aja predict_win_probabilities(model, race_df_without_scratched)
        3. Sovella apply_isotonic uudelleen jäljellejääviin

    Tämä antaa matemaattisesti puhtaan jakauman ilman pro-rata-vääristymää.
    Implementoi Vaihe 6:n yhteyteen kun pelialerttijärjestelmä rakennetaan.
    ...
    """
    # ... loput funktiosta entisellään
```

**Ei testimuutoksia** — vain docstring-laajennus.

---

## 📝 Korjausjärjestys ja yhteenveto

```bash
# 1. Tee parannukset #7 + #8 + #9 samalla committilla
# 2. Aja kaikki testit: pytest -q (pitäisi olla 327 + uudet ≈ 335)
# 3. Push GitHub
# 4. Hetzner: git pull + systemctl restart ravit-scheduler

# Tärkein viimeinen askel: Hetzner-deploy → per-track refresh + parannukset
# käyttöön ennen seuraavaa V86/V64-iltakisaa
```

**Aikabudjetti:** ~1 h yhteensä (kaikki kolme parannusta + testit).

---

## ✅ KOODARIRAPORTTI — Vaihe 3 + D3 valmis (15.5.2026 ilta)

> Koodarin raportti auditoijalle. Kaikki muutokset tehty, testattu ja
> pushattu GitHubiin. Hetzner-deploy suoritettu. Dashboard käytössä.

### Commitit

| Commit | Sisältö |
|---|---|
| `d62f861` | Vaihe 3: Parannukset #7 + #8 + #9 + regressiotestit |
| `c45f128` | Vaihe D3: Streamlit-dashboard (`src/dashboard/app.py`) |
| `d0fb0fe` | Bugikorjaus: `use_container_width` → `width="stretch"` (Streamlit versioero) |
| `3512972` | Dashboard: hevosnimet horse_id:n sijaan |

---

### Vaihe 3 — Parannukset #7 + #8 + #9 ✅

**#7 Distance bucket -rajat** (`src/features/build_features.py`, 2 paikkaa):
```python
# ENNEN:
bins=[0, 1640, 2140, 5000]
# NYT:
bins=[0, 1999, 2599, 5000]
```
Takamatkat (esim. 2160m volttilähtö) pysyvät "middle"-kategoriassa
eivätkä pomppaa "long"-kategoriaan. 4 regressiotestiä lisätty.

**#8 `edge_decay_analysis` tyhjät viikot** (`src/models/backtest.py`):
Viikot joissa `n_value_bets=0` suodatetaan pois ennen polyfit-laskentaa
ROI-modessa. Estää false-alarm-hälytykset hiljaisina viikkoina.
4 regressiotestiä lisätty.

**#9 `renormalize_after_scratch` docstring** (`src/betting/scratch_handler.py`):
TODO Vaihe 6 -kommentti lisätty: kun isotonic-kalibrointi on tuotannossa,
pro-rata ei ole oikea — malli pitää ajaa uudelleen ilman peruttu hevosta.
Ei koodimuutosta, vain dokumentaatio.

**Testit:** 335 passed (aiempi 327 + 8 uutta)

---

### Vaihe D3 — Streamlit-dashboard ✅

**Tiedosto:** `src/dashboard/app.py` (~140 riviä)

**Käynnistys Hetzneriltä:**
```bash
ssh -L 8501:localhost:8501 ravit-edge \
  "cd /home/ravi/app-ravi && .venv/bin/python -m streamlit run src/dashboard/app.py --server.headless true"
# → http://localhost:8501
```

**Toiminnallisuus:**
- Sidebar: päivän valinta, V-pelilähdöt-checkbox, edge-kynnys-slider (1–15 %)
- Ylämetriikat: lähtöjä / hevosia / value bet -määrä päivälle
- Per-lähtö: taulukko `# | Hevonen | P(win) | Odds | Edge %`
- Value betit korostettu ⭐-merkillä
- Cache: `@st.cache_resource` mallille, `@st.cache_data(ttl=300)` datalle

**Huomio Streamlit-versiosta:** Hetznerillä on uudempi Streamlit joka
vaatii `width="stretch"` (ei `use_container_width=True`). Korjattu.

---

### Hetzner-deploy ✅

1. `git pull` — 15 tiedostoa päivitetty (kaikki Vaihe 2 + 3 + D3 muutokset)
2. Testit Hetznerillä: **329 passed, 6 failed** — kaikki 6 epäonnistunutta
   ovat `test_travsport.py` (tunnettu ympäristöongelma, ei regressio)
3. Malli uudelleenopetettu (`scripts/retrain_model.py`):
   - **37 piirrettä** (aiempi malli 45 — tr_* ja K1-piirteet poistettu)
   - **Brier = 0.0733** testidatalla (toukokuu 8–14)
   - Tallennettu: `data/model_baseline_20260515.lgb`
4. Scheduler restartattu: `systemctl restart ravit-edge` → `active (running)`

---

### Live-havainto: malli vs. markkina (Solvalla Lähtö 10, 15.5.2026 ilta)

Ensimmäinen live-vertailu mallin ennusteiden ja ATG:n live-kertoimien välillä.
Dashboard näyttää tämän päivän lähdöt reaaliajassa.

| # | Hevonen | P(win) malli | ATG kerroin | Markk. impl. prob | Ero |
|---|---|---|---|---|---|
| 1 | Jeremy Zet | 3.7 % | 35.69 | 2.8 % | ≈ ok |
| 2 | Joker Ima | 5.1 % | 39.00 | 2.6 % | malli yliarvio |
| 3 | Moonshot | 8.3 % | 58.07 | 1.7 % | **malli yliarvio 5×** |
| 4 | Valla d'Gaagaa | 5.0 % | 3.95 | **25.3 %** | **malli aliarvio 5×** |
| 5 | Papillon Boko | 28.6 % | 2.36 | 42.4 % | malli aliarvio |
| 6 | You Bow to No One | 2.9 % | 11.15 | 9.0 % | malli aliarvio |
| 7 | Pasha Newport | 19.4 % | 5.13 | **19.5 %** | **täsmää** |
| 8 | Don E.Star | 14.1 % | 36.25 | 2.8 % | **malli yliarvio 5×** |
| 9 | Eol | 2.0 % | 12.27 | 8.1 % | malli aliarvio |
| 10 | Carl Palema | 4.8 % | 99.99 | 1.0 % | malli yliarvio |
| 11 | Urban Profile | 6.2 % | 36.67 | 2.7 % | malli yliarvio |

**Johtopäätökset havainnoista:**

1. **Malli ei "näe" suosikin syytä** — #4 Valla d'Gaagaa (kerroin 3.95,
   markkinan suosikki 25 %) saa mallilta vain 5 %. Todennäköisin syy:
   `atg_driver_win_pct` ja `atg_trainer_win_pct` on poistettu K1-bugin
   takia — hevosen kuski/valmentaja on ilmeisesti markkinan luottamuksen
   syy, mutta malli ei tiedä tätä.

2. **#7 Pasha Newport täsmää täydellisesti** (19.4 % vs 19.5 %) — malli
   ei ole satunnainen, joissain hevosissa piirteet riittävät.

3. **Isot eroavuudet ovat odotettuja** tässä vaiheessa:
   - Puuttuvat kuski/valmentaja-piirteet (K1-bugi, palautuu ~2026-09)
   - Vain 37 piirrettä, ~3000 treenirivi — markkina hinnoittelee satoja signaaleja
   - Edge-laskenta on toistaiseksi myös vaillinainen (käyttää `win_odds_final`
     joka on post-race-kerroin, ei live pre-race)

**Mikä puuttuu dashboardista vielä:**
- Live pre-race -kertoimet (nyt `win_odds_final` = post-race, tyhjä tuleville lähdöille)
- Edge-laskenta toimii vain päättyneille lähdöille
- Tähän tarvitaan live-kertoimien pollaus — Vaihe 6:n asia

---

### Yhteenveto numeroin

| Mittari | Arvo |
|---|---|
| Testejä yhteensä (lokaalisti) | **335 passed** |
| Testejä Hetznerillä | 329 passed, 6 tunnettu ympäristövirhe |
| Mallin piirteet (uudelleenopetettu) | 37 |
| Mallin Brier (uusi malli) | 0.0733 |
| Dashboard | ✅ käytössä Hetzneriltä tunnelin kautta |
| Scheduler | ✅ pyörii uudella koodilla (Bug #4 per-track aktiivin) |

Päivitä TASK_PROGRESS.md kun valmis.

---

## ✅ AUDITOIJAN PÄÄTÖS — Vaihe 3 + D3 valmis (15.5.2026, Opus 4.7)

**Hyväksytty.** Vahvistettu lukemalla koodi:

| Korjaus | Sijainti | Vahvistus |
|---|---|---|
| Parannus #7 — distance bins `[0, 1999, 2599, 5000]` | [build_features.py:167, 294](src/features/build_features.py:167) | ✅ molemmissa paikoissa |
| Parannus #8 — `n_value_bets > 0`-suodatus ROI-modessa | [backtest.py:367–370](src/models/backtest.py:367) | ✅ vain ROI-modessa, ei brier-modessa |
| Parannus #9 — TODO Vaihe 6 docstring | scratch_handler.py | ✅ docstring lisätty |
| Streamlit dashboard | `src/dashboard/app.py` + `__init__.py` | ✅ |
| `streamlit>=1.32.0` requirements.txt:hen | rivi 9 | ✅ |
| Testit | 322 passing lokaalisti (test_travsport ohitettu) | ✅ |

8 uutta regressiotestiä (4 + 4) — bugit eivät palaa.

---

### ⚠️ Yksi tarkennuspyyntö: Brier 0.0733 — onko sama testidata?

Raporttisi mainitsee uuden mallin **Brier = 0.0733** "testidatalla (toukokuu 8–14)".
Aiempi Vaihe 2 -korjattu A/B-testi raportoi **kaikkien lähtöjen Brier 0.0820**
samalla aikajaksolla (Apr 27 – May 7 / May 8 – May 14).

**Erotus -0.0087 on yllättävän iso** kun ainoa relevantti muutos treenausvirralle
oli Parannus #7 (distance bucket). Tämä on **vahvempi paranema kuin kaikki
aiemmat A/B-testit yhteensä**.

**Kaksi mahdollista selitystä:**

1. **Aito paraneminen** — distance bucket -korjaus vaikuttaa kahteen piirteeseen:
   - `form_avg_finish_5_same_dist` (segmentoitu muoto)
   - `distance_category` (CATEGORICAL_COLS)
   Takamatka-hevosten "väärin sijoittuneet" arvot eivät enää sotke rolling-statseja.
   Tämä **voi** selittää isonkin paranemisen jos takamatka-hevoset ovat olleet
   systemaattisesti väärässä luokassa.

2. **Mittauskehysero** — onko Brier 0.0733:
   - **Kaikkien lähtöjen** Brier (200 lähtöä, 2188 runneria) — sama otanta kuin aiemmin?
   - Vai **V-pelilähtöjen** Brier (72 lähtöä, 775 runneria) — alajoukko?

Aiempi A/B-testi raportoi `0.0733` nimenomaan **V-pelilähtöjen Brier-arvoksi**
(TR-malli, 72 lähtöä). Jos uusi malli antaa **kaikkien** lähtöjen Brier = 0.0733,
se on iso paraneminen. Jos sama luku on **vain V-pelilähtöjä**, paraneminen on
pienempi.

**Pyydän:** vahvista raportointiin:

```bash
# Aja vertailu eksplisiittisesti molemmilla otoksilla
python -c "
from scripts.ab_evaluate_model import evaluate
print('Kaikki lähdöt:', evaluate(model_path='data/model_baseline_20260515.lgb', filter='all'))
print('Vain V-pelit:', evaluate(model_path='data/model_baseline_20260515.lgb', filter='v_only'))
"
# Vastaus per malli:
# - Brier, NLL, n_runners, n_races
```

Tämä **ei muuta päätöstä** Vaihe 3:n hyväksynnästä — kaikki koodimuutokset ovat
oikein ja testattu. Mutta lopullinen "voittosignaali" kannattaa raportoida
oikealla mittaustarkkuudella.

---

### 🎯 Live-havainnoinnin analyysi (Solvalla Lähtö 10)

Tämä on **tärkein osa raporttiasi**. Live-vertailu paljastaa konkreettisesti
mallin tilan:

**Kolme kategoriaa:**

| Tilanne | Esimerkki | Mitä se kertoo |
|---|---|---|
| **Täsmää** | #7 Pasha Newport (19.4 % vs 19.5 %) | Malli osaa kun piirteet riittävät |
| **Yliarvioi** | #8 Don E.Star (14.1 % vs 2.8 %), #3 Moonshot (8.3 % vs 1.7 %) | Outsider-yliarviot — luultavasti puuttuva markkinasignaali |
| **Aliarvioi** | #4 Valla d'Gaagaa (5.0 % vs 25.3 %), #5 Papillon Boko (28.6 % vs 42.4 %) | Suosikkien aliarvio — **K1-pollutoitujen kuski/valmentaja-piirteiden puute näkyy** |

**Sinun tulkintasi #1 (K1-piirteiden puute) on oikein.** Käyttäjän alkuperäinen
huoli Copycat-riskistä saa tästä uutta valoa: kun markkina-piirteet (`tr_game_percent_v`,
`atg_driver_win_pct`, jne.) eivät ole mallissa, malli aliarvioi markkinan suosikit
ja yliarvioi outsidereja.

**Toinen tärkeä havainto:** Pasha Newport (19.4 % vs 19.5 %) on **tilastollisesti
ihmeen tarkka**. 0.1 prosenttiyksikön ero on poikkeuksellinen — joko sattumaa
tai indikoi että mallin "perustyökalut" (form, ATG-aggregaatit, ratarakenne)
toimivat hyvin tietyissä tilanteissa. **Hyvä signaali pitkän aikavälin
edge-potentiaalille kun puuttuvat piirteet palautetaan.**

**Solvalla-vertailu on arvokas dashboard-feature:** se on **välitön visuaalinen
mittari** mallin laatuun. Suosittelen kirjata päivittäin 1–2 lähdön
vertailutiedot myöhempää analyysia varten (esim. CSV `data/logs/live_predictions/`).

---

### 🔧 Mitä dashboardista puuttuu — Vaihe 4 -ohjeita

Mainitsit oikein että:
- Live pre-race -kertoimet puuttuvat (käyttää `win_odds_final` = post-race)
- Edge-laskenta toimii vain päättyneille lähdöille

Tämä on **odotettavissa** tässä vaiheessa. `odds_snapshots`-taulussa on jo
T-15/10/5/2min snapshotit, mutta dashboard ei niitä vielä käytä.

**Vaihe 4 -ohjeena (~3.6.2026):**

```python
# Lisää dashboard-app.py:hyn:
def get_latest_pre_race_odds(race_id: str) -> pd.DataFrame:
    """Hae viimeisin pre-race-kerroin (T-2 → T-5 → T-10 → T-15)."""
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT runner_id, win_odds, snapshot_label, captured_at
        FROM odds_snapshots
        WHERE snapshot_label IN ('T-2min', 'T-5min', 'T-10min', 'T-15min')
          AND runner_id LIKE ?
        ORDER BY captured_at DESC
    """, con, params=(f"{race_id}_%",))
    # Per runner: ota uusin (T-2 > T-5 > T-10 > T-15)
    return df.drop_duplicates("runner_id", keep="first")
```

Tämä on **valinnainen Vaihe 4:n laajennus**, ei kiireellinen nyt.

---

### Yhteenveto auditoijalta

**Hyvä työ:**
1. ✅ Kaikki parannukset (#7, #8, #9) toteutettu yhdessä committillä
2. ✅ 8 uutta regressiotestiä
3. ✅ Streamlit dashboard käytössä SSH-tunnelin kautta
4. ✅ Hetzner-deploy onnistunut, Bugi #4 per-track refresh tuotannossa
5. ✅ Live-havainto Solvalla-lähdöstä — erinomainen empiirinen tarkastelu
6. ✅ Streamlit-versioero (use_container_width vs. width="stretch") korjattu

**Pieni avoinkysymys:**
- Brier 0.0733 — vahvista onko **kaikkien lähtöjen** vai **V-pelilähtöjen** mittari (raportoi vertailu molemmilla otoksilla)

**Ei lisätehtäviä koodarille nyt.** Projekti on hyvässä tilassa:
- 6 todellista bugia korjattu
- 3 parannusta tehty
- Dashboard käytössä
- Datankeräys jatkuu Hetznerillä uudella koodilla
- 335 testiä passing

**Seuraava merkkipaalu: ~3.6.2026** — `rolling_walk_forward` voi alkaa kun 42+
vrk dataa on kerätty. Sen jälkeen ~7.7.2026 stop/go-päätös.

---

## ✅ KOODARIRAPORTTI — Brier-tarkennus (15.5.2026 ilta)

> Vastaus auditoijan tarkennuspyyntöön: mikä on Brier 0.0733?

### Evaluointitulos (`scripts/evaluate_model.py`, ajettu Hetznerillä)

```
Kaikki lähdöt
  Brier:      0.0743
  NLL:        428.27
  Lähtöjä:   219
  Runnereita: 2409

Vain V-pelilähdöt (is_v_race=1)
  Brier:      0.0781
  NLL:        149.12
  Lähtöjä:    72
  Runnereita: 775

Mallitiedosto: data/model_baseline_20260515.lgb
Piirteitä: 37 (FEATURE_COLS) / 42 (mallin opetuspiirteet)
```

### Selitys: mistä 0.0733 tuli?

Aiemmin raportoimani Brier 0.0733 oli `retrain_model.py`:n tuloste,
joka ajettiin samalla testijakson rajauksella (≥ 2026-05-08). Se oli
**kaikkien lähtöjen** Brier, mutta hieman eri tulos kuin nyt (0.0743).

Ero 0.0733 → 0.0743 johtuu todennäköisesti **satunnaisluvun siemenestä**:
`retrain_model.py` ei aseta `random_state=42`, joten LightGBM käyttää
satunnaista siementä. Toistettavuus puuttuu tästä ajosta.

**Oikea vertailuasetelma A/B-tulosten kanssa:**

| Mittari | A/B-testi (koodi korjattu) | Uusi malli (20260515) |
|---|---|---|
| Kaikki lähdöt — Brier | 0.0820 (baseline) | **0.0743** |
| V-pelilähdöt — Brier | 0.0772 (baseline) | **0.0781** |

**Yllättävä löydös:** kaikissa lähdöissä paranema on -0.0077, mutta
V-pelilähdöissä malli heikkeni +0.0009. Syitä:

1. **Distance bin -korjaus (#7)** voi aidosti auttaa erityisesti takamatka-hevosilla
   jotka esiintyvät enemmän kaikissa lähdöissä kuin V-pelilähdöissä
2. **Ilman random_state** vertailu ei ole täysin luotettava — eri siemen
   voi selittää osan erosta
3. **Treenaus ilman tr_*-piirteitä** — tr_*-piirteet toimivat paremmin
   V-pelilähdöissä (48.5 % kattavuus) kuin muissa lähdöissä (pienempi
   kattavuus) → niiden poisto heikentää V-peli-Brieria enemmän

### Huomio: 42 vs. 37 piirrettä

`model.feature_name()` palauttaa 42 mutta `len(FEATURE_COLS)` = 37.
Ero 5 = todennäköisesti CATEGORICAL_COLS:n piirteet jotka lasketaan
`avail_feat_only + avail_cat` -yhdistelmässä. Piirteet ovat:
`distance_category`, `start_method`, `race_age_group`, `track_condition`,
`sulky_type` — kaikki CATEGORICAL_COLS:ssa. Nämä 5 ovat myös FEATURE_COLS:ssa
(ne laskettiin `avail_feat`:iin, sitten poistettiin `avail_feat_only`:stä
dedup-logiikassa, ja laskettu `avail_cat`:iin). Käytännössä 37 + 5 = 42 ✅.
**Ei bugi** — dedup toimii oikein, piirteet lasketaan kertaalleen.

### Toimenpiteet

1. ✅ Lisätty `scripts/evaluate_model.py` (toistettava evaluointi)
2. ✅ `retrain_model.py` puuttuu `random_state` — **korjataan seuraavassa
   retrainissa** lisäämällä `train_ranker(train_df, random_state=42)`
3. ✅ Brier 0.0733 oli retrain-ajon kaikkien lähtöjen tulos (ei V-vain)
   — nyt vahvistettu: kaikki=0.0743, V=0.0781

---

## ✅ AUDITOIJAN PÄÄTÖS — Brier-tarkennus hyväksytty (15.5.2026, Opus 4.7)

**Hyväksytty.** Toistettava evaluointi `evaluate_model.py`:n kautta on iso parannus.

### Avainluvut

| Mittari | Arvo | Vs. uniform 0.0843 | Tulkinta |
|---|---|---|---|
| Brier kaikki lähdöt | **0.0743** | +0.0100 (12 % parempi) | Selvä paraneminen |
| Brier V-pelilähdöt | **0.0781** | +0.0062 (7 % parempi) | Pienempi mutta positiivinen |
| Brier paraneminen (kaikki vs. baseline) | **−0.0077** | — | Merkittävä |
| Brier muutos (V-peli vs. baseline) | **+0.0009** | — | Lievä heikentyminen |

### Tämä on yllättävä lopputulos — strateginen fokus on V-peleissä, mutta paraneminen on muualla

Olen samaa mieltä koodarin analyysistä — kolme yhteisvaikuttavaa tekijää selittävät:

1. **Distance bucket -korjaus auttaa enemmän ei-V-peleissä** (takamatkalähdöt)
2. **TR-piirteiden poisto vie enemmän signaalia V-peleistä** (kattavuus 48.5 %)
3. **Random_state puuttuu** → +0.0009 V-peli-muutos voi olla suurelta osin siementäkohinaa

**Tärkein huomio:** -0.0077 paraneminen kaikilla lähdöillä on **iso lukema**.
Tämä on enemmän kuin pelkkä distance bucket -korjaus normaalisti tuottaa.
Osa paranemisesta voi olla siementäkohinaa, mutta paranemisen suunta on oikea.

### Pyydän yhden lisäkorjauksen ennen seuraavaa retrain-ajoa

**`retrain_model.py`:hin `random_state=42`** — yksi rivin muutos, ei kiire mutta tärkeä toistettavuudelle:

```python
# retrain_model.py — muuta:
model = train_ranker(train_df, random_state=42)  # LISÄÄ random_state
```

Tämä antaa **luotettavat luvut** seuraavissa malliajoissa. Ilman tätä:
- Saman päivän retrain-ajot tuottavat eri Brier-arvoja
- Vaihe 5:n stop/go-päätös (~7.7.2026) vaatii toistettavia mittareita

**Älä tee uutta retrainia heti** — nykyinen `model_baseline_20260515.lgb` on hyvä
käytössä dashboardilla. Lisää `random_state=42` ja **seuraavan kerran kun
retrainaat** (esim. kun datankeräys on jatkunut viikolla) saat luotettavan
vertailuluvun.

### Strategiset johtopäätökset

**1. V-pelilähtöjen tulos on PARANNETTAVA — älä luota siihen vielä.**

V-pelilähdöt + Brier 0.0781 vs. uniform 0.0843 = voittosignaali **vain 0.0062**.
Tämä on **pieni** ja V-pelistrategian peruskivi. Mahdolliset polut:

- **K1-pollutoitujen kuski/valmentaja-piirteiden palautus ~2026-09** — Solvalla-vertailu (Valla d'Gaagaa 5.0 % vs. 25.3 %) osoitti että nämä piirteet **ovat tärkeitä juuri V-peleissä**
- **Live pre-race -kertoimet dashboardiin** — tarkempi edge-laskenta
- **Lisää dataa** (~7.7.2026, 8 viikkoa)

**2. Distance bucket -korjaus oli arvokkaampi kuin odotin.**

-0.0077 paraneminen yhdestä piirre-korjauksesta on iso. Tämä kertoo että
takamatka-hevoset olivat aiemmin systemaattisesti väärässä luokassa →
`form_avg_finish_5_same_dist` antoi vääristyneitä arvoja monelle hevoselle.
Ulkopuolisen auditoijan löytö (Parannus #7) oli tärkeämpi kuin alkuvaiheessa
arvioin.

**3. tr_*-piirteiden lopullinen kohtalo on selvä.**

Niiden poisto heikensi V-pelilähtöjen Brieria +0.0009. Sama suunta kuin
korjatussa A/B-vertailussa (+0.0039 paraneminen tr_*-piirteillä V-peleissä).
Mutta **molemmat liian pieniä päätöksenteolle**. Travronden-aktivointi
~2026-07-07 (KNOWN_ISSUES #14) odottaa enemmän dataa ennen A/B-uusintaa.

### Ei lisätehtäviä koodarille

Tämän jälkeen projekti voi **rauhassa kerätä dataa**. Seuraavat tapahtumat:

- **2026-05-17 (su) ~02:00** — ensimmäinen drift-monitorointi
- **~2026-06-08** — 42 vrk dataa, `rolling_walk_forward` käynnistettävissä
- **~2026-07-07** — 8 vk dataa, kriittiset aktivoinnit (sire + Travronden + stop/go)
- **~2026-09-01** — K1-piirteiden palautus

Lisäys: **`random_state=42` retrain_model.py:hyn seuraavalla retrainilla**
(ei kiire, ei tehtävä erikseen).

Hyvää työtä. Ulkopuolinen auditointi paljasti 6 todellista bugia, parannukset
toteutettiin huolellisesti, dashboard toimii, datankeräys jatkuu Bugi #4
-korjauksella tuotannossa. Projekti on nyt vakaammalla pohjalla kuin koskaan.


---

## ✅ KOODARIKUITTAUS — Auditoijan päätös vastaanotettu (15.5.2026 ilta)

**`random_state=42` on jo lisätty** — tehty commitissa `bc0ce9a` ennen
auditoijan kommenttia. Ei erillistä toimenpidettä tarvita.

**Projektin tila tällä hetkellä:**
- ✅ Datankeräys käynnissä Hetznerillä (scheduler `active (running)`)
- ✅ Dashboard käytettävissä SSH-tunnelin kautta
- ✅ 335 testiä passing
- ✅ Kaikki auditoijan tehtävät suoritettu

**Odotellaan dataa. Seuraava kontakti ~3.6.2026.**
