"""Travrondenspel.se API-asiakas.

Julkinen mutta dokumentoimaton API. Tarjoaa pre-race-dataa V-pelilähdöille:
  - start_interval_group   — asiantuntijan per-hevonen pace-arvio (1/11/21/31)
  - is_first_after_castration, is_first_new_driver/trainer, is_first_shoes/carriage
  - game_percent           — V-pelin markkinasentimentti (ATG)
  - expected_odds          — Travrondenin oma kerroinennuste
  - horse.speed_records.{K,M,L} — ennätysajat per matkaluokka

EI käytetä:
  - speed, comment — POST-RACE-dataa (D1-tutkimus 14.5.2026 vahvisti)
  - placement, result — outcome-tietoa

ToS: Käytetään vain henkilökohtaiseen tutkimukseen, rehellinen User-Agent,
     1 req/s, ei rinnakkaisia kutsuja. URL-polku sisältää /public/ — julkinen.

Tekniset rajoitukset:
  - Dokumentoimaton API — voi muuttua ilman varoitusta
  - Kattaa vain V-pelilähdöt (V3, V4, V5, V64, V75, V86)
  - start_interval_group täytetty n. 60 % hevosista
  - Analyysikentät julkaistaan ~12–24 h ennen lähtöä
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from src.paths import RAW_DIR

logger = logging.getLogger(__name__)

_BASE = "https://www.travrondenspel.se/api/v1/public"
_UA = "ravit-edge research (jarkkom.lahde@gmail.com)"
_DEFAULT_TTL_DAYS = 30   # vanhentunut data ei muutu → pitkä cache
_RATE_LIMIT_SEC = 1.0


class TravrondenAPIClient:
    """Travrondenspel.se API-asiakas paikallisella tiedostocachella.

    Yksinkertainen: yksi JSON-tiedosto per round_id ja race_id.
    Cache-TTL 30 vrk — valmistuneen kierroksen data ei muutu.

    Käyttö:
        with TravrondenAPIClient() as tr:
            round_data = tr.get_round(171922)
            race_data  = tr.get_race(385514)

    Args:
        cache_dir: hakemisto JSON-cacheille (oletus: data/raw/travronden/)
        cache_ttl_days: välimuistin voimassaoloaika vuorokausina
        rate_limit_seconds: pyyntöjen välinen odotusaika (älä laske alle 1.0)
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        cache_ttl_days: int = _DEFAULT_TTL_DAYS,
        rate_limit_seconds: float = _RATE_LIMIT_SEC,
    ) -> None:
        self.cache_dir = cache_dir or (RAW_DIR / "travronden")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ttl = cache_ttl_days * 86400
        self._rate = rate_limit_seconds
        self._last_req: float = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": _UA},
            timeout=20.0,
            follow_redirects=True,
        )

    # ------------------------------------------------------------------
    # Julkinen API
    # ------------------------------------------------------------------

    def get_round(self, round_id: int, force_refresh: bool = False) -> dict[str, Any]:
        """Palauta /round/{round_id}/ -vastaus (välimuistilla).

        Vastaus sisältää:
          - status: "upcoming" | "analysed" | "finished"
          - round_date, track_key, game_type
          - legs[]: [{id, leg, race (=race_id), race_number, meet_id}, ...]
        """
        return self._cached_get(
            f"round_{round_id}", f"/round/{round_id}/", force_refresh
        )

    def get_race(self, race_id: int, force_refresh: bool = False) -> dict[str, Any]:
        """Palauta /race/{race_id}/ -vastaus (välimuistilla).

        Vastaus sisältää starts[] per hevonen:
          - horse.atg_id, horse.name, horse.speed_records
          - start_interval_group, is_first_*, game_percent, expected_odds
          - score (post-race — EI käytetä piirteenä)
        """
        return self._cached_get(
            f"race_{race_id}", f"/race/{race_id}/", force_refresh
        )

    def get_finished_round_ids(
        self,
        start_id: int,
        end_id: int,
        step: int = 100,
    ) -> list[int]:
        """Skannaa round_id-alue ja palauta finished-kierrosten id:t.

        Käyttö datapilottiin: löydä kaikki valmiit kierrokset väliltä.
        Hitaampaa kuin suorat haut — käytä vain kerran per batch.

        Args:
            start_id: alin testattava round_id
            end_id: korkein testattava round_id
            step: id-hyppäys (100 = tarkistaa n. 10 % id-avaruudesta)
        """
        finished = []
        for rid in range(start_id, end_id + 1, step):
            try:
                data = self.get_round(rid)
                if data.get("status") in ("analysed", "finished"):
                    finished.append(rid)
                    logger.debug("finished: %d (%s)", rid, data.get("round_date"))
            except Exception as e:
                logger.debug("round %d ei saatavilla: %s", rid, e)
        return finished

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TravrondenAPIClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Sisäinen toteutus
    # ------------------------------------------------------------------

    def _cached_get(
        self, cache_key: str, path: str, force_refresh: bool
    ) -> dict[str, Any]:
        cache_path = self.cache_dir / f"{cache_key}.json"
        if not force_refresh and self._is_fresh(cache_path):
            return json.loads(cache_path.read_text(encoding="utf-8"))
        data = self._fetch(f"{_BASE}{path}")
        cache_path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        return data

    def _is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        age = time.monotonic() - path.stat().st_mtime
        # monotonic vs real-time: käytä wall-clock st_mtime
        import os
        age_wall = time.time() - os.stat(path).st_mtime
        return age_wall < self._ttl

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        wait = self._rate - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_req = time.time()

    def _fetch(self, url: str) -> dict[str, Any]:
        """Hae URL HTTP:llä, max 3 yritystä exponential-backoffilla."""
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                self._throttle()
                logger.debug("GET %s (yritys %d)", url, attempt + 1)
                r = self._client.get(url)
                if r.status_code == 404:
                    return {}   # ei löydy → tyhjä dict, tallennetaan cacheen
                r.raise_for_status()
                return r.json()
            except Exception as exc:
                last_exc = exc
                wait = 2 ** attempt
                logger.warning("Pyyntö epäonnistui %s: %s — odotetaan %ds", url, exc, wait)
                time.sleep(wait)
        raise RuntimeError(f"Kaikki yritykset epäonnistuivat: {url}") from last_exc
