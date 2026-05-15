"""Travrondenspel pre-race-piirteiden ekstrahointi ja yhdistäminen.

Vain pre-race-kelpoiset kentät — EI leakage-vaarallisia:
  ✅ start_interval_group  (asiantuntija-pace-arvio, pre-race)
  ✅ is_first_*            (signaalit, pre-race)
  ✅ game_percent          (markkinasentimentti, pre-race)
  ✅ expected_odds         (Travrondenin kerroinennuste, pre-race)
  ✅ speed_records.K/M/L  (historialliset ennätykset, pre-race)
  ❌ speed                 (tässä lähdössä saavutettu km-aika, POST-RACE)
  ❌ comment               (jälkikommentti, POST-RACE)
  ❌ placement/result      (tulos, POST-RACE)

Yhdistys runners-DataFrameen horse_id-avaimella:
  Travronden: horse.atg_id (int)
  Runners:    horse_id (str)  ←→  str(atg_id)

LEFT JOIN: jos hevosta ei ole Travronden-datassa (ei V-pelissä),
kaikki tr_*-kentät NaN. LightGBM käsittelee NaN:t automaattisesti.

Nimeämiskonventio: kaikki kentät tr_-etuliitteellä erottuakseen
ATG/Travsport-piirteistä. Näin `_resolve_cols` ja FEATURE_COLS hallinta
on selkeää.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Travronden-piirteet FEATURE_COLS:iin lisättäväksi (kun pilotti hyväksytty)
# tr_start_interval_group lisätään kategorisena (1/11/21/31) tai numeerisena
TRAVRONDEN_FEATURE_COLS: list[str] = [
    "tr_start_interval_group",      # ⭐⭐⭐ pace-arvio (1=nopein, 31=hitain)
    "tr_is_first_after_castration", # ⭐⭐ tunnettu prediktiivinen signaali
    "tr_is_first_new_driver",       # ⭐⭐ ohjastajan vaihto
    "tr_is_first_new_trainer",      # ⭐ valmentajan vaihto
    "tr_is_first_shoes",            # ⭐ kenkämuutos
    "tr_is_first_carriage",         # ⭐ sulkymuutos
    "tr_speed_record_k",            # ⭐⭐ paras km-aika sprint (s, ei×100)
    "tr_speed_record_m",            # ⭐⭐ paras km-aika middle
    "tr_speed_record_l",            # ⭐⭐ paras km-aika long
    "tr_expected_odds",             # ⭐ Travrondenin kerroinennuste (ei×100)
    "tr_game_percent_v",            # ⭐ V-pelin markkinaprosentti (0–100)
]


def parse_travronden_race(race_response: dict[str, Any]) -> pd.DataFrame:
    """Pura per-runner pre-race-piirteet yhden lähdön API-vastauksesta.

    Args:
        race_response: /race/{race_id}/ -vastauksen JSON-dict

    Returns:
        DataFrame jossa yksi rivi per hevonen, sarakkeet: horse_id + tr_*
        Tyhjä DataFrame jos starts-lista puuttuu tai on tyhjä.
    """
    starts = race_response.get("starts") or []
    if not starts:
        return pd.DataFrame(columns=["horse_id"] + TRAVRONDEN_FEATURE_COLS)

    rows = []
    for s in starts:
        horse = s.get("horse") or {}
        atg_id = horse.get("atg_id")
        if not atg_id or atg_id == 0:
            continue   # ei tunnistamatonta

        row: dict[str, Any] = {"horse_id": str(atg_id)}

        # --- Pace-arvio ---
        row["tr_start_interval_group"] = s.get("start_interval_group")

        # --- Signaalipiirteet (bool → 0/1/None) ---
        row["tr_is_first_after_castration"] = _to_int_flag(s.get("is_first_after_castration"))
        row["tr_is_first_new_driver"]       = _to_int_flag(s.get("is_first_new_driver"))
        row["tr_is_first_new_trainer"]      = _to_int_flag(s.get("is_first_new_trainer"))
        row["tr_is_first_shoes"]            = _to_int_flag(s.get("is_first_shoes"))
        row["tr_is_first_carriage"]         = _to_int_flag(s.get("is_first_carriage"))

        # --- Speed records (×100 → sekuntia, esim. 7490 → 74.90 s) ---
        sr = horse.get("speed_records") or {}
        row["tr_speed_record_k"] = _parse_speed_record(sr.get("K"))
        row["tr_speed_record_m"] = _parse_speed_record(sr.get("M"))
        row["tr_speed_record_l"] = _parse_speed_record(sr.get("L"))

        # --- Kerroin ja markkinasentimentti ---
        eo = s.get("expected_odds")
        row["tr_expected_odds"] = float(eo) / 100.0 if eo is not None else None

        row["tr_game_percent_v"] = _parse_game_percent(s.get("game_percent"))

        rows.append(row)

    if not rows:
        return pd.DataFrame(columns=["horse_id"] + TRAVRONDEN_FEATURE_COLS)

    df = pd.DataFrame(rows)
    # Varmista että kaikki sarakkeet ovat läsnä (vaikka API-rakenne muuttuisi)
    for col in TRAVRONDEN_FEATURE_COLS:
        if col not in df.columns:
            df[col] = np.nan
    return df[["horse_id"] + TRAVRONDEN_FEATURE_COLS]


def merge_travronden_features(
    runners: pd.DataFrame,
    travronden_df: pd.DataFrame,
) -> pd.DataFrame:
    """Yhdistä Travronden-piirteet runners-DataFrameen LEFT JOIN:lla.

    Jos sama horse_id esiintyy travronden_df:ssä useammin kuin kerran
    (esim. sama hevonen kahdessa eri V-pelissä samana päivänä), otetaan
    viimeisin rivi (keep="last") — molemmat ovat identtisiä pre-race-dataa.

    Args:
        runners: runners-DataFrame jossa horse_id-sarake
        travronden_df: parse_travronden_race():n tai pilottikollektiosta
                       koottu DataFrame (horse_id + tr_*-sarakkeet)

    Returns:
        runners-DataFrame täydennettynä tr_*-sarakkeilla (NaN jos ei matchaa)
    """
    if travronden_df is None or len(travronden_df) == 0:
        logger.debug("Travronden-data tyhjä — ei yhdistetä mitään")
        # Lisää NaN-sarakkeet jotta downstream-koodi ei kaadu puuttuviin sarakkeisiin
        for col in TRAVRONDEN_FEATURE_COLS:
            runners = runners.copy()
            runners[col] = np.nan
        return runners

    # Deduplikoi horse_id:n mukaan — pidä viimeisin (yleensä identtinen)
    tr_dedup = travronden_df.drop_duplicates(subset=["horse_id"], keep="last")

    # Varmista yhteensopiva tyyppi
    runners = runners.copy()
    runners["horse_id"] = runners["horse_id"].astype(str)
    tr_dedup = tr_dedup.copy()
    tr_dedup["horse_id"] = tr_dedup["horse_id"].astype(str)

    result = runners.merge(tr_dedup, on="horse_id", how="left")

    n_matched = result["tr_start_interval_group"].notna().sum() if "tr_start_interval_group" in result.columns else 0
    logger.debug(
        "Travronden-yhdistys: %d/%d runneria sai tr_*-piirteet",
        n_matched, len(result),
    )
    return result


# ---------------------------------------------------------------------------
# Yksityiset apufunktiot
# ---------------------------------------------------------------------------

def _to_int_flag(val: Any) -> int | None:
    """Muunna bool/None → 0/1/None."""
    if val is None:
        return None
    return int(bool(val))


def _parse_speed_record(rec: Any) -> float | None:
    """Pura speed_records.{K/M/L}-objektista km-aika sekunteina.

    Travronden tallentaa ajan int×100 (esim. 7490 = 74.90 s/km).
    Muunnetaan sekunteiksi jotta skaala on yhteensopiva atg_best_km_for_this_setup:n kanssa.

    Huom: atg_best_km_for_this_setup on sekunteina (esim. 74.9).
    """
    if not isinstance(rec, dict):
        return None
    sp = rec.get("speed")
    if sp is None:
        return None
    try:
        return float(sp) / 100.0
    except (TypeError, ValueError):
        return None


def _parse_game_percent(gp: Any) -> float | None:
    """Pura game_percent.providers.ATG.V*-arvo prosentteina (0–100).

    Rakenne: {"providers": {"ATG": {"V64": {"percent": 2498, ...}, ...}}}
    Percent on int×100 (2498 = 24.98 %). Muunnetaan 0–100-skaalaan.

    Otetaan ensimmäinen numeerinen V-pelin prosentti (V64 > V75 > V86 > V5 > V4).
    """
    if not isinstance(gp, dict):
        return None
    atg = gp.get("providers", {}).get("ATG", {})
    if not atg:
        return None
    # Prioriteettijärjestys: isoin V-peli ensin (eniten pelattu → paras signaali)
    for key in ("V75", "V86", "V64", "V5", "V4", "V3"):
        v = atg.get(key)
        if isinstance(v, dict):
            pct = v.get("percent")
            if pct is not None:
                try:
                    return float(pct) / 100.0
                except (TypeError, ValueError):
                    pass
    return None
