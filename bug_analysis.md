# Bugianalyysi — ravit-edge

> Päivitetty 10.5.2026. Vain avoimet ongelmat listattu yksityiskohtaisesti.
> Korjatut bugit ovat yhteenvedossa tiedoston lopussa.

---

## Avoimet — korjattava ennen Vaihetta 3

### #5 · `driver_trainer_features` M:N-join + sarakkeen nimeäminen

**Tiedosto:** `src/features/build_features.py`  
**Status:** ✅ **KORJATTU 10.5.2026**

→ Siirretty "Korjattu"-osioon, ks. alla.

---

### #8 · `race_setup_features` data leakage `track_horse_wins_cum`-piirteessä

**Tiedosto:** `src/features/build_features.py`  
**Status:** ✅ **KORJATTU 10.5.2026**

→ Siirretty "Korjattu"-osioon, ks. alla.

---

## Avoimet — korjattava ennen Vaihetta 6

### #7 · `correlated_kelly_adjust` ei säädä panoksia korrelaatiolle

**Tiedosto:** `src/betting/bankroll.py`, rivit 112–135

```python
def correlated_kelly_adjust(bets_in_race, config):
    total_prob = sum(p for p, _ in bets_in_race)
    raw = [kelly_stake(p, o, 1.0, config) for p, o in bets_in_race]

    if total_prob >= 1.0:
        return [r * 0.5 for r in raw]
    return raw  # ← normaalitilanteessa palauttaa ilman säätöä
```

Kun `total_prob < 1.0` (normaali tilanne), funktio palauttaa `raw`-panokset
ilman mitään korrelaatiosäätöä. Oikea toteutus skaalaisi panokset
`total_prob`:lla: `[r * total_prob for r in raw]`.

**Prioriteetti:** ei vaikuta tuotantoon nyt — relevantti vasta V6.

---

## Avoimet — koodihygienia (ei tuotantovaikutusta)

### #2 · `_km_seconds` ei validoi arvoaluetta

**Tiedosto:** `src/data/scheduler.py`, `_km_seconds()`

`seconds`-kentän arvoa ei validoida `< 60`. Jos ATG palauttaisi virheellisen
arvon, funktio laskisi väärän sekuntimäärän hiljaisesti. ATG:n data on
empiirisesti puhdasta, ei todellinen riski nyt.

### #3 · `birth_year`-laskenta vuodenvaihteessa

**Tiedosto:** `src/data/scheduler.py`, `_upsert_horse()`

```python
obj.birth_year = (date.today().year - age) if isinstance(age, int) else None
```

`date.today().year` on ajohetken vuosi, ei kilpailuvuosi. Manuaalisissa
`run-once`-ajoissa tammikuussa ±1 vuoden heitto. Ei ongelma normaalissa
reaaliaikaisessa keräyksessä.

### #4 · `_kilometer_time_from_sort` reuna-ehto arvolle < 1000

**Tiedosto:** `src/data/scrapers/travsport.py`

Arvo `900` laskettaisiin `0:90,0`:ksi (sekunnit = 90). Valid km-aika-sortValuet
alkavat aina ≥ 1000 — arvo ei esiinny tuotantodatassa.

### #9 · Loggaus-aukko (ks. myös ROADMAP.md #7)

`src.data.atg_client.logger` ja `src.data.scrapers.travsport.logger` eivät
kuulu scheduler-loggaushierarkiaan → eivät päädy `scheduler.log`:iin.
Journalctl näyttää kaiken. Matala prioriteetti.

### #10 · Odds-sentinelien kommentointi hajallaan

**Tiedosto:** `src/data/scrapers/travsport.py`

`_INVALID_KM_TIME = 9990` ja `_INVALID_ODDS = 9998` viittaavat eri kenttiin
eri normalisoijissa — ei ristiriita. Selkeämpi kommentointi auttaisi
lukijaa, mutta ei funktiota.

### #12 · Stop-loss lasketaan `starting_bankroll`:sta

**Tiedosto:** `src/betting/bankroll.py`, rivi 149

Stop-loss kynnys on kiinteä absoluuttinen summa. Kasvaneella bankrollilla
stop-loss aktivoituu prosentuaalisesti myöhemmin kuin docstring lupaa.
Mahdollisesti tarkoituksellinen design-valinta — ei relevantti ennen V6.

---

## Korjattu

| # | Kuvaus | Korjattu |
|---|--------|---------|
| **#1** | `fetch_results` naive `datetime.now()` → `datetime.now(timezone.utc)` | 10.5.2026 |
| **#5** | `driver_trainer_features` MultiIndex-merge kaatui tai räjäytti rivimäärän | 10.5.2026 |
| **#6** | `backtest.py` `if False` — kvartti-labeli ei koskaan toiminut | 10.5.2026 |
| **#8** | `track_horse_wins_cum` globaali `shift(1)` — data leakage yli ryhmärajojen | 10.5.2026 |

Bugit #5 ja #8 olivat pakollisia korjata ennen Vaihetta 3 (feature engineering).
Bugit #1 ja #6 olivat pieniä korjauksia jotka tehtiin samalla kertaa.
