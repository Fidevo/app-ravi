"""
SQLite-skeema ravidatalle.

Suunniteltu jatkuvaan kasvuun: aluksi MVP, mutta rakenne tukee
historiallisen datan keräämistä ja siirtoa Postgresiin myöhemmin.

Päätaulut:
  - races          : lähtöjen master-data (rata, etäisyys, lähtötapa, päivä)
  - runners        : hevonen × lähtö (sijoitus, kerroin, ohjastaja, valmentaja)
  - horses         : hevosen perustiedot (sukupuoli, syntymävuosi, sukutaulu)
  - results        : ajetut lähdöt + voittokerroin
  - odds_snapshots : kerroinhistoria (markkinaliikkeet)
"""

from __future__ import annotations

import logging

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, relationship

from src.paths import DB_PATH as _DB_PATH_ABS

logger = logging.getLogger(__name__)

# Absoluuttinen oletuspolku stringinä (create_engine vaatii str:n).
_DEFAULT_DB_PATH = str(_DB_PATH_ABS)


class Base(DeclarativeBase):
    pass


class Race(Base):
    __tablename__ = "races"

    race_id = Column(String, primary_key=True)
    race_date = Column(Date, nullable=False, index=True)
    track = Column(String, nullable=False, index=True)
    race_number = Column(Integer)
    distance = Column(Integer)         # metreissä
    start_method = Column(String)      # "auto" | "voltstart"
    purse_sek = Column(Integer)        # palkintosumma
    track_condition = Column(String)   # rata: kuiva/pehmeä jne.

    runners = relationship("Runner", back_populates="race")


class Horse(Base):
    __tablename__ = "horses"

    horse_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    sex = Column(String)
    birth_year = Column(Integer)
    sire = Column(String)        # isä
    dam = Column(String)         # emä
    dam_sire = Column(String)    # emänisä


class Runner(Base):
    """Yksi hevonen yhdessä lähdössä."""

    __tablename__ = "runners"

    runner_id = Column(String, primary_key=True)  # race_id + start_number
    race_id = Column(String, ForeignKey("races.race_id"), index=True)
    horse_id = Column(String, ForeignKey("horses.horse_id"), index=True)
    start_number = Column(Integer)        # lähtörata
    handicap_meters = Column(Integer)     # takamatka volttilähdössä
    driver = Column(String, index=True)
    trainer = Column(String, index=True)

    # Tulokset (täytetään lähdön jälkeen)
    finish_position = Column(Integer)     # 1 = voitto, NULL = ei tullut maaliin
    kilometer_time_seconds = Column(Float)  # esim. 73.4 = 1.13.4
    win_odds_final = Column(Float)        # lopullinen voittokerroin

    # ATG-aggregaattipiirteet hevosesta (täytetään runnerin tallennushetkellä).
    # HUOM: lifetime_win_rate sisältää survivorship biasin - huonot hevoset
    # lopettavat aikaisin, joten piirre on osittain kokemuksen, osittain
    # lahjakkuuden proxy. Älä tulkitse pelkästään "kuinka hyvä hevonen on".
    atg_lifetime_starts = Column(Integer)
    atg_lifetime_win_rate = Column(Float)
    atg_lifetime_top3_rate = Column(Float)
    atg_current_year_win_rate = Column(Float)
    # Paras km-aika tämän lähdön (startMethod, distance-bucket) -kontekstissa.
    # Lasketaan dynaamisesti life.records-listasta scheduler-jobissa.
    atg_best_km_for_this_setup = Column(Float)

    # ATG driver-aggregaatit (kuluva vuosi, start.driver.statistics).
    # winPercentage tallennetaan float 0.0-1.0 (ATG antaa int×10000).
    atg_driver_id = Column(String)
    atg_driver_starts = Column(Integer)
    atg_driver_win_pct = Column(Float)
    atg_driver_earnings = Column(Integer)

    # ATG trainer-aggregaatit (kuluva vuosi, start.horse.trainer.statistics).
    atg_trainer_id = Column(String)
    atg_trainer_starts = Column(Integer)
    atg_trainer_win_pct = Column(Float)
    atg_trainer_earnings = Column(Integer)

    # Kengät ja sulky (start.horse.shoes / start.horse.sulky).
    # NULL kun ATG ei ole raportoinut (esim. ennen lähtökortin valmistumista).
    # changed_*: tosi = vaihdettu vs hevosen edellinen startti (signaali
    # valmentajan tarkoituksellisesta muutoksesta).
    shoes_front = Column(Boolean)
    shoes_back = Column(Boolean)
    shoes_changed_front = Column(Boolean)
    shoes_changed_back = Column(Boolean)
    sulky_type = Column(String)        # esim "VA" (Vanlig), "AM" (Amerikansk)
    sulky_changed = Column(Boolean)    # type tai colour muuttunut

    race = relationship("Race", back_populates="runners")


class HorseStart(Base):
    """Hevosen yksittäinen startti Travsport-historiasta.

    Uniikkia dataa jota ATG ei anna: täydellinen starttihistoria per hevonen
    (km-ajat, lähtöradat, palkinnot, ohjastajat). Kerätään schedulerin
    fetch_daily_races-jobin yhteydessä TravsportAPIClient:llä.

    Deduplikointi: (horse_id, travsport_race_id) on UNIQUE.
    """

    __tablename__ = "horse_starts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    horse_id = Column(String, ForeignKey("horses.horse_id"), nullable=False, index=True)
    race_date = Column(String)              # ISO YYYY-MM-DD
    track = Column(String)
    distance = Column(Integer)
    start_method = Column(String)           # 'V' | 'A'
    start_number = Column(Integer)
    finish_position = Column(Integer)       # NULL = varikko/laukka
    kilometer_time_seconds = Column(Float)
    driver = Column(String)
    trainer = Column(String)
    prize_won = Column(Integer, default=0)
    win_odds_final = Column(Float)
    withdrawn = Column(Boolean, default=False)
    travsport_race_id = Column(Integer)     # Travsport-natiivi race_id
    race_number = Column(Integer)
    track_condition = Column(String)        # radan kunto (Travsport: "LE", "ME", "TU" tms.)


# Partial unique -indeksi horse_starts-deduplikointiin.
# Erillinen koska SQLAlchemy:n __table_args__ + UNIQUE ei toimi
# partial-indeksien kanssa SQLite:ssä.
_HORSE_STARTS_UNIQUE_INDEX = (
    "ux_horse_starts_dedup",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_horse_starts_dedup "
    "ON horse_starts (horse_id, travsport_race_id) "
    "WHERE travsport_race_id IS NOT NULL",
)


class OddsSnapshot(Base):
    """Aikaleimattu kerroin - mahdollistaa markkinaliikkeiden seurannan.

    Step 3 lisäkentät:
      raw_win_odds      : lähteen raaka-arvo (esim. ATG finalOdds)
      devigged_win_odds : devig-laskettu (margin poistettu) - approksimaatio
      vig_pct           : lähdön kokonaismargin (%) myöhempää analyysia varten
      snapshot_label    : "T-15min" | "T-10min" | "T-5min" | "T-2min" | "result"
      source            : "atg_pari_mutuel" (MVP) | "pinnacle" | "betfair_exchange"
                          | "unibet"  -- Step 4:ssa lisätään external bookmakers

    Idempotenssi: UNIQUE (runner_id, snapshot_label) - sama snapshotti
    samalle runnerille tallennetaan vain kerran. Vanhat NULL-label rivit
    (Step 2 fetch_results) säilyvät, indeksi on partial.
    """

    __tablename__ = "odds_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    runner_id = Column(String, ForeignKey("runners.runner_id"), index=True)
    captured_at = Column(DateTime, nullable=False, index=True)
    win_odds = Column(Float)
    place_odds = Column(Float)
    # Step 3 lisäkentät - lisätään ALTER TABLE -migraationa olemassa olevaan
    # tauluun, joten ei nullable=False (taustalla on rivejä ilman näitä).
    raw_win_odds = Column(Float)
    devigged_win_odds = Column(Float)
    vig_pct = Column(Float)
    snapshot_label = Column(String, index=True)
    source = Column(String, index=True)


# Sarakkeet jotka pitää lisätä olemassa oleviin tauluihin (puuttuvina).
# SQLAlchemy create_all luo PUUTTUVAT taulut mutta ei lisää uusia
# sarakkeita olemassa oleviin - siksi tämä erillinen migraatio.
_COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "horse_starts": [
        ("track_condition", "TEXT"),
    ],
    "runners": [
        ("atg_lifetime_starts", "INTEGER"),
        ("atg_lifetime_win_rate", "REAL"),
        ("atg_lifetime_top3_rate", "REAL"),
        ("atg_current_year_win_rate", "REAL"),
        ("atg_best_km_for_this_setup", "REAL"),
        # Driver/trainer-aggregaatit (A-vaihtoehto, ATG:n statistics-kentästä)
        ("atg_driver_id", "TEXT"),
        ("atg_driver_starts", "INTEGER"),
        ("atg_driver_win_pct", "REAL"),
        ("atg_driver_earnings", "INTEGER"),
        ("atg_trainer_id", "TEXT"),
        ("atg_trainer_starts", "INTEGER"),
        ("atg_trainer_win_pct", "REAL"),
        ("atg_trainer_earnings", "INTEGER"),
        # TODO #2: shoes/sulky (5.5.2026)
        ("shoes_front", "BOOLEAN"),
        ("shoes_back", "BOOLEAN"),
        ("shoes_changed_front", "BOOLEAN"),
        ("shoes_changed_back", "BOOLEAN"),
        ("sulky_type", "TEXT"),
        ("sulky_changed", "BOOLEAN"),
    ],
    "odds_snapshots": [
        ("raw_win_odds", "REAL"),
        ("devigged_win_odds", "REAL"),
        ("vig_pct", "REAL"),
        ("snapshot_label", "TEXT"),
        ("source", "TEXT"),
    ],
}

# Partial unique -indeksit. Sallivat vanhat NULL-label rivit (Step 2),
# mutta Step 3:n labeled snapshotit unikoituvat (race × runner × label).
_INDEX_MIGRATIONS: list[tuple[str, str]] = [
    (
        "ux_odds_snapshot_label",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_odds_snapshot_label "
        "ON odds_snapshots (runner_id, snapshot_label) "
        "WHERE snapshot_label IS NOT NULL",
    ),
    _HORSE_STARTS_UNIQUE_INDEX,
]


def migrate(db_path: str = _DEFAULT_DB_PATH) -> list[str]:
    """Lisää puuttuvat sarakkeet olemassa olevaan tietokantaan.

    Idempotentti: ajo tyhjästä DB:stä tai täydestä DB:stä on turvallinen.
    Palauttaa listan tehdyistä muutoksista lokitusta varten.
    """
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    applied: list[str] = []

    # WAL-mode: paremmat rinnakkaiset SELECT:it (Streamlit-UI vs scheduler-
    # kirjoittaja). Asetus on PERSISTENT, tallentuu DB-tiedostoon - tarkistus
    # joka migrate-kutsussa on halpaa eikä tee mitään jos jo WAL:ssa.
    # HUOM: PRAGMA journal_mode pitää ajaa transaktion ULKOPUOLELLA,
    # ei begin()-blokissa - SQLite hylkää muutoksen muuten.
    with engine.connect() as conn:
        current = conn.execute(text("PRAGMA journal_mode")).scalar()
        if (current or "").lower() != "wal":
            new_mode = conn.execute(text("PRAGMA journal_mode=WAL")).scalar()
            logger.info(
                "SQLite journal_mode changed: %s -> %s", current, new_mode
            )
            applied.append(f"journal_mode {current} -> {new_mode}")

    with engine.begin() as conn:
        for table, cols in _COLUMN_MIGRATIONS.items():
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, sqltype in cols:
                if name in existing:
                    continue
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}")
                )
                applied.append(f"{table}.{name} ({sqltype})")
        existing_indexes = {
            ix["name"]
            for table in inspector.get_table_names()
            for ix in inspector.get_indexes(table)
        }
        for index_name, ddl in _INDEX_MIGRATIONS:
            if index_name in existing_indexes:
                continue
            conn.execute(text(ddl))
            applied.append(f"index {index_name}")
    return applied


def create_database(db_path: str = _DEFAULT_DB_PATH) -> None:
    """Luo tietokannan ja aja migraatiot."""
    applied = migrate(db_path)
    print(f"Tietokanta valmis: {db_path}")
    if applied:
        print("Lisätyt sarakkeet:")
        for change in applied:
            print(f"  + {change}")


if __name__ == "__main__":
    create_database()
