"""Tests for scheduler idempotency and result updates."""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.data.schema import Horse, HorseStart, OddsSnapshot, Race, Runner, migrate
from src.data import scheduler as scheduler_mod
from src.data.scheduler import (
    _ensure_runner_exists,
    _parse_terms,
    _person_aggregates,
    _set_if_not_none,
    _upsert_runner,
    backfill_correct_atg_aggregates,
    backfill_race_class,
    capture_odds_snapshot,
    fetch_daily_races,
    fetch_results,
    retry_incomplete_results,
    schedule_odds_snapshots,
)

# ---------------------------------------------------------------------------
# Synteettinen ATG-fixture
# ---------------------------------------------------------------------------

RACE_ID = "2026-04-27_99_1"

SAMPLE_RACE = {
    "id": RACE_ID,
    "date": "2026-04-27",
    "number": 1,
    "distance": 2140,
    "startMethod": "auto",
    "startTime": "2026-04-27T18:00:00",
    "track": {"name": "Solvalla"},
    "prize": 50000,
    "starts": [
        {
            "id": 1,
            "number": 1,
            "postPosition": 1,
            "distance": 2140,
            "horse": {
                "id": 100001,
                "name": "Test Horse 1",
                "age": 5,
                "sex": "gelding",
                "trainer": {"firstName": "First", "lastName": "Trainer"},
                "pedigree": {
                    "father": {"id": 1001, "name": "Famous Sire"},
                    "mother": {"id": 1002, "name": "Famous Dam"},
                    "grandfather": {"id": 1003, "name": "Famous Grandsire"},
                },
                "statistics": {
                    "life": {
                        "starts": 50,
                        "earnings": 1000000,
                        "placement": {"1": 5, "2": 8, "3": 10},
                        "records": [
                            {
                                "startMethod": "auto",
                                "distance": "medium",
                                "time": {"minutes": 1, "seconds": 13, "tenths": 4},
                            },
                        ],
                    },
                    "years": {"2026": {"starts": 10, "placement": {"1": 2}}},
                },
            },
            "driver": {
                "id": 608305,
                "firstName": "Driver",
                "lastName": "One",
                "statistics": {
                    "years": {
                        "2026": {
                            "starts": 142,
                            "placement": {"1": 22, "2": 18, "3": 15},
                            "winPercentage": 1549,
                            "earnings": 2341000,
                        }
                    }
                },
            },
        },
        {
            "id": 2,
            "number": 2,
            "postPosition": 2,
            "distance": 2160,
            "horse": {
                "id": 100002,
                "name": "Test Horse 2",
                "age": 7,
                "sex": "mare",
                "trainer": {"firstName": "Second", "lastName": "Trainer"},
                "statistics": {
                    "life": {
                        "starts": 0,
                        "placement": {"1": 0, "2": 0, "3": 0},
                        "records": [],
                    },
                    "years": {},
                },
            },
            "driver": {
                "id": 608400,
                "firstName": "Driver",
                "lastName": "Two",
                "statistics": {"years": {}},
            },
        },
    ],
}

SAMPLE_CALENDAR = {
    "tracks": [
        {"name": "Solvalla", "races": [{"id": RACE_ID}]},
    ]
}


class FakeATG:
    def __init__(self, race: dict | None = None):
        self.race = race or SAMPLE_RACE
        self.calls = {"calendar": 0, "race": 0, "game": 0}

    def get_calendar_day(self, _d, swedish_only=True):  # noqa: ANN001
        self.calls["calendar"] += 1
        return SAMPLE_CALENDAR

    def get_race(self, _rid):  # noqa: ANN001
        self.calls["race"] += 1
        return self.race

    def get_win_pool_game(self, race_id):  # noqa: ANN001
        """Mock /games/vinnare_<race_id>:tä rakentaen pool-rakenteen
        nykyisestä self.race-fixturesta. Käyttää result.finalOdds:ia
        lähteenä koska samat fixtures-fileet kattavat sekä pre/post-race."""
        self.calls["game"] += 1
        game_starts = []
        for s in self.race.get("starts") or []:
            new_s = {k: v for k, v in s.items() if k != "result"}
            raw = (s.get("result") or {}).get("finalOdds")
            if raw is not None:
                new_s["pools"] = {
                    "vinnare": {"odds": int(round(float(raw) * 100))}
                }
            game_starts.append(new_s)
        race_copy = {**self.race, "starts": game_starts}
        return {"id": f"vinnare_{race_id}", "races": [race_copy]}

    def close(self) -> None:
        pass


class FakeTravsport:
    """Mock TravsportAPIClient joka palauttaa synteettistä starttihistoriaa."""

    def __init__(self, starts_per_horse: dict[str, list[dict]] | None = None):
        self.starts_per_horse = starts_per_horse or {}
        self.calls: list[str] = []

    def get_results(self, horse_id, force_refresh=False):
        self.calls.append(str(horse_id))
        return self.starts_per_horse.get(str(horse_id), [])

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _session(db: str):
    return sessionmaker(bind=create_engine(f"sqlite:///{db}"))()


def test_fetch_daily_races_creates_rows(tmp_path):
    db = str(tmp_path / "test.db")
    migrate(db)
    stats = fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG())

    assert stats["races_processed"] == 1
    assert stats["runners_inserted"] == 2
    assert stats["runners_updated"] == 0
    assert stats["errors"] == []

    with _session(db) as s:
        assert s.query(Race).count() == 1
        assert s.query(Runner).count() == 2
        assert s.query(Horse).count() == 2

        runner = s.query(Runner).filter_by(start_number=1).one()
        assert runner.atg_lifetime_starts == 50
        assert runner.atg_lifetime_win_rate == 5 / 50
        assert runner.atg_lifetime_top3_rate == (5 + 8 + 10) / 50
        assert runner.atg_current_year_win_rate == 2 / 10
        # 1:13.4 = 73.4s, ja distance_bucket(2140) = "medium" matchaa recordia
        assert runner.atg_best_km_for_this_setup == 73.4

        # 0-startteja hevosella aggregaatit None
        runner2 = s.query(Runner).filter_by(start_number=2).one()
        assert runner2.atg_lifetime_starts is None
        assert runner2.atg_lifetime_win_rate is None
        assert runner2.atg_best_km_for_this_setup is None

        # Handicap_meters: start.distance 2160 - race.distance 2140 = 20
        assert runner2.handicap_meters == 20


def test_fetch_daily_races_idempotent(tmp_path):
    db = str(tmp_path / "test.db")
    migrate(db)
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG())
    stats2 = fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG())

    assert stats2["runners_inserted"] == 0
    assert stats2["runners_updated"] == 2
    with _session(db) as s:
        assert s.query(Race).count() == 1
        assert s.query(Runner).count() == 2
        assert s.query(Horse).count() == 2


def test_fetch_results_updates_finish_position_no_new_runners(tmp_path):
    db = str(tmp_path / "test.db")
    migrate(db)
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG())

    race_with_results = copy.deepcopy(SAMPLE_RACE)
    for i, s in enumerate(race_with_results["starts"]):
        s["result"] = {
            "place": i + 1,
            "kmTime": {"minutes": 1, "seconds": 14, "tenths": 0},
            "finalOdds": 2.34 + i,  # 2.34, 3.34
        }

    stats = fetch_results(RACE_ID, db_path=db, atg=FakeATG(race_with_results))
    assert stats["runners_updated"] == 2
    assert stats["snapshots_inserted"] == 2
    assert stats["errors"] == []

    with _session(db) as s:
        assert s.query(Runner).count() == 2  # ei uusia rivejä
        runners = s.query(Runner).order_by(Runner.start_number).all()
        assert runners[0].finish_position == 1
        assert runners[1].finish_position == 2
        assert runners[0].kilometer_time_seconds == 74.0
        assert runners[0].win_odds_final == 2.34
        assert runners[1].win_odds_final == 3.34
        # ATG-aggregaatit säilyvät (eivät tyhjenny tulosten päivityksessä)
        assert runners[0].atg_lifetime_starts == 50
        assert s.query(OddsSnapshot).count() == 2


def test_fetch_results_idempotent(tmp_path):
    """Tulosten haku kahteen kertaan: snapshotteja kertyy mutta runners ei
    duplikoidu."""
    db = str(tmp_path / "test.db")
    migrate(db)
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG())

    race_with_results = copy.deepcopy(SAMPLE_RACE)
    for i, s in enumerate(race_with_results["starts"]):
        s["result"] = {
            "place": i + 1,
            "kmTime": {"minutes": 1, "seconds": 14, "tenths": 0},
            "finalOdds": 2.50,
        }
    fetch_results(RACE_ID, db_path=db, atg=FakeATG(race_with_results))
    fetch_results(RACE_ID, db_path=db, atg=FakeATG(race_with_results))

    with _session(db) as s:
        assert s.query(Runner).count() == 2
        # H3: snapshot_label="result" + UNIQUE → 2 ajoa = 2 riviä
        # (idempotentti, päivittää in-place)
        assert s.query(OddsSnapshot).count() == 2
        for snap in s.query(OddsSnapshot).all():
            assert snap.snapshot_label == "result"


def test_fetch_results_handles_disqualified_and_galloped(tmp_path):
    """ATG empiirisesti: place=0 = ei-maaliin, place=None + kmTime.code = laukka.
    Molemmissa finish_position ja kilometer_time_seconds → None."""
    db = str(tmp_path / "test.db")
    migrate(db)
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG())

    race_with_results = copy.deepcopy(SAMPLE_RACE)
    race_with_results["starts"][0]["result"] = {
        "place": 0,  # ei-maaliin / hylätty
        "kmTime": {"minutes": 1, "seconds": 20, "tenths": 8},
        "finalOdds": 76.72,
    }
    race_with_results["starts"][1]["result"] = {
        "place": None,  # laukka
        "kmTime": {"code": "10"},
        "finalOdds": 4.10,
    }

    fetch_results(RACE_ID, db_path=db, atg=FakeATG(race_with_results))

    with _session(db) as s:
        runners = s.query(Runner).order_by(Runner.start_number).all()
        assert runners[0].finish_position is None  # place=0
        assert runners[1].finish_position is None  # place=None
        assert runners[1].kilometer_time_seconds is None  # laukka-koodi
        # finalOdds tallentuu silti molemmille
        assert runners[0].win_odds_final == 76.72
        assert runners[1].win_odds_final == 4.10


def _build_race_with_odds(odds_per_runner: list[float | None], race_id: str = RACE_ID) -> dict:
    """Rakenna race jossa N runneria, kullakin annettu finalOdds (None = scratch)."""
    starts = []
    for i, o in enumerate(odds_per_runner):
        s = {
            "id": i + 1,
            "number": i + 1,
            "postPosition": i + 1,
            "distance": 2140,
            "horse": {
                "id": 200000 + i,
                "name": f"H{i + 1}",
                "age": 5,
                "sex": "gelding",
                "trainer": {"firstName": "T", "lastName": str(i)},
                "statistics": {
                    "life": {"starts": 0, "placement": {}, "records": []},
                    "years": {},
                },
            },
            "driver": {"firstName": "D", "lastName": str(i)},
        }
        if o is not None:
            s["result"] = {"finalOdds": o}
        starts.append(s)
    return {
        "id": race_id,
        "date": "2026-04-27",
        "number": 1,
        "distance": 2140,
        "startMethod": "auto",
        "startTime": "2026-04-27T18:00:00",
        "track": {"name": "Solvalla"},
        "prize": 50000,
        "starts": starts,
    }


def test_capture_odds_snapshot_creates_snapshots(tmp_path):
    db = str(tmp_path / "test.db")
    migrate(db)
    odds = [2.0, 4.0, 5.0, 10.0, 10.0, 20.0, 50.0, 100.0]
    race = _build_race_with_odds(odds)
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG(race))

    stats = capture_odds_snapshot(
        RACE_ID, "T-15min", db_path=db, atg=FakeATG(race)
    )
    assert stats["snapshots_inserted"] == 8
    assert stats["snapshots_updated"] == 0
    assert stats["errors"] == []

    with _session(db) as s:
        snaps = s.query(OddsSnapshot).filter_by(snapshot_label="T-15min").all()
        assert len(snaps) == 8
        for snap in snaps:
            assert snap.source == "atg_pari_mutuel"
            assert snap.raw_win_odds is not None
            assert snap.devigged_win_odds is not None
            assert snap.devigged_win_odds > snap.raw_win_odds  # devig kasvattaa


def test_capture_odds_snapshot_idempotent(tmp_path):
    db = str(tmp_path / "test.db")
    migrate(db)
    race = _build_race_with_odds([2.5, 3.5])
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG(race))

    capture_odds_snapshot(RACE_ID, "T-5min", db_path=db, atg=FakeATG(race))
    stats2 = capture_odds_snapshot(RACE_ID, "T-5min", db_path=db, atg=FakeATG(race))
    assert stats2["snapshots_inserted"] == 0
    assert stats2["snapshots_updated"] == 2

    with _session(db) as s:
        snaps = s.query(OddsSnapshot).filter_by(snapshot_label="T-5min").all()
        assert len(snaps) == 2  # ei duplikaatteja


def test_capture_odds_snapshot_handles_scratched(tmp_path):
    db = str(tmp_path / "test.db")
    migrate(db)
    # Toisella runnerilla ei finalOdds-kenttää (scratch)
    race = _build_race_with_odds([3.0, None, 5.0])
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG(race))

    stats = capture_odds_snapshot(
        RACE_ID, "T-2min", db_path=db, atg=FakeATG(race)
    )
    assert stats["snapshots_inserted"] == 2  # vain 2 (skratattu pois)
    assert stats["errors"] == []

    with _session(db) as s:
        snaps = s.query(OddsSnapshot).filter_by(snapshot_label="T-2min").all()
        assert len(snaps) == 2
        runner_ids = {snap.runner_id for snap in snaps}
        assert f"{RACE_ID}_2" not in runner_ids


def test_capture_odds_snapshot_calculates_vig(tmp_path):
    db = str(tmp_path / "test.db")
    migrate(db)
    odds = [2.0, 4.0, 5.0, 10.0, 10.0, 20.0, 50.0, 100.0]
    expected_vig = sum(1.0 / o for o in odds) - 1.0
    race = _build_race_with_odds(odds)
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG(race))

    stats = capture_odds_snapshot(
        RACE_ID, "T-10min", db_path=db, atg=FakeATG(race)
    )
    assert abs(stats["vig_pct"] - expected_vig) < 1e-9
    with _session(db) as s:
        snap = s.query(OddsSnapshot).filter_by(snapshot_label="T-10min").first()
        assert abs(snap.vig_pct - expected_vig) < 1e-9


def test_schedule_odds_snapshots_skips_past():
    sched = MagicMock()
    past = datetime.now(timezone.utc) - timedelta(minutes=30)
    n = schedule_odds_snapshots(sched, RACE_ID, past)
    assert n == 0
    sched.add_job.assert_not_called()


def test_schedule_odds_snapshots_partial():
    sched = MagicMock()
    # Lähtö T+10min: T-15 ja T-10 ovat menneitä, T-5 ja T-2 tulevia
    start = datetime.now(timezone.utc) + timedelta(minutes=10)
    n = schedule_odds_snapshots(sched, RACE_ID, start)
    assert n == 2
    assert sched.add_job.call_count == 2
    scheduled_labels = {
        call.kwargs.get("id") or call.args[0]
        for call in sched.add_job.call_args_list
    }
    # job-id on f"snap_{race_id}_{label}"
    job_ids = [c.kwargs["id"] for c in sched.add_job.call_args_list]
    assert any("T-5min" in jid for jid in job_ids)
    assert any("T-2min" in jid for jid in job_ids)
    assert not any("T-15min" in jid for jid in job_ids)
    assert not any("T-10min" in jid for jid in job_ids)


def test_initial_setup_schedules_jobs_for_today(monkeypatch):
    """Yksinkertainen tapaus: aamupäivä, hae vain tänään."""
    sched = MagicMock()
    captured = {}

    def fake_fetch_daily_races(target, db_path, scheduler, **kwargs):
        captured.setdefault("calls", []).append((target, scheduler))
        return {"races_processed": 3, "snapshot_jobs": 12, "result_jobs": 3, "errors": []}

    monkeypatch.setattr(scheduler_mod, "fetch_daily_races", fake_fetch_daily_races)
    # Lukitse lokaali aikaa: aamupäivä Stockholm
    fixed = datetime(2026, 4, 27, 9, 0, tzinfo=scheduler_mod.ATG_TZ)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(scheduler_mod, "datetime", _DT)

    result = scheduler_mod._initial_setup(sched, db_path="x.db")
    assert len(captured["calls"]) == 1
    assert captured["calls"][0][0] == date(2026, 4, 27)
    assert captured["calls"][0][1] is sched
    assert result["tomorrow"] == {}


def test_initial_setup_fetches_tomorrow_when_late(monkeypatch):
    """Iltapäivä >=18:00: hae myös huominen."""
    sched = MagicMock()
    targets: list[date] = []

    def fake_fetch_daily_races(target, db_path, scheduler, **kwargs):
        targets.append(target)
        return {"races_processed": 1, "snapshot_jobs": 4, "result_jobs": 1, "errors": []}

    monkeypatch.setattr(scheduler_mod, "fetch_daily_races", fake_fetch_daily_races)
    fixed = datetime(2026, 4, 27, 19, 30, tzinfo=scheduler_mod.ATG_TZ)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(scheduler_mod, "datetime", _DT)

    scheduler_mod._initial_setup(sched, db_path="x.db")
    assert targets == [date(2026, 4, 27), date(2026, 4, 28)]


def test_initial_setup_handles_atg_failure(monkeypatch, caplog):
    """fetch_daily_races heittää -> _initial_setup ei kaadu, virhe lokiin."""
    sched = MagicMock()

    def boom(target, db_path, scheduler, **kwargs):
        raise RuntimeError("ATG 503")

    monkeypatch.setattr(scheduler_mod, "fetch_daily_races", boom)
    fixed = datetime(2026, 4, 27, 9, 0, tzinfo=scheduler_mod.ATG_TZ)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(scheduler_mod, "datetime", _DT)

    with caplog.at_level("ERROR", logger="ravit_edge.scheduler"):
        result = scheduler_mod._initial_setup(sched, db_path="x.db")
    assert "error" in result["today"]
    assert "ATG 503" in result["today"]["error"]
    assert any("fetch_daily_races failed" in r.message for r in caplog.records)


def test_daily_setup_schedules_jobs(monkeypatch):
    sched = MagicMock()
    captured = {}

    def fake_fetch_daily_races(target, db_path, scheduler, **kwargs):
        captured["target"] = target
        captured["scheduler"] = scheduler
        captured["db_path"] = db_path
        return {"races_processed": 5, "snapshot_jobs": 20, "result_jobs": 5, "errors": []}

    monkeypatch.setattr(scheduler_mod, "fetch_daily_races", fake_fetch_daily_races)
    fixed = datetime(2026, 4, 27, 3, 0, tzinfo=scheduler_mod.ATG_TZ)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(scheduler_mod, "datetime", _DT)

    stats = scheduler_mod._daily_setup(sched, db_path="x.db")
    assert captured["target"] == date(2026, 4, 27)
    assert captured["scheduler"] is sched
    assert captured["db_path"] == "x.db"
    assert stats["races_processed"] == 5


def test_fetch_results_cold_start_creates_runner(tmp_path):
    """Jos race tulee fetch_resultsiin ilman edeltävää daily-fetchiä,
    runner luodaan silti. Riviä ei pidä jättää välistä."""
    db = str(tmp_path / "test.db")
    migrate(db)

    race_with_results = copy.deepcopy(SAMPLE_RACE)
    for i, s in enumerate(race_with_results["starts"]):
        s["result"] = {
            "place": i + 1,
            "kmTime": {"minutes": 1, "seconds": 14, "tenths": 0},
            "finalOdds": 1.00,
        }
    fetch_results(RACE_ID, db_path=db, atg=FakeATG(race_with_results))

    with _session(db) as s:
        assert s.query(Race).count() == 1
        assert s.query(Runner).count() == 2
        assert s.query(Runner).filter_by(start_number=1).one().finish_position == 1


# ---------------------------------------------------------------------------
# Osa 1: ATG driver/trainer-aggregaatit
# ---------------------------------------------------------------------------


def test_person_aggregates_extracts_driver_stats():
    """_person_aggregates poimii oikeat kentät driver-statsista."""
    driver = {
        "id": 608305,
        "firstName": "Driver",
        "lastName": "One",
        "statistics": {
            "years": {
                "2026": {
                    "starts": 142,
                    "placement": {"1": 22, "2": 18, "3": 15},
                    "winPercentage": 1549,
                    "earnings": 2341000,
                }
            }
        },
    }
    race = {"date": "2026-04-27"}
    result = _person_aggregates(driver, race, "atg_driver")
    assert result["atg_driver_id"] == "608305"
    assert result["atg_driver_starts"] == 142
    assert abs(result["atg_driver_win_pct"] - 0.1549) < 1e-9
    assert result["atg_driver_earnings"] == 2341000


def test_person_aggregates_returns_none_for_missing_year():
    """Jos driver ei ole ajanut kuluvana vuonna, starts/win_pct/earnings → None."""
    driver = {
        "id": 999,
        "statistics": {"years": {"2025": {"starts": 50, "winPercentage": 1200}}},
    }
    race = {"date": "2026-04-27"}
    result = _person_aggregates(driver, race, "atg_driver")
    assert result["atg_driver_id"] == "999"
    assert result["atg_driver_starts"] is None
    assert result["atg_driver_win_pct"] is None
    assert result["atg_driver_earnings"] is None


def test_person_aggregates_handles_none_person():
    result = _person_aggregates(None, {"date": "2026-04-27"}, "atg_trainer")
    assert result["atg_trainer_id"] is None
    assert result["atg_trainer_starts"] is None


def test_fetch_daily_races_saves_driver_trainer_aggregates(tmp_path):
    """fetch_daily_races tallentaa driver/trainer-aggregaatit runner-riville."""
    db = str(tmp_path / "test.db")
    migrate(db)
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG())

    with _session(db) as s:
        runner1 = s.query(Runner).filter_by(start_number=1).one()
        # Driver 1: 142 starts, winPercentage 1549 = 0.1549
        assert runner1.atg_driver_id == "608305"
        assert runner1.atg_driver_starts == 142
        assert abs(runner1.atg_driver_win_pct - 0.1549) < 1e-9
        assert runner1.atg_driver_earnings == 2341000

        # Driver 2: ei 2026-tilastoja → None
        runner2 = s.query(Runner).filter_by(start_number=2).one()
        assert runner2.atg_driver_id == "608400"
        assert runner2.atg_driver_starts is None
        assert runner2.atg_driver_win_pct is None


# ---------------------------------------------------------------------------
# Osa 2: Travsport horse_starts
# ---------------------------------------------------------------------------


TRAVSPORT_STARTS = {
    "100001": [
        {
            "race_date": "2026-03-15",
            "track": "SO",
            "race_id": 88001,
            "race_number": 3,
            "distance": 2140,
            "start_method": "A",
            "start_number": 5,
            "finish_position": 2,
            "kilometer_time_seconds": 73.8,
            "position_at_800m": None,
            "track_condition": "LE",
            "driver": "Driver One",
            "trainer": "First Trainer",
            "prize_won": 25000,
            "win_odds_final": 4.5,
            "withdrawn": False,
        },
        {
            "race_date": "2026-02-20",
            "track": "AX",
            "race_id": 87999,
            "race_number": 1,
            "distance": 2640,
            "start_method": "V",
            "start_number": 3,
            "finish_position": 1,
            "kilometer_time_seconds": 78.2,
            "position_at_800m": None,
            "track_condition": "ME",
            "driver": "Driver One",
            "trainer": "First Trainer",
            "prize_won": 50000,
            "win_odds_final": 2.1,
            "withdrawn": False,
        },
    ],
    "100002": [
        {
            "race_date": "2026-04-01",
            "track": "SO",
            "race_id": 88050,
            "race_number": 7,
            "distance": 2140,
            "start_method": "A",
            "start_number": 1,
            "finish_position": None,
            "kilometer_time_seconds": None,
            "position_at_800m": None,
            "track_condition": None,    # puuttuu — normaalia
            "driver": "Driver Two",
            "trainer": "Second Trainer",
            "prize_won": 0,
            "win_odds_final": None,
            "withdrawn": True,
        },
    ],
}


def test_fetch_daily_races_collects_horse_starts(tmp_path):
    """Travsport-historiakeruu tallentaa horse_starts-rivit."""
    db = str(tmp_path / "test.db")
    migrate(db)
    ts = FakeTravsport(TRAVSPORT_STARTS)
    stats = fetch_daily_races(
        date(2026, 4, 27), db_path=db, atg=FakeATG(), travsport=ts
    )

    assert stats["horse_starts_inserted"] == 3  # 2 + 1
    assert stats.get("horse_starts_errors", 0) == 0
    # Molemmat hevoset haettu
    assert set(ts.calls) == {"100001", "100002"}

    with _session(db) as s:
        assert s.query(HorseStart).count() == 3
        h1_starts = (
            s.query(HorseStart)
            .filter_by(horse_id="100001")
            .order_by(HorseStart.race_date)
            .all()
        )
        assert len(h1_starts) == 2
        assert h1_starts[0].track == "AX"
        assert h1_starts[0].finish_position == 1
        assert h1_starts[0].kilometer_time_seconds == 78.2
        assert h1_starts[0].track_condition == "ME"
        assert h1_starts[1].track == "SO"
        assert h1_starts[1].finish_position == 2
        assert h1_starts[1].track_condition == "LE"

        withdrawn = s.query(HorseStart).filter_by(horse_id="100002").one()
        assert withdrawn.withdrawn is True
        assert withdrawn.finish_position is None
        assert withdrawn.track_condition is None  # puuttui mock-datasta → NULL


def test_fetch_daily_races_horse_starts_idempotent(tmp_path):
    """Toinen ajo ei lisää duplikaatteja horse_starts-tauluun."""
    db = str(tmp_path / "test.db")
    migrate(db)
    ts = FakeTravsport(TRAVSPORT_STARTS)
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG(), travsport=ts)
    stats2 = fetch_daily_races(
        date(2026, 4, 27), db_path=db, atg=FakeATG(), travsport=ts
    )

    assert stats2["horse_starts_inserted"] == 0
    with _session(db) as s:
        assert s.query(HorseStart).count() == 3  # ei kasvanut


def test_fetch_daily_races_works_without_travsport(tmp_path):
    """travsport=None → ATG-flow toimii normaalisti ilman horse_starts."""
    db = str(tmp_path / "test.db")
    migrate(db)
    stats = fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG())

    assert stats["races_processed"] == 1
    assert "horse_starts_inserted" not in stats
    with _session(db) as s:
        assert s.query(HorseStart).count() == 0


def test_fetch_daily_races_survives_travsport_failure(tmp_path):
    """Travsport-haku epäonnistuu yhdelle hevoselle → muut onnistuvat."""
    db = str(tmp_path / "test.db")
    migrate(db)

    class FailingTravsport:
        calls = []
        def get_results(self, horse_id, force_refresh=False):
            self.calls.append(str(horse_id))
            if str(horse_id) == "100001":
                raise ConnectionError("timeout")
            return TRAVSPORT_STARTS.get(str(horse_id), [])

    ts = FailingTravsport()
    stats = fetch_daily_races(
        date(2026, 4, 27), db_path=db, atg=FakeATG(), travsport=ts
    )

    # ATG-flow onnistui
    assert stats["races_processed"] == 1
    # horse_starts: 100001 failasi (0 starttia), 100002 onnistui (1 startti)
    assert stats["horse_starts_inserted"] == 1
    with _session(db) as s:
        assert s.query(HorseStart).count() == 1
        assert s.query(HorseStart).one().horse_id == "100002"


# ---------------------------------------------------------------------------
# backfill_track_condition
# ---------------------------------------------------------------------------


def test_backfill_track_condition_updates_null_rows(tmp_path):
    """backfill_track_condition lukee välimuistitiedostoja ja päivittää
    horse_starts.track_condition = NULL -rivit. Ei API-kutsuja."""
    import json
    from src.data.scheduler import backfill_track_condition

    # Rakenna DB ja lisää horse_starts ilman track_condition
    db = str(tmp_path / "test.db")
    migrate(db)
    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)
    with Session_() as session:
        session.add(Horse(horse_id="9001", name="TestHorse"))
        session.add(HorseStart(
            horse_id="9001",
            race_date="2026-01-15",
            track="SO",
            travsport_race_id=55001,
            track_condition=None,    # NULL — backfillin kohde
        ))
        session.commit()

    # Luo välimuistitiedosto jossa on trackCondition
    cache_dir = tmp_path / "travsport"
    cache_dir.mkdir()
    raw_start = {
        "raceInformation": {
            "date": "2026-01-15",
            "raceId": 55001,
            "raceNumber": 3,
        },
        "trackCode": "SO",
        "trackCondition": "LE",
        "distance": {"sortValue": 2140},
        "startMethod": "A",
        "startPosition": {"sortValue": 4},
        "placement": {"sortValue": 2},
        "kilometerTime": {"sortValue": 1193},
        "driver": {"name": "Arto Keski-Rautio"},
        "trainer": {"name": "Matti Ojala"},
        "prizeMoney": {"sortValue": 25000},
        "odds": {"sortValue": 45},
        "withdrawn": False,
    }
    (cache_dir / "9001_results.json").write_text(
        json.dumps([raw_start]), encoding="utf-8"
    )

    result = backfill_track_condition(db_path=db, cache_dir=cache_dir)

    assert result["updated"] == 1
    assert result["errors"] == 0

    with Session_() as session:
        hs = session.query(HorseStart).filter_by(horse_id="9001").one()
        assert hs.track_condition == "LE", (
            f"track_condition pitäisi olla 'LE', sai: {hs.track_condition!r}"
        )


def test_backfill_track_condition_skips_already_filled(tmp_path):
    """Rivi jolla on jo track_condition ei ylikirjoitu."""
    import json
    from src.data.scheduler import backfill_track_condition

    db = str(tmp_path / "test.db")
    migrate(db)
    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)
    with Session_() as session:
        session.add(Horse(horse_id="9002", name="AlreadyFilled"))
        session.add(HorseStart(
            horse_id="9002",
            race_date="2026-01-15",
            track="SO",
            travsport_race_id=55002,
            track_condition="TU",    # jo täynnä — ei saa ylikirjoittaa
        ))
        session.commit()

    cache_dir = tmp_path / "travsport"
    cache_dir.mkdir()
    raw_start = {
        "raceInformation": {"date": "2026-01-15", "raceId": 55002, "raceNumber": 1},
        "trackCode": "SO",
        "trackCondition": "LE",   # eri arvo kuin DB:ssä
        "distance": {"sortValue": 2140},
        "startMethod": "A",
        "startPosition": {"sortValue": 1},
        "placement": {"sortValue": 1},
        "kilometerTime": {"sortValue": 1180},
        "driver": {"name": "Driver"},
        "trainer": {"name": "Trainer"},
        "prizeMoney": {"sortValue": 0},
        "odds": {"sortValue": 30},
        "withdrawn": False,
    }
    (cache_dir / "9002_results.json").write_text(
        json.dumps([raw_start]), encoding="utf-8"
    )

    result = backfill_track_condition(db_path=db, cache_dir=cache_dir)

    assert result["updated"] == 0
    with Session_() as session:
        hs = session.query(HorseStart).filter_by(horse_id="9002").one()
        assert hs.track_condition == "TU"   # ei muuttunut


def test_backfill_track_condition_is_idempotent(tmp_path):
    """Kaksi peräkkäistä ajoa → sama tulos, ei duplikaatteja."""
    import json
    from src.data.scheduler import backfill_track_condition

    db = str(tmp_path / "test.db")
    migrate(db)
    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)
    with Session_() as session:
        session.add(Horse(horse_id="9003", name="IdempotentHorse"))
        session.add(HorseStart(
            horse_id="9003",
            race_date="2026-02-01",
            track="AX",
            travsport_race_id=55003,
            track_condition=None,
        ))
        session.commit()

    cache_dir = tmp_path / "travsport"
    cache_dir.mkdir()
    raw_start = {
        "raceInformation": {"date": "2026-02-01", "raceId": 55003, "raceNumber": 5},
        "trackCode": "AX",
        "trackCondition": "ME",
        "distance": {"sortValue": 2640},
        "startMethod": "V",
        "startPosition": {"sortValue": 2},
        "placement": {"sortValue": 3},
        "kilometerTime": {"sortValue": 1200},
        "driver": {"name": "Driver"},
        "trainer": {"name": "Trainer"},
        "prizeMoney": {"sortValue": 10000},
        "odds": {"sortValue": 60},
        "withdrawn": False,
    }
    (cache_dir / "9003_results.json").write_text(
        json.dumps([raw_start]), encoding="utf-8"
    )

    r1 = backfill_track_condition(db_path=db, cache_dir=cache_dir)
    r2 = backfill_track_condition(db_path=db, cache_dir=cache_dir)

    assert r1["updated"] == 1
    assert r2["updated"] == 0   # toinen ajo ei päivitä mitään


# ---------------------------------------------------------------------------
# retry_incomplete_results
# ---------------------------------------------------------------------------


def _build_partial_results_race(race_id: str, n_runners: int, n_with_results: int) -> dict:
    """Race jossa vain ensimmäiset n_with_results runneria saivat tuloksen.
    Loput runners ovat result.place=0 (ei maaliin) ilman kmTime-objektia."""
    starts = []
    for i in range(n_runners):
        s = {
            "id": i + 1,
            "number": i + 1,
            "postPosition": i + 1,
            "distance": 2140,
            "horse": {
                "id": 300000 + i,
                "name": f"P{i + 1}",
                "age": 5,
                "sex": "gelding",
                "trainer": {"firstName": "T", "lastName": str(i)},
                "statistics": {"life": {"starts": 0, "placement": {}, "records": []}, "years": {}},
            },
            "driver": {"firstName": "D", "lastName": str(i), "statistics": {"years": {}}},
        }
        if i < n_with_results:
            s["result"] = {
                "place": i + 1,
                "kmTime": {"minutes": 1, "seconds": 14, "tenths": 0},
                "finalOdds": 5.0 + i,
            }
        starts.append(s)
    return {
        "id": race_id,
        "date": "2026-04-27",
        "number": 1,
        "distance": 2140,
        "startMethod": "auto",
        "startTime": "2026-04-27T18:00:00",
        "track": {"name": "Solvalla"},
        "prize": 50000,
        "starts": starts,
    }


def test_retry_incomplete_results_picks_up_null_finish_position(tmp_path):
    """Race jossa alunperin vain top-3 sai tuloksen → retry hakee uudelleen
    jolloin ATG palauttaa kaikki paikat. NULL-rivien pitäisi täyttyä."""
    db = str(tmp_path / "test.db")
    migrate(db)

    # Ekassa fetch_resultsissa vain top-3 raporttiin (simuloi T+30min ATG-tilaa)
    partial = _build_partial_results_race(RACE_ID, n_runners=10, n_with_results=3)
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG(partial))
    fetch_results(RACE_ID, db_path=db, atg=FakeATG(partial))

    with _session(db) as s:
        n_with_finish = s.query(Runner).filter(
            Runner.race_id == RACE_ID, Runner.finish_position.isnot(None)
        ).count()
        assert n_with_finish == 3  # alkutila

    # Retry: ATG palauttaa nyt kaikki 10 paikkaa (simuloi T+useita tunteja).
    # lookback iso jotta testifixture-päivä (2026-04-27) on aina ikkunassa
    # vaikka kalenteriaika juoksee eteenpäin testin elinkaaren aikana.
    full = _build_partial_results_race(RACE_ID, n_runners=10, n_with_results=10)
    stats = retry_incomplete_results(db_path=db, lookback_days=3650, atg=FakeATG(full))

    assert stats["races_checked"] == 1  # vain RACE_ID oli vajaa
    assert stats["races_updated"] == 1
    assert stats["errors"] == []

    with _session(db) as s:
        n_with_finish = s.query(Runner).filter(
            Runner.race_id == RACE_ID, Runner.finish_position.isnot(None)
        ).count()
        assert n_with_finish == 10  # kaikki nyt täytetty


def test_retry_incomplete_results_skips_complete_races(tmp_path):
    """Race joka on jo täysin täytetty → ei nouse vajaiden listalle."""
    db = str(tmp_path / "test.db")
    migrate(db)
    full = _build_partial_results_race(RACE_ID, n_runners=5, n_with_results=5)
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG(full))
    fetch_results(RACE_ID, db_path=db, atg=FakeATG(full))

    # Retry: ei pitäisi löytää mitään korjattavaa
    stats = retry_incomplete_results(db_path=db, lookback_days=3650, atg=FakeATG(full))
    assert stats["races_checked"] == 0
    assert stats["races_updated"] == 0


def test_retry_incomplete_results_lookback_filter(tmp_path, monkeypatch):
    """Race vanhempi kuin lookback_days → ei haeta uudelleen."""
    db = str(tmp_path / "test.db")
    migrate(db)
    partial = _build_partial_results_race(RACE_ID, n_runners=5, n_with_results=2)
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG(partial))
    fetch_results(RACE_ID, db_path=db, atg=FakeATG(partial))

    # Lukitse "tämän päivän" date kauas tulevaisuuteen → race on >7pv vanha
    fixed = datetime(2026, 5, 20, 4, 30, tzinfo=scheduler_mod.ATG_TZ)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(scheduler_mod, "datetime", _DT)
    stats = retry_incomplete_results(db_path=db, lookback_days=7, atg=FakeATG(partial))
    assert stats["races_checked"] == 0  # filtteri sulki racen pois


def test_retry_incomplete_results_idempotent(tmp_path):
    """Toinen retry-ajo samalla täydellä datalla → ei rikkoonnu, ei
    duplikaatteja result-snapshotteihin (UNIQUE-indeksi suojaa)."""
    db = str(tmp_path / "test.db")
    migrate(db)
    partial = _build_partial_results_race(RACE_ID, n_runners=5, n_with_results=2)
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG(partial))
    fetch_results(RACE_ID, db_path=db, atg=FakeATG(partial))

    full = _build_partial_results_race(RACE_ID, n_runners=5, n_with_results=5)
    retry_incomplete_results(db_path=db, lookback_days=3650, atg=FakeATG(full))
    stats2 = retry_incomplete_results(db_path=db, lookback_days=3650, atg=FakeATG(full))

    # Toinen ajo: vajaita ei enää jää (kaikki finish_pos asetettu)
    assert stats2["races_checked"] == 0

    with _session(db) as s:
        # 5 runneria × 1 result-snapshot = 5 (ei duplikaatteja)
        assert s.query(OddsSnapshot).filter_by(snapshot_label="result").count() == 5


# ---------------------------------------------------------------------------
# TODO #2: shoes/sulky-piirteet
# ---------------------------------------------------------------------------


from src.data.scheduler import _shoes_sulky_fields


def test_shoes_sulky_fields_full_data():
    """Tyypillinen ATG-rakenne kaikilla kentillä (Yalla Yalla -tyylinen)."""
    horse = {
        "shoes": {
            "reported": True,
            "front": {"hasShoe": True, "changed": False},
            "back": {"hasShoe": False, "changed": True},
        },
        "sulky": {
            "reported": True,
            "type": {"code": "AM", "changed": True},
            "colour": {"code": "BL", "changed": False},
        },
    }
    f = _shoes_sulky_fields(horse)
    assert f["shoes_front"] is True
    assert f["shoes_back"] is False
    assert f["shoes_changed_front"] is False
    assert f["shoes_changed_back"] is True
    assert f["sulky_type"] == "AM"
    assert f["sulky_changed"] is True  # type.changed=True


def test_shoes_sulky_fields_missing_changed():
    """Macabre/Lady Gaagaa -tyylinen: front.changed ja back.changed puuttuvat."""
    horse = {
        "shoes": {
            "reported": True,
            "front": {"hasShoe": True},
            "back": {"hasShoe": True},
        },
        "sulky": {
            "reported": True,
            "type": {"code": "VA", "changed": False},
            "colour": {"code": "GU", "changed": False},
        },
    }
    f = _shoes_sulky_fields(horse)
    assert f["shoes_front"] is True
    assert f["shoes_back"] is True
    assert f["shoes_changed_front"] is None  # ATG ei kerro
    assert f["shoes_changed_back"] is None
    assert f["sulky_type"] == "VA"
    assert f["sulky_changed"] is False  # molemmat changed=False


def test_shoes_sulky_fields_not_reported():
    """reported=false → kaikki None (ei keksitä arvoja)."""
    horse = {
        "shoes": {"reported": False, "front": {"hasShoe": True}, "back": {"hasShoe": True}},
        "sulky": {"reported": False, "type": {"code": "VA"}, "colour": {"code": "GU"}},
    }
    f = _shoes_sulky_fields(horse)
    assert all(v is None for v in f.values())


def test_shoes_sulky_fields_completely_missing():
    """horse.shoes ja horse.sulky kokonaan puuttuvat → kaikki None."""
    f = _shoes_sulky_fields({})
    assert all(v is None for v in f.values())


def test_upsert_runner_writes_shoes_sulky(tmp_path):
    """End-to-end: fetch_daily_races kirjoittaa shoes/sulky-kentät DB:hen."""
    db = str(tmp_path / "test.db")
    migrate(db)
    race = copy.deepcopy(SAMPLE_RACE)
    # Lisää shoes/sulky #1 hevoselle (täysi data)
    race["starts"][0]["horse"]["shoes"] = {
        "reported": True,
        "front": {"hasShoe": True, "changed": False},
        "back": {"hasShoe": False, "changed": True},
    }
    race["starts"][0]["horse"]["sulky"] = {
        "reported": True,
        "type": {"code": "AM", "changed": True},
        "colour": {"code": "GU", "changed": False},
    }
    # #2 hevonen: shoes ei raportoitu
    race["starts"][1]["horse"]["shoes"] = {"reported": False}
    # sulky kokonaan puuttuu

    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG(race))

    with _session(db) as s:
        r1 = s.query(Runner).filter_by(start_number=1).one()
        assert r1.shoes_front is True
        assert r1.shoes_back is False
        assert r1.shoes_changed_back is True
        assert r1.sulky_type == "AM"
        assert r1.sulky_changed is True

        r2 = s.query(Runner).filter_by(start_number=2).one()
        assert r2.shoes_front is None
        assert r2.sulky_type is None


# ---------------------------------------------------------------------------
# Dynaaminen refresh-jobi (Ruotsin lukitusraja 15min ennen 1. lähtöä)
# ---------------------------------------------------------------------------


from src.data.scheduler import refresh_day_runners


def test_fetch_daily_races_returns_first_race_start_utc(tmp_path):
    """fetch_daily_races palauttaa stats['first_race_start_utc'] = aikaisin SE-lähtö."""
    db = str(tmp_path / "test.db")
    migrate(db)
    stats = fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG())
    # SAMPLE_RACE startTime = 2026-04-27T18:00:00 (Stockholm) = 16:00 UTC (CEST)
    assert stats["first_race_start_utc"] is not None
    assert stats["first_race_start_utc"].tzinfo is timezone.utc
    assert stats["first_race_start_utc"].hour == 16  # 18:00 CEST → 16:00 UTC


def test_setup_for_date_schedules_refresh_job(monkeypatch):
    """_setup_for_date kutsuu _schedule_first_race_refresh stats:n perusteella."""
    sched = MagicMock()
    captured: list = []

    def fake_fetch_daily_races(target, db_path, scheduler, travsport):
        # Palauta tunnettu first_race_start_utc tulevaisuuteen
        future = datetime.now(timezone.utc) + timedelta(hours=4)
        return {
            "races_processed": 5,
            "snapshot_jobs": 20,
            "result_jobs": 5,
            "errors": [],
            "first_race_start_utc": future,
        }

    monkeypatch.setattr(scheduler_mod, "fetch_daily_races", fake_fetch_daily_races)
    stats = scheduler_mod._setup_for_date(sched, date(2026, 4, 27), "x.db", "test")

    assert stats.get("refresh_jobs") == 1
    # Varmista että add_job kutsuttiin refresh_runners_-id:llä
    job_ids = [c.kwargs["id"] for c in sched.add_job.call_args_list]
    assert any("refresh_runners_" in jid for jid in job_ids)


def test_setup_for_date_skips_refresh_when_first_race_in_past(monkeypatch):
    """Jos 1. lähdön - 10min on jo mennyt (esim. iltapäivä-restartti), ei ajasteta."""
    sched = MagicMock()

    def fake_fetch_daily_races(target, db_path, scheduler, travsport):
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        return {
            "races_processed": 5, "snapshot_jobs": 0, "result_jobs": 0,
            "errors": [], "first_race_start_utc": past,
        }

    monkeypatch.setattr(scheduler_mod, "fetch_daily_races", fake_fetch_daily_races)
    stats = scheduler_mod._setup_for_date(sched, date(2026, 4, 27), "x.db", "test")

    assert stats.get("refresh_jobs") == 0
    # Yksi tarkistus: refresh_runners_ -jobia ei lisätty
    job_ids = [c.kwargs.get("id", "") for c in sched.add_job.call_args_list]
    assert not any("refresh_runners_" in jid for jid in job_ids)


# ---------------------------------------------------------------------------
# TODO #3: Gallop-suodatus
# ---------------------------------------------------------------------------


def test_get_calendar_day_filters_gallop_tracks():
    """ATGClient.get_calendar_day suodattaa gallop-radat (sport != 'trot') pois.

    Käytetään monkeypatchia _get-metodiin jotta HTTP-pyyntöä ei tehdä.
    SE-radat joiden sport != 'trot' (Bro Park, Jägersro Galopp) eivät
    päädy paluuarvon 'tracks'-listaan.
    """
    from src.data.atg_client import ATGClient

    raw_calendar = {
        "tracks": [
            {"name": "Solvalla", "countryCode": "SE", "sport": "trot", "races": []},
            {"name": "Bro Park", "countryCode": "SE", "sport": "gallop", "races": []},
            {"name": "Jägersro Galopp", "countryCode": "SE", "sport": "gallop", "races": []},
            {"name": "Vincennes", "countryCode": "FR", "sport": "trot", "races": []},
        ]
    }
    client = ATGClient()
    client._get = lambda path: raw_calendar  # type: ignore[method-assign]

    result = client.get_calendar_day("2026-05-10", swedish_only=True)
    client.close()

    track_names = [t["name"] for t in result["tracks"]]
    assert track_names == ["Solvalla"]
    assert "Bro Park" not in track_names
    assert "Jägersro Galopp" not in track_names
    assert "Vincennes" not in track_names


def test_retry_incomplete_results_skips_gallop_tracks(tmp_path):
    """retry_incomplete_results ei yritä hakea gallop-ratojen tuloksia.

    Bro Park, Göteborg Galopp ja Jägersro Galopp eivät koskaan saa
    kmTime-objekteja ATG:sta → ne olisivat aina vajaita → turhia
    API-kutsuja joka päivä. GALLOP_TRACKS NOT IN -filtteri poistaa ne.
    Testataan Bro Parkillla; logiikka on sama kaikille GALLOP_TRACKS-radoille.
    """
    db = str(tmp_path / "test.db")
    migrate(db)

    # Rakenna gallop-race jossa kaikki runnerit ovat vajaita (0 tulosta)
    gallop_race = _build_partial_results_race(
        "2026-04-27_gallop_1", n_runners=5, n_with_results=0
    )
    gallop_race["track"] = {"name": "Bro Park"}  # gallop-rata (GALLOP_TRACKS)

    # Tallenna race DB:hen suoraan fetch_results:llä (calendar-filtteri
    # ei ole käytössä tässä kutsussa - simuloi jo olemassa olevaa dataa)
    fetch_results("2026-04-27_gallop_1", db_path=db, atg=FakeATG(gallop_race))

    # Varmista että race on DB:ssä vajaana
    with _session(db) as s:
        n_null = (
            s.query(Runner)
            .filter(Runner.race_id == "2026-04-27_gallop_1")
            .filter(Runner.kilometer_time_seconds.is_(None))
            .count()
        )
        assert n_null == 5  # kaikki runnerit vajaita - alkutila oikein

    # retry_incomplete_results ei saa hakea gallop-rataa uudelleen
    retry_atg = FakeATG(gallop_race)
    stats = retry_incomplete_results(
        db_path=db, lookback_days=3650, atg=retry_atg
    )

    assert stats["races_checked"] == 0   # Bro Park ohitettu GALLOP_TRACKS-filtterillä
    assert retry_atg.calls["race"] == 0  # ATG-kutsuja ei tehty


def test_refresh_day_runners_calls_fetch_daily_without_scheduling(monkeypatch):
    """refresh_day_runners EI saa kutsua scheduleria/travsportia - vain runner-päivitys."""
    captured = {}

    def fake_fetch_daily_races(target, db_path, atg=None, scheduler=None, travsport=None):
        captured["target"] = target
        captured["scheduler"] = scheduler
        captured["travsport"] = travsport
        return {"races_processed": 5}

    monkeypatch.setattr(scheduler_mod, "fetch_daily_races", fake_fetch_daily_races)
    refresh_day_runners(date(2026, 4, 27), db_path="x.db")

    assert captured["target"] == date(2026, 4, 27)
    assert captured["scheduler"] is None  # EI uudelleen-ajasteta snapshotteja
    assert captured["travsport"] is None  # EI uudelleen-haeta horse_starts


# ---------------------------------------------------------------------------
# _parse_terms
# ---------------------------------------------------------------------------


def test_parse_terms_none_input():
    """None tai tyhjä lista → kaikki kentät None."""
    r = _parse_terms(None)
    assert r == {
        "race_terms": None,
        "race_min_earnings": None,
        "race_max_earnings": None,
        "race_age_group": None,
    }
    assert _parse_terms([]) == r


def test_parse_terms_range_swedish_thousands():
    """'X - Y kr' ruotsalaisella tuhaterottelupisteel → min=X, max=Y."""
    terms = ["För 3-åriga och äldre, 1.500 - 85.000 kr.", "Bonus", "2140m auto"]
    r = _parse_terms(terms)
    assert r["race_min_earnings"] == 1500
    assert r["race_max_earnings"] == 85000
    assert r["race_age_group"] == "3yo+"
    assert r["race_terms"] == terms[0]


def test_parse_terms_hogst_maiden():
    """'högst X kr' → min=0, max=X (alkeislähtö / maiden)."""
    terms = ["För 2-åriga, högst 30.000 kr."]
    r = _parse_terms(terms)
    assert r["race_min_earnings"] == 0
    assert r["race_max_earnings"] == 30000
    assert r["race_age_group"] == "2yo"


def test_parse_terms_lagst_top_class():
    """'lägst X kr' → min=X, max=None (korkein luokka, avoin ylöspäin)."""
    terms = ["För 5-åriga och äldre, lägst 500.000 kr."]
    r = _parse_terms(terms)
    assert r["race_min_earnings"] == 500000
    assert r["race_max_earnings"] is None
    assert r["race_age_group"] == "5yo+"


def test_parse_terms_4yo_plus():
    """4-åriga och äldre → '4yo+'."""
    terms = ["För 4-åriga och äldre, 50.000 - 200.000 kr."]
    r = _parse_terms(terms)
    assert r["race_age_group"] == "4yo+"
    assert r["race_min_earnings"] == 50000
    assert r["race_max_earnings"] == 200000


def test_parse_terms_3yo_exact():
    """3-åriga (ilman 'och äldre') → '3yo'."""
    terms = ["För 3-åriga, 10.000 - 60.000 kr."]
    r = _parse_terms(terms)
    assert r["race_age_group"] == "3yo"


def test_parse_terms_no_earnings_criteria():
    """Finaalit / sponsorilähdöt — ei ansaintaehtoja → min/max None."""
    terms = ["Grand Prix Final, 5-åriga och äldre, kallblod."]
    r = _parse_terms(terms)
    assert r["race_min_earnings"] is None
    assert r["race_max_earnings"] is None
    assert r["race_age_group"] == "5yo+"
    assert r["race_terms"] is not None


def test_parse_terms_no_space_in_amount():
    """Yhteen kirjoitettu luku ('10000' ilman tuhaterottelua) toimii myös."""
    terms = ["För 4-åriga och äldre, 10000 - 80000 kr."]
    r = _parse_terms(terms)
    assert r["race_min_earnings"] == 10000
    assert r["race_max_earnings"] == 80000


# ---------------------------------------------------------------------------
# backfill_race_class
# ---------------------------------------------------------------------------


def _make_atg_race(race_id: str, terms: list | None = None, condition: str | None = None) -> dict:
    """Luo minimaalinen ATG-race-dict backfill_race_class-testejä varten."""
    return {
        "id": race_id,
        "date": "2026-04-27",
        "number": 1,
        "distance": 2140,
        "startMethod": "auto",
        "startTime": "2026-04-27T18:00:00",
        "track": {"name": "Solvalla", "condition": condition},
        "prize": 50000,
        "terms": terms or ["För 3-åriga och äldre, 1.500 - 85.000 kr.", "", ""],
        "starts": [],
    }


def test_backfill_race_class_populates_fields(tmp_path):
    """backfill_race_class täyttää uudet kentät kaikille races-riveille."""
    db = str(tmp_path / "test.db")
    migrate(db)

    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)

    # Lisää race ilman termejä
    with Session_() as s:
        race = Race(
            race_id="2026-04-27_99_1",
            race_date=date(2026, 4, 27),
            track="Solvalla",
        )
        s.add(race)
        s.commit()

    # Mock ATGClient
    mock_atg = MagicMock()
    mock_atg.get_race.return_value = _make_atg_race(
        "2026-04-27_99_1",
        terms=["För 3-åriga och äldre, 1.500 - 85.000 kr.", "", ""],
        condition="light",
    )

    result = backfill_race_class(db_path=db, atg=mock_atg)

    assert result["updated"] == 1
    assert result["errors"] == 0
    assert result["total_races"] == 1

    with Session_() as s:
        race = s.get(Race, "2026-04-27_99_1")
        assert race.race_min_earnings == 1500
        assert race.race_max_earnings == 85000
        assert race.race_age_group == "3yo+"
        assert race.track_condition == "light"
        assert race.race_terms is not None


def test_backfill_race_class_handles_api_errors_gracefully(tmp_path):
    """Yksittäinen virhe ei keskeytä koko backfilliä."""
    db = str(tmp_path / "test.db")
    migrate(db)

    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)

    with Session_() as s:
        for i in range(1, 4):
            s.add(Race(race_id=f"2026-04-27_99_{i}", race_date=date(2026, 4, 27), track="Solvalla"))
        s.commit()

    mock_atg = MagicMock()

    def fake_get_race(race_id):
        if race_id == "2026-04-27_99_2":
            raise RuntimeError("API virhe")
        return _make_atg_race(race_id)

    mock_atg.get_race.side_effect = fake_get_race

    result = backfill_race_class(db_path=db, atg=mock_atg)

    assert result["total_races"] == 3
    assert result["updated"] == 2   # 2 onnistui
    assert result["errors"] == 1    # 1 epäonnistui


def test_backfill_race_class_is_idempotent(tmp_path):
    """Sama ajo kahdesti ei aiheuta ongelmia (idempotentti)."""
    db = str(tmp_path / "test.db")
    migrate(db)

    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)

    with Session_() as s:
        s.add(Race(race_id="2026-04-27_99_1", race_date=date(2026, 4, 27), track="Solvalla"))
        s.commit()

    mock_atg = MagicMock()
    mock_atg.get_race.return_value = _make_atg_race("2026-04-27_99_1", condition="heavy")

    r1 = backfill_race_class(db_path=db, atg=mock_atg)
    r2 = backfill_race_class(db_path=db, atg=mock_atg)

    assert r1["errors"] == 0
    assert r2["errors"] == 0
    # Toisella ajolla arvo on sama — ei datakonfliktivirheitä
    with Session_() as s:
        race = s.get(Race, "2026-04-27_99_1")
        assert race.track_condition == "heavy"


# ---------------------------------------------------------------------------
# K1-korjaus: _ensure_runner_exists + fetch_results ei ylikirjoita atg_*
# ---------------------------------------------------------------------------


def test_fetch_results_does_not_overwrite_atg_aggregates(tmp_path):
    """K1-regressiotesti: fetch_results EI saa ylikirjoittaa atg_*-aggregaatteja.

    Pre-race-haussa runner saa atg_lifetime_starts=10.
    Post-race-haussa ATG palauttaa starts=11 (päivitetty).
    Odotus: DB:ssä pysyy 10 — pre-race-arvo säilyy.
    """
    db = str(tmp_path / "test.db")
    migrate(db)

    # Vaihe 1: pre-race-haku (starts=10)
    pre_race = copy.deepcopy(SAMPLE_RACE)
    pre_race["starts"][0]["horse"]["statistics"]["life"]["starts"] = 10
    pre_race["starts"][0]["horse"]["statistics"]["life"]["placement"] = {
        "1": 2, "2": 1, "3": 1
    }
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG(pre_race))

    with _session(db) as s:
        runner = s.query(Runner).filter_by(start_number=1).one()
        assert runner.atg_lifetime_starts == 10  # saniteettitarkistus

    # Vaihe 2: post-race-haku (ATG päivitti starts=11, voitto lisätty)
    post_race = copy.deepcopy(pre_race)
    post_race["starts"][0]["horse"]["statistics"]["life"]["starts"] = 11
    post_race["starts"][0]["horse"]["statistics"]["life"]["placement"] = {
        "1": 3, "2": 1, "3": 1
    }
    post_race["starts"][0]["result"] = {
        "place": 1,
        "kmTime": {"minutes": 1, "seconds": 13, "tenths": 8},
        "finalOdds": 3.20,
    }
    fetch_results(RACE_ID, db_path=db, atg=FakeATG(post_race))

    with _session(db) as s:
        runner = s.query(Runner).filter_by(start_number=1).one()
        # atg_lifetime_starts EI saa kasvaa — pre-race-arvo säilyy
        assert runner.atg_lifetime_starts == 10, (
            f"K1-vuoto: atg_lifetime_starts muuttui 10 → {runner.atg_lifetime_starts}"
        )
        # Tulos kuitenkin päivittyy normaalisti
        assert runner.finish_position == 1
        assert runner.kilometer_time_seconds == 73.8


def test_fetch_results_cold_start_writes_atg_aggregates(tmp_path):
    """Cold-start: runner ei ole olemassa ennen fetch_results-kutsua.
    Tässä tapauksessa _ensure_runner_exists() SAA kirjoittaa atg_*-arvot
    (koska ei ole pre-race-arvoja ylikirjoitettavaksi).
    """
    db = str(tmp_path / "test.db")
    migrate(db)

    # Ei ennakkohakua — suoraan fetch_results cold-startina
    race_with_results = copy.deepcopy(SAMPLE_RACE)
    race_with_results["starts"][0]["horse"]["statistics"]["life"]["starts"] = 42
    race_with_results["starts"][0]["result"] = {
        "place": 2,
        "kmTime": {"minutes": 1, "seconds": 14, "tenths": 5},
        "finalOdds": 8.50,
    }

    fetch_results(RACE_ID, db_path=db, atg=FakeATG(race_with_results))

    with _session(db) as s:
        runner = s.query(Runner).filter_by(start_number=1).one()
        # Cold-start: atg_* kirjoitetaan koska riviä ei ollut olemassa
        assert runner.atg_lifetime_starts == 42
        assert runner.finish_position == 2


def test_set_if_not_none_does_not_overwrite_with_none():
    """_set_if_not_none: olemassa oleva arvo ei muutu jos uusi arvo on None."""
    class FakeObj:
        field = "alkuperäinen"

    obj = FakeObj()
    _set_if_not_none(obj, "field", None)
    assert obj.field == "alkuperäinen"


def test_set_if_not_none_writes_non_none_value():
    """_set_if_not_none kirjoittaa arvon kun se ei ole None."""
    class FakeObj:
        field = None

    obj = FakeObj()
    _set_if_not_none(obj, "field", "uusi_arvo")
    assert obj.field == "uusi_arvo"


def test_upsert_race_does_not_overwrite_existing_fields_with_none(tmp_path):
    """M1-suoja: _upsert_race ei ylikirjoita olemassa olevaa arvoa None:lla.

    Esimerkki: purse_sek asetetaan pre-race-haussa. Fetch_results-haussa
    race.prize puuttuu (None). Odotus: purse_sek säilyy DB:ssä.
    """
    db = str(tmp_path / "test.db")
    migrate(db)

    # Pre-race: race kaikilla kentillä
    fetch_daily_races(date(2026, 4, 27), db_path=db, atg=FakeATG())

    with _session(db) as s:
        race = s.get(Race, RACE_ID)
        assert race.purse_sek == 50000  # saniteettitarkistus

    # Post-race: race jolla prize=None (ATG ei aina palauta tätä uudelleen)
    race_no_prize = copy.deepcopy(SAMPLE_RACE)
    race_no_prize["prize"] = None
    race_no_prize["track"]["condition"] = None  # myös track_condition puuttuu

    fetch_results(RACE_ID, db_path=db, atg=FakeATG(race_no_prize))

    with _session(db) as s:
        race = s.get(Race, RACE_ID)
        assert race.purse_sek == 50000, (
            f"M1-vuoto: purse_sek ylikirjoitettiin None:lla "
            f"(sai {race.purse_sek!r})"
        )


# ---------------------------------------------------------------------------
# backfill_correct_atg_aggregates
# ---------------------------------------------------------------------------


def test_backfill_correct_atg_aggregates_fixes_starts_and_rates(tmp_path):
    """backfill_correct_atg_aggregates korjaa atg_lifetime_starts -= 1
    ja laskee win/top3-raten oikein pre-race-tilaan."""
    db = str(tmp_path / "test.db")
    migrate(db)

    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)
    with Session_() as s:
        s.add(Horse(horse_id="77001", name="Backfill Horse"))
        # Hevosella: stored starts=51 (K1-vuoto, pitäisi olla 50)
        # stored win_rate = 10/51 (voitti tämän lähdön → is_win=1)
        # stored top3_rate = 20/51
        stored_starts = 51
        stored_wr = 10 / 51   # 10 voittoa 51 startista (post-race)
        stored_t3r = 20 / 51  # 20 top3:a 51 startista
        s.add(Runner(
            runner_id=f"{RACE_ID}_99",
            race_id=RACE_ID,
            horse_id="77001",
            start_number=99,
            atg_lifetime_starts=stored_starts,
            atg_lifetime_win_rate=stored_wr,
            atg_lifetime_top3_rate=stored_t3r,
            finish_position=1,  # voitti
        ))
        s.commit()

    result = backfill_correct_atg_aggregates(db_path=db)

    assert result["updated"] == 1
    assert result["skipped_zero_starts"] == 0
    assert result["errors"] == 0

    with Session_() as s:
        runner = s.get(Runner, f"{RACE_ID}_99")
        # starts: 51 → 50
        assert runner.atg_lifetime_starts == 50
        # win_rate: (10 - 1) / 50 = 9/50 = 0.18
        assert abs(runner.atg_lifetime_win_rate - 9 / 50) < 0.005
        # top3_rate: (20 - 1) / 50 = 19/50 = 0.38
        assert abs(runner.atg_lifetime_top3_rate - 19 / 50) < 0.005


def test_backfill_correct_atg_aggregates_non_winner(tmp_path):
    """Hevonen ei voittanut: wins ei vähennetä is_win=0 koska ei voittoa."""
    db = str(tmp_path / "test.db")
    migrate(db)

    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)
    with Session_() as s:
        s.add(Horse(horse_id="77002", name="Non Winner"))
        stored_starts = 21
        stored_wr = 5 / 21    # 5 voittoa, hevonen tuli 3. tässä lähdössä
        stored_t3r = 10 / 21
        s.add(Runner(
            runner_id=f"{RACE_ID}_98",
            race_id=RACE_ID,
            horse_id="77002",
            start_number=98,
            atg_lifetime_starts=stored_starts,
            atg_lifetime_win_rate=stored_wr,
            atg_lifetime_top3_rate=stored_t3r,
            finish_position=3,  # ei voittanut, oli top3
        ))
        s.commit()

    backfill_correct_atg_aggregates(db_path=db)

    with Session_() as s:
        runner = s.get(Runner, f"{RACE_ID}_98")
        assert runner.atg_lifetime_starts == 20
        # win_rate: 5/20 = 0.25 (voittoja ei vähennetä — ei voittanut)
        assert abs(runner.atg_lifetime_win_rate - 5 / 20) < 0.005
        # top3_rate: (10 - 1) / 20 = 0.45 (oli top3, vähennetään 1)
        assert abs(runner.atg_lifetime_top3_rate - 9 / 20) < 0.005


def test_backfill_correct_atg_aggregates_skips_zero_starts(tmp_path):
    """Hevosella stored_starts=1: korjaus → 0 alkustart, rate=NULL."""
    db = str(tmp_path / "test.db")
    migrate(db)

    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)
    with Session_() as s:
        s.add(Horse(horse_id="77003", name="Debut Horse"))
        s.add(Runner(
            runner_id=f"{RACE_ID}_97",
            race_id=RACE_ID,
            horse_id="77003",
            start_number=97,
            atg_lifetime_starts=1,   # debyytti: stored=1 → pre=0
            atg_lifetime_win_rate=1.0,
            atg_lifetime_top3_rate=1.0,
            finish_position=1,
        ))
        s.commit()

    result = backfill_correct_atg_aggregates(db_path=db)
    assert result["skipped_zero_starts"] == 1
    assert result["updated"] == 1  # päivitetään starts=0, rate=NULL

    with Session_() as s:
        runner = s.get(Runner, f"{RACE_ID}_97")
        assert runner.atg_lifetime_starts == 0
        assert runner.atg_lifetime_win_rate is None
        assert runner.atg_lifetime_top3_rate is None


def test_backfill_correct_atg_aggregates_skips_null_starts(tmp_path):
    """Runner jolla atg_lifetime_starts IS NULL ohitetaan kokonaan.

    SQL-filtteri (WHERE atg_lifetime_starts IS NOT NULL) poistaa nämä
    rivit jo ennen Python-looppia — ne eivät näy missään laskurissa.
    """
    db = str(tmp_path / "test.db")
    migrate(db)

    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)
    with Session_() as s:
        s.add(Horse(horse_id="77004", name="No Stats"))
        s.add(Runner(
            runner_id=f"{RACE_ID}_96",
            race_id=RACE_ID,
            horse_id="77004",
            start_number=96,
            atg_lifetime_starts=None,  # tieto puuttui ATG:lta
        ))
        s.commit()

    result = backfill_correct_atg_aggregates(db_path=db)
    # NULL-rivit suodatetaan SQL:ssä — ei updated, ei skipped
    assert result["updated"] == 0
    assert result["skipped_zero_starts"] == 0
    assert result["errors"] == 0


# ---------------------------------------------------------------------------
# A4 — _upsert_runner M1-symmetria: _set_if_not_none
# ---------------------------------------------------------------------------


def test_upsert_runner_does_not_overwrite_existing_fields_with_none(tmp_path):
    """A4-M1-symmetria: _upsert_runner ei ylikirjoita olemassa olevia ATG-arvoja
    Nonella kun ATG:n vastaus on tilapäisesti vajaa.

    Skenaario: refresh_day_runners kutsuu _upsert_runner kahdesti.
    1. kutsu: täysi ATG-data → arvot tallennetaan.
    2. kutsu: ATG:n vastaus puuttuu statistics → None-arvot.
    Odotus: alkuperäiset arvot säilyvät (ei ylikirjoitusta).
    """
    db = str(tmp_path / "test.db")
    migrate(db)

    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)

    # Minimalinen race-dict jota _upsert_runner vaatii
    race_dict = {
        "id": RACE_ID,
        "date": "2026-04-27",
        "distance": 2140,
        "startMethod": "auto",
    }

    # Start-dict 1. kutsulla: täysi data
    start_full = {
        "number": 77,
        "distance": 2140,
        "horse": {
            "id": 200001,
            "name": "M1 Test Horse",
            "trainer": {"firstName": "Test", "lastName": "Trainer"},
            "statistics": {
                "life": {
                    "starts": 42,
                    "earnings": 500000,
                    "placement": {"1": 8, "2": 12, "3": 6},
                    "records": [],
                },
                "years": {"2026": {"starts": 6, "placement": {"1": 2}}},
            },
            "shoes": {
                "reported": True,
                "front": {"hasShoe": True, "changed": False},
                "back": {"hasShoe": False, "changed": True},
            },
            "sulky": {
                "reported": True,
                "type": {"code": "AM", "changed": False},
                "colour": {"code": "BL", "changed": False},
            },
        },
        "driver": {
            "id": 999001,
            "firstName": "Test",
            "lastName": "Driver",
            "statistics": {
                "years": {
                    "2026": {
                        "starts": 88,
                        "placement": {"1": 15},
                        "winPercentage": 1705,
                        "earnings": 1200000,
                    }
                }
            },
        },
    }

    # Start-dict 2. kutsulla: statistics puuttuu (ATG vajaa vastaus)
    start_no_stats = copy.deepcopy(start_full)
    start_no_stats["horse"]["statistics"] = None
    start_no_stats["driver"]["statistics"] = {"years": {}}

    with Session_() as s:
        s.add(Horse(horse_id="200001", name="M1 Test Horse"))
        s.commit()

    # 1. kutsu: luo runner täydellä datalla
    with Session_() as s:
        _upsert_runner(s, race_dict, start_full)
        s.commit()

    # Tarkista alkuperäiset arvot
    with Session_() as s:
        runner = s.get(Runner, f"{RACE_ID}_77")
        assert runner is not None
        original_starts = runner.atg_lifetime_starts
        assert original_starts == 42, f"Alkuperäinen starts = {original_starts}, odotettiin 42"

    # 2. kutsu: päivitä ilman statistiikkaa (vajaa ATG-vastaus)
    with Session_() as s:
        _upsert_runner(s, race_dict, start_no_stats)
        s.commit()

    # Arvot pitää säilyä — ei ylikirjoitusta Nonella
    with Session_() as s:
        runner = s.get(Runner, f"{RACE_ID}_77")
        assert runner.atg_lifetime_starts == 42, (
            f"A4-M1-vuoto: atg_lifetime_starts ylikirjoitettiin Nonella "
            f"(sai {runner.atg_lifetime_starts!r}). "
            "_upsert_runner pitää käyttää _set_if_not_none()."
        )
        # Kengät: alkuperäiset arvot säilyvät
        assert runner.shoes_front is True, (
            f"shoes_front ylikirjoitettiin: {runner.shoes_front!r}"
        )


def test_upsert_runner_writes_new_values_when_field_was_none(tmp_path):
    """_upsert_runner kirjoittaa uuden arvon kun kenttä oli aiemmin None.

    Varmistaa että _set_if_not_none ei estä ENSIMMÄISTÄ kirjoitusta
    (None → arvo on sallittu, vain arvo → None ei ole).
    """
    db = str(tmp_path / "test.db")
    migrate(db)

    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)

    race_dict = {
        "id": RACE_ID,
        "date": "2026-04-27",
        "distance": 2140,
        "startMethod": "auto",
    }

    # 1. kutsu: vajaa data — statistics puuttuu
    start_empty = {
        "number": 78,
        "distance": 2140,
        "horse": {
            "id": 200002,
            "name": "Empty Horse",
            "trainer": None,
            "statistics": None,
        },
        "driver": None,
    }

    # 2. kutsu: täysi data
    start_full = copy.deepcopy(SAMPLE_RACE["starts"][0])
    start_full["number"] = 78
    start_full["horse"]["id"] = 200002

    with Session_() as s:
        s.add(Horse(horse_id="200002", name="Empty Horse"))
        s.commit()

    with Session_() as s:
        _upsert_runner(s, race_dict, start_empty)
        s.commit()

    with Session_() as s:
        runner = s.get(Runner, f"{RACE_ID}_78")
        assert runner.atg_lifetime_starts is None  # oli None

    with Session_() as s:
        _upsert_runner(s, race_dict, start_full)
        s.commit()

    with Session_() as s:
        runner = s.get(Runner, f"{RACE_ID}_78")
        # None → arvo: pitää päivittyä
        assert runner.atg_lifetime_starts == 50, (
            f"atg_lifetime_starts = {runner.atg_lifetime_starts!r}, odotettiin 50. "
            "_set_if_not_none estää None→arvo-päivityksen (väärä toiminta)."
        )


# B2-jälkityö — dam_sire grandfather-avainkorjaus

from src.data.scheduler import _upsert_horse


def test_upsert_horse_reads_dam_sire_from_grandfather(tmp_path):
    """_upsert_horse lukee dam_sire pedigree.grandfather.name:sta (ei mothersFather).

    ATG API käyttää avainta 'grandfather' emänisälle — tämä on oikea avain.
    """
    db = str(tmp_path / "test.db")
    migrate(db)
    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)

    horse_dict = {
        "id": 999001,
        "name": "Test Horse",
        "age": 5,
        "sex": "gelding",
        "pedigree": {
            "father": {"id": 111, "name": "Great Sire"},
            "mother": {"id": 222, "name": "Great Dam"},
            "grandfather": {"id": 333, "name": "Grandfather Sire"},
        },
    }

    with Session_() as s:
        _upsert_horse(s, horse_dict)
        s.commit()

    with Session_() as s:
        horse = s.get(Horse, "999001")
        assert horse is not None
        assert horse.sire == "Great Sire"
        assert horse.dam == "Great Dam"
        assert horse.dam_sire == "Grandfather Sire", (
            f"dam_sire = {horse.dam_sire!r}, odotettiin 'Grandfather Sire'. "
            "_upsert_horse lukee väärää ATG-kenttää."
        )


def test_upsert_horse_dam_sire_none_when_no_grandfather(tmp_path):
    """_upsert_horse asettaa dam_sire = None kun grandfather puuttuu pedigreestä."""
    db = str(tmp_path / "test.db")
    migrate(db)
    engine = create_engine(f"sqlite:///{db}")
    Session_ = sessionmaker(bind=engine)

    horse_dict = {
        "id": 999002,
        "name": "No Grandfather Horse",
        "age": 4,
        "sex": "mare",
        "pedigree": {
            "father": {"id": 111, "name": "Some Sire"},
            "mother": {"id": 222, "name": "Some Dam"},
            # grandfather puuttuu — dam_sire pitää olla None
        },
    }

    with Session_() as s:
        _upsert_horse(s, horse_dict)
        s.commit()

    with Session_() as s:
        horse = s.get(Horse, "999002")
        assert horse.dam_sire is None
