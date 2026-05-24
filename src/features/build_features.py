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

# Valinnaiset pool-sarakkeet ja niiden oletusarvot puuttuvalle datalle.
# had_gallop: vain horse_starts-taulussa (ei runners) → oletus False
# race_max_earnings: runners saa sen races-pre-mergellä, horse_starts
#   rikasteellaan build_feature_matrix():ssa → oletus NaN
#   (Bugikorjaus 23.5.2026: muutettu race_min_earnings → race_max_earnings)
_POOL_COLS_OPTIONAL_DEFAULTS: dict = {
    "had_gallop": False,
    "race_max_earnings": float("nan"),
}
_POOL_COLS_OPTIONAL: list[str] = list(_POOL_COLS_OPTIONAL_DEFAULTS.keys())

# C6: minimisegmenttikoko — sama kaava kuin sire-piirteiden _SIRE_MIN_STARTS.
# Piirre nollataan NaN:ksi jos (hevonen, luokka)-segmentissä on alle tämän
# verran aiempia startteja. Poistaa sparse-segmenttien kohinan (diagnostiikka
# 22.5.2026: 42.9 % segmenteistä n≤3, kattavuus vain 27.1 %).
# Kokeile 3 jos 5 leikkaa liikaa walk-forwardissa (~2026-07).
_CLASS_MIN_STARTS: int = 5

# B2: minimisegmenttikynnys same_method / same_dist -piirteille.
# Alhaisepi kuin C6 (5) koska 2 buckettia (auto/volt) ja 3 buckettia (matka)
# → segmentit ovat luonnostaan isompia. Diagnostiikka 22.5.2026:
# 34–37 % n≤3, seg_med=6, Q1=2 — hieman parempaa kuin C6 (42.9 %).
# Kokeile 2:ta jos 3 leikkaa liikaa walk-forwardissa (~2026-07).
_B2_MIN_STARTS: int = 3

# Muotolaskennan tulossarakkeet (siirretään runners:iin mergellä)
_FORM_OUT_COLS = [
    "horse_id", "race_date",
    "form_avg_finish_5", "form_win_rate_5", "form_top3_rate_5",
    "form_avg_km_time_5", "form_best_km_time_5", "form_ewm_km_time",
    "form_market_avg_5", "form_days_since_last",
    "last_race_had_gallop",
    # B2: segmentoidut muotopiirteet (lisätään vain jos sarakkeet löytyivät poolista)
    "form_avg_finish_5_same_method", "form_avg_finish_5_same_dist",
    "form_avg_km_time_5_same_dist",  # km-aika samassa matkaluokassa (bugikorjaus 23.5.2026)
    # C6: luokkakohtaiset muotopiirteet (lisätään vain jos race_max_earnings saatavilla)
    "form_win_rate_5_same_class", "form_avg_finish_5_same_class", "form_avg_km_time_5_same_class",
]

# Normalisointitaulu rataolot-koodit: Travsport + ATG → kanoninen muoto.
# Travsport käyttää lyhenteitä ("n"/"v"/"s"/"t"), ATG käyttää englantia.
# Taulun arvo on LightGBM:n näkemä kategorinen taso (track_condition_win_rate).
_TRACK_COND_NORM: dict[str, str] = {
    # Travsport-koodit (horse_starts.track_condition)
    "n": "light", "N": "light",
    "v": "heavy", "V": "heavy",
    "s": "winter", "S": "winter",
    "t": "winter", "T": "winter",
    # ATG-arvot (races.track_condition) — passthrough-normalisaatio
    "light": "light", "good": "light", "dead": "light",
    "heavy": "heavy", "winter": "winter",
}


def _linear_slope(vals: "np.ndarray") -> float:
    """Palauttaa lineaariregressiosuoran kulmakertoimen arvoille.

    x = järjestysindeksi 0..n-1, y = vals.
    Palauttaa NaN jos alle 2 validia havaintoa.
    """
    mask = ~np.isnan(vals)
    n = int(mask.sum())
    if n < 2:
        return np.nan
    x = np.arange(len(vals), dtype=float)[mask]
    y = vals[mask]
    return float(np.polyfit(x, y, 1)[0])


def _normalize_driver_name(name: str) -> str:
    """Normalisoi Travsport-nimiformaatti ATG-nimiformaatiksi.

    Travsport tallentaa kuski/valmentaja-nimet muodossa "Sukunimi Etunimi"
    (esim. "Kontio Jorma"), mutta ATG käyttää muotoa "Etunimi Sukunimi"
    (esim. "Jorma Kontio"). Tämä funktio kääntää yksisanaisen tai kaksi-
    (tai useampi)osaisen Travsport-nimen ATG-järjestykseen.

    Esimerkit:
        "Kontio Jorma"        → "Jorma Kontio"
        "Van Der Berg Pieter" → "Pieter Van Der Berg"
        "Madonna"             → "Madonna"  (yksiosainen, ei muutosta)
    """
    parts = str(name).strip().split()
    if len(parts) < 2:
        return name
    # Oletus: viimeinen sana on etunimi (Travsport: Sukunimi [Sukunimi2] Etunimi)
    return " ".join(parts[-1:] + parts[:-1])


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
    # Valinnaiset sarakkeet: otetaan mukaan jos löytyy df:stä TAI horse_starts:sta.
    # had_gallop on vain horse_starts-taulussa — runners saa oletusarvon False.
    hs_cols_set = set(horse_starts.columns) if horse_starts is not None else set()
    opt_cols_avail = [
        c for c in _POOL_COLS_OPTIONAL if c in df.columns or c in hs_cols_set
    ]
    pool_cols_full = _POOL_COLS + seg_cols_avail + opt_cols_avail

    # --- Rakenna pool: runners + valinnainen horse_starts-historia ---
    current = df[[c for c in pool_cols_full if c in df.columns]].copy()
    # Täytä puuttuvat optionaaliset sarakkeet per-sarake-oletusarvoilla
    for c in opt_cols_avail:
        if c not in current.columns:
            current[c] = _POOL_COLS_OPTIONAL_DEFAULTS.get(c, False)
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
    # B-10: NaN finish_position (vetäytyneet / tulevat hevoset) pitää jättää pois
    # rolling-laskennasta. .where(notna) tuottaa NaN vähäisissä riveissä niin
    # että pandas rolling mean ohittaa ne eikä laske niitä 0-voittoina.
    combined["_is_win"] = (combined["finish_position"] == 1).where(
        combined["finish_position"].notna()
    )
    combined["_is_top3"] = (combined["finish_position"] <= 3).where(
        combined["finish_position"].notna()
    )
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
    # Gallop-filtteri: maski km_time NaN:iksi laukkastarteilla jotta nopeat
    # hevoset eivät näytä hitailta pelkkien laukojen takia.
    if "had_gallop" in combined.columns:
        combined["_km_clean"] = combined["kilometer_time_seconds"].where(
            ~combined["had_gallop"].fillna(False).astype(bool)
        )
    else:
        combined["_km_clean"] = combined["kilometer_time_seconds"]

    combined["form_avg_km_time_5"] = grouped["_km_clean"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
    )
    combined["form_best_km_time_5"] = grouped["_km_clean"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).min()
    )
    # Recency-painotettu km-aika: viimeisin startti painaa enemmän (span=n_last)
    # adjust=False → O(n) muistivaatimus per ryhmä (adjust=True olisi O(n²))
    combined["form_ewm_km_time"] = grouped["_km_clean"].transform(
        lambda s: s.shift(1).ewm(span=n_last, min_periods=1, adjust=False).mean()
    )
    combined["form_market_avg_5"] = grouped["_market_prob"].transform(
        lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
    )

    # Lepo päivissä
    combined["_prev_race_date"] = grouped["race_date"].shift(1)
    combined["form_days_since_last"] = (
        combined["race_date"] - combined["_prev_race_date"]
    ).dt.days

    # last_race_had_gallop: 1.0 jos edellinen startti päättyi laukkaan
    if "had_gallop" in combined.columns:
        combined["last_race_had_gallop"] = grouped["had_gallop"].transform(
            lambda s: s.shift(1).fillna(False).astype(float)
        )
    else:
        combined["last_race_had_gallop"] = np.nan

    # --- B2: Segmentoidut muotopiirteet ---
    # form_avg_finish_5_same_method:  rolling 5 vain samalla starttimuodolla (auto/volt)
    # form_avg_finish_5_same_dist:    rolling 5 vain samalla matkaluokalla (sprint/middle/long)
    # form_avg_km_time_5_same_dist:   rolling 5 km-aika vain samalla matkaluokalla
    #   Bugikorjaus 23.5.2026 (raviasiantuntija): form_avg_km_time_5 laskee km-ajan yli
    #   kaikkien matkojen — 1600 m ja 3200 m eivät ole vertailukelpoisia.
    #   Tämä matkaluokkakohtainen versio korjaa sen.
    # Lasketaan vain jos start_method/distance löytyivät poolista.
    if "start_method" in combined.columns:
        grouped_method = combined.groupby(
            ["horse_id", "start_method"], group_keys=False
        )
        combined["form_avg_finish_5_same_method"] = grouped_method[
            "finish_position"
        ].transform(lambda s: s.shift(1).rolling(n_last, min_periods=1).mean())
        # _B2_MIN_STARTS-kynnys: nollaa sparse-segmentit (diagnostiikka 22.5.2026:
        # 34.2 % segmenteistä n≤3, Q1=2 → kohinaa sparse-hevosten auto/volt-jakaumissa)
        _b2_method_n = grouped_method["finish_position"].transform(
            lambda s: s.shift(1).rolling(n_last, min_periods=1).count()
        )
        combined.loc[_b2_method_n < _B2_MIN_STARTS, "form_avg_finish_5_same_method"] = np.nan
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
        # form_avg_km_time_5_same_dist: km-aika samassa matkaluokassa (bugikorjaus 23.5.2026)
        # Bugikorjaus 24.5.2026: aiemmin käytettiin "km_time" jota ei ole combined:ssa.
        # Oikea sarake on "_km_clean" (gallop-filteroitu kilometer_time_seconds),
        # joka on laskettu yläpuolella form_avg_km_time_5:tä varten.
        if "_km_clean" in combined.columns:
            combined["form_avg_km_time_5_same_dist"] = grouped_dist[
                "_km_clean"
            ].transform(lambda s: s.shift(1).rolling(n_last, min_periods=1).mean())
        elif "kilometer_time_seconds" in combined.columns:
            combined["form_avg_km_time_5_same_dist"] = grouped_dist[
                "kilometer_time_seconds"
            ].transform(lambda s: s.shift(1).rolling(n_last, min_periods=1).mean())
        else:
            combined["form_avg_km_time_5_same_dist"] = np.nan
        # _B2_MIN_STARTS-kynnys: nollaa sparse-segmentit (diagnostiikka 22.5.2026:
        # 36.6 % segmenteistä n≤3, Q1=2 → sama mekanismi kuin same_method)
        _b2_dist_n = grouped_dist["finish_position"].transform(
            lambda s: s.shift(1).rolling(n_last, min_periods=1).count()
        )
        combined.loc[_b2_dist_n < _B2_MIN_STARTS, "form_avg_finish_5_same_dist"] = np.nan
        combined.loc[_b2_dist_n < _B2_MIN_STARTS, "form_avg_km_time_5_same_dist"] = np.nan
        combined = combined.drop(columns=["_dist_bucket"])
    else:
        combined["form_avg_finish_5_same_dist"] = np.nan
        combined["form_avg_km_time_5_same_dist"] = np.nan

    # --- C6: Luokkakohtaiset muotopiirteet ---
    # Vaatii race_max_earnings poolissa (runners: pre-merge races:sta,
    # horse_starts: rikastettu build_feature_matrix():ssa).
    # Luokkabucketit (SEK) kuvaavat lähdön tasoa ylärajan mukaan (race_max_earnings).
    # race_max_earnings = NULL merkitsee huippuluokkaa (ei ylärajaa) → "elite".
    # Bugikorjaus 23.5.2026: aiemmin käytettiin race_min_earnings (alaraja),
    # joka on raviasiantuntijan mukaan väärä — Ruotsissa luokka määräytyy
    # hevosen kumulatiivisten palkintosumman YLÄRAJAN mukaan.
    #   low    : 0–50 000  (aloittelijat / kevyet avoimet)
    #   medium : 50 000–150 000  (perustaso)
    #   high   : 150 000–500 000  (korkeampi taso)
    #   elite  : 500 000+ tai NULL  (huipputaso / ei ylärajaa)
    # NaN: joinaamaton historiastartit (norjalaiset/vanhat radat) → LightGBM käsittelee.
    if "race_max_earnings" in combined.columns and (
        combined["race_max_earnings"].notna().any() or combined["race_max_earnings"].isna().any()
    ):
        # NULL race_max_earnings = elite (ei ylärajaa → huippuluokka)
        _max_earn = combined["race_max_earnings"].fillna(float("inf"))
        combined["_race_class_bucket"] = pd.cut(
            _max_earn,
            bins=[0, 50_000, 150_000, 500_000, float("inf")],
            labels=["low", "medium", "high", "elite"],
            right=True,
            include_lowest=True,
        )
        grouped_class = combined.groupby(
            ["horse_id", "_race_class_bucket"], group_keys=False, observed=True
        )
        combined["form_win_rate_5_same_class"] = grouped_class["_is_win"].transform(
            lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
        )
        combined["form_avg_finish_5_same_class"] = grouped_class["finish_position"].transform(
            lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
        )
        combined["form_avg_km_time_5_same_class"] = grouped_class["_km_clean"].transform(
            lambda s: s.shift(1).rolling(n_last, min_periods=1).mean()
        )
        # Minimisegmenttikynnys: nollaa C6-piirteet jos (horse, class) -segmentissä
        # on alle _CLASS_MIN_STARTS aiempaa starttia. Perustuu finish_position-laskuriin
        # (aina saatavilla, toisin kuin _km_clean jossa voi olla NaN gallop-filterin takia).
        # Diagnostiikka 22.5.2026: mediaani segmentti=4, 42.9 % ≤3 → kohinaa malliin.
        _seg_n = grouped_class["finish_position"].transform(
            lambda s: s.shift(1).rolling(n_last, min_periods=1).count()
        )
        _sparse = _seg_n < _CLASS_MIN_STARTS
        for _c6_col in (
            "form_win_rate_5_same_class",
            "form_avg_finish_5_same_class",
            "form_avg_km_time_5_same_class",
        ):
            combined.loc[_sparse, _c6_col] = np.nan
        combined = combined.drop(columns=["_race_class_bucket"])
    else:
        combined["form_win_rate_5_same_class"] = np.nan
        combined["form_avg_finish_5_same_class"] = np.nan
        combined["form_avg_km_time_5_same_class"] = np.nan

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
    # B-7: NaN finish_position (vetäytyneet / tulevat hevoset) pitää jättää pois
    # laskennasta. Käytetään .where(notna) jotta NaN-rivit eivät kasvata
    # laskuria mutta eivät voittoja → win_rate ei aliarvioidz.
    _fp_notna = df["finish_position"].notna()
    df["is_win"] = (df["finish_position"] == 1).where(_fp_notna).astype("float")
    df["is_top3"] = (df["finish_position"] <= 3).where(_fp_notna).astype("float")

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

    # KNOWN_ISSUES #15: Travsport tallentaa nimet "Sukunimi Etunimi" -järjestyksessä,
    # ATG käyttää "Etunimi Sukunimi" -järjestystä. Normalisoidaan horse_starts-nimet
    # ATG-formaattiin ennen mergeä jotta matchit löytyvät.
    for _col in ("driver", "trainer"):
        if _col in hs.columns:
            hs[_col] = hs[_col].map(
                lambda n: _normalize_driver_name(n) if isinstance(n, str) else n
            )

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

        # MUISTIOPTIMISAATIO: yksittäinen runners_df × hist -merge räjäyttää
        # väliaikaisen DataFramen kymmeniin miljooniin riveihin kun nimet
        # matchaavat oikein (n_runners × n_hist_per_role → OOM 3+ GB).
        # Korjaus: iteroidaan yksittäisten roolin arvojen (driver/trainer) yli
        # jolloin kunkin iteraation intermediate on pieni.
        role_vals = runners_df[role].dropna().unique()
        hist_by_role = {v: grp for v, grp in hist.groupby(role)}

        role_agg_parts: list[pd.DataFrame] = []
        runners_cols = runners_df[["race_id", "horse_id", "race_date", role]].copy()

        for rval in role_vals:
            rval_runners = runners_cols[runners_cols[role] == rval]
            rval_hist    = hist_by_role.get(rval)
            if rval_hist is None or rval_hist.empty:
                continue  # Ei historiaa — jätetään pois, lopussa LEFT-join tuottaa NaN

            merged = rval_runners.merge(
                rval_hist,
                on=role,
                suffixes=("_runner", "_hist"),
                how="left",
            )
            # Point-in-time filter + aikaikkuna
            cutoff_early = merged["race_date_hist"] < merged["race_date_runner"]
            cutoff_late  = (
                merged["race_date_hist"]
                >= merged["race_date_runner"] - pd.Timedelta(days=lookback_days)
            )
            in_window = merged[cutoff_early & cutoff_late]
            if in_window.empty:
                continue

            grp = (
                in_window.groupby(["race_id", "horse_id"])
                .agg(
                    _n_starts=("is_win", "count"),
                    _wins=("is_win", "sum"),
                    _top3=("is_top3", "sum"),
                )
                .reset_index()
            )
            role_agg_parts.append(grp)

        if role_agg_parts:
            agg = pd.concat(role_agg_parts, ignore_index=True)
        else:
            agg = pd.DataFrame(columns=["race_id", "horse_id", "_n_starts", "_wins", "_top3"])

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
      - has_handicap          : 1 jos hevosella on tasamatka (handicap_meters > 0)
      - is_back_row_auto      : 1 jos autolähdössä ja lähtönumero > 8 (takarivin signaali)
      - (back_row säilyy yhteensopivuussyistä, alias has_handicap:lle)
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
    # has_handicap: hevosella on lisämatka (takamatka volttilähdössä tai autolähdössä).
    # Nimetty aiemmin "back_row" — termi oli harhaanjohtava, koska takamatka on eri
    # asia kuin volttilähdön takarivi. Raviasiantuntijan bugikorjaus 23.5.2026.
    df["has_handicap"] = (df["handicap_meters"].fillna(0) > 0).astype(int)
    # Takaisinyhteensopivuus: back_row säilyy aliaksena has_handicap:lle.
    df["back_row"] = df["has_handicap"]
    # is_back_row_auto: autolähdössä lähtönumero > 8 = konkreettinen takarivin haitta.
    # Volttilähdössä kaikki lähtevät samanaikaisesti — ei sama ilmiö.
    # Lähde: raviasiantuntija 23.5.2026.
    if "start_method" in df.columns:
        df["is_back_row_auto"] = (
            (df["start_number"] > 8) & (df["start_method"].str.lower() == "auto")
        ).astype(float)  # float: NaN jos start_method puuttuu
        # Nullaa rivit joissa start_method on NaN
        df.loc[df["start_method"].isna(), "is_back_row_auto"] = np.nan
    else:
        df["is_back_row_auto"] = np.nan
    # Normalisoitu lähtörata: inside_post-etu vaihtelee kenttäkoon mukaan
    df["field_size"] = df.groupby("race_id")["horse_id"].transform("count")
    df["post_pos_norm"] = df["start_number"] / df["field_size"].replace(0, np.nan)

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

    Käytetään **leave-one-out (LOO)** -laskentaa: hevosen omat startit
    vähennetään sen isäoriin kokonaisaggregaatista ennen win-rate-laskentaa.
    Tämä estää "indirektin leakagen" jossa hevosen oma menestys nostaa
    sen sire-ratea, joka kertoo "tämä hevonen on hyvä" eikä "tämä sire on hyvä".

    Point-in-time (leakage-korjaus 22.5.2026): horse_starts rajataan
    ennen runners:in vanhinta race_dateja. Tämä on konservatiivinen
    globaali katkaisu — oikea per-runner-PIT vaatisi _loo_stats:n
    rakennemuutoksen. Nykytoteutus ei vuoda tulevaisuutta mutta saattaa
    aliarvioida sire-statistiikkaa uusimmille runnereille.
    TODO: refaktoroi _loo_stats per-runner-PIT:ksi kun sire-piirteet
          aktivoidaan uudelleen (~2026-07).

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
        runners: DataFrame jossa horse_id ja race_date (lähdöt joita ennustetaan)
        horses: DataFrame jossa horse_id, sire, dam_sire
        horse_starts: Travsport-historia (kaikki hevoset)

    Returns:
        runners-DataFrame lisättyinä sire/dam_sire-sarakkeilla
    """
    pedigree = horses[["horse_id", "sire", "dam_sire"]].drop_duplicates("horse_id")

    # Point-in-time: poista tulevaisuuden startit ennen aggregointia.
    # Käytetään runners:in vanhinta race_dateja kynnyksenä (konservatiivinen
    # globaali katkaisu). Estää backtestauksen "aikamatkailun" eli sen että
    # vuoden 2024 ennuste sisältäisi oriin jälkeläisten vuoden 2026 tulokset.
    hs_pit = horse_starts.copy()
    if "race_date" in runners.columns and "race_date" in hs_pit.columns:
        min_runner_date = pd.to_datetime(runners["race_date"]).min()
        hs_pit = hs_pit[pd.to_datetime(hs_pit["race_date"]) < min_runner_date]

    # --- 1. Liitä sire/dam_sire horse_starts-riveihin horses-taulusta ---
    starts = hs_pit.merge(pedigree, on="horse_id", how="left")
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
         järjestetään nousevasti per lähtö ja sijoitetaan heti virallisten jälkeen
         (N+1, N+2, ...).
         Järjestysaika (bugikorjaus 23.5.2026, raviasiantuntija):
           - Volttilähtö (start_method == "volte") JA distance+handicap_meters saatavilla:
               total_time = km_time * (distance + handicap_meters) / 1000
             Perustelu: hevosilla on eri lähtömatkat (tasamatkat) — km_aika on sama
             vaikka hevonen ajoi enemmän metreitä. total_time on oikea vertailuaika.
           - Muulloin (autolähtö tai ei distance-dataa): käytetään km_time suoraan.
      3. Vetäytyneet/peruuntuneet (sekä finish että km_time NULL):
         sijoitetaan viimeisiksi järjestyksessä (km_aika ei tiedossa).
      4. Lähdöt joissa KAIKKI sijoitukset ovat NULL (tulevat lähdöt) jätetään
         koskemattomiksi.

    HUOM: Kutsu tätä vain koulutusaineistolla. Ennustamisessa (live-data)
    finish_position ei ole koskaan tiedossa — siellä tätä ei tarvita.

    Args:
        runners: DataFrame jossa on sarakkeet race_id, finish_position,
                 kilometer_time_seconds. Muut sarakkeet läpäistään sellaisinaan.
                 Valinnainen: distance, handicap_meters, start_method
                 (tarvitaan volttilähtö-korjaukseen).

    Returns:
        Kopio DataFramesta jossa finish_position täytetty kaikille riveille
        joissa lähtö on ajettu. Rivimäärä ei muutu.
    """
    # Vektorisoitu toteutus — vältetään rivittäinen df.loc-silmukka joka on
    # O(n×m) 284k+ riviä × 25k+ lähtöä -skaalalla.
    df = runners.copy()
    df["finish_position"] = df["finish_position"].astype(float)

    # Vain lähdöt joissa on JOTAIN sijoituksia mutta ei kaikkia (partial races).
    race_max = df.groupby("race_id")["finish_position"].transform("max")
    needs_fill_mask = race_max.notna() & df["finish_position"].isna()

    if not needs_fill_mask.any():
        return df

    needs = df[needs_fill_mask].copy()
    needs["_race_max"] = race_max[needs_fill_mask]

    # Erottele km_aika-hevoset vs. vetäytyneet
    has_km = needs["kilometer_time_seconds"].notna()
    km_runners = needs[has_km].copy()
    withdrawn = needs[~has_km].copy()

    # km_aika-hevoset: järjestä nousevasti per lähtö → sijoitus = race_max + rank
    # Volttilähtö-korjaus (bugikorjaus 23.5.2026): käytä total_time jos distance+handicap
    # saatavilla ja start_method == "volte". Muulloin km_time riittää (autolähtö).
    if not km_runners.empty:
        _has_volte_fix = (
            "distance" in km_runners.columns
            and "handicap_meters" in km_runners.columns
            and "start_method" in km_runners.columns
        )
        if _has_volte_fix:
            _is_volte = km_runners["start_method"].str.lower().eq("volte")
            _dist = km_runners["distance"].fillna(0)
            _hcap = km_runners["handicap_meters"].fillna(0)
            _sort_time = km_runners["kilometer_time_seconds"].copy()
            # total_time (sekuntia) = km_time * kokonaismatka / 1000
            _total_time = km_runners["kilometer_time_seconds"] * (_dist + _hcap) / 1000
            _sort_time = _sort_time.where(~_is_volte, _total_time)
        else:
            _sort_time = km_runners["kilometer_time_seconds"]
        km_runners["_sort_time"] = _sort_time
        km_runners["_rank"] = km_runners.groupby("race_id")[
            "_sort_time"
        ].rank(method="first", ascending=True)
        km_runners["finish_position"] = km_runners["_race_max"] + km_runners["_rank"]

    # Vetäytyneet: sijoitetaan km_aika-hevosten jälkeen
    if not withdrawn.empty:
        km_count = (
            km_runners.groupby("race_id").size().rename("_km_count")
            if not km_runners.empty
            else pd.Series(dtype=int)
        )
        withdrawn["_km_count"] = withdrawn["race_id"].map(km_count).fillna(0)
        withdrawn["_w_rank"] = withdrawn.groupby("race_id").cumcount() + 1
        withdrawn["finish_position"] = (
            withdrawn["_race_max"] + withdrawn["_km_count"] + withdrawn["_w_rank"]
        )

    # Päivitä df yhdellä sijoituksella
    filled = pd.concat(
        [x[["finish_position"]] for x in [km_runners, withdrawn] if not x.empty]
    )
    df.loc[filled.index, "finish_position"] = filled["finish_position"]

    return df


# ----------------------------------------------------------------------
# 8. Starttipaikan vinouma per rata (start_position_win_rate)
# ----------------------------------------------------------------------

def start_position_features(
    runners_df: pd.DataFrame,
    races_df: pd.DataFrame,
    historical_runners_df: pd.DataFrame | None = None,
    min_samples: int = 10,
) -> pd.DataFrame:
    """Laske historiallinen voitto-% per (track, start_number, start_method).

    Bugikorjaus 24.5.2026 (raviasiantuntija): aiemmin ryhmittely oli vain
    (track, start_number), joka sekoitti autolähdön ja volttilähdön tilastot.
    Nyt ryhmittely on (track, start_number, start_method) kun start_method on
    saatavilla. Autolähdössä ja volttilähdössä ratojen dynamiikka on täysin erilainen:
      - Volttilähtö: rata 1 ylivoimaisesti paras (lyhin matka ensimmäiseen
        kaarteeseen, välitön keulahevonenmahdollisuus). Rata 8+ usein toivoton.
      - Autolähtö: rata 1 ei yhtä ylivoimainen; radat 4–5 usein parhaita.
    Ilman start_method-erottelua malli saa kompromissiluvun joka ei kuvaa
    kumpaakaan lähtötapaa oikein.

    Käyttää **point-in-time** -tilastoa kun runners_df:ssä on race_date-sarake:
    kullekin lähdölle lasketaan aggregaatti vain aiemmista lähdöistä
    (race_date < nykyinen race_date), jolloin treenidatan oma tulos ei
    kontaminoi omaa start_position_win_rate -arvoaan. Ilman race_date-saraketta
    käytetään fallback-globaaliaggregointia (taaksepäin-yhteensopiva).

    Args:
        runners_df: nykyiset lähdöt, vaaditut sarakkeet: race_id, start_number,
            finish_position. Valinnaisesti race_date ja start_method.
        races_df: races-taulu, vaaditut sarakkeet: race_id, track.
            Valinnaisesti start_method (käytetään jos runners_df:ssä ei ole).
        historical_runners_df: runners historiasta (valinnainen). Jos annetaan,
            yhdistetään pooliin ennen aggregointia.
        min_samples: min. näytemäärä — alle tämän → NaN (oletus 10)

    Returns:
        DataFrame sarakkeilla [race_id, start_number,
            start_position_win_rate, start_position_win_rate_n]
    """
    # track_map: liittää track (ja start_method jos saatavilla) race_id:n mukaan.
    _tm_cols = ["race_id", "track"]
    if "start_method" in races_df.columns:
        _tm_cols.append("start_method")
    track_map = races_df[_tm_cols].drop_duplicates("race_id")

    # Onko start_method käytettävissä jossain muodossa?
    has_start_method = (
        "start_method" in runners_df.columns
        or "start_method" in races_df.columns
    )

    def _add_track(df: pd.DataFrame) -> pd.DataFrame:
        """Liitä track (ja start_method) race_id:n perusteella."""
        d = df.copy()
        d["race_id"] = d["race_id"].astype(str)
        tm = track_map.copy()
        tm["race_id"] = tm["race_id"].astype(str)
        return d.merge(tm, on="race_id", how="left")

    def _compute_agg(pool_sub: pd.DataFrame) -> pd.DataFrame:
        """Laske win rate -aggregaatti osajoukolle.

        Ryhmittely: (track, start_number, start_method) jos start_method
        saatavilla — muuten (track, start_number) fallbackina.
        """
        if has_start_method and "start_method" in pool_sub.columns:
            group_keys = ["track", "start_number", "start_method"]
        else:
            group_keys = ["track", "start_number"]
        agg = (
            pool_sub.groupby(group_keys, observed=True)
            .agg(_n=("is_win", "count"), _wins=("is_win", "sum"))
            .reset_index()
        )
        agg["start_position_win_rate"] = np.where(
            agg["_n"] >= min_samples,
            agg["_wins"] / agg["_n"],
            np.nan,
        )
        agg = agg.rename(columns={"_n": "start_position_win_rate_n"})
        out_cols = group_keys + ["start_position_win_rate", "start_position_win_rate_n"]
        return agg[out_cols]

    # merge-avaimet: (track, start_number, start_method) tai (track, start_number)
    _merge_keys = ["track", "start_number", "start_method"] if has_start_method \
        else ["track", "start_number"]

    # Rakenna pool race_date:n kanssa (point-in-time-suodatusta varten)
    has_race_date = "race_date" in runners_df.columns
    pool_cols = ["race_id", "start_number", "finish_position"]
    if has_race_date:
        pool_cols = pool_cols + ["race_date"]
    # start_method pooliin jos runners_df:ssä — muuten _add_track liittää races_df:stä
    if "start_method" in runners_df.columns:
        pool_cols = pool_cols + ["start_method"]

    pool = _add_track(runners_df[[c for c in pool_cols if c in runners_df.columns]].copy())

    if historical_runners_df is not None and len(historical_runners_df) > 0:
        cols_needed = ["race_id", "start_number", "finish_position"]
        hist_cols = [c for c in cols_needed if c in historical_runners_df.columns]
        if len(hist_cols) == 3:
            if has_race_date and "race_date" in historical_runners_df.columns:
                hist_cols = hist_cols + ["race_date"]
            hist = _add_track(historical_runners_df[hist_cols].copy())
            pool = pd.concat([pool, hist], ignore_index=True)

    pool = pool.dropna(subset=["track", "start_number", "finish_position"])
    pool["is_win"] = (pool["finish_position"] == 1).astype(float)

    # ------------------------------------------------------------------ #
    # Point-in-time -laskenta: jos race_date saatavilla, käytetään vain   #
    # dataa joka oli olemassa ENNEN kunkin lähdön päivää.                  #
    # Tämä estää treenilähdön oman tuloksen kontaminoimasta omia piirteitä. #
    # ------------------------------------------------------------------ #
    if has_race_date and "race_date" in pool.columns:
        unique_dates = sorted(runners_df["race_date"].dropna().unique())
        parts: list[pd.DataFrame] = []

        for cut_date in unique_dates:
            # Suodata historiallinen data: vain päivät ennen cut_date
            hist_pool = pool[pool["race_date"] < cut_date]
            # Slice-sarakkeet: race_id + start_number (+ start_method jos saatavilla)
            _slice_cols = ["race_id", "start_number"]
            if "start_method" in runners_df.columns:
                _slice_cols.append("start_method")
            runners_slice = runners_df[
                runners_df["race_date"] == cut_date
            ][_slice_cols].copy()

            if hist_pool.empty:
                # Ei historiaa — palautetaan NaN
                runners_slice["start_position_win_rate"] = np.nan
                runners_slice["start_position_win_rate_n"] = np.nan
                parts.append(runners_slice[["race_id", "start_number",
                                            "start_position_win_rate",
                                            "start_position_win_rate_n"]])
                continue

            agg = _compute_agg(hist_pool)
            # _add_track liittää track + start_method (jos races_df:ssä)
            runners_with_track = _add_track(runners_slice)
            # Merge-avaimet: (track, start_number, start_method) tai (track, start_number)
            _avail_merge = [k for k in _merge_keys if k in runners_with_track.columns
                            and k in agg.columns]
            runners_with_track = runners_with_track.merge(
                agg, on=_avail_merge, how="left"
            )
            runners_with_track["race_id"] = runners_with_track["race_id"].astype(str)

            result_slice = runners_slice[["race_id", "start_number"]].copy()
            result_slice["race_id"] = result_slice["race_id"].astype(str)
            result_slice = result_slice.merge(
                runners_with_track[["race_id", "start_number",
                                    "start_position_win_rate",
                                    "start_position_win_rate_n"]],
                on=["race_id", "start_number"],
                how="left",
            )
            parts.append(result_slice)

        out = runners_df[["race_id", "start_number"]].copy()
        out["race_id"] = out["race_id"].astype(str)
        if parts:
            combined = pd.concat(parts, ignore_index=True)
            combined["race_id"] = combined["race_id"].astype(str)
            out = out.merge(combined, on=["race_id", "start_number"], how="left")
        else:
            out["start_position_win_rate"] = np.nan
            out["start_position_win_rate_n"] = np.nan
        return out

    # ------------------------------------------------------------------ #
    # Fallback: globaali aggregaatti (ei race_date -saraketta käytettävissä) #
    # ------------------------------------------------------------------ #
    agg = _compute_agg(pool)

    runners_with_track = _add_track(runners_df[["race_id", "start_number"]].copy())
    _avail_merge = [k for k in _merge_keys if k in runners_with_track.columns
                    and k in agg.columns]
    runners_with_track = runners_with_track.merge(agg, on=_avail_merge, how="left")

    out = runners_df[["race_id", "start_number"]].copy()
    out["race_id"] = out["race_id"].astype(str)
    runners_with_track["race_id"] = runners_with_track["race_id"].astype(str)
    out = out.merge(
        runners_with_track[["race_id", "start_number",
                             "start_position_win_rate", "start_position_win_rate_n"]],
        on=["race_id", "start_number"],
        how="left",
    )
    return out


# ----------------------------------------------------------------------
# 9. Lähtötapa-preferenssi (start_method_win_rate_diff)
# ----------------------------------------------------------------------

def start_method_features(
    runners_df: pd.DataFrame,
    horse_starts_df: pd.DataFrame,
    min_starts: int = 3,
) -> pd.DataFrame:
    """Laske per hevonen: auto_win_rate - volte_win_rate (horse_starts).

    Point-in-time: vain startit ennen kyseistä race_date (< ei <=).

    Args:
        runners_df: vaaditut sarakkeet: race_id, horse_id, race_date
        horse_starts_df: vaaditut sarakkeet: horse_id, race_date, start_method, finish_position
        min_starts: min. startteja per metodi — alle tämän metodin win_rate = NaN

    Returns:
        DataFrame sarakkeilla [race_id, horse_id, start_method_win_rate_diff]
    """
    runners_df = runners_df.copy()
    runners_df["race_date"] = pd.to_datetime(runners_df["race_date"])
    runners_df["horse_id"] = runners_df["horse_id"].astype(str)

    hs = horse_starts_df.copy()
    hs["race_date"] = pd.to_datetime(hs["race_date"])
    hs["horse_id"] = hs["horse_id"].astype(str)

    if "start_method" not in hs.columns:
        out = runners_df[["race_id", "horse_id"]].copy()
        out["start_method_win_rate_diff"] = np.nan
        return out

    # Normalisoi start_method (Travsport: A/V/L → auto/volte)
    from src.data.track_codes import START_METHOD_TO_ATG
    hs["_sm"] = hs["start_method"].map(
        lambda m: START_METHOD_TO_ATG.get(m, m) if m is not None else None
    )

    hs["is_win"] = (hs["finish_position"] == 1).astype(float)

    # Merge runners × horse_starts per horse_id (point-in-time)
    merged = runners_df[["race_id", "horse_id", "race_date"]].merge(
        hs[["horse_id", "race_date", "_sm", "is_win"]],
        on="horse_id",
        suffixes=("_runner", "_hist"),
        how="left",
    )
    # Vain startit ENNEN runner.race_date
    merged = merged[merged["race_date_hist"] < merged["race_date_runner"]]

    # Aggregoi per (race_id, horse_id, start_method)
    agg = (
        merged.groupby(["race_id", "horse_id", "_sm"])
        .agg(_n=("is_win", "count"), _wins=("is_win", "sum"))
        .reset_index()
    )
    agg["_wr"] = np.where(
        agg["_n"] >= min_starts,
        agg["_wins"] / agg["_n"],
        np.nan,
    )

    # Pivot: auto ja volte
    auto_df = agg[agg["_sm"] == "auto"][["race_id", "horse_id", "_wr"]].rename(
        columns={"_wr": "_auto_wr"}
    )
    volte_df = agg[agg["_sm"] == "volte"][["race_id", "horse_id", "_wr"]].rename(
        columns={"_wr": "_volte_wr"}
    )

    out = runners_df[["race_id", "horse_id"]].copy()
    out = out.merge(auto_df, on=["race_id", "horse_id"], how="left")
    out = out.merge(volte_df, on=["race_id", "horse_id"], how="left")
    out["start_method_win_rate_diff"] = out["_auto_wr"] - out["_volte_wr"]
    out = out.drop(columns=["_auto_wr", "_volte_wr"])
    return out


# ----------------------------------------------------------------------
# 10. Lepopäivien U-käyrä (rest_days_bucket) — kategorinen
# ----------------------------------------------------------------------

def rest_days_bucket_features(df: pd.DataFrame) -> pd.DataFrame:
    """Luo kategorinen rest_days_bucket form_days_since_last:sta.

    Kategoriat:
      'short'     : < 6 päivää (liian väsynyt)
      'optimal'   : 6–21 päivää (paras ikkuna)
      'long'      : 22–60 päivää (hieman rusta)
      'very_long' : > 60 päivää tai ensimmäinen startti (iso kysymysmerkki)

    Vaatii form_days_since_last-sarakkeen df:ssä.
    NaN (ensimmäinen startti) → 'very_long'.

    Args:
        df: DataFrame jossa on form_days_since_last-sarake

    Returns:
        df kopiolla lisätyllä rest_days_bucket-sarakkeella (string / NaN)
    """
    df = df.copy()
    if "form_days_since_last" not in df.columns:
        df["rest_days_bucket"] = np.nan
        return df

    days = df["form_days_since_last"]

    conditions = [
        days.isna(),
        days.notna() & (days < 0),   # B-11: negatiiviset lepopäivät = datavirhe
        days > 60,
        (days >= 22) & (days <= 60),
        (days >= 6) & (days <= 21),
        days < 6,
    ]
    choices = ["very_long", "unknown", "very_long", "long", "optimal", "short"]

    df["rest_days_bucket"] = np.select(conditions, choices, default="very_long")
    return df


# ----------------------------------------------------------------------
# 11. Kuski×rata ja valmentaja×rata 60d-voitto-% (driver/trainer_track_win_rate_60d)
# ----------------------------------------------------------------------

def driver_trainer_track_features(
    runners_df: pd.DataFrame,
    horse_starts_df: pd.DataFrame,
    races_df: pd.DataFrame,
    lookback_days: int = 60,
    min_starts: int = 3,
) -> pd.DataFrame:
    """Laske kuski×rata ja valmentaja×rata voitto-% 60 päivän ikkunassa.

    Point-in-time: vain startit ennen race_date (< ei <=).
    Normalisoi horse_starts.track TRACKCODE_TO_NAME-mapilla.

    Args:
        runners_df: vaaditut sarakkeet: race_id, horse_id, race_date, driver, trainer
        horse_starts_df: vaaditut sarakkeet: driver, trainer, track, finish_position, race_date
        races_df: vaaditut sarakkeet: race_id, track
        lookback_days: aikaikkuna (oletus 60)
        min_starts: vähimmäisstarttimäärä — alle tämän → NaN (oletus 3)

    Returns:
        DataFrame sarakkeilla [race_id, horse_id,
            driver_track_win_rate_60d, trainer_track_win_rate_60d]
    """
    from src.data.track_codes import TRACKCODE_TO_NAME

    runners_df = runners_df.copy()
    runners_df["race_date"] = pd.to_datetime(runners_df["race_date"])
    runners_df["horse_id"] = runners_df["horse_id"].astype(str)

    hs = horse_starts_df.copy()
    hs["race_date"] = pd.to_datetime(hs["race_date"])
    hs["is_win"] = (hs["finish_position"] == 1).astype(float)

    # KNOWN_ISSUES #15: normalisoi Travsport-nimet ATG-formaattiin
    for _col in ("driver", "trainer"):
        if _col in hs.columns:
            hs[_col] = hs[_col].map(
                lambda n: _normalize_driver_name(n) if isinstance(n, str) else n
            )

    # Normalisoi Travsport-ratakoodit ATG-nimiksi
    if "track" in hs.columns:
        hs["track"] = hs["track"].map(
            lambda t: TRACKCODE_TO_NAME.get(t, t) if t is not None else None
        )

    # Liitä track runners:iin races-taulun kautta.
    # Huom: build_feature_matrix() kutsuu tätä JÄLKEEN race_setup_features():n,
    # joten df:ssä on jo track-sarake. Mergetään vain jos se puuttuu — muuten
    # saadaan track_x/track_y-konflikti joka kaataa funktion.
    track_map = races_df[["race_id", "track"]].drop_duplicates("race_id").copy()
    track_map["race_id"] = track_map["race_id"].astype(str)
    runners_with_track = runners_df.copy()
    runners_with_track["race_id"] = runners_with_track["race_id"].astype(str)
    if "track" not in runners_with_track.columns:
        runners_with_track = runners_with_track.merge(track_map, on="race_id", how="left")

    out = runners_df[["race_id", "horse_id"]].copy()
    out["race_id"] = out["race_id"].astype(str)

    suffix = f"{lookback_days}d"

    for role in ("driver", "trainer"):
        win_col = f"{role}_track_win_rate_{suffix}"

        if role not in runners_with_track.columns or role not in hs.columns or "track" not in hs.columns:
            out[win_col] = np.nan
            continue

        hist = hs[["race_date", role, "track", "is_win"]].dropna(subset=["track"]).copy()

        # MUISTIOPTIMISAATIO: iteroidaan per (role_val, track) -pari kuten
        # driver_trainer_hs_features():ssä — vältetään OOM-räjähdys.
        runners_cols_t = runners_with_track[
            ["race_id", "horse_id", "race_date", role, "track"]
        ].copy()
        hist_by_role_track = {k: grp for k, grp in hist.groupby([role, "track"])}

        role_agg_parts_t: list[pd.DataFrame] = []
        for (rval, tval), runner_grp in runners_cols_t.groupby([role, "track"], sort=False):
            hist_grp = hist_by_role_track.get((rval, tval))
            if hist_grp is None or hist_grp.empty:
                continue

            merged = runner_grp.merge(
                hist_grp,
                on=[role, "track"],
                suffixes=("_runner", "_hist"),
                how="left",
            )
            cutoff_early = merged["race_date_hist"] < merged["race_date_runner"]
            cutoff_late  = (
                merged["race_date_hist"]
                >= merged["race_date_runner"] - pd.Timedelta(days=lookback_days)
            )
            in_window = merged[cutoff_early & cutoff_late]
            if in_window.empty:
                continue

            grp = (
                in_window.groupby(["race_id", "horse_id"])
                .agg(_n_starts=("is_win", "count"), _wins=("is_win", "sum"))
                .reset_index()
            )
            role_agg_parts_t.append(grp)

        if role_agg_parts_t:
            agg = pd.concat(role_agg_parts_t, ignore_index=True)
        else:
            agg = pd.DataFrame(columns=["race_id", "horse_id", "_n_starts", "_wins"])

        agg[win_col] = np.where(
            agg["_n_starts"] >= min_starts,
            agg["_wins"] / agg["_n_starts"],
            np.nan,
        )

        agg["race_id"] = agg["race_id"].astype(str)
        out = out.merge(agg[["race_id", "horse_id", win_col]], on=["race_id", "horse_id"], how="left")

    # Varmista sarakkeet olemassa
    for role in ("driver", "trainer"):
        col = f"{role}_track_win_rate_{suffix}"
        if col not in out.columns:
            out[col] = np.nan

    return out


# ----------------------------------------------------------------------
# 12. C5 — Vaihe 7: km_time_trend, prize_money_trend, track_condition_win_rate
# ----------------------------------------------------------------------

def km_time_trend_features(
    runners_df: pd.DataFrame,
    horse_starts_df: pd.DataFrame,
    n_last: int = 8,
) -> pd.DataFrame:
    """Laske km-ajan lineaarinen trendi per runner (viim. n_last startista).

    Negatiivinen arvo = hevonen nopeutuu (parantuva muoto).
    Positiivinen arvo = hevonen hidastuu (heikkeneva muoto).

    Point-in-time: vain startit ennen race_date (< ei <=).

    Args:
        runners_df: vaaditut sarakkeet: race_id, horse_id, race_date
        horse_starts_df: vaaditut sarakkeet: horse_id, race_date,
                         kilometer_time_seconds
        n_last: viimeiset N starttia trendiä varten (oletus 8)

    Returns:
        DataFrame sarakkeilla [race_id, horse_id, km_time_trend]
        NaN jos alle 2 validia havaintoa.
    """
    runners_df = runners_df.copy()
    runners_df["race_date"] = pd.to_datetime(runners_df["race_date"])
    runners_df["horse_id"] = runners_df["horse_id"].astype(str)

    hs = horse_starts_df.copy()
    hs["race_date"] = pd.to_datetime(hs["race_date"])
    hs["horse_id"] = hs["horse_id"].astype(str)

    if "kilometer_time_seconds" not in hs.columns:
        out = runners_df[["race_id", "horse_id"]].copy()
        out["km_time_trend"] = np.nan
        return out

    # Merge runners × horse_starts per horse_id
    merged = runners_df[["race_id", "horse_id", "race_date"]].merge(
        hs[["horse_id", "race_date", "kilometer_time_seconds"]],
        on="horse_id",
        suffixes=("_runner", "_hist"),
        how="left",
    )
    # Point-in-time
    merged = merged[merged["race_date_hist"] < merged["race_date_runner"]].copy()
    # Viimeiset n_last startit per (race_id, horse_id), ajallisesti järjestettynä
    merged = merged.sort_values(["race_id", "horse_id", "race_date_hist"])
    merged = merged.groupby(["race_id", "horse_id"]).tail(n_last)

    slopes = (
        merged.groupby(["race_id", "horse_id"])["kilometer_time_seconds"]
        .apply(lambda s: _linear_slope(s.values.astype(float)))
        .reset_index(name="km_time_trend")
    )

    out = runners_df[["race_id", "horse_id"]].copy()
    out["race_id"] = out["race_id"].astype(str)
    slopes["race_id"] = slopes["race_id"].astype(str)
    out = out.merge(slopes, on=["race_id", "horse_id"], how="left")
    return out


def prize_money_trend_features(
    runners_df: pd.DataFrame,
    horse_starts_df: pd.DataFrame,
    n_last: int = 8,
) -> pd.DataFrame:
    """Laske palkintorahan lineaarinen trendi per runner (viim. n_last startista).

    Positiivinen arvo = hevonen nousee luokkatasoa (enemmän rahaa).
    Negatiivinen arvo = hevonen laskee luokkatasoa (vähemmän rahaa).

    Point-in-time: vain startit ennen race_date (< ei <=).

    Args:
        runners_df: vaaditut sarakkeet: race_id, horse_id, race_date
        horse_starts_df: vaaditut sarakkeet: horse_id, race_date, prize_won
        n_last: viimeiset N starttia trendiä varten (oletus 8)

    Returns:
        DataFrame sarakkeilla [race_id, horse_id, prize_money_trend]
        NaN jos alle 2 validia havaintoa tai prize_won-sarake puuttuu.
    """
    runners_df = runners_df.copy()
    runners_df["race_date"] = pd.to_datetime(runners_df["race_date"])
    runners_df["horse_id"] = runners_df["horse_id"].astype(str)

    hs = horse_starts_df.copy()
    hs["race_date"] = pd.to_datetime(hs["race_date"])
    hs["horse_id"] = hs["horse_id"].astype(str)

    if "prize_won" not in hs.columns:
        out = runners_df[["race_id", "horse_id"]].copy()
        out["prize_money_trend"] = np.nan
        return out

    merged = runners_df[["race_id", "horse_id", "race_date"]].merge(
        hs[["horse_id", "race_date", "prize_won"]],
        on="horse_id",
        suffixes=("_runner", "_hist"),
        how="left",
    )
    merged = merged[merged["race_date_hist"] < merged["race_date_runner"]].copy()
    merged = merged.sort_values(["race_id", "horse_id", "race_date_hist"])
    merged = merged.groupby(["race_id", "horse_id"]).tail(n_last)

    slopes = (
        merged.groupby(["race_id", "horse_id"])["prize_won"]
        .apply(lambda s: _linear_slope(s.values.astype(float)))
        .reset_index(name="prize_money_trend")
    )

    out = runners_df[["race_id", "horse_id"]].copy()
    out["race_id"] = out["race_id"].astype(str)
    slopes["race_id"] = slopes["race_id"].astype(str)
    out = out.merge(slopes, on=["race_id", "horse_id"], how="left")
    return out


def track_condition_win_rate_features(
    runners_df: pd.DataFrame,
    horse_starts_df: pd.DataFrame,
    races_df: pd.DataFrame,
    min_starts: int = 3,
) -> pd.DataFrame:
    """Laske voitto-% per hevonen normalisoidussa rataolossa.

    Normalisoi rataolot sekä horse_starts:sta ("n","v","s","t") että
    races:sta ("light","heavy","winter") _TRACK_COND_NORM-taulun avulla
    ennen vertailua. Näin Travsport-historia ja ATG-lähdöt vertautuvat oikein.

    Point-in-time: vain startit ennen race_date (< ei <=).

    Args:
        runners_df: vaaditut sarakkeet: race_id, horse_id, race_date
        horse_starts_df: vaaditut sarakkeet: horse_id, race_date,
                         track_condition, finish_position
        races_df: vaaditut sarakkeet: race_id, track_condition
        min_starts: min. startit samassa olosuhteessa — alle → NaN (oletus 3)

    Returns:
        DataFrame sarakkeilla [race_id, horse_id, track_condition_win_rate]
        NaN jos track_condition puuttuu tai alle min_starts havaintoa.
    """
    runners_df = runners_df.copy()
    runners_df["race_date"] = pd.to_datetime(runners_df["race_date"])
    runners_df["horse_id"] = runners_df["horse_id"].astype(str)

    # Tarkista sarakkeet — palauta NaN-sarake jos tieto puuttuu
    if (
        "track_condition" not in horse_starts_df.columns
        or "track_condition" not in races_df.columns
    ):
        out = runners_df[["race_id", "horse_id"]].copy()
        out["track_condition_win_rate"] = np.nan
        return out

    hs = horse_starts_df.copy()
    hs["race_date"] = pd.to_datetime(hs["race_date"])
    hs["horse_id"] = hs["horse_id"].astype(str)
    hs["is_win"] = (hs["finish_position"] == 1).astype(float)
    # Normalisoi Travsport-koodit kanonisiksi rataolo-arvoiksi
    hs["_cond_norm"] = hs["track_condition"].map(
        lambda c: _TRACK_COND_NORM.get(str(c), None)
        if c is not None and not (isinstance(c, float) and np.isnan(c))
        else None
    )

    # Hae nykyisen lähdön normalisoitu rataolo races-taulusta
    race_cond = races_df[["race_id", "track_condition"]].drop_duplicates("race_id").copy()
    race_cond["race_id"] = race_cond["race_id"].astype(str)
    race_cond["_current_cond"] = race_cond["track_condition"].map(
        lambda c: _TRACK_COND_NORM.get(str(c), None)
        if c is not None and not (isinstance(c, float) and np.isnan(c))
        else None
    )

    runners_with_cond = runners_df[["race_id", "horse_id", "race_date"]].copy()
    runners_with_cond["race_id"] = runners_with_cond["race_id"].astype(str)
    runners_with_cond = runners_with_cond.merge(
        race_cond[["race_id", "_current_cond"]], on="race_id", how="left"
    )

    # Merge runners × horse_starts per horse_id
    merged = runners_with_cond.merge(
        hs[["horse_id", "race_date", "_cond_norm", "is_win"]],
        on="horse_id",
        suffixes=("_runner", "_hist"),
        how="left",
    )
    # Point-in-time filter
    merged = merged[merged["race_date_hist"] < merged["race_date_runner"]].copy()
    # Suodata vain samat normalisoidut rataolot (molemmat NotNone ja yhtä suuret)
    merged = merged[
        merged["_cond_norm"].notna()
        & merged["_current_cond"].notna()
        & (merged["_cond_norm"] == merged["_current_cond"])
    ]

    # Aggregoi per (race_id, horse_id)
    agg = (
        merged.groupby(["race_id", "horse_id"])
        .agg(
            _n=("is_win", "count"),
            _wins=("is_win", "sum"),
        )
        .reset_index()
    )
    agg["track_condition_win_rate"] = np.where(
        agg["_n"] >= min_starts,
        agg["_wins"] / agg["_n"],
        np.nan,
    )

    out = runners_df[["race_id", "horse_id"]].copy()
    out["race_id"] = out["race_id"].astype(str)
    agg["race_id"] = agg["race_id"].astype(str)
    out = out.merge(
        agg[["race_id", "horse_id", "track_condition_win_rate"]],
        on=["race_id", "horse_id"],
        how="left",
    )
    return out


# ----------------------------------------------------------------------
# 13. Muutospiirteet: ohjastajan vaihto ja matkamuutos
# ----------------------------------------------------------------------

def change_features(
    runners_df: pd.DataFrame,
    horse_starts_df: pd.DataFrame,
    races_df: pd.DataFrame,
) -> pd.DataFrame:
    """Laske muutospiirteet edelliseen starttiin verrattuna.

    driver_changed (float 0.0/1.0):
      1.0 jos nykyisen lähdön kuski on eri kuin hevosen viimeisin kuski
      horse_starts-historiassa. 0.0 jos sama. NaN jos ei historiaa tai
      kuskin nimi puuttuu.

    distance_change_m (float):
      Nykyinen matka (metreissä) - edellinen matka.
      Positiivinen = pidempi matka nyt. NaN jos ei historiaa.

    Point-in-time: vain horse_starts joiden race_date < runner.race_date
    (EI <=) jotta saman päivän startit eivät vuoda.

    Niminormalisointi: Travsport tallentaa "Sukunimi Etunimi" → muunnetaan
    ATG-formaattiin "Etunimi Sukunimi" ennen vertailua.

    Args:
        runners_df: vaaditut sarakkeet: race_id, horse_id, race_date.
            Valinnaisesti driver (nykyinen kuski), distance (nykyinen matka).
        horse_starts_df: vaaditut sarakkeet: horse_id, race_date.
            Valinnaisesti driver, distance.
        races_df: käytetään vain jos distance puuttuu runners_df:stä.
            Vaadittu sarake: race_id, distance.

    Returns:
        DataFrame sarakkeilla [race_id, horse_id, driver_changed, distance_change_m].
        NaN jos ei historiaa tai tarvittava sarake puuttuu.
    """
    runners_df = runners_df.copy()
    runners_df["race_date"] = pd.to_datetime(runners_df["race_date"])
    runners_df["horse_id"] = runners_df["horse_id"].astype(str)

    hs = horse_starts_df.copy()
    hs["race_date"] = pd.to_datetime(hs["race_date"])
    hs["horse_id"] = hs["horse_id"].astype(str)

    # Normalisoi Travsport-nimet ATG-formaattiin (Sukunimi Etunimi → Etunimi Sukunimi)
    if "driver" in hs.columns:
        hs["driver"] = hs["driver"].map(
            lambda n: _normalize_driver_name(n) if isinstance(n, str) else n
        )

    # Rakenna runners-työ-DataFrame nykyisillä arvoilla
    runners_work = runners_df[["race_id", "horse_id", "race_date"]].copy()
    runners_work["race_id"] = runners_work["race_id"].astype(str)

    # Nykyinen kuski runners_df:stä
    has_curr_driver = "driver" in runners_df.columns
    if has_curr_driver:
        runners_work["curr_driver"] = runners_df["driver"].values

    # Nykyinen matka: runners_df > races_df
    if "distance" in runners_df.columns:
        runners_work["curr_distance"] = runners_df["distance"].values
    elif "distance" in races_df.columns:
        dist_map = (
            races_df[["race_id", "distance"]]
            .drop_duplicates("race_id")
            .copy()
        )
        dist_map["race_id"] = dist_map["race_id"].astype(str)
        runners_work = runners_work.merge(
            dist_map.rename(columns={"distance": "curr_distance"}),
            on="race_id",
            how="left",
        )

    # horse_starts-osa: vain tarvittavat sarakkeet, nimetään konfliktin estämiseksi
    hs_cols_src: dict[str, str] = {
        "horse_id": "horse_id",
        "race_date": "hist_date",
    }
    has_hist_driver = "driver" in hs.columns
    has_hist_distance = "distance" in hs.columns
    has_hist_prize = "prize_won" in hs.columns
    if has_hist_driver:
        hs_cols_src["driver"] = "hist_driver"
    if has_hist_distance:
        hs_cols_src["distance"] = "hist_distance"
    if has_hist_prize:
        hs_cols_src["prize_won"] = "hist_prize_won"

    hs_sub = hs[[c for c in hs_cols_src]].rename(columns=hs_cols_src)

    # Merge: runners × horse_starts per horse_id, sitten point-in-time-suodatus
    merged = runners_work.merge(hs_sub, on="horse_id", how="left")
    merged = merged[merged["hist_date"] < merged["race_date"]].copy()

    # Viimeisin start per (race_id, horse_id) = suurin hist_date.
    # D-korjaus (18.5.2026): groupby().last() ottaa viimeisen EI-NaN-arvon PER SARAKE
    # erikseen — eri startien arvot sekoittuvat jos joillakin on NaN.
    # Ratkaisu: .tail(1) ottaa todellisen viimeisen RIVIN koko DataFrame-järjestyksessä.
    merged = merged.sort_values(["race_id", "horse_id", "hist_date"])
    last = merged.groupby(["race_id", "horse_id"], sort=False).tail(1).copy()
    last["race_id"] = last["race_id"].astype(str)

    # driver_changed: 1.0 jos eri kuski, 0.0 jos sama, NaN jos tieto puuttuu
    if has_curr_driver and has_hist_driver and "curr_driver" in last.columns and "hist_driver" in last.columns:
        last["driver_changed"] = np.where(
            last["curr_driver"].isna() | last["hist_driver"].isna(),
            np.nan,
            (last["curr_driver"] != last["hist_driver"]).astype(float),
        )
    else:
        last["driver_changed"] = np.nan

    # distance_change_m: nykyinen - edellinen matka
    if "curr_distance" in last.columns and has_hist_distance and "hist_distance" in last.columns:
        last["distance_change_m"] = (
            last["curr_distance"].astype(float) - last["hist_distance"].astype(float)
        )
    else:
        last["distance_change_m"] = np.nan

    # prev_prize_won: hevosen edellisen startin palkinto (luokkamuutos-proxy)
    if has_hist_prize and "hist_prize_won" in last.columns:
        last["prev_prize_won"] = last["hist_prize_won"].astype(float)
    else:
        last["prev_prize_won"] = np.nan

    out = runners_df[["race_id", "horse_id"]].copy()
    out["race_id"] = out["race_id"].astype(str)
    out_cols = ["race_id", "horse_id", "driver_changed", "distance_change_m", "prev_prize_won"]
    out = out.merge(
        last[[c for c in out_cols if c in last.columns]],
        on=["race_id", "horse_id"],
        how="left",
    )

    # Varmista sarakkeet olemassa (tyhjä horse_starts → NaN)
    for col in ("driver_changed", "distance_change_m", "prev_prize_won"):
        if col not in out.columns:
            out[col] = np.nan

    return out


# ----------------------------------------------------------------------
# M1: Markkinaodds-todennäköisyys
# ----------------------------------------------------------------------

def market_odds_feature(runners_df: pd.DataFrame) -> pd.DataFrame:
    """Laske devigoitu markkinatodennäköisyys ATG closing-line kertoimesta.

    Käyttää runners-taulun win_odds_final-saraketta joka edustaa ATG:n
    viimeistä pari-mutuel-kerrointa ennen lähtöä. Devigointi poistaa
    bookmakerin marginaalin: 1/odds-summa per lähtö on tyypillisesti ~1.15,
    ja jakamalla tällä saadaan todelliset todennäköisyydet jotka summautuvat
    1.0:aan per lähtö.

    Treenauksessa (historiallinen data): win_odds_final saatavilla → piirre
    laskettu kaikille runnereille.

    Ennustuksessa (päivän lähdöt): win_odds_final=NULL → market_implied_prob=NaN.
    Dashboard täyttää NaN:t live-kertoimilla odds_snapshots-taulusta
    (_inject_live_market_odds, app.py) ennen mallikutsua.

    Args:
        runners_df: DataFrame jossa vähintään race_id, horse_id.
            Valinnaisesti win_odds_final (Float) — jos puuttuu, kaikki NaN.

    Returns:
        DataFrame sarakkeilla [race_id, horse_id, market_implied_prob].
    """
    df = runners_df[["race_id", "horse_id"]].copy()
    df["market_implied_prob"] = float("nan")

    if "win_odds_final" not in runners_df.columns:
        return df

    work = runners_df[["race_id", "horse_id", "win_odds_final"]].copy()
    valid = work[work["win_odds_final"] > 1.0].copy()

    if valid.empty:
        return df

    # 1/odds = raaka implisiittinen todennäköisyys (sisältää vigin)
    valid["raw_prob"] = 1.0 / valid["win_odds_final"]

    # Devig per lähtö: jaa jokaisen runnerin raw_prob lähdön vig-kertoimella
    race_vig = (
        valid.groupby("race_id")["raw_prob"]
        .sum()
        .rename("race_vig")
        .reset_index()
    )
    valid = valid.merge(race_vig, on="race_id", how="left")
    valid["market_implied_prob"] = valid["raw_prob"] / valid["race_vig"]

    # B-korjaus (18.5.2026): df:ssä on jo market_implied_prob=NaN (alustus alussa).
    # Jos mergettaisiin suoraan, syntyisi market_implied_prob_x ja _x/_y -konflikti.
    # Ratkaisu: poistetaan NaN-alustus ennen mergeä. Left join tuottaa NaN:t
    # automaattisesti runnerille jotka eivät ole valid-joukossa (odds ≤ 1.0 tai NULL).
    df = df.drop(columns=["market_implied_prob"])
    df = df.merge(
        valid[["race_id", "horse_id", "market_implied_prob"]],
        on=["race_id", "horse_id"],
        how="left",
    )
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
    for c in ("start_method", "distance", "race_max_earnings"):
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

    # C6: rikasta horse_starts race_max_earnings:lla luokkabucketointia varten.
    # Bugikorjaus 23.5.2026: muutettu race_min_earnings → race_max_earnings.
    # Ensisijainen: race_max_earnings on tallennettu suoraan horse_starts-tauluun
    # scheduler._migrate_schema()-backfillillä.
    # Fallback: jos sarake puuttuu (vanha DB tai testi), joinataan races-tauluun.
    # Joineamattomille (norjalaiset radat, data ennen DB:n alkua) jää NaN → LightGBM käsittelee.
    # horse_starts on jo kopioitu yllä jos start_method-normalisointi ajettiin;
    # muussa tapauksessa kopioidaan tässä ennen muutosta.
    if horse_starts is not None and "race_max_earnings" not in horse_starts.columns:
        _hs_join_keys = ["race_date", "track", "race_number"]
        if (
            all(c in races.columns for c in _hs_join_keys + ["race_max_earnings"])
            and all(c in horse_starts.columns for c in _hs_join_keys)
        ):
            _races_cls = (
                races[_hs_join_keys + ["race_max_earnings"]]
                .drop_duplicates(subset=_hs_join_keys)
            )
            # Kopioidaan vain jos ei jo kopioitu start_method-blokin toimesta
            if "start_method" not in horse_starts.columns:
                horse_starts = horse_starts.copy()
            horse_starts = horse_starts.merge(_races_cls, on=_hs_join_keys, how="left")

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

    # C1: rest_days_bucket — kategorinen lepopäivien U-käyrä
    # Vaatii form_days_since_last:n joka on laskettu form_features():ssa
    df = rest_days_bucket_features(df)

    # C2: starttipaikan vinouma per rata
    _sp_cols = ["start_position_win_rate", "start_position_win_rate_n"]
    sp_feat = start_position_features(df, races)
    # race_id-tyypit yhtenäistettävä ennen mergeä
    _df_race_id_orig = df["race_id"].dtype
    sp_feat["race_id"] = sp_feat["race_id"].astype(df["race_id"].dtype)
    df = df.merge(sp_feat, on=["race_id", "start_number"], how="left")

    # C3: lähtötapa-preferenssi (auto vs. volte win_rate per hevonen)
    _sm_cols = ["start_method_win_rate_diff"]
    if horse_starts is not None and len(horse_starts) > 0:
        sm_feat = start_method_features(df, horse_starts)
        sm_feat["race_id"] = sm_feat["race_id"].astype(df["race_id"].dtype)
        sm_feat["horse_id"] = sm_feat["horse_id"].astype(df["horse_id"].dtype)
        df = df.merge(sm_feat, on=["race_id", "horse_id"], how="left")
    else:
        for col in _sm_cols:
            df[col] = np.nan

    # C4: kuski×rata ja valmentaja×rata 60d-tilastot
    _dtr_cols = ["driver_track_win_rate_60d", "trainer_track_win_rate_60d"]
    if horse_starts is not None and len(horse_starts) > 0:
        dtr_feat = driver_trainer_track_features(df, horse_starts, races)
        dtr_feat["race_id"] = dtr_feat["race_id"].astype(df["race_id"].dtype)
        dtr_feat["horse_id"] = dtr_feat["horse_id"].astype(df["horse_id"].dtype)
        df = df.merge(dtr_feat, on=["race_id", "horse_id"], how="left")
    else:
        for col in _dtr_cols:
            df[col] = np.nan

    # C5: Vaihe 7 — trendit ja rataolot-preferenssi
    _c5_cols = ["km_time_trend", "prize_money_trend", "track_condition_win_rate"]
    if horse_starts is not None and len(horse_starts) > 0:
        km_feat = km_time_trend_features(df, horse_starts)
        km_feat["race_id"] = km_feat["race_id"].astype(df["race_id"].dtype)
        km_feat["horse_id"] = km_feat["horse_id"].astype(df["horse_id"].dtype)
        df = df.merge(km_feat, on=["race_id", "horse_id"], how="left")

        prize_feat = prize_money_trend_features(df, horse_starts)
        prize_feat["race_id"] = prize_feat["race_id"].astype(df["race_id"].dtype)
        prize_feat["horse_id"] = prize_feat["horse_id"].astype(df["horse_id"].dtype)
        df = df.merge(prize_feat, on=["race_id", "horse_id"], how="left")

        cond_feat = track_condition_win_rate_features(df, horse_starts, races)
        cond_feat["race_id"] = cond_feat["race_id"].astype(df["race_id"].dtype)
        cond_feat["horse_id"] = cond_feat["horse_id"].astype(df["horse_id"].dtype)
        df = df.merge(cond_feat, on=["race_id", "horse_id"], how="left")
    else:
        for col in _c5_cols:
            df[col] = np.nan

    # B2: sukutaulupiirteet — vaatii sekä horse_starts että horses-parametrin
    if horse_starts is not None and horses is not None:
        df = sire_features(df, horses, horse_starts)

    # 13: Muutospiirteet — ohjastajan vaihto ja matkamuutos edelliseen starttiin
    # driver_changed: 1 jos eri kuski kuin viimeisin horse_starts-startti (point-in-time)
    # distance_change_m: nykyinen matka - edellinen matka (metreinä)
    # prev_prize_won: edellisen startin palkinto (luokkamuutos-proxy)
    _chg_cols = ["driver_changed", "distance_change_m", "prev_prize_won"]
    if horse_starts is not None and len(horse_starts) > 0:
        chg_feat = change_features(df, horse_starts, races)
        chg_feat["race_id"] = chg_feat["race_id"].astype(df["race_id"].dtype)
        chg_feat["horse_id"] = chg_feat["horse_id"].astype(df["horse_id"].dtype)
        df = df.merge(chg_feat, on=["race_id", "horse_id"], how="left")
    else:
        for col in _chg_cols:
            df[col] = np.nan

    # driver_quality_signal: kuskin laatu muutostilanteessa
    # Kuvaa muutoksen suuntaa osittain: korkea arvo = vaihto hyvään kuskiin.
    # Puuttuva puoli (vanhan kuskin laatu) vaatisi erillisen hist-hakuun.
    if "driver_changed" in df.columns and "driver_win_rate_365d" in df.columns:
        df["driver_quality_signal"] = df["driver_win_rate_365d"].where(
            df["driver_changed"] == 1.0
        )

    # M1: Markkinaodds-todennäköisyys (win_odds_final → devigoitu implied prob)
    # Treenauksessa: win_odds_final saatavilla → feature lasketaan.
    # Ennustuksessa: win_odds_final=NULL → NaN → dashboardissa täytetään
    # live-kertoimilla (ks. app.py _inject_live_market_odds).
    mkt = market_odds_feature(df)
    mkt["race_id"] = mkt["race_id"].astype(df["race_id"].dtype)
    mkt["horse_id"] = mkt["horse_id"].astype(df["horse_id"].dtype)
    df = df.merge(mkt, on=["race_id", "horse_id"], how="left")

    return df
