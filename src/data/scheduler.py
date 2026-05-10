"""
Päivittäinen scheduler ATG-datan keräämiseen.

Kaksi job-funktiota (Step 2):
  fetch_daily_races(target_date) - aamuyö 03:00, hakee päivän lähdöt
                                   + ATG-aggregaattipiirteet hevosista
  fetch_results(race_id)         - lähdön jälkeen +30min, päivittää
                                   tulokset ja tallentaa odds_snapshot

Closing odds -jobi (b) tulee Step 3:ssa erikseen.

ATG:n /races/{id} täyttää data vaiheittain:
  T+0…30min: vain odds + top-3 sijoitukset, EI km-aikoja
  T+1…2h:    kaikki sijoitukset 1-N
  T+useita tunteja - useita päiviä: kmTime-objektit täyttyvät

Tästä syystä +30min fetch_results täydennetään päivittäisellä
retry_incomplete_results -jobilla klo 04:30 Stockholm-aikaa, joka käy
läpi viim. 7 päivän racet joilla on NULL kilometer_time_seconds tai
vajaita finish_positions ja yrittää hakea ATG:lta uudelleen.

Käyttö:
  python -m src.data.scheduler run-once [--date YYYY-MM-DD]
  python -m src.data.scheduler run-forever
  python -m src.data.scheduler fetch-results --race-id RACE_ID
  python -m src.data.scheduler retry-incomplete [--lookback 7]
  python -m src.data.scheduler refresh-day-runners [--date YYYY-MM-DD]

Production-deployment:
  systemd: ks. README "Scheduler-deploy" -osio
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# ATG:n /races/{id}.startTime ja /calendar/{day}-rivit ovat naive-merkkijonoja
# Ruotsin paikallisaikaa. Sisäisesti käytämme UTC-tietoisia datetime-arvoja
# jotta DateTrigger-jobit eivät siirry tunnilla DST-vaihdoksissa eivätkä
# vertailut now()-arvoon mene pieleen kun kone ajaa eri tz:ssa.
ATG_TZ = ZoneInfo("Europe/Stockholm")

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.betting.clv_tracker import calculate_vig, devig_odds
from src.data.atg_client import ATGClient
from src.data.schema import Horse, HorseStart, OddsSnapshot, Race, Runner, migrate
from src.paths import DB_PATH as _DB_PATH_ABS
from src.paths import LOG_DIR as _LOG_DIR_ABS
from src.paths import RAW_DIR as _RAW_DIR_ABS

# Snapshot-offsetit lähtöajasta. T-2min on viimeinen "luotettava" piste
# ennen kassa-aukon kiristymistä (ATG sulkee n. T-30s).
SNAPSHOT_OFFSETS: list[tuple[str, timedelta]] = [
    ("T-15min", timedelta(minutes=15)),
    ("T-10min", timedelta(minutes=10)),
    ("T-5min", timedelta(minutes=5)),
    ("T-2min", timedelta(minutes=2)),
]

# Absoluuttiset polut (src.paths) - eivät riipu CWD:stä. Stringi
# DB_PATH:lle koska SQLAlchemyn create_engine ottaa stringin.
LOG_DIR = _LOG_DIR_ABS
LOG_FILE = LOG_DIR / "scheduler.log"
DB_PATH = str(_DB_PATH_ABS)

# TODO #3: Tunnetut gallop-radat ATG:n SE-kalenterissa. Näiden lähdöillä
# ei ole kmTime-objekteja, joten retry_incomplete_results ohittaa ne
# eikä tee turhia API-kutsuja. atg_client.get_calendar_day suodattaa
# nämä pois jo calendar-tasolla (sport != "trot"), joten uusia gallop-
# lähtöjä ei tule DB:hen - tämä lista suojaa jo olemassa olevia rivejä.
GALLOP_TRACKS: frozenset[str] = frozenset({
    "Bro Park",        # Stockholm-alue
    "Göteborg Galopp", # Göteborg/Åby-alue
    "Jägersro Galopp", # Malmö-alue
})

logger = logging.getLogger("ravit_edge.scheduler")


def setup_logging() -> None:
    """Idempotentti logging-asennus: tiedosto + stderr."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.handlers.TimedRotatingFileHandler(
        LOG_FILE, when="midnight", backupCount=14, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)


# ----------------------------------------------------------------------
# ATG-rakenneapurit
# ----------------------------------------------------------------------


def _track_name(race: dict) -> str:
    t = race.get("track")
    if isinstance(t, dict):
        return t.get("name") or ""
    return str(t or "")


def _person_name(person: dict | None) -> str | None:
    if not person:
        return None
    fn = person.get("firstName") or ""
    ln = person.get("lastName") or ""
    full = f"{fn} {ln}".strip()
    return full or person.get("shortName")


def _km_seconds(time_obj: Any) -> float | None:
    """ATG time-objekti {minutes, seconds, tenths} → sekunteina."""
    if not isinstance(time_obj, dict):
        return None
    try:
        return (
            int(time_obj["minutes"]) * 60
            + int(time_obj["seconds"])
            + int(time_obj["tenths"]) / 10
        )
    except (KeyError, TypeError, ValueError):
        return None


def _distance_bucket(meters: int | None) -> str:
    """Mappaa metrit ATG:n life.records-bucketiin."""
    if meters is None:
        return ""
    if meters <= 1700:
        return "short"
    if meters <= 2300:
        return "medium"
    return "long"


def _best_km_for_setup(
    records: list[dict], start_method: str, bucket: str
) -> float | None:
    """Paras km-aika life.records-listasta tämän startMethodin/bucketin mukaan.

    Fallback: jos täsmäävää bucketia ei ole, käytä startMethodin kaikkia.
    """
    if not records or not start_method:
        return None
    matches = [
        r
        for r in records
        if r.get("startMethod") == start_method and r.get("distance") == bucket
    ]
    if not matches:
        matches = [r for r in records if r.get("startMethod") == start_method]
    if not matches:
        return None
    times = [_km_seconds(m.get("time")) for m in matches]
    times = [t for t in times if t is not None]
    return min(times) if times else None


def _atg_aggregates(horse: dict, race: dict) -> dict:
    """Laske 5 ATG-aggregaattipiirrettä."""
    stats = horse.get("statistics") or {}
    life = stats.get("life") or {}
    starts = int(life.get("starts") or 0)
    placement = life.get("placement") or {}
    wins = int(placement.get("1") or 0)
    twos = int(placement.get("2") or 0)
    threes = int(placement.get("3") or 0)
    win_rate = wins / starts if starts else None
    top3_rate = (wins + twos + threes) / starts if starts else None

    year = str(race.get("date") or "")[:4]
    year_stats = (stats.get("years") or {}).get(year, {})
    year_starts = int(year_stats.get("starts") or 0)
    year_wins = int((year_stats.get("placement") or {}).get("1") or 0)
    year_win_rate = year_wins / year_starts if year_starts else None

    bucket = _distance_bucket(race.get("distance"))
    best_km = _best_km_for_setup(
        life.get("records") or [], race.get("startMethod") or "", bucket
    )

    return {
        "atg_lifetime_starts": starts or None,
        "atg_lifetime_win_rate": win_rate,
        "atg_lifetime_top3_rate": top3_rate,
        "atg_current_year_win_rate": year_win_rate,
        "atg_best_km_for_this_setup": best_km,
    }


def _person_aggregates(person: dict | None, race: dict, prefix: str) -> dict:
    """Poimi driver/trainer-aggregaatit ATG:n statistics-kentästä.

    ATG:n start.driver ja start.horse.trainer sisältävät:
      statistics.years.{year}.{starts, placement, winPercentage, earnings}

    winPercentage on int×10000 (esim. 1549 = 15.49% = 0.1549).
    Tallennetaan kuluvan vuoden (race.date[:4]) statistiikka.

    Args:
        person: ATG:n driver- tai trainer-dict (sis. id, statistics)
        race: ATG:n race-dict (tarvitaan vuosiluvulle)
        prefix: "atg_driver" tai "atg_trainer"

    Returns:
        dict: {prefix_id, prefix_starts, prefix_win_pct, prefix_earnings}
    """
    empty = {
        f"{prefix}_id": None,
        f"{prefix}_starts": None,
        f"{prefix}_win_pct": None,
        f"{prefix}_earnings": None,
    }
    if not person:
        return empty

    pid = person.get("id")
    stats = person.get("statistics") or {}
    year = str(race.get("date") or "")[:4]
    year_stats = (stats.get("years") or {}).get(year, {})
    starts = int(year_stats.get("starts") or 0)
    if not starts:
        return {**empty, f"{prefix}_id": str(pid) if pid else None}

    # winPercentage int×10000 → float 0.0-1.0
    raw_wp = year_stats.get("winPercentage")
    win_pct = float(raw_wp) / 10000.0 if raw_wp is not None else None
    earnings = int(year_stats.get("earnings") or 0) or None

    return {
        f"{prefix}_id": str(pid) if pid else None,
        f"{prefix}_starts": starts,
        f"{prefix}_win_pct": win_pct,
        f"{prefix}_earnings": earnings,
    }


def _shoes_sulky_fields(horse: dict) -> dict:
    """Poimi kengät- ja sulky-tiedot ATG:n start.horse-objektista.

    ATG-rakenne (havaittu 5.5.2026):
        horse.shoes = {
            "reported": bool,
            "front": {"hasShoe": bool, "changed": bool},  # changed voi puuttua
            "back":  {"hasShoe": bool, "changed": bool},  # sama
        }
        horse.sulky = {
            "reported": bool,
            "type":   {"code": "VA"|"AM"|..., "changed": bool},
            "colour": {"code": "GU"|..., "changed": bool},
        }

    Jos `reported` on false (tai shoes/sulky kokonaan puuttuu), kaikki
    palautetaan Noneksi - emme keksi arvoja kun ATG itse sanoo että
    tieto ei ole vielä saatavilla. `changed`-kenttä voi myös puuttua
    yksittäisistä starteista jolloin se on None vaikka shoes.reported
    on true (näimme tämän empiirisesti useissa hevosissa).
    """
    shoes = horse.get("shoes") or {}
    sulky = horse.get("sulky") or {}
    shoes_reported = bool(shoes.get("reported"))
    sulky_reported = bool(sulky.get("reported"))
    front = shoes.get("front") or {}
    back = shoes.get("back") or {}
    sulky_type = sulky.get("type") or {}
    sulky_colour = sulky.get("colour") or {}

    # changed voi puuttua → vain True/False/None (ei kovakooda False)
    type_changed = sulky_type.get("changed")
    colour_changed = sulky_colour.get("changed")
    if type_changed is None and colour_changed is None:
        sulky_changed: bool | None = None
    else:
        sulky_changed = bool(type_changed) or bool(colour_changed)

    return {
        "shoes_front": front.get("hasShoe") if shoes_reported else None,
        "shoes_back": back.get("hasShoe") if shoes_reported else None,
        "shoes_changed_front": front.get("changed") if shoes_reported else None,
        "shoes_changed_back": back.get("changed") if shoes_reported else None,
        "sulky_type": sulky_type.get("code") if sulky_reported else None,
        "sulky_changed": sulky_changed if sulky_reported else None,
    }


def _odds_to_decimal(raw: Any) -> float | None:
    """ATG-kerroin desimaalimuotoon.

    Empiirisesti vahvistettu 2026-04-27: /races/{id}-endpointin
    start.result.finalOdds on jo desimaalimuodossa (esim. 2.41, 65.32).
    Identity-konversio. Ei sekoitettavissa horse.statistics.lastFiveStarts
    .averageOdds-kenttään, joka on int*100 (eri konteksti).
    """
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _pool_odds_to_decimal(pool: Any) -> float | None:
    """vinnare-pool-kerroin desimaalimuotoon.

    /games/vinnare_<race_id>:n start.pools.vinnare.odds on int×100
    (esim. 4539 = 45.39). Empiirisesti vahvistettu 2026-04-28.
    None jos pool / odds-kenttä puuttuu (esim. scratched horse).
    """
    if not isinstance(pool, dict):
        return None
    raw = pool.get("odds")
    if raw is None:
        return None
    try:
        return float(raw) / 100.0
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# Idempotentit upsertit (session.get + in-place update)
# ----------------------------------------------------------------------


def _upsert_race(session: Session, race: dict) -> None:
    rid = str(race["id"])
    obj = session.get(Race, rid)
    if obj is None:
        obj = Race(race_id=rid)
        session.add(obj)
    obj.race_date = (
        date.fromisoformat(race["date"]) if race.get("date") else None
    )
    obj.track = _track_name(race)
    obj.race_number = race.get("number")
    obj.distance = race.get("distance")
    obj.start_method = race.get("startMethod")
    obj.purse_sek = race.get("prize") if isinstance(race.get("prize"), int) else None


def _upsert_horse(session: Session, horse: dict) -> None:
    hid = horse.get("id")
    if not hid:
        return
    obj = session.get(Horse, str(hid))
    if obj is None:
        obj = Horse(horse_id=str(hid))
        session.add(obj)
    obj.name = horse.get("name") or ""
    obj.sex = horse.get("sex")
    age = horse.get("age")
    obj.birth_year = (date.today().year - age) if isinstance(age, int) else None
    pedigree = horse.get("pedigree") or {}
    obj.sire = (pedigree.get("father") or {}).get("name")
    obj.dam = (pedigree.get("mother") or {}).get("name")
    obj.dam_sire = (pedigree.get("mothersFather") or {}).get("name")


def _upsert_runner(
    session: Session, race: dict, start: dict
) -> tuple[bool, bool]:
    """Palauttaa (inserted, updated)."""
    horse = start.get("horse") or {}
    if not horse.get("id"):
        return (False, False)
    runner_id = f"{race['id']}_{start.get('number')}"
    obj = session.get(Runner, runner_id)
    inserted = obj is None
    if inserted:
        obj = Runner(runner_id=runner_id)
        session.add(obj)

    obj.race_id = str(race["id"])
    obj.horse_id = str(horse["id"])
    obj.start_number = start.get("number")
    handicap = (start.get("distance") or 0) - (race.get("distance") or 0)
    obj.handicap_meters = handicap if handicap > 0 else None
    obj.driver = _person_name(start.get("driver"))
    obj.trainer = _person_name(horse.get("trainer"))
    for k, v in _atg_aggregates(horse, race).items():
        setattr(obj, k, v)
    for k, v in _person_aggregates(start.get("driver"), race, "atg_driver").items():
        setattr(obj, k, v)
    for k, v in _person_aggregates(horse.get("trainer"), race, "atg_trainer").items():
        setattr(obj, k, v)
    for k, v in _shoes_sulky_fields(horse).items():
        setattr(obj, k, v)
    return (inserted, not inserted)


# ----------------------------------------------------------------------
# Job-funktiot
# ----------------------------------------------------------------------


def _engine(db_path: str = DB_PATH):
    return create_engine(f"sqlite:///{db_path}")


def _upsert_horse_starts(
    session: Session,
    horse_id: str,
    travsport: Any,
) -> dict:
    """Hae hevosen starttihistoria Travsportista ja tallenna horse_starts-tauluun.

    Käyttää TravsportAPIClient:n get_results-metodia, joka palauttaa
    normalisoidun listan startteja. Cache (7pv) hoitaa ettei samaa
    hevosta haeta turhaan.

    Idempotentti: (horse_id, travsport_race_id) on UNIQUE -indeksi.
    Rivit joilla travsport_race_id on None ohitetaan (ks. partial index).

    Returns:
        dict: {inserted: int, skipped: int}
    """
    stats = {"inserted": 0, "skipped": 0}
    try:
        starts = travsport.get_results(horse_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Travsport history fetch failed for horse %s: %s", horse_id, exc
        )
        return stats

    for s in starts:
        ts_race_id = s.get("race_id")
        if ts_race_id is None:
            stats["skipped"] += 1
            continue
        # Idempotentti: tarkista onko rivi jo olemassa
        existing = (
            session.query(HorseStart)
            .filter_by(horse_id=str(horse_id), travsport_race_id=int(ts_race_id))
            .one_or_none()
        )
        if existing is not None:
            stats["skipped"] += 1
            continue
        session.add(
            HorseStart(
                horse_id=str(horse_id),
                race_date=s.get("race_date"),
                track=s.get("track"),
                distance=s.get("distance"),
                start_method=s.get("start_method"),
                start_number=s.get("start_number"),
                finish_position=s.get("finish_position"),
                kilometer_time_seconds=s.get("kilometer_time_seconds"),
                driver=s.get("driver"),
                trainer=s.get("trainer"),
                prize_won=s.get("prize_won", 0),
                win_odds_final=s.get("win_odds_final"),
                withdrawn=s.get("withdrawn", False),
                travsport_race_id=int(ts_race_id),
                race_number=s.get("race_number"),
                track_condition=s.get("track_condition"),
            )
        )
        stats["inserted"] += 1
    return stats


def backfill_track_condition(
    db_path: str = DB_PATH,
    cache_dir: "Path | None" = None,
) -> dict:
    """Täytä track_condition kaikille olemassa oleville horse_starts-riveille
    paikallisista Travsport-välimuistitiedostoista.

    Ei tee yhtään API-kutsua — lukee pelkästään jo ladattuja JSON-tiedostoja
    (`data/raw/travsport/{horse_id}_results.json`). Turvallista ajaa kun
    scheduler pyörii samanaikaisesti: SQLite WAL-mode hoitaa rinnakkaiskirjoitukset.

    Idempotentti: päivittää vain rivit joilla track_condition IS NULL.
    Voidaan ajaa uudelleen jos keskeytyi.

    Returns:
        dict: {updated, skipped, errors, cache_files}
    """
    import json

    from src.data.scrapers.travsport import _normalize_start
    from pathlib import Path as _Path

    if cache_dir is None:
        cache_dir = _RAW_DIR_ABS / "travsport"
    cache_dir = _Path(cache_dir)
    engine = create_engine(f"sqlite:///{db_path}")
    Session_ = sessionmaker(bind=engine)

    updated = 0
    skipped = 0
    errors = 0

    result_files = sorted(cache_dir.glob("*_results.json"))
    logger.info(
        "backfill_track_condition: %d välimuistitiedostoa löydetty", len(result_files)
    )

    with Session_() as session:
        for cache_file in result_files:
            # Tiedostonimi: {horse_id}_results.json
            horse_id = cache_file.stem.rsplit("_", 1)[0]
            try:
                raw_starts = json.loads(cache_file.read_text(encoding="utf-8"))
                for r in raw_starts:
                    s = _normalize_start(r)
                    ts_race_id = s.get("race_id")
                    tc = s.get("track_condition")
                    if ts_race_id is None or tc is None:
                        # track_condition puuttuu tästä startista (normaalia)
                        skipped += 1
                        continue
                    n = session.execute(
                        text("""
                            UPDATE horse_starts
                               SET track_condition = :tc
                             WHERE horse_id = :hid
                               AND travsport_race_id = :rid
                               AND track_condition IS NULL
                        """),
                        {"tc": tc, "hid": str(horse_id), "rid": int(ts_race_id)},
                    ).rowcount
                    updated += n
                    if n == 0:
                        skipped += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "backfill_track_condition: virhe tiedostossa %s: %s",
                    cache_file.name,
                    exc,
                )
                errors += 1
        session.commit()

    logger.info(
        "backfill_track_condition valmis: päivitetty=%d, ohitettu=%d, virheitä=%d, tiedostoja=%d",
        updated,
        skipped,
        errors,
        len(result_files),
    )
    return {
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "cache_files": len(result_files),
    }


def fetch_daily_races(
    target_date: date | None = None,
    db_path: str = DB_PATH,
    atg: ATGClient | None = None,
    scheduler: BlockingScheduler | None = None,
    travsport: Any | None = None,
) -> dict:
    target = target_date or date.today()
    logger.info("fetch_daily_races: target=%s", target.isoformat())

    own_client = atg is None
    client = atg or ATGClient()
    Session_ = sessionmaker(bind=_engine(db_path))
    stats: dict[str, Any] = {
        "races_processed": 0,
        "runners_inserted": 0,
        "runners_updated": 0,
        "errors": [],
        "first_race_start_utc": None,  # päivän aikaisin SE-lähtö, refresh-jobin ankkuri
    }
    # Kerää uniikki horse_id:t Travsport-hakua varten
    horse_ids_seen: set[str] = set()
    earliest_start_dt: datetime | None = None
    try:
        cal = client.get_calendar_day(target)
        with Session_() as session:
            for track in cal.get("tracks", []) or []:
                for cal_race in track.get("races", []) or []:
                    rid = cal_race.get("id")
                    if not rid:
                        continue
                    try:
                        race = client.get_race(rid)
                        _upsert_race(session, race)
                        ins = upd = 0
                        starts = race.get("starts") or []
                        for s in starts:
                            horse = s.get("horse") or {}
                            _upsert_horse(session, horse)
                            i, u = _upsert_runner(session, race, s)
                            ins += int(i)
                            upd += int(u)
                            if horse.get("id"):
                                horse_ids_seen.add(str(horse["id"]))
                        session.commit()
                        stats["races_processed"] += 1
                        stats["runners_inserted"] += ins
                        stats["runners_updated"] += upd
                        # Snapshot-jobit ajastetaan vain jos scheduler
                        # annettu (run_forever-konteksti). run_once ei
                        # ajasta - sen scope päättyy funktion palatessa.
                        # Seuraa aikaisinta SE-lähtöä — käytetään refresh-
                        # jobin ankkurina (1. lähdön - 10min hetkellä shoes/
                        # sulky/jne. on jo lukittu, fetch_daily_races
                        # uudelleenajo täyttää lopulliset arvot).
                        race_start_dt = _parse_atg_datetime(race.get("startTime"))
                        if race_start_dt is not None:
                            if earliest_start_dt is None or race_start_dt < earliest_start_dt:
                                earliest_start_dt = race_start_dt

                        if scheduler is not None:
                            start_dt = race_start_dt
                            if start_dt is not None:
                                try:
                                    n_snap = schedule_odds_snapshots(
                                        scheduler, str(rid), start_dt, db_path
                                    )
                                    n_res = _schedule_results_job(
                                        scheduler, str(rid), start_dt, db_path
                                    )
                                    stats.setdefault("snapshot_jobs", 0)
                                    stats["snapshot_jobs"] += n_snap
                                    stats.setdefault("result_jobs", 0)
                                    stats["result_jobs"] += n_res
                                except Exception as exc:  # noqa: BLE001
                                    logger.exception(
                                        "Scheduling failed for race %s", rid
                                    )
                                    stats["errors"].append(
                                        f"schedule {rid}: {exc}"
                                    )
                        logger.info(
                            "%s L%s (id=%s): %d runners (+%d new, ~%d upd)",
                            _track_name(race),
                            race.get("number"),
                            rid,
                            len(starts),
                            ins,
                            upd,
                        )
                    except Exception as exc:  # noqa: BLE001
                        session.rollback()
                        stats["errors"].append(f"race {rid}: {exc}")
                        logger.exception("Failed race %s", rid)

            # --- Travsport-hevoshistorian keräys ---
            # Ajetaan ATG-datan jälkeen, per uniikki hevonen.
            # Try/except per hevonen: yksittäisen epäonnistumisen ei pidä
            # estää muita. Cache (7pv) estää turhat uudelleenhaut.
            if travsport is not None and horse_ids_seen:
                hs_inserted = 0
                hs_errors = 0
                for hid in sorted(horse_ids_seen):
                    try:
                        hs = _upsert_horse_starts(session, hid, travsport)
                        hs_inserted += hs["inserted"]
                    except Exception as exc:  # noqa: BLE001
                        hs_errors += 1
                        logger.warning(
                            "horse_starts upsert failed for %s: %s", hid, exc
                        )
                session.commit()
                stats["horse_starts_inserted"] = hs_inserted
                stats["horse_starts_errors"] = hs_errors
                logger.info(
                    "Travsport horse_starts: %d horses, +%d starts, %d errors",
                    len(horse_ids_seen),
                    hs_inserted,
                    hs_errors,
                )
    finally:
        if own_client:
            client.close()

    if earliest_start_dt is not None:
        stats["first_race_start_utc"] = earliest_start_dt

    logger.info(
        "fetch_daily_races: %d races, +%d new, ~%d upd, %d errors",
        stats["races_processed"],
        stats["runners_inserted"],
        stats["runners_updated"],
        len(stats["errors"]),
    )
    return stats


def fetch_results(
    race_id: str,
    db_path: str = DB_PATH,
    atg: ATGClient | None = None,
) -> dict:
    logger.info("fetch_results: race=%s", race_id)
    own_client = atg is None
    client = atg or ATGClient()
    Session_ = sessionmaker(bind=_engine(db_path))
    stats: dict[str, Any] = {
        "runners_updated": 0,
        "snapshots_inserted": 0,
        "errors": [],
    }
    try:
        race = client.get_race(race_id)
        now = datetime.now(timezone.utc)
        with Session_() as session:
            # Varmista että race + runners ovat olemassa (kylmäkäynnistys-suoja)
            _upsert_race(session, race)
            for s in race.get("starts") or []:
                horse = s.get("horse") or {}
                if not horse.get("id"):
                    continue
                _upsert_horse(session, horse)
                _upsert_runner(session, race, s)

                runner_id = f"{race_id}_{s.get('number')}"
                runner = session.get(Runner, runner_id)
                if runner is None:
                    continue

                result = s.get("result") or {}
                # ATG /races/{id}: result.place=0 tai None tarkoittaa ei
                # maalia / hylätty / laukka. Tallenna oikeat sijoitukset
                # (1+) ja jätä muut Noneksi.
                place = result.get("place")
                runner.finish_position = place if isinstance(place, int) and place > 0 else None
                # ATG-kenttä on "kmTime" (ei kilometerTime), ja laukanneilla
                # se voi olla {"code": "10"} -muodossa - _km_seconds palauttaa
                # silloin None.
                runner.kilometer_time_seconds = _km_seconds(result.get("kmTime"))
                win_odds = _odds_to_decimal(result.get("finalOdds"))
                runner.win_odds_final = win_odds

                stats["runners_updated"] += 1
                if win_odds is not None:
                    # Idempotentti: snapshot_label="result" + UNIQUE-indeksi
                    # → toistetut fetch_results-ajot päivittävät, ei
                    # duplikoi. Tämä on virallinen "closing"-kerroin
                    # post-race, ei pre-race nominaalipiste.
                    existing = (
                        session.query(OddsSnapshot)
                        .filter_by(runner_id=runner_id, snapshot_label="result")
                        .one_or_none()
                    )
                    if existing is None:
                        session.add(
                            OddsSnapshot(
                                runner_id=runner_id,
                                captured_at=now,
                                win_odds=win_odds,
                                raw_win_odds=win_odds,
                                snapshot_label="result",
                                source="atg_pari_mutuel",
                            )
                        )
                        stats["snapshots_inserted"] += 1
                    else:
                        existing.captured_at = now
                        existing.win_odds = win_odds
                        existing.raw_win_odds = win_odds
                        existing.source = "atg_pari_mutuel"
            session.commit()
    except Exception as exc:  # noqa: BLE001
        stats["errors"].append(str(exc))
        logger.exception("Failed fetch_results for %s", race_id)
    finally:
        if own_client:
            client.close()

    logger.info(
        "fetch_results: race=%s, %d runners updated, %d snapshots, %d errors",
        race_id,
        stats["runners_updated"],
        stats["snapshots_inserted"],
        len(stats["errors"]),
    )
    return stats


# ----------------------------------------------------------------------
# Closing odds snapshots (Step 3)
# ----------------------------------------------------------------------


def capture_odds_snapshot(
    race_id: str,
    snapshot_label: str,
    db_path: str = DB_PATH,
    atg: ATGClient | None = None,
) -> dict:
    """Tallenna yhden ajankohdan kerroin-snapshotti per runner.

    MVP-toteutus käyttää ATG:n pari-mutuel finalOdds-kenttää: kentän
    toinen merkitys ennen lähtöä on "current pool odds". Step 4:ssa
    source vaihtuu sharp-bookkeriin (Pinnacle / Betfair Exchange).

    Idempotentti: (runner_id, snapshot_label) on UNIQUE - jos rivi on jo
    olemassa, päivitetään in-place captured_at + arvot.

    Vig lasketaan koko lähdöstä (kaikki valid kertoimet), devig per runner.

    AIKALEIMAT - älä sekoita näitä:
      * captured_at: TODELLINEN tallennushetki UTC:ssä (datetime.now(utc)
        funktion ajohetkellä). Voi poiketa nominaali-ajoituksesta jos
        scheduler oli alhaalla / jobi ajettiin misfire_grace_time:n
        sisällä myöhässä.
      * snapshot_label: NOMINAALI-ajoituspiste suhteessa lähtöaikaan
        ("T-15min"..."T-2min"). Pysyvä avain joka mahdollistaa CLV-
        vertailut yli päivien vaikka todelliset captured_at-arvot
        liukuisivat sekunteja.
    """
    logger.info("capture_odds_snapshot: race=%s label=%s", race_id, snapshot_label)
    own_client = atg is None
    client = atg or ATGClient()
    Session_ = sessionmaker(bind=_engine(db_path))
    stats: dict[str, Any] = {
        "snapshots_inserted": 0,
        "snapshots_updated": 0,
        "errors": [],
        "vig_pct": None,
    }
    try:
        # Pre-race odds tulevat /games/vinnare_<race_id>:stä, EI /races:sta.
        # /races sisältää pre-race-aikaan vain runner-perustiedot ilman pools.
        game = client.get_win_pool_game(race_id)
        races_in_game = game.get("races") or []
        starts = (races_in_game[0].get("starts") if races_in_game else []) or []

        per_runner: list[tuple[str, float | None]] = []
        odds_list: list[float] = []
        for s in starts:
            runner_id = f"{race_id}_{s.get('number')}"
            raw = _pool_odds_to_decimal((s.get("pools") or {}).get("vinnare"))
            if raw is not None and raw > 1.0:
                odds_list.append(raw)
            per_runner.append((runner_id, raw))

        vig = calculate_vig(odds_list) if odds_list else 0.0
        stats["vig_pct"] = vig
        now = datetime.now(timezone.utc)

        with Session_() as session:
            for runner_id, raw in per_runner:
                if raw is None or raw <= 1.0:
                    continue
                if session.get(Runner, runner_id) is None:
                    # Runner ei vielä ole DB:ssä (esim. snapshot ennen
                    # daily-fetchiä). Skippaa hiljaisesti - cold start
                    # täyttää myöhemmin.
                    continue
                fair = devig_odds(raw, odds_list)
                existing = (
                    session.query(OddsSnapshot)
                    .filter_by(runner_id=runner_id, snapshot_label=snapshot_label)
                    .one_or_none()
                )
                if existing is None:
                    session.add(
                        OddsSnapshot(
                            runner_id=runner_id,
                            captured_at=now,
                            win_odds=raw,  # legacy-sarake = raw
                            raw_win_odds=raw,
                            devigged_win_odds=fair,
                            vig_pct=vig,
                            snapshot_label=snapshot_label,
                            source="atg_pari_mutuel",
                        )
                    )
                    stats["snapshots_inserted"] += 1
                else:
                    existing.captured_at = now
                    existing.win_odds = raw
                    existing.raw_win_odds = raw
                    existing.devigged_win_odds = fair
                    existing.vig_pct = vig
                    existing.source = "atg_pari_mutuel"
                    stats["snapshots_updated"] += 1
            session.commit()
    except Exception as exc:  # noqa: BLE001
        stats["errors"].append(str(exc))
        logger.exception("Failed capture_odds_snapshot %s/%s", race_id, snapshot_label)
    finally:
        if own_client:
            client.close()

    logger.info(
        "capture_odds_snapshot: race=%s label=%s, +%d new, ~%d upd, vig=%.3f",
        race_id,
        snapshot_label,
        stats["snapshots_inserted"],
        stats["snapshots_updated"],
        stats["vig_pct"] or 0.0,
    )
    return stats


def retry_incomplete_results(
    db_path: str = DB_PATH,
    lookback_days: int = 7,
    atg: ATGClient | None = None,
) -> dict:
    """Käy läpi viim. N päivän racet joilla on vajaita tulostietoja ja
    yritä hakea ATG:lta uudelleen.

    ATG:n /races/{id} -vastaus täyttyy vaiheittain (ks. moduulin docstring).
    Pelkkä T+30min fetch_results ei aina saa kaikkea: km-ajat ja sijoitukset
    4+ ilmaantuvat tunteja-päiviä myöhemmin. Tämä jobi ajaa fetch_results:n
    uudelleen jokaiselle racelle joka on edelleen vajaa.

    "Vajaa" = on ainakin yksi runner jolla finish_position IS NULL TAI
    kilometer_time_seconds IS NULL. Gallop-radat (GALLOP_TRACKS) jätetään
    pois kyselystä: niillä ei ole kmTime-objekteja ATG:ssa, joten ne
    olisivat aina vajaita ja aiheuttaisivat turhia API-kutsuja päivittäin.

    Idempotentti: fetch_results upsertit eivät duplikoi mitään.

    Args:
        lookback_days: kuinka pitkä aikaikkuna nykyhetkestä taaksepäin
            (oletus 7 — ATG ei tyypillisesti enää muutu sen jälkeen)

    Returns:
        dict: yhteenveto {races_checked, races_updated, errors}
    """
    cutoff = (datetime.now(ATG_TZ).date() - timedelta(days=lookback_days)).isoformat()
    logger.info(
        "retry_incomplete_results: lookback=%d days, cutoff>=%s",
        lookback_days, cutoff,
    )

    own_client = atg is None
    client = atg or ATGClient()
    Session_ = sessionmaker(bind=_engine(db_path))
    stats: dict[str, Any] = {
        "races_checked": 0,
        "races_updated": 0,
        "errors": [],
    }
    try:
        # Etsi uniikit race_idt joilla on vajaita runnereita.
        # Gallop-radat (GALLOP_TRACKS) jätetään pois: niillä ei ole
        # kmTime-objekteja ATG:ssa, joten ne olisivat aina vajaita.
        with Session_() as session:
            gallop_sorted = sorted(GALLOP_TRACKS)
            not_in_clause = ", ".join(f":gt{i}" for i in range(len(gallop_sorted)))
            params: dict = {
                "cutoff": cutoff,
                **{f"gt{i}": t for i, t in enumerate(gallop_sorted)},
            }
            rows = session.execute(text(f"""
                SELECT DISTINCT ra.race_id
                FROM races ra
                JOIN runners r ON ra.race_id = r.race_id
                WHERE ra.race_date >= :cutoff
                  AND ra.track NOT IN ({not_in_clause})
                  AND (r.finish_position IS NULL OR r.kilometer_time_seconds IS NULL)
                ORDER BY ra.race_id
            """), params).fetchall()
            race_ids = [row[0] for row in rows]

        logger.info(
            "retry_incomplete_results: %d races have NULL fields, retrying",
            len(race_ids),
        )

        for rid in race_ids:
            stats["races_checked"] += 1
            try:
                # fetch_results palauttaa stats jossa runners_updated > 0
                # jos jokin oikeasti muuttui. Kutsumme sitä omalla atg-
                # clientilla jaetun rate-limitin säilyttämiseksi.
                fetch_results(rid, db_path=db_path, atg=client)
                stats["races_updated"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"race {rid}: {exc}")
                logger.exception("retry_incomplete_results: race %s failed", rid)
    finally:
        if own_client:
            client.close()

    logger.info(
        "retry_incomplete_results: %d races checked, %d updated, %d errors",
        stats["races_checked"],
        stats["races_updated"],
        len(stats["errors"]),
    )
    return stats


def refresh_day_runners(
    target_date: date | None = None,
    db_path: str = DB_PATH,
    atg: ATGClient | None = None,
) -> dict:
    """Hae päivän kalenteri uudelleen ja päivitä runner-tiedot lopulliseksi.

    Tarkoitus: Ruotsin raviurheilussa kengitys- (barfota) ja kärry-
    (sulky) tiedot lukitaan 15min ennen päivän 1. lähdön starttia
    (Travsport / ATG -konventio). Sitä ennen valmentaja voi vaihtaa
    varustetta vapaasti, ja klo 03:00 _daily_setup voi saada vajaan/
    stale-version. Tämä jobi ajetaan dynaamisesti **päivän 1. lähdön
    startTime - 10min** -hetkellä → 5min varmuusmarginaali lukitusrajaan.

    Käytännössä: kutsuu `fetch_daily_races(target, scheduler=None,
    travsport=None)` joka ajaa _upsert_runner kaikille starts:lle —
    shoes/sulky/jne. päivittyy lopulliseen tilaansa. Snapshot- ja
    result-jobit on jo ajastettu aamuyöllä, EI uudelleen-ajasteta.

    Idempotentti: jos shoes/sulky olivat jo lopulliset 03:00-haussa,
    refresh ei muuta mitään.
    """
    target = target_date or date.today()
    logger.info("refresh_day_runners: target=%s", target.isoformat())
    return fetch_daily_races(target, db_path=db_path, atg=atg, scheduler=None, travsport=None)


def _schedule_first_race_refresh(
    scheduler: BlockingScheduler,
    target_date: date,
    first_race_start_utc: datetime,
    db_path: str = DB_PATH,
) -> int:
    """Ajasta refresh_day_runners päivän 1. lähdön - 10min hetkellä.

    Returns: 1 jos ajastettiin, 0 jos jo mennyt (esim. iltapäivärestart).
    """
    refresh_at = first_race_start_utc - timedelta(minutes=10)
    if refresh_at < datetime.now(timezone.utc):
        logger.info(
            "_schedule_first_race_refresh: target=%s, first race - 10min "
            "(%s) is in the past, skipping",
            target_date.isoformat(),
            refresh_at.isoformat(timespec="minutes"),
        )
        return 0
    scheduler.add_job(
        refresh_day_runners,
        trigger=DateTrigger(run_date=refresh_at),
        args=[target_date, db_path],
        id=f"refresh_runners_{target_date.isoformat()}",
        replace_existing=True,
        misfire_grace_time=300,  # 5min - jos hetken viive
    )
    logger.info(
        "_schedule_first_race_refresh: target=%s, scheduled at %s "
        "(first race %s - 10min)",
        target_date.isoformat(),
        refresh_at.isoformat(timespec="minutes"),
        first_race_start_utc.isoformat(timespec="minutes"),
    )
    return 1


def _schedule_results_job(
    scheduler: BlockingScheduler,
    race_id: str,
    start_time_utc: datetime,
    db_path: str = DB_PATH,
) -> int:
    """Ajasta tulosten haku 30min lähdön jälkeen. Skippaa jos jo mennyt."""
    run_at = start_time_utc + timedelta(minutes=30)
    if run_at < datetime.now(timezone.utc):
        return 0
    scheduler.add_job(
        fetch_results,
        trigger=DateTrigger(run_date=run_at),
        args=[race_id, db_path],
        id=f"results_{race_id}",
        replace_existing=True,
        misfire_grace_time=120,
    )
    return 1


def schedule_odds_snapshots(
    scheduler: BlockingScheduler,
    race_id: str,
    start_time_utc: datetime,
    db_path: str = DB_PATH,
) -> int:
    """Ajasta 4 snapshot-jobia per lähtö. Skippaa jo menneet.

    misfire_grace_time=120: jos prosessi on alhaalla 2min, ajaa silti
    kun palautuu. Yli 2min myöhässä -> ajetaan myöhemmin (turha snapshot,
    annetaan pudota).
    """
    now = datetime.now(timezone.utc)
    scheduled = 0
    for label, delta in SNAPSHOT_OFFSETS:
        run_at = start_time_utc - delta
        if run_at < now:
            continue
        scheduler.add_job(
            capture_odds_snapshot,
            trigger=DateTrigger(run_date=run_at),
            args=[race_id, label, db_path],
            id=f"snap_{race_id}_{label}",
            replace_existing=True,
            misfire_grace_time=120,
        )
        scheduled += 1
    return scheduled


# ----------------------------------------------------------------------
# Run-modes
# ----------------------------------------------------------------------


def _parse_atg_datetime(s: str | None) -> datetime | None:
    """Tulkitse ATG:n startTime tz-tietoisena UTC-datetimena.

    ATG palauttaa "2026-04-27T18:00:00" naiivina. Lokalisoimme Europe/
    Stockholmiin ja konvertoimme UTC:hen, jotta DateTrigger ja vertailut
    toimivat oikein DST-rajoilla ja muissa aikavyöhykkeissä ajettuna.
    Jos arvossa on jo offset (esim. "Z"), kunnioitetaan sitä.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ATG_TZ)
    return dt.astimezone(timezone.utc)


def run_once(target_date: date | None = None, db_path: str = DB_PATH) -> dict:
    """Aja päivä kerran: hae lähdöt + hae tulokset niistä jotka ovat jo
    juostu (start_time + 30min < now). Ei ajasta snapshotteja - tämä on
    manuaalitesti, scheduler-instanssin scope ei ulotu paluun yli."""
    from src.data.scrapers.travsport import TravsportAPIClient

    target = target_date or date.today()
    with TravsportAPIClient() as ts:
        daily = fetch_daily_races(target, db_path=db_path, travsport=ts)
    finished_results = 0
    with ATGClient() as atg:
        cal = atg.get_calendar_day(target)
        now = datetime.now(timezone.utc)
        for track in cal.get("tracks", []) or []:
            for r in track.get("races", []) or []:
                start_dt = _parse_atg_datetime(r.get("startTime"))
                if r.get("id") and start_dt and now > start_dt + timedelta(minutes=30):
                    fetch_results(r["id"], db_path=db_path, atg=atg)
                    finished_results += 1
    return {"daily": daily, "finished_results_processed": finished_results}


# Iltapäivän kynnys jolloin _initial_setup hakee jo huomisen lähdöt
# (jotta seuraavan aamun varhaisia kortteja ei missata 03:00-jobiin asti).
_LATE_AFTERNOON_HOUR_LOCAL = 18


def _setup_for_date(
    scheduler: BlockingScheduler,
    target: date,
    db_path: str,
    label: str,
    travsport: Any | None = None,
) -> dict:
    """Yhteinen toteutus _initial_setupille ja _daily_setupille.

    Eristää ATG-virheet: jos koko päivän haku epäonnistuu, lokita ja
    palauta error-stats. Per-race-virheet jäävät fetch_daily_races:in
    vastuulle (se täyttää errors-listan ja jatkaa).
    """
    try:
        stats = fetch_daily_races(
            target, db_path=db_path, scheduler=scheduler, travsport=travsport
        )
        # Ajasta refresh-jobi 1. lähdön - 10min hetkellä jotta shoes/sulky
        # ja muut runner-kentät päivittyvät lukitusrajan jälkeen lopulliseksi.
        first = stats.get("first_race_start_utc")
        if first is not None:
            n_refresh = _schedule_first_race_refresh(scheduler, target, first, db_path)
            stats["refresh_jobs"] = n_refresh
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s: fetch_daily_races failed for %s", label, target)
        return {"error": str(exc), "target": target.isoformat()}

    logger.info(
        "%s for %s: %d races, %d snapshot jobs, %d result jobs, %d refresh jobs, %d errors",
        label,
        target.isoformat(),
        stats.get("races_processed", 0),
        stats.get("snapshot_jobs", 0),
        stats.get("result_jobs", 0),
        stats.get("refresh_jobs", 0),
        len(stats.get("errors", [])),
    )
    return stats


def _initial_setup(
    scheduler: BlockingScheduler,
    db_path: str = DB_PATH,
    travsport: Any | None = None,
) -> dict:
    """Käynnistyssetuppi: hae tämän päivän + tarvittaessa huomisen lähdöt.

    Iltapäivällä (>=18:00 Stockholm) haetaan myös huominen, koska
    seuraava 03:00-jobi olisi liian myöhään aamukortteja varten - jotkut
    ravipäivät alkavat klo 12. Lokitetaan selvästi jos näin tehdään.
    """
    today = datetime.now(ATG_TZ).date()
    today_stats = _setup_for_date(
        scheduler, today, db_path, "_initial_setup", travsport=travsport
    )

    tomorrow_stats: dict = {}
    local_hour = datetime.now(ATG_TZ).hour
    if local_hour >= _LATE_AFTERNOON_HOUR_LOCAL:
        tomorrow = today + timedelta(days=1)
        logger.info(
            "_initial_setup: local time %02d:00 >= %02d:00 - prefetching %s",
            local_hour,
            _LATE_AFTERNOON_HOUR_LOCAL,
            tomorrow.isoformat(),
        )
        tomorrow_stats = _setup_for_date(
            scheduler, tomorrow, db_path, "_initial_setup (tomorrow)",
            travsport=travsport,
        )

    return {"today": today_stats, "tomorrow": tomorrow_stats}


def _daily_setup(
    scheduler: BlockingScheduler,
    db_path: str = DB_PATH,
    travsport: Any | None = None,
) -> dict:
    """Päivittäinen 03:00-jobi: hae päivän lähdöt + ajasta snapshot/result-jobit."""
    today = datetime.now(ATG_TZ).date()
    return _setup_for_date(
        scheduler, today, db_path, "_daily_setup", travsport=travsport
    )


def run_forever(db_path: str = DB_PATH) -> None:
    """Tuotanto-scheduler: blokkaa terminaalin, ajaa kunnes Ctrl+C / SIGTERM.

    Käytä systemd / supervisord / screen / tmux tuotannossa - tämä
    funktio ei daemonisoi itse.

    TravsportAPIClient luodaan kerran ja pidetään auki koko schedulerin
    elinkaaren ajan. Cache (7pv) estää turhat uudelleenhaut, rate limit
    (1 req/sec) suojelee API:a.
    """
    from src.data.scrapers.travsport import TravsportAPIClient

    scheduler = BlockingScheduler(timezone=timezone.utc)
    travsport = TravsportAPIClient()

    _initial_setup(scheduler, db_path=db_path, travsport=travsport)

    scheduler.add_job(
        _daily_setup,
        trigger=CronTrigger(hour=3, minute=0, timezone=ATG_TZ),
        args=[scheduler, db_path, travsport],
        id="daily_setup",
        replace_existing=True,
        misfire_grace_time=600,  # 10min - jos kone heräsi sleepistä
    )

    # Päivittäinen retry vajaista tuloksista klo 04:30 Stockholm.
    # Sijoitettu 04:00 cron-backupin JÄLKEEN jotta backup ehtii ottaa
    # snapshotin ennen retryjä (diagnostiikkaystävällinen).
    scheduler.add_job(
        retry_incomplete_results,
        trigger=CronTrigger(hour=4, minute=30, timezone=ATG_TZ),
        args=[db_path],
        id="retry_incomplete_results",
        replace_existing=True,
        misfire_grace_time=1800,  # 30min - tämä ei ole aikakriittinen
    )

    logger.info("Scheduler started; blocking. Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shutdown requested")
        scheduler.shutdown(wait=False)
    finally:
        travsport.close()


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(prog="src.data.scheduler")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_once = sub.add_parser("run-once", help="Aja kerran (manuaalitesti)")
    p_once.add_argument("--date", help="YYYY-MM-DD (oletus: tänään)")

    sub.add_parser(
        "run-forever",
        help=(
            "Käynnistä BlockingScheduler tuotantokäyttöön. BLOKKAA "
            "TERMINAALIN. Käytä systemd / supervisord / tmux / screen "
            "jotta ajo jatkuu SSH-session jälkeen."
        ),
    )

    p_results = sub.add_parser("fetch-results", help="Hae yhden racen tulokset")
    p_results.add_argument("--race-id", required=True)

    p_snap = sub.add_parser(
        "capture-snapshot", help="Tallenna yksi odds-snapshotti (manuaalitesti)"
    )
    p_snap.add_argument("--race-id", required=True)
    p_snap.add_argument(
        "--label",
        required=True,
        help="esim. T-15min | T-10min | T-5min | T-2min",
    )

    p_retry = sub.add_parser(
        "retry-incomplete",
        help="Hae uudelleen tulokset raceille joilla on vajaita kenttiä "
             "(NULL finish_position tai kilometer_time_seconds). Pyörii "
             "automaattisesti klo 04:30 cron-jobissa run-forever-tilassa.",
    )
    p_retry.add_argument(
        "--lookback", type=int, default=7,
        help="Kuinka monta päivää taaksepäin etsitään vajaita raceja (oletus 7)",
    )

    p_refresh = sub.add_parser(
        "refresh-day-runners",
        help="Hae päivän kalenteri uudelleen ja päivitä runner-tiedot lopulliseksi "
             "(shoes/sulky lukitaan 15min ennen päivän 1. lähtöä). "
             "Pyörii automaattisesti dynaamisella DateTriggerillä.",
    )
    p_refresh.add_argument(
        "--date", help="YYYY-MM-DD (oletus: tänään)",
    )

    sub.add_parser(
        "backfill-track-condition",
        help="Täytä track_condition kaikille horse_starts-riveille paikallisista "
             "Travsport-välimuistitiedostoista. Ei API-kutsuja. Ajo kerran riittää "
             "(idempotentti: päivittää vain NULL-rivit).",
    )

    args = parser.parse_args()
    migrate(DB_PATH)  # varmista että uudet sarakkeet ovat olemassa

    if args.cmd == "run-once":
        target = date.fromisoformat(args.date) if args.date else None
        print(run_once(target))
    elif args.cmd == "run-forever":
        run_forever()
    elif args.cmd == "fetch-results":
        print(fetch_results(args.race_id))
    elif args.cmd == "capture-snapshot":
        print(capture_odds_snapshot(args.race_id, args.label))
    elif args.cmd == "retry-incomplete":
        print(retry_incomplete_results(lookback_days=args.lookback))
    elif args.cmd == "refresh-day-runners":
        target = date.fromisoformat(args.date) if args.date else None
        print(refresh_day_runners(target))
    elif args.cmd == "backfill-track-condition":
        print(backfill_track_condition())


if __name__ == "__main__":
    _main()
