# Ravit Edge — Tunnetut ongelmat

> Päivitetty 10.5.2026.
> Vain avoimet ongelmat — korjatut bugit löytyvät tiedoston lopusta.

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
    return raw  # ← normaalitilanteessa ei mitään säätöä
```

Kun `total_prob < 1.0` (normaali tilanne), panokset palautetaan ilman
korrelaatiosäätöä. Oikea toteutus: `[r * total_prob for r in raw]`.

**Prioriteetti:** ei vaikuta tuotantoon nyt — relevantti vasta V6.

---

## Avoimet — koodihygienia (ei tuotantovaikutusta)

### #2 · `_km_seconds` ei validoi arvoaluetta

**Tiedosto:** `src/data/scheduler.py`

`seconds`-kenttää ei validoida `< 60`. ATG:n data on empiirisesti puhdasta
eikä tämä ole käytännön riski, mutta virheellinen syöte ei kaatuisi ääneen.

### #3 · `birth_year`-laskenta vuodenvaihteessa

**Tiedosto:** `src/data/scheduler.py`, `_upsert_horse()`

```python
obj.birth_year = (date.today().year - age) if isinstance(age, int) else None
```

Manuaalisissa `run-once`-ajoissa tammikuussa voi tulla ±1 vuoden heitto.
Ei ongelma reaaliaikaisessa tuotantokeräyksessä.

### #4 · `_kilometer_time_from_sort` reuna-ehto arvolle < 1000

**Tiedosto:** `src/data/scrapers/travsport.py`

Arvo `900` laskettaisiin `0:90,0`:ksi (sekunnit = 90). Validit
sortValue-arvot alkavat aina ≥ 1000 — ei esiinny tuotantodatassa.

### #9 · Loggaus-aukko

`src.data.atg_client.logger` ja `src.data.scrapers.travsport.logger`
eivät kulje `scheduler.log`-tiedostoon — vain stderriin / journalctl:iin.
Korjaus: lisää loggerit `setup_logging()`:hin. Matala prioriteetti.

### #10 · Odds-sentinelien kommentointi hajallaan

**Tiedosto:** `src/data/scrapers/travsport.py`

`_INVALID_KM_TIME = 9990` ja `_INVALID_ODDS = 9998` viittaavat eri kenttiin
eri normalisoijissa — ei ristiriita, mutta selkeämpi kommentointi auttaisi.

### #12 · Stop-loss lasketaan `starting_bankroll`:sta

**Tiedosto:** `src/betting/bankroll.py`

Stop-loss-kynnys on kiinteä absoluuttinen summa. Kasvaneella bankrollilla
prosentuaalinen raja aktivoituu myöhemmin kuin docstring lupaa.
Mahdollisesti tarkoituksellinen design — ei relevantti ennen V6.

---

## Korjattu

| # | Kuvaus | Korjattu |
|---|---|---|
| **#1** | `fetch_results` naive `datetime.now()` → `datetime.now(timezone.utc)` | 10.5.2026 |
| **#5** | `driver_trainer_features` MultiIndex-merge kaatui tai räjäytti rivimäärän | 10.5.2026 |
| **#6** | `backtest.py` `if False` — kvartti-labeli ei koskaan toiminut | 10.5.2026 |
| **#8** | `track_horse_wins_cum` globaali `shift(1)` — data leakage yli ryhmärajojen | 10.5.2026 |
