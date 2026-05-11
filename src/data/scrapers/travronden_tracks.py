"""Hae SE-ratojen rakennetiedot Travrondenspel.se:n API:sta.

Endpoint (vahvistettu empiirisesti 11.5.2026):
    GET /api/v1/public/round/{round_id}/statistics/
    → response["round"]["tracks"][0]  (yksi rata per round)

Vahvistetut kenttänimet ja esimerkkiarvot (Färjestad, round 171922):
    id                "F"           travronden_code
    atg_id            15            yhdistäjä ATG-rajapintaan
    name              "Färjestad"   vastaa races.track-arvoa
    country           "SE"          suodatin SE-ratoihin
    length_total      1000          radan koko (m)
    length_home_stretch 177         loppusuoran pituus (m) — kriittisin piirre
    width_1           2040          sisempi leveys
    width_2           2110          ulompi leveys
    dosage            1700          kaarteen kallistus (yksikkö epäselvä)
    open_stretch      false         toinen passing-linja loppusuoralla
    angled_wing       false         kaltevat keulakaaret autostart-lähdöissä
    slug              "farjestad"   URL-tunniste
    track_description str           yleinen kuvaus
    track_analysis    str           ravialan asiantuntija-arvio (ei FEATURE_COLS)
    built             "1936"        rakennusvuosi (string, myös "1936 (renoverad)")
    capacity          "10000"       yleisömäärä (string! → parsitaan int:ksi)
    homepage          "https://..."

Strategia:
    Skannaa round_id:t taaksepäin SCAN_FROM:sta (tunnettu uusin round).
    Kerää uniikit SE-radat (country == "SE").
    Pysähdy kun ei löydy uusia SE-ratoja EARLY_STOP_AFTER peräkkäisen
    round_id:n aikana TAI SCAN_LIMIT saavutettu.
    Cache: jokainen statistics-vastaus tallennetaan tiedostoon — sama
    round_id ei aiheuta uusia API-kutsuja.

Käyttö:
    Ajetaan KERRAN (tai harvoin) — rata-rakenne ei muutu vuosittain.
    ~26 SE-rataa, löydetään skannaamalla viimeiset 2–3 kk (n. 1000–3000
    round_id:tä). Idempotentti: upsert_tracks päivittää olemassa olevat rivit.

    python -m src.data.scheduler fetch-track-structures

ToS:
    Endpoint sisältää kirjaimellisesti /public/ → suunniteltu julkiseksi.
    Henkilökohtainen tutkimuskäyttö, rate limit 1 req/s, tunnistautuva UA.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from tenacity import retry, stop_after_attempt, wait_exponential

from src.data.schema import Track
from src.paths import RAW_DIR

logger = logging.getLogger(__name__)

_BASE = "https://www.travrondenspel.se/api/v1/public"
_UA = "ravit-edge research (jarkkom.lahde@gmail.com)"
_RATE_LIMIT_SEC = 1.0  # 1 req/s — kunnioitetaan palvelinta

# Uusin tunnettu round_id (päivitetään manuaalisesti tarvittaessa).
# Skannaus alkaa tästä ja menee taaksepäin.
SCAN_FROM = 171922  # V64 Färjestad 11.5.2026

# Skannausraja: kuinka monta round_id:tä yritetään enintään.
# ~40 round/vrk, 3 kk = ~3600 round → 5000 kattaa varmasti.
# Oletusarvo riittää normaalitapauksessa (kaikki radat löytyvät nopeammin).
SCAN_LIMIT = 5000

# Early-stop: jos tähän peräkkäiseen määrään round_id:tä ei löydy uusia
# SE-ratoja, skannataan vielä 300 lisää ja sitten lopetetaan.
# Suuri arvo → varmempi, hidas. Pieni → nopea, voi jättää harvinaisia ratoja.
EARLY_STOP_WINDOW = 500


# ---------------------------------------------------------------------------
# HTTP-asiakas + välimuisti
# ---------------------------------------------------------------------------

class TravrondenTracksClient:
    """Hae ja välimuistita Travrondenspel statistics-vastaukset."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        rate_limit_seconds: float = _RATE_LIMIT_SEC,
    ) -> None:
        self.cache_dir = (cache_dir or RAW_DIR / "travronden_tracks")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._rate_limit_seconds = rate_limit_seconds
        self._last_request: float = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": _UA},
            timeout=15.0,
        )

    def get_statistics(self, round_id: int) -> dict | None:
        """Palauta /round/{id}/statistics/ -vastaus. None jos 404 tai virhe.

        Vastaus välimuistitetaan tiedostoon — sama round_id ei aiheuta
        uutta HTTP-kutsua.
        """
        cache_path = self.cache_dir / f"round_{round_id}_statistics.json"
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cache_path.unlink(missing_ok=True)

        data = self._fetch(round_id)
        if data is not None:
            cache_path.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        return data

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TravrondenTracksClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # --- sisäiset ---

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self._rate_limit_seconds:
            time.sleep(self._rate_limit_seconds - elapsed)
        self._last_request = time.time()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def _fetch(self, round_id: int) -> dict | None:
        self._rate_limit()
        url = f"{_BASE}/round/{round_id}/statistics/"
        try:
            r = self._client.get(url)
        except httpx.RequestError as exc:
            logger.warning("travronden_tracks: verkkovirhe round %d: %s", round_id, exc)
            return None
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            logger.warning(
                "travronden_tracks: round %d → HTTP %d", round_id, r.status_code
            )
            return None
        return r.json()


# ---------------------------------------------------------------------------
# Skannaus + koostaminen
# ---------------------------------------------------------------------------

def fetch_all_se_tracks(
    scan_from: int = SCAN_FROM,
    scan_limit: int = SCAN_LIMIT,
    cache_dir: Path | None = None,
) -> dict[str, dict]:
    """Skannaa round_id:t taaksepäin ja kerää uniikit SE-radat.

    Palauttaa dict: track_name → raaka tracks-objekti (kenttänimet
    suoraan Travrondenin vastauksesta).

    Pysähtyy kun:
    - kaikki ID:t scan_from..scan_from-scan_limit on käyty, TAI
    - EARLY_STOP_WINDOW peräkkäistä round_id:tä ilman uusia SE-ratoja
      (tällöin skannataan vielä 300 extra-ID:tä varmistuksena)
    """
    tracks_seen: dict[str, dict] = {}
    ids_since_last_new = 0
    extra_buffer = 0
    in_extra = False

    with TravrondenTracksClient(cache_dir=cache_dir) as client:
        for offset in range(scan_limit):
            rid = scan_from - offset
            if rid <= 0:
                break

            data = client.get_statistics(rid)
            if data is None:
                ids_since_last_new += 1
            else:
                track_list = (data.get("round") or {}).get("tracks") or []
                found_new = False
                for t in track_list:
                    name = t.get("name")
                    country = t.get("country", "")
                    if name and country == "SE" and name not in tracks_seen:
                        tracks_seen[name] = t
                        found_new = True
                        logger.info(
                            "travronden_tracks: uusi rata %r (round %d, yhteensä %d)",
                            name, rid, len(tracks_seen),
                        )
                if found_new:
                    ids_since_last_new = 0
                    in_extra = False
                    extra_buffer = 0
                else:
                    ids_since_last_new += 1

            # Early-stop logiikka
            if not in_extra and ids_since_last_new >= EARLY_STOP_WINDOW:
                logger.info(
                    "travronden_tracks: %d ID:tä ilman uusia SE-ratoja — "
                    "ajetaan 300 extra-ID:tä varmistuksena",
                    EARLY_STOP_WINDOW,
                )
                in_extra = True
                extra_buffer = 300

            if in_extra:
                extra_buffer -= 1
                if extra_buffer <= 0:
                    logger.info(
                        "travronden_tracks: early stop — löydetty %d SE-rataa",
                        len(tracks_seen),
                    )
                    break

    return tracks_seen


# ---------------------------------------------------------------------------
# Tietokantakirjoitus
# ---------------------------------------------------------------------------

def upsert_tracks(db_path: str, tracks_data: dict[str, dict]) -> dict:
    """Tallenna rata-tiedot tracks-tauluun. Idempotentti.

    Args:
        db_path: polku SQLite-tietokantaan
        tracks_data: fetch_all_se_tracks():n palauttama dict

    Returns:
        {"updated": int, "skipped": int}
    """
    engine = create_engine(f"sqlite:///{db_path}")
    Session_ = sessionmaker(bind=engine)
    updated = 0
    skipped = 0

    with Session_() as session:
        for name, t in tracks_data.items():
            if not name:
                skipped += 1
                continue
            obj = session.get(Track, name)
            if obj is None:
                obj = Track(track_name=name)

            obj.travronden_code = t.get("id")
            obj.atg_track_id = _to_int(t.get("atg_id"))
            obj.slug = t.get("slug")
            obj.country = t.get("country") or "SE"
            obj.length_total = _to_int(t.get("length_total"))
            obj.length_home_stretch = _to_int(t.get("length_home_stretch"))
            obj.width_1 = _to_int(t.get("width_1"))
            obj.width_2 = _to_int(t.get("width_2"))
            obj.dosage = _to_int(t.get("dosage"))
            obj.open_stretch = _to_bool(t.get("open_stretch"))
            obj.angled_wing = _to_bool(t.get("angled_wing"))
            obj.description = t.get("track_description")
            obj.track_analysis = t.get("track_analysis")
            obj.built = str(t.get("built")) if t.get("built") is not None else None
            obj.capacity = _parse_capacity(t.get("capacity"))
            obj.homepage = t.get("homepage")
            obj.source = "travronden"
            obj.updated = datetime.now(timezone.utc)

            session.add(obj)
            updated += 1

        session.commit()

    return {"updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# Apufunktiot
# ---------------------------------------------------------------------------

def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_bool(v: Any) -> bool | None:
    if v is None:
        return None
    return bool(v)


def _parse_capacity(v: Any) -> int | None:
    """Capacity voi olla string '10000', '10 000' tai int."""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    try:
        # Poistetaan kaikki unicode-välilyönnit (tavallinen, kapea, ei-katkaiseva jne.)
        cleaned = "".join(c for c in str(v) if not c.isspace()).replace(",", "")
        return int(cleaned)
    except (TypeError, ValueError):
        return None
