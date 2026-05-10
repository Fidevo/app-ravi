# Ravit Edge — Roadmap

## Missä olemme nyt (10.5.2026 — Vaihe 2, viikko 2)

Datankeräysjärjestelmä on pyörinyt tuotannossa 4.5.2026 alkaen.

**Datasetin tila (10.5.2026):**
- Ravipäiviä kerätty: 14 vrk (27.4 → 10.5)
- Trot-lähtöjä: **356** (galloppi suodatettu; race-luokka + ratakunto täytetty kaikille)
- Trot-runnereita: **~3 600**
- Hevoshistoriastartteja: **103 747** (Travsport)
- Odds-snapshotteja: **14 758** (T-15min / T-10min / T-5min / T-2min / result)
- Keräysvauhti: ~23 trot-lähtöä/vrk (isot lauantait ~35+)

**Ennuste:** Nykyvauhdilla datasetissä on ~950–1000 trot-lähtöä 8.6.2026
mennessä. Vaihe 3:n aloitus on mahdollinen myös hieman etuajassa jos data
katsotaan riittäväksi laadultaan ennen 8.6.

---

## Vaihe 1: Infrastruktuuri ✅ VALMIS

- ATG REST API client ja Travsport WebAPI client
- SQLite WAL-mode + skeemamigraatio
- Scheduler 4-vaiheisilla snapshoteilla per lähtö
- Result-haku +30min lähdön jälkeen + päivittäinen retry klo 04:30
- CLV-tracker ja bankroll management
- Hetzner CAX11 -palvelin Helsingissä + systemd auto-restart
- Päivittäinen DB-backup klo 04:00, 14 vrk säilytys
- UFW-palomuuri + fail2ban
- GitHub-versionhallinta

---

## Vaihe 2: Datankeräysjakso (27.4 – 8.6.2026, ~6 viikkoa)

**Pääprioriteetti: anna schedulerin pyöriä häiriöttä.**

### ✅ Viikko 1 (4.5–10.5): Datanlaadun varmistus

- **#1** `retry_incomplete_results` cron klo 04:30 — km-ajat ja sijoitukset täydentyvät
- **#2** Shoes/sulky-piirteet (6 saraketta) — kengät ja kärry per startti
- **#3** Gallop-suodatus — Bro Park, Göteborg Galopp, Jägersro Galopp pois kalenterista
- Lukitusraja-refresh — DateTrigger T-10min ennen 1. lähtöä, shoes/sulky lukitaan oikein

### ✅ Viikko 2 (10.5): Piirrerikastus

- **Track condition** — `horse_starts.track_condition` Travsportista (zero-API backfill)
- **Race-luokka ATG:sta** — `race_terms`, `race_min_earnings`, `race_max_earnings`,
  `race_age_group` ja `races.track_condition` kaikille 356 lähdölle
- Bugit #1, #5, #6, #8 korjattu (ks. `bug_analysis.md`)

### Viikot 3–6 (10.5–8.6): Minimaaliset muutokset

- Lokin silmäily 1–2 kertaa viikossa anomalioiden varalta
- Viikoittainen DB-varmuuskopiointi paikalliselle koneelle (manuaalinen)
- **#7 (matala):** ATG-clientin lokit → `scheduler.log` — `atg_client.logger`
  ja `travsport.logger` eivät tällä hetkellä kulje scheduler.log-tiedostoon,
  vain stderriin/journalctl:iin. Korjaus: lisää loggerit `setup_logging()`:hin.
- Mahdolliset pienet bugikorjaukset — ei uusia ominaisuuksia

---

## Vaihe 3: Mallin treenaus (8.6 – 22.6.2026, 2 viikkoa)

Vasta kun datasetissä on 600+ trot-lähtöä:

- Feature engineering -pipeline
  - Perussarakkeet: start_method, distance, handicap_meters, driver, trainer,
    shoes/sulky, atg-aggregaatit, Travsport-historia
  - Luokkapiirteet: race_min/max_earnings, race_age_group, track_condition
  - Johdetut piirteet: `barfota_law_active`, `horse_age`,
    race_number-normalisointi
- Walk-forward train/test split (ei random → ei data leakage)
- LightGBM lambdarank -mallin treenaus
- Softmax-kalibrointi voittotodennäköisyyksiksi
- Kalibrointitaulu ja Brier score -arviointi

---

## Vaihe 4: Backtest + paperitestaus (22.6 – 22.7.2026, 4 viikkoa)

- Walk-forward backtest viimeisten viikkojen datalla
- Paperitestauksen aloitus elävillä lähdöillä
  - Kirjaa value-pelit, älä pelaa rahalla
  - Tallenna T-2min kerroin pelihetkenä, vertaa final closing odds:iin
- CLV-mittaus käyttäen ATG-baselinea + devig-laskentaa
- Tavoite: vähintään 100 paperipeliä ennen päätöstä

---

## Vaihe 5: Päätöspiste (~22.7.2026)

Realistinen arvio kolmesta mahdollisesta lopputuloksesta:

**A: Edge todistettu (CLV +3% tai enemmän, n>100)**
→ Siirry vaiheeseen 6 pienillä rahoilla

**B: Edge epäselvä (CLV -2% to +3%, kohinaa)**
→ Kerää 4 viikkoa lisää dataa, treenaa malli uudelleen
→ Mahdollisesti lisää piirteitä (sharp-markkinat, pace-data)

**C: Ei edgea (CLV alle -2%)**
→ Pysähdy ja tutki bugit
→ Älä pelaa oikealla rahalla missään tapauksessa

Useimmat ML-vedonlyöntiprojektit eivät pääse tähän vaiheeseen
positiivisella lopputuloksella. Tämä on rehellinen näkymä, ei pessimismiä.

---

## Vaihe 6 (vain jos edge todistettu): Pelaaminen pienillä rahoilla

- Streamlit-dashboard päivän lähdöistä
- Manuaalinen pelaaminen 1–5€ panoksiin
- 4–8 viikkoa CLV-seurantaa oikealla rahalla
- Tilastollisesti merkittävä lopputulos vaatii 200–300+ peliä
- Bookkerivertailu (Unibet, Betsson, Veikkaus)
- Korjattava ennen V6: `correlated_kelly_adjust` (ks. `bug_analysis.md` #7)

---

## Vaihe 7 (vain jos pelaaminen tuottavaa): Skaalaus

- Mahdollinen Betfair Exchange -integraatio (tutki ensin Ruotsin
  ravien likviditeetti — voi olla riittämätön)
- Persistentit job-storet: vaihda `SQLAlchemyJobStore` jotta schedulerin
  restart-tilanteet eivät menetä snapshot-ikkunoita
- Sharp-markkinakertoimet (Pinnacle/Betfair) CLV-vertailuun — skeema valmis
- Telegram-/email-alerts kun value-peli löytyy

---

## Pitkän tähtäimen visio (3–12 kk)

- **Pace-piirteet** (position_at_800m, juoksuvire): ATG:n `/races/{id}` ei
  sisällä tätä. Travsport `/results`-endpointista ei myöskään saada. Vaatii
  joko per-race-endpointin selvitystä tai web-scrapingia — erillinen tutkimustehtävä.
- Sää-API-integraatio (Open-Meteo) — rata × sade × hevosen historia
- Conditional logit / Plackett-Luce trifecta-todennäköisyyksille
- Postgres jos datakanta kasvaa yli 500 MB
- Sukutaulupiirteet (isä/emänisä-tilastot)

---

## Tietoisesti pois jätetty

- **Pinnacle/Betfair sharp-kertoimet nyt:** Ruotsin ravien likviditeetti
  Betfairissa on todennäköisesti liian ohut CLV-pohjaksi. ATG-devig riittää
  MVP:lle. Lykätty vaiheeseen 7.
- **Toisen lajin lisääminen:** Jokainen laji vaatii oman lajituntemuksen
  kuukausia. Lykätty kunnes raviedge on todistettu.
- **Kaupallinen tuotteistaminen:** Ei mietitä ennen kuin malli tuottaa
  todistettua edgea oikealla rahalla useamman kuukauden ajan.
