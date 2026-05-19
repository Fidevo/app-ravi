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

### 🔴 Tarkistettava heti

| Asia | Tila |
|---|---|
| `race_min_earnings` / `race_max_earnings` NULL-tilanne serverillä | ❓ Tarkistamatta |

Auditoija raportoi nämä tyhjiksi. Jos tosi: poistetaan FEATURE_COLS:ista
tai ajetaan `backfill_race_class()` uudelleen.

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
