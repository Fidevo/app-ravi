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

# Races-taulun aina läsnä olevat sarakkeet race_setup_features()-mergessä.
_RACE_COLS_BASE = ["race_id", "track", "distance", "start_method"]
# Lisäsarakkeet: otetaan mukaan vain jos ne löytyvät races-DataFramesta.
# Näin backward-yhteensopivuus säilyy vanhojen testitietojen kanssa.
_RACE_COLS_EXTRA = ["track_condition", "race_min_earnings", "race_max_earnings", "race_age_group"]


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

    Käyttää lookback_days vuorokauden takaista ikkunaa (rolling), ei koko
    historiaa, jotta piirre heijastaa nykyistä iskussa olevaa muotoa.

    Korjattu (bugit #5a ja #5b):
      - Ei käytetä .reset_index().to_flat_index() -kombinaatiota, joka
        kaatui IndexErroriin kun ensimmäinen kolumni (rooli) oli merkkijono
        eikä tuple.
      - drop_duplicates ennen mergeä estää M:N-riviräjähdyksen kun sama
        ohjastaja ajaa useita lähtöjä samana päivänä (closed="left" antaa
        saman rolling-tuloksen kaikille saman päivän lähdöille).
    """
    df = runners.sort_values("race_date").copy()
    df["race_date"] = pd.to_datetime(df["race_date"])
    df["is_win"] = (df["finish_position"] == 1).astype(int)
    df["is_top3"] = (df["finish_position"] <= 3).astype(int)

    for role in ("driver", "trainer"):
        # Rolling-aggregaatti per rooli aikaindeksillä, closed="left" = ei leakagea
        agg = (
            df.set_index("race_date")
            .groupby(role)[["is_win", "is_top3"]]
            .rolling(f"{lookback_days}D", closed="left")
            .agg(["mean", "count"])
        )
        # Indeksi on MultiIndex (role_value, race_date) — nimetään eksplisiittisesti
        # ilman to_flat_index()-ongelmia. Sarakkeiden järjestys: agg() palauttaa
        # (is_win, mean), (is_win, count), (is_top3, mean), (is_top3, count).
        agg.index.names = [role, "race_date"]
        agg.columns = [
            f"{role}_win_rate_{lookback_days}d",   # is_win mean
            f"{role}_starts_{lookback_days}d",      # is_win count (= nähdyt starttimäärät ikkunassa)
            f"{role}_top3_rate_{lookback_days}d",  # is_top3 mean
            f"{role}_top3_count_{lookback_days}d", # is_top3 count
        ]
        agg = agg.reset_index()

        # Poistetaan duplikaatit ennen mergeä:
        # jos ohjastaja ajaa N lähtöä samana päivänä, closed="left" antaa saman
        # rolling-tuloksen kaikille → tarvitaan vain yksi rivi per (role, päivä).
        # Ilman tätä merge tekee N×N-ristitulon → rivimäärä räjähtää.
        agg = agg.drop_duplicates(subset=[role, "race_date"])

        df = df.merge(agg, on=["race_date", role], how="left")

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

    Korjattu (bugi #8):
      - track_horse_wins_cum käyttää nyt .transform(lambda s: s.cumsum().shift(1))
        eikä .cumsum().shift(1). groupby().cumsum() palauttaa tavallisen Seriesin,
        joten perässä tuleva .shift(1) oli globaali — edellisen (horse_id, track)
        -ryhmän viimeinen kumulatiivinen summa vuoti seuraavan ryhmän ensimmäiseen
        riviin. Tämä näkyy virheellisenä win_ratena kun hevonen vuorottelee ratojen
        välillä (esim. Solvalla-Bergsåker-Solvalla → 2. Solvalla-startti sai väärän
        wins_cum-arvon). .transform() pitää laskun ryhmän sisällä.
    """
    race_cols = _RACE_COLS_BASE + [c for c in _RACE_COLS_EXTRA if c in races.columns]
    df = runners.merge(races[race_cols], on="race_id", how="left")

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
    # Korjattu: transform() pitää cumsum+shift ryhmän sisällä eikä vuoda
    # edellisen (horse_id, track) -ryhmän arvoja seuraavan alkuun.
    df["track_horse_wins_cum"] = (
        df.groupby(["horse_id", "track"])["is_win"]
        .transform(lambda s: s.cumsum().shift(1))
        .fillna(0)
    )
    df["track_horse_win_rate"] = np.where(
        df["track_horse_starts"] > 0,
        df["track_horse_wins_cum"] / df["track_horse_starts"],
        np.nan,
    )

    return df.drop(columns=["is_win", "track_horse_wins_cum"])


# ----------------------------------------------------------------------
# 4. Johdetut piirteet
# ----------------------------------------------------------------------

def derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Laskee piirteet jotka voidaan johtaa suoraan olemassa olevasta datasta.

    Ei vaadi ulkoisia lähteitä — kaikki lasketaan muista sarakkeista.

    Lisää:
      barfota_law_active : 1 jos talvikielto (1.12.–28.2.), muuten 0.
        ATG ei raportoi barfota-tietoa talvikieltoaikana → shoes_*=NULL.
        Ilman tätä piirrettä malli ei tiedä johtuuko NULL talvikiellosta
        vai siitä että hevosella todella on kengät.

      horse_age : hevosen ikä kilpailupäivänä (race_date.year - birth_year).
        Lasketaan vain jos birth_year on df:ssä — muuten ohitetaan hiljaisesti.
        birth_year saadaan JOIN:lla horses-tauluun ennen build_feature_matrix()-
        kutsua. Puuttuva birth_year → horse_age puuttuu → train_ranker() ohittaa
        sen FEATURE_COLS:ista automaattisesti.
    """
    dates = pd.to_datetime(df["race_date"])

    # Talvikielto: joulukuu, tammikuu tai helmikuu
    df["barfota_law_active"] = (
        (dates.dt.month == 12) | (dates.dt.month <= 2)
    ).astype(int)

    # Hevosen ikä: lasketaan vain kun birth_year on saatavilla
    if "birth_year" in df.columns:
        df["horse_age"] = dates.dt.year - df["birth_year"].astype("Int64")

    return df


# ----------------------------------------------------------------------
# Yhdistäjä
# ----------------------------------------------------------------------

def build_feature_matrix(
    runners: pd.DataFrame, races: pd.DataFrame
) -> pd.DataFrame:
    """Aja kaikki feature-funktiot ja palauta valmis matriisi mallille.

    Piirre-pipeline järjestyksessä:
      1. form_features       — hevosen muoto viim. N startista
      2. driver_trainer_features — rolling-tilastot ohjastajalle/valmentajalle
      3. race_setup_features — lähtörata, rata-kokemus, lähdön luokka
      4. derived_features    — johdetut piirteet (barfota, horse_age)

    Valinnainen horse_age-piirre: lisää birth_year runners-DataFrameen
    JOIN:lla horses-tauluun ennen tätä kutsua.
    """
    df = form_features(runners)
    df = driver_trainer_features(df)
    df = race_setup_features(df, races)
    df = derived_features(df)
    return df
