# TODO — ravit-edge

Tunnetut, dokumentoidut tehtävät joita EI tehdä Hetzner-MVP-deploymentissa
mutta jotka pitää muistaa. Järjestys = prioriteetti.

## Datan kattavuus

### 1. Result-haku double-trigger ⚡ tärkeä
**Ongelma:** `_schedule_results_job` ajaa `fetch_results`:in vain kerran
`startTime + 30min`. ATG:n `/races/{id}` -vastaus täyttyy kuitenkin
vaiheittain:
- T+0…30min: vain `finalOdds` + top-3 sijoitukset, ei `kmTime`-objekteja
- T+1…2h: kaikki sijoitukset 1-N
- T+useita tunteja: kaikki `kmTime`-objektit täyttyvät

**Vaikutus:** Pelkän +30min-triggerin tuloksena `kilometer_time_seconds`
on NULL valtaosalla runnerista, ja `finish_position` puuttuu sijoituksilta
4+. Empiirinen vahvistus 4/30:n datalle: ennen jälkikäteiskorjausta vain
108/299 (36 %) runneria sai km-ajan, jälkikäteiskorjauksen jälkeen
248/299 (83 %).

**Korjaus:** Lisää `_schedule_results_job` ajastamaan KAKSI triggeriä
per race:
- T+30min (saa odds + top-3 mahdollisimman tuoreena)
- T+3h tai T+6h (täydellinen data)

Vaihtoehtoisesti **päivittäinen retry-jobi** joka klo esim. 04:30 käy
läpi viim. 7 päivän racet joilla on NULL `kilometer_time_seconds`
tai vajaita `finish_positions` ja yrittää hakea ATG:lta uudelleen.

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

### 4. Auto-restart kaatumisen jälkeen
Nykyinen `run_forever` ei käynnisty automaattisesti uudelleen jos
prosessi kaatuu (esim. uncaught exception, OOM). Hetzner-deployssa
tämä hoituu **systemd**-yksiköllä `Restart=on-failure` ja
`RestartSec=30s`. Dokumentoi systemd-template README:ssä.

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
