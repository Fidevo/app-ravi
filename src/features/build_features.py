"""
Feature engineering ravimallille.

Kolme pääpiirrettä jotka raviasiantuntija on määritellyt tärkeiksi:
  1. Hevosen muoto (form)        -> form_features()
  2. Ohjastaja & valmentaja      -> driver_trainer_features()
  3. Lähtörata ja kilometriajat  -> race_setup_features()

Kaikki funktiot ottavat sisään pandas DataFramen ja palauttavat
lähtö × hevonen -tasoisen feature-matriisin.

HUOM: Vältä data leakage. Käytä vain dataa joka oli saatavilla
ENNEN kyseistä lähtöä (group by horse_id, expand backwards).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------
# 1. Hevosen muoto
# ----------------------------------------------------------------------

def form_features(runners: pd.DataFrame, n_last: int = 5) -> pd.DataFrame:
    """Lasketaan muotopiirteet kullekin runnerille viim. n_last startista.

    Olettaa että `runners` on järjestetty (horse_id, race_date) ja sisältää:
      - horse_id, race_date, finish_position, kilometer_time_seconds, win_odds_final

    Palauttaa per-runner-piirteet (NaN ensimmäisille starteille):
      - form_avg_finish_5      : viim. 5 startin keskimääräinen sijoitus
      - form_win_rate_5        : voitto-% viim. 5 startissa
      - form_top3_rate_5       : top-3-% viim. 5 startissa
      - form_avg_km_time_5     : keskimääräinen kilometriaika
      - form_best_km_time_5    : paras kilometriaika
      - form_days_since_last   : päiviä edellisestä startista (lepo)
      - form_market_avg_5      : keskimääräinen markkina-arvio (1/odds)
    """
    df = runners.sort_values(["horse_id", "race_date"]).copy()
    df["is_win"] = (df["finish_position"] == 1).astype(int)
    df["is_top3"] = (df["finish_position"] <= 3).astype(int)
    df["market_prob"] = 1.0 / df["win_odds_final"].replace(0, np.nan)

    # shift(1) jotta nykyinen lähtö ei vuoda piirteisiin
    grouped = df.groupby("horse_id", group_keys=False)

    df["form_avg_finish_5"] = grouped["finish_position"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
    )
    df["form_win_rate_5"] = grouped["is_win"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
    )
    df["form_top3_rate_5"] = grouped["is_top3"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
    )
    df["form_avg_km_time_5"] = grouped["kilometer_time_seconds"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
    )
    df["form_best_km_time_5"] = grouped["kilometer_time_seconds"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).min()
    )
    df["form_market_avg_5"] = grouped["market_prob"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
    )

    # Lepo päivissä
    df["prev_race_date"] = grouped["race_date"].shift(1)
    df["form_days_since_last"] = (
        pd.to_datetime(df["race_date"]) - pd.to_datetime(df["prev_race_date"])
    ).dt.days

    return df.drop(columns=["is_win", "is_top3", "market_prob", "prev_race_date"])


# ----------------------------------------------------------------------
# 2. Ohjastaja- ja valmentajatilastot
# ----------------------------------------------------------------------

def driver_trainer_features(
    runners: pd.DataFrame, lookback_days: int = 365
) -> pd.DataFrame:
    """Rolling-tilastot ohjastajalle ja valmentajalle ennen kutakin lähtöä.

    Käyttää 365 vuorokauden takaista ikkunaa (rolling), ei koko historiaa,
    jotta piirre heijastaa nykyistä iskussa olevaa muotoa.
    """
    df = runners.sort_values("race_date").copy()
    df["race_date"] = pd.to_datetime(df["race_date"])
    df["is_win"] = (df["finish_position"] == 1).astype(int)
    df["is_top3"] = (df["finish_position"] <= 3).astype(int)

    for role in ("driver", "trainer"):
        # rolling per role
        rolled = (
            df.set_index("race_date")
            .groupby(role)[["is_win", "is_top3"]]
            .rolling(f"{lookback_days}D", closed="left")
            .agg(["mean", "count"])
            .reset_index()
        )
        rolled.columns = [
            "race_date" if c[0] == "race_date" else f"{role}_{c[0]}_{c[1]}"
            for c in rolled.columns.to_flat_index()
        ]
        # Liitä takaisin runners-tasolle
        # (yksinkertaistettu - tuotannossa kannattaa indeksoida tarkemmin)
        df = df.merge(
            rolled.rename(columns={role: role}),
            on=["race_date", role],
            how="left",
        )

    return df.drop(columns=["is_win", "is_top3"])


# ----------------------------------------------------------------------
# 3. Lähtöasetelma: rata, lähtörata, kilometriaika-konteksti
# ----------------------------------------------------------------------

def race_setup_features(runners: pd.DataFrame, races: pd.DataFrame) -> pd.DataFrame:
    """Lähtörata ja rata-spesifiset piirteet.

    Lisää:
      - inside_post           : 1 jos lähtörata 1-3 (autostart edge)
      - back_row              : 1 jos takamatka volttilähdössä
      - distance_category     : lyhyt/keski/pitkä matka
      - track_horse_starts    : kuinka monta kertaa hevonen on aiemmin
                                ajanut tällä radalla (kokemus)
      - track_horse_win_rate  : voitto-% kyseisellä radalla
    """
    df = runners.merge(
        races[["race_id", "track", "distance", "start_method"]],
        on="race_id",
        how="left",
    )

    df["inside_post"] = (df["start_number"] <= 3).astype(int)
    df["back_row"] = (df["handicap_meters"].fillna(0) > 0).astype(int)

    df["distance_category"] = pd.cut(
        df["distance"],
        bins=[0, 1640, 2140, 5000],
        labels=["sprint", "middle", "long"],
    )

    # Hevosen historia kyseisellä radalla (ennen kyseistä lähtöä)
    df = df.sort_values(["horse_id", "race_date"])
    df["is_win"] = (df["finish_position"] == 1).astype(int)
    df["track_horse_starts"] = (
        df.groupby(["horse_id", "track"]).cumcount()
    )
    df["track_horse_wins_cum"] = (
        df.groupby(["horse_id", "track"])["is_win"]
        .cumsum()
        .shift(1)
        .fillna(0)
    )
    df["track_horse_win_rate"] = np.where(
        df["track_horse_starts"] > 0,
        df["track_horse_wins_cum"] / df["track_horse_starts"],
        np.nan,
    )

    return df.drop(columns=["is_win", "track_horse_wins_cum"])


# ----------------------------------------------------------------------
# Yhdistäjä
# ----------------------------------------------------------------------

def build_feature_matrix(
    runners: pd.DataFrame, races: pd.DataFrame
) -> pd.DataFrame:
    """Aja kaikki feature-funktiot ja palauta valmis matriisi mallille."""
    df = form_features(runners)
    df = driver_trainer_features(df)
    df = race_setup_features(df, races)
    return df
