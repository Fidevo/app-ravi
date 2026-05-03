# Ravit Edge - Projektin Etenemissuunnitelma (Roadmap)

Tässä on katsaus siihen, mitä olemme jo rakentaneet ja mitä on vuorossa seuraavaksi. Tämä dokumentti auttaa hahmottamaan projektin kokonaiskuvan.

## ✅ TEHTY (Viikko 1: Datankeräys & Infrastruktuuri)

Luotettava datankeräys on kaiken perusta. Nämä osat ovat valmiina ja pyörivät tuotannossa (tai valmiina pyörimään tuotannossa schedulerin kautta).

- **ATG API Client**: Hakee lähtölistat, hevoset, kuskit ja kertoimet.
- **Travsport API Client**: Hakee hevosten tarkan starttihistorian (sijoitukset, kilometriajat, jne.), jota ATG ei suoraan anna.
- **Tietokanta (SQLite WAL-mode)**: Optimoitu jatkuvaan kirjoitukseen. Rakennettu taulut: `races`, `horses`, `runners`, `horse_starts`, `odds_snapshots`.
- **Scheduler (Dataputki)**:
  - Hakee päivän lähdöt aamuyöllä.
  - Tallentaa pre-race-kertoimet 4 eri pisteessä (T-15min, T-10min, T-5min, T-2min) markkinaliikkeiden seuraamiseksi.
  - Hakee tulokset automaattisesti +30min lähdön jälkeen.
- **CLV-Tracker (Closing Line Value)**: Työkalut voittavan vedonlyönnin mittaamiseen (marginaalin / vigin poistaminen, CLV-prosentin laskenta).

---

## ⏳ TEKEMÄTTÄ (Viikot 2-3: Koneoppiminen & Sharp-kertoimet)

Nyt kun keräämme laadukasta dataa, seuraava askel on sen jalostaminen todennäköisyyksiksi.

- **Sharp-kertoimien integrointi (Pinnacle / Betfair Exchange)**
  - Tällä hetkellä keräämme ATG:n (Toto) kertoimia, joissa on korkea marginaali (15-25%).
  - Mallin kalibrointiin ja vertailuun tarvitsemme "fiksut" kertoimet (Betfair Exchange koska tämä on ilmainen).
- **Feature Engineering (Piirteiden rakennus)**
  - Raakadatan muuttaminen ML-mallille sopivaksi (esim. hevosen viimeisen 5 startin keskiarvo-km-aika, kuskin ja valmentajan vire, laukkaprosentti).
  - Markkinaliikkeiden (steam/drift) laskenta kerätyistä T-15 -> T-2 snapshoteista.
- **LightGBM -koneoppimismallin koulutus**
  - Opetetaan malli ennustamaan hevosen voittotodennäköisyys.
  - Opetusdatana käytetään `horse_starts` -taulun historiallista dataa.
- **Backtestaus (Historiallinen testaus)**
  - Simuloidaan mallin vetoja menneisyyden dataan: "Olisiko tämä malli tehnyt rahaa viimeisen 6 kuukauden aikana?"

---

## 🔮 TULEVAISUUS (Viikot 4+: Käyttöliittymä & Automatisointi)

Kun malli on todistettu tuottavaksi backtestauksessa ja paperitestauksessa, rakennetaan työkalut sen päivittäiseen käyttöön.

- **Streamlit-käyttöliittymä (Dashboard)**
  - Visuaalinen näkymä päivän lähtöihin.
  - Näyttää rinnakkain mallin todennäköisyyden, markkinan kertoimen ja ylikertoimen (Value).
- **Vedonlyönti-alertit (Telegram / Email)**
  - Automaattinen ilmoitus, kun T-10min kohdalla löytyy selkeä ylikerroin.
- **Live-seuranta**
  - Oma CLV-seurantadashboard: kuinka hyvin omat vedot voittavat päätöskertoimen.
