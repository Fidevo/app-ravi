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

# Muotolaskennan tulossarakkeet (siirretään runners:iin mergellä)
_FORM_OUT_COLS = [
    "horse_id", "race_date",
    "form_avg_finish_5", "form_win_rate_5", "form_top3_rate_5",
    "form_avg_km_time_5", "form_best_km_time_5",
    "form_market_avg_5", "form_days_since_last",
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

    # --- Rakenna pool: runners + valinnainen horse_starts-historia ---
    current = df[_POOL_COLS].copy()
    current["_is_runner"] = True

    if horse_starts is not None and len(horse_starts) > 0:
        hist = horse_starts[_POOL_COLS].copy()
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

    # --- Palauta vain runner-rivit, liitä form-piirteet runners:iin ---
    runner_form = (
        combined[combined["_is_runner"]][_FORM_OUT_COLS]
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
# 5. Treeniesiesimerkkien esikäsittely — puuttuvien sijoitusten täyttö
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
) -> pd.DataFrame:
    """Aja kaikki feature-funktiot ja palauta valmis matriisi mallille.

    Piirre-pipeline järjestyksessä:
      1. form_features       — hevosen muoto viim. N startista
      2. driver_trainer_features — rolling-tilastot ohjastajalle/valmentajalle
      3. race_setup_features — lähtörata, rata-kokemus, lähdön luokka
      4. derived_features    — johdetut piirteet (barfota, horse_age)

    Valinnainen horse_starts-parametri: Travsport-historia koko uralta.
    Jos annetaan, form_features() käyttää sitä laajentamaan muotodatan
    kattavuutta runners-taulun 14 päivän sijaan koko uraan.

    Valinnainen horse_age-piirre: lisää birth_year runners-DataFrameen
    JOIN:lla horses-tauluun ennen tätä kutsua.
    """
    df = form_features(runners, horse_starts=horse_starts)
    df = driver_trainer_features(df)
    df = race_setup_features(df, races)
    df = derived_features(df)
    return df
