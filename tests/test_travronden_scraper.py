"""Yksikkötestit TravrondenAPIClientille.

Kaikki testit offline — ei oikeita API-kutsuja.
httpx.Client mockataan unittest.mock.patch:lla.

Kattaa:
  - Cache toimii: fresh-tiedosto palautetaan ilman HTTP-kutsua
  - Cache vanhenee: uusi haku tehdään kun TTL ylittyy
  - 404 → tyhjä dict, tallennetaan cacheen, ei kaada
  - Retry: 5xx-virhe → yritetään uudelleen (max 3), lopulta RuntimeError
  - Rate-limit: _throttle() kutsuu sleep:iä jos viime pyyntö oli äsken
  - get_finished_round_ids: palauttaa vain finished/analysed-kierrokset
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from src.data.scrapers.travronden import TravrondenAPIClient


# ---------------------------------------------------------------------------
# Apumetodit
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, json_data: dict | None = None):
    """Rakenna fake httpx.Response-olio."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "travronden_cache"


@pytest.fixture
def client(cache_dir: Path) -> TravrondenAPIClient:
    return TravrondenAPIClient(cache_dir=cache_dir, cache_ttl_days=30, rate_limit_seconds=0.0)


# ---------------------------------------------------------------------------
# Cache-testit
# ---------------------------------------------------------------------------

class TestCache:

    def test_fresh_cache_skips_http(self, client: TravrondenAPIClient, cache_dir: Path):
        """Fresh cache-tiedosto palautetaan ilman HTTP-kutsua."""
        cache_path = cache_dir / "round_999.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"status": "finished", "round_date": "2026-01-01"}
        cache_path.write_text(json.dumps(data), encoding="utf-8")

        with patch.object(client._client, "get") as mock_get:
            result = client.get_round(999)

        mock_get.assert_not_called()
        assert result["status"] == "finished"

    def test_expired_cache_triggers_http(self, client: TravrondenAPIClient, cache_dir: Path):
        """Vanhentunut cache → HTTP-haku."""
        cache_path = cache_dir / "round_888.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        old_data = {"status": "old"}
        cache_path.write_text(json.dumps(old_data), encoding="utf-8")

        # Aseta tiedoston mtime kaukaiseen menneisyyteen (35 vrk sitten)
        old_mtime = time.time() - (35 * 86400)
        import os
        os.utime(cache_path, (old_mtime, old_mtime))

        new_data = {"status": "finished", "round_date": "2026-01-15"}
        mock_resp = _make_response(200, new_data)

        with patch.object(client._client, "get", return_value=mock_resp):
            result = client.get_round(888)

        assert result["status"] == "finished"
        # Tarkista että cache päivittyi
        saved = json.loads(cache_path.read_text())
        assert saved["status"] == "finished"

    def test_force_refresh_bypasses_fresh_cache(self, client: TravrondenAPIClient, cache_dir: Path):
        """force_refresh=True ohittaa tuoreen cachen."""
        cache_path = cache_dir / "round_777.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"status": "old"}), encoding="utf-8")

        new_data = {"status": "analysed"}
        mock_resp = _make_response(200, new_data)

        with patch.object(client._client, "get", return_value=mock_resp):
            result = client.get_round(777, force_refresh=True)

        assert result["status"] == "analysed"

    def test_cache_is_written_after_fetch(self, client: TravrondenAPIClient, cache_dir: Path):
        """Haettu data tallennetaan cache-tiedostoon."""
        data = {"status": "finished", "legs": []}
        mock_resp = _make_response(200, data)

        with patch.object(client._client, "get", return_value=mock_resp):
            client.get_round(555)

        cache_path = cache_dir / "round_555.json"
        assert cache_path.exists()
        saved = json.loads(cache_path.read_text())
        assert saved["status"] == "finished"

    def test_get_race_uses_race_cache_key(self, client: TravrondenAPIClient, cache_dir: Path):
        """get_race() käyttää race_{id}-avaimena."""
        cache_path = cache_dir / "race_12345.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"starts": []}
        cache_path.write_text(json.dumps(data), encoding="utf-8")

        with patch.object(client._client, "get") as mock_get:
            result = client.get_race(12345)

        mock_get.assert_not_called()
        assert "starts" in result


# ---------------------------------------------------------------------------
# HTTP-virhetilanteet
# ---------------------------------------------------------------------------

class TestHttpErrors:

    def test_404_returns_empty_dict(self, client: TravrondenAPIClient):
        """404 → tyhjä dict, ei kaada."""
        mock_resp = _make_response(404)
        mock_resp.raise_for_status.side_effect = None  # 404 ei raise_for_status:ssa

        with patch.object(client._client, "get", return_value=mock_resp):
            result = client.get_round(404)

        assert result == {}

    def test_404_result_cached(self, client: TravrondenAPIClient, cache_dir: Path):
        """404 → tyhjä dict tallennetaan cacheen (ei toisteta pyyntöjä)."""
        mock_resp = _make_response(404)
        mock_resp.raise_for_status.side_effect = None

        with patch.object(client._client, "get", return_value=mock_resp) as mock_get:
            client.get_round(404)
            client.get_round(404)  # toinen kutsu → cachesta

        assert mock_get.call_count == 1

    def test_5xx_retried_three_times_then_raises(self, client: TravrondenAPIClient):
        """5xx-virhe → retry 3× → RuntimeError."""
        mock_resp = _make_response(500)

        with patch.object(client._client, "get", return_value=mock_resp) as mock_get:
            with patch("time.sleep"):  # ei oikeita odotuksia testeissä
                with pytest.raises(RuntimeError, match="Kaikki yritykset"):
                    client.get_round(500)
            assert mock_get.call_count == 3

    def test_network_exception_retried(self, client: TravrondenAPIClient):
        """Verkkovirhe (ei HTTP) → retry 3×."""
        with patch.object(client._client, "get", side_effect=ConnectionError("timeout")) as mock_get:
            with patch("time.sleep"):
                with pytest.raises(RuntimeError):
                    client.get_round(600)
            assert mock_get.call_count == 3


# ---------------------------------------------------------------------------
# Rate-limit
# ---------------------------------------------------------------------------

class TestRateLimit:

    def test_throttle_sleeps_when_too_fast(self, cache_dir: Path):
        """Peräkkäiset kutsut ilman taukoa → sleep() kutsutaan."""
        fast_client = TravrondenAPIClient(
            cache_dir=cache_dir,
            rate_limit_seconds=1.0,  # 1 sekunnin rajoitus
        )
        fast_client._last_req = time.time()  # simuloi äsken tehtyä pyyntöä

        with patch("time.sleep") as mock_sleep:
            fast_client._throttle()

        # sleep() pitäisi kutsua (aika < 1s on kulunut)
        mock_sleep.assert_called_once()
        sleep_duration = mock_sleep.call_args[0][0]
        assert sleep_duration > 0

    def test_throttle_no_sleep_when_enough_time_passed(self, cache_dir: Path):
        """Kun tarpeeksi aikaa kulunut → ei sleep:iä."""
        slow_client = TravrondenAPIClient(
            cache_dir=cache_dir,
            rate_limit_seconds=0.01,
        )
        slow_client._last_req = time.time() - 10.0  # 10 s sitten

        with patch("time.sleep") as mock_sleep:
            slow_client._throttle()

        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# get_finished_round_ids
# ---------------------------------------------------------------------------

class TestGetFinishedRoundIds:

    def _setup_round_response(self, client, round_id: int, status: str):
        """Tallenna fake round-vastaus cacheen."""
        cache_path = client.cache_dir / f"round_{round_id}.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"status": status, "round_date": "2026-01-01"}),
            encoding="utf-8",
        )

    def test_returns_only_finished_and_analysed(self, client: TravrondenAPIClient):
        """Vain finished/analysed palautetaan — upcoming ohitetaan."""
        self._setup_round_response(client, 1000, "finished")
        self._setup_round_response(client, 1100, "upcoming")
        self._setup_round_response(client, 1200, "analysed")

        result = client.get_finished_round_ids(1000, 1200, step=100)

        assert 1000 in result
        assert 1100 not in result
        assert 1200 in result

    def test_empty_when_none_finished(self, client: TravrondenAPIClient):
        """Kaikki upcoming → tyhjä lista."""
        self._setup_round_response(client, 2000, "upcoming")
        self._setup_round_response(client, 2100, "upcoming")

        result = client.get_finished_round_ids(2000, 2100, step=100)

        assert result == []

    def test_exception_per_round_is_swallowed(self, client: TravrondenAPIClient):
        """Yksittäinen 404-round ei kaada koko skannausta."""
        self._setup_round_response(client, 3000, "finished")
        # 3100 ei ole cachessa → 404
        mock_404 = _make_response(404)
        mock_404.raise_for_status.side_effect = None

        with patch.object(client._client, "get", return_value=mock_404):
            result = client.get_finished_round_ids(3000, 3100, step=100)

        assert 3000 in result  # cachesta onnistui

    def test_step_controls_granularity(self, client: TravrondenAPIClient):
        """step-parametri määrittää testattavien id:den välin."""
        for rid in [4000, 4050, 4100]:
            self._setup_round_response(client, rid, "finished")

        result = client.get_finished_round_ids(4000, 4100, step=50)
        assert result == [4000, 4050, 4100]


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

class TestContextManager:

    def test_context_manager_closes_client(self, cache_dir: Path):
        """__exit__ kutsuu close():a — HTTP-asiakas suljetaan siististi."""
        tr = TravrondenAPIClient(cache_dir=cache_dir)
        with patch.object(tr._client, "close") as mock_close:
            with tr:
                pass  # __exit__ kutsutaan kun with-blokki päättyy
        # close() kutsuttiin __exit__:ssa
        mock_close.assert_called_once()
