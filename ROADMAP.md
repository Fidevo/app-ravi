# Ravit Edge - Roadmap

Tämä on projektin korkean tason näkymä. Konkreettiset tekniset
tehtävät löytyvät [TODO.md](TODO.md)-tiedostosta.

## Missä olemme nyt (10.5.2026 — Vaihe 2, viikko 2)

Datankeräysjärjestelmä on pyörinyt tuotannossa 4.5.2026 alkaen.
Vaihe 2 alkoi käytännössä jo 27.4.2026 kun scheduler ajettiin
manuaalisesti ensimmäistä kertaa ennen Hetzner-deploytä.

**Datasetin tila (10.5.2026):**
- Ravipäiviä kerätty: 14 vrk (27.4 → 10.5)
- Trot-lähtöjä: **325** (puhtaasti raveja, galloppi suodatettu)
- Trot-runnereita: **3 537**
- Hevoshistoriastartteja: **103 747** (Travsport)
- Odds-snapshotteja: **14 758** (T-15min / T-10min / T-5min / T-2min / result)
- Keräysvauhti: ~23 trot-lähtöä/vrk (isot lauantait ~35+)

**Ennuste:** Nykyvauhdilla datasetissä on ~950–1000 trot-lähtöä 8.6.2026
mennessä — selvästi yli alkuperäisen 600-lähdön minimin. Vaihe 3:n
aloitus on mahdollinen myös hieman etuajassa jos data katsotaan
riittäväksi laadultaan ennen 8.6.

## Vaihe 1: Infrastruktuuri ✅ VALMIS

- ATG REST API client ja Travsport WebAPI client
- SQLite WAL-mode + skeemamigraatio
- Scheduler 4-vaiheisilla snapshoteilla per lähtö
- Result-haku +30min lähdön jälkeen + päivittäinen retry klo 04:30 ✅
- CLV-tracker ja bankroll management
- Hetzner CAX11 -palvelin Helsingissä
- systemd-yksikkö auto-restart-ominaisuudella
- Päivittäinen DB-backup klo 04:00, 14 vrk säilytys
- UFW-palomuuri + fail2ban
- GitHub-versionhallinta + workflow
- Kengät/sulky-piirteet (shoes/sulky, 6 saraketta) ✅
- Lukitusraja-refresh T-10min ennen 1. lähtöä ✅
- Gallop-suodatus (sport=="trot", 3 tunnettua gallop-rataa) ✅

## Vaihe 2: Datankeräysjakso (27.4 – 8.6.2026, ~6 viikkoa)

**Pääprioriteetti: Anna schedulerin pyöriä häiriöttä.**

Tärkeintä on minimoida muutokset ja maksimoida luotettava
datankeräys. Viikko 1 käytettiin tärkeimpien bugien korjaamiseen
ennen kuin data-aukot ehtivät kasvaa.

### ✅ Viikko 1 (4.5–10.5): Datanlaadun varmistus

- ✅ TODO #1: `retry_incomplete_results` cron klo 04:30 (4.5.2026)
- ✅ TODO #2: Shoes/sulky-piirteet ATG:n horse-objektista (5.5.2026)
- ✅ Lukitusraja-refresh: DateTrigger T-10min ennen 1. lähtöä (5.5.2026)
- ✅ TODO #3: Gallop-suodatus — Bro Park, Göteborg Galopp, Jägersro Galopp
  pois kalenterista ja retry-kyselystä (10.5.2026)
- ✅ Shoes/sulky-auditointi tuotantodatasta: löydettiin ja korjattiin
  puuttuva Göteborg Galopp `GALLOP_TRACKS`-listasta (10.5.2026)

### Viikot 2–6 (10.5–8.6): Minimaaliset muutokset

- TODO #7: ATG-clientin lokit → scheduler.log (matala prioriteetti,
  tehdään kun sopii)
- Viikoittainen DB-varmuuskopiointi paikalliselle koneelle (manuaalinen)
- Lokin silmäily 1-2 kertaa viikossa anomalioiden varalta
- Mahdolliset pienet bugikorjaukset — ei uusia ominaisuuksia

## Vaihe 3: Mallin treenaus (8.6 – 22.6.2026, 2 viikkoa)

Vasta kun datasetissä on 600+ trot-lähtöä omasta keräyksestä
(saavutamme tämän jo ennen 8.6):

- Feature engineering -pipeline rakennetaan
  - Perussarakkeet: start_method, distance, handicap_meters, driver,
    trainer, shoes/sulky, atg-aggregaatit, Travsport-historia
  - Mahdolliset johdetut piirteet: `barfota_law_active`, `horse_age`,
    päivän 1. lähdön `race_number`-normalisointi
- Walk-forward train/test split (ei random! → ei data leakage)
- LightGBM lambdarank -mallin treenaus
- Softmax-kalibrointi voittotodennäköisyyksiksi
- Kalibrointitaulu ja Brier score -arviointi
- Ensimmäisen mallin tallentaminen ja dokumentointi

## Vaihe 4: Backtest + paperitestaus (22.6 – 22.7.2026, 4 viikkoa)

- Walk-forward backtest viimeisten viikkojen datalla
- Paperitestauksen aloitus elävillä lähdöillä
  - Kirjaa value-pelit, älä pelaa rahalla
  - Tallenna T-2min kerroin pelihetkenä, vertaa final closing odds:iin
- CLV-mittaus käyttäen ATG-baselinea + devig-laskentaa
- Tavoite: vähintään 100 paperipeliä ennen päätöstä

## Vaihe 5: Päätöspiste (~22.7.2026)

Realistinen arvio kolmesta mahdollisesta lopputuloksesta:

**A: Edge todistettu (CLV +3% tai enemmän, n>100)**
→ Siirry vaiheeseen 6 pienillä rahoilla

**B: Edge epäselvä (CLV -2% to +3%, kohinaa)**
→ Kerää 4 viikkoa lisää dataa, treenaa malli uudelleen
→ Mahdollisesti lisää piirteitä (TODO #6: sharp-markkinat)

**C: Ei edgea (CLV alle -2%)**
→ Pysähdy ja tutki bugit
→ Älä pelaa oikealla rahalla missään tapauksessa

Useimmat ML-vedonlyöntiprojektit eivät pääse tähän vaiheeseen
positiivisella lopputuloksella. Tämä on rehellinen näkymä, ei pessimismiä.

## Vaihe 6 (vain jos edge todistettu): Pelaaminen pienillä rahoilla

- Streamlit-dashboard päivän lähdöistä
- Manuaalinen pelaaminen 1-5€ panoksiin
- 4-8 viikkoa CLV-seurantaa oikealla rahalla
- Tilastollisesti merkittävä lopputulos vaatii 200-300+ peliä
- Bookkerivertailu (Unibet, Betsson, Veikkaus)
- Bankroll management aktiivisesti

## Vaihe 7 (vain jos pelaaminen tuottavaa): Skaalaus

- Mahdollinen Betfair Exchange -integraatio (tutki ensin Ruotsin
  ravien likviditeetti - voi olla riittämätön)
- TODO #5: Persistentit job-storet (kun pelataan oikealla rahalla,
  schedulerin restart-tilanteet eivät saa hävittää snapshotteja)
- Telegram-/email-alerts kun value-peli löytyy
- Mahdollinen julkinen sivusto:
  - Vaatii juridisen tarkistuksen Suomessa
  - Edge erodoituu jos signaalit julkistetaan
  - Mahdollisesti freemium-malli

## Pitkän tähtäimen visio (3-12 kk)

- Pace-piirteet (juoksuvire, position_at_800m): vaatii web-scraping-
  tutkimusta, ei kuulu nykyisiin API-endpointteihin
- Sää-API-integraatio (Open-Meteo) — rata × sade × hevosen historia
- Conditional logit / Plackett-Luce trifecta-todennäköisyyksille
- Postgres jos datakanta kasvaa yli 500 MB
- Sukutaulupiirteet (isä/emänisä-tilastot)
- Tallikommentit (subjektiiviset, vaatii NLP-analyysia)

## Tietoisesti pois jätetty

Nämä ovat tulleet keskusteluissa esiin mutta on jätetty pois
nykyisestä roadmapista perusteltuna:

- **Pinnacle/Betfair sharp-kertoimet nyt**: Ruotsin ravien likviditeetti
  Betfairissa on todennäköisesti liian ohut CLV-pohjaksi. Sertifikaatti-
  pohjainen kirjautuminen iso työ. ATG-devig riittää MVP:lle. Lykätty
  vaiheeseen 7.

- **Toisen lajin lisääminen**: Datankeräysmoottori on osittain laji-
  riippumaton, mutta jokainen laji vaatii oman lajituntemuksen
  kuukausia. Lykätty kunnes raviedge on todistettu.

- **Tallikommentit ja muut subjektiiviset tiedot**: Vaativat NLP:tä
  tai web-scrapingia. Lykätty vaiheeseen 7+.

- **Kaupallinen tuotteistaminen**: Ei mietitä ennen kuin malli
  tuottaa todistettua edgea oikealla rahalla useamman kuukauden ajan.
