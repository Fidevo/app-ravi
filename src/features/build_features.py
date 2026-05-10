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
            bins=[0, 1640, 2140, 5000],
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
        bins=[0, 1640, 2140, 5000],
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
    """Lisää sire/dam_sire-aggregaatit runner-riveille.

    Lasketaan horse_starts-koko-uradata:sta (ei rajoitettu 14 päivään).
    Aggregaatit lasketaan MUIDEN saman isäoriin jälkeläisten starteista —
    ei data leakagea nykyisen hevosen omasta startista koska laskenta
    perustuu populaatiostatistiikkaan, ei hevosen omiin tuloksiin.

    Piirteet:
      sire_lifetime_win_rate     : isäoriin jälkeläisten voitto-% (kaikki ajat)
      sire_lifetime_starts       : montako starttia estimaatin pohjana
      dam_sire_lifetime_win_rate : emänisän jälkeläisten voitto-%
      dam_sire_lifetime_starts   : montako starttia estimaatin pohjana

    Pienet sample-koot suodatetaan:
      Jos sire_lifetime_starts < _SIRE_MIN_STARTS (30), asetetaan
      sire_lifetime_win_rate = NaN (kohina eikä signaali). LightGBM
      käsittelee NaN:n automaattisesti puuttuvana arvona.

    Args:
        runners: DataFrame jossa horse_id (lähdöt joita ennustetaan)
        horses: DataFrame jossa horse_id, sire, dam_sire
        horse_starts: Travsport-historia (103k+ starttia, kaikki hevoset)

    Returns:
        runners-DataFrame lisättyinä sire/dam_sire-sarakkeilla
    """
    # --- 1. Liitä sire/dam_sire horse_starts-riveihin horses-taulusta ---
    starts_with_pedigree = horse_starts.merge(
        horses[["horse_id", "sire", "dam_sire"]].drop_duplicates("horse_id"),
        on="horse_id",
        how="left",
    )
    starts_with_pedigree["is_win"] = (
        starts_with_pedigree["finish_position"] == 1
    ).astype(float)

    # --- 2. Per-sire aggregaatti (isäoriin kaikki jälkeläiset) ---
    sire_stats = (
        starts_with_pedigree.dropna(subset=["sire"])
        .groupby("sire")
        .agg(
            sire_lifetime_starts=("is_win", "count"),
            sire_lifetime_win_rate=("is_win", "mean"),
        )
        .reset_index()
    )
    # Suodata pois pienet otokset — kohinainen estimaatti on haitallisempi kuin NaN
    sire_stats.loc[
        sire_stats["sire_lifetime_starts"] < _SIRE_MIN_STARTS,
        "sire_lifetime_win_rate",
    ] = np.nan

    # --- 3. Per-dam_sire aggregaatti (emänisän kaikki jälkeläiset) ---
    dam_sire_stats = (
        starts_with_pedigree.dropna(subset=["dam_sire"])
        .groupby("dam_sire")
        .agg(
            dam_sire_lifetime_starts=("is_win", "count"),
            dam_sire_lifetime_win_rate=("is_win", "mean"),
        )
        .reset_index()
    )
    dam_sire_stats.loc[
        dam_sire_stats["dam_sire_lifetime_starts"] < _SIRE_MIN_STARTS,
        "dam_sire_lifetime_win_rate",
    ] = np.nan

    # --- 4. Liitä runnersiin sire- ja dam_sire-tilastot ---
    df = runners.merge(
        horses[["horse_id", "sire", "dam_sire"]].drop_duplicates("horse_id"),
        on="horse_id",
        how="left",
    )
    df = df.merge(sire_stats, on="sire", how="left")
    df = df.merge(dam_sire_stats, on="dam_sire", how="left")

    return df


# ----------------------------------------------------------------------
# 6. Treeniesiesimerkkien esikäsittely — puuttuvien sijoitusten täyttö
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
) -> pd.DataFrame:
    """Aja kaikki feature-funktiot ja palauta valmis matriisi mallille.

    Piirre-pipeline järjestyksessä:
      1. form_features           — hevosen muoto viim. N startista
      2. driver_trainer_features — rolling-tilastot ohjastajalle/valmentajalle
      3. race_setup_features     — lähtörata, rata-kokemus, lähdön luokka
      4. derived_features        — johdetut piirteet (barfota, horse_age)
      5. sire_features           — sukutaulupiirteet (B2, valinnainen)

    Valinnainen horse_starts-parametri: Travsport-historia koko uralta.
    Jos annetaan, form_features() käyttää sitä laajentamaan muotodatan
    kattavuutta runners-taulun 14 päivän sijaan koko uraan.

    Valinnainen horses-parametri: horses-taulu jossa sire/dam_sire-kentät.
    Jos annetaan horse_starts:n kanssa, lasketaan sukutaulupiirteet (B2).

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
    df = race_setup_features(df, races, horse_starts=horse_starts)  # B1: track-historia
    df = derived_features(df)

    # B2: sukutaulupiirteet — vaatii sekä horse_starts että horses-parametrin
    if horse_starts is not None and horses is not None:
        df = sire_features(df, horses, horse_starts)

    return df
