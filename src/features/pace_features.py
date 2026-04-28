"""
Pace- ja juoksuvire-piirteet.

VAROITUS — V2-MODUULI, EI KÄYTETÄ MALLIN ENSIMMÄISESSÄ VERSIOSSA:
    Tämä moduuli vaatii per-race position_at_800m -dataa joka EI ole
    saatavilla nykyisistä ATG- tai Travsport-API-endpointeista
    (vahvistettu 2026-04-27: ATG horse.statistics ei sisällä per-startti
    -dataa, Travsport /results-endpoint ei palauta asema- tai
    juoksuvire-kenttiä). Käyttöönotto vaatii erillistä DevTools-
    tutkimusta - todennäköisesti jokin per-race endpoint Travsportin
    SPA:ssa, jota emme ole vielä löytäneet.

Tämä on RAVIASIANTUNTIJAN valinta numero 1: lähdön tempo ja hevosten
asemointi ratkaisee enemmän kuin pelkkä form. Sama hevonen joka tekee
4. tilan johdosta vs. kuolemanpaikalta on käytännössä eri hevonen.

KOLME PÄÄKOMPONENTTIA:
  1. Juoksuvire (running style) per hevonen - mitä se tekee lähdössä
  2. Pace-pressure-ennuste per lähtö - kuinka kova tempo on tulossa
  3. Kuolemanpaikan rangaistus historiastarttien analyysissä

DATAN LÄHDE:
  Travsport.se hevossivut sisältävät jokaisesta startista:
    - Asema 800m / 500m / maalissa
    - Vire (esim. "j" = johdossa, "s" = selässä, "u" = ulkona)
    - Kilometriaika

  Tämä pitää scrapata - ATG:n API ei anna tätä detaljitasoa.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

# Juoksuvire-luokat. Travsportin lyhenteet vaihtelevat, alla normalisoidut.
RunningStyle = Literal["leader", "stalker", "presser", "closer", "trailer"]


def classify_running_style(position_at_800m: int, finish_position: int) -> RunningStyle:
    """Yksinkertainen luokitus 800m aseman ja sijoituksen perusteella.

    leader   : 1-2. asemassa 800m:llä (kärkipakka)
    stalker  : 3-4. asemassa, voi hyökätä
    presser  : 5-6. asemassa, takaa-ajaja
    closer   : 7+ asemassa mutta kiri loppumetreille parani
    trailer  : 7+ asemassa, ei kiriä
    """
    if position_at_800m <= 2:
        return "leader"
    elif position_at_800m <= 4:
        return "stalker"
    elif position_at_800m <= 6:
        return "presser"
    elif finish_position < position_at_800m:
        return "closer"
    return "trailer"


def horse_running_style_distribution(
    history: pd.DataFrame, n_last: int = 8
) -> pd.DataFrame:
    """Laske kunkin hevosen juoksuvirejakauma viim. n_last startista.

    Olettaa että `history` sisältää sarakkeet:
      horse_id, race_date, position_at_800m, finish_position

    Palauttaa per-hevonen-piirteet:
      style_leader_pct, style_stalker_pct, style_presser_pct,
      style_closer_pct, style_trailer_pct,
      style_dominant   (yleisin tyyli, kategorinen piirre)
    """
    df = history.sort_values(["horse_id", "race_date"]).copy()
    df["style"] = df.apply(
        lambda r: classify_running_style(
            r["position_at_800m"], r["finish_position"]
        ),
        axis=1,
    )

    # Rolling viim. n_last starttia per hevonen
    df["rn"] = df.groupby("horse_id").cumcount(ascending=False)
    recent = df[df["rn"] < n_last]

    style_dummies = pd.get_dummies(recent["style"], prefix="style")
    grouped = pd.concat([recent[["horse_id"]], style_dummies], axis=1)
    distribution = grouped.groupby("horse_id").mean()
    distribution.columns = [f"{c}_pct" for c in distribution.columns]

    distribution["style_dominant"] = distribution.idxmax(axis=1).str.replace(
        "style_", ""
    ).str.replace("_pct", "")

    return distribution.reset_index()


def race_pace_pressure(
    runners_in_race: pd.DataFrame, post_position_col: str = "start_number"
) -> dict[str, float]:
    """Ennusta lähdön tempo hevosten juoksuvirejakaumasta.

    LOGIIKKA:
      - Useita "leadereita" sisäradoilla → kova tempo, takaa-ajajat hyötyvät
      - Yksi selvä leader yksin → keulahevonen saa rauhassa juosta
      - Paljon "closer"-tyyppejä → pace pehmeä, lopputaisto ratkaisee

    Olettaa runners_in_race sisältää piirteet horse_running_style_distribution:sta.
    """
    if "style_leader_pct" not in runners_in_race.columns:
        return {"pace_score": np.nan, "leader_count": np.nan}

    # Painota sisäradan hevosia (etu autostarttilähdössä)
    weights = np.where(
        runners_in_race[post_position_col] <= 4,
        1.5,
        1.0,
    )

    weighted_leaders = (runners_in_race["style_leader_pct"] * weights).sum()
    n_likely_leaders = (runners_in_race["style_leader_pct"] > 0.4).sum()

    # Pace score: 0 = pehmeä, 1 = kova
    if n_likely_leaders == 0:
        pace_score = 0.0
    elif n_likely_leaders == 1:
        pace_score = 0.2
    elif n_likely_leaders == 2:
        pace_score = 0.5
    elif n_likely_leaders == 3:
        pace_score = 0.8
    else:
        pace_score = 1.0

    return {
        "pace_score": pace_score,
        "leader_count": float(n_likely_leaders),
        "weighted_leader_signal": float(weighted_leaders),
        "closer_count": float((runners_in_race["style_closer_pct"] > 0.3).sum()),
    }


def death_position_penalty(history: pd.DataFrame) -> pd.DataFrame:
    """Säädä historiastarttien sijoitusta sen mukaan ajoiko hevonen kuolemanpaikalta.

    "Kuolemanpaikka" = ulkona ilman vetoapua viim. kierroksen.
    Travsportissa tämä on usein merkitty väline-/asema-sarakkeessa
    (esim "u" = ulkona, "uu" = kahden parista ulkona).

    Tuottaa kaksi piirrettä per historiastartti:
      - finish_position_adjusted : sijoitus säädettynä (parempi jos vaikea asema)
      - was_death_position       : 1/0
    """
    df = history.copy()

    # Travsport-konventiot - säädä kun tiedät tarkat lyhenteet
    death_indicators = ["u", "uu", "ud"]  # ulkona, ulkona ulkona, ulkona dödesposition
    df["was_death_position"] = df["position_indicator"].isin(death_indicators).astype(int)

    # Säätö: kuolemanpaikalta tehty sijoitus on käytännössä 1.5-2 sijaa parempi
    df["finish_position_adjusted"] = np.where(
        df["was_death_position"] == 1,
        df["finish_position"] - 1.5,
        df["finish_position"],
    )
    df["finish_position_adjusted"] = df["finish_position_adjusted"].clip(lower=1)

    return df


def kilometer_time_normalized(history: pd.DataFrame) -> pd.DataFrame:
    """Normalisoi kilometriaika rata × etäisyys × lähtötapa -kontekstiin.

    Raakakilometriaika valehtelee: 1.13 lyhyellä matkalla autostartista
    on huonompi kuin 1.14 pitkällä matkalla volttilähdöstä.

    Lasketaan z-score per (track, distance_bucket, start_method) -ryhmä.
    """
    df = history.copy()
    df["distance_bucket"] = pd.cut(
        df["distance"],
        bins=[0, 1640, 2140, 5000],
        labels=["sprint", "middle", "long"],
    )

    df["km_time_zscore"] = df.groupby(
        ["track", "distance_bucket", "start_method"]
    )["kilometer_time_seconds"].transform(
        lambda s: (s - s.mean()) / s.std() if s.std() > 0 else 0
    )
    # Käännä etumerkki: nopeampi (negatiivinen z) -> positiivinen "speed_score"
    df["speed_score"] = -df["km_time_zscore"]
    return df


def build_pace_features(
    runners: pd.DataFrame, history: pd.DataFrame
) -> pd.DataFrame:
    """Yhdistä kaikki pace-piirteet yhteen.

    Args:
        runners: nykyiset lähtökortin runnerit
        history: hevosten historiastartit (sis. position_at_800m,
                 position_indicator, jne.)
    """
    # 1. Hevoskohtaiset juoksuvire-piirteet historiasta
    style_features = horse_running_style_distribution(history)
    df = runners.merge(style_features, on="horse_id", how="left")

    # 2. Lähtökohtainen pace-pressure
    pace_features_per_race = (
        df.groupby("race_id", group_keys=False)
        .apply(lambda g: pd.Series(race_pace_pressure(g)))
        .reset_index()
    )
    df = df.merge(pace_features_per_race, on="race_id", how="left")

    return df
