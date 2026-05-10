# 🐛 Bugianalyysi — ravit-edge

Koko koodipohja luettu läpi. Alla löydökset vakavuusjärjestyksessä.

> **Validoitu 10.5.2026** — jokainen löydös on tarkistettu suoraan
> koodista. Löydökset on merkitty:
> - ✅ **VAHVISTETTU** — koodi vastaa kuvausta, löydös on oikein
> - ✅⚠️ **VAHVISTETTU, KORJAUS** — löydös oikein mutta mekanismi tai
>   rivinumero täsmennetty
> - ⚠️ **OSITTAIN OIKEIN** — huomio validi, mutta uhkamalli ei pidä paikkaansa
>
> Yhtään täysin väärää löydöstä ei havaittu. Alkuperäinen analyysi on
> laadukas. Rivinnumerot ovat viitteellisiä — koodin kasvaessa ne liukuvat.

---

## 🔵 Koodihygienia (ei vaikuta dataan nykyisellä palvelimella)

### 1. `fetch_results` käyttää naive `datetime.now()` — aikaleima ilman aikavyöhykettä

✅⚠️ **VAHVISTETTU — rivinumero korjattu: rivi 636, ei 625**

> [!NOTE]
> **PÄIVITYS 5.5.2026:** Hetzner-palvelimen aikavyöhyke varmistettu:
> `Time zone: Etc/UTC (UTC, +0000)`. Koska palvelin on UTC:ssä,
> `datetime.now()` ja `datetime.now(timezone.utc)` palauttavat **saman
> numeerisen arvon** — ainoa ero on Python-objektin `tzinfo`-attribuutti,
> jonka SQLite ei tallenna. **Data on puhdasta, aikaleimoja ei tarvitse
> korjata.** Bugi on alennettu koodihygienia-tasolle.

**Tiedosto:** `src/data/scheduler.py`, rivi 636 (ei 625 kuten alkuperäisessä raportissa)

```python
# Rivi 636 — rivi 625 on logger.info-kutsu
now = datetime.now()  # pitäisi olla datetime.now(timezone.utc)
```

`fetch_results` tallentaa `captured_at`-aikaleiman `datetime.now()`:lla (naive).
Kaikissa muissa paikoissa (`capture_odds_snapshot`) käytetään oikein
`datetime.now(timezone.utc)`. Koodi on epäkonsistentti, mutta koska
palvelimen tz on UTC, tuotetut arvot ovat identtisiä.

**Miksi korjata silti:** Jos palvelimen aikavyöhyke joskus vaihdettaisiin,
bugi aktivoituisi heti. Korjaus on yksi sana.

**Korjaus:** `now = datetime.now(timezone.utc)` — seuraavan muun muutoksen yhteydessä

---

### 2. `_km_seconds` parsinta hiljaisesti väärin — `seconds` voi olla > 59

✅⚠️ **VAHVISTETTU — rivinumero korjattu: funktio alkaa rivillä 131, ei 120**

**Tiedosto:** `src/data/scheduler.py`, rivi 131

```python
def _km_seconds(time_obj: Any) -> float | None:
    # ...
    return (
        int(time_obj["minutes"]) * 60
        + int(time_obj["seconds"])      # ei validoi < 60
        + int(time_obj["tenths"]) / 10
    )
```

Ei validoi, että `seconds < 60`. Jos ATG API palauttaisi virheellisen arvon,
funktio palauttaisi väärän luvun hiljaisesti. Huomio: funktiossa on jo
`try/except (KeyError, TypeError, ValueError)` joka suojaa parse-virheiltä,
mutta ei arvoalueen virheiltä.

> [!NOTE]
> Ei todellinen tuotantobugi — ATG:n data on empiirisesti puhdasta.
> Defensive coding -periaate puuttuu.

---

### 3. `birth_year`-laskenta on epäluotettava vuodenvaihteessa

✅⚠️ **VAHVISTETTU — rivinumero korjattu: rivi 372, ei 361. Parempi korjaus erilainen.**

**Tiedosto:** `src/data/scheduler.py`, rivi 372

```python
obj.birth_year = (date.today().year - age) if isinstance(age, int) else None
```

ATG:n `age`-kenttä on hevosen kilpailuvuosi-ikä. `date.today().year` on
ajohetken vuosi, ei kilpailuvuosi. Jos ajetaan `run-once` tammikuussa 2027
päivälle 2026-04-27, saadaan `birth_year = 2027 - age` vaikka pitäisi olla
`2026 - age`.

**Parempi korjaus:** käytä kilpailuvuotta race-datasta sen sijaan kuin
`today().year`. Mutta `_upsert_horse` ei saa `race`-dict:tä parametrina —
vaatisi funktion siinatuurin muutoksen. Muihin aggregaatteihin (vuositilastot)
on jo olemassa `year = str(race.get("date") or "")[:4]` -apumuuttuja.

Käytännössä ei ongelma koska scheduler ajaa aina reaaliaikaisia päiviä.
Manuaalisissa takautuvissa `run-once`-ajoissa vuodenvaihteen yli
`birth_year` voi heittää ±1 vuoden.

---

## 🟡 Merkittävät ongelmat (ei kaada tuotantoa, mutta riskejä)

### 4. `_kilometer_time_from_sort` parsii vääriin sekunteihin tietyillä arvoilla

✅ **VAHVISTETTU**

**Tiedosto:** `src/data/scrapers/travsport.py`, rivi 230

```python
def _kilometer_time_from_sort(field: Any) -> float | None:
    v = _sort_int(field)
    if v is None or v >= _INVALID_KM_TIME:  # 9990
        return None
    minutes = v // 1000
    seconds_int = (v % 1000) // 10
    tenths = v % 10
    return minutes * 60 + seconds_int + tenths / 10.0
```

Testatut arvot toimivat oikein: `1193 → 79.3s` ✓, `1224 → 82.4s` ✓.
Reuna-ehto: arvo `900` → `minutes=0, seconds_int=90, tenths=0 → 90.0s`.
Tämä olisi 1:30,0 väärin formatoituna (pitäisi olla `1300`).

Käytännössä Travsportin valid km-aika-sortValuet alkavat aina ≥ 1000
(nopein mahdollinen on ~1:00,0 eli 1000). Arvo `900` ei esiinny
tuotantodatassa — validointia ei ole mutta se ei myöskään tarvita nyt.

> [!NOTE]
> Defensive coding puuttuu, ei todellinen tuotantoriski.

---

### 5. `driver_trainer_features` multi-index merge tuottaa virheellisiä tuloksia

✅ **VAHVISTETTU — vakavampi kuin raportoitu. Koodi kaatuu tai räjäyttää rivit.**

**Tiedosto:** `src/features/build_features.py`, rivit 94–113

```python
rolled = (
    df.set_index("race_date")
    .groupby(role)[["is_win", "is_top3"]]
    .rolling(f"{lookback_days}D", closed="left")
    .agg(["mean", "count"])
    .reset_index()
)
rolled.columns = [
    "race_date" if c[0] == "race_date" else f"{role}_{c[0]}_{c[1]}"
    for c in rolled.columns.to_flat_index()
]
df = df.merge(
    rolled.rename(columns={role: role}),  # no-op!
    on=["race_date", role],
    how="left",
)
```

**Ongelma 1 — sarakkeen nimeäminen kaatuu tai tuottaa väärän nimen:**
`reset_index()`:n jälkeen `rolled` sisältää sekä stringi-sarakkeita
(`role`, `race_date`) että MultiIndex-sarakkeita agg:sta. `to_flat_index()`
palauttaa 1-tuplet string-sarakkeille ja 2-tuplet multi-indekseille.
`f"{role}_{c[0]}_{c[1]}"` kaatuu `IndexError`iin 1-tuplen `c[1]`-viittauksella
(role-sarakkeelle), tai — riippuen pandas-versiosta — nimeää rooli-sarakkeen
väärin `"driver_driver_"`-muotoon. `rename(columns={role: role})` on no-op.
Merge `on=["race_date", role]` epäonnistuu `KeyError`:lla koska rooli-sarake
puuttuu tai on väärällä nimellä.

**Ongelma 2 — M:N-join räjäyttää rivimäärän (riippumaton ongelmasta 1):**
Jos sama ohjastaja ajaa 3 lähtöä samana päivänä, `rolled`-taulussa on
3 identtistä riviä kyseiselle (race_date, driver)-yhdistelmälle.
Merge `on=["race_date", driver]` → 3 × 3 = 9 riviä alkuperäisen 3:n sijaan.
Tämä row explosion vioittaa feature-matriisin hiljaisesti.

> [!WARNING]
> **Korjattava ennen Vaihetta 3.** Funktio kaatuu tai tuottaa väärää
> feature-dataa. Oikea toteutus vaatii `transform()`:n tai erillisen
> aggregoinnin ennen mergeä.

---

### 6. `backtest.py` rivi 127 — aina `%Y-%m` format (kvartti-labeli ei toimi)

✅ **VAHVISTETTU**

**Tiedosto:** `src/models/backtest.py`, rivi 127

```python
period=f"{q_start.strftime('%Y-Q%q' if False else '%Y-%m')}",
```

`if False` on aina False → `'%Y-%m'` käytetään aina. `%q` ei ole validi
Python `strftime`-koodi (vain pandas:ssa). Tämä on selkeä kesken jäänyt
TODO — kvartti-labeli ei koskaan toimi. `BacktestResult.period`-kenttä
on muodossa `"2026-01"` eikä koskaan `"2026-Q1"`.

---

### 7. `correlated_kelly_adjust` ei itse asiassa säädä panoksia korrelaatiolle

✅ **VAHVISTETTU**

**Tiedosto:** `src/betting/bankroll.py`, rivit 112–135

```python
def correlated_kelly_adjust(bets_in_race, config):
    total_prob = sum(p for p, _ in bets_in_race)
    raw = [kelly_stake(p, o, 1.0, config) for p, o in bets_in_race]

    if total_prob >= 1.0:
        return [r * 0.5 for r in raw]
    return raw  # ← normaalitilanteessa palauttaa ilman säätöä
```

Kun `total_prob < 1.0` (normaali tilanne — et pelaa koko kenttää),
funktio palauttaa `raw`-panokset ilman mitään säätöä. Docstringin lupaus
"jaa kunkin pelin Kelly suhteessa yhteenlaskettuun voittotodennäköisyyteen"
ei toteudu. Oikea implementaatio skaalaisi panokset `total_prob`:lla:
`[r * total_prob for r in raw]` (tai vastaava approksimaatio).

> [!WARNING]
> Korjattava ennen Vaihetta 6. Tällä hetkellä ei vaikuta tuotantoon.

---

### 8. `race_setup_features` data leakage `track_horse_wins_cum`-piirteessä

✅ **VAHVISTETTU**

**Tiedosto:** `src/features/build_features.py`, rivit 154–159

```python
df["track_horse_wins_cum"] = (
    df.groupby(["horse_id", "track"])["is_win"]
    .cumsum()
    .shift(1)
    .fillna(0)
)
```

`.cumsum()` lasketaan oikein ryhmittäin `groupby`:n sisällä. Mutta
`.cumsum()` palauttaa tavallisen Seriesin (ei enää groupby-kontekstissa),
ja sen jälkeinen `.shift(1)` on **globaali siirto**, ei ryhmänsisäinen.

Esimerkki: hevonen ajaa radoilla SO ja AX. Järjestys df:ssä on
`[SO-1, SO-2, AX-1, AX-2]`. Cumsumit: `[s1, s2, a1, a2]`.
Global shift(1): `[NaN, s1, s2, a1]`. AX:n ensimmäinen rivi saa
SO:n toisen cumsum-arvon — **leakage yli ratojen**.

Oikea korjaus: `df.groupby(...).transform(lambda s: s.cumsum().shift(1))`
jotta shift pysyy ryhmän sisällä.

> [!WARNING]
> Korjattava ennen Vaihetta 3. Aiheuttaa vääriä track_horse_win_rate
> -arvoja hevosten ensimmäisillä starteilla uudella radalla.

---

## 🔵 Pienet huomiot (ei kriittisiä, hyvä tiedostaa)

### 9. Loggaus-aukko (TODO #7 — tiedostettu)

✅ **VAHVISTETTU — dokumentoitu TODO.md:ssä**

`src.data.atg_client.logger` ja `src.data.scrapers.travsport.logger`
eivät kuulu `ravit_edge.scheduler`-hierarkiaan → niiden lokit eivät
päädy `scheduler.log`:iin, vain stderriin / journalctl:iin.

---

### 10. `_odds` sentinel-raja Travsportissa: `>= 9998` vs km-ajan `>= 9990`

⚠️ **OSITTAIN OIKEIN — uhkamalli ei pidä paikkaansa, mutta huomio validi**

**Tiedosto:** `src/data/scrapers/travsport.py`, rivit 51–56

```python
_INVALID_KM_TIME = 9990   # km-aika-sentinelit: 9990+
_INVALID_ODDS    = 9998   # odds-sentinelit:    9998+
```

Alkuperäinen raportti kysyy: jos Travsport käyttää arvoja 9990–9997
jossakin odds-kentässä, ne tulkittaisiin valideiksi oddsiksi (~999.0–999.7).

**Täsmennys:** `_INVALID_KM_TIME` ja `_INVALID_ODDS` viittaavat
**eri kenttiin** eri normalisoijissa (`_kilometer_time_from_sort` vs
`_odds`). Km-aika-sentineleillä ei ole vaikutusta odds-parsintaan eikä
päinvastoin — välit eivät ristiriidassa. Arvo 9990 km-aika-kentässä →
filtteröidään oikein; arvo 9990 odds-kentässä → tulkitaan 999.0:ksi
(hyvin epätodennäköinen aito kerroin, käytännössä ei esiinny).

Aidon sentinelin vaara olisi jos sama 9990–9997 arvo esiintyisi
odds-kentässä sentinelinä, mutta Travsportin dokumentoitu
sentineliskaala odds-kentälle alkaa 9998:sta. Sentinel-arvojen
kommentointi on kuitenkin selkeämpää tehdä (ne ovat nyt hajallaan).

---

### 11. `OddsSnapshot.captured_at` — ei timezone-tietoinen SQLite:ssä

✅ **VAHVISTETTU — suora seuraus bugista #1**

SQLite tallentaa `DateTime`-sarakkeen naive-stringinä. `fetch_results`
kirjoittaa naive `datetime.now()` -arvoja, `capture_odds_snapshot`
kirjoittaa tietoisia `datetime.now(timezone.utc)` -arvoja. Teknisesti
tietokannassa on sekoitus, mutta koska palvelin on UTC, **numeeriset
arvot ovat identtisiä**. Bugi #1:n korjaus korjaa myös tämän.

---

### 12. `update_after_settlement` stop-loss lasketaan `starting_bankroll`:sta, ei nykyisestä

✅ **VAHVISTETTU — voi olla tarkoituksellinen design-valinta**

**Tiedosto:** `src/betting/bankroll.py`, rivi 149

```python
threshold = state.config.starting_bankroll * state.config.weekly_stop_loss_pct
```

Stop-loss kynnys on kiinteä absoluuttinen arvo (esim. 1 500 SEK
10 000 SEK startilla). Jos bankroll kasvaa 20 000 SEK:iin, stop-loss
on edelleen 1 500 SEK (7,5 % → eri kuin docstringin 15 %).

Käytännön vaikutus: suuremmalla bankrollilla stop-loss aktivoituu
vasta kun häviöprosentti on suhteellisesti pienempi. Konservatiivinen
vai virhe — riippuu tarkoituksesta. Tyypillinen käytäntö on laskea
stop-loss `current_bankroll`:sta.

Tällä hetkellä ei vaikuta tuotantoon (V6 ennen kuin relevantti).

---

## Yhteenveto

| # | Vakavuus | Kuvaus | Vaikuttaa nyt? | Validointi |
|---|----------|--------|----------------|-----------|
| 1 | 🔵 | `fetch_results` naive `datetime.now()` (rivi 636) | **Ei** — palvelin UTC | ✅ Vahvistettu |
| 2 | 🔵 | `_km_seconds` ei validoi arvoaluetta (rivi 131) | Ei | ✅ Vahvistettu |
| 3 | 🔵 | `birth_year` vuodenvaihteessa (rivi 372) | Marginaalinen | ✅⚠️ Korjaus erilainen |
| 4 | 🔵 | `_kilometer_time_from_sort` reuna-ehto | Ei | ✅ Vahvistettu |
| 5 | 🟡 | `driver_trainer_features` kaatuu tai räjäyttää rivit | **Ei vielä (V3)** | ✅ Vakavampi kuin raportoitu |
| 6 | 🔵 | `backtest.py` `if False` — kvartti-labeli rikki | Ei vielä (V3) | ✅ Vahvistettu |
| 7 | 🟡 | `correlated_kelly_adjust` ei säädä mitään | Ei vielä (V6) | ✅ Vahvistettu |
| 8 | 🟡 | `track_horse_wins_cum` shift(1) — data leakage | **Ei vielä (V3)** | ✅ Vahvistettu |
| 9 | 🔵 | Loggaus-aukko (TODO #7) | Kyllä — ei kriittinen | ✅ Vahvistettu |
| 10 | 🔵 | Odds sentinel-raja 9990 vs 9998 | Ei | ⚠️ Eri kentät, ei ristiriita |
| 11 | 🔵 | `captured_at` timezone-sekoitus | **Ei** — palvelin UTC | ✅ Seuraus #1:stä |
| 12 | 🔵 | Stop-loss `starting_bankroll`:sta | Ei vielä (V6) | ✅ Design choice |

> [!IMPORTANT]
> **Mikään löydetyistä bugeista ei vaikuta tällä hetkellä tuotantodatan
> oikeellisuuteen.**
>
> **Ennen Vaihetta 3 (8.6.2026) pakollista korjata:**
> - **#5** `driver_trainer_features` — kaatuu tai tuottaa väärää dataa
>   (M:N-join row explosion + sarakkeen nimeäminen rikki)
> - **#8** `track_horse_wins_cum` — data leakage yli ryhmärajojen
>
> **Ennen Vaihetta 6 korjattava:**
> - **#7** `correlated_kelly_adjust` — ei tee lupaamaansa säätöä
>
> **Seuraavan deploy-syklin yhteydessä (matala prioriteetti):**
> - **#1** `datetime.now()` → `datetime.now(timezone.utc)` (1 sana)
> - **#6** `if False` → oikea kvartti-logiikka

---

*Alkuperäinen analyysi tehty ulkopuolisen tahon toimesta. Validoitu
ja täsmennetty 10.5.2026.*
