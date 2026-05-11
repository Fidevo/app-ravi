# Jatkokorjaukset — toteutusohje

> Auditoija: Claude (Opus 4.7)
> Päivämäärä: 2026-05-10
> Konteksti: Tämä on ACTION_PLAN.md:n jälkitarkastuksen löydökset.
> ACTION_PLAN.md:n korjaukset (K1, B3, B4) ovat enimmäkseen oikein, mutta
> **B1 ja B2 eivät toimi tuotannossa, K1-backfill on osittainen**.
> Auditoija ajoi pipelinen tuotantodatalla ennen tämän kirjoittamista —
> kaikki bugiväitteet on **empiirisesti vahvistettu** (luvut alla).

> **Koodari: raportoi edistymisesi tiedostoon `TASK_PROGRESS.md`**
> jokaisen vaiheen jälkeen. Auditoija tarkistaa ennen seuraavaan
> vaiheeseen siirtymistä.

---

## Empiiriset todisteet bugeista (älä luota mun sanaan, vahvista ensin itse)

Aja tämä snippet venvistä projektin juuressa:

```python
import pandas as pd, sqlite3, sys
sys.path.insert(0, ".")
con = sqlite3.connect("data/ravit.db")
runners = pd.read_sql(
    "SELECT r.*, ra.race_date FROM runners r JOIN races ra ON r.race_id=ra.race_id",
    con,
)
races = pd.read_sql("SELECT * FROM races", con)
horse_starts = pd.read_sql(
    "SELECT * FROM horse_starts WHERE withdrawn != 1 "
    "AND (finish_position != 99 OR finish_position IS NULL)", con,
)
from src.features.build_features import build_feature_matrix, fill_finish_positions
features = build_feature_matrix(fill_finish_positions(runners), races, horse_starts=horse_starts)

print("B2 same_method notna%:", round(features['form_avg_finish_5_same_method'].notna().mean()*100, 2))
print("B2 same_dist notna%:",   round(features['form_avg_finish_5_same_dist'].notna().mean()*100, 2))
print("B1 track_horse_win_rate notna%:", round(features['track_horse_win_rate'].notna().mean()*100, 2))
print("B1 track_horse_starts mean:",     round(features['track_horse_starts'].mean(), 2))
print("Baseline form_avg_finish_5 notna%:", round(features['form_avg_finish_5'].notna().mean()*100, 2))
```

**Mun saamat luvut tuotantodatasta (10.5.2026):**

| Mittari | Arvo | Selitys |
|---|---|---|
| B2 same_method notna% | **0.0** | ❌ Ei yhtään piirrettä — bugi vahvistettu |
| B2 same_dist notna% | **0.0** | ❌ Sama — bugi vahvistettu |
| B1 track_horse_win_rate notna% | **0.4** | ❌ Lähes kaikki NaN — track-mismatch |
| B1 track_horse_starts mean | **0.0** | ❌ Yhtään historiariviä ei matchaa |
| Baseline form_avg_finish_5 notna% | 82.72 | ✅ Vertailu — runners+horse_starts join toimii kun ratanimeä ei tarvita |

ACTION_PLAN.md väitti B1 NaN-% = ~15. **Tuotannossa se on 99.6 %.** Lupauksen ja
toteutuksen välinen ero on 84 prosenttiyksikköä. Vastaavasti B2 piti tuottaa
kaksi uutta piirrettä — molemmat ovat 100 % NaN.

---

## Vaihejako — älä yritä kaikkea kerralla

Tehdään yksi vaihe kerrallaan, ja **raportoi joka vaiheen jälkeen
TASK_PROGRESS.md:hen** ennen kuin siirryt seuraavaan. Auditoija tarkistaa
ja antaa luvan jatkaa.

```
VAIHE A — Pakolliset bugikorjaukset (~1–2 päivää)
   ├── A1: B2 segmentoidut piirteet — todellinen toteutus (kriittinen)
   ├── A2: B1 trackCode↔ratanimi-mappaus (kriittinen)
   ├── A3: K1-backfillin loppuunvienti (driver/trainer/year)
   └── A4: M1-symmetria + tuotantotyyliset assertiotestit

VAIHE B — Mallin laadun parannukset (~3–5 päivää)
   ├── B1: Isotonic regression rinnalle temperature scalingin kanssa
   ├── B2: Sukutaulupiirteet (sire/dam_sire-aggregaatit horse_starts:sta)
   └── B3: Devigged closing odds piirteenä (kun snapshot-dataa riittää)

VAIHE C — Tuotantokypsyys ja monitorointi (~1 viikko)
   ├── C1: Feature drift -monitorointi (viikkojobi)
   ├── C2: Walk-forward-ikkunan vähimmäispituus dokumentointiin
   └── C3: Pace-piirteen pilotti (100 lähdön manuaalinen scrape)
```

Jos käy kiire ja täytyy priorisoida, **tee A-vaihe ehdottomasti loppuun**
ennen kuin Vaihetta 3 (mallin treenaus) edes harkitaan. B-vaiheen voi
osittain limittää treenauksen kanssa.

---

# VAIHE A — Pakolliset bugikorjaukset

## A1 · B2 segmentoidut piirteet — todellinen toteutus

**Tila:** ❌ Toteutus on rikki. `form_avg_finish_5_same_method` ja
`form_avg_finish_5_same_dist` ovat 100 % NaN tuotannossa.

**Juurisyy** ([build_features.py:88–90](src/features/build_features.py:88)):
```python
seg_cols_avail = [c for c in _POOL_COLS_SEGMENTED if c in df.columns]
pool_cols_full = _POOL_COLS + seg_cols_avail
```

`df` = `runners`-DataFrame. **Skeema-tarkistus:** `runners`-taulussa ei
ole `start_method`- eikä `distance`-saraketta — ne ovat `races`-taulussa
([schema.py:80–129](src/data/schema.py:80)). Workflow ei pre-mergeä näitä
runnersiin ennen `form_features`-kutsua, joten `seg_cols_avail = []`.
Sama suodatus rajoittaa `hist_cols`:n, joten `horse_starts`:in saatavilla
oleva `start_method`/`distance` jää käyttämättä.

**Korjaus (suositus 1: pre-merge build_feature_matrix:ssa):**

```python
# build_feature_matrix() — lisää NÄMÄ ennen form_features-kutsua
def build_feature_matrix(runners, races, horse_starts=None):
    # Pre-mergeä races-tason tieto runnersiin jotta form_features
    # voi laskea segmentoidut piirteet (B2). NÄMÄ TARVITAAN MYÖS
    # horse_starts-puolella, joka tuo ne jo natiivisti.
    race_meta_cols = ["race_id"]
    for c in ("start_method", "distance"):
        if c in races.columns:
            race_meta_cols.append(c)
    runners_with_meta = runners.merge(
        races[race_meta_cols], on="race_id", how="left"
    )
    df = form_features(runners_with_meta, horse_starts=horse_starts)
    df = driver_trainer_features(df)
    df = race_setup_features(df, races, horse_starts=horse_starts)
    df = derived_features(df)
    return df
```

**Tarkistuslista A1:lle:**

- [ ] Korjaus toteutettu (joko pre-merge `build_feature_matrix`:ssa TAI
  seg_cols-keräys sekä df:stä että horse_starts:sta `form_features`:ssa)
- [ ] Aja "Empiiriset todisteet" -snippetin yläosa uudelleen ja varmista
  että `B2 same_method notna%` on **selvästi yli 70**
  (perusteltu vaatimus: form_avg_finish_5 baseline on 82.7 %, segmentoidut
  pitäisi olla samaa luokkaa pienen NaN-lisämäärän kera)
- [ ] Lisää regressiotesti
  `tests/test_build_features.py::test_segmented_form_features_have_values_with_horse_starts`
  joka assertoi `result["form_avg_finish_5_same_method"].notna().mean() > 0.5`
  realistisella synteettisellä datalla
- [ ] Päivitä `test_computed_cols_present_after_build_feature_matrix` lisäämällä
  segmentoidut sarakkeet listalle
- [ ] Raportoi `TASK_PROGRESS.md`:hen: ennen-jälkeen-luvut B2 same_method/same_dist
  notna% tuotantodatasta

---

## A2 · B1 trackCode↔ratanimi-mappaus

**Tila:** ❌ B1 ei tuota lupausta — `track_horse_win_rate` notna% on 0.4 %
(odotus 85+ %), `track_horse_starts mean = 0.0`.

**Juurisyy:** ATG ja Travsport käyttävät täysin eri ratamerkintätapoja:

| Lähde | Esimerkkiarvoja |
|---|---|
| `races.track` (ATG) | "Axevalla", "Boden", "Bollnäs", "Bro Park", "Färjestad" |
| `horse_starts.track` (Travsport, trackCode) | "Bs", "G", "Ro", "D", "B", "H", "Ås", "Ov", "Rä", "Så" |

`drop_duplicates(subset=["horse_id","race_date","track"])` ja groupby:t
käsittelevät näitä eri tracksina → historiarivit eivät yhdisty runner-riveihin.

**Korjaus:** Luo trackCode → ATG-nimi -mappitaulukko ja normalisoi
`horse_starts.track` (tai pidä ATG-nimi normalisoituna ja muunna se
trackCodiksi — kumpi tahansa, kunhan symmetrinen).

**Suositeltu paikka:** `src/data/track_codes.py` (uusi tiedosto), import
`build_features.py`:ssa.

**Vaihe 1 — Listaa kaikki uniikit arvot molemmista lähteistä:**

```python
# Aja kerran ja kopioi tulos pohjaksi mappiin
import sqlite3, pandas as pd
con = sqlite3.connect("data/ravit.db")
print("Travsport-koodit:", sorted(set(
    pd.read_sql("SELECT DISTINCT track FROM horse_starts", con)["track"].dropna()
)))
print("ATG-nimet:", sorted(set(
    pd.read_sql("SELECT DISTINCT track FROM races", con)["track"].dropna()
)))
```

**Vaihe 2 — Rakenna mappi käsin** (Travsportin trackCode-listaus on
vakiintunut — ks. esim. https://www.travsport.se/sport/banor):

```python
# src/data/track_codes.py
"""Travsportin trackCode → ATG:n full track name -mappaus.

Travsport käyttää 1–3 merkin lyhennettä (esim. "S" = Solvalla).
ATG käyttää koko nimeä. Tämä mappi normalisoi horse_starts-rivit
samaan ratamerkintään kuin races.track jotta track-historian
laskenta race_setup_features():ssa toimii.

Lähde: Travsportin julkinen ratataulukko + manuaalinen vahvistus
ravit.db:n DISTINCT-arvoista.
"""

TRACKCODE_TO_NAME = {
    "Ax":  "Axevalla",
    "B":   "Boden",
    "Bs":  "Bergsåker",
    "Bo":  "Bollnäs",
    "Br":  "Bro Park",         # galoppi (suodatetaan pois)
    "D":   "Dannero",
    "E":   "Eskilstuna",
    "F":   "Färjestad",
    "G":   "Gävle",
    "H":   "Hagmyren",
    "Hd":  "Halmstad",
    "J":   "Jägersro",
    "Kr":  "Kalmar",
    "Lu":  "Lindesberg",
    "Ly":  "Lycksele",
    "Ma":  "Mantorp",
    "Ov":  "Oviken",
    "Rä":  "Rättvik",
    "Ro":  "Romme",
    "S":   "Solvalla",
    "Sk":  "Skellefteå",
    "Så":  "Sämre",            # tarkista nimi
    "Ti":  "Tingsryd",
    "U":   "Umåker",
    "Vg":  "Vaggeryd",
    "Vi":  "Visby",
    "Y":   "Ystad",
    "Ås":  "Åby",
    "Ör":  "Örebro",
    "Ös":  "Östersund",
    # Lisää puuttuvat — älä jätä mappaamatta jääneitä koodeja
    # hiljaisesti, vaan kaada test_track_code_coverage:lla.
}
```

**Tärkeää:** **älä keksi nimiä**. Tarkista jokainen koodi joko
Travsportin sivuilta tai vertaamalla `horse_starts.race_date`+`distance`
samaan päivään `races`-taulussa ja siirtämällä nimi sen perusteella.

**Vaihe 3 — Käytä mappia `race_setup_features`:ssa:**

```python
def race_setup_features(runners, races, horse_starts=None):
    ...
    if horse_starts is not None and len(horse_starts) > 0 and "track" in horse_starts.columns:
        from src.data.track_codes import TRACKCODE_TO_NAME
        pool_hist = horse_starts[["horse_id","race_date","track","finish_position"]].copy()
        pool_hist["track"] = pool_hist["track"].map(TRACKCODE_TO_NAME).fillna(pool_hist["track"])
        # Jos koodille ei löydy nimeä, säilytetään raakakoodi → ei matchaa,
        # mutta ei myöskään korruptoi. Lokita varoitus alkuvaiheessa.
        ...
```

**Tarkistuslista A2:lle:**

- [ ] `src/data/track_codes.py` luotu kattavalla mapilla
- [ ] `race_setup_features()` käyttää mappia hist-rivien normalisointiin
- [ ] `tests/test_build_features.py::test_track_code_normalization` —
  testi joka rakentaa fake horse_starts-rivin trackCode="S" ja runners-rivin
  track="Solvalla" ja varmistaa että track_horse_starts >= 1
- [ ] `tests/test_track_codes.py::test_all_horse_starts_codes_have_mapping` —
  testi joka käy DISTINCT track läpi `data/ravit.db`:stä (jos olemassa)
  TAI fixturessa olevasta listasta ja varmistaa että jokaiselle on mappaus
- [ ] Aja Empiiriset todisteet -snippet ja varmista
  `B1 track_horse_win_rate notna% > 80` ja `track_horse_starts mean > 1.0`
- [ ] Raportoi `TASK_PROGRESS.md`:hen: ennen-jälkeen-luvut B1:lle ja
  uniikkien koodien kattavuus (montako koodia mapissa vs. DB:ssä havaittu)

---

## A3 · K1-backfillin loppuunvienti

**Tila:** ⚠ Osittainen. `backfill_correct_atg_aggregates`
([scheduler.py:1503](src/data/scheduler.py:1503)) korjasi vain
`atg_lifetime_starts`, `atg_lifetime_win_rate`, `atg_lifetime_top3_rate`
3 589 rivissä.

**Edelleen pollutoituneet kentät** (jokainen on FEATURE_COLS:issa
[ranker.py:51,54–57](src/models/ranker.py:51)):

- `atg_current_year_win_rate`
- `atg_driver_starts`
- `atg_driver_win_pct`
- `atg_trainer_starts`
- `atg_trainer_win_pct`

Backfill-funktion docstring myöntää tämän rehellisesti rivillä 1519:
"nimittäjä ei ole tiedossa". Aito.

**Päätös koodarille — valitse yksi:**

### Vaihtoehto A: Poista pollutoituneet kentät FEATURE_COLS:ista
Yksinkertaisin. Treenaa malli ilman näitä kunnes 3–4 viikkoa K1-korjauksen
jälkeistä puhdasta dataa kertyy. Sen jälkeen lisää takaisin.

```python
# ranker.py — kommentoi pois K1-pollutoidut kentät kunnes uusi data riittää
FEATURE_COLS = [
    ...
    # K1-vuoto-pollutoituneet ennen 10.5.2026 — aktivoi takaisin kun
    # >= 600 lähtöä on kerätty K1-korjauksen jälkeen (n. 4 viikkoa)
    # "atg_current_year_win_rate",
    # "atg_driver_starts",
    # "atg_driver_win_pct",
    # "atg_trainer_starts",
    # "atg_trainer_win_pct",
    ...
]
```

### Vaihtoehto B: Re-fetchaa pre-race driver/trainer-statsit
Jokaiselle 3 589 rivin runner-riville: hae ATG:lta hevosen ja ohjastajan
statistiikat **lähtöpäivän alusta** (käyttäen statistics.years.<vuosi>:sta
kuluvan vuoden lukemia, käännetty pre-race-tilaan). Vaatii että storeissa
on saatavilla pre-race-dump tai että ATG paljastaa historiallisia
arvoja. Todennäköisesti **ei mahdollista** — ATG ei tarjoa pisteleimattua
historiadataa.

**Suositus:** Vaihtoehto A. Kirjoita CLAUDE.md/README:hen muistutus
päivämäärästä jolloin kentät palautetaan.

**Tarkistuslista A3:lle:**

- [ ] Päätös valittu (A tai B) ja perusteltu `TASK_PROGRESS.md`:ssä
- [ ] Vaihtoehto A: kentät kommentoitu pois `FEATURE_COLS`:ista, kommentti
  selittää miksi ja milloin palautetaan
- [ ] Vaihtoehto A: lisätty TODO sekä `KNOWN_ISSUES.md`:hen että
  selkeä päivämäärämuistutus
- [ ] Treenikoodi ei kaadu puuttuvien sarakkeiden takia
  (`_resolve_cols` hoitaa jo tämän — varmista testillä)
- [ ] Raportoi `TASK_PROGRESS.md`:hen valinta + perustelu

---

## A4 · M1-symmetria + tuotantotyyliset assertiotestit

### A4a — `_set_if_not_none` myös `_upsert_runner`:iin

**Tila:** ⚠ M1-suoja on vain `_upsert_race`:ssa. `_upsert_runner`
([scheduler.py:476](src/data/scheduler.py:476)) tekee yhä raakaa
`setattr(obj, k, v)`:ia jokaiselle ATG-aggregaatille ja shoes/sulky-kentälle.

Käytännössä tämä on OK koska `_upsert_runner`:ia ei enää kutsuta
`fetch_results`:sta. Mutta `refresh_day_runners` (T-10min ennen 1. lähtöä)
kutsuu `_upsert_runner`:ia, ja jos ATG:n vastaus on osittain vajaa, hyvät
arvot ylikirjoitetaan Nonella.

**Korjaus:** Käytä `_set_if_not_none` myös `_upsert_runner`:ssa kentille
joiden None-arvo voisi olla "tieto puuttuu juuri nyt mutta oli aiemmin":

```python
# _upsert_runner — muuta nämä:
for k, v in _atg_aggregates(horse, race).items():
    _set_if_not_none(obj, k, v)
for k, v in _person_aggregates(start.get("driver"), race, "atg_driver").items():
    _set_if_not_none(obj, k, v)
for k, v in _person_aggregates(horse.get("trainer"), race, "atg_trainer").items():
    _set_if_not_none(obj, k, v)
for k, v in _shoes_sulky_fields(horse).items():
    _set_if_not_none(obj, k, v)

# Ydinkenttiä (race_id, horse_id, start_number) ei suojaeta — ne ovat
# vakiomeät rivin elinkaaren ajan. handicap_meters voidaan myös suojata.
_set_if_not_none(obj, "handicap_meters", handicap if handicap > 0 else None)
_set_if_not_none(obj, "driver", _person_name(start.get("driver")))
_set_if_not_none(obj, "trainer", _person_name(horse.get("trainer")))
```

### A4b — Tuotantotyylinen B1/B2-assertio testeissä

Lisää testi joka varmistaa että B1 ja B2 todella tuottavat arvoja
**realistisella datalla**, ei vain testifixturella:

```python
# tests/test_build_features.py
def test_b1_b2_produce_values_in_realistic_pipeline(self):
    """Regressio: B1 (track_horse_win_rate) ja B2 (segmentoidut piirteet)
    pitää tuottaa non-NaN-arvoja kun runners ja horse_starts tulevat
    realistisesti — runners ilman start_method/distance, horse_starts
    Travsport-trackCodella jonka mappi normalisoi."""
    # Aseta horse_starts jossa on 5 starttia (S = Solvalla mappauksen kautta)
    # ja runners-rivi jossa track="Solvalla" mutta EI start_method:ia
    # (kuten todellisessa SQL-haussa).
    # Assertoi:
    #   features["form_avg_finish_5_same_method"].notna().any() == True
    #   features["track_horse_starts"].iloc[0] >= 5
    ...
```

**Tarkistuslista A4:lle:**

- [ ] `_upsert_runner` käyttää `_set_if_not_none`:ia ATG-aggregaateille,
  driver/trainer-aggregaateille, shoes/sulky-kentille
- [ ] Olemassa oleva `test_upsert_race_does_not_overwrite_existing_fields_with_none`
  -tyylinen testi `_upsert_runner`:lle
- [ ] Lisätty `test_b1_b2_produce_values_in_realistic_pipeline` joka
  emuloi tuotantorakennetta (runners ilman start_method/distance,
  horse_starts trackCodella)
- [ ] Raportoi `TASK_PROGRESS.md`:hen

---

# VAIHE B — Mallin laadun parannukset

> Vaihe B aloitetaan vasta kun A on hyväksytty.

## B1 · Isotonic regression rinnalle temperature scalingin kanssa

**Mitä tehdään:** ACTION_PLAN.md valitsi B3:ssa **temperature scaling** —
yksiparametrinen monotoninen venytys joka ei korjaa epälineaarista
miskalibrointia. Lisää **isotonic regression** vaihtoehdoksi ja vertaa
kumpi pärjää paremmin validointidatalla.

**Miksi:** Temperature scaling olettaa että kalibrointivirhe on uniformi
todennäköisyysavaruudessa (kerroin sama kaikille). Käytännössä mallit
ovat usein liian itsevarmoja keskialueella (40–70 %) mutta hyvin kalibroituja
ääreissä. Isotonic regression on ei-parametrinen ja oppii eri korjauksen
eri todennäköisyysalueille — Plackett-Lucen jälkeen seuraava aste
kalibrointiteoriassa.

**Toteutus:**

```python
# src/models/ranker.py
from sklearn.isotonic import IsotonicRegression

def calibrate_isotonic(predictions: pd.DataFrame) -> IsotonicRegression:
    """Opi ei-parametrinen kalibrointikäyrä softmax-ennusteille.

    Vaihtoehto temperature scalingille: monotoninen mutta ei-parametrinen,
    osaa korjata epälineaarista miskalibrointia. Vaatii vähintään ~500
    validointiriviä luotettavaan oppimiseen — pienemmällä riskinä
    ylisovittuminen.

    Tarvittavat sarakkeet predictions-DataFramessa: race_id, win_prob,
    finish_position
    """
    df = predictions.dropna(subset=["finish_position", "win_prob"]).copy()
    actual_win = (df["finish_position"] == 1).astype(int).values
    raw_probs = df["win_prob"].values
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(raw_probs, actual_win)
    return iso


def apply_isotonic(predictions: pd.DataFrame, iso: IsotonicRegression) -> pd.DataFrame:
    """Sovella opittu kalibrointi ennusteisiin per lähtö ja
    re-normalisoi summaksi 1.0."""
    out = predictions.copy()
    out["win_prob"] = iso.transform(out["win_prob"].values)
    # Re-normalisoi per lähtö jotta P:t summautuvat 1.0:aan
    out["win_prob"] = out.groupby("race_id")["win_prob"].transform(
        lambda s: s / s.sum() if s.sum() > 0 else s
    )
    return out
```

**Vertailu treenausvaiheessa:**

```python
# Vaihe 3:n treenausnotebookissa
val_predictions = predict_win_probabilities(model, val_df)

# A: temperature scaling
T = calibrate_temperature(val_predictions)
val_temp = predict_win_probabilities(model, val_df, temperature=T)

# B: isotonic
iso = calibrate_isotonic(val_predictions)
val_iso = apply_isotonic(val_predictions, iso)

# Vertaa: log-loss ja kalibrointitaulu (evaluate_calibration)
print("temp NLL:", log_loss(actual, val_temp["win_prob"]))
print("iso  NLL:", log_loss(actual, val_iso["win_prob"]))
```

Valitse pienempi NLL ja paremmin kalibroitu malli. Jos validointijoukko
on alle 500 lähtöä, suosi temperature scalingia (vähemmän ylisovittumista).

**Tärkeää:** Älä poista temperature scalingia. Pidä molemmat työkalut
saatavilla ja valitse empiirisesti.

**Tarkistuslista B1:lle:**

- [ ] `calibrate_isotonic()` ja `apply_isotonic()` lisätty ranker.py:hyn
- [ ] Yksikkötestit: kaksi pientä validointidatasettiä (yksi hyvin
  kalibroituneella mallilla, toinen yli-itsevarmalla mallilla) ja
  assertio että isotonic + re-normalisointi tuottaa P:t jotka
  summautuvat 1.0:aan per lähtö
- [ ] Dokumentaatio: milloin valita temperature vs. isotonic
- [ ] Raportoi `TASK_PROGRESS.md`:hen

---

## B2 · Sukutaulupiirteet (sire/dam_sire-aggregaatit)

**Miksi:** Vakiintunut alan tieto: isäoriin ja emänisän win-rate ovat
moderately predictive piirteitä raveissa. `horses`-taulussa on jo `sire`,
`dam`, `dam_sire` ([schema.py:65–74](src/data/schema.py:65)).
`horse_starts`-taulussa on 103k starttia kaikkien aikojen ajalta.
**Ei vaadi yhtään uutta API-kutsua.**

**Toteutus — pre-compute aggregate-taulukko ennen feature-pipelinea:**

```python
# src/features/build_features.py — uusi funktio
def sire_features(runners, horses, horse_starts):
    """Lisää sire/dam_sire-aggregaatit runner-riveille.

    Lasketaan koko historiapoolista (`horse_starts`-taulu, ei rajoitettu
    14 päivään). Aggregaatit per oriin (sire) ja per emänisä (dam_sire).

    Piirteet:
      sire_lifetime_win_rate     : isän jälkeläisten voitto-% (kaikki ajat)
      sire_lifetime_starts       : kuinka monta starttia laskennan pohjana
      dam_sire_lifetime_win_rate : emänisän jälkeläisten voitto-%
      dam_sire_lifetime_starts   : kuinka monta starttia laskennan pohjana

    HUOM: ei data leakagea tämän hevosen omasta lähdöstä — laskenta
    perustuu MUIDEN saman isän jälkeläisten startteihin. Mutta varmista:
    suodata pois nykyisen hevosen omat startit jos halutaan tiukasti
    out-of-sample.
    """
    # 1. Liitä sire/dam_sire kaikkiin horse_starts-riveihin horses-taulusta
    starts_with_pedigree = horse_starts.merge(
        horses[["horse_id", "sire", "dam_sire"]], on="horse_id", how="left"
    )

    # 2. Per-sire aggregaatti
    starts_with_pedigree["is_win"] = (
        starts_with_pedigree["finish_position"] == 1
    ).astype(float)
    sire_stats = (
        starts_with_pedigree.dropna(subset=["sire"])
        .groupby("sire")
        .agg(
            sire_lifetime_starts=("is_win", "count"),
            sire_lifetime_win_rate=("is_win", "mean"),
        )
        .reset_index()
    )
    dam_sire_stats = (
        starts_with_pedigree.dropna(subset=["dam_sire"])
        .groupby("dam_sire")
        .agg(
            dam_sire_lifetime_starts=("is_win", "count"),
            dam_sire_lifetime_win_rate=("is_win", "mean"),
        )
        .reset_index()
    )

    # 3. Liitä runnersiin sire- ja dam_sire-tilastot
    df = runners.merge(horses[["horse_id","sire","dam_sire"]], on="horse_id", how="left")
    df = df.merge(sire_stats, on="sire", how="left")
    df = df.merge(dam_sire_stats, on="dam_sire", how="left")
    return df
```

**Lisää piirteet `FEATURE_COLS`:iin** ja kutsu `sire_features()`
`build_feature_matrix`:ssa.

**Tärkeää:** suodata pois pieniin sample-kokoihin perustuvat estimaatit:
`sire_lifetime_starts < 30` → asetetaan win_rate NaN:ksi (kohina, ei
signaali). Anna LightGBM:n käsitellä NaN.

**Tarkistuslista B2:lle:**

- [ ] `sire_features()` toteutettu ja kutsuttu `build_feature_matrix`:ssa
- [ ] Pieni sample-koko-suodatin (`< 30 starttia → NaN`)
- [ ] Lisätty `FEATURE_COLS`:iin
- [ ] Yksikkötesti synteettisellä datalla: hevosen sire-rate täsmää
  manuaalisesti laskettuun arvoon
- [ ] Empiirinen tarkistus tuotantodatasta:
  `features["sire_lifetime_win_rate"].notna().mean() > 0.5`
- [ ] Raportoi `TASK_PROGRESS.md`:hen

---

## B3 · Devigged closing odds piirteenä

**Tila:** Skeema valmis (`odds_snapshots.devigged_win_odds`), mutta ei
käytetä piirteenä. Pre-race markkinatieto on usein vahvin yksittäinen
ennustaja, ja devigged versio (vig poistettu) on huomattavasti
informatiivisempi kuin raaka pari-mutuel-kerroin.

**Toteutus:**

```python
# build_features.py — uusi funktio
def market_features(runners, odds_snapshots, snapshot_label="T-2min"):
    """Lisää pre-race-markkinapiirteet runner-riveille.

    Käytetään T-2min snapshotia (viimeinen luotettava ennen lähtöä).
    Devigged versio on piirre — raaka win_odds_final on jo runners:ssa
    mutta sisältää 18–22 % vigin.
    """
    snap = odds_snapshots[
        odds_snapshots["snapshot_label"] == snapshot_label
    ][["runner_id", "devigged_win_odds", "raw_win_odds"]].copy()
    snap["pre_race_market_prob_devigged"] = 1.0 / snap["devigged_win_odds"]
    snap["pre_race_market_prob_raw"] = 1.0 / snap["raw_win_odds"]
    return runners.merge(
        snap[["runner_id","pre_race_market_prob_devigged","pre_race_market_prob_raw"]],
        on="runner_id", how="left"
    )
```

**Edellytys:** odota kunnes T-2min-snapshotteja on >= 2 viikkoa puhdasta
dataa K1-korjauksen jälkeen.

**Tarkistuslista B3:lle:**

- [ ] `market_features()` toteutettu
- [ ] Lisätty `FEATURE_COLS`:iin
- [ ] Käytetty T-2min by default (testattava että label-arvo on oikein)
- [ ] Raportoi `TASK_PROGRESS.md`:hen

---

# VAIHE C — Tuotantokypsyys ja monitorointi

> Aloitetaan vasta kun A ja B ovat valmiita.

## C1 · Feature drift -monitorointi

**Miksi tämä on tärkein C-vaiheen kohta:** K1-vuoto olisi havaittu
välittömästi jos jakaumamonitorointi olisi ollut paikallaan.
`atg_lifetime_starts`-jakauman keskiarvo olisi siirtynyt +1 askelma
päivässä. Jokaisesta tulevasta vastaavasta bugista pitää saada hälytys
viikossa, ei kuukausissa.

**Toteutus — viikkojobi joka logaa jakaumat:**

```python
# src/monitoring/feature_drift.py — uusi tiedosto
"""Viikoittainen feature-jakaumien lokitus.

Aja sunnuntai-aamuna ennen treenausjobeja. Vertaa edellisen viikon
jakaumiin ja varoita jos jokin liikkuu yli ±2σ historiallisesta.
"""
def log_feature_distributions(db_path: str, output_path: Path) -> dict:
    """Per piirre: mean, std, p25, p50, p75, NaN-%.
    Tallenna data/logs/feature_drift_YYYY-WW.csv.
    Vertaa edellisen viikon CSV:hen ja varoita poikkeamista."""
    ...
```

**Aikataulu:** crontab-merkintä (Hetzner) sunnuntaisin klo 02:00.

**Hälytysraja (alkuvaiheessa):** mean tai p50 liikkuu yli 2σ tai NaN-%
nousee yli 10 prosenttiyksikköä. Säädä myöhemmin kun datan luonnollinen
varianssi on ymmärretty.

**Tarkistuslista C1:lle:**

- [ ] `feature_drift.py` toteutettu, generoi CSV:n
- [ ] Hetznerillä cronjob viikoittain
- [ ] Vertaa-edelliseen logiikka + hälytys (Telegram, email tai pelkkä
  loki — riippuen olemassa olevasta hälytyspolusta)
- [ ] Raportoi `TASK_PROGRESS.md`:hen ensimmäinen ajo

---

## C2 · Walk-forward-ikkunan vähimmäispituus

**Mitä tehdään:** Päivitä ROADMAP.md ja README.md:n Vaihe 5
("Päätöspiste") **eksplisiittisesti vaatimaan vähintään 8–12 viikkoa
walk-forward-validointia ennen go/no-go-päätöstä**.

**Miksi:** Ravissa on kausivaihtelua (talvi/kesä, ratakelit). 14 vrk on
yhden tutkimuskuukauden alle ja ei tee oikeutta sesonkivariaatiolle.
ACTION_PLAN.md hyväksyi tämän rajoituksen mutta ei merkinnyt sitä
päätösprosessiin riittävän selvästi.

**Konkreettinen muutos:**

```markdown
# ROADMAP.md, Vaihe 5 — lisää tähän kohtaan
## Vaihe 5: Päätöspiste

**EI saa tehdä päätöstä alle 8 viikon walk-forward-datalla.**
Mieluiten 12 viikkoa. Trotissa on kausivaihtelua (talvi/kesä,
sade/pakkanen → ratakeli) joka ei näy lyhyemmässä ikkunassa.

| Lopputulos | CLV | n | Toimenpide |
|---|---|---|---|
| **A: Edge todistettu** | +3 % tai enemmän | n>=200 | Siirry V6 pienillä rahoilla |
| **B: Edge epäselvä** | -2 % – +3 % | n>=200 | Lisää 4 vk dataa, treenaa uudelleen |
| **C: Ei edgea** | alle -2 % | n>=200 | Pysähdy, tutki bugit, älä pelaa |
| **D: Liian vähän dataa** | mikä tahansa | n<200 | Älä tee päätöstä — odota lisää |
```

**Tarkistuslista C2:lle:**

- [ ] ROADMAP.md Vaihe 5 päivitetty
- [ ] README.md viittaus päätöksenteon vähimmäisvaatimuksiin
- [ ] Raportoi `TASK_PROGRESS.md`:hen

---

## C3 · Pace-piirteen pilotti (manuaalinen scrape, 100 lähtöä)

**Miksi:** Auditoijan vahva suositus. Ravialalla on yleisesti tunnistettu
että pace-piirteet (asema 800m, lähtövauhti) ovat yksittäisistä
piirteistä **tärkein voittajan ennuste**. ATG ja Travsport eivät tarjoa
sitä API:nsa kautta nykyaikana.

**Pilotti — älä rakenna täyttä järjestelmää, tee tutkimusiteraatio:**

1. Valitse 100 satunnaisesti valittua lähtöä joiden tulokset on jo
   kerätty (DB:stä `WHERE finish_position IS NOT NULL`).
2. Käy nämä manuaalisesti läpi Travsportin "raviraportti"-sivuilta
   (https://www.travsport.se/.../raceXXXXX/raceReport tms.).
3. Merkitse jokaiselle hevoselle luokka "fast" / "neutral" / "slow"
   avauksesta — yksinkertainen kategoria.
4. Tallenna manuaalinen data CSV:hen `data/raw/pace_pilot.csv`.
5. Treenaa malli **kahdella tavalla**: (a) ilman pace-piirrettä,
   (b) pace-piirteellä mukana — vain näiden 100 lähdön testijoukolla.
6. Vertaa NDCG@1 ja kalibrointi.

**Päätösehto:** Jos pace-piirre nostaa NDCG@1:tä yli 5 %-yksikköä
(esim. 0.32 → 0.37), rakenna scraping-järjestelmä Vaiheessa 7. Jos alle,
hylkää tutkimuksen jälkeen ja siirry pedigreeseen + sääintegroinnin.

**Aikataulu:** ~6–10 tuntia manuaalista työtä. Tee kerralla, älä
hajauta — toistuva työ on hidasta.

**Tarkistuslista C3:lle:**

- [ ] 100 lähtöä satunnaisesti valittu ja CSV-pohja luotu
- [ ] Manuaalinen pace-luokitus tehty (CSV täynnä)
- [ ] Vertailutreenaus tehty
- [ ] Tulokset raportoitu — NDCG ennen/jälkeen + kalibrointi
- [ ] Päätös: rakennetaanko scraping vai ei

---

## Yhteenveto — koodarille

Tämä on iso lista, mutta ole rauhallinen. **Tee yksi vaihe kerrallaan**,
raportoi `TASK_PROGRESS.md`:hen, odota auditoijan tarkistus,
sitten seuraava vaihe.

**Kriittisin polku:** A1 → A2 → A3 → A4 → (tarkistus) →
B1 → B2 → (tarkistus) → C1 → C2 → C3.

A-vaiheen jälkeen Vaihe 3 (mallin treenaus) voidaan **aloittaa**.
Mutta ennen kuin yksikään malli pelaa rahaa, B-vaiheen kalibrointi
pitää olla tehtynä.

Ole rehellinen — jos jokin ei toimi tai vaatii arkkitehtuurin
muuttamista, **kerro siitä TASK_PROGRESS.md:ssä** äläkä yritä venyttää
ratkaisua. Toinen iteraatio on parempi kuin huono ratkaisu joka jää.

Onnea matkaan.
