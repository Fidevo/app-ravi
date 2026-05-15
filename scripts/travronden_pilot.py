"""Travronden Vaihe 2 -pilotti: kerää pre-race-piirteet vanhoilta kierroksilta.

Hakee n. 100 finished-kierrosta, parsii per-hevonen tr_*-piirteet ja
tallentaa CSV:ksi A/B-vertailua varten.

Kaikki haettu data cachettuu data/raw/travronden/{round,race}_*.json:nä
(30 vrk TTL) → pilottia voi jatkaa kesken tai ajaa uudelleen ilman extra-pyyntöjä.

Käyttö:
    # Perusajo — hae ~100 kierrosta automaattisesti
    python scripts/travronden_pilot.py

    # Tietyt round_id:t
    python scripts/travronden_pilot.py --round-ids 171800,171700,171000

    # Skannaa alue (hitaampaa)
    python scripts/travronden_pilot.py --scan-from 165000 --scan-to 171922 --scan-step 50

Tuottaa:
    data/travronden_pilot.csv — koottu pre-race-piirre-DataFrame
    data/travronden_pilot_stats.txt — kenttien kattavuustilastot
"""

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

# Polku löydetään myös suoraan ajettaessa (ei moduulina)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.scrapers.travronden import TravrondenAPIClient
from src.features.travronden_features import TRAVRONDEN_FEATURE_COLS, parse_travronden_race
from src.paths import DATA_DIR

# Known finished round_id:t (D1-tutkimuksesta ja manuaalisesta hausta).
# Kattaa 166000–171800 väliltä n. 18 kierrosta + laajempi skannattu joukko.
_KNOWN_FINISHED_IDS = [
    # Tuoreimmat (2026)
    171800, 171700, 171600, 171500, 171400, 171300,
    171200, 171100, 171000, 170800, 170500, 170000,
    # Vanhemmat (2025–2026)
    169500, 169000, 168500, 168000, 167500, 167000,
    166500, 166000,
    # Laajennus kattavuuteen (~100 kierrosta tavoite)
    165500, 165000, 164500, 164000, 163500, 163000,
    162500, 162000, 161500, 161000, 160500, 160000,
    159500, 159000, 158500, 158000, 157500, 157000,
    156500, 156000, 155500, 155000, 154500, 154000,
    153500, 153000, 152500, 152000, 151500, 151000,
    150500, 150000, 149500, 149000, 148500, 148000,
    147500, 147000, 146500, 146000, 145500, 145000,
    144500, 144000, 143500, 143000, 142500, 142000,
    141500, 141000, 140500, 140000, 139500, 139000,
    138500, 138000, 137500, 137000, 136500, 136000,
    135500, 135000, 134500, 134000, 133500, 133000,
    132500, 132000, 131500, 131000, 130500, 130000,
]


def collect_rounds(
    client: TravrondenAPIClient,
    round_ids: list[int],
    max_rounds: int = 100,
    max_legs_per_round: int = 10,
) -> pd.DataFrame:
    """Hae per-hevonen piirteet round_ids:n kierroksista.

    Args:
        client: TravrondenAPIClient-instanssi
        round_ids: haettavat round_id:t
        max_rounds: enimmäismäärä kierroksia (tavoite ~100)
        max_legs_per_round: enimmäismäärä legiä per kierros

    Returns:
        DataFrame jossa horse_id, round_id, race_id, race_date, race_number,
        track_key + kaikki tr_*-piirteet
    """
    all_rows: list[pd.DataFrame] = []
    rounds_ok = 0
    races_fetched = 0

    for rid in round_ids:
        if rounds_ok >= max_rounds:
            break

        try:
            rd = client.get_round(rid)
        except Exception as e:
            logger.warning("Round %d epäonnistui: %s", rid, e)
            continue

        if not rd:
            logger.debug("Round %d: tyhjä vastaus (404?)", rid)
            continue

        status = rd.get("status", "")
        if status not in ("analysed", "finished"):
            logger.debug("Round %d: status=%s — ohitetaan", rid, status)
            continue

        legs = rd.get("legs", []) or []
        round_date = rd.get("round_date", "")
        track_key = rd.get("track_key", "")

        logger.info("Round %d (%s %s) — %d legiä", rid, round_date, track_key, len(legs))

        for leg in legs[:max_legs_per_round]:
            race_id = leg.get("race")
            race_number = leg.get("race_number")
            if not race_id:
                continue

            try:
                race = client.get_race(race_id)
            except Exception as e:
                logger.warning("Race %d epäonnistui: %s", race_id, e)
                continue

            df = parse_travronden_race(race)
            if len(df) == 0:
                logger.debug("Race %d: ei starts-dataa", race_id)
                continue

            df["round_id"] = rid
            df["race_id_tr"] = race_id
            df["race_date"] = round_date
            df["race_number"] = race_number
            df["track_key"] = track_key

            all_rows.append(df)
            races_fetched += 1

        rounds_ok += 1

    if not all_rows:
        logger.warning("Ei dataa kerätty — tarkista round_id:t ja yhteys")
        return pd.DataFrame()

    result = pd.concat(all_rows, ignore_index=True)
    logger.info(
        "Kerätty: %d kierrosta, %d lähtöä, %d runner-riviä",
        rounds_ok, races_fetched, len(result),
    )
    return result


def print_coverage_stats(df: pd.DataFrame) -> None:
    """Tulosta kenttien täyttöaste stdoutiin."""
    print(f"\n{'='*60}")
    print(f"TRAVRONDEN PILOTTI — KATTAVUUSTILASTOT")
    print(f"{'='*60}")
    print(f"Runner-rivejä yhteensä: {len(df)}")
    print(f"Kierroksia: {df['round_id'].nunique()}")
    print(f"Lähtöjä:    {df['race_id_tr'].nunique()}")
    print(f"Päivämääräväli: {df['race_date'].min()} – {df['race_date'].max()}")
    print()
    print(f"{'Piirre':<40} {'notna%':>8} {'sample-arvot'}")
    print("-" * 75)
    for col in TRAVRONDEN_FEATURE_COLS:
        if col not in df.columns:
            print(f"  {col:<38} {'PUUTTUU':>8}")
            continue
        pct = df[col].notna().mean() * 100
        samples = df[col].dropna().unique()[:5]
        sample_str = str(list(samples))[:40]
        icon = "✅" if pct > 70 else ("🟡" if pct > 30 else "❌")
        print(f"  {icon} {col:<36} {pct:>6.1f}%  {sample_str}")
    print()


logger = logging.getLogger(__name__)


def main() -> int:
    ap = argparse.ArgumentParser(description="Travronden Vaihe 2 -pilotti")
    ap.add_argument("--round-ids", type=str, default=None,
                    help="Pilkulla erotettu lista round_id:tä")
    ap.add_argument("--scan-from", type=int, default=None)
    ap.add_argument("--scan-to",   type=int, default=None)
    ap.add_argument("--scan-step", type=int, default=100)
    ap.add_argument("--max-rounds", type=int, default=100)
    ap.add_argument("--out", type=Path,
                    default=DATA_DIR / "travronden_pilot.csv",
                    help="Tulostiedoston polku")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Päätä round_id-lista
    if args.round_ids:
        round_ids = [int(x.strip()) for x in args.round_ids.split(",")]
    elif args.scan_from and args.scan_to:
        round_ids = list(range(args.scan_from, args.scan_to + 1, args.scan_step))
        logger.info("Skannataan %d kandidaatti-id:tä", len(round_ids))
    else:
        round_ids = _KNOWN_FINISHED_IDS
        logger.info("Käytetään %d tunnettua finished-id:tä", len(round_ids))

    with TravrondenAPIClient() as client:
        df = collect_rounds(client, round_ids, max_rounds=args.max_rounds)

    if len(df) == 0:
        logger.error("Ei dataa — pilotti epäonnistui")
        return 1

    # Tallenna CSV
    df.to_csv(args.out, index=False)
    logger.info("Tallennettu: %s (%d riviä)", args.out, len(df))

    # Tilastot
    print_coverage_stats(df)

    # Tallenna stats myös tekstitiedostoon
    stats_path = args.out.with_suffix(".stats.txt")
    with open(stats_path, "w", encoding="utf-8") as f:
        f.write(f"Travronden pilotti stats\n")
        f.write(f"Runner-rivejä: {len(df)}\n")
        f.write(f"Kierroksia: {df['round_id'].nunique()}\n")
        for col in TRAVRONDEN_FEATURE_COLS:
            pct = df[col].notna().mean() * 100 if col in df.columns else 0
            f.write(f"  {col}: {pct:.1f}%\n")
    logger.info("Tilastot: %s", stats_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
