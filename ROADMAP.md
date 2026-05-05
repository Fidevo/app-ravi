# Ravit Edge - Roadmap

Tämä on projektin korkean tason näkymä. Konkreettiset tekniset
tehtävät löytyvät [TODO.md](TODO.md)-tiedostosta.

## Missä olemme nyt (4.5.2026)

Datankeräysjärjestelmä pyörii tuotannossa Hetznerin pilvessä 24/7.
Scheduler on käynnistynyt 4.5.2026 ja kerää automaattisesti ATG- ja
Travsport-dataa Ruotsin raveista.

**Datasetin tila:**
- Lähtöjä: ~175+ (kasvaa päivittäin)
- Runnereita: ~1870+
- Hevoshistoriastartteja: 52400+ (Travsport)
- Odds-snapshotteja: 6440+ (T-15min, T-10min, T-5min, T-2min, result)
- Aikaväli: 27.4.2026 alkaen, kasvaa joka päivä

## Vaihe 1: Infrastruktuuri ✅ VALMIS

- ATG REST API client ja Travsport WebAPI client
- SQLite WAL-mode + skeemamigraatio
- Scheduler 4-vaiheisilla snapshoteilla per lähtö
- Result-haku +30min lähdön jälkeen (osittainen, katso TODO #1)
- CLV-tracker ja bankroll management
- Hetzner CAX11 -palvelin Helsingissä
- systemd-yksikkö auto-restart-ominaisuudella
- Päivittäinen DB-backup klo 04:00
- UFW-palomuuri + fail2ban
- GitHub-versionhallinta + workflow

## Vaihe 2: Datankeräysjakso (4.5 - 8.6.2026, 5 viikkoa)

**Pääprioriteetti: Anna schedulerin pyöriä häiriöttä.**

Tärkeintä tässä vaiheessa on minimoida muutokset ja maksimoida
luotettava datankeräys. 5 viikkoa kerää noin 600-800 lähtöä,
joka on minimi mielekkääseen ML-mallin treenaukseen.

**Tehdään ensimmäisen 1-2 viikon aikana ([TODO.md](TODO.md) viittauksilla):**

- TODO #3: Galoppirata-suodatus (Bro Park, Jägersro Galopp pois)

**Tehdään loput viikot 2-5:**

- TODO #7: ATG-clientin lokien yhdistäminen scheduler.log:iin
- Viikoittainen DB-varmuuskopiointi paikalliselle koneelle
- Lokin tarkistus 1-2 kertaa viikossa
- Mahdolliset pienet bugikorjaukset

## Vaihe 3: Mallin treenaus (8.6 - 22.6.2026, 2 viikkoa)

Vasta kun datasetissä on 600+ lähtöä omasta keräyksestä:

- Feature engineering -pipeline rakennetaan
- Walk-forward train/test split (ei random!)
- LightGBM lambdarank -mallin treenaus
- Softmax-kalibrointi voittotodennäköisyyksiksi
- Kalibrointitaulu ja Brier score -arviointi
- Ensimmäisen mallin tallentaminen ja dokumentointi

## Vaihe 4: Backtest + paperitestaus (22.6 - 22.7.2026, 4 viikkoa)

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
- TODO #4: Persistentit job-storet (kun pelataan oikealla rahalla,
  schedulerin restart-tilanteet eivät saa hävittää snapshotteja)
- Telegram-/email-alerts kun value-peli löytyy
- Mahdollinen julkinen sivusto:
  - Vaatii juridisen tarkistuksen Suomessa
  - Edge erodoituu jos signaalit julkistetaan
  - Mahdollisesti freemium-malli

## Pitkän tähtäimen visio (3-12 kk)

- Pace-piirteet (juoksuvire, position_at_800m): vaatii web-scraping-
  tutkimusta, ei kuulu nykyisiin API-endpointteihin
- Sää-API-integraatio (Open-Meteo) - rata × sade × hevosen historia
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
