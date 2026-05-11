# Rata-piirteet — prioriteetti #1 ennen mallin treenausta

> Auditoija: Claude (Opus 4.7), 10.5.2026
> Konteksti: käyttäjän huomautus että rata-rakenne määrittelee mallin
> oppivuutta enemmän kuin per-runner-analytiikka — täysin oikein.
> Tämä tiedosto **korvaa prioriteettijärjestyksessä**
> TASK_TRAVRONDEN_INVESTIGATION.md:n. Tehdään tämä ensin.

---

## 1. Miksi rata-piirteet ovat #1

LightGBM on erinomainen oppimaan **interaktioita** — mutta vain jos
raakapiirteet ovat saatavilla. Tällä hetkellä projekti tietää:

- ✅ Rata-NIMEN (`races.track` = "Solvalla", "Färjestad", …)
- ❌ Rata-RAKENTEEN (loppusuoran pituus, open stretch, jne.)

Ilman rakennetietoa malli näkee jokaisen radan **omana ainutlaatuisena
mustana laatikkona**. Se voi oppia *"Färjestadissa eturata voittaa usein"*,
mutta ei pysty yleistämään tätä uusille radoille tai päättelemään **miksi**.

Kun lisätään rakennepiirteet, malli voi oppia:

- "Pitkä loppusuora (>200 m) + autostart → eturadan etu pienenee"
- "Open stretch -rata → passing edge → korkea lähtörata vähemmän haitta"
- "Pieni rata (800 m) + voltti → asetelma ratkaisee, sisäradan edge suuri"
- "Lyhyt loppusuora (<150 m) → spets/etupaikka ratkaisee → inside_post arvokkaampi"

Käyttäjän huomautus oli oikea: **ilman näitä raakapiirteitä malli on
"väärässä arvioissa"** — se voi oppia rata × outcome -korrelaatioita
mutta ei ymmärrä raviradan fysiikkaa.

LightGBM ei tarvitse käsin tehtyjä interaktiopiirteitä — puurakenne tekee
ne automaattisesti **kun ja vain kun** raakapiirteet ovat puiden saatavilla.

---

## 2. Mitä piirteitä haetaan ja mistä

### 2.1 Pakollinen ydinjoukko (per rata, ei muutu projektin elinaikana)

Travrondenspel.se:n `/round/{id}/statistics/`:n `round.tracks[i]` -objektista:

| Kenttä | Tyyppi | Esimerkki (Färjestad) | Miksi tärkeä |
|---|---|---|---|
| `id` | str | "F" | Travrondenin trackCode |
| `atg_id` | int | 15 | **Suora yhdistäjä ATG-rataan** |
| `name` | str | "Färjestad" | yhdistäjä `races.track`-arvoon |
| `length_total` | int (m) | 1000 | Radan koko vaikuttaa pace-dynamiikkaan |
| `length_home_stretch` | int (m) | 177 | **#1 tärkein** — loppukirivaroitus |
| `width_1` | int | 2040 | Sisempi leveys (autostart-mittaus?) |
| `width_2` | int | 2110 | Ulompi leveys |
| `dosage` | int | 1700 | Kaarteen kallistus (asteen yksikkö epäselvä) |
| `open_stretch` | bool | false | Onko toinen passing-linja loppusuoralla |
| `angled_wing` | bool | false | Kaltevat keulakaaret autostartille |
| `country` | str | "SE" | SE-suodatus |
| `slug` | str | "farjestad" | URL-tunniste |

**Hyöty per piirre:**

- **`length_home_stretch`** — tunnetuin rata-vakio raveissa. Färjestadin 177 m on suhteellisen lyhyt → spets-rata. Solvallan ~220 m on pitkä → passing edge. Tämä yksittäinen luku selittää suuren osan radakohtaisesta vaihtelusta.
- **`open_stretch`** — toinen passing-linja vähentää eturadan dominanssia dramaattisesti. Esimerkiksi Mantorpissa ja Boråsissa on open stretch — malli pitää tietää tämä.
- **`length_total`** — pieni rata (800–900 m) = tiukemmat kaarteet = pace räjähtää loppukaarteeseen = positioning tärkeämpää. Iso rata (1000+ m) = pitkät suorat = nopeus + loppukiri.
- **`angled_wing`** — vaikuttaa autostartin "haittiehevosen" todelliseen lähtöradan etuun.
- **`dosage`** — kaarteen kallistus. Yhdistettynä `length_total`:in kanssa kertoo paljon kaarrenopeudesta. Mitta-yksikkö epäselvä (1700? promille? milliradianeja?) — tämä on selvitettävä, mutta arvon säilyttäminen sellaisenaan riittää LightGBM:lle.

### 2.2 Bonus-piirteet (per rata, tekstipohjainen, ei pakollinen)

`round.tracks[i]`:ssa on myös:

- `track_description` — yleinen kuvaus (NLP / harvinainen käyttö)
- `track_races` — kuuluisat lähdöt
- `track_profiles` — kuuluisat valmentajat
- **`track_analysis`** — ⭐ **ravialan asiantuntija-arvio radasta**
- `built` — rakennusvuosi
- `capacity` — yleisömäärä

`track_analysis` on **kullan­arvoinen ihmistarkastukseen**. Esim. Färjestadin
analyysi sanoo suoraan:
> "På Färjestad är det ett stort minus med innerspår bakom bilen — särskilt
> över 2140 och 3140 meter är det svårt att spetsa därifrån. Spår fem är
> däremot riktigt bra att ha — har man en startsnabb häst kommer man iväg
> mycket snabbt därifrån. Färjestad är en riktig spetsbana — det är fler
> spetsvinnare här än på genomsnittsbanan."

Tämä on **inhimillistä eksperttipalautetta** joka voi auttaa:
1. Tarkistamaan että mallin oppimat säännöt ovat järkeviä
2. Validoimaan että rakennepiirteet (esim. lyhyt loppusuora → spets-rata)
   vastaavat alan ihmiskäsitystä
3. Antaa tekstidataa myöhempään NLP-piirteenotto­vaiheeseen

**Ei pakollinen treenissä** — talleta tauluun mutta älä laita FEATURE_COLS:iin.

### 2.3 Vaihtoehtoinen lähde — etsi tämä muualtakin

Käyttäjä mainitsi: "tätä voi etsiä muualtakin". Hyvä ajatus —
**rata-rakenne ei muutu**, joten yksi luotettava manuaalinen lähde riittää.

Vaihtoehtoiset lähteet rata-rakenteille:

1. **Wikipedia** — esim.
   https://sv.wikipedia.org/wiki/F%C3%A4rjestadstravet sisältää usein
   length_total, home_stretch, built. **Vakaa lähde** mutta vaatii
   manuaalisen koonnin per rata.
2. **Ravinetti.fi / Travsport.se** — radat-osio
   (https://www.travsport.se/banor/) sisältää joissain tapauksissa
   technisen kuvauksen, joissain ei.
3. **ATG track-info** — `/calendar/{day}`-vastauksessa on `track`-objekti
   joka voi olla rikkaampi kuin meidän nykyinen käyttömme. **Tarkista**
   `_track_name`:ssa ([scheduler.py:116](src/data/scheduler.py:116)) —
   nyt poimitaan vain `name`, mutta jos `track.length_home_stretch` löytyy,
   se on käytössämme jo ilmaiseksi.
4. **Travrondenspel** — yllä mainittu, kattaa kaikki.
5. **Manuaalinen taulukointi** — 26 SE-rataa, suora googlaus ~1 h työ.

**Strategia:** käytä Travrondenspeliä ensisijaisena lähteenä (rakenteellinen
JSON), mutta validoi 3–5 ratafields manuaalisesti Wikipediasta varmistaaksesi
että `length_home_stretch`-tyyppiset luvut ovat oikein. Jos jollain
radalla Travrondenspelin data on puutteellinen → täytä manuaalisesti.

---

## 3. Tekninen toteutus — vaiheittain

### Vaihe 1 — Static `tracks`-taulu schemaan

Lisää uusi taulu `src/data/schema.py`:hyn:

```python
class Track(Base):
    """Raviradan staattiset rakennepiirteet.

    Yksi rivi per uniikki rata. Päivitetään harvoin — radan rakenne ei
    muutu. Lähde: Travrondenspel:n /api/v1/public/round/{id}/ -vastauksen
    round.tracks[i]-objekti. Vaihtoehtoinen lähde: Wikipedia, manuaalinen
    koonti.
    """
    __tablename__ = "tracks"
    track_name = Column(String, primary_key=True)        # "Färjestad" — vastaa races.track:n arvoa
    travronden_code = Column(String, index=True)         # "F" (yhdistää horse_starts.track:iin track_codes-mapin kautta)
    atg_track_id = Column(Integer, index=True)           # 15
    slug = Column(String)                                # "farjestad"
    country = Column(String, default="SE")
    # Rakenne (numeerinen — käytetään FEATURE_COLS:issa)
    length_total = Column(Integer)                       # metreinä
    length_home_stretch = Column(Integer)                # metreinä — kriittinen
    width_1 = Column(Integer)
    width_2 = Column(Integer)
    dosage = Column(Integer)                             # kallistus, yksikkö epäselvä — säilytä raakana
    # Rakenne (boolean → tallenna int 0/1 sqlite:ssä)
    open_stretch = Column(Boolean)
    angled_wing = Column(Boolean)
    # Tekstit (käyttöön myöhemmin, ei FEATURE_COLS:iin)
    description = Column(String)
    track_analysis = Column(String)                      # ravialan asiantuntija-arvio
    # Meta
    built = Column(String)                               # vuosi merkkijonona, koska "1936" ja "1936 (renoverad 2001)" -muotoja
    capacity = Column(Integer)
    homepage = Column(String)
    source = Column(String)                              # "travronden" | "wikipedia" | "manual"
    updated = Column(DateTime, default=datetime.utcnow)
```

Lisää _COLUMN_MIGRATIONS-dictiin EI MITÄÄN tracks-tauluun — Base.metadata.create_all luo uuden taulun automaattisesti.

### Vaihe 2 — Yksinkertainen kerääjä Travrondenspelistä

`src/data/scrapers/travronden_tracks.py` (uusi tiedosto):

```python
"""Hae SE-ratojen rakennetiedot Travrondenspel:n /round-endpointin kautta.

Tarvitaan yksi round_id per rata. Käytä DB:stä tunnettuja round_id:tä
(ks. dokumentaatio) tai ATG-kalenterin perusteella generoituja.

Tämä ajetaan KERRAN — ei toistuvaa keräystä. ~26 SE-rataa = ~26 API-kutsua.
1 req/s = 30 sekunnin ajo.

Rate limit ja rehellinen UA — sama kuin TravsportAPIClient:llä.
"""

import logging, time
from datetime import datetime
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.data.schema import Track

logger = logging.getLogger(__name__)
_BASE = "https://www.travrondenspel.se/api/v1/public"
_UA = "ravit-edge research (jarkkom.lahde@gmail.com)"

# Lista tunnetuista round_id:istä joista kerätään rata-tiedot.
# Yksi round riittää per rata. Päivitä jos rata-nimi puuttuu.
# Helppo tapa kerätä: katso DevToolsista tai aja round-rangea 171000-172000.
KNOWN_ROUND_IDS_PER_TRACK = {
    "Färjestad": 171922,
    # Lisää loput ratojen testikierrokset.
    # ALTERNATIIVI: aja inkrementaalinen haku 100 viimeisen kierroksen yli
    # ja kerää uniikit tracks-objektit.
}


def fetch_track_structures(round_ids: list[int]) -> list[dict]:
    """Hae rata-tiedot annetuista round_id:istä. Palauta uniikit tracks-rivit."""
    tracks_seen: dict[str, dict] = {}
    with httpx.Client(headers={"User-Agent": _UA}, timeout=30.0) as c:
        for rid in round_ids:
            r = c.get(f"{_BASE}/round/{rid}/statistics/")
            if r.status_code != 200:
                logger.warning("round %s: %s", rid, r.status_code)
                time.sleep(1.0)
                continue
            for t in (r.json().get("round") or {}).get("tracks", []) or []:
                key = t.get("name")
                if key and key not in tracks_seen:
                    tracks_seen[key] = t
            time.sleep(1.0)  # rate limit
    return list(tracks_seen.values())


def upsert_tracks(db_path: str, tracks_data: list[dict]) -> dict:
    """Tallenna rata-tiedot tracks-tauluun. Idempotentti (päivittää jos olemassa)."""
    Session_ = sessionmaker(bind=create_engine(f"sqlite:///{db_path}"))
    updated = 0
    with Session_() as session:
        for t in tracks_data:
            name = t.get("name")
            if not name:
                continue
            obj = session.get(Track, name) or Track(track_name=name)
            obj.travronden_code = t.get("id")
            obj.atg_track_id = t.get("atg_id")
            obj.slug = t.get("slug")
            obj.country = t.get("country") or "SE"
            obj.length_total = t.get("length_total")
            obj.length_home_stretch = t.get("length_home_stretch")
            obj.width_1 = t.get("width_1")
            obj.width_2 = t.get("width_2")
            obj.dosage = t.get("dosage")
            obj.open_stretch = bool(t.get("open_stretch")) if t.get("open_stretch") is not None else None
            obj.angled_wing = bool(t.get("angled_wing")) if t.get("angled_wing") is not None else None
            obj.description = t.get("track_description")
            obj.track_analysis = t.get("track_analysis")
            obj.built = str(t.get("built")) if t.get("built") else None
            obj.capacity = _parse_capacity(t.get("capacity"))
            obj.homepage = t.get("homepage")
            obj.source = "travronden"
            obj.updated = datetime.utcnow()
            session.add(obj)
            updated += 1
        session.commit()
    return {"updated": updated}


def _parse_capacity(v) -> int | None:
    """capacity voi olla string '10000', '10 000' tai int."""
    if v is None: return None
    if isinstance(v, int): return v
    try:
        return int(str(v).replace(" ", ""))
    except ValueError:
        return None
```

### Vaihe 3 — CLI-komento

Lisää `src/data/scheduler.py`:n _main()-funktioon:

```python
sub.add_parser(
    "fetch-track-structures",
    help="Hae rata-rakennetiedot Travrondenspelistä tracks-tauluun. "
         "Ajetaan kerran tai harvoin (rata-rakenne ei muutu).",
)
```

Ja toteutuskomento:

```python
elif args.cmd == "fetch-track-structures":
    from src.data.scrapers.travronden_tracks import (
        KNOWN_ROUND_IDS_PER_TRACK, fetch_track_structures, upsert_tracks
    )
    rounds = list(KNOWN_ROUND_IDS_PER_TRACK.values())
    data = fetch_track_structures(rounds)
    print(f"Found {len(data)} unique tracks")
    print(upsert_tracks(DB_PATH, data))
```

### Vaihe 4 — Manuaalinen validointi 3–5 radalla

**Ennen kuin piirteet otetaan käyttöön malli­treenissä**, validoi käsin
Wikipediasta tai Travsport.se-sivuilta:

| Rata | length_total Wikipediasta | length_home_stretch Wikipediasta | Travronden vastaako? |
|---|---|---|---|
| Solvalla | 1000 m | ~220 m | (tarkista) |
| Färjestad | 1000 m | 177 m | ✅ vastaa |
| Bergsåker | 1000 m | (tarkista) | (tarkista) |
| Åby | (tarkista) | (tarkista) | (tarkista) |
| Jägersro | (tarkista) | (tarkista) | (tarkista) |

Jos jokin numero näyttää oudolta (esim. home_stretch 50 m tai 500 m), epäile.

### Vaihe 5 — Lisää piirteet feature-pipeliniin

`src/features/build_features.py`:

```python
def track_structure_features(runners: pd.DataFrame, tracks: pd.DataFrame) -> pd.DataFrame:
    """Liitä rata-rakennepiirteet runners-DataFrameen.

    tracks-DataFrame ladataan tracks-taulusta. Avain: track_name = races.track.

    Lisättävät piirteet (raakapiirteet — LightGBM oppii interaktiot):
      track_length_total       : radan koko metreinä
      track_home_stretch_m     : loppusuoran pituus metreinä
      track_open_stretch       : 1 jos rata sisältää open stretch -linjan
      track_angled_wing        : 1 jos kaltevat keulakaaret autostartille
      track_width_1, track_width_2, track_dosage

    Johdettuja kategoriapiirteitä EI tehdä manuaalisesti — LightGBM
    keksii rajat automaattisesti.
    """
    cols = [
        "track_name", "length_total", "length_home_stretch",
        "open_stretch", "angled_wing", "width_1", "width_2", "dosage",
    ]
    t = tracks[cols].rename(columns={
        "track_name": "track",
        "length_total": "track_length_total",
        "length_home_stretch": "track_home_stretch_m",
        "open_stretch": "track_open_stretch",
        "angled_wing": "track_angled_wing",
        "width_1": "track_width_1",
        "width_2": "track_width_2",
        "dosage": "track_dosage",
    })
    # bool → int
    for c in ["track_open_stretch", "track_angled_wing"]:
        t[c] = t[c].fillna(False).astype(int)
    return runners.merge(t, on="track", how="left")
```

Kutsu `build_feature_matrix`:ssa **race_setup_features:n jälkeen** (track-sarake
on tällöin olemassa):

```python
def build_feature_matrix(runners, races, horse_starts=None, horses=None, tracks=None):
    df = form_features(runners_with_meta, horse_starts=horse_starts)
    df = driver_trainer_features(df)
    df = race_setup_features(df, races, horse_starts=horse_starts)
    if tracks is not None:
        df = track_structure_features(df, tracks)
    if horses is not None and horse_starts is not None:
        df = sire_features(df, horses, horse_starts)
    df = derived_features(df)
    return df
```

### Vaihe 6 — Lisää FEATURE_COLS:iin

`src/models/ranker.py`:

```python
FEATURE_COLS: list[str] = [
    ...
    # --- Rata-rakenne (track_structure_features) ---
    "track_length_total",
    "track_home_stretch_m",
    "track_open_stretch",
    "track_angled_wing",
    "track_width_1",
    "track_width_2",
    "track_dosage",
    ...
]
```

Käytä `_resolve_cols`:n mekanismia — jos `tracks`-DataFrameä ei anneta,
sarakkeet puuttuvat ja malli ohittaa ne (kuten horse_age nyt).

---

## 4. Mitä malli oppii automaattisesti

LightGBM tekee puurakenteen kautta interaktiot ilman manuaalisia
yhdistelmiä. Esimerkkejä mallin oppimista säännöistä **kunhan
raakapiirteet ovat saatavilla**:

```
IF start_method == "auto" AND start_number <= 3
   AND track_home_stretch_m < 180:
   → eturadan etu suuri (lyhyt loppusuora, sisäradan etu)

IF start_method == "voltstart" AND back_row == 1
   AND track_open_stretch == 1:
   → takamatka vähemmän haitta (open stretch antaa toisen linjan)

IF distance >= 2640 AND track_length_total <= 900
   AND inside_post == 1:
   → "På Färjestad är det ett stort minus med innerspår bakom bilen
       över 2140 och 3140 meter" — täsmälleen tämä oppii itse.
```

Käyttäjän huomautus oli kohdallaan: **ilman raaka rata-piirteitä malli
joutuu opettelemaan "rata X = paikka Y voittaa" -säännöt yksitellen,
sen sijaan että oppisi yleisempiä fysiikan sääntöjä**. Raakapiirteiden
lisääminen on suorin tapa parantaa yleistettävyyttä.

---

## 5. ToS-päivitys — sallittu käyttö

Aiempi ohje oli liian varovainen. Tarkennus:

- URL-polku sisältää kirjaimellisesti `/public/` — endpoint on
  **suunniteltu julkiseksi**.
- Käyttö on **henkilökohtaista tutkimusta**, ei kaupallista myyntiä.
- Käytä tunnistautuva User-Agent — kerro projekti + sähköposti.
- Rate limit 1 req/s on enemmän kuin riittävä — rata-haku tapahtuu
  KERRAN (26 kutsua koko projektin elinaikana).
- **Ei toistuvaa keräystä** — kun tracks-taulu on täytetty, ei API-kutsuja.

Tämä on **selvästi pienempi rasitus** kuin nykyinen ATG/Travsport-keräys.

---

## 6. Aikataulu

```
Tehtävä A (1–2 h):  Lisää Track-luokka schemaan, luo migraatio
Tehtävä B (2–3 h):  fetch_track_structures + upsert_tracks + CLI
                    → aja ja kerää 26 rataa
Tehtävä C (1 h):    Validoi 5 rataa Wikipediasta
                    → korjaa erot manuaalisesti (source="manual")
Tehtävä D (1 h):    track_structure_features + FEATURE_COLS-päivitys
Tehtävä E (30 min): Smoke-testi: build_feature_matrix tracks-parametrilla
                    → varmista track_length_total notna% >= 95
                    (NaN vain jos rata puuttuu tracks-taulusta)

Yhteensä: 1 työpäivä.
```

Tämä tehdään **ennen** Vaiheen 3 (mallin treenaus) ensimmäistä ajoa.
Rata-piirteet ovat halvempi parannus kuin per-runner-analytiikka
(TASK_TRAVRONDEN_INVESTIGATION.md) ja vaikutus on todennäköisesti
suurempi.

---

## 7. Raportointi

Lisää `TASK_PROGRESS.md`:hen uusi osio:

```markdown
# VAIHE 2.5 — Rata-piirteet (tehdään ennen Vaihetta 3)

## Tehtävä A · Track-luokka schemaan
**Status:** _(täytä)_

## Tehtävä B · Travronden track-fetcher
**Status:** _(täytä)_
**Kerätyt radat:** _ kpl
**Esimerkki tulos:** _(yksi rivi DB:stä)_

## Tehtävä C · Wikipedia-validointi
**Status:** _(täytä)_
**Validoidut radat:** _ kpl
**Erot Travronden vs Wikipedia:** _(lista)_

## Tehtävä D · track_structure_features
**Status:** _(täytä)_

## Tehtävä E · Smoke-testi
**Status:** _(täytä)_
**track_length_total notna%:** _ %
**track_home_stretch_m notna%:** _ %
```

---

## 8. Pää-suositus

**Tee tämä ennen Vaihetta 3** (mallin treenaus).

Rata-rakennepiirteet ovat **kriittisin yksittäinen lisäys** mallin
ymmärtämiseen. Ne ovat:
- Helpoimmat hakea (yksi kerää-ajo, 30 sekuntia, 26 API-kutsua)
- Vakaimmat (rakenne ei muutu vuosiin)
- Selkeimmin perustellut (käyttäjän pointti raveista fysiikkana)
- Selkeimmät ToS-tilanteeltaan (public-endpoint, harva käyttö)

Tämän jälkeen Vaihe 3 (treenaus) voi alkaa rikkaammalla featuristolla.
TASK_TRAVRONDEN_INVESTIGATION.md (per-runner-analytiikka) on toissijainen
parannus jonka voi tehdä Vaiheen 3 ensimmäisten tulosten jälkeen, kun
on selvempi mitä mallista vielä puuttuu.
