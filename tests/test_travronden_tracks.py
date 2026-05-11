"""Testit travronden_tracks.py:lle.

Kaikki testit offline — ei oikeita API-kutsuja. TravrondenTracksClient
mockataan tai testifunktiot kutsutaan suoraan välimuistikansion kautta.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.data.scrapers.travronden_tracks import (
    _parse_capacity,
    _to_bool,
    _to_int,
    fetch_all_se_tracks,
    upsert_tracks,
)
from src.data.schema import migrate


# ---------------------------------------------------------------------------
# Testifixtuuri: Färjestad-tyyppinen tracks-objekti (kopioi live-vastauksesta)
# ---------------------------------------------------------------------------

FARJESTAD_TRACK = {
    "id": "F",
    "atg_id": 15,
    "st_id": 7,
    "name": "Färjestad",
    "country": "SE",
    "name_alt": "",
    "length_total": 1000,
    "length_home_stretch": 177,
    "width_1": 2040,
    "width_2": 2110,
    "dosage": 1700,
    "open_stretch": False,
    "angled_wing": False,
    "slug": "farjestad",
    "track_description": "Färjestad är den största av Värmlands tre travbanor.",
    "track_analysis": "På Färjestad är det ett stort minus med innerspår bakom bilen.",
    "built": "1936",
    "capacity": "10000",
    "homepage": "https://www.ftrav.se",
}

SOLVALLA_TRACK = {
    "id": "So",
    "atg_id": 5,
    "name": "Solvalla",
    "country": "SE",
    "length_total": 1000,
    "length_home_stretch": 220,
    "width_1": 1000,
    "width_2": 1000,
    "dosage": 1500,
    "open_stretch": True,
    "angled_wing": True,
    "slug": "solvalla",
    "track_description": "Solvalla i Stockholm.",
    "track_analysis": "Solvalla är en av Nordens modernaste travbanor.",
    "built": "1925",
    "capacity": "15000",
    "homepage": "https://www.solvalla.se",
}

NORWEGIAN_TRACK = {
    "id": "Je",
    "atg_id": 99,
    "name": "Jarlsberg",
    "country": "NO",   # ← pitää suodattua pois
    "length_total": 1050,
    "length_home_stretch": 200,
    "open_stretch": False,
    "angled_wing": False,
    "slug": "jarlsberg",
}


def _make_statistics_response(track: dict) -> dict:
    """Rakenna minimaalinen /statistics/-vastaus yhdellä radalla."""
    return {"round": {"tracks": [track]}}


# ---------------------------------------------------------------------------
# Yksikkötestit: apufunktiot
# ---------------------------------------------------------------------------

class TestParseCapacity:
    def test_int_passthrough(self):
        assert _parse_capacity(10000) == 10000

    def test_string_no_spaces(self):
        assert _parse_capacity("10000") == 10000

    def test_string_with_space(self):
        assert _parse_capacity("10 000") == 10000

    def test_string_with_narrow_space(self):
        # kapea välilyönti (U+202F) — ei pitäisi kaataa
        assert _parse_capacity("10 000") == 10000

    def test_none_returns_none(self):
        assert _parse_capacity(None) is None

    def test_garbage_returns_none(self):
        assert _parse_capacity("ei numero") is None


class TestToInt:
    def test_int(self):
        assert _to_int(177) == 177

    def test_float(self):
        assert _to_int(177.9) == 177

    def test_string_int(self):
        assert _to_int("177") == 177

    def test_none(self):
        assert _to_int(None) is None

    def test_garbage(self):
        assert _to_int("abc") is None


class TestToBool:
    def test_true(self):
        assert _to_bool(True) is True

    def test_false(self):
        assert _to_bool(False) is False

    def test_none(self):
        assert _to_bool(None) is None

    def test_int_truthy(self):
        assert _to_bool(1) is True

    def test_int_falsy(self):
        assert _to_bool(0) is False


# ---------------------------------------------------------------------------
# Testit: fetch_all_se_tracks (mockattu HTTP)
# ---------------------------------------------------------------------------

class TestFetchAllSeTracks:
    """fetch_all_se_tracks kutsuu TravrondenTracksClient.get_statistics().
    Mockataan client niin ettei oikeita HTTP-kutsuja tehdä."""

    def _mock_client(self, responses: dict[int, dict | None]):
        """Palauta mock-client joka antaa responses[round_id] tai None."""
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get_statistics.side_effect = lambda rid: responses.get(rid)
        return client

    def test_finds_se_tracks(self, tmp_path):
        responses = {
            100: _make_statistics_response(FARJESTAD_TRACK),
            99: _make_statistics_response(SOLVALLA_TRACK),
            98: None,
        }
        with patch(
            "src.data.scrapers.travronden_tracks.TravrondenTracksClient",
            return_value=self._mock_client(responses),
        ):
            result = fetch_all_se_tracks(
                scan_from=100, scan_limit=5, cache_dir=tmp_path
            )
        assert "Färjestad" in result
        assert "Solvalla" in result

    def test_filters_non_se(self, tmp_path):
        responses = {
            100: _make_statistics_response(FARJESTAD_TRACK),
            99: _make_statistics_response(NORWEGIAN_TRACK),
        }
        with patch(
            "src.data.scrapers.travronden_tracks.TravrondenTracksClient",
            return_value=self._mock_client(responses),
        ):
            result = fetch_all_se_tracks(
                scan_from=100, scan_limit=5, cache_dir=tmp_path
            )
        assert "Färjestad" in result
        assert "Jarlsberg" not in result

    def test_no_duplicates(self, tmp_path):
        # Sama rata kahdessa eri roundissa — pitää näkyä vain kerran
        responses = {
            100: _make_statistics_response(FARJESTAD_TRACK),
            99: _make_statistics_response(FARJESTAD_TRACK),
        }
        with patch(
            "src.data.scrapers.travronden_tracks.TravrondenTracksClient",
            return_value=self._mock_client(responses),
        ):
            result = fetch_all_se_tracks(
                scan_from=100, scan_limit=5, cache_dir=tmp_path
            )
        assert list(result.keys()).count("Färjestad") == 1

    def test_empty_when_no_data(self, tmp_path):
        responses = {100: None, 99: None, 98: None}
        with patch(
            "src.data.scrapers.travronden_tracks.TravrondenTracksClient",
            return_value=self._mock_client(responses),
        ):
            result = fetch_all_se_tracks(
                scan_from=100, scan_limit=3, cache_dir=tmp_path
            )
        assert result == {}


# ---------------------------------------------------------------------------
# Testit: upsert_tracks (oikea SQLite-tietokanta)
# ---------------------------------------------------------------------------

class TestUpsertTracks:
    @pytest.fixture()
    def db_path(self, tmp_path):
        db = str(tmp_path / "test.db")
        migrate(db)
        return db

    def test_inserts_new_track(self, db_path):
        tracks = {"Färjestad": FARJESTAD_TRACK}
        result = upsert_tracks(db_path, tracks)
        assert result["updated"] == 1
        assert result["skipped"] == 0

        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT track_name, travronden_code, atg_track_id, length_total, "
            "length_home_stretch, open_stretch, angled_wing, source "
            "FROM tracks WHERE track_name = 'Färjestad'"
        ).fetchone()
        con.close()

        assert row is not None
        name, code, atg_id, length_total, home_stretch, open_s, angled, source = row
        assert name == "Färjestad"
        assert code == "F"
        assert atg_id == 15
        assert length_total == 1000
        assert home_stretch == 177
        assert open_s == 0      # False → 0 SQLitessä
        assert angled == 0
        assert source == "travronden"

    def test_updates_existing_track(self, db_path):
        """Toinen upsert päivittää olemassa olevan rivin."""
        tracks_v1 = {"Färjestad": {**FARJESTAD_TRACK, "length_home_stretch": 100}}
        tracks_v2 = {"Färjestad": {**FARJESTAD_TRACK, "length_home_stretch": 177}}

        upsert_tracks(db_path, tracks_v1)
        upsert_tracks(db_path, tracks_v2)

        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT length_home_stretch FROM tracks WHERE track_name = 'Färjestad'"
        ).fetchone()
        con.close()
        assert row[0] == 177

    def test_inserts_multiple_tracks(self, db_path):
        tracks = {
            "Färjestad": FARJESTAD_TRACK,
            "Solvalla": SOLVALLA_TRACK,
        }
        result = upsert_tracks(db_path, tracks)
        assert result["updated"] == 2

        con = sqlite3.connect(db_path)
        count = con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        con.close()
        assert count == 2

    def test_open_stretch_true_stored_correctly(self, db_path):
        tracks = {"Solvalla": SOLVALLA_TRACK}
        upsert_tracks(db_path, tracks)

        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT open_stretch, angled_wing FROM tracks WHERE track_name = 'Solvalla'"
        ).fetchone()
        con.close()
        assert row[0] == 1   # True → 1
        assert row[1] == 1

    def test_capacity_string_parsed_to_int(self, db_path):
        tracks = {"Färjestad": FARJESTAD_TRACK}  # capacity = "10000"
        upsert_tracks(db_path, tracks)

        con = sqlite3.connect(db_path)
        cap = con.execute(
            "SELECT capacity FROM tracks WHERE track_name = 'Färjestad'"
        ).fetchone()[0]
        con.close()
        assert cap == 10000

    def test_source_is_travronden(self, db_path):
        upsert_tracks(db_path, {"Färjestad": FARJESTAD_TRACK})
        con = sqlite3.connect(db_path)
        src = con.execute(
            "SELECT source FROM tracks WHERE track_name = 'Färjestad'"
        ).fetchone()[0]
        con.close()
        assert src == "travronden"

    def test_empty_dict_no_crash(self, db_path):
        result = upsert_tracks(db_path, {})
        assert result["updated"] == 0


# ---------------------------------------------------------------------------
# Integraatiotesti: välimuisti toimii (ei oikeita HTTP-kutsuja)
# ---------------------------------------------------------------------------

class TestCaching:
    def test_cache_avoids_second_fetch(self, tmp_path):
        """Jos round-tiedosto on jo välimuistissa, ei tehdä HTTP-kutsua."""
        from src.data.scrapers.travronden_tracks import TravrondenTracksClient

        cache_path = tmp_path / f"round_12345_statistics.json"
        cached_data = _make_statistics_response(FARJESTAD_TRACK)
        cache_path.write_text(json.dumps(cached_data), encoding="utf-8")

        client = TravrondenTracksClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch") as mock_fetch:
            result = client.get_statistics(12345)
            mock_fetch.assert_not_called()

        assert result["round"]["tracks"][0]["name"] == "Färjestad"
        client.close()
