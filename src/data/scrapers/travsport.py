"""
Travsport.se webapi-client.

Travsportin julkinen webapi tarjoaa kaiken hevosdatan JSONina ilman
loginia. URL-malli (vahvistettu 2026-04-27):

    https://api.travsport.se/webapi/horses/{endpoint}
        /organisation/{organisation}/sourceofdata/{source}/horseid/{horse_id}

Toimivat endpointit (anonyymi pääsy):
    basicinformation  - hevosen perustiedot
    results           - startit (ks. paginointi-huomautus)
    statistics        - vuosiyhteenvedot
    history           - omistaja- ja treenari-aikajana

Paginointi: epäselvä (2026-04-27). API hyväksyi from/to/limit-parametrit
HTTP 200:lla mutta ei ilmeisesti rajoittanut tai laajentanut tulosta.
Hevosen koko ura -tutkimus jää erilliseksi taskiksi.

Pace-piirteet (asema 800m, juoksuvire-koodi) EIVÄT näy /results-
vastauksessa - ne tulevat per-race-endpointista, joka pitää selvittää
erikseen.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.paths import RAW_DIR as _RAW_DIR_ABS

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = _RAW_DIR_ABS / "travsport"

API_BASE = "https://api.travsport.se/webapi"
USER_AGENT = "ravit-edge-research/0.1 (educational; contact: ravit-edge)"

ENDPOINTS = ("basicinformation", "results", "statistics", "history")

DEFAULT_CACHE_TTL_SECONDS = 7 * 24 * 3600
DEFAULT_RATE_LIMIT_SECONDS = 1.0

# 989+ ovat sortValue-sentinelejä (varikko, hylätty, peräytys).
_NON_FINISH_PLACEMENT = 989
# 9990+ ovat km-aika-sentinelejä (laukka, ei aikaa).
_INVALID_KM_TIME = 9990
# 9998+ ovat odds-sentinelejä (ei kerrointa / varikko).
_INVALID_ODDS = 9998


class TravsportAPIClient:
    """Cache-pohjainen webapi-client Travsportin hevosdatalle."""

    def __init__(
        self,
        cache_dir: str | Path = _DEFAULT_CACHE_DIR,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS,
        organisation: str = "TROT",
        source_of_data: str = "SPORT",
        api_base: str = API_BASE,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_seconds = cache_ttl_seconds
        self.rate_limit_seconds = rate_limit_seconds
        self.organisation = organisation
        self.source_of_data = source_of_data
        self.api_base = api_base
        self._last_request: float = 0.0
        self._client = httpx.Client(
            timeout=20.0,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Language": "sv-SE,sv;q=0.9",
            },
            follow_redirects=True,
        )

    # ---- Julkiset metodit ---------------------------------------------

    def get_basic_info(self, horse_id: int | str, force_refresh: bool = False) -> dict:
        return self._get("basicinformation", horse_id, force_refresh)

    def get_statistics(self, horse_id: int | str, force_refresh: bool = False) -> dict:
        return self._get("statistics", horse_id, force_refresh)

    def get_history(self, horse_id: int | str, force_refresh: bool = False) -> dict:
        return self._get("history", horse_id, force_refresh)

    def get_results(
        self, horse_id: int | str, force_refresh: bool = False
    ) -> list[dict[str, Any]]:
        """Palauta hevosen startit normalisoituna list[dict]:nä.

        Kentät matchaavat README:n speksiä:
            race_date (str, ISO YYYY-MM-DD)
            track (str)                       # Travsportin trackCode
            distance (int, metriä)
            start_method (str)                # 'V'=voltti, 'A'=auto
            start_number (int | None)         # lähtörata
            finish_position (int | None)      # None jos varikko/hylätty/laukka
            kilometer_time_seconds (float | None)
            position_at_800m (int | None)     # ei tällä hetkellä saatavilla
                                              # /results-endpointista
            driver (str | None)
            trainer (str | None)
            prize_won (int)                   # SEK
            win_odds_final (float | None)     # totopelin lopulliset kertoimet
            withdrawn (bool)
            race_id (int | None)              # join-avain
            race_number (int | None)
            raw (dict)                        # alkuperäinen rivi varalta
        """
        raw_starts = self._get("results", horse_id, force_refresh)
        return [_normalize_start(r) for r in raw_starts]

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TravsportAPIClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- Sisäiset apurit ----------------------------------------------

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self._last_request = time.time()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def _fetch_json(self, url: str) -> Any:
        self._rate_limit()
        logger.info("GET %s", url)
        response = self._client.get(url)
        response.raise_for_status()
        return response.json()

    def _cache_path(self, horse_id: int | str, endpoint: str) -> Path:
        return self.cache_dir / f"{horse_id}_{endpoint}.json"

    def _cache_is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        age = time.time() - path.stat().st_mtime
        return age < self.cache_ttl_seconds

    def _build_url(self, endpoint: str, horse_id: int | str) -> str:
        return (
            f"{self.api_base}/horses/{endpoint}"
            f"/organisation/{self.organisation}"
            f"/sourceofdata/{self.source_of_data}"
            f"/horseid/{horse_id}"
        )

    def _get(self, endpoint: str, horse_id: int | str, force_refresh: bool) -> Any:
        if endpoint not in ENDPOINTS:
            raise ValueError(f"Tuntematon endpoint: {endpoint}")
        cache_file = self._cache_path(horse_id, endpoint)
        if not force_refresh and self._cache_is_fresh(cache_file):
            return json.loads(cache_file.read_text(encoding="utf-8"))

        data = self._fetch_json(self._build_url(endpoint, horse_id))
        cache_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return data


# ---- Normalisointi ----------------------------------------------------


def _normalize_start(r: dict) -> dict[str, Any]:
    info = r.get("raceInformation") or {}
    return {
        "race_date": info.get("date"),
        "track": r.get("trackCode"),
        "race_id": info.get("raceId"),
        "race_number": info.get("raceNumber"),
        "distance": _sort_int(r.get("distance")),
        "start_method": r.get("startMethod"),
        "start_number": _sort_int(r.get("startPosition")),
        "finish_position": _placement(r.get("placement")),
        "kilometer_time_seconds": _kilometer_time_from_sort(r.get("kilometerTime")),
        # km_time sortValue >= 9990 tarkoittaa laukkaa; withdrawn-hevoset eivät ole laukkoja
        "had_gallop": ((_sort_int(r.get("kilometerTime")) or 0) >= _INVALID_KM_TIME
                       and not bool(r.get("withdrawn"))),
        "position_at_800m": None,   # ei saatavilla Travsport /results-endpointista
        "track_condition": _track_condition(r.get("trackCondition")),
        "driver": (r.get("driver") or {}).get("name"),
        "trainer": (r.get("trainer") or {}).get("name"),
        "prize_won": _sort_int(r.get("prizeMoney")) or 0,
        "win_odds_final": _odds(r.get("odds")),
        "withdrawn": bool(r.get("withdrawn")),
        "raw": r,
    }


def _sort_int(field: Any) -> int | None:
    if not isinstance(field, dict):
        return None
    v = field.get("sortValue")
    return int(v) if isinstance(v, (int, float)) else None


def _placement(field: Any) -> int | None:
    v = _sort_int(field)
    if v is None or v >= _NON_FINISH_PLACEMENT:
        return None
    return v


def _odds(field: Any) -> float | None:
    v = _sort_int(field)
    if v is None or v >= _INVALID_ODDS:
        return None
    # sortValue on kerroin x10 (esim. 234 = 23.4)
    return v / 10.0


def _kilometer_time_from_sort(field: Any) -> float | None:
    """sortValue muodossa MSSt → sekunteina.

    Esim. 1193 = 1:19,3 = 79.3s. 9990+ = laukka/ei aikaa.
    """
    v = _sort_int(field)
    if v is None or v >= _INVALID_KM_TIME:
        return None
    minutes = v // 1000
    seconds_int = (v % 1000) // 10
    tenths = v % 10
    return minutes * 60 + seconds_int + tenths / 10.0


def _track_condition(field: Any) -> str | None:
    """Normalisoi Travsportin trackCondition-kenttä.

    Kenttä voi olla:
      - merkkijono: "LE", "ME", "TU" (Lätt/Medium/Tung) tai pidempi kuvaus
      - dict: {"sortValue": ..., "displayValue": "Latt"} (kuten raceType-kentässä)
      - None: puuttuu (joillekin vanhoille starteille)

    Tallennetaan sellaisenaan TEXT-kenttään; feature engineering -vaiheessa
    enkoodataan kategoriseksi piirteeksi.
    """
    if field is None:
        return None
    if isinstance(field, str):
        return field.strip() or None
    if isinstance(field, dict):
        # Priorisoi displayValue (ihmisluettava), fallback sortValue-koodi
        val = field.get("displayValue") or field.get("shortName") or field.get("sortValue")
        return str(val).strip() if val is not None else None
    return None


def parse_kilometer_time(text: str) -> float | None:
    """Apufunktio merkkijonopohjaiselle km-ajalle (esim. '1.13,4a').

    Käytetään kun data tulee tekstinä eikä JSONin sortValue-kenttänä
    (esim. ATG-screenscrape jos joskus tarvitaan).
    """
    if not text:
        return None
    cleaned = re.sub(r"[avk]\s*$", "", text.strip())
    # Muoto "M.SS,t" → minuutit + sekunnit + kymmenykset
    m = re.fullmatch(r"(\d+)\.(\d{1,2}),(\d)", cleaned)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 10
    # Pelkkä "SS,t" tai "SS.t"
    try:
        return float(cleaned.replace(",", "."))
    except ValueError:
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with TravsportAPIClient() as t:
        starts = t.get_results(792729, force_refresh=True)
        print(f"Frances Will: {len(starts)} starttia")
        for s in starts:
            print(
                f"  {s['race_date']} rata={s['track']} "
                f"{s['distance']}m {s['start_method']} "
                f"sija={s['finish_position']} km={s['kilometer_time_seconds']} "
                f"odds={s['win_odds_final']} palkinto={s['prize_won']}"
            )
