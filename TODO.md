# TODO — ravit-edge

> Korkean tason aikataulu ja faasit: katso [ROADMAP.md](ROADMAP.md)
> Tämä tiedosto sisältää konkreettiset tekniset tehtävät
> yksityiskohtineen ja perusteluineen.

Tunnetut, dokumentoidut tehtävät joita EI tehty Hetzner-MVP-deploymentissa
mutta jotka pitää muistaa. Järjestys = prioriteetti.

## Datan kattavuus

### 2. Travsport shoes/sulky takautuva uudelleenajo
Kun `shoes` ja `sulky`-piirteet lisätään `Runner`-tauluun (ATG:n
`start.horse.shoes` ja `start.horse.sulky` -kentistä), tarvitaan
kertaluonteinen takautuva uudelleenajo joka kerää nämä jo olemassa
oleville runnereille (kaikki `runners` joilla `race_date` viim. 30pv).

## Datan suodatus

### 3. Galoppirata vs ravirata -erottelu
ATG palauttaa SE-radoilla myös galoppia (mm. **Bro Park**, **Jägersro
Galopp**). Galopissa ei ole `kmTime`-objekteja → kaikki gallop-runnerit
näyttävät NULL km-ajalla. Tällä hetkellä ne ovat datassa "näennäisinä
puutteina".

**Korjaus:** Käytä `track.sport == "trot"` -suodatusta `atg_client.py`:n
`get_calendar_day(swedish_only=True)` -metodissa, tai erottele
discipline-kenttä erilliseksi sarakkeeksi `Race`-mallissa jotta
gallop-rivit voi suodattaa pois ML-treenissä.

## Schedulerin robusttius

### 5. Persistentit job-storet
Nykyinen `BlockingScheduler` käyttää `MemoryJobStore`-oletusta —
ajastetut jobit häviävät restartissa. `_initial_setup` korjaa tämän
osittain käynnistyksessä, mutta jos restart osuu juuri snapshot-ikkunaan
(T-15min … T-2min), snapshotit menetetään.

**Korjaus:** vaihda `SQLAlchemyJobStore` joka persistoi jobit DB:hen.
HUOM: vaatii että `args` on serialisoitavissa (pickle) — esim.
TravsportAPIClient-instanssi pitää välittää muulla tavalla.

## DB / havainnointi

### 6. Pre-race snapshotin lähde-monipuolisuus
MVP käyttää vain `atg_pari_mutuel`-pool-kerrointa (vinnare-game).
Step 4 tuo Pinnacle / Betfair Exchange / Unibet -kertoimet sharp-
markkinoiden CLV-vertailuun. Skeema on jo valmis (`source`-kenttä).

### 7. ATG-clientin lokit eivät kulje scheduler.log-tiedostoon
`src.data.atg_client.logger` ja `src.data.scrapers.travsport.logger`
ovat moduulin nimisiä loggereita, eivät `ravit_edge.scheduler`:n
alapuussa. Stderriin tulostuvat, mutta eivät päädy `data/logs/scheduler.log`-
tiedostoon. Jos halutaan keskitetty loki: lisää nämä loggerit
`setup_logging()`:hin.

---

## Tehty

### ✅ #1. Result-haku double-trigger — TEHTY päivittäisellä retry-jobilla (4.5.2026)
Ratkaisu: hybridi joka säilyttää nykyisen T+30min-triggerin (nopea
ensimmäinen veto, saa odds + top-3 mahdollisimman tuoreena) ja lisää
päivittäisen `retry_incomplete_results`-cron-jobin klo 04:30 Stockholm-
aikaa. Cron etsii viim. 7 päivän racet joilla on NULL `finish_position`
tai NULL `kilometer_time_seconds` ja kutsuu fetch_resultsin jokaiselle.

**Empiirinen tulos** (4.5.2026 ekassa ajossa): 191 racea käytiin läpi
193 sekunnissa, 1039 → 936 vajaata runneria (-103 parannettu, ~10 %).
Loput aukot ovat lähinnä galoppi-radat (kmTime ei eksistoi ATG:ssa,
ratkeaa TODO #3:lla) ja oikeasti maaliin tulematta jääneet runnerit
(laukat, scratchit — normaalia ravidatan luonnetta).

**Toteutus:**
- `src/data/scheduler.py:retry_incomplete_results(db_path, lookback_days=7)`
- Cron-ajastus run_forever:ssä `CronTrigger(hour=4, minute=30, timezone=ATG_TZ)`
- CLI manuaalitestiin: `python -m src.data.scheduler retry-incomplete --lookback 7`
- 4 uutta pytestiä (kaikki vihreänä, 37/37 läpi)

**Vaihtoehto T+30min + T+6h hylättiin koska:**
- 4/30-data: jopa 3 vrk:n päästä Åby L3/Boden L2 olivat 35 % NULL → kiinteä
  T+6h ei riitä
- N×race-jobit raskaampia kuin yksi cron-ajo
- Cron-jobi tappaa myös harvinaiset "ATG täydentää useita päiviä myöhemmin"
  -tapaukset

### ✅ #4. Auto-restart kaatumisen jälkeen — TEHTY systemd:llä Hetzner-deployssa (4.5.2026)
Toteutettu `/etc/systemd/system/ravit-edge.service` -yksikössä:
- `Restart=on-failure`
- `RestartSec=10s`
- `TimeoutStopSec=30s`
- `journalctl -u ravit-edge` -lokit

Lisäksi cron-pohjainen päivittäinen DB-backup klo 04:00 Stockholm-aikaa,
14 vrk säilytys, `/home/ravi/backups/`-hakemistossa.
