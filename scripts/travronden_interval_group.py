"""
start_interval_group -selvitys (auditoijan pyyntö)

Kysymys: tarkoittaako start_interval_group...
  (a) hevosen historiallinen pace-luokka (sama arvo eri kierroksilla)
  (b) kilpailun pace-asetelma (sama arvo kaikille lähdön hevosille)
  (c) Travrondenin per-hevonen pace-arvio (vaihtelee hevoskohtaisesti)

Metodi:
  - Hae 10 finished-kierrosta eri päiviltä
  - Kerää kaikki (horse_atg_id, round_id, race_id, start_interval_group) -rivit
  - Etsi hevoset jotka esiintyvät >= 2 kierroksella
  - Tarkista: onko start_interval_group sama vai eri?
  - Tarkista myös: vaihteleeko arvo saman lähdön sisällä eri hevosten välillä?
"""
import requests
import time
import json
from collections import defaultdict

hdrs = {"User-Agent": "ravit-edge research (jarkkom.lahde@gmail.com)"}
BASE = "https://www.travrondenspel.se/api/v1/public"

ROUND_IDS = [
    171800, 171700, 171600, 171500, 171400,
    171300, 171200, 171100, 171000, 170800,
]


def get_json(url):
    try:
        r = requests.get(url, headers=hdrs, timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


print("Haetaan start_interval_group -dataa...")

# horse_atg_id -> list of (round_id, race_id, group)
horse_records = defaultdict(list)
# race_id -> list of group values (kaikki saman lähdön hevoset)
race_groups = defaultdict(list)

rows_collected = 0

for rid in ROUND_IDS:
    rd = get_json(f"{BASE}/round/{rid}/")
    time.sleep(1)
    if not rd or rd.get("status") != "finished":
        continue

    print(f"  Round {rid} ({rd.get('round_date')}) — {len(rd.get('legs', []))} legiä")

    for leg in rd.get("legs", [])[:3]:   # max 3 legiä per kierros
        race_id = leg.get("race")
        if not race_id:
            continue
        race = get_json(f"{BASE}/race/{race_id}/")
        time.sleep(1)
        if not race:
            continue

        for start in race.get("starts", []) or []:
            horse = start.get("horse") or {}
            atg_id = horse.get("atg_id")
            group = start.get("start_interval_group")
            if atg_id and atg_id != 0:
                horse_records[atg_id].append((rid, race_id, group))
                rows_collected += 1
            if group is not None:
                race_groups[race_id].append(group)

print(f"\nKerätty {rows_collected} rivejä {len(horse_records)} eri hevoselta")
print(f"Lähtöjä: {len(race_groups)}\n")

# --- Analyysi 1: vaihteleeko group saman lähdön sisällä? ---
print("=" * 60)
print("ANALYYSI 1: Vaihteleeko group saman lähdön sisällä?")
print("=" * 60)
race_unique_groups = {r: set(g for g in groups if g is not None)
                      for r, groups in race_groups.items()}
races_with_multiple = {r: g for r, g in race_unique_groups.items() if len(g) > 1}
races_with_single   = {r: g for r, g in race_unique_groups.items() if len(g) == 1}
races_with_none     = {r: g for r, g in race_groups.items()
                       if all(x is None for x in g)}

print(f"Lähdöt joissa group vaihtelee (eri hevosilla eri arvo): {len(races_with_multiple)}")
print(f"Lähdöt joissa kaikilla sama group: {len(races_with_single)}")
print(f"Lähdöt joissa kaikki None: {len(races_with_none)}")

# Näytä esimerkkejä
print("\nEsimerkkejä VAIHTELEVISTA lähdöistä:")
for race_id, groups in list(races_with_multiple.items())[:5]:
    all_groups_in_race = [g for g in race_groups[race_id] if g is not None]
    print(f"  race={race_id}: {sorted(all_groups_in_race)}")

print("\nEsimerkkejä YHTENÄISISTÄ lähdöistä:")
for race_id, groups in list(races_with_single.items())[:5]:
    print(f"  race={race_id}: kaikki {list(groups)[0]}")

# --- Analyysi 2: onko sama hevonen eri kierroksilla eri ryhmässä? ---
print("\n" + "=" * 60)
print("ANALYYSI 2: Sama hevonen eri kierroksilla — pysyykö group?")
print("=" * 60)

multi_round_horses = {hid: recs for hid, recs in horse_records.items() if len(recs) >= 2}
print(f"Hevosia joilla >= 2 kierrosta datassa: {len(multi_round_horses)}")

stable_count = 0
varying_count = 0
always_none_count = 0
examples_varying = []
examples_stable = []

for atg_id, recs in multi_round_horses.items():
    groups = [g for _, _, g in recs if g is not None]
    if not groups:
        always_none_count += 1
        continue
    unique = set(groups)
    if len(unique) == 1:
        stable_count += 1
        if len(examples_stable) < 3:
            examples_stable.append((atg_id, recs))
    else:
        varying_count += 1
        if len(examples_varying) < 5:
            examples_varying.append((atg_id, recs))

print(f"\nTulos:")
print(f"  Sama group joka kierroksella (stable): {stable_count}")
print(f"  Eri group eri kierroksilla  (varying): {varying_count}")
print(f"  Kaikki None (ei dataa): {always_none_count}")
total_multi = stable_count + varying_count + always_none_count
if total_multi:
    print(f"  Stabiili-%: {stable_count/total_multi*100:.1f}%")

if examples_varying:
    print(f"\nEsimerkkejä VAIHTELEVISTA hevosista (atg_id → [(round, race, group)]):")
    for atg_id, recs in examples_varying:
        print(f"  Hevonen {atg_id}:")
        for rid, race_id, g in recs:
            print(f"    round={rid}, race={race_id}, group={g}")

if examples_stable:
    print(f"\nEsimerkkejä STABIILEISTA hevosista:")
    for atg_id, recs in examples_stable:
        print(f"  Hevonen {atg_id}: group={recs[0][2]} (joka kierroksella sama)")

# --- Yhteenveto ---
print("\n" + "=" * 60)
print("TULKINTA")
print("=" * 60)
if varying_count > stable_count * 0.3:
    print("→ Tulkinta (b) tai (c): group VAIHTELEE lähdöstä toiseen")
    print("  = ei pelkästään historiallinen luokka, vaan per-lähtö tai per-kierros -arvio")
    if len(races_with_multiple) > len(races_with_single):
        print("  → Lisäksi vaihtelee SAMAN LÄHDÖN SISÄLLÄ → per-hevonen arvio (tulkinta c)")
    else:
        print("  → Saman lähdön sisällä kaikilla sama → per-lähtö asetelma (tulkinta b)")
else:
    print("→ Tulkinta (a): group on STABIILI hevoskohtaisesti")
    print("  = historiallinen pace-luokka, ei lähtökohtainen tieto")
    if len(races_with_multiple) > len(races_with_single) * 0.5:
        print("  HUOM: silti vaihtelua joissakin lähdöissä — ehkä päivitytyy harvoin")
