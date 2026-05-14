"""
Travronden Vaihe 1 — API-selvitys

Tehtävä: Hae vanhoja (status=finished) round_id:tä ja raportoi:
  1. Mitkä kentät (rating, speed, comment, interviews) on täytetty
  2. speed-kentän tyyppi ja skaala
  3. start_interval_group:n arvojen jakauma
  4. Esimerkkiarvot 2–3:sta hevosesta per löydetty kierros

Etsitään kierroksia laajemmalta alueelta jotta löydetään valmistuneita.
"""

import requests
import json
import time
import sys

hdrs = {
    "User-Agent": "ravit-edge research (jarkkom.lahde@gmail.com)",
    "Accept": "application/json",
}
BASE = "https://www.travrondenspel.se/api/v1/public"


def get_json(url, retries=2):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=hdrs, timeout=15)
            if r.status_code == 404:
                return None, 404
            if r.status_code == 200:
                return r.json(), 200
            return None, r.status_code
        except Exception as e:
            if attempt == retries:
                return None, f"error:{e}"
            time.sleep(2)


def safe_preview(val, maxlen=80):
    if val is None:
        return "None"
    s = str(val)
    return s[:maxlen] + "..." if len(s) > maxlen else s


print("=" * 70)
print("TRAVRONDEN VAIHE 1 — API-SELVITYS")
print("=" * 70)

# --- Vaihe 1a: Etsi finished-kierroksia ---
# Testataan laajempi ID-alue: tunnettu 171922 on 11.5.2026.
# Mennään taaksepäin kunnes löydytään riittävästi finished-kierroksia.
# Myös kokeillaan ids joita document suosittelee.

candidate_ids = [
    171800, 171700, 171600, 171500, 171400, 171300,
    171200, 171100, 171000, 170800, 170500, 170000,
    169500, 169000, 168500, 168000, 167000, 166000,
]

finished_rounds = []

print(f"\n--- Scanning {len(candidate_ids)} round_id:tä... ---\n")

for rid in candidate_ids:
    data, status = get_json(f"{BASE}/round/{rid}/")
    time.sleep(1)
    if data is None:
        print(f"  {rid}: HTTP {status}")
        continue
    s = data.get("status", "?")
    rd = data.get("round_date", "?")
    game = data.get("game_type", "?")
    legs = data.get("legs", [])
    print(f"  {rid}: status={s:12s} date={rd} game={game} legs={len(legs)}")
    if s in ("analysed", "finished") and legs:
        finished_rounds.append((rid, data))

print(f"\n-> Löytyi {len(finished_rounds)} finished/analysed kierrosta")

if not finished_rounds:
    print("\nEI LÖYDETTY yhtään finished-kierrosta kandidaateista!")
    print("Kokeillaan vielä laajemmalla otoksella...")
    for rid in range(165000, 170000, 500):
        data, status = get_json(f"{BASE}/round/{rid}/")
        time.sleep(1)
        if data is None:
            continue
        s = data.get("status", "?")
        rd = data.get("round_date", "?")
        legs = data.get("legs", [])
        print(f"  {rid}: status={s} date={rd} legs={len(legs)}")
        if s in ("analysed", "finished") and legs:
            finished_rounds.append((rid, data))
        if len(finished_rounds) >= 3:
            break

if not finished_rounds:
    print("KRIITTINEN: Ei löydetty yhtään finished-kierrosta. Tarkista API manuaalisesti.")
    sys.exit(1)

# --- Vaihe 1b: Tutki finished-kierroksia tarkemmin ---
print("\n" + "=" * 70)
print("DETAILED FIELD ANALYSIS — FINISHED ROUNDS")
print("=" * 70)

all_interval_groups = []
field_fill = {
    "rating": 0, "speed": 0, "comment": 0, "interviews": 0,
    "expected_odds": 0, "ranking": 0, "preliminary_equipment": 0,
}
field_total = 0
speed_values = []
rating_values = []
interval_values = []

for round_idx, (rid, round_data) in enumerate(finished_rounds[:5]):
    print(f"\n--- ROUND {rid} (status={round_data.get('status')}, date={round_data.get('round_date')}, game={round_data.get('game_type')}) ---")
    legs = round_data.get("legs", [])
    print(f"    Legs (races): {len(legs)}")

    for leg_idx, leg in enumerate(legs[:2]):  # tarkista 2 ensimmäistä legiä
        race_id = leg.get("race")
        race_number = leg.get("race_number", "?")
        if not race_id:
            continue
        print(f"\n    Leg {leg_idx+1}: race_id={race_id}, race_number={race_number}")

        race_data, rstatus = get_json(f"{BASE}/race/{race_id}/")
        time.sleep(1)
        if race_data is None:
            print(f"      HTTP {rstatus}")
            continue

        starts = race_data.get("starts", []) or []
        print(f"    Starts (hevoset): {len(starts)}")

        for si, s in enumerate(starts[:4]):  # 4 ensimmäistä hevosta
            field_total += 1
            horse = s.get("horse") or {}
            atg_id = horse.get("atg_id")
            horse_name = horse.get("name", "?")

            rating = s.get("rating")
            speed = s.get("speed")
            comment = s.get("comment")
            interviews = s.get("interviews")
            expected_odds = s.get("expected_odds")
            ranking = s.get("ranking")
            prelim_eq = s.get("preliminary_equipment")
            sig = s.get("start_interval_group")

            if rating is not None:
                field_fill["rating"] += 1
                rating_values.append(rating)
            if speed is not None:
                field_fill["speed"] += 1
                speed_values.append(speed)
            if comment is not None and str(comment).strip():
                field_fill["comment"] += 1
            if interviews is not None and len(interviews or []) > 0:
                field_fill["interviews"] += 1
            if expected_odds is not None:
                field_fill["expected_odds"] += 1
            if ranking is not None:
                field_fill["ranking"] += 1
            if prelim_eq is not None:
                field_fill["preliminary_equipment"] += 1
            if sig is not None:
                interval_values.append(sig)

            print(f"\n      Start {si+1}: horse={horse_name} (atg_id={atg_id})")
            print(f"        rating={safe_preview(rating)}")
            print(f"        speed={safe_preview(speed)} [type={type(speed).__name__}]")
            print(f"        ranking={safe_preview(ranking)}")
            print(f"        expected_odds={safe_preview(expected_odds)}")
            print(f"        start_interval_group={safe_preview(sig)}")
            print(f"        comment={safe_preview(comment)}")
            if interviews:
                print(f"        interviews=[{len(interviews)} item(s)]")
                for iv in (interviews or [])[:1]:
                    print(f"          -> {safe_preview(iv)}")
            else:
                print(f"        interviews=None/[]")
            print(f"        is_first_new_driver={s.get('is_first_new_driver')}")
            print(f"        is_first_after_castration={s.get('is_first_after_castration')}")
            print(f"        is_first_shoes={s.get('is_first_shoes')}")
            # game_percent
            gp = s.get("game_percent") or {}
            gp_atg = gp.get("providers", {}).get("ATG", {})
            if gp_atg:
                print(f"        game_percent keys={list(gp_atg.keys())[:5]}")
                for k, v in list(gp_atg.items())[:2]:
                    print(f"          {k}: {safe_preview(v)}")
            # speed_records
            sr = horse.get("speed_records") or {}
            if sr:
                print(f"        speed_records keys={list(sr.keys())}")
                for code, rec in list(sr.items())[:2]:
                    print(f"          {code}: {safe_preview(rec)}")

print("\n" + "=" * 70)
print("YHTEENVETO — KENTTIEN TÄYTTÖASTE")
print("=" * 70)
print(f"Analysoitu yhteensä {field_total} runner-riviä\n")

for field, count in field_fill.items():
    pct = (count / field_total * 100) if field_total else 0
    bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
    status = "✅" if pct > 70 else ("🟡" if pct > 20 else "❌")
    print(f"  {status} {field:30s} {count:3d}/{field_total} ({pct:5.1f}%) {bar}")

print()
if speed_values:
    print(f"speed-arvojen tyyppi: {type(speed_values[0]).__name__}")
    print(f"speed min={min(speed_values)} max={max(speed_values)} unique={sorted(set(speed_values))[:20]}")
else:
    print("speed: ei yhtään arvoa — kenttä tyhjä kaikilla")

if rating_values:
    print(f"\nrating-arvojen tyyppi: {type(rating_values[0]).__name__}")
    print(f"rating min={min(rating_values)} max={max(rating_values)} unique={sorted(set(rating_values))[:20]}")
else:
    print("\nrating: ei yhtään arvoa — kenttä tyhjä kaikilla")

if interval_values:
    print(f"\nstart_interval_group min={min(interval_values)} max={max(interval_values)}")
    from collections import Counter
    top = Counter(interval_values).most_common(10)
    print(f"  yleisimmät: {top}")
else:
    print("\nstart_interval_group: kaikki None")

print("\n" + "=" * 70)
print("PÄÄTÖS")
print("=" * 70)
if field_fill["speed"] > 0 and field_fill["rating"] > 0:
    print("✅ speed JA rating täytetty — jatka Vaiheeseen 2 (pilotti)")
elif field_fill["speed"] > 0:
    print("🟡 speed täytetty mutta rating tyhjä — speed saattaa riittää C3:lle")
elif field_fill["rating"] > 0:
    print("🟡 rating täytetty mutta speed tyhjä — asiantuntija-rating saatavilla")
else:
    print("❌ speed JA rating molemmat tyhjät — vain 'varmasti saatavilla' -kentät hyödyllisiä")
    print("   Hyödylliset: is_first_*, game_percent, speed_records, start_interval_group")
