"""
ATG API Client.

Käyttää atg.se:n julkista REST-rajapintaa, jota sivusto itse käyttää.
Kaikki endpointit on JSONia ja toimivat ilman autentikointia.

Pää-endpointit (testattu toimivaksi 2024-2026):
  - https://www.atg.se/services/racinginfo/v1/api/calendar/day/{YYYY-MM-DD}
  - https://www.atg.se/services/racinginfo/v1/api/games/{gameId}
  - https://www.atg.se/services/racinginfo/v1/api/races/{raceId}

HUOM: Tämä on epävirallinen julkinen API. Käytä järkevää rate limittausta
(max ~1 req/sec) äläkä rakenna liikennettä joka vahingoittaa palvelua.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

BASE_URL = "https://www.atg.se/services/racinginfo/v1/api"
USER_AGENT = "ravit-edge-research/0.1 (educational; contact: ravit-edge)"
DEFAULT_TIMEOUT = 15.0

# Whitelist-suodatus calendar/day-vastaukseen. ATG palauttaa myös
# Vincennes (FR), Lingfield (GB), Bjerke (NO), Turffontein (ZA) jne.
# joiden /races-vastauksissa start.horse.id puuttuu - tällöin Runner FK
# ei voi tallentua. Lisäksi malli kalibroidaan ruotsalaiseen pelimarkki-
# naan, joten ulkomaiset radat ovat kohinaa nykyversiossa.
SWEDISH_COUNTRY_CODE = "SE"

# TODO #3: Gallop-suodatus. ATG palauttaa SE-kalenteri sisältää myös
# galloppia (Bro Park, Jägersro Galopp). Galopin lähdöissä ei ole
# kmTime-objekteja → kaikki gallop-runnerit näyttäisivät NULL km-ajalla
# ja retry_incomplete_results yrittäisi hakea niitä joka päivä turhaan.
# Suodatetaan pois jo calendar-tasolla sport-kentän perusteella.
TROT_SPORT = "trot"


class ATGClient:
    """Synchronous client for ATG's public racing info API."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        rate_limit_seconds: float = 1.0,
    ):
        self.base_url = base_url
        self.timeout = timeout
        self.rate_limit_seconds = rate_limit_seconds
        self._last_request_time: float = 0.0
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self._last_request_time = time.time()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def _get(self, path: str) -> dict[str, Any]:
        self._rate_limit()
        url = f"{self.base_url}{path}"
        logger.debug("GET %s", url)
        response = self._client.get(url)
        response.raise_for_status()
        return response.json()

    def get_calendar_day(
        self, target_date: date | str, swedish_only: bool = True
    ) -> dict[str, Any]:
        """Hae kaikki kyseisen päivän ravipäivät (tracks) ja niiden lähdöt.

        Args:
            target_date: Päivämäärä YYYY-MM-DD muodossa tai date-objekti.
            swedish_only: Suodata pois ulkomaiset radat (countryCode != "SE").
                Oletuksena True - ks. SWEDISH_COUNTRY_CODE-vakion perustelu.

        Returns:
            JSON jossa avain "tracks" -> list of meetings,
            jokaisella "races" -> list of races with raceId.
        """
        if isinstance(target_date, date):
            target_date = target_date.isoformat()
        data = self._get(f"/calendar/day/{target_date}")
        if not swedish_only:
            return data

        all_tracks = data.get("tracks") or []
        kept: list[dict[str, Any]] = []
        skipped: list[str] = []
        for t in all_tracks:
            country_ok = t.get("countryCode") == SWEDISH_COUNTRY_CODE
            sport_ok = t.get("sport") == TROT_SPORT
            if country_ok and sport_ok:
                kept.append(t)
            else:
                reason = (
                    f"sport={t.get('sport')}"
                    if country_ok
                    else f"countryCode={t.get('countryCode')}"
                )
                skipped.append(f"{t.get('name')} ({reason})")
        if skipped:
            logger.info(
                "Skipped %d non-trot/non-SE tracks: %s",
                len(skipped),
                ", ".join(skipped),
            )
        data["tracks"] = kept
        return data

    def get_game(self, game_id: str) -> dict[str, Any]:
        """Hae yksittäisen pelin (esim. V75-{gameId}) tiedot.

        Sisältää kaikki lähdöt, hevoset, ohjastajat ja senhetkiset kertoimet/sijoitukset.
        """
        return self._get(f"/games/{game_id}")

    def get_race(self, race_id: str) -> dict[str, Any]:
        """Hae yksittäisen lähdön tarkat tiedot.

        HUOM: ennen lähtöä /races/{id} EI sisällä per-runner kertoimia
        (start.pools puuttuu). Pre-race odds:ille käytä
        get_win_pool_game(race_id), joka antaa start.pools.vinnare.odds.
        Lähdön jälkeen result.finalOdds täyttyy tähän endpointiin.
        """
        return self._get(f"/races/{race_id}")

    def get_win_pool_game(self, race_id: str) -> dict[str, Any]:
        """Hae voitto-pelin (vinnare) live-pool yhdelle lähdölle.

        Game-id-format: "vinnare_<race_id>". Vastaus sisältää
        races[0].starts[*].pools.vinnare.odds (int×100, esim. 4539=45.39).
        Tämä päivittyy reaaliaikaisesti pelikassan sulkeutumiseen asti
        (~T-30s) ja on ainoa tapa saada pre-race kertoimia ATG:lta.
        """
        return self._get(f"/games/vinnare_{race_id}")

    def list_today_races(self) -> list[dict[str, Any]]:
        """Helper: palauta tämän päivän kaikki lähdöt litteänä listana."""
        data = self.get_calendar_day(date.today())
        races: list[dict[str, Any]] = []
        for track in data.get("tracks", []):
            track_name = track.get("name")
            for race in track.get("races", []):
                races.append(
                    {
                        "race_id": race.get("id"),
                        "track": track_name,
                        "race_number": race.get("number"),
                        "start_time": race.get("startTime"),
                        "distance": race.get("distance"),
                        "start_method": race.get("startMethod"),
                    }
                )
        return races

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ATGClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


if __name__ == "__main__":
    # Pikatesti: hae tämän päivän lähdöt ja tulosta yhteenveto
    logging.basicConfig(level=logging.INFO)
    with ATGClient() as atg:
        races = atg.list_today_races()
        print(f"Tänään {date.today()}: {len(races)} lähtöä")
        for r in races[:5]:
            print(f"  {r['track']} L{r['race_number']} klo {r['start_time']} ({r['distance']}m)")
