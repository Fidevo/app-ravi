"""Poimi ASVT:n viralliset huutokauppatulokset (myyntihinnat) → CSV.

Primäärilähde: ASVT (huutokaupan järjestäjä) julkaisee per-erä-tulokset
PDF:inä osoitteessa https://www.asvt.se/auktioner/auktionsresultat.
robots.txt sallii /images/ (missä PDF:t ovat) geneeriselle user-agentille.

Tämä skripti EI tarvitse sukutaulua tai uratulosta — ne saadaan
laillisesti omasta DB:stä / Travsport-APIsta. Täältä otetaan vain:
  - myyntihinta (kaupallinen kohde)
  - liitosavain DB:hen: nimi + syntymävuosi (regnr "YY-NNNN")

PDF-rivin layout (vahvistettu 2026 elitauktion-tuloksesta):
  Nr  Namn  Kön(H/S/V)  Regnr(YY-NNNN)  Far  Mor  Säljare  Pris  Köpare
Parseri ankkuroituu Kön-kirjaimeen ja Regnr-kuvioon — kestää monisanaiset
nimet/säljare/köpare-kentät. Far/Mor/Säljare/Köpare jätetään raakana
'tail'-sarakkeeseen (pedigree tulee DB:stä; ei tarvita tässä).

Käyttö:
  python scripts/scrape_asvt_auctions.py            # kaikki Resultat-PDF:t
  python scripts/scrape_asvt_auctions.py --limit 5  # vain 5 (testaus)

Tuloste: data/asvt_auction_lots.csv
Cache:   data/raw/asvt/*.pdf (ei lataa uudelleen jos jo olemassa)
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import time
from pathlib import Path

import httpx
from pypdf import PdfReader

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("asvt")

BASE = "https://www.asvt.se"
RESULTS_PAGE = f"{BASE}/auktioner/auktionsresultat"
UA = "ravit-edge research (jarkkom.lahde@gmail.com)"
RATE_LIMIT_SEC = 1.0

CACHE_DIR = Path("data/raw/asvt")
OUT_CSV = Path("data/asvt_auction_lots.csv")

# Isojen myyntien (Elit/Derby/Kriterie/NGS) yhteinen ankkuri:
#   Nr  Namn  Kön(H/S/V)  <rest>
# rest sisältää (layoutista riippuen): [Regnr] Far Mor Säljare Pris[ kr] Köpare.
# Internetauktionit (sektioitu, ei Nr/Kön) eivät matchaa → ohitetaan.
_LINE_RE = re.compile(r"^\s*(\d+)\s+(.+?)\s+([HSV])\s+(.*)$")
# Regnr "YY-NNNN" rest:in alussa (vain uudempi layout)
_REGNR_RE = re.compile(r"^(\d{2})-(\d{3,5})\b")
# Hinta: "150 000", "1 400 000" (väli tuhaterottimena), valinnainen " kr"
_PRICE_RE = re.compile(r"(\d{1,3}(?:\s\d{3})+)\s*(?:kr)?")
# Status-avainsanat (åter=takaisinosto, struken/osåld=ei myyty).
_STATUS_RE = re.compile(r"åter|struken|osåld|osald", re.I)
# 4-numeroinen vuosi 2000–2029 (otsikosta/tiedostonimestä)
_YEAR4_RE = re.compile(r"\b(20[0-2]\d)\b")
# Lyhytkoodi tiedostonimessä: krit_10/krit05/elit01/jub02 → 20YY.
# (?!\d) eikä \b — alaviiva on sanamerkki → \b ei toimi "krit_10_resultat":ssa.
_SHORTCODE_RE = re.compile(r"(krit|elit|derby|mix|jub|bc)[_-]?(\d{2})(?!\d)", re.I)

# Yearling-myynnit: hevoset ovat 1-vuotiaita → birth_year = auction_year − 1
# (definitionaalisesti tosi; vahvistettu 2026 elit: regnr "25" = auction_year-1).
_YEARLING_TYPES = {"elit", "kriterie", "derby", "ngs"}


def _auction_type(fn: str) -> str:
    f = fn.lower()
    if "derby" in f:
        return "derby"
    if "krit" in f or "ngs" in f:
        return "kriterie"
    if "elit" in f:
        return "elit"
    if "bc" in f or "breeders" in f:
        return "bc"
    if "jub" in f:
        return "jubileum"
    if "mix" in f:
        return "mix"
    if re.match(r"resultat\d{4}-\d", f):
        return "internet"
    return "other"


def _resolve_year(head_lines: list[str], fn: str) -> int | None:
    """auction_year: tiedostonimen vuosi (tuoreet) → otsikkorivi (vanhat
    pressrelease-PDF:t) → lyhytkoodi. EI löysää 'mikä tahansa vuosi sivulla'
    -fallbackia (poimi aiemmin väärän vuoden datariveistä)."""
    # Tiedostonimen 4-num vuosi ILMAN \b (alaviiva on sanamerkki → \b ei toimi
    # "elitauktion_2026":ssa). Tuoreet myynnit nimeävät vuoden tiedostoon.
    m = re.search(r"20[0-2]\d", fn)
    if m:
        return int(m.group(0))
    for ln in head_lines[:3]:           # otsikkoalue: vanhojen PDF:ien vuosi
        m = _YEAR4_RE.search(ln)
        if m:
            return int(m.group(1))
    m = _SHORTCODE_RE.search(fn)        # lyhytkoodi krit_10 → 2010
    if m:
        return 2000 + int(m.group(2))
    return None


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept-Language": "sv-SE,sv;q=0.9"},
    )


def find_result_pdfs(client: httpx.Client) -> list[str]:
    """Hae tulossivulta vain per-erä-tulos-PDF:t (Resultat_*), ei pressreleaseja."""
    r = client.get(RESULTS_PAGE)
    r.raise_for_status()
    hrefs = re.findall(r'href="([^"]+\.pdf[^"]*)"', r.text, re.I)
    out: list[str] = []
    seen: set[str] = set()
    for h in hrefs:
        name = h.rsplit("/", 1)[-1].lower()
        # Vain tulostiedostot. Pressrelease_* sisältää vain yhteenvetolukuja.
        if "resultat" not in name:
            continue
        url = h if h.startswith("http") else BASE + h
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def download(client: httpx.Client, url: str) -> Path | None:
    """Lataa PDF cacheen (skip jos jo olemassa). Palauttaa polun tai None."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fname = url.rsplit("/", 1)[-1].split("?")[0]
    path = CACHE_DIR / fname
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        time.sleep(RATE_LIMIT_SEC)  # kohtelias: 1 req/s
        r = client.get(url)
        r.raise_for_status()
        path.write_bytes(r.content)
        return path
    except Exception as e:  # noqa: BLE001
        logger.warning("  lataus epäonnistui %s: %s", url, e)
        return None


def parse_pdf(path: Path) -> list[dict]:
    """Parsii yhden tulos-PDF:n per-erä-riveiksi."""
    try:
        reader = PdfReader(str(path))
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
    except Exception as e:  # noqa: BLE001
        logger.warning("  PDF-luku epäonnistui %s: %s", path.name, e)
        return []

    head_lines = [ln for ln in text.splitlines() if ln.strip()][:10]
    auction_year = _resolve_year(head_lines, path.name)
    auction_type = _auction_type(path.name)
    is_yearling = auction_type in _YEARLING_TYPES

    rows: list[dict] = []
    for ln in text.splitlines():
        m = _LINE_RE.match(ln)
        if not m:
            continue
        nr, name, sex, rest = m.groups()

        # Valinnainen Regnr rest:in alussa (uudempi layout) → syntymävuosi.
        regnr, birth_year, by_source = None, None, None
        rm = _REGNR_RE.match(rest)
        if rm:
            yy, serial = rm.groups()
            regnr = f"{yy}-{serial}"
            birth_year, by_source = 2000 + int(yy), "regnr"
            rest = rest[rm.end():].strip()

        low = rest.lower()
        # Status myyntirivin lopusta. Åter=takaisinosto, struken/osåld=ei myyty.
        if "struken" in low or "osåld" in low or "osald" in low:
            status = "unsold"
        elif "åter" in low or low.rstrip().endswith("ter"):
            status = "aterrop"  # takaisinosto (ei aito arm's-length-kauppa)
        else:
            status = "sold"

        pm = _PRICE_RE.search(rest)
        # Poista kaikki ei-numerot (väli, sitomaton väli \xa0 jne. tuhaterottimina)
        price = int(re.sub(r"\D", "", pm.group(1))) if pm else None
        if price == 0:  # "0 kr" = ei myyty
            price = None
            if status == "sold":
                status = "unsold"

        # PRECISION-suoja: aito erärivi sisältää AINA hinnan tai status-avainsanan.
        # Ilman tätä pressrelease-proosa (digit + yksittäinen H/S/V) tuottaisi roskaa.
        if price is None and status == "sold" and not _STATUS_RE.search(rest):
            continue

        # birth_year: regnr > yearling-sääntö (auction_year-1) > None (täytä DB-joinissa)
        if birth_year is None and is_yearling and auction_year:
            birth_year, by_source = auction_year - 1, "yearling_rule"

        rows.append({
            "auction_file": path.name,
            "auction_year": auction_year,
            "auction_type": auction_type,
            "nr": int(nr),
            "name": name.strip(),
            "sex": sex,
            "birth_year": birth_year,        # None → täytä DB-joinissa nimellä
            "birth_year_source": by_source,  # regnr / yearling_rule / None
            "regnr": regnr,
            "price_sek": price,
            "status": status,
            "tail": rest.strip(),            # Far Mor Säljare (Pris) Köpare — raakana
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="Käsittele vain N PDF:ää (0=kaikki)")
    args = ap.parse_args()

    with _client() as client:
        pdfs = find_result_pdfs(client)
        logger.info("Löytyi %d Resultat-PDF:ää tulossivulta", len(pdfs))
        if args.limit:
            pdfs = pdfs[: args.limit]

        all_rows: list[dict] = []
        for i, url in enumerate(pdfs, 1):
            path = download(client, url)
            if path is None:
                continue
            rows = parse_pdf(path)
            sold = sum(1 for r in rows if r["status"] == "sold" and r["price_sek"])
            logger.info("[%d/%d] %-45s %3d erää (%d myyty)", i, len(pdfs), path.name, len(rows), sold)
            all_rows.extend(rows)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    cols = ["auction_file", "auction_year", "auction_type", "nr", "name", "sex",
            "birth_year", "birth_year_source", "regnr", "price_sek", "status", "tail"]
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(all_rows)

    sold = [r for r in all_rows if r["status"] == "sold" and r["price_sek"]]
    logger.info("\nValmis: %d erää, %d myyty hinnalla → %s", len(all_rows), len(sold), OUT_CSV)
    if sold:
        prices = sorted(r["price_sek"] for r in sold)
        logger.info("Hinta SEK: min=%d  mediaani=%d  max=%d  | vuodet: %s",
                    prices[0], prices[len(prices) // 2], prices[-1],
                    sorted({r["auction_year"] for r in all_rows if r["auction_year"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
