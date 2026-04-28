"""Tests for Travsport API client.

Käyttää tallennetuja sample_792729_*.json-tiedostoja mockattuna
httpx-vastauksena - testit eivät tee oikeita verkkopyyntöjä.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.data.scrapers.travsport import (
    TravsportAPIClient,
    _kilometer_time_from_sort,
    _placement,
    parse_kilometer_time,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "data" / "raw" / "travsport"
SAMPLE_HORSE_ID = 792729


def _sample(endpoint: str) -> Any:
    path = SAMPLE_DIR / f"sample_{SAMPLE_HORSE_ID}_{endpoint}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class _FakeResponse:
    def __init__(self, payload: Any):
        self._payload = payload

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        pass


@pytest.fixture
def client_with_mocked_http(tmp_path, monkeypatch):
    """TravsportAPIClient jonka HTTP-kutsut palauttavat sample-JSONin."""
    client = TravsportAPIClient(
        cache_dir=tmp_path,
        rate_limit_seconds=0.0,
        cache_ttl_seconds=3600,
    )

    def fake_get(self, url: str, *args, **kwargs):
        # Päättele endpoint URLista: .../horses/{endpoint}/organisation/...
        endpoint = url.split("/horses/")[1].split("/")[0]
        return _FakeResponse(_sample(endpoint))

    monkeypatch.setattr("httpx.Client.get", fake_get)
    # Estä todellinen verkko myös sleepillä jos rate-limit kutsutaan
    monkeypatch.setattr("time.sleep", lambda *_: None)
    return client


def test_get_results_returns_normalized_list(client_with_mocked_http):
    starts = client_with_mocked_http.get_results(SAMPLE_HORSE_ID)
    assert isinstance(starts, list)
    assert len(starts) > 0

    expected_keys = {
        "race_date",
        "track",
        "race_id",
        "race_number",
        "distance",
        "start_method",
        "start_number",
        "finish_position",
        "kilometer_time_seconds",
        "position_at_800m",
        "driver",
        "trainer",
        "prize_won",
        "win_odds_final",
        "withdrawn",
        "raw",
    }
    assert expected_keys <= set(starts[0].keys())


def test_get_results_field_types(client_with_mocked_http):
    starts = client_with_mocked_http.get_results(SAMPLE_HORSE_ID)
    finished = [s for s in starts if s["finish_position"] is not None]
    # Frances Willillä on ainakin yksi sija (5. paikka 2024-10-22)
    assert finished, "Sample-datassa pitäisi olla vähintään yksi sijoitus"
    s = finished[0]
    assert isinstance(s["finish_position"], int)
    assert isinstance(s["distance"], int)
    assert s["start_method"] in {"V", "A"}
    assert isinstance(s["race_date"], str) and len(s["race_date"]) == 10


def test_scratched_starts_have_none_finish(client_with_mocked_http):
    starts = client_with_mocked_http.get_results(SAMPLE_HORSE_ID)
    # Sample-datan ensimmäinen rivi on tämän päivän startti (varikko, sortValue 989)
    today = starts[0]
    assert today["finish_position"] is None
    assert today["kilometer_time_seconds"] is None


def test_get_basic_info_passthrough(client_with_mocked_http):
    info = client_with_mocked_http.get_basic_info(SAMPLE_HORSE_ID)
    assert info["name"] == "Frances Will"
    assert info["id"] == SAMPLE_HORSE_ID


def test_cache_hit_skips_http(tmp_path, monkeypatch):
    """Tuore cache-tiedosto pitää lukea ilman http-kutsua."""
    cache_file = tmp_path / f"{SAMPLE_HORSE_ID}_results.json"
    cache_file.write_text(
        json.dumps(_sample("results"), ensure_ascii=False), encoding="utf-8"
    )

    client = TravsportAPIClient(
        cache_dir=tmp_path,
        rate_limit_seconds=0.0,
        cache_ttl_seconds=3600,
    )

    def boom(*_a, **_kw):
        raise AssertionError("HTTP-kutsua ei pitäisi tehdä cache-osumalla")

    monkeypatch.setattr("httpx.Client.get", boom)
    starts = client.get_results(SAMPLE_HORSE_ID)
    assert len(starts) > 0


def test_force_refresh_bypasses_cache(tmp_path, monkeypatch):
    """force_refresh=True pitää tehdä http-kutsu vaikka cache on tuore."""
    cache_file = tmp_path / f"{SAMPLE_HORSE_ID}_results.json"
    cache_file.write_text("[]", encoding="utf-8")  # tyhjä cache

    client = TravsportAPIClient(
        cache_dir=tmp_path,
        rate_limit_seconds=0.0,
        cache_ttl_seconds=3600,
    )

    calls = SimpleNamespace(n=0)

    def fake_get(self, url: str, *args, **kwargs):
        calls.n += 1
        return _FakeResponse(_sample("results"))

    monkeypatch.setattr("httpx.Client.get", fake_get)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    # Ilman force_refresh: lukee tyhjän cachen
    assert client.get_results(SAMPLE_HORSE_ID) == []
    assert calls.n == 0

    # force_refresh=True: tekee http-kutsun
    starts = client.get_results(SAMPLE_HORSE_ID, force_refresh=True)
    assert calls.n == 1
    assert len(starts) > 0


def test_kilometer_time_from_sort():
    assert _kilometer_time_from_sort({"sortValue": 1193}) == pytest.approx(79.3)
    assert _kilometer_time_from_sort({"sortValue": 1224}) == pytest.approx(82.4)
    # Sentinel-arvot
    assert _kilometer_time_from_sort({"sortValue": 9999}) is None
    assert _kilometer_time_from_sort({"sortValue": 9997}) is None
    assert _kilometer_time_from_sort(None) is None


def test_placement_filters_sentinels():
    assert _placement({"sortValue": 5}) == 5
    assert _placement({"sortValue": 989}) is None  # varikko
    assert _placement({"sortValue": 990}) is None  # gdk


def test_parse_kilometer_time_text():
    assert parse_kilometer_time("1.19,3") == pytest.approx(79.3)
    assert parse_kilometer_time("1.19,3a") == pytest.approx(79.3)
    assert parse_kilometer_time("") is None
    assert parse_kilometer_time("ei mitään") is None
