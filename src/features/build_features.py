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


# Sarakkeet joita tarvitaan muotolaskentaan sekä runners- että horse_starts-tauluista.
_POOL_COLS = [
    "horse_id", "race_date", "finish_position",
    "kilometer_time_seconds", "win_odds_final",
]

# Lisäsarakkeet segmentoituihin muotopiirteisiin (B2). Nämä haetaan pooliin
# vain jos ne löytyvät syötedatasta — taaksepäin-yhteensopiva.
_POOL_COLS_SEGMENTED = ["start_method", "distance"]

# Muotolaskennan tulossarakkeet (siirretään runners:iin mergellä)
_FORM_OUT_COLS = [
    "horse_id", "race_date",
    "form_avg_finish_5", "form_win_rate_5", "form_top3_rate_5",
    "form_avg_km_time_5", "form_best_km_time_5",
    "form_market_avg_5", "form_days_since_last",
    # B2: segmentoidut muotopiirteet (lisätään vain jos sarakkeet löytyivät poolista)
    "form_avg_finish_5_same_method", "form_avg_finish_5_same_dist",
]


# ----------------------------------------------------------------------
# 1. Hevosen muoto
# ----------------------------------------------------------------------

def form_features(
    runners: pd.DataFrame,
    horse_starts: pd.DataFrame | None = None,
    n_last: int = 5,
) -> pd.DataFrame:
    """Lasketaan muotopiirteet kullekin runnerille viim. n_last startista.

    Olettaa että `runners` sisältää:
      - horse_id, race_date, finish_position, kilometer_time_seconds, win_odds_final

    Valinnainen `horse_starts`-parametri (Travsport-historia):
      Jos annetaan, yhdistetään runners- ja horse_starts-data yhdeksi pooliksi
      ennen rolling-laskentaa. Tämä antaa merkittävästi enemmän historiaa
      form-piirteiden laskentaan — erityisesti alkuvaiheessa kun runners-taulussa
      on dataa vain 14 päivän ajalta.

      Deduplikaatio: jos sama (horse_id, race_date) löytyy molemmista,
      runners-taulun arvo säilytetään (runners on master-data).

      Ei data leakagea: shift(1) ennen rolling() varmistaa että
      kyseisen lähdön tulos ei vuoda omiin piirteisiinsä.

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
    df["race_date"] = pd.to_datetime(df["race_date"])
    df["horse_id"] = df["horse_id"].astype(str)

    # B2: sisällytä start_method ja distance pooliin jos saatavilla
    seg_cols_avail = [c for c in _POOL_COLS_SEGMENTED if c in df.columns]
    pool_cols_full = _POOL_COLS + seg_cols_avail

    # --- Rakenna pool: runners + valinnainen horse_starts-historia ---
    current = df[pool_cols_full].copy()
    current["_is_runner"] = True

    if horse_starts is not None and len(horse_starts) > 0:
        # Ota vain sarakkeet jotka on saatavilla myös horse_starts:ssa
        hist_cols = [c for c in pool_cols_full if c in horse_starts.columns]
        hist = horse_starts[hist_cols].copy()
        hist["race_date"] = pd.to_datetime(hist["race_date"])
        hist["horse_id"] = hist["horse_id"].astype(str)
        hist["_is_runner"] = False
        # Yhdistä: hist ensin, sitten current — drop_duplicates(keep="last")
        # säilyttää runners-arvon kun sama (horse_id, race_date) löytyy molemmista.
        combined = pd.concat([hist, current], ignore_index=True)
        combined = combined.sort_values(
            ["horse_id", "race_date", "_is_runner"]
        ).reset_index(drop=True)
        combined = combined.drop_duplicates(
            subset=["horse_id", "race_date"], keep="last"
        )
    else:
        combined = current.copy()

    combined = combined.sort_values(["horse_id", "race_date"]).reset_index(drop=True)

    # --- Apusarakkeet laskentaan (float jotta NaN toimii oikein) ---
    combined["_is_win"] = (combined["finish_position"] == 1).astype(float)
    combined["_is_top3"] = (combined["finish_position"] <= 3).astype(float)
    combined["_market_prob"] = 1.0 / combined["win_odds_final"].replace(0, np.nan)

    # shift(1) jotta nykyinen lähtö ei vuoda piirteisiin
    grouped = combined.groupby("horse_id", group_keys=False)

    combined["form_avg_finish_5"] = grouped["finish_position"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
    )
    combined["form_win_rate_5"] = grouped["_is_win"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
    )
    combined["form_top3_rate_5"] = grouped["_is_top3"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
    )
    combined["form_avg_km_time_5"] = grouped["kilometer_time_seconds"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
    )
    combined["form_best_km_time_5"] = grouped["kilometer_time_seconds"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).min()
    )
    combined["form_market_avg_5"] = grouped["_market_prob"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
    )

    # Lepo päivissä
    combined["_prev_race_date"] = grouped["race_date"].shift(1)
    combined["form_days_since_last"] = (
        combined["race_date"] - combined["_prev_race_date"]
    ).dt.days

    # --- B2: Segmentoidut muotopiirteet ---
    # form_avg_finish_5_same_method: rolling 5 vain samalla starttimuodolla (auto/volt)
    # form_avg_finish_5_same_dist:   rolling 5 vain samalla matkaluokalla (sprint/middle/long)
    # Lasketaan vain jos start_method/distance löytyivät poolista.
    if "start_method" in combined.columns:
        grouped_method = combined.groupby(
            ["horse_id", "start_method"], group_keys=False
        )
        combined["form_avg_finish_5_same_method"] = grouped_method[
            "finish_position"
        ].transform(lambda s: s.shift(1).rolling(n_last, min_periods=1).mean())
    else:
        combined["form_avg_finish_5_same_method"] = np.nan

    if "distance" in combined.columns:
        combined["_dist_bucket"] = pd.cut(
            combined["distance"],
            bins=[0, 1999, 2599, 5000],
            labels=["sprint", "middle", "long"],
        )
        grouped_dist = combined.groupby(
            ["horse_id", "_dist_bucket"], group_keys=False, observed=True
        )
        combined["form_avg_finish_5_same_dist"] = grouped_dist[
            "finish_position"
        ].transform(lambda s: s.shift(1).rolling(n_last, min_periods=1).mean())
        combined = combined.drop(columns=["_dist_bucket"])
    else:
        combined["form_avg_finish_5_same_dist"] = np.nan

    # --- Palauta vain runner-rivit, liitä form-piirteet runners:iin ---
    # Suodata _FORM_OUT_COLS:sta vain sarakkeet jotka löytyvät combined:sta
    out_cols = [c for c in _FORM_OUT_COLS if c in combined.columns]
    runner_form = (
        combined[combined["_is_runner"]][out_cols]
        .drop_duplicates(subset=["horse_id", "race_date"])
    )
    return df.merge(runner_form, on=["horse_id", "race_date"], how="left")


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
# 2b. Ohjastaja- ja valmentajatilastot horse_starts-taulusta (60d)
# ----------------------------------------------------------------------

def driver_trainer_hs_features(
    runners_df: pd.DataFrame,
    horse_starts_df: pd.DataFrame,
    lookback_days: int = 60,
    min_starts: int = 3,
) -> pd.DataFrame:
    """Laske kuljettajan ja valmentajan win%/top3% horse_starts-taulusta.

    Point-in-time: käytetään vain starteja joiden race_date < runner.race_date
    (EI <=) jotta saman päivän startit eivät vuoda.

    Ikkuna: viimeiset lookback_days päivää ennen kyseistä lähtöä.

    Args:
        runners_df: DataFrame jossa sarakkeet race_id, horse_id, driver, trainer,
                    race_date — yksi rivi per runner jota ennustetaan.
        horse_starts_df: koko horse_starts-taulu (Travsport-historia).
                         Vaaditut sarakkeet: driver, trainer, finish_position, race_date.
        lookback_days: aikaikkuna päivinä (oletus 60).
        min_starts: vähimmäisstarttimäärä — alle tämän → NaN (oletus 3).

    Returns:
        DataFrame sarakkeilla [race_id, horse_id,
            driver_win_rate_60d, driver_top3_rate_60d,
            trainer_win_rate_60d, trainer_top3_rate_60d].
        NaN jos alle min_starts starttia ikkunassa.
    """
    runners_df = runners_df.copy()
    runners_df["race_date"] = pd.to_datetime(runners_df["race_date"])

    hs = horse_starts_df.copy()
    hs["race_date"] = pd.to_datetime(hs["race_date"])
    hs["is_win"]  = (hs["finish_position"] == 1).astype(float)
    hs["is_top3"] = (hs["finish_position"] <= 3).astype(float)

    suffix = f"{lookback_days}d"
    results = []

    for role in ("driver", "trainer"):
        win_col  = f"{role}_win_rate_{suffix}"
        top3_col = f"{role}_top3_rate_{suffix}"

        if role not in runners_df.columns or role not in hs.columns:
            # Sarake puuttuu — tuota NaN-sarakkeet
            runners_df[win_col]  = np.nan
            runners_df[top3_col] = np.nan
            continue

        # Valmistele historia-DataFrame: vain tarpeelliset sarakkeet
        hist = hs[["race_date", role, "is_win", "is_top3"]].copy()

        # Merge: yhdistä runners_df × hist roolisarakkeen mukaan
        # Jokaiselle runner-riville löydetään kaikki historian rivit
        # joilla sama driver/trainer.
        merged = runners_df[["race_id", "horse_id", "race_date", role]].merge(
            hist,
            on=role,
            suffixes=("_runner", "_hist"),
            how="left",
        )

        # Point-in-time filter: vain startit ennen kyseistä lähtöpäivää
        # JA ikkunan sisällä
        cutoff_early = (
            merged["race_date_hist"] < merged["race_date_runner"]
        )
        cutoff_late = (
            merged["race_date_hist"]
            >= merged["race_date_runner"] - pd.Timedelta(days=lookback_days)
        )
        in_window = merged[cutoff_early & cutoff_late].copy()

        # Aggregoi per (race_id, horse_id)
        agg = (
            in_window.groupby(["race_id", "horse_id"])
            .agg(
                _n_starts=("is_win", "count"),
                _wins=("is_win", "sum"),
                _top3=("is_top3", "sum"),
            )
            .reset_index()
        )

        # win_rate / top3_rate — NaN jos alle min_starts
        agg[win_col] = np.where(
            agg["_n_starts"] >= min_starts,
            agg["_wins"] / agg["_n_starts"],
            np.nan,
        )
        agg[top3_col] = np.where(
            agg["_n_starts"] >= min_starts,
            agg["_top3"] / agg["_n_starts"],
            np.nan,
        )

        results.append(agg[["race_id", "horse_id", win_col, top3_col]])

    # Liitä tulokset runners_df:ään
    out = runners_df[["race_id", "horse_id"]].copy()
    for agg_df in results:
        out = out.merge(agg_df, on=["race_id", "horse_id"], how="left")

    # Varmista että kaikki sarakkeet ovat olemassa (vaikka role puuttuisi)
    for role in ("driver", "trainer"):
        for metric in ("win_rate", "top3_rate"):
            col = f"{role}_{metric}_{suffix}"
            if col not in out.columns:
                out[col] = np.nan

    return out


# ----------------------------------------------------------------------
# 3. Lähtöasetelma: rata, lähtörata, kilometriaika-konteksti
# ----------------------------------------------------------------------

def race_setup_features(
    runners: pd.DataFrame,
    races: pd.DataFrame,
    horse_starts: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Lähtörata ja rata-spesifiset piirteet.

    Lisää:
      - inside_post           : 1 jos lähtörata 1-3 (autostart edge)
      - back_row              : 1 jos takamatka volttilähdössä
      - distance_category     : lyhyt/keski/pitkä matka
      - track_horse_starts    : kuinka monta kertaa hevonen on aiemmin
                                ajanut tällä radalla (kokemus)
      - track_horse_win_rate  : voitto-% kyseisellä radalla

    Valinnainen horse_starts-parametri (B1-korjaus):
      Jos annetaan, yhdistetään runners- ja horse_starts-data pooliksi
      track-historiatietojen laskentaan. Tämä laajentaa track_horse_starts
      ja track_horse_win_rate laskennan runners-taulun 14 päivästä koko
      urahistoriaan (103k starttia). Muuttaa NaN-% ~97.5 % → ~15 %.

      Deduplikaatio: sama (horse_id, race_date, track) → runners voittaa.

    Korjattu (bugi #8):
      - track_horse_wins_cum käyttää nyt .transform(lambda s: s.cumsum().shift(1))
        eikä .cumsum().shift(1). groupby().cumsum() palauttaa tavallisen Seriesin,
        joten perässä tuleva .shift(1) oli globaali — edellisen (horse_id, track)
        -ryhmän viimeinen kumulatiivinen summa vuoti seuraavan ryhmän ensimmäiseen
        riviin. .transform() pitää laskun ryhmän sisällä.
    """
    race_cols = _RACE_COLS_BASE + [c for c in _RACE_COLS_EXTRA if c in races.columns]
    # A1-korjaus: build_feature_matrix() saattaa olla jo pre-mergannut start_method
    # ja distance runnersiin (jotta form_features() saa ne B2-laskentaan). Suodata
    # ne pois race_cols:ista jotta merge ei tee _x/_y-suffix-konfliktia.
    cols_not_in_runners = [
        c for c in race_cols if c == "race_id" or c not in runners.columns
    ]
    df = runners.merge(races[cols_not_in_runners], on="race_id", how="left")

    df["inside_post"] = (df["start_number"] <= 3).astype(int)
    df["back_row"] = (df["handicap_meters"].fillna(0) > 0).astype(int)

    df["distance_category"] = pd.cut(
        df["distance"],
        bins=[0, 1999, 2599, 5000],
        labels=["sprint", "middle", "long"],
    )

    # B1: Rakenna track-historiapooli runners + horse_starts
    # Tarvitsemme: horse_id, race_date, track, finish_position
    pool_current = df[["horse_id", "race_date", "track", "finish_position"]].copy()
    pool_current["horse_id"] = pool_current["horse_id"].astype(str)
    pool_current["race_date"] = pd.to_datetime(pool_current["race_date"])
    pool_current["_is_runner"] = True

    if horse_starts is not None and len(horse_starts) > 0 and "track" in horse_starts.columns:
        from src.data.track_codes import TRACKCODE_TO_NAME
        pool_hist = horse_starts[["horse_id", "race_date", "track", "finish_position"]].copy()
        pool_hist["horse_id"] = pool_hist["horse_id"].astype(str)
        pool_hist["race_date"] = pd.to_datetime(pool_hist["race_date"])
        # A2-korjaus: normalisoi Travsport-koodit ATG:n rataniksi.
        # horse_starts.track on lyhennekoodeja ("S", "Ax" jne.) mutta
        # races.track (= pool_current.track) on ATG-nimiä ("Solvalla" jne.).
        # Ilman normalisointia drop_duplicates ja groupby eivät tunnista
        # samaa rataa — track_horse_starts ja track_horse_win_rate pysyvät 0.4 %.
        pool_hist["track"] = pool_hist["track"].map(
            lambda t: TRACKCODE_TO_NAME.get(t, t) if t is not None else None
        )
        pool_hist["_is_runner"] = False
        # hist ensin, current viimeisenä → drop_duplicates(keep="last") säilyttää runners-arvon
        pool = pd.concat([pool_hist, pool_current], ignore_index=True)
        pool = pool.sort_values(
            ["horse_id", "race_date", "_is_runner"]
        ).reset_index(drop=True)
        pool = pool.drop_duplicates(subset=["horse_id", "race_date", "track"], keep="last")
    else:
        pool = pool_current.copy()

    pool = pool.sort_values(["horse_id", "track", "race_date"]).reset_index(drop=True)
    pool["is_win"] = (pool["finish_position"] == 1).astype(float)

    # track_horse_starts = kumulatiivinen laskuri per (horse_id, track), shift(1) = ennen tätä lähtöä
    pool["track_horse_starts"] = pool.groupby(["horse_id", "track"]).cumcount()
    # Korjattu: transform() pitää cumsum+shift ryhmän sisällä eikä vuoda ratojen välillä
    pool["track_horse_wins_cum"] = (
        pool.groupby(["horse_id", "track"])["is_win"]
        .transform(lambda s: s.cumsum().shift(1))
        .fillna(0)
    )
    pool["track_horse_win_rate"] = np.where(
        pool["track_horse_starts"] > 0,
        pool["track_horse_wins_cum"] / pool["track_horse_starts"],
        np.nan,
    )

    # Palauta vain runner-rivit ja liitä track-piirteet runners-DataFrameen
    runner_pool = pool[pool["_is_runner"]][
        ["horse_id", "race_date", "track", "track_horse_starts", "track_horse_win_rate"]
    ].drop_duplicates(subset=["horse_id", "race_date", "track"])

    df["horse_id"] = df["horse_id"].astype(str)
    df["race_date"] = pd.to_datetime(df["race_date"])
    df = df.merge(
        runner_pool,
        on=["horse_id", "race_date", "track"],
        how="left",
    )

    return df


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
    df = df.copy()  # P2-korjaus: ei mutaatiota callersin DataFrameen
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
# 5. Sukutaulupiirteet (B2 — Vaihe B)
# ----------------------------------------------------------------------

# Pienin otos-raja sire/dam_sire-aggregaateille. Alle tämän → NaN.
# Liian pieni otos antaa kohinaisen estimaatin (overfitting yksilötasolle).
_SIRE_MIN_STARTS = 30


def sire_features(
    runners: pd.DataFrame,
    horses: pd.DataFrame,
    horse_starts: pd.DataFrame,
) -> pd.DataFrame:
    """Lisää sire/dam_sire-aggregaatit runner-riveille (leave-one-out).

    Lasketaan horse_starts-koko-uradata:sta (ei rajoitettu 14 päivään).
    Käytetään **leave-one-out (LOO)** -laskentaa: hevosen omat startit
    vähennetään sen isäoriin kokonaisaggregaatista ennen win-rate-laskentaa.
    Tämä estää "indirektin leakagen" jossa hevosen oma menestys nostaa
    sen sire-ratea, joka kertoo "tämä hevonen on hyvä" eikä "tämä sire on hyvä".

    Piirteet:
      sire_lifetime_win_rate     : isäoriin muiden jälkeläisten voitto-% (LOO)
      sire_lifetime_starts       : muiden jälkeläisten starttimäärä (LOO-pohja)
      dam_sire_lifetime_win_rate : emänisän muiden jälkeläisten voitto-% (LOO)
      dam_sire_lifetime_starts   : muiden jälkeläisten starttimäärä (LOO-pohja)

    Pienet sample-koot suodatetaan:
      Jos LOO-starttimäärä < _SIRE_MIN_STARTS (30), asetetaan
      win_rate = NaN (kohina eikä signaali). LightGBM käsittelee NaN:t
      automaattisesti puuttuvana arvona.

    Args:
        runners: DataFrame jossa horse_id (lähdöt joita ennustetaan)
        horses: DataFrame jossa horse_id, sire, dam_sire
        horse_starts: Travsport-historia (103k+ starttia, kaikki hevoset)

    Returns:
        runners-DataFrame lisättyinä sire/dam_sire-sarakkeilla
    """
    pedigree = horses[["horse_id", "sire", "dam_sire"]].drop_duplicates("horse_id")

    # --- 1. Liitä sire/dam_sire horse_starts-riveihin horses-taulusta ---
    starts = horse_starts.merge(pedigree, on="horse_id", how="left")
    starts["is_win"] = (starts["finish_position"] == 1).astype(float)

    # ------------------------------------------------------------------ #
    # Apufunktio: laske LOO-aggregaatit yhdelle pedigree-sarakkeelle      #
    # ------------------------------------------------------------------ #
    def _loo_stats(
        starts_df: pd.DataFrame,
        group_col: str,
        prefix: str,
    ) -> pd.DataFrame:
        """Laske leave-one-out sire/dam_sire-stats per (horse_id, group_col).

        Returns:
            DataFrame jossa sarakkeet horse_id, group_col,
            {prefix}_lifetime_starts, {prefix}_lifetime_win_rate.
        """
        valid = starts_df.dropna(subset=[group_col]).copy()

        # Per-group kokonaisaggregaatti (kaikkien jälkeläisten startit)
        group_totals = (
            valid.groupby(group_col)
            .agg(total_starts=("is_win", "count"), total_wins=("is_win", "sum"))
            .reset_index()
        )

        # Per-(horse_id, group) oma kontribuutio
        own_contrib = (
            valid.groupby(["horse_id", group_col])
            .agg(own_starts=("is_win", "count"), own_wins=("is_win", "sum"))
            .reset_index()
        )

        # Yhdistä: group-kokonaisuus + oma kontribuutio per hevonen
        merged = own_contrib.merge(group_totals, on=group_col, how="left")

        # LOO: vähennetään hevosen omat startit/voitot
        merged["loo_starts"] = merged["total_starts"] - merged["own_starts"]
        merged["loo_wins"]   = merged["total_wins"]   - merged["own_wins"]

        # Win-rate: NaN jos alle minimikynnyksen (kohinainen estimaatti)
        starts_col = f"{prefix}_lifetime_starts"
        rate_col   = f"{prefix}_lifetime_win_rate"
        merged[starts_col] = merged["loo_starts"]
        merged[rate_col] = np.where(
            merged["loo_starts"] >= _SIRE_MIN_STARTS,
            merged["loo_wins"] / merged["loo_starts"].replace(0, np.nan),
            np.nan,
        )

        return merged[["horse_id", group_col, starts_col, rate_col]]

    # --- 2. Sire-LOO ---
    sire_loo = _loo_stats(starts, "sire", "sire")

    # --- 3. Dam_sire-LOO ---
    dam_sire_loo = _loo_stats(starts, "dam_sire", "dam_sire")

    # --- 4. Liitä runners:iin ---
    df = runners.merge(pedigree, on="horse_id", how="left")
    df = df.merge(sire_loo[["horse_id", "sire_lifetime_starts",
                             "sire_lifetime_win_rate"]],
                  on="horse_id", how="left")
    df = df.merge(dam_sire_loo[["horse_id", "dam_sire_lifetime_starts",
                                 "dam_sire_lifetime_win_rate"]],
                  on="horse_id", how="left")

    return df


# ----------------------------------------------------------------------
# 6. Ratarakenne (track_structure_features)
# ----------------------------------------------------------------------

def track_structure_features(
    runners: pd.DataFrame,
    tracks: pd.DataFrame,
) -> pd.DataFrame:
    """Liitä rata-rakennepiirteet runners-DataFrameen.

    Avain: runners.track (saatavilla race_setup_features():n jälkeen —
    race_setup_features() mergaa track:n races-taulusta runners:iin).

    LEFT JOIN tracks-tauluun: jos rata puuttuu (esim. gallop-rata tai
    manuaalinen stub ilman rakennetietoja), kaikki tr_*-sarakkeet ovat NaN.
    LightGBM käsittelee NaN:t automaattisesti.

    Sarakkeet jotka lisätään:
      track_length_total      radan kokonaispituus (m)
      track_home_stretch_m    loppusuoran pituus (m) — kriittisin piirre
      track_open_stretch      onko toinen passing-linja (int 0/1)
      track_angled_wing       kaltevat keulakaaret autostartille (int 0/1)
      track_width_1           sisempi leveys (autostart-mittaus)
      track_width_2           ulompi leveys
      track_dosage            kaarteen kallistus (raaka luku)

    Toleroi puuttuvia sarakkeita: jos tracks-DataFramessa ei ole jotain
    saraketta (esim. vanha tietokanta ilman dosage-kenttää), se sivuutetaan
    ja vastaava piirresarake jätetään pois. Näin backward-yhteensopivuus
    säilyy.

    Args:
        runners: DataFrame jossa on sarake 'track' (saatu race_setup_features():lta).
        tracks:  DataFrame joka vastaa tracks-taulua (sqlalchemy Track-mallin kenttänimet).

    Returns:
        runners-DataFrame lisättyjen piirresarakkeiden kanssa (rivimäärä ei muutu).
    """
    # Sarakkeet tracks-taulussa → piirrenimi runners-DataFramessa
    COL_MAP = {
        "length_total": "track_length_total",
        "length_home_stretch": "track_home_stretch_m",
        "open_stretch": "track_open_stretch",
        "angled_wing": "track_angled_wing",
        "width_1": "track_width_1",
        "width_2": "track_width_2",
        "dosage": "track_dosage",
    }

    # Ota vain ne sarakkeet jotka löytyvät syöttedatasta (+ pääavain)
    available = {src: dst for src, dst in COL_MAP.items() if src in tracks.columns}
    if not available:
        # tracks-DataFrame tyhjä tai sarakkeet puuttuvat kokonaan — ei mergattavaa
        return runners

    t = (
        tracks[["track_name"] + list(available.keys())]
        .rename(columns={"track_name": "track", **available})
    )

    # Defensiivinen duplikaattisuojaus: track_name on PK (schema suojaa), mutta
    # jos ladatussa DataFramessa jostain syystä duplikaattirivejä, merge räjäyttäisi
    # runners-rivimäärän. Halvempi tarkistaa kuin selvitellä jälkeenpäin.
    t = t.drop_duplicates(subset=["track"], keep="last")

    # Boolean-sarakkeet → int (0/1) jotta NaN:it myöhemmin selkeitä
    for bool_col in ("track_open_stretch", "track_angled_wing"):
        if bool_col in t.columns:
            t[bool_col] = t[bool_col].astype("Int64")  # nullable int → NaN säilyy

    return runners.merge(t, on="track", how="left")


# ----------------------------------------------------------------------
# 7. Treeniesiesimerkkien esikäsittely — puuttuvien sijoitusten täyttö
# ----------------------------------------------------------------------

def fill_finish_positions(runners: pd.DataFrame) -> pd.DataFrame:
    """Täyttää puuttuvat finish_position-arvot ennen mallin treenausta.

    ATG raportoi sijoitukset vain top 6–8 hevoselle per lähtö. Hevoset jotka
    ajoivat mutta eivät sijoittuneet saavat NULL:n finish_position-sarakkeeseen
    vaikka heillä on kilometer_time_seconds. Tämä tekee LambdaRank-treenauksesta
    epäluotettavaa (vajaat järjestykset).

    Täyttölogiikka per lähtö (race_id):
      1. Viralliset sijoitukset (1–N) säilytetään muuttumattomina.
      2. Hevoset jotka ajoivat (kilometer_time_seconds IS NOT NULL, finish=NULL):
         järjestetään km-ajan mukaan nousevasti (nopein saa parhaan sijoituksen)
         ja sijoitetaan heti virallisten jälkeen (N+1, N+2, ...).
      3. Vetäytyneet/peruuntuneet (sekä finish että km_time NULL):
         sijoitetaan viimeisiksi järjestyksessä (km_aika ei tiedossa).
      4. Lähdöt joissa KAIKKI sijoitukset ovat NULL (tulevat lähdöt) jätetään
         koskemattomiksi.

    HUOM: Kutsu tätä vain koulutusaineistolla. Ennustamisessa (live-data)
    finish_position ei ole koskaan tiedossa — siellä tätä ei tarvita.

    Args:
        runners: DataFrame jossa on sarakkeet race_id, finish_position,
                 kilometer_time_seconds. Muut sarakkeet läpäistään sellaisinaan.

    Returns:
        Kopio DataFramesta jossa finish_position täytetty kaikille riveille
        joissa lähtö on ajettu. Rivimäärä ei muutu.
    """
    # Kopio jota muokataan suoraan .loc:lla — vältetään groupby().apply()
    # -sarakkeen katoamisongelma eri pandas-versioissa.
    df = runners.copy()

    for _race_id, group in df.groupby("race_id"):
        # Jos kaikki NULL → lähtöä ei ole ajettu vielä, ei muutoksia
        if group["finish_position"].isna().all():
            continue

        next_pos = int(group["finish_position"].max()) + 1

        # Ajoi mutta ei sijoitusta: järjestä km_ajan mukaan (nopein ensin)
        ran_mask = (
            group["finish_position"].isna()
            & group["kilometer_time_seconds"].notna()
        )
        if ran_mask.any():
            for idx in group[ran_mask].sort_values("kilometer_time_seconds").index:
                df.loc[idx, "finish_position"] = next_pos
                next_pos += 1

        # Vetäytyneet (ei km_aikaa eikä sijoitusta): viimeiset
        withdrawn_mask = (
            group["finish_position"].isna()
            & group["kilometer_time_seconds"].isna()
        )
        if withdrawn_mask.any():
            for idx in group[withdrawn_mask].index:
                df.loc[idx, "finish_position"] = next_pos
                next_pos += 1

    return df


# ----------------------------------------------------------------------
# Yhdistäjä
# ----------------------------------------------------------------------

def build_feature_matrix(
    runners: pd.DataFrame,
    races: pd.DataFrame,
    horse_starts: pd.DataFrame | None = None,
    horses: pd.DataFrame | None = None,
    tracks: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Aja kaikki feature-funktiot ja palauta valmis matriisi mallille.

    Piirre-pipeline järjestyksessä:
      1. form_features             — hevosen muoto viim. N startista
      2. driver_trainer_features   — rolling-tilastot ohjastajalle/valmentajalle
      3. race_setup_features       — lähtörata, rata-kokemus, lähdön luokka
      4. track_structure_features  — ratarakenne (loppusuora, leveys jne.)
      5. derived_features          — johdetut piirteet (barfota, horse_age)
      6. sire_features             — sukutaulupiirteet (B2, valinnainen)

    Valinnainen horse_starts-parametri: Travsport-historia koko uralta.
    Jos annetaan, form_features() käyttää sitä laajentamaan muotodatan
    kattavuutta runners-taulun 14 päivän sijaan koko uraan.

    Valinnainen horses-parametri: horses-taulu jossa sire/dam_sire-kentät.
    Jos annetaan horse_starts:n kanssa, lasketaan sukutaulupiirteet (B2).

    Valinnainen tracks-parametri: tracks-taulu rakennepiirteineen.
    Jos annetaan, liitetään ratarakenne (track_length_total,
    track_home_stretch_m, jne.) runners:iin LEFT JOIN:lla.

    Valinnainen horse_age-piirre: lisää birth_year runners-DataFrameen
    JOIN:lla horses-tauluun ennen tätä kutsua.
    """
    # A1-korjaus: pre-merge start_method ja distance races-taulusta runners:iin
    # ENNEN form_features()-kutsua. Ilman tätä seg_cols_avail=[] aina koska
    # runners-taulussa ei ole näitä sarakkeita — ne ovat races-taulussa.
    # Tämä mahdollistaa B2-segmentoidut piirteet (form_avg_finish_5_same_method,
    # form_avg_finish_5_same_dist) jotka olivat 100 % NaN tuotannossa.
    # horse_starts:ssa nämä sarakkeet ovat jo natiivisti (Travsport tallentaa ne).
    race_meta_cols = ["race_id"]
    for c in ("start_method", "distance"):
        if c in races.columns and c not in runners.columns:
            race_meta_cols.append(c)
    if len(race_meta_cols) > 1:
        runners_with_meta = runners.merge(
            races[race_meta_cols], on="race_id", how="left"
        )
    else:
        runners_with_meta = runners

    # A1b-korjaus: normalisoi horse_starts.start_method Travsport-koodeista
    # ATG-nimiksi ENNEN form_features()-kutsua.
    # Travsport: "A"=auto, "V"=volte, "L"=auto (harvinainen)
    # ATG/races: "auto", "volte"
    # Ilman normalisointia groupby(["horse_id","start_method"]) ei matchaa:
    # "auto" ≠ "A" → B2 same_method pysyy 4 % vaikka start_method-sarake löytyisi.
    if horse_starts is not None and "start_method" in (horse_starts.columns if horse_starts is not None else []):
        from src.data.track_codes import START_METHOD_TO_ATG
        horse_starts = horse_starts.copy()
        horse_starts["start_method"] = horse_starts["start_method"].map(
            lambda m: START_METHOD_TO_ATG.get(m, m) if m is not None else None
        )

    df = form_features(runners_with_meta, horse_starts=horse_starts)
    df = driver_trainer_features(df)

    # horse_starts-pohjainen 60d driver/trainer-tilasto (ei ATG K1-bugista)
    _hs_cols = [
        "driver_win_rate_60d", "driver_top3_rate_60d",
        "trainer_win_rate_60d", "trainer_top3_rate_60d",
    ]
    if horse_starts is not None and len(horse_starts) > 0:
        hs_feat = driver_trainer_hs_features(df, horse_starts)
        df = df.merge(hs_feat, on=["race_id", "horse_id"], how="left")
    else:
        # Sarakkeet syntyvät aina (NaN) jotta FEATURE_COLS pysyy yhtenäisenä
        for col in _hs_cols:
            df[col] = np.nan

    df = race_setup_features(df, races, horse_starts=horse_starts)  # B1: track-historia

    # D: ratarakenne — track-sarake on saatavilla race_setup_features():n jälkeen
    if tracks is not None:
        df = track_structure_features(df, tracks)

    df = derived_features(df)

    # B2: sukutaulupiirteet — vaatii sekä horse_starts että horses-parametrin
    if horse_starts is not None and horses is not None:
        df = sire_features(df, horses, horse_starts)

    return df
