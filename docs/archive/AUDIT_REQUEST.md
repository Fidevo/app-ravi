# Koodikatsaus — Ravit Edge

> Tämä tiedosto on ohje ulkopuoliselle auditoijalle.
> Kirjoita löydöksesi tiedostoon `AUDIT_FINDINGS.md` projektin juureen.

---

## Mikä tämä projekti on — lue tämä ensin

Olet auditoimassa **Ravit Edge** -projektia. Kyseessä on Ruotsin ravien
voittotodennäköisyyslaskin ja value-bet-detektori, joka rakentaa
LightGBM LambdaRank -mallin julkisista ATG- ja Travsport-rajapinnoista.

**Ennen kuin teet mitään muuta, lue nämä kolme tiedostoa kokonaan:**

1. **`README.md`** — arkkitehtuuri, ML-lähestymistapa, tietokantataulut, käyttö
2. **`ROADMAP.md`** — missä vaiheessa projekti on nyt ja miksi
3. **`KNOWN_ISSUES.md`** — jo tunnistetut avoimet bugit (älä raportoi näitä uudelleen)

Nämä tiedostot ovat kattavat ja ne on kirjoitettu juuri sinua varten.
Ilman näiden lukemista voit antaa vääriä signaaleja — esimerkiksi
raportoida "bugin" joka on jo tunnettu tai jo korjattu.

---

## Projektin konteksti auditoijalle

### Vaihe ja kypsyystaso

Projekti on **Vaihe 2 → Vaihe 3 -siirtymässä**. Datankeräys on pyörinyt
tuotannossa Hetzner-palvelimella noin 2 viikkoa. Feature engineering
-pipeline on juuri valmistunut ja validoitu. Mallin treenausta ei ole
vielä aloitettu.

Tässä vaiheessa kriittisimmät bugit ovat:
1. **Data leakage** — piirteisiin vuotaa dataa jota ei ollut saatavilla
   ennen lähtöä (vakava, tekee mallista hyödyttömän)
2. **Treeniesimerkkien korruptio** — finish_position-käsittely, ryhmittelyt,
   shift/rolling-logiikka
3. **Päättelyn oikeellisuus** — lasketaanko todennäköisyydet ja Kelly oikein

### Tekninen ympäristö

- Python 3.12, SQLite WAL-mode, LightGBM, pandas, APScheduler
- Hetzner CAX11, crontab-pohjainen ajastus (ei systemd-servicea)
- 104 pytest-testiä, kaikki vihreällä

### Tietolähteet ja niiden erityispiirteet

**ATG REST API** (`https://www.atg.se/services/racinginfo/v1/api`)
- Tarjoaa lähtölistat, pre-race-kertoimet (4 snapshotia/lähtö), tulokset
- Raportoi viralliset sijoitukset vain **top 6–8 hevoselle** — loput saavat NULL:n
  finish_position-sarakkeeseen vaikka hevonen ajoi (km_aika on tallessa)
- Tarjoaa valmiit aggregaatit per startti: `atg_driver_win_pct`,
  `atg_lifetime_win_rate`, `atg_best_km_for_this_setup` jne.
- Vain Ruotsin radat (countryCode == "SE") — ulkomaisten hevosten horse.id puuttuu

**Travsport WebAPI** (`https://api.travsport.se/webapi`)
- Hevosen koko urahistoria starttikohtaisesti (103 747 starttia, 2014→)
- `horse_starts`-taulu: finish_position, kilometer_time_seconds, win_odds_final
- Erityinen: `finish_position = 99` on sentinel-arvo DNF/disqualified-starteille
- `withdrawn = 1` tarkoittaa vetäytynyttä hevosta (ei ajanut)
- 7 vrk TTL-cache — ei haeta uudelleen jos tuore tieto löytyy

### Kriittiset design-päätökset jotka pitää ymmärtää

**Miksi LambdaRank eikä binääriluokittelu?**
Hevoset eivät ole riippumattomia — kyseessä on kilpailu. LambdaRank oppii
järjestämään hevoset saman lähdön sisällä, mikä on oikeampi formulointi
kuin yksittäisen hevosen P(win) luokitteleminen muista riippumattomana.

**Miksi fill_finish_positions()?**
ATG antaa sijoitukset vain top 6–8:lle. Ilman täyttölogiikkaa 37 % treeniesimerkeistä
puuttuisi kokonaan. `fill_finish_positions()` järjestää "unplaced" hevoset km-ajan
mukaan ja antaa heille synteettiset sijoitukset (7., 8., jne.) virallisten jälkeen.
Vetäytyneet saavat viimeiset sijoitukset. Tulevat lähdöt (kaikki NULL) jätetään
koskemattomiksi.

**Miksi horse_starts + runners yhdistettynä form_features():ssä?**
runners-taulussa on vain 14 päivää dataa — suurimmalla osalla hevosista 0–2
omaa starttia. Ilman horse_starts-historiaa 95 % muotopiirteistä on NaN:ia.
Pool-pohjainen laskenta yhdistää molemmat lähteet; runners voittaa duplikaateissa.

**Miksi shift(1) ennen rolling()?**
Estää data leakagen: nykyisen lähdön tulos ei saa vaikuttaa sen omiin piirteisiin.

**Barfota-laki (talvikielto):**
ATG ei raportoi kenkätietoja 1.12.–28.2. välisenä aikana → kaikki `shoes_*`-sarakkeet
ovat NULL talvella. `barfota_law_active`-piirre erottaa "hevosella ei ole kenkiä"
ja "talvikielto on voimassa, ei tietoa". Ilman tätä piirrettä malli ei pysty
erottamaan näitä tilanteita toisistaan.

---

## Auditoinnin laajuus

### Ensisijainen — etsi nämä

**1. Data leakage**
- `src/features/build_features.py`: `form_features()`, `driver_trainer_features()`,
  `race_setup_features()` — vuotaako nykyisen lähdön data piirteisiin?
- Erityisesti: `shift(1)` ja `rolling()`-kutsut — onko ne oikein jokaisessa
  groupby-kontekstissa?
- `driver_trainer_features()`: `closed="left"` rolling-ikkunassa — estääkö tämä
  saman päivän lähdön vuotamisen? Onko drop_duplicates oikeassa paikassa?

**2. Treenilogiikka**
- `src/features/build_features.py`: `fill_finish_positions()`
  - Mitä tapahtuu jos samassa lähdössä on sekä vetäytyneitä että unplaced-hevosia
    joilla ei ole km_aikaa? Onko järjestys deterministinen?
  - Mitä jos kaikki lähdön hevoset vetäytyvät? (edge case)
  - Voisiko synteettinen sijoitus olla epälooginen suhteessa km_aikaan?

**3. pool-pohjainen form_features()**
- `src/features/build_features.py`: `form_features(runners, horse_starts, n_last)`
  - Deduplikaatiologiikka: `drop_duplicates(keep="last")` poistaa horse_starts-rivin
    kun runners sisältää saman (horse_id, race_date). Onko sort_values-järjestys
    ennen drop_duplicates varmasti oikein (runners tulee viimeisenä)?
  - Mitä jos horse_starts sisältää tulevaisuuden startteja (race_date > runners)?
    Testattu, mutta onko edge case katettu täysin?
  - horse_id-tyyppi: konvertoitaanko str:ksi molemmissa DataFrameissa ennen
    concat/merge? Voisiko integer vs. string -tyyppiristiriita jäädä huomaamatta?

**4. Ranker ja todennäköisyyslaskenta**
- `src/models/ranker.py`: `predict_win_probabilities()`
  - Softmax: `np.exp(s - s.max()) / np.exp(s - s.max()).sum()` — onko tämä
    numeerisesti vakaa? Onko groupby("race_id") oikein?
  - `detect_value_bets()`: voiko lähtö jolla on vain yksi hevonen tuottaa
    outlierin (P=1.0, edge = ∞)?

**5. Scheduler ja datankeruu**
- `src/data/scheduler.py`: `_upsert_horse()`, `_upsert_runner()`, `_upsert_race()`
  - Käytetäänkö `merge` vai `insert or replace`? Voiko uudelleenajo ylikirjoittaa
    olemassa olevan tuloksen virheellisellä NULL:lla?
  - `backfill_race_class()`: mitä tapahtuu jos ATG:n terms-kenttä on epäodotettu
    muoto? Onko parseri defensiivinen?
  - Aikavyöhyke: onko kaikki datetime-käsittely UTC-pohjaista? Sekaantuuko
    Stockholm-aika (UTC+1/+2) ja UTC missään?

**6. Testit**
- Ovatko testit aidosti itsenäisiä vai jakavatko ne tilaa?
- Onko jokin kriittinen polku (esim. data leakage -suojaus) testaamatta?
- Testataan oikeita asioita: onko yhtään testiä joka menee läpi vaikka
  buginen implementaatio olisi käytössä?

### Toissijainen — silmäile myös

- Rate limiting: onko 1 req/s -raja varmasti voimassa molemmissa asiakkaissa
  kaikissa skenaarioissa (retry-looppi, concurrent calls)?
- Muistinkäyttö: `horse_starts` on 103 747 riviä — kuormittuuko muisti
  feature-pipeline:ssa isommilla dataseteillä?
- SQLite WAL-mode: voiko useamman samanaikaisen kirjoituksen tilanne (scheduler
  + manuaalinen ajo) johtaa lukitusten kanssa ongelmiin?
- `_resolve_cols()` ranker.py:ssä: puuttuvat sarakkeet logitetaan varoituksena
  ja ohitetaan hiljaisesti — onko tämä oikea käytös vai pitäisiko joissain
  tilanteissa kaatua ääneen?

---

## Mitä EI tarvitse auditoida

- `src/betting/clv_tracker.py` — ei käytössä vielä
- `src/ui/` — ei olemassa vielä
- `.gitignore`, `requirements.txt`, infrastruktuurikonfiguraatio
- Tiedossa olevat ongelmat `KNOWN_ISSUES.md`:ssä (#2, #3, #4, #7, #9, #10, #12)

---

## Mallin kehittämiseen liittyvät kysymykset

Kun olet tehnyt bugi-auditoinnin, toivomme myös vapaata pohdintaa seuraavista:

**Datan laatu ja luotettavuus:**
- `form_avg_finish_5` lasketaan kaikista starteista riippumatta matkasta tai
  starttimuodosta (autostart vs. volttilähtö). Onko tämä ongelma? Miten
  segmentoisit muotopiirteet luotettavammiksi?
- ATG raportoi vain top 6–8 sijoituksen ja loput ovat synteettisiä (km-ajan
  mukaan). Onko synteettinen sijoitus riittävän luotettava signaali mallille,
  vai pitäisiko nämä rivit jättää kokonaan pois treeniä varten?
- `win_odds_final` on ATG:n pari-mutuel-kerroin (takeout ~18–22 %). Kuinka
  informatiivinen tämä on verrattuna Pinnacle/Betfair sharp-kertoimiin?
  Olisiko parempi käyttää implied probability × devig-korjausta?

**Piirteet:**
- Onko jotain ilmeistä piirrettä jota ei ole mietitty? Ravien kontekstissa
  esimerkiksi: lähtöpaikka × starttimuoto -yhdistelmä, hevosen ikä ×
  lähdön taso, valmentajan menestys tietyllä radalla?
- `track_horse_win_rate` on 97.5 % NaN (vain 14 pv dataa). Onko vaihtoehtoinen
  tapa estimoida rata-kokemus horse_starts-datan avulla (pidempi historia)?
- Kengät (`shoes_changed_front`, `shoes_changed_back`) ovat NULL-arvoja talvella
  barfota-lain takia. `barfota_law_active`-piirre on käytössä, mutta riittääkö
  se? Pitäisikö shoes-piirteet imputoida tai jättää pois talvikaudelta?

**Mallin arkkitehtuuri:**
- LambdaRank vs. vaihtoehdot (XGBoost rank:pairwise, CatBoost YetiRank,
  Plackett-Luce): onko LambdaRank oikea valinta tässä kontekstissa?
- Softmax-kalibrointi per lähtö on yksinkertainen. Olisiko isotonic regression
  tai Platt scaling parempi kalibrointimenetelmä? Missä tilanteessa softmax
  antaa vääristyneitä todennäköisyyksiä?
- Walk-forward split: 14 päivää on lyhyt. Mikä on minimaalinen aikaikkuna
  validille walk-forward -evaluoinnille trotting-kontekstissa?

**Yleiset havainnot:**
- Onko jotain muuta mitä näet projektissa joka voisi parantaa mallin
  luotettavuutta tai ennustustarkkuutta tulevaisuudessa?

---

## Löydösten raportointi

**Kirjoita löydöksesi tiedostoon `AUDIT_FINDINGS.md` projektin juureen.**

Suositeltu rakenne:

```markdown
# Auditoinnin löydökset — Ravit Edge

> Auditoija: [nimi tai tunnus]
> Päivämäärä: YYYY-MM-DD
> Auditoidut tiedostot: [lista]

## Kriittiset löydökset (korjattava ennen treeniä)
...

## Merkittävät löydökset (korjattava ennen tuotantoa)
...

## Pienet havainnot (koodihygienia, ei tuotantovaikutusta)
...

## Testikattavuus — arvio
...

## Ideat ja ehdotukset mallin kehittämiseen
...

## Yhteenveto
Löydösten kokonaismäärä: X kriittistä, Y merkittävää, Z pientä
```

Arvioi jokainen löydös:
- **Kriittinen** — voi tehdä mallista hyödyttömän tai korruption (esim. data leakage)
- **Merkittävä** — vaikuttaa tuloksiin mutta ei kaada järjestelmää
- **Pieni** — koodihygienia, kommentit, edge caset jotka eivät esiinny tuotannossa

Jos et löydä bugeja jossakin alueessa, kirjoita sekin ylös — "ei löydöksiä"
on yhtä arvokas tieto kuin löydökset.
