# TODO — ravit-edge

> Korkean tason aikataulu ja faasit: katso [ROADMAP.md](ROADMAP.md)
> Tämä tiedosto sisältää konkreettiset tekniset tehtävät
> yksityiskohtineen ja perusteluineen.

Elämme nyt **Vaihetta 2, viikkoa 2** (10.5.2026). Viikko 1 käytettiin
datanlaadun varmistukseen — kaikki kriittiset tehtävät tehty. Järjestys
= prioriteetti jäljellä olevissa tehtävissä.

---

## Avoinna — Vaihe 2 (viikot 2–6, 10.5–8.6.2026)

### 7. ATG-clientin lokit eivät kulje scheduler.log-tiedostoon
`src.data.atg_client.logger` ja `src.data.scrapers.travsport.logger`
ovat moduulin nimisiä loggereita, eivät `ravit_edge.scheduler`:n
alapuussa. Stderriin tulostuvat, mutta eivät päädy `data/logs/scheduler.log`-
tiedostoon.

**Korjaus:** lisää nämä loggerit `setup_logging()`:hin propagaation kautta
tai konfiguroi ne eksplisiittisesti.

**Prioriteetti:** matala — scheduler.log on luettava, journalctl näyttää
kaiken. Tehdään kun sopii, ei blokoi datankeräystä.

---

## Avoinna — Vaihe 7 (vain jos pelaaminen tuottavaa)

### 5. Persistentit job-storet
Nykyinen `BlockingScheduler` käyttää `MemoryJobStore`-oletusta —
ajastetut jobit häviävät restartissa. `_initial_setup` korjaa tämän
osittain käynnistyksessä, mutta jos restart osuu juuri snapshot-ikkunaan
(T-15min … T-2min), snapshotit menetetään.

**Korjaus:** vaihda `SQLAlchemyJobStore` joka persistoi jobit DB:hen.
HUOM: vaatii että `args` on serialisoitavissa (pickle) — esim.
TravsportAPIClient-instanssi pitää välittää muulla tavalla.

**Prioriteetti:** matala nyt — MemoryJobStore riittää datankeräys- ja
paperitestausvaiheessa. Kriittiseksi vasta Vaiheessa 7 kun pelataan
oikealla rahalla eikä yhtäkään snapshotia voi menettää.

### 6. Pre-race snapshotin lähde-monipuolisuus
MVP käyttää vain `atg_pari_mutuel`-pool-kerrointa (vinnare-game).
Vaihe 7 tuo Pinnacle / Betfair Exchange / Unibet -kertoimet sharp-
markkinoiden CLV-vertailuun. Skeema on jo valmis (`source`-kenttä).

**Prioriteetti:** ei nyt — ATG-devig riittää kalibrointiin Vaiheissa 3-5.

---

## Tehty — Vaihe 2, viikko 1 (4.5–10.5.2026)

### ✅ #3. Galoppirata vs ravirata -erottelu — TEHTY (10.5.2026)
ATG palautti SE-kalenterissa myös gallop-ratoja (Bro Park, Göteborg
Galopp, Jägersro Galopp). Galopin lähdöillä ei ole `kmTime`-objekteja
→ runnerit olisivat aina NULL km-ajalla → `retry_incomplete_results`
hakisi niitä turhaan joka päivä (~50–70+ turhaa API-kutsua/vrk).

**Toteutus:**
- `atg_client.py`: `get_calendar_day` suodattaa nyt `sport == "trot"`
  -ehdolla. Gallop-radat eivät enää päädy DB:hen.
- `scheduler.py`: `GALLOP_TRACKS` frozenset (`{"Bro Park",
  "Göteborg Galopp", "Jägersro Galopp"}`). `retry_incomplete_results`
  lisää `NOT IN` -filterin jo olemassa oleville gallop-riveille.
- **2 uutta pytestiä** (48/48 läpi).

**Huomio:** Alkuperäinen lista sisälsi vain Bro Park + Jägersro Galopp.
Tuotantodatan auditoinnissa (10.5.2026) havaittiin kolmas gallop-rata,
Göteborg Galopp (72 runneria DB:ssä). Lisätty samana päivänä.

### ✅ Shoes/sulky-auditointi tuotantodatasta — TEHTY (10.5.2026)
Tarkistettiin että #2-implementaatio toimii oikein tuotannossa.

**Löydökset (3 537 trot-runneria, 27.4–10.5.2026):**
- shoes_front/back: 90 % täynnä (371 NULL = shoes.reported=False, odotettua)
- shoes_changed: 83 % täynnä (248 lisää NULL = ATG:n tunnettu käytös,
  jossa changed-kenttä puuttuu vaikka reported=True — koodissa oikein None)
- sulky_type + sulky_changed: aina yhdenmukaisesti täynnä tai NULL (✅)
- Ei "mahdottomia" tapauksia (shoes NULL mutta changed täynnä: 0 kpl ✅)
- Sulky-jakauma: VA 88 %, AM 12 % (pysyvä)

**Ainoa löydetty ongelma:** Göteborg Galopp puuttui `GALLOP_TRACKS`-
listasta (ks. #3 yllä). Korjattu.

### ✅ Lukitusraja-refresh-jobi — TEHTY dynaamisella DateTriggerillä (5.5.2026)
**Konteksti:** Ruotsin raviurheilussa kengitys- (barfota) ja kärry- (sulky)
tiedot lukitaan **15min ennen päivän 1. lähdön starttia**. Sitä ennen
valmentaja voi muuttaa varustetta vapaasti. Schedulerin `_daily_setup`
03:00 saattoi siis saada vajaita/stale shoes/sulky-tietoja.

**Toteutus:**
- `fetch_daily_races` palauttaa nyt `stats["first_race_start_utc"]`
- `_schedule_first_race_refresh()` ajastaa `refresh_day_runners` jobin
  DateTriggeriin `first_race_start_utc - 10min`
- `refresh_day_runners()` kutsuu `fetch_daily_races(scheduler=None,
  travsport=None)` — pelkkä runner-päivitys, EI uudelleen-ajasta
  snapshot/result-jobeja
- CLI: `python -m src.data.scheduler refresh-day-runners --date YYYY-MM-DD`

**Huomioi myös:**
- Talvikielto 1.12.–28.2.: ATG ei palauta barfota-tietoa → kaikki None
- 2-vuotiaat: kengät aina pakollisia, ATG raportoi shoes=true molemmissa
- Feature engineering -vaiheessa: `barfota_law_active`, `horse_age`

**4 uutta pytestiä** (46/46 läpi).

### ✅ #2. Shoes/sulky -piirteet ATG:n start.horse-objektista — TEHTY (5.5.2026)
Lisätty 6 uutta saraketta `runners`-tauluun:
- `shoes_front`, `shoes_back` (BOOL): kenkiä etu/taka
- `shoes_changed_front`, `shoes_changed_back` (BOOL): muutos vs edellinen startti
- `sulky_type` (TEXT): `VA`=Vanlig (Standard), `AM`=Amerikansk
- `sulky_changed` (BOOL): tyyppi tai väri muutettu

**Toteutus:** `_shoes_sulky_fields(horse)`-helper `scheduler.py`:ssa.
Käsittelee puuttuvat `changed`-kentät (None, ei False) ja
`reported=false`-tilan oikein.

**Takautuva uudelleenajo:** 27.4–4.5 shoes/sulky populoitu manuaalisesti.

**5 uutta pytestiä.**

### ✅ #1. Result-haku double-trigger — TEHTY päivittäisellä retry-jobilla (4.5.2026)
Hybridi: T+30min-trigger (nopea veto) + päivittäinen `retry_incomplete_results`
cron klo 04:30 Stockholm-aikaa. Cron etsii viim. 7 päivän racet joilla
on NULL `finish_position` tai NULL `kilometer_time_seconds`.

**Empiirinen tulos (4.5.2026):** 191 racea / 193 sek, -103 vajaan
runnerin aukkoa (~10 %). Loput aukot: galloppi (korjattu #3:lla) ja
oikeasti maaliin tulematta jääneet (laukat, scratchit — normaalia).

**4 uutta pytestiä** (37/37 läpi).

### ✅ #4. Auto-restart kaatumisen jälkeen — TEHTY systemd:llä (4.5.2026)
`/etc/systemd/system/ravit-edge.service`:
- `Restart=on-failure`, `RestartSec=10s`, `TimeoutStopSec=30s`
- `journalctl -u ravit-edge` -lokit

Lisäksi cron-pohjainen päivittäinen DB-backup klo 04:00 Stockholm-
aikaa, 14 vrk säilytys, `/home/ravi/backups/`.
