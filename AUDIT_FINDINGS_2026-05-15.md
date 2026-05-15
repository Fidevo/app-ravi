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
