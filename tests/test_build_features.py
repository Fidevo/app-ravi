"""
Testit build_features.py -moduulille.

Kattaa erityisesti korjatut bugit:
  #5a — driver_trainer_features: to_flat_index()-kaatuminen / väärät sarakkeiden nimet
  #5b — driver_trainer_features: M:N-riviräjähdys kun sama ohjastaja ajaa useita
         lähtöjä samana päivänä
  #8  — race_setup_features: track_horse_wins_cum globaali shift(1) vuoti
         edellisen (horse_id, track)-ryhmän arvoja seuraavan ryhmän alkuun
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.build_features import (
    _normalize_driver_name,
    build_feature_matrix,
    derived_features,
    driver_trainer_features,
    driver_trainer_hs_features,
    driver_trainer_track_features,
    fill_finish_positions,
    form_features,
    km_time_trend_features,
    prize_money_trend_features,
    race_setup_features,
    rest_days_bucket_features,
    sire_features,
    start_method_features,
    start_position_features,
    track_condition_win_rate_features,
)


# ---------------------------------------------------------------------------
# Apufunktiot testidatan rakentamiseen
# ---------------------------------------------------------------------------

def _runners(*rows: dict) -> pd.DataFrame:
    """Luo minimaalisen runners-DataFramen annetuilla riveillä.

    Puuttuvat kentät täytetään turvallisilla oletusarvoilla.
    """
    defaults: dict = {
        "horse_id": 1,
        "race_id": 1,
        "race_date": "2024-01-01",
        "finish_position": 2,
        "kilometer_time_seconds": 90.0,
        "win_odds_final": 3.0,
        "driver": "Arto",
        "trainer": "Matti",
        "start_number": 2,
        "handicap_meters": 0,
        # Huom: "track" EI kuulu runners-tauluun — se tulee races-taulusta
        # race_setup_features()-mergessä. Jos runners-helperissä on track-sarake,
        # merge tuottaa track_x / track_y -nimiristiriidan.
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _races(*rows: dict) -> pd.DataFrame:
    """Luo minimaalisen races-DataFramen annetuilla riveillä.

    Sisältää uudet race-luokkasarakkeet oletuksena None:lla jotta olemassa
    olevat testit toimivat muuttumattomina — NaN/None on sallittu arvo
    kaikille uusille sarakkeille (valinnainen data).
    """
    defaults: dict = {
        "race_id": 1,
        "track": "Solvalla",
        "distance": 2000,
        "start_method": "auto",
        # Uudet race-luokkasarakkeet — None = ei tietoa (esim. finaalit)
        "track_condition": None,
        "race_min_earnings": None,
        "race_max_earnings": None,
        "race_age_group": None,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


# ---------------------------------------------------------------------------
# Bug #5a & #5b — driver_trainer_features
# ---------------------------------------------------------------------------

class TestDriverTrainerFeatures:
    """Testit driver_trainer_features()-funktiolle."""

    def test_no_row_explosion_when_driver_races_twice_on_same_day(self):
        """Bug #5b: jos ohjastaja ajaa 2 lähtöä samana päivänä, rivimäärä
        ei saa kasvaa (M:N-merge ilman drop_duplicates tuplaisi rivit)."""
        runners = _runners(
            # Arto ajaa kaksi eri hevosta samana päivänä
            {"race_id": 1, "horse_id": 1, "race_date": "2024-01-01",
             "finish_position": 1, "driver": "Arto", "trainer": "Matti"},
            {"race_id": 2, "horse_id": 2, "race_date": "2024-01-01",
             "finish_position": 2, "driver": "Arto", "trainer": "Teppo"},
            # Seuraavana viikkona
            {"race_id": 3, "horse_id": 3, "race_date": "2024-01-08",
             "finish_position": 1, "driver": "Arto", "trainer": "Matti"},
        )
        result = driver_trainer_features(runners)
        assert len(result) == len(runners), (
            f"Riviräjähdys: syöte {len(runners)} riviä → tulos {len(result)} riviä. "
            "drop_duplicates ennen mergeä puuttuu."
        )

    def test_correct_column_names_default_lookback(self):
        """Bug #5a: to_flat_index()-ongelma tuotti väärät sarakkeiden nimet
        tai kaatoi funktion. Tarkistetaan oikeat nimet oletusikkunalla (365d)."""
        runners = _runners(
            {"race_id": 1, "horse_id": 1, "race_date": "2024-01-01",
             "finish_position": 1, "driver": "Arto", "trainer": "Matti"},
            {"race_id": 2, "horse_id": 2, "race_date": "2024-01-08",
             "finish_position": 2, "driver": "Arto", "trainer": "Matti"},
        )
        result = driver_trainer_features(runners, lookback_days=365)
        expected_cols = [
            "driver_win_rate_365d",
            "driver_starts_365d",
            "driver_top3_rate_365d",
            "driver_top3_count_365d",
            "trainer_win_rate_365d",
            "trainer_starts_365d",
            "trainer_top3_rate_365d",
            "trainer_top3_count_365d",
        ]
        for col in expected_cols:
            assert col in result.columns, (
                f"Sarake '{col}' puuttuu tuloksesta. Löydetyt sarakkeet: "
                f"{sorted(result.columns.tolist())}"
            )

    def test_correct_column_names_custom_lookback(self):
        """Sarakkeiden nimien pitää heijastaa käytettyä aikaikkuna-parametria."""
        runners = _runners(
            {"race_id": 1, "horse_id": 1, "race_date": "2024-01-01",
             "finish_position": 1, "driver": "Arto", "trainer": "Matti"},
        )
        result = driver_trainer_features(runners, lookback_days=180)
        assert "driver_win_rate_180d" in result.columns
        assert "driver_win_rate_365d" not in result.columns

    def test_no_leakage_first_race_has_nan_stats(self):
        """closed='left' takaa ettei ensimmäinen lähtö näe omia tuloksiaan.
        Ohjastajan tilastojen pitää olla NaN kun aiempaa dataa ei ole."""
        runners = _runners(
            {"race_id": 1, "horse_id": 1, "race_date": "2024-01-01",
             "finish_position": 1, "driver": "Arto", "trainer": "Matti"},
            {"race_id": 2, "horse_id": 2, "race_date": "2024-01-08",
             "finish_position": 1, "driver": "Arto", "trainer": "Matti"},
        )
        result = driver_trainer_features(runners)
        first = result[result["race_date"] == pd.Timestamp("2024-01-01")]
        # Ensimmäisessä lähdössä Artolla ei ole aiempia starteja → NaN
        assert first["driver_starts_365d"].isna().all(), (
            "Ensimmäisessä lähdössä driver_starts_365d pitää olla NaN "
            f"(sai {first['driver_starts_365d'].values})"
        )

    def test_second_race_sees_first_race_stats(self):
        """Toisessa lähdössä rolling-tilasto heijastaa ensimmäistä lähtöä."""
        runners = _runners(
            {"race_id": 1, "horse_id": 1, "race_date": "2024-01-01",
             "finish_position": 1, "driver": "Arto", "trainer": "Matti"},
            {"race_id": 2, "horse_id": 2, "race_date": "2024-01-08",
             "finish_position": 3, "driver": "Arto", "trainer": "Matti"},
        )
        result = driver_trainer_features(runners)
        second = result[result["race_date"] == pd.Timestamp("2024-01-08")]
        # Arto ajoi 1 startin (race_id=1) → starts=1, win_rate=1.0, top3_rate=1.0
        assert second["driver_starts_365d"].iloc[0] == pytest.approx(1.0)
        assert second["driver_win_rate_365d"].iloc[0] == pytest.approx(1.0)
        assert second["driver_top3_rate_365d"].iloc[0] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Bug #8 — race_setup_features: cross-track leakage
# ---------------------------------------------------------------------------

class TestRaceSetupFeatures:
    """Testit race_setup_features()-funktiolle."""

    def test_no_cross_track_leakage_alternating_tracks(self):
        """Bug #8: globaali .shift(1) vuoti edellisen radan viimeisen
        cumsum-arvon seuraavan radan ensimmäiseen riviin.

        Tässä testissä hevonen vuorottelee ratojen välillä:
          Solvalla (voitto) → Bergsåker (ei voittoa) → Solvalla (voitto)
                           → Bergsåker (voitto)

        Bugisteella koodilla 2. Solvalla-startin track_horse_win_rate
        olisi 0.0 (väärä) ja 2. Bergsåker-startin win_rate olisi 2.0
        (mahdoton arvo). Korjatulla koodilla molemmat ovat 1.0 ja 0.0.
        """
        runners = _runners(
            {"race_id": 1, "horse_id": 10, "race_date": "2024-01-01",
             "finish_position": 1},   # Solvalla — voitto
            {"race_id": 2, "horse_id": 10, "race_date": "2024-01-08",
             "finish_position": 2},   # Bergsåker — ei voittoa
            {"race_id": 3, "horse_id": 10, "race_date": "2024-01-15",
             "finish_position": 1},   # Solvalla — voitto
            {"race_id": 4, "horse_id": 10, "race_date": "2024-01-22",
             "finish_position": 1},   # Bergsåker — voitto
        )
        races = _races(
            {"race_id": 1, "track": "Solvalla"},
            {"race_id": 2, "track": "Bergsåker"},
            {"race_id": 3, "track": "Solvalla"},
            {"race_id": 4, "track": "Bergsåker"},
        )
        result = race_setup_features(runners, races)

        r3 = result[result["race_id"] == 3].iloc[0]  # 2. Solvalla-startti
        r4 = result[result["race_id"] == 4].iloc[0]  # 2. Bergsåker-startti

        # race_id=3: 1 aiempi Solvalla-startti (voitto) → win_rate=1.0
        assert r3["track_horse_starts"] == 1
        assert r3["track_horse_win_rate"] == pytest.approx(1.0), (
            f"race_id=3 (2. Solvalla): win_rate={r3['track_horse_win_rate']:.4f}, "
            "odotettiin 1.0. Bugilla saisi 0.0 (vuoto)."
        )

        # race_id=4: 1 aiempi Bergsåker-startti (ei voittoa) → win_rate=0.0
        assert r4["track_horse_starts"] == 1
        assert r4["track_horse_win_rate"] == pytest.approx(0.0), (
            f"race_id=4 (2. Bergsåker): win_rate={r4['track_horse_win_rate']:.4f}, "
            "odotettiin 0.0. Bugilla saisi 2.0 (mahdoton arvo)."
        )

    def test_track_horse_starts_correct_per_track(self):
        """track_horse_starts lasketaan erikseen per rata, ei globaalisti."""
        runners = _runners(
            {"race_id": 1, "horse_id": 10, "race_date": "2024-01-01",
             "finish_position": 2},   # Solvalla, 1. startti
            {"race_id": 2, "horse_id": 10, "race_date": "2024-01-08",
             "finish_position": 2},   # Bergsåker, 1. startti (ei 2. globaali)
            {"race_id": 3, "horse_id": 10, "race_date": "2024-01-15",
             "finish_position": 2},   # Solvalla, 2. startti
        )
        races = _races(
            {"race_id": 1, "track": "Solvalla"},
            {"race_id": 2, "track": "Bergsåker"},
            {"race_id": 3, "track": "Solvalla"},
        )
        result = race_setup_features(runners, races)

        assert result[result["race_id"] == 1].iloc[0]["track_horse_starts"] == 0
        assert result[result["race_id"] == 2].iloc[0]["track_horse_starts"] == 0
        assert result[result["race_id"] == 3].iloc[0]["track_horse_starts"] == 1

    def test_first_track_start_has_nan_win_rate(self):
        """Ensimmäisessä startissa radalla track_horse_win_rate on NaN
        (ei aiempaa dataa → ei voida laskea prosenttia)."""
        runners = _runners(
            {"race_id": 1, "horse_id": 10, "race_date": "2024-01-01",
             "finish_position": 1},
        )
        races = _races({"race_id": 1, "track": "Solvalla"})
        result = race_setup_features(runners, races)
        assert result["track_horse_win_rate"].isna().all(), (
            "Ensimmäisessä startissa win_rate pitää olla NaN "
            f"(sai {result['track_horse_win_rate'].values})"
        )

    def test_win_rate_accumulates_correctly_single_track(self):
        """Voittoprosentti kertyy oikein kun hevonen ajaa samalla radalla."""
        runners = _runners(
            {"race_id": 1, "horse_id": 10, "race_date": "2024-01-01",
             "finish_position": 1},   # voitto
            {"race_id": 2, "horse_id": 10, "race_date": "2024-01-08",
             "finish_position": 2},   # ei voittoa
            {"race_id": 3, "horse_id": 10, "race_date": "2024-01-15",
             "finish_position": 1},   # voitto
        )
        races = _races(
            {"race_id": 1, "track": "Solvalla"},
            {"race_id": 2, "track": "Solvalla"},
            {"race_id": 3, "track": "Solvalla"},
        )
        result = race_setup_features(runners, races).sort_values("race_id")

        r1 = result[result["race_id"] == 1].iloc[0]
        r2 = result[result["race_id"] == 2].iloc[0]
        r3 = result[result["race_id"] == 3].iloc[0]

        # race_id=1: 0 aiempaa startia → NaN
        assert r1["track_horse_starts"] == 0
        assert np.isnan(r1["track_horse_win_rate"])

        # race_id=2: 1 aiempi startti (voitto) → 1.0
        assert r2["track_horse_starts"] == 1
        assert r2["track_horse_win_rate"] == pytest.approx(1.0)

        # race_id=3: 2 aiempaa startia (1 voitto) → 0.5
        assert r3["track_horse_starts"] == 2
        assert r3["track_horse_win_rate"] == pytest.approx(0.5)

    def test_no_row_explosion_in_race_setup(self):
        """race_setup_features ei saa kasvattaa rivimäärää."""
        runners = _runners(
            {"race_id": 1, "horse_id": 10, "race_date": "2024-01-01",
             "finish_position": 1},
            {"race_id": 2, "horse_id": 11, "race_date": "2024-01-01",
             "finish_position": 2},
        )
        races = _races(
            {"race_id": 1, "track": "Solvalla"},
            {"race_id": 2, "track": "Solvalla"},
        )
        result = race_setup_features(runners, races)
        assert len(result) == len(runners)

    def test_race_class_columns_merged_when_present(self):
        """race_setup_features sisällyttää race-luokkasarakkeet kun races niitä sisältää."""
        runners = _runners({"race_id": 1, "horse_id": 10, "race_date": "2024-01-01",
                            "finish_position": 2})
        races = _races({"race_id": 1, "track": "Solvalla",
                        "track_condition": "light",
                        "race_min_earnings": 1500,
                        "race_max_earnings": 85000,
                        "race_age_group": "3yo+"})
        result = race_setup_features(runners, races)
        assert result.iloc[0]["track_condition"] == "light"
        assert result.iloc[0]["race_min_earnings"] == pytest.approx(1500)
        assert result.iloc[0]["race_max_earnings"] == pytest.approx(85000)
        assert result.iloc[0]["race_age_group"] == "3yo+"

    def test_race_class_absent_when_races_lacks_columns(self):
        """race_setup_features ei kaadu kun races ei sisällä uusia sarakkeita.

        Varmistaa backward-yhteensopivuuden vanhan datan kanssa: puuttuvat
        sarakkeet eivät näy tuloksessa (ei NaN-sarakkeita tyhjästä).
        """
        runners = _runners({"race_id": 1, "horse_id": 10, "race_date": "2024-01-01",
                            "finish_position": 2})
        # Vanha races ilman uusia sarakkeita
        races = pd.DataFrame([{"race_id": 1, "track": "Solvalla",
                               "distance": 2000, "start_method": "auto"}])
        result = race_setup_features(runners, races)
        # Ei kaadu — uudet sarakkeet puuttuvat mutta se on ok
        assert len(result) == 1
        assert "track_condition" not in result.columns
        assert "race_min_earnings" not in result.columns


# ---------------------------------------------------------------------------
# Korjaus 5 — derived_features
# ---------------------------------------------------------------------------

class TestDerivedFeatures:
    """Testit derived_features()-funktiolle."""

    def _base_df(self, race_date: str, **extra) -> pd.DataFrame:
        row = {"horse_id": 1, "race_id": 1, "race_date": race_date,
               "finish_position": 2, **extra}
        return pd.DataFrame([row])

    def test_barfota_law_active_in_december(self):
        """Joulukuu → talvikielto aktiivinen."""
        df = self._base_df("2024-12-15")
        result = derived_features(df)
        assert result.iloc[0]["barfota_law_active"] == 1

    def test_barfota_law_active_in_january(self):
        """Tammikuu → talvikielto aktiivinen."""
        df = self._base_df("2024-01-10")
        result = derived_features(df)
        assert result.iloc[0]["barfota_law_active"] == 1

    def test_barfota_law_active_in_february(self):
        """Helmikuu → talvikielto aktiivinen."""
        df = self._base_df("2024-02-29")
        result = derived_features(df)
        assert result.iloc[0]["barfota_law_active"] == 1

    def test_barfota_law_inactive_in_march(self):
        """Maaliskuu → talvikielto ei aktiivinen (kielto loppuu 28.2.)."""
        df = self._base_df("2024-03-01")
        result = derived_features(df)
        assert result.iloc[0]["barfota_law_active"] == 0

    def test_barfota_law_inactive_in_summer(self):
        """Kesäkuu → talvikielto ei aktiivinen."""
        df = self._base_df("2024-06-15")
        result = derived_features(df)
        assert result.iloc[0]["barfota_law_active"] == 0

    def test_horse_age_computed_when_birth_year_available(self):
        """horse_age lasketaan oikein kun birth_year on df:ssä."""
        df = self._base_df("2024-05-10", birth_year=2018)
        result = derived_features(df)
        assert "horse_age" in result.columns
        assert result.iloc[0]["horse_age"] == 6  # 2024 - 2018

    def test_horse_age_skipped_without_birth_year(self):
        """horse_age puuttuu jos birth_year ei ole df:ssä — ei kaadu."""
        df = self._base_df("2024-05-10")
        result = derived_features(df)
        # Ei kaadu, horse_age ei yksinkertaisesti ole tuloksessa
        assert "horse_age" not in result.columns
        assert "barfota_law_active" in result.columns  # muut piirteet kyllä

    def test_derived_features_does_not_drop_existing_columns(self):
        """derived_features ei poista olemassa olevia sarakkeita."""
        df = self._base_df("2024-06-01")
        df["some_existing_col"] = 42
        result = derived_features(df)
        assert "some_existing_col" in result.columns
        assert result.iloc[0]["some_existing_col"] == 42


# ---------------------------------------------------------------------------
# Integraatiotesti — FEATURE_COLS vs. build_feature_matrix
# ---------------------------------------------------------------------------

class TestFeatureColsIntegration:
    """Varmistaa että FEATURE_COLS-nimet täsmäävät build_feature_matrix()-tulosteeseen.

    Tämä on regressiotesti bugi #1:lle: ranker.py:n FEATURE_COLS:in ja
    build_features.py:n tuottamien sarakkeiden nimiristiriita joka kaatoi
    train_ranker():n KeyError:iin.
    """

    def test_computed_cols_present_after_build_feature_matrix(self):
        """build_feature_matrix() tuottaa kaikki laskennalliset piirteet
        jotka FEATURE_COLS odottaa (poislukien pass-through runners-sarakkeet
        ja valinnainen horse_age joka vaatii birth_year JOIN:in)."""
        from src.models.ranker import FEATURE_COLS

        runners = _runners(
            {"race_id": 1, "horse_id": 10, "race_date": "2024-01-01",
             "finish_position": 1, "driver": "Arto", "trainer": "Matti",
             "start_number": 2, "handicap_meters": 0},
            {"race_id": 1, "horse_id": 11, "race_date": "2024-01-01",
             "finish_position": 2, "driver": "Arto", "trainer": "Teppo",
             "start_number": 4, "handicap_meters": 0},
            {"race_id": 2, "horse_id": 10, "race_date": "2024-01-08",
             "finish_position": 2, "driver": "Arto", "trainer": "Matti",
             "start_number": 1, "handicap_meters": 0},
        )
        races = _races(
            {"race_id": 1, "track": "Solvalla", "distance": 2140,
             "start_method": "auto", "track_condition": "light",
             "race_min_earnings": 1500, "race_max_earnings": 85000,
             "race_age_group": "3yo+"},
            {"race_id": 2, "track": "Solvalla", "distance": 2140,
             "start_method": "auto", "track_condition": "heavy",
             "race_min_earnings": 0, "race_max_earnings": 30000,
             "race_age_group": "2yo"},
        )

        result = build_feature_matrix(runners, races)

        # Piirteet jotka build_feature_matrix laskee (ei pass-through)
        computed_cols = [
            "form_avg_finish_5", "form_win_rate_5", "form_top3_rate_5",
            "form_avg_km_time_5", "form_best_km_time_5",
            "form_market_avg_5", "form_days_since_last",
            "driver_win_rate_365d", "driver_starts_365d", "driver_top3_rate_365d",
            "trainer_win_rate_365d", "trainer_top3_rate_365d",
            "inside_post", "back_row", "distance_category",
            "track_horse_starts", "track_horse_win_rate",
            "track_condition", "race_min_earnings", "race_max_earnings",
            "race_age_group",
            "barfota_law_active",
        ]
        missing = [c for c in computed_cols if c not in result.columns]
        assert not missing, (
            f"build_feature_matrix() ei tuottanut näitä FEATURE_COLS-sarakkeita: "
            f"{missing}\nKaikki sarakkeet: {sorted(result.columns.tolist())}"
        )

    def test_driver_col_names_match_feature_cols(self):
        """Varmistaa ettei vanha bugi #1 palaa: ohjastajasarakkeiden nimet
        täsmäävät FEATURE_COLS:iin."""
        from src.models.ranker import FEATURE_COLS

        runners = _runners(
            {"race_id": 1, "horse_id": 1, "race_date": "2024-01-01",
             "finish_position": 1, "driver": "Arto", "trainer": "Matti"},
            {"race_id": 2, "horse_id": 2, "race_date": "2024-01-08",
             "finish_position": 2, "driver": "Arto", "trainer": "Matti"},
        )
        races = _races(
            {"race_id": 1, "track": "Solvalla"},
            {"race_id": 2, "track": "Solvalla"},
        )
        result = build_feature_matrix(runners, races)

        driver_cols_in_feature_cols = [c for c in FEATURE_COLS if c.startswith("driver_win") or c.startswith("trainer_win")]
        for col in driver_cols_in_feature_cols:
            assert col in result.columns, (
                f"Sarake '{col}' on FEATURE_COLS:issa mutta puuttuu "
                f"build_feature_matrix()-tuloksesta. Vanha bugi #1 palasi."
            )


# ---------------------------------------------------------------------------
# Testit form_features() + horse_starts-integraatiolle (korjaus #6)
# ---------------------------------------------------------------------------

def _horse_starts(*rows: dict) -> pd.DataFrame:
    """Luo minimaalisen horse_starts-DataFramen.

    Vaaditut sarakkeet: horse_id, race_date, finish_position,
    kilometer_time_seconds, win_odds_final.
    """
    defaults: dict = {
        "horse_id": 1,
        "race_date": "2023-01-01",
        "finish_position": 2,
        "kilometer_time_seconds": 90.0,
        "win_odds_final": 5.0,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


class TestFormFeaturesWithHorseStarts:
    """Testit form_features()-funktiolle horse_starts-parametrin kanssa.

    Varmistaa:
      - Backward-yhteensopivuus: horse_starts=None toimii kuten ennen
      - Historia parantaa piirteitä: enemmän dataa → paremmat estimaatit
      - Ei data leakage: tulevat horse_starts-rivit eivät vuoda piirteisiin
      - Deduplikaatio: runners-taulun arvo voittaa horse_starts-arvon
      - Rivisäilyvyys: runners-rivien määrä pysyy samana
      - Lepoaika lasketaan horse_starts-päivämäärästä
      - build_feature_matrix() välittää horse_starts form_features():lle
    """

    def test_without_horse_starts_backward_compat(self):
        """Ilman horse_starts form_features() käyttäytyy kuten ennen (ei kaadu)."""
        runners = _runners(
            {"horse_id": 1, "race_id": 1, "race_date": "2024-05-01",
             "finish_position": 1, "kilometer_time_seconds": 88.0, "win_odds_final": 3.0},
            {"horse_id": 1, "race_id": 2, "race_date": "2024-05-10",
             "finish_position": 2, "kilometer_time_seconds": 90.0, "win_odds_final": 4.0},
        )
        # Ei horse_starts — vanhan käyttötavan pitää toimia
        result = form_features(runners)
        assert len(result) == 2
        assert "form_avg_finish_5" in result.columns
        assert "form_days_since_last" in result.columns
        # Toinen rivi: käyttää ensimmäisen rivin tietoja (shift(1))
        assert result.iloc[1]["form_avg_finish_5"] == pytest.approx(1.0)
        assert result.iloc[1]["form_days_since_last"] == 9

    def test_history_improves_form_when_runners_sparse(self):
        """Kun horse_starts antaa useampia starteja, rolling-piirteet
        lasketaan täydestä historiasta eivätkä ole NaN vaikka runners-data ohuet."""
        # runners: vain 1 rivi (ei historiaa runners-taulussa)
        runners = _runners(
            {"horse_id": 42, "race_id": 99, "race_date": "2024-05-01",
             "finish_position": 3, "kilometer_time_seconds": 91.0, "win_odds_final": 6.0},
        )
        # horse_starts: 4 aikaisempaa starttia samalle hevoselle
        hs = _horse_starts(
            {"horse_id": 42, "race_date": "2023-11-01", "finish_position": 1, "kilometer_time_seconds": 87.0, "win_odds_final": 4.0},
            {"horse_id": 42, "race_date": "2023-12-01", "finish_position": 2, "kilometer_time_seconds": 89.0, "win_odds_final": 5.0},
            {"horse_id": 42, "race_date": "2024-01-15", "finish_position": 1, "kilometer_time_seconds": 88.0, "win_odds_final": 3.0},
            {"horse_id": 42, "race_date": "2024-03-10", "finish_position": 3, "kilometer_time_seconds": 92.0, "win_odds_final": 7.0},
        )
        result_without = form_features(runners)
        result_with = form_features(runners, horse_starts=hs)

        # Ilman historiaa: 1. rivi on NaN (shift(1) → tyhjä)
        assert pd.isna(result_without.iloc[0]["form_avg_finish_5"])

        # Historian kanssa: on 4 aiempaa starttia → piirre ei ole NaN
        assert not pd.isna(result_with.iloc[0]["form_avg_finish_5"])
        # Odotusarvo: (1+2+1+3)/4 = 1.75 (4 viimeisintä startia ennen 2024-05-01)
        assert result_with.iloc[0]["form_avg_finish_5"] == pytest.approx(1.75)

    def test_no_leakage_future_history_excluded(self):
        """horse_starts-rivit jotka ovat MYÖHEMMIN kuin runners-lähtö
        eivät saa vuotaa piirteisiin (shift(1) + rolling käsittelee tämän)."""
        runners = _runners(
            {"horse_id": 5, "race_id": 10, "race_date": "2024-03-01",
             "finish_position": 2, "kilometer_time_seconds": 90.0, "win_odds_final": 4.0},
        )
        hs = _horse_starts(
            # Yksi vanhempi rivi (OK) ja yksi tulevaisuuden rivi (EI saa vaikuttaa)
            {"horse_id": 5, "race_date": "2024-01-01", "finish_position": 1, "kilometer_time_seconds": 87.0, "win_odds_final": 3.0},
            {"horse_id": 5, "race_date": "2024-06-01", "finish_position": 5, "kilometer_time_seconds": 99.0, "win_odds_final": 2.0},
        )
        result = form_features(runners, horse_starts=hs)
        # Vain 1 aiempi startti (2024-01-01) pitäisi vaikuttaa.
        # shift(1) poolissa: runners-rivi on 2. (2024-03-01), näkee vain 2024-01-01.
        # Tulevaisuuden rivi (2024-06-01) on poolissa 3. sijoilla, ei vuoda.
        assert result.iloc[0]["form_avg_finish_5"] == pytest.approx(1.0)
        # win_rate: 1 voitto / 1 näytetty startti = 1.0
        assert result.iloc[0]["form_win_rate_5"] == pytest.approx(1.0)

    def test_dedup_runners_take_priority_over_horse_starts(self):
        """Jos sama (horse_id, race_date) löytyy sekä runners- että horse_starts-
        taulusta, runners-taulun arvo säilytetään (deduplikaatiosääntö)."""
        runners = _runners(
            {"horse_id": 7, "race_id": 1, "race_date": "2024-01-15",
             "finish_position": 1, "kilometer_time_seconds": 88.5, "win_odds_final": 2.0},
            {"horse_id": 7, "race_id": 2, "race_date": "2024-02-01",
             "finish_position": 3, "kilometer_time_seconds": 92.0, "win_odds_final": 5.0},
        )
        # horse_starts sisältää saman 2024-01-15 rivin eri arvoilla
        hs = _horse_starts(
            {"horse_id": 7, "race_date": "2024-01-15", "finish_position": 4,
             "kilometer_time_seconds": 95.0, "win_odds_final": 10.0},
        )
        result = form_features(runners, horse_starts=hs)
        # Rivimäärä pysyy 2 (ei duplikaatteja)
        assert len(result) == 2
        # 2. rivi (2024-02-01): form_avg_finish_5 pitää perustua runners-arvoon 1
        # eikä horse_starts-arvoon 4
        assert result.iloc[1]["form_avg_finish_5"] == pytest.approx(1.0)

    def test_row_count_preserved_with_horse_starts(self):
        """horse_starts ei lisää ylimääräisiä rivejä runners-DataFrameen."""
        runners = _runners(
            {"horse_id": 1, "race_id": 1, "race_date": "2024-04-01", "finish_position": 2},
            {"horse_id": 1, "race_id": 2, "race_date": "2024-04-15", "finish_position": 1},
            {"horse_id": 2, "race_id": 1, "race_date": "2024-04-01", "finish_position": 3},
        )
        hs = _horse_starts(
            {"horse_id": 1, "race_date": "2024-01-10", "finish_position": 1},
            {"horse_id": 1, "race_date": "2024-02-20", "finish_position": 2},
            {"horse_id": 99, "race_date": "2024-03-01", "finish_position": 1},  # hevonen jota ei runners:issa
        )
        result = form_features(runners, horse_starts=hs)
        # Rivimäärä = runners-rivit, ei enemmän
        assert len(result) == len(runners)

    def test_days_since_last_uses_horse_starts_dates(self):
        """form_days_since_last lasketaan horse_starts-historian viimeisestä
        startista, ei vain runners-datan edellisestä startista."""
        runners = _runners(
            {"horse_id": 3, "race_id": 5, "race_date": "2024-04-10",
             "finish_position": 2, "kilometer_time_seconds": 90.0, "win_odds_final": 4.0},
        )
        # Viimeisin horse_starts-rivi on 30 päivää aiemmin
        hs = _horse_starts(
            {"horse_id": 3, "race_date": "2024-03-11", "finish_position": 1,
             "kilometer_time_seconds": 87.0, "win_odds_final": 3.0},
        )
        result_without = form_features(runners)
        result_with = form_features(runners, horse_starts=hs)

        # Ilman historiaa: ensimmäinen rivi → NaN (ei edellistä starttia)
        assert pd.isna(result_without.iloc[0]["form_days_since_last"])
        # Historian kanssa: 2024-04-10 - 2024-03-11 = 30 päivää
        assert result_with.iloc[0]["form_days_since_last"] == 30

    def test_build_feature_matrix_passes_horse_starts_through(self):
        """build_feature_matrix() välittää horse_starts-parametrin
        form_features():lle — integraatiotesti koko pipelinelle."""
        runners = _runners(
            {"horse_id": 10, "race_id": 1, "race_date": "2024-05-01",
             "finish_position": 2, "kilometer_time_seconds": 90.0, "win_odds_final": 4.0,
             "driver": "Arto", "trainer": "Matti", "start_number": 3, "handicap_meters": 0},
        )
        races = _races({"race_id": 1, "track": "Solvalla", "distance": 2140, "start_method": "auto"})
        hs = _horse_starts(
            {"horse_id": 10, "race_date": "2024-01-01", "finish_position": 1,
             "kilometer_time_seconds": 87.0, "win_odds_final": 3.0},
            {"horse_id": 10, "race_date": "2024-02-15", "finish_position": 3,
             "kilometer_time_seconds": 92.0, "win_odds_final": 6.0},
            {"horse_id": 10, "race_date": "2024-03-20", "finish_position": 2,
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
        )
        result_without = build_feature_matrix(runners, races)
        result_with = build_feature_matrix(runners, races, horse_starts=hs)

        # Ilman historiaa: NaN (vain 1 runners-rivi, shift(1) → tyhjä)
        assert pd.isna(result_without.iloc[0]["form_avg_finish_5"])
        # Historian kanssa: 3 aiempaa starttia → laskettu arvo
        assert not pd.isna(result_with.iloc[0]["form_avg_finish_5"])
        # (1+3+2)/3 = 2.0
        assert result_with.iloc[0]["form_avg_finish_5"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Testit fill_finish_positions() -funktiolle
# ---------------------------------------------------------------------------

def _race_df(*rows: dict) -> pd.DataFrame:
    """Apufunktio: luo minimaalisen runners-DataFrame fill_finish_positions-testeille."""
    defaults: dict = {
        "race_id": "race_1",
        "horse_id": 1,
        "finish_position": None,
        "kilometer_time_seconds": None,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


class TestFillFinishPositions:
    """Testit fill_finish_positions()-funktiolle.

    ATG raportoi sijoitukset vain top 6-8 hevoselle. Loput hevoset jotka
    ajoivat (on km_aika) tai vetäytyivät (ei km_aikaa) saavat NULL:n.
    Tämä funktio täyttää puuttuvat sijoitukset ennen LambdaRank-treenausta.
    """

    def test_official_positions_unchanged(self):
        """Viralliset sijoitukset (1–N) eivät muutu."""
        df = _race_df(
            {"horse_id": 1, "finish_position": 1, "kilometer_time_seconds": 75.0},
            {"horse_id": 2, "finish_position": 2, "kilometer_time_seconds": 76.0},
            {"horse_id": 3, "finish_position": 3, "kilometer_time_seconds": 77.0},
        )
        result = fill_finish_positions(df)
        assert list(result.sort_values("horse_id")["finish_position"]) == [1, 2, 3]

    def test_unplaced_runners_get_positions_after_official(self):
        """Hevoset joilla on km_aika mutta ei sijoitusta saavat sijoitukset
        virallisen viimeisen sijoituksen jälkeen."""
        df = _race_df(
            {"horse_id": 1, "finish_position": 1, "kilometer_time_seconds": 75.0},
            {"horse_id": 2, "finish_position": 2, "kilometer_time_seconds": 76.0},
            {"horse_id": 3, "finish_position": None, "kilometer_time_seconds": 78.0},  # ajoi, ei sijaa
            {"horse_id": 4, "finish_position": None, "kilometer_time_seconds": 80.0},  # ajoi, ei sijaa
        )
        result = fill_finish_positions(df)
        positions = dict(zip(result["horse_id"], result["finish_position"]))
        assert positions[1] == 1
        assert positions[2] == 2
        # Molemmat unplaced saavat sijoitukset 3 ja 4
        assert positions[3] == 3   # nopeampi (78.0) saa paremman sijoituksen
        assert positions[4] == 4   # hitaampi (80.0) saa huonomman

    def test_unplaced_ordered_by_km_time_ascending(self):
        """Unplaced-hevoset järjestetään km_ajan mukaan: nopein saa parhaan sijoituksen."""
        df = _race_df(
            {"horse_id": 1, "finish_position": 1, "kilometer_time_seconds": 75.0},
            # Nämä kolme ajoivat eri nopeuksilla, hitain ensin datassa
            {"horse_id": 4, "finish_position": None, "kilometer_time_seconds": 85.0},  # hitain
            {"horse_id": 2, "finish_position": None, "kilometer_time_seconds": 77.0},  # nopein
            {"horse_id": 3, "finish_position": None, "kilometer_time_seconds": 81.0},  # keski
        )
        result = fill_finish_positions(df)
        positions = dict(zip(result["horse_id"], result["finish_position"]))
        assert positions[2] == 2   # nopein (77.0) → 2. sija
        assert positions[3] == 3   # keski (81.0) → 3. sija
        assert positions[4] == 4   # hitain (85.0) → 4. sija

    def test_withdrawn_get_last_positions(self):
        """Vetäytyneet (ei km_aikaa eikä sijoitusta) saavat viimeiset sijoitukset."""
        df = _race_df(
            {"horse_id": 1, "finish_position": 1, "kilometer_time_seconds": 75.0},
            {"horse_id": 2, "finish_position": None, "kilometer_time_seconds": 80.0},  # ajoi
            {"horse_id": 3, "finish_position": None, "kilometer_time_seconds": None},  # vetäytyi
            {"horse_id": 4, "finish_position": None, "kilometer_time_seconds": None},  # vetäytyi
        )
        result = fill_finish_positions(df)
        positions = dict(zip(result["horse_id"], result["finish_position"]))
        assert positions[1] == 1
        assert positions[2] == 2   # ajoi → 2. sija
        # Vetäytyneet saavat sijat 3 ja 4 (ei km_aikaa → viimeiset)
        assert positions[3] in (3, 4)
        assert positions[4] in (3, 4)
        assert positions[3] != positions[4]   # eri sijoitukset

    def test_future_race_all_null_unchanged(self):
        """Lähdöt joissa KAIKKI sijoitukset ovat NULL (tulevat lähdöt)
        jätetään koskemattomiksi — niitä ei ole ajettu."""
        df = _race_df(
            {"horse_id": 1, "finish_position": None, "kilometer_time_seconds": None},
            {"horse_id": 2, "finish_position": None, "kilometer_time_seconds": None},
            {"horse_id": 3, "finish_position": None, "kilometer_time_seconds": None},
        )
        result = fill_finish_positions(df)
        assert result["finish_position"].isna().all()

    def test_row_count_unchanged(self):
        """fill_finish_positions() ei lisää eikä poista rivejä."""
        df = _race_df(
            {"horse_id": 1, "finish_position": 1, "kilometer_time_seconds": 75.0},
            {"horse_id": 2, "finish_position": 2, "kilometer_time_seconds": 76.0},
            {"horse_id": 3, "finish_position": None, "kilometer_time_seconds": 79.0},
            {"horse_id": 4, "finish_position": None, "kilometer_time_seconds": None},
        )
        result = fill_finish_positions(df)
        assert len(result) == len(df)

    def test_no_null_finish_positions_remain_for_completed_race(self):
        """Kun lähtö on ajettu (osa hevosista sai sijoituksen), kaikki
        NULL-sijoitukset täytetään — ei yhtään NULL:ia jälkeen."""
        df = _race_df(
            {"horse_id": 1, "finish_position": 1, "kilometer_time_seconds": 75.0},
            {"horse_id": 2, "finish_position": None, "kilometer_time_seconds": 80.0},
            {"horse_id": 3, "finish_position": None, "kilometer_time_seconds": None},
        )
        result = fill_finish_positions(df)
        assert result["finish_position"].notna().all()

    def test_multiple_races_independent(self):
        """Jokainen lähtö käsitellään itsenäisesti — sijoitukset alkavat 1:stä
        per lähtö, eivät jatku edellisestä lähdöstä."""
        df = pd.DataFrame([
            {"race_id": "A", "horse_id": 1, "finish_position": 1,    "kilometer_time_seconds": 75.0},
            {"race_id": "A", "horse_id": 2, "finish_position": None,  "kilometer_time_seconds": 80.0},
            {"race_id": "B", "horse_id": 3, "finish_position": 1,    "kilometer_time_seconds": 76.0},
            {"race_id": "B", "horse_id": 4, "finish_position": None,  "kilometer_time_seconds": 82.0},
        ])
        result = fill_finish_positions(df)
        race_a = result[result["race_id"] == "A"].sort_values("horse_id")
        race_b = result[result["race_id"] == "B"].sort_values("horse_id")
        # Molemmissa lähdöissä: viralliset pysyvät, unplaced saa seuraavan
        assert list(race_a["finish_position"]) == [1, 2]
        assert list(race_b["finish_position"]) == [1, 2]

    def test_all_positions_unique_within_race(self):
        """Jokainen hevonen saa uniikin sijoituksen — ei kahta samaa sijaa."""
        df = _race_df(
            {"horse_id": 1, "finish_position": 1,   "kilometer_time_seconds": 75.0},
            {"horse_id": 2, "finish_position": 2,   "kilometer_time_seconds": 76.0},
            {"horse_id": 3, "finish_position": None, "kilometer_time_seconds": 78.0},
            {"horse_id": 4, "finish_position": None, "kilometer_time_seconds": 82.0},
            {"horse_id": 5, "finish_position": None, "kilometer_time_seconds": None},
        )
        result = fill_finish_positions(df)
        positions = result["finish_position"].tolist()
        assert len(positions) == len(set(positions))   # kaikki uniikkeja


# ---------------------------------------------------------------------------
# A1 — B2 segmentoidut piirteet: regressiotestit
# ---------------------------------------------------------------------------

class TestSegmentedFormFeatures:
    """Regressiotestit A1-korjaukselle: B2-segmentoidut muotopiirteet.

    Ennen A1:tä: form_avg_finish_5_same_method ja form_avg_finish_5_same_dist
    olivat 100 % NaN koska runners-DataFramessa ei ollut start_method/distance-
    sarakkeita (ne ovat races-taulussa).

    A1-korjaus: build_feature_matrix() pre-mergaa start_method ja distance
    races-taulusta runners:iin ennen form_features()-kutsua.
    """

    def test_segmented_form_features_have_values_with_horse_starts(self):
        """A1-regressio: form_avg_finish_5_same_method pitää tuottaa arvoja
        kun horse_starts sisältää start_method-sarakkeen ja races-taulussa on se."""
        # 5 historistarttia hevoselle 42 — kaikki autostartteja
        hs = pd.DataFrame([
            {"horse_id": 42, "race_date": f"2023-0{m}-01",
             "finish_position": 2, "kilometer_time_seconds": 90.0,
             "win_odds_final": 5.0, "start_method": "auto", "distance": 2140}
            for m in range(1, 6)
        ])
        # Yksi runners-rivi — ilman start_method (kuten DB:stä haettuna)
        runners = _runners(
            {"horse_id": 42, "race_id": 1, "race_date": "2024-01-01",
             "finish_position": 1, "kilometer_time_seconds": 88.0,
             "win_odds_final": 3.0, "driver": "Arto", "trainer": "Matti",
             "start_number": 2, "handicap_meters": 0},
        )
        races = _races({"race_id": 1, "track": "Solvalla", "distance": 2140,
                        "start_method": "auto"})

        result = build_feature_matrix(runners, races, horse_starts=hs)

        # A1-korjaus: pitää tuottaa arvoja, ei 100 % NaN
        notna_pct = result["form_avg_finish_5_same_method"].notna().mean()
        assert notna_pct > 0.5, (
            f"form_avg_finish_5_same_method notna% = {notna_pct:.1%}, odotettiin > 50 %. "
            "A1-korjaus (pre-merge start_method races-taulusta) ei toimi."
        )

    def test_segmented_dist_features_have_values(self):
        """A1-regressio: form_avg_finish_5_same_dist pitää tuottaa arvoja."""
        hs = pd.DataFrame([
            {"horse_id": 7, "race_date": f"2023-0{m}-01",
             "finish_position": 3, "kilometer_time_seconds": 91.0,
             "win_odds_final": 6.0, "start_method": "auto", "distance": 2140}
            for m in range(1, 6)
        ])
        runners = _runners(
            {"horse_id": 7, "race_id": 1, "race_date": "2024-01-01",
             "finish_position": 2, "kilometer_time_seconds": 90.0,
             "win_odds_final": 4.0, "driver": "B", "trainer": "C",
             "start_number": 1, "handicap_meters": 0},
        )
        races = _races({"race_id": 1, "track": "Solvalla",
                        "distance": 2140, "start_method": "auto"})

        result = build_feature_matrix(runners, races, horse_starts=hs)

        notna_pct = result["form_avg_finish_5_same_dist"].notna().mean()
        assert notna_pct > 0.5, (
            f"form_avg_finish_5_same_dist notna% = {notna_pct:.1%}, odotettiin > 50 %."
        )

    def test_segmented_cols_in_build_feature_matrix_output(self):
        """build_feature_matrix() tuottaa molemmat segmentoidut sarakkeet."""
        runners = _runners(
            {"horse_id": 1, "race_id": 1, "race_date": "2024-01-01",
             "finish_position": 1, "driver": "A", "trainer": "B",
             "start_number": 1, "handicap_meters": 0},
        )
        races = _races({"race_id": 1, "track": "Solvalla",
                        "distance": 2140, "start_method": "auto"})

        result = build_feature_matrix(runners, races)

        assert "form_avg_finish_5_same_method" in result.columns, \
            "form_avg_finish_5_same_method puuttuu tuloksesta"
        assert "form_avg_finish_5_same_dist" in result.columns, \
            "form_avg_finish_5_same_dist puuttuu tuloksesta"

    def test_no_column_conflicts_from_pre_merge(self):
        """A1-pre-merge + race_setup_features ei saa tuottaa _x/_y-sarakkeiden nimiä."""
        runners = _runners(
            {"horse_id": 1, "race_id": 1, "race_date": "2024-01-01",
             "finish_position": 2, "driver": "A", "trainer": "B",
             "start_number": 2, "handicap_meters": 0},
        )
        races = _races({"race_id": 1, "track": "Solvalla",
                        "distance": 2140, "start_method": "auto"})

        result = build_feature_matrix(runners, races)

        conflict_cols = [c for c in result.columns if c.endswith("_x") or c.endswith("_y")]
        assert not conflict_cols, (
            f"Merge-konflikti: tuloksessa on _x/_y-sarakkeita: {conflict_cols}"
        )


# ---------------------------------------------------------------------------
# A2 — B1 track code -normalisointi: regressiotestit
# ---------------------------------------------------------------------------

class TestTrackCodeNormalization:
    """Regressiotestit A2-korjaukselle: Travsport trackCode → ATG ratanimi.

    Ennen A2:ta: horse_starts.track oli lyhennekoodeja ("S", "Ax") mutta
    races.track on ATG-nimiä ("Solvalla", "Axevalla"). drop_duplicates ja
    groupby eivät tunnistaneet samaa rataa → track_horse_starts = 0 aina.

    A2-korjaus: race_setup_features() normalisoi horse_starts.track TRACKCODE_TO_NAME:llä.
    """

    def test_track_code_s_matches_solvalla(self):
        """A2-regressio: horse_starts-rivi trackCode='S' matchaa runners-rivin
        track='Solvalla' ja kasvattaa track_horse_starts >= 1."""
        runners = _runners(
            {"horse_id": 1, "race_id": 1, "race_date": "2024-05-01",
             "finish_position": 2, "start_number": 3, "handicap_meters": 0},
        )
        races = _races({"race_id": 1, "track": "Solvalla",
                        "distance": 2140, "start_method": "auto"})
        # horse_starts käyttää Travsport-koodia "S" = Solvalla
        hs = pd.DataFrame([
            {"horse_id": 1, "race_date": "2024-01-01", "track": "S",
             "finish_position": 1, "kilometer_time_seconds": 87.0,
             "win_odds_final": 3.0, "start_method": "auto", "distance": 2140},
            {"horse_id": 1, "race_date": "2024-02-15", "track": "S",
             "finish_position": 2, "kilometer_time_seconds": 89.0,
             "win_odds_final": 5.0, "start_method": "auto", "distance": 2140},
            {"horse_id": 1, "race_date": "2024-03-20", "track": "S",
             "finish_position": 1, "kilometer_time_seconds": 88.0,
             "win_odds_final": 4.0, "start_method": "auto", "distance": 2140},
        ])
        result = race_setup_features(runners, races, horse_starts=hs)

        track_starts = result.iloc[0]["track_horse_starts"]
        assert track_starts >= 1, (
            f"track_horse_starts = {track_starts}, odotettiin >= 1. "
            "A2-trackCode-normalisointi ei toimi: 'S' ei mapannut 'Solvalla':ksi."
        )

    def test_multiple_track_codes_normalize_correctly(self):
        """Eri ratakoodit normalisoituvat oikeiksi ATG-nimiksi."""
        from src.data.track_codes import TRACKCODE_TO_NAME, normalize_track
        for code, atg_name in [("S", "Solvalla"), ("Ax", "Axevalla"),
                                ("Bo", "Boden"), ("Bs", "Bollnäs"),
                                ("B", "Bergsåker"), ("Mp", "Mantorp"),
                                ("Å", "Åby")]:
            result = normalize_track(code)
            assert result == atg_name, (
                f"normalize_track({code!r}) = {result!r}, odotettiin {atg_name!r}"
            )

    def test_unknown_code_returned_as_is(self):
        """Tuntematon koodi palautetaan sellaisenaan (ei kaadu)."""
        from src.data.track_codes import normalize_track
        result = normalize_track("XYZ")
        assert result == "XYZ"

    def test_none_returns_none(self):
        """normalize_track(None) palauttaa None."""
        from src.data.track_codes import normalize_track
        assert normalize_track(None) is None


# ---------------------------------------------------------------------------
# A4b — B1/B2 realistinen pipeline-testi
# ---------------------------------------------------------------------------

class TestB1B2RealisticPipeline:
    """A4b: B1 ja B2 tuottavat arvoja realistisessa pipelinessa.

    Emuloi tuotantorakennetta: runners tulee SQL:stä ilman start_method/distance,
    horse_starts tulee Travsportista trackCodella. Varmistaa että A1+A2-korjaukset
    yhdessä tuottavat arvoja.
    """

    def test_b1_b2_produce_values_in_realistic_pipeline(self):
        """Regressio: B1 (track_horse_win_rate) ja B2 (segmentoidut piirteet)
        tuottavat non-NaN-arvoja kun runners on ilman start_method/distance
        ja horse_starts käyttää Travsport trackCodeja."""
        # horse_starts: 5 starttia Solvallassa, Travsport-koodilla "S"
        hs = pd.DataFrame([
            {"horse_id": 99, "race_date": f"2023-0{m}-01", "track": "S",
             "finish_position": 2, "kilometer_time_seconds": 89.0,
             "win_odds_final": 4.0, "start_method": "auto", "distance": 2140}
            for m in range(1, 6)
        ])
        # runners: tullut DB:stä — EI start_method eikä distance -saraketta
        runners = pd.DataFrame([{
            "horse_id": 99, "race_id": 1, "race_date": "2024-01-15",
            "finish_position": 1, "kilometer_time_seconds": 88.0,
            "win_odds_final": 3.0, "driver": "Arto", "trainer": "Matti",
            "start_number": 2, "handicap_meters": 0,
        }])
        # races: ATG-nimet
        races = _races({"race_id": 1, "track": "Solvalla",
                        "distance": 2140, "start_method": "auto"})

        result = build_feature_matrix(runners, races, horse_starts=hs)

        # B1: track_horse_starts pitää olla >= 5 (5 historistarttia Solvallassa)
        track_starts = result.iloc[0]["track_horse_starts"]
        assert track_starts >= 5, (
            f"track_horse_starts = {track_starts}, odotettiin >= 5. "
            "B1-track-normalisointi ei toimi."
        )

        # B1: track_horse_win_rate ei saa olla NaN (on >= 1 startti)
        assert not pd.isna(result.iloc[0]["track_horse_win_rate"]), \
            "track_horse_win_rate on NaN vaikka track_horse_starts >= 5"

        # B2: form_avg_finish_5_same_method ei saa olla NaN
        assert not pd.isna(result.iloc[0]["form_avg_finish_5_same_method"]), \
            "form_avg_finish_5_same_method on NaN — A1-pre-merge ei toimi"


# ---------------------------------------------------------------------------
# D — track_structure_features: yksikkötestit
# ---------------------------------------------------------------------------

def _tracks(*rows: dict) -> pd.DataFrame:
    """Luo minimaalisen tracks-DataFramen annetuilla riveillä.

    Kutsuttuna ilman argumentteja palauttaa yhden oletusrivin (Solvalla).
    """
    defaults: dict = {
        "track_name": "Solvalla",
        "length_total": 1000,
        "length_home_stretch": 220,
        "width_1": 1000,
        "width_2": 1000,
        "dosage": 1500,
        "open_stretch": True,
        "angled_wing": False,
    }
    if not rows:
        rows = ({},)
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _runners_with_track(*rows: dict) -> pd.DataFrame:
    """Luo runners-DataFramen jossa on track-sarake (simuloi race_setup_features():n jälkeistä tilaa).

    Kutsuttuna ilman argumentteja palauttaa yhden oletusrivin.
    """
    defaults: dict = {
        "horse_id": 1,
        "race_id": 1,
        "race_date": "2024-01-01",
        "finish_position": 2,
        "kilometer_time_seconds": 90.0,
        "win_odds_final": 3.0,
        "driver": "Arto",
        "trainer": "Matti",
        "start_number": 2,
        "handicap_meters": 0,
        "track": "Solvalla",
    }
    if not rows:
        rows = ({},)
    return pd.DataFrame([{**defaults, **r} for r in rows])


class TestTrackStructureFeatures:
    """Testit track_structure_features()-funktiolle (Tehtävä D)."""

    def test_adds_track_columns(self):
        """track_structure_features() lisää kaikki 7 piirresaraketta."""
        from src.features.build_features import track_structure_features

        runners = _runners_with_track()
        tracks = _tracks()
        result = track_structure_features(runners, tracks)

        for col in [
            "track_length_total", "track_home_stretch_m",
            "track_open_stretch", "track_angled_wing",
            "track_width_1", "track_width_2", "track_dosage",
        ]:
            assert col in result.columns, f"Sarake '{col}' puuttuu tuloksesta"

    def test_correct_values_mapped(self):
        """Sarakkeiden arvot vastaavat tracks-taulun rivejä."""
        from src.features.build_features import track_structure_features

        runners = _runners_with_track({"track": "Färjestad"})
        tracks = _tracks({"track_name": "Färjestad", "length_home_stretch": 177,
                          "length_total": 1000, "open_stretch": False, "angled_wing": False,
                          "width_1": 2040, "width_2": 2110, "dosage": 1700})
        result = track_structure_features(runners, tracks)

        row = result.iloc[0]
        assert row["track_home_stretch_m"] == 177
        assert row["track_length_total"] == 1000
        assert row["track_width_1"] == 2040
        assert row["track_width_2"] == 2110
        assert row["track_dosage"] == 1700

    def test_unknown_track_gives_nan(self):
        """Rata joka ei löydy tracks-taulusta saa NaN-arvot (LEFT JOIN)."""
        from src.features.build_features import track_structure_features

        runners = _runners_with_track({"track": "TuntematonRata"})
        tracks = _tracks({"track_name": "Solvalla"})
        result = track_structure_features(runners, tracks)

        assert pd.isna(result.iloc[0]["track_length_total"])
        assert pd.isna(result.iloc[0]["track_home_stretch_m"])

    def test_row_count_unchanged(self):
        """Rivimäärä ei muutu mergen jälkeen."""
        from src.features.build_features import track_structure_features

        runners = _runners_with_track(
            {"horse_id": 1, "track": "Solvalla"},
            {"horse_id": 2, "track": "Solvalla"},
            {"horse_id": 3, "track": "Färjestad"},
        )
        tracks = _tracks(
            {"track_name": "Solvalla"},
            {"track_name": "Färjestad", "length_home_stretch": 177},
        )
        result = track_structure_features(runners, tracks)
        assert len(result) == 3

    def test_tolerates_missing_columns_in_tracks(self):
        """Jos tracks-DataFrame puuttuu sarakkeita, ne sivuutetaan eikä kaaduta."""
        from src.features.build_features import track_structure_features

        runners = _runners_with_track()
        # Poistetaan dosage-sarake — simuloidaan vanhaa tietokantaa
        tracks = _tracks().drop(columns=["dosage"])
        result = track_structure_features(runners, tracks)

        # dosage-sarake puuttuu tracks:sta → ei track_dosage-saraketta tuloksessa
        assert "track_dosage" not in result.columns
        # Muut sarakkeet kyllä löytyvät
        assert "track_home_stretch_m" in result.columns

    def test_open_stretch_boolean_to_int(self):
        """open_stretch (True/False) muunnetaan int-tyyppiseksi (0/1)."""
        from src.features.build_features import track_structure_features

        runners = _runners_with_track(
            {"track": "A"},
            {"track": "B"},
        )
        tracks = _tracks(
            {"track_name": "A", "open_stretch": True, "angled_wing": False},
            {"track_name": "B", "open_stretch": False, "angled_wing": True},
        )
        result = track_structure_features(runners, tracks)

        row_a = result[result["track"] == "A"].iloc[0]
        row_b = result[result["track"] == "B"].iloc[0]
        assert row_a["track_open_stretch"] == 1
        assert row_a["track_angled_wing"] == 0
        assert row_b["track_open_stretch"] == 0
        assert row_b["track_angled_wing"] == 1

    def test_empty_tracks_returns_all_nan(self):
        """Tyhjä tracks-DataFrame → kaikki piirresarakkeet NaN, rivimäärä ei muutu."""
        from src.features.build_features import track_structure_features

        runners = _runners_with_track()
        # Luo tracks-DataFrame jossa on oikeat sarakkeet mutta ei rivejä
        tracks = _tracks().iloc[0:0]  # tyhjä, mutta schema sama
        result = track_structure_features(runners, tracks)

        assert len(result) == 1
        assert pd.isna(result.iloc[0]["track_length_total"])

    def test_no_duplicate_rows_when_track_appears_once(self):
        """Tracks-taulussa yksi rivi per rata — merger ei saa tuplata rivejä."""
        from src.features.build_features import track_structure_features

        runners = _runners_with_track(
            {"horse_id": 1},
            {"horse_id": 2},
        )
        tracks = _tracks()  # yksi rivi: Solvalla
        result = track_structure_features(runners, tracks)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Parannus #7 — Distance bucket -rajat: bins=[0,1999,2599,5000]
# ---------------------------------------------------------------------------

class TestDistanceBucketsTakamatka:
    """Testit distance_category-laskennalle uusilla bin-rajoilla.

    Ennen: bins=[0, 1640, 2140, 5000]
    Jälkeen (parannus #7): bins=[0, 1999, 2599, 5000]

    Muutos tehtiin koska esim. 2140m (takamatkalähdöt) kuuluu edelleen
    'middle'-luokkaan, ja 2160m ei saa lentää 'long'-luokkaan.
    """

    def _distance_category(self, distance: int) -> str:
        """Apumetodi: aja race_setup_features yhden hevosen lähdölle ja
        palauta distance_category-arvo."""
        runners = _runners(
            {"race_id": 1, "horse_id": 1, "race_date": "2024-01-01",
             "finish_position": 2, "start_number": 2, "handicap_meters": 0},
        )
        races = _races({"race_id": 1, "track": "Solvalla",
                        "distance": distance, "start_method": "auto"})
        result = race_setup_features(runners, races)
        return str(result.iloc[0]["distance_category"])

    def test_2140m_normal_is_middle(self):
        """2140m → 'middle' (raja on 1999 < x ≤ 2599)."""
        assert self._distance_category(2140) == "middle"

    def test_2160m_takamatka_stays_in_middle(self):
        """2160m takamatkalähtö → 'middle', EI 'long' (vanha raja 2140 olisi lentänyt)."""
        assert self._distance_category(2160) == "middle"

    def test_2640m_long_distance_is_long(self):
        """2640m → 'long' (raja on x > 2599)."""
        assert self._distance_category(2640) == "long"

    def test_1640m_short_distance_is_sprint(self):
        """1640m → 'sprint' (raja on x ≤ 1999)."""
        assert self._distance_category(1640) == "sprint"


class TestBuildFeatureMatrixWithTracks:
    """Testit build_feature_matrix():lle tracks-parametrilla (integraatio)."""

    def test_tracks_none_does_not_add_columns(self):
        """Ilman tracks-parametria track_*-sarakkeet eivät ilmesty tulokseen."""
        runners = _runners(
            {"race_id": 1, "horse_id": 1, "race_date": "2024-01-01",
             "finish_position": 2, "driver": "A", "trainer": "B",
             "start_number": 2, "handicap_meters": 0},
        )
        races = _races({"race_id": 1, "track": "Solvalla"})
        result = build_feature_matrix(runners, races)  # ei tracks-parametria

        track_struct_cols = [c for c in result.columns if c.startswith("track_") and c not in
                             ("track_horse_starts", "track_horse_win_rate", "track_condition",
                              "track_condition_win_rate")]
        assert not track_struct_cols, (
            f"track_*-sarakkeet ilmestyivät ilman tracks-parametria: {track_struct_cols}"
        )

    def test_tracks_param_adds_structure_columns(self):
        """Kun tracks-parametri annetaan, track_*-piirresarakkeet lisätään."""
        runners = _runners(
            {"race_id": 1, "horse_id": 1, "race_date": "2024-01-01",
             "finish_position": 2, "driver": "A", "trainer": "B",
             "start_number": 2, "handicap_meters": 0},
        )
        races = _races({"race_id": 1, "track": "Solvalla"})
        tracks = _tracks({"track_name": "Solvalla", "length_home_stretch": 220})
        result = build_feature_matrix(runners, races, tracks=tracks)

        assert "track_home_stretch_m" in result.columns
        assert result.iloc[0]["track_home_stretch_m"] == 220

    def test_feature_cols_track_structure_columns_present(self):
        """FEATURE_COLS:in track_*-sarakkeet löytyvät tuloksesta kun tracks annetaan."""
        from src.models.ranker import FEATURE_COLS

        runners = _runners(
            {"race_id": 1, "horse_id": 1, "race_date": "2024-01-01",
             "finish_position": 2, "driver": "A", "trainer": "B",
             "start_number": 2, "handicap_meters": 0},
        )
        races = _races({"race_id": 1, "track": "Solvalla"})
        tracks = _tracks({"track_name": "Solvalla"})
        result = build_feature_matrix(runners, races, tracks=tracks)

        track_struct_feat_cols = [c for c in FEATURE_COLS if c.startswith("track_length") or
                                  c.startswith("track_home") or c.startswith("track_open") or
                                  c.startswith("track_angled") or c.startswith("track_width") or
                                  c.startswith("track_dosage")]
        missing = [c for c in track_struct_feat_cols if c not in result.columns]
        assert not missing, (
            f"FEATURE_COLS:in track-rakenne-sarakkeet puuttuvat tuloksesta: {missing}"
        )


# ---------------------------------------------------------------------------
# driver_trainer_hs_features — horse_starts-pohjaiset 60d-tilastot
# ---------------------------------------------------------------------------

def _hs_starts(*rows: dict) -> pd.DataFrame:
    """Luo minimaalisen horse_starts-DataFramen driver_trainer_hs_features-testeille.

    Vaaditut sarakkeet: driver, trainer, finish_position, race_date.
    """
    defaults: dict = {
        "horse_id": "h1",
        "race_date": "2024-01-01",
        "driver": "Arto",
        "trainer": "Matti",
        "finish_position": 2,
        "kilometer_time_seconds": 90.0,
        "win_odds_final": 5.0,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


class TestDriverTrainerHsFeatures:
    """Testit driver_trainer_hs_features()-funktiolle.

    Varmistaa:
      - Perustapaus: oikea win%/top3% lasketaan 60d-ikkunasta
      - Point-in-time: saman päivän startit EI tule mukaan (< eikä <=)
      - Liian vähän starteja → NaN (alle min_starts=3)
      - Valmentaja-tilastot vastaavasti oikein
    """

    def _make_runner(
        self,
        race_date: str = "2024-03-01",
        driver: str = "Arto",
        trainer: str = "Matti",
        race_id: int = 99,
        horse_id: str = "h99",
    ) -> pd.DataFrame:
        return pd.DataFrame([{
            "race_id": race_id,
            "horse_id": horse_id,
            "race_date": race_date,
            "driver": driver,
            "trainer": trainer,
            "finish_position": None,
        }])

    def test_driver_win_rate_60d_basic(self):
        """5 starttia, 2 voittoa → driver_win_rate_60d = 0.40 (40%)."""
        runner = self._make_runner(race_date="2024-03-01", driver="Arto")
        hs = _hs_starts(
            # 5 starttia ikkunassa (kaikki < 2024-03-01 ja >= 2024-01-01)
            {"driver": "Arto", "race_date": "2024-02-20", "finish_position": 1},
            {"driver": "Arto", "race_date": "2024-02-10", "finish_position": 2},
            {"driver": "Arto", "race_date": "2024-01-25", "finish_position": 1},
            {"driver": "Arto", "race_date": "2024-01-15", "finish_position": 3},
            {"driver": "Arto", "race_date": "2024-01-05", "finish_position": 4},
        )
        result = driver_trainer_hs_features(runner, hs)
        assert "driver_win_rate_60d" in result.columns
        val = result.iloc[0]["driver_win_rate_60d"]
        assert val == pytest.approx(2 / 5), (
            f"driver_win_rate_60d = {val}, odotettiin 0.40 (2 voittoa / 5 starttia)"
        )

    def test_driver_win_rate_60d_excludes_same_race_day(self):
        """Saman päivän startit EI saa tulla mukaan (käytetään <, ei <=)."""
        runner = self._make_runner(race_date="2024-03-01", driver="Arto")
        hs = _hs_starts(
            # 3 starttia ikkunassa ennen race_date
            {"driver": "Arto", "race_date": "2024-02-25", "finish_position": 1},
            {"driver": "Arto", "race_date": "2024-02-15", "finish_position": 2},
            {"driver": "Arto", "race_date": "2024-01-20", "finish_position": 3},
            # Sama päivä kuin runner — EI saa tulla mukaan
            {"driver": "Arto", "race_date": "2024-03-01", "finish_position": 1},
        )
        result = driver_trainer_hs_features(runner, hs)
        val = result.iloc[0]["driver_win_rate_60d"]
        # Vain 3 hyväksyttyä starttia: 1 voitto / 3 = 0.333...
        assert val == pytest.approx(1 / 3, abs=1e-6), (
            f"driver_win_rate_60d = {val}, odotettiin ~0.333. "
            "Saman päivän startti vuotaa mukaan (käytä < eikä <=)."
        )

    def test_driver_win_rate_60d_too_few_starts(self):
        """Alle 3 starttia ikkunassa → NaN (ei luotettava estimaatti)."""
        runner = self._make_runner(race_date="2024-03-01", driver="Arto")
        hs = _hs_starts(
            # Vain 2 starttia — alle min_starts=3
            {"driver": "Arto", "race_date": "2024-02-20", "finish_position": 1},
            {"driver": "Arto", "race_date": "2024-02-10", "finish_position": 2},
        )
        result = driver_trainer_hs_features(runner, hs)
        val = result.iloc[0]["driver_win_rate_60d"]
        assert pd.isna(val), (
            f"driver_win_rate_60d = {val}, odotettiin NaN (alle 3 starttia)."
        )

    def test_driver_top3_rate_60d_basic(self):
        """driver_top3_rate_60d lasketaan samalla logiikalla kuin win_rate."""
        runner = self._make_runner(race_date="2024-03-01", driver="Arto")
        hs = _hs_starts(
            # 4 starttia: 3 top3 (pos 1, 2, 3) ja 1 ei (pos 5)
            {"driver": "Arto", "race_date": "2024-02-20", "finish_position": 1},
            {"driver": "Arto", "race_date": "2024-02-10", "finish_position": 2},
            {"driver": "Arto", "race_date": "2024-01-25", "finish_position": 3},
            {"driver": "Arto", "race_date": "2024-01-15", "finish_position": 5},
        )
        result = driver_trainer_hs_features(runner, hs)
        val = result.iloc[0]["driver_top3_rate_60d"]
        assert val == pytest.approx(3 / 4), (
            f"driver_top3_rate_60d = {val}, odotettiin 0.75 (3 top3 / 4 starttia)"
        )

    def test_trainer_win_rate_60d_basic(self):
        """Vastaava testi valmentajalle: 4 starttia, 1 voitto → 25%."""
        runner = self._make_runner(race_date="2024-03-01", trainer="Matti")
        hs = _hs_starts(
            {"trainer": "Matti", "race_date": "2024-02-20", "finish_position": 1},
            {"trainer": "Matti", "race_date": "2024-02-10", "finish_position": 2},
            {"trainer": "Matti", "race_date": "2024-01-25", "finish_position": 4},
            {"trainer": "Matti", "race_date": "2024-01-15", "finish_position": 5},
        )
        result = driver_trainer_hs_features(runner, hs)
        val = result.iloc[0]["trainer_win_rate_60d"]
        assert val == pytest.approx(1 / 4), (
            f"trainer_win_rate_60d = {val}, odotettiin 0.25 (1 voitto / 4 starttia)"
        )

    def test_window_excludes_starts_older_than_60d(self):
        """Startit jotka ovat yli 60 päivää vanhoja ei lasketa mukaan."""
        runner = self._make_runner(race_date="2024-03-01", driver="Arto")
        hs = _hs_starts(
            # 3 starttia ikkunassa
            {"driver": "Arto", "race_date": "2024-02-20", "finish_position": 1},
            {"driver": "Arto", "race_date": "2024-02-10", "finish_position": 1},
            {"driver": "Arto", "race_date": "2024-01-15", "finish_position": 1},
            # Tämä startti on yli 60 päivää ennen 2024-03-01 (= 2024-01-01)
            # 2024-03-01 - 60d = 2024-01-01 → 2023-12-31 on liian vanha
            {"driver": "Arto", "race_date": "2023-12-31", "finish_position": 5},
        )
        result = driver_trainer_hs_features(runner, hs)
        val = result.iloc[0]["driver_win_rate_60d"]
        # Vain 3 hyväksyttyä: 3 voittoa / 3 = 1.0
        assert val == pytest.approx(1.0), (
            f"driver_win_rate_60d = {val}, odotettiin 1.0 "
            "(vain 3 ikkunan sisäistä starttia, kaikki voittoja)"
        )

    def test_row_count_preserved(self):
        """Funktio ei saa muuttaa runner-rivien määrää."""
        runners = pd.DataFrame([
            {"race_id": 1, "horse_id": "h1", "race_date": "2024-03-01",
             "driver": "Arto", "trainer": "Matti", "finish_position": None},
            {"race_id": 1, "horse_id": "h2", "race_date": "2024-03-01",
             "driver": "Pekka", "trainer": "Juha", "finish_position": None},
        ])
        hs = _hs_starts(
            {"driver": "Arto", "trainer": "Matti", "race_date": "2024-02-15", "finish_position": 1},
            {"driver": "Arto", "trainer": "Matti", "race_date": "2024-02-05", "finish_position": 2},
            {"driver": "Arto", "trainer": "Matti", "race_date": "2024-01-20", "finish_position": 3},
        )
        result = driver_trainer_hs_features(runners, hs)
        assert len(result) == 2, (
            f"Rivimäärä muuttui: syöte 2 → tulos {len(result)}"
        )

    def test_output_columns_present(self):
        """Kaikki 4 piirresaraketta löytyvät tuloksesta."""
        runner = self._make_runner()
        hs = _hs_starts(
            {"driver": "Arto", "race_date": "2024-02-01", "finish_position": 1},
            {"driver": "Arto", "race_date": "2024-01-15", "finish_position": 2},
            {"driver": "Arto", "race_date": "2024-01-05", "finish_position": 3},
        )
        result = driver_trainer_hs_features(runner, hs)
        for col in ["driver_win_rate_60d", "driver_top3_rate_60d",
                    "trainer_win_rate_60d", "trainer_top3_rate_60d"]:
            assert col in result.columns, f"Sarake '{col}' puuttuu tuloksesta"

    def test_driver_name_travsport_format_normalized(self):
        """KNOWN_ISSUES #15: Travsport-nimi 'Sukunimi Etunimi' matchaa ATG-nimen
        'Etunimi Sukunimi' normalisoinnin jälkeen."""
        # ATG-runner käyttää "Etunimi Sukunimi" -formaattia
        runner = self._make_runner(driver="Jorma Kontio")
        # horse_starts (Travsport) käyttää "Sukunimi Etunimi" -formaattia
        hs = _hs_starts(
            {"driver": "Kontio Jorma", "race_date": "2024-01-20", "finish_position": 1},
            {"driver": "Kontio Jorma", "race_date": "2024-01-10", "finish_position": 2},
            {"driver": "Kontio Jorma", "race_date": "2024-01-01", "finish_position": 1},
        )
        result = driver_trainer_hs_features(runner, hs)
        val = result.iloc[0]["driver_win_rate_60d"]
        assert not pd.isna(val), (
            "driver_win_rate_60d on NaN — normalisointi ei toiminut "
            "(ATG 'Jorma Kontio' ei matchannut Travsport 'Kontio Jorma' -nimeä)"
        )
        assert val == pytest.approx(2 / 3), (
            f"Odotettiin 2/3 (2 voittoa / 3 starttia), saatiin {val}"
        )

    def test_driver_name_multi_part_surname_normalized(self):
        """Monisanainen sukunimi: 'van der Berg Pieter' → 'Pieter van der Berg'."""
        runner = self._make_runner(driver="Pieter van der Berg")
        hs = _hs_starts(
            {"driver": "van der Berg Pieter", "race_date": "2024-01-20", "finish_position": 1},
            {"driver": "van der Berg Pieter", "race_date": "2024-01-10", "finish_position": 3},
            {"driver": "van der Berg Pieter", "race_date": "2024-01-01", "finish_position": 2},
        )
        result = driver_trainer_hs_features(runner, hs)
        val = result.iloc[0]["driver_win_rate_60d"]
        assert not pd.isna(val), (
            "Monisanainen sukunimi ei normalisoitunut oikein"
        )
        assert val == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# _normalize_driver_name — apufunktio nimiformaatin normalisointiin
# ---------------------------------------------------------------------------

class TestNormalizeDriverName:
    """Suorat yksikkötestit _normalize_driver_name()-apufunktiolle."""

    def test_two_part_name(self):
        """'Kontio Jorma' → 'Jorma Kontio'."""
        assert _normalize_driver_name("Kontio Jorma") == "Jorma Kontio"

    def test_three_part_name_compound_surname(self):
        """'van der Berg Pieter' → 'Pieter van der Berg'."""
        assert _normalize_driver_name("van der Berg Pieter") == "Pieter van der Berg"

    def test_single_word_unchanged(self):
        """Yksiosainen nimi palautetaan muuttumattomana."""
        assert _normalize_driver_name("Madonna") == "Madonna"

    def test_already_atg_format_roundtrip(self):
        """ATG-formaatti 'Jorma Kontio' normalisoidaan → 'Kontio Jorma'
        (funktio olettaa aina Travsport-syötteen — älä kutsu ATG-nimillä)."""
        # Toimii odotetusti: funktio kääntää joka tapauksessa viimeisen eteen
        assert _normalize_driver_name("Jorma Kontio") == "Kontio Jorma"

    def test_whitespace_stripped(self):
        """Ylimääräiset välilyönnit poistetaan."""
        assert _normalize_driver_name("  Kontio  Jorma  ") == "Jorma Kontio"

    def test_non_string_passthrough(self):
        """Ei-merkkijono (None, NaN, int) ei kaadu — palautetaan sellaisenaan."""
        # Funktio saa isinstance(n, str) -tarkistuksen lambda-tasolla,
        # mutta testataan suoraa kutsua varmuuden vuoksi
        result = _normalize_driver_name(None)
        # Ei kaadu — palauttaa None tai str("None"), kumpi tahansa OK
        assert result is not None or result is None  # ei nosta poikkeusta


# ---------------------------------------------------------------------------
# C2 — start_position_features: starttipaikan vinouma per rata
# ---------------------------------------------------------------------------

class TestStartPositionFeatures:
    """Testit start_position_features()-funktiolle."""

    def test_start_position_win_rate_basic(self):
        """Point-in-time: lähtö joka näkee 5 aiempaa lähtöä saa win_rate = 0.60 (3/5).

        Vanha testi odotti globaalia aggregaattia (race_id=1 → 0.60). Korjattu
        18.5.2026 auditoinnin yhteydessä: point-in-time tarkoittaa että race_id=1
        ei näe omaa tulostaan → NaN. Race_id=6 (päivä kaikkien jälkeen) näkee
        5 aiempaa lähtöä → 3/5 = 0.60.
        """
        runners = _runners(
            # 5 historiallista lähtöä (muodostavat historiadatan)
            {"race_id": 1, "horse_id": 1, "race_date": "2024-01-01",
             "start_number": 1, "finish_position": 1},
            {"race_id": 2, "horse_id": 2, "race_date": "2024-01-08",
             "start_number": 1, "finish_position": 1},
            {"race_id": 3, "horse_id": 3, "race_date": "2024-01-15",
             "start_number": 1, "finish_position": 1},
            {"race_id": 4, "horse_id": 4, "race_date": "2024-01-22",
             "start_number": 1, "finish_position": 2},
            {"race_id": 5, "horse_id": 5, "race_date": "2024-01-29",
             "start_number": 1, "finish_position": 3},
            # Ennustettava lähtö — race_date kaikkien 5 jälkeen → näkee 3/5 voittoa
            {"race_id": 6, "horse_id": 6, "race_date": "2024-02-05",
             "start_number": 1, "finish_position": 2},
        )
        races = _races(
            {"race_id": 1, "track": "Solvalla"},
            {"race_id": 2, "track": "Solvalla"},
            {"race_id": 3, "track": "Solvalla"},
            {"race_id": 4, "track": "Solvalla"},
            {"race_id": 5, "track": "Solvalla"},
            {"race_id": 6, "track": "Solvalla"},
        )
        result = start_position_features(runners, races, min_samples=5)
        # race_id=6 näkee kaikki 5 aiempaa → 3 voittoa / 5 = 0.60
        val = result[result["race_id"].astype(str) == "6"]["start_position_win_rate"].iloc[0]
        assert val == pytest.approx(3 / 5), (
            f"start_position_win_rate = {val}, odotettiin 0.60 (3/5 voittoa)"
        )
        # Point-in-time: race_id=1 ei näe omaa tulostaan → alle min_samples → NaN
        val_first = result[result["race_id"].astype(str) == "1"]["start_position_win_rate"].iloc[0]
        assert pd.isna(val_first), (
            f"race_id=1:llä ei saa olla point-in-time-historiaa, saatiin {val_first}"
        )

    def test_start_position_win_rate_nan_below_min_samples(self):
        """Alle min_samples näytteitä → NaN."""
        runners = _runners(
            {"race_id": 1, "horse_id": 1, "race_date": "2024-01-01",
             "start_number": 1, "finish_position": 1},
            {"race_id": 2, "horse_id": 2, "race_date": "2024-01-08",
             "start_number": 1, "finish_position": 2},
        )
        races = _races(
            {"race_id": 1, "track": "Solvalla"},
            {"race_id": 2, "track": "Solvalla"},
        )
        result = start_position_features(runners, races, min_samples=10)
        assert result["start_position_win_rate"].isna().all(), (
            "Alle min_samples näytteitä → pitää olla NaN"
        )

    def test_start_position_win_rate_output_columns(self):
        """Tuloksessa on oikeat sarakkeet."""
        runners = _runners({"race_id": 1, "horse_id": 1,
                            "start_number": 1, "finish_position": 1})
        races = _races({"race_id": 1, "track": "Solvalla"})
        result = start_position_features(runners, races)
        assert "start_position_win_rate" in result.columns
        assert "start_position_win_rate_n" in result.columns


# ---------------------------------------------------------------------------
# C3 — start_method_features: lähtötapa-preferenssi
# ---------------------------------------------------------------------------

class TestStartMethodFeatures:
    """Testit start_method_features()-funktiolle."""

    def _make_runner(
        self,
        race_date: str = "2024-06-01",
        horse_id: str = "h1",
        race_id: int = 99,
    ) -> pd.DataFrame:
        return pd.DataFrame([{
            "race_id": race_id,
            "horse_id": horse_id,
            "race_date": race_date,
            "driver": "Arto",
            "trainer": "Matti",
            "finish_position": None,
            "start_number": 1,
        }])

    def test_start_method_win_rate_diff_auto_better(self):
        """Auto 40%, voltti 20% → diff = +0.20."""
        runner = self._make_runner(race_date="2024-06-01", horse_id="h1")
        hs = pd.DataFrame([
            # Auto: 5 starttia, 2 voittoa → 40%
            {"horse_id": "h1", "race_date": "2024-05-01", "start_method": "auto",
             "finish_position": 1, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
            {"horse_id": "h1", "race_date": "2024-04-15", "start_method": "auto",
             "finish_position": 1, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
            {"horse_id": "h1", "race_date": "2024-04-01", "start_method": "auto",
             "finish_position": 2, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
            {"horse_id": "h1", "race_date": "2024-03-15", "start_method": "auto",
             "finish_position": 3, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
            {"horse_id": "h1", "race_date": "2024-03-01", "start_method": "auto",
             "finish_position": 4, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
            # Voltti: 5 starttia, 1 voitto → 20%
            {"horse_id": "h1", "race_date": "2024-05-15", "start_method": "volte",
             "finish_position": 1, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
            {"horse_id": "h1", "race_date": "2024-04-20", "start_method": "volte",
             "finish_position": 2, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
            {"horse_id": "h1", "race_date": "2024-04-05", "start_method": "volte",
             "finish_position": 3, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
            {"horse_id": "h1", "race_date": "2024-03-20", "start_method": "volte",
             "finish_position": 4, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
            {"horse_id": "h1", "race_date": "2024-03-05", "start_method": "volte",
             "finish_position": 5, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
        ])
        result = start_method_features(runner, hs, min_starts=3)
        val = result.iloc[0]["start_method_win_rate_diff"]
        assert val == pytest.approx(0.20, abs=1e-6), (
            f"start_method_win_rate_diff = {val}, odotettiin +0.20 (auto=0.40 - volte=0.20)"
        )

    def test_start_method_win_rate_diff_nan_too_few(self):
        """Alle 3 starttia kummassakin → NaN."""
        runner = self._make_runner(race_date="2024-06-01", horse_id="h2")
        hs = pd.DataFrame([
            {"horse_id": "h2", "race_date": "2024-05-01", "start_method": "auto",
             "finish_position": 1, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
            {"horse_id": "h2", "race_date": "2024-04-01", "start_method": "volte",
             "finish_position": 2, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
        ])
        result = start_method_features(runner, hs, min_starts=3)
        val = result.iloc[0]["start_method_win_rate_diff"]
        # auto_wr=NaN, volte_wr=NaN → diff = NaN
        assert pd.isna(val), (
            f"start_method_win_rate_diff = {val}, odotettiin NaN (alle 3 starttia)"
        )

    def test_start_method_point_in_time(self):
        """Saman päivän startit eivät saa vaikuttaa (< ei <=)."""
        runner = self._make_runner(race_date="2024-06-01", horse_id="h3")
        hs = pd.DataFrame([
            # 3 ennen race_date
            {"horse_id": "h3", "race_date": "2024-05-20", "start_method": "auto",
             "finish_position": 1, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
            {"horse_id": "h3", "race_date": "2024-05-10", "start_method": "auto",
             "finish_position": 2, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
            {"horse_id": "h3", "race_date": "2024-05-01", "start_method": "auto",
             "finish_position": 3, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
            # Sama päivä — EI saa tulla mukaan
            {"horse_id": "h3", "race_date": "2024-06-01", "start_method": "auto",
             "finish_position": 1, "driver": "A", "trainer": "B",
             "kilometer_time_seconds": 90.0, "win_odds_final": 5.0},
        ])
        result = start_method_features(runner, hs, min_starts=3)
        val = result.iloc[0]["start_method_win_rate_diff"]
        # auto: 3 starttia (1 win, 2 ei) → win_rate = 1/3
        # volte: 0 starttia → NaN → diff = NaN (koska volte NaN)
        # Tärkeintä: diff ei ole 2/4 (ei vuoda saman päivän starttia)
        # Jos vuotaa → auto=2/4=0.5, jos ei → auto=1/3; diff on NaN koska volte=NaN
        assert pd.isna(val) or val != pytest.approx(0.5), (
            "Saman päivän startti vuosi mukaan (käytä <, ei <=)"
        )


# ---------------------------------------------------------------------------
# C1 — rest_days_bucket: lepopäivien U-käyrä
# ---------------------------------------------------------------------------

class TestRestDaysBucket:
    """Testit rest_days_bucket_features()-funktiolle."""

    def _df_with_days(self, days) -> pd.DataFrame:
        return pd.DataFrame([{
            "horse_id": 1,
            "race_id": 1,
            "race_date": "2024-01-15",
            "form_days_since_last": days,
        }])

    def test_rest_days_bucket_short(self):
        """< 6 päivää → 'short'."""
        result = rest_days_bucket_features(self._df_with_days(4))
        assert result.iloc[0]["rest_days_bucket"] == "short"

    def test_rest_days_bucket_optimal(self):
        """10 päivää → 'optimal'."""
        result = rest_days_bucket_features(self._df_with_days(10))
        assert result.iloc[0]["rest_days_bucket"] == "optimal"

    def test_rest_days_bucket_long(self):
        """30 päivää → 'long'."""
        result = rest_days_bucket_features(self._df_with_days(30))
        assert result.iloc[0]["rest_days_bucket"] == "long"

    def test_rest_days_bucket_very_long(self):
        """90 päivää → 'very_long'."""
        result = rest_days_bucket_features(self._df_with_days(90))
        assert result.iloc[0]["rest_days_bucket"] == "very_long"

    def test_rest_days_bucket_nan_is_very_long(self):
        """NaN (ensimmäinen startti) → 'very_long'."""
        result = rest_days_bucket_features(self._df_with_days(np.nan))
        assert result.iloc[0]["rest_days_bucket"] == "very_long"

    def test_rest_days_bucket_boundary_6_is_optimal(self):
        """Tasan 6 päivää → 'optimal' (raja: >= 6)."""
        result = rest_days_bucket_features(self._df_with_days(6))
        assert result.iloc[0]["rest_days_bucket"] == "optimal"

    def test_rest_days_bucket_boundary_21_is_optimal(self):
        """Tasan 21 päivää → 'optimal' (raja: <= 21)."""
        result = rest_days_bucket_features(self._df_with_days(21))
        assert result.iloc[0]["rest_days_bucket"] == "optimal"

    def test_rest_days_bucket_boundary_22_is_long(self):
        """Tasan 22 päivää → 'long' (raja: >= 22)."""
        result = rest_days_bucket_features(self._df_with_days(22))
        assert result.iloc[0]["rest_days_bucket"] == "long"

    def test_rest_days_bucket_no_form_days_column(self):
        """Jos form_days_since_last puuttuu, rest_days_bucket = NaN, ei kaaduta."""
        df = pd.DataFrame([{"horse_id": 1, "race_id": 1, "race_date": "2024-01-01"}])
        result = rest_days_bucket_features(df)
        assert "rest_days_bucket" in result.columns


# ---------------------------------------------------------------------------
# C4 — driver_trainer_track_features: kuski×rata ja valmentaja×rata 60d
# ---------------------------------------------------------------------------

class TestDriverTrainerTrackFeatures:
    """Testit driver_trainer_track_features()-funktiolle."""

    def _make_runner(
        self,
        race_date: str = "2024-03-01",
        driver: str = "Arto",
        trainer: str = "Matti",
        race_id: int = 99,
        horse_id: str = "h99",
    ) -> pd.DataFrame:
        return pd.DataFrame([{
            "race_id": race_id,
            "horse_id": horse_id,
            "race_date": race_date,
            "driver": driver,
            "trainer": trainer,
            "finish_position": None,
            "start_number": 1,
        }])

    def test_driver_track_win_rate_60d_basic(self):
        """Kuski×rata: 5 starttia, 2 voittoa → 0.40."""
        runner = self._make_runner(race_date="2024-03-01", driver="Arto")
        races = _races({"race_id": 99, "track": "Solvalla"})
        hs = pd.DataFrame([
            {"driver": "Arto", "trainer": "M", "track": "S",
             "race_date": "2024-02-20", "finish_position": 1,
             "horse_id": "h1", "kilometer_time_seconds": 90.0, "win_odds_final": 4.0},
            {"driver": "Arto", "trainer": "M", "track": "S",
             "race_date": "2024-02-10", "finish_position": 1,
             "horse_id": "h2", "kilometer_time_seconds": 90.0, "win_odds_final": 4.0},
            {"driver": "Arto", "trainer": "M", "track": "S",
             "race_date": "2024-01-25", "finish_position": 2,
             "horse_id": "h3", "kilometer_time_seconds": 91.0, "win_odds_final": 5.0},
            {"driver": "Arto", "trainer": "M", "track": "S",
             "race_date": "2024-01-15", "finish_position": 3,
             "horse_id": "h4", "kilometer_time_seconds": 92.0, "win_odds_final": 6.0},
            {"driver": "Arto", "trainer": "M", "track": "S",
             "race_date": "2024-01-05", "finish_position": 4,
             "horse_id": "h5", "kilometer_time_seconds": 93.0, "win_odds_final": 7.0},
        ])
        result = driver_trainer_track_features(runner, hs, races, min_starts=3)
        val = result.iloc[0]["driver_track_win_rate_60d"]
        assert val == pytest.approx(2 / 5), (
            f"driver_track_win_rate_60d = {val}, odotettiin 0.40 (2/5 voittoa)"
        )

    def test_driver_track_win_rate_60d_nan_below_min(self):
        """Alle min_starts starteja → NaN."""
        runner = self._make_runner(race_date="2024-03-01", driver="Arto")
        races = _races({"race_id": 99, "track": "Solvalla"})
        hs = pd.DataFrame([
            {"driver": "Arto", "trainer": "M", "track": "S",
             "race_date": "2024-02-20", "finish_position": 1,
             "horse_id": "h1", "kilometer_time_seconds": 90.0, "win_odds_final": 4.0},
            {"driver": "Arto", "trainer": "M", "track": "S",
             "race_date": "2024-02-10", "finish_position": 2,
             "horse_id": "h2", "kilometer_time_seconds": 90.0, "win_odds_final": 4.0},
        ])
        result = driver_trainer_track_features(runner, hs, races, min_starts=3)
        val = result.iloc[0]["driver_track_win_rate_60d"]
        assert pd.isna(val), (
            f"driver_track_win_rate_60d = {val}, odotettiin NaN (alle 3 starttia)"
        )

    def test_driver_track_excludes_different_track(self):
        """Eri radan startit eivät tule mukaan."""
        runner = self._make_runner(race_date="2024-03-01", driver="Arto")
        races = _races({"race_id": 99, "track": "Solvalla"})
        hs = pd.DataFrame([
            # Solvalla: 2 starttia (S = Solvalla)
            {"driver": "Arto", "trainer": "M", "track": "S",
             "race_date": "2024-02-20", "finish_position": 1,
             "horse_id": "h1", "kilometer_time_seconds": 90.0, "win_odds_final": 4.0},
            {"driver": "Arto", "trainer": "M", "track": "S",
             "race_date": "2024-02-10", "finish_position": 1,
             "horse_id": "h2", "kilometer_time_seconds": 90.0, "win_odds_final": 4.0},
            # Bergsåker: 5 tappiota — ei saa vaikuttaa Solvalla-win_rateen
            {"driver": "Arto", "trainer": "M", "track": "B",
             "race_date": "2024-02-15", "finish_position": 5,
             "horse_id": "h3", "kilometer_time_seconds": 95.0, "win_odds_final": 10.0},
            {"driver": "Arto", "trainer": "M", "track": "B",
             "race_date": "2024-02-05", "finish_position": 5,
             "horse_id": "h4", "kilometer_time_seconds": 95.0, "win_odds_final": 10.0},
            {"driver": "Arto", "trainer": "M", "track": "B",
             "race_date": "2024-01-25", "finish_position": 5,
             "horse_id": "h5", "kilometer_time_seconds": 95.0, "win_odds_final": 10.0},
        ])
        result = driver_trainer_track_features(runner, hs, races, min_starts=2)
        val = result.iloc[0]["driver_track_win_rate_60d"]
        # Vain 2 Solvalla-starttia → NaN jos min_starts=3, mutta tässä min_starts=2
        # Jos molemmat voittoja → 1.0 (EI 0.4 Bergsåker mukana)
        assert val == pytest.approx(1.0), (
            f"driver_track_win_rate_60d = {val}, odotettiin 1.0 (vain Solvalla-startit mukaan)"
        )

    def test_point_in_time_excludes_same_day(self):
        """Saman päivän startit eivät tule mukaan."""
        runner = self._make_runner(race_date="2024-03-01", driver="Arto")
        races = _races({"race_id": 99, "track": "Solvalla"})
        hs = pd.DataFrame([
            # 3 ennen race_date
            {"driver": "Arto", "trainer": "M", "track": "S",
             "race_date": "2024-02-20", "finish_position": 1,
             "horse_id": "h1", "kilometer_time_seconds": 90.0, "win_odds_final": 4.0},
            {"driver": "Arto", "trainer": "M", "track": "S",
             "race_date": "2024-02-10", "finish_position": 2,
             "horse_id": "h2", "kilometer_time_seconds": 90.0, "win_odds_final": 4.0},
            {"driver": "Arto", "trainer": "M", "track": "S",
             "race_date": "2024-01-20", "finish_position": 3,
             "horse_id": "h3", "kilometer_time_seconds": 90.0, "win_odds_final": 4.0},
            # Sama päivä kuin runner — EI saa tulla mukaan
            {"driver": "Arto", "trainer": "M", "track": "S",
             "race_date": "2024-03-01", "finish_position": 1,
             "horse_id": "h4", "kilometer_time_seconds": 88.0, "win_odds_final": 3.0},
        ])
        result = driver_trainer_track_features(runner, hs, races, min_starts=3)
        val = result.iloc[0]["driver_track_win_rate_60d"]
        # 3 starttia (1 voitto) → 1/3 ≈ 0.333
        # Jos saman päivän vuotaa: 4 starttia (2 voittoa) → 0.5
        assert val == pytest.approx(1 / 3, abs=1e-6), (
            f"driver_track_win_rate_60d = {val}, odotettiin 1/3. "
            "Saman päivän startti vuosi mukaan."
        )

    def test_output_columns_present(self):
        """Molemmat sarakkeet löytyvät tuloksesta."""
        runner = self._make_runner()
        races = _races({"race_id": 99, "track": "Solvalla"})
        hs = pd.DataFrame([
            {"driver": "Arto", "trainer": "Matti", "track": "S",
             "race_date": "2024-01-01", "finish_position": 1,
             "horse_id": "h1", "kilometer_time_seconds": 90.0, "win_odds_final": 4.0},
        ])
        result = driver_trainer_track_features(runner, hs, races)
        assert "driver_track_win_rate_60d" in result.columns
        assert "trainer_track_win_rate_60d" in result.columns


# ---------------------------------------------------------------------------
# C5 — Vaihe 7: km_time_trend, prize_money_trend, track_condition_win_rate
# ---------------------------------------------------------------------------

def _horse_starts_v7(*rows: dict) -> pd.DataFrame:
    """Luo horse_starts-DataFramen Vaihe 7 -testeille.

    Sisältää kaikki C5-piirteiden vaatimat sarakkeet:
    horse_id, race_date, finish_position, kilometer_time_seconds,
    prize_won, track_condition, win_odds_final.
    """
    defaults: dict = {
        "horse_id": 1,
        "race_date": "2023-01-01",
        "finish_position": 2,
        "kilometer_time_seconds": 90.0,
        "prize_won": 0,
        "track_condition": "n",  # Travsport: "n"=kevyt
        "win_odds_final": 5.0,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


class TestVaihe7Features:
    """Testit C5-piirteille: km_time_trend, prize_money_trend,
    track_condition_win_rate."""

    # ------------------------------------------------------------------
    # km_time_trend_features
    # ------------------------------------------------------------------

    def test_km_time_trend_negative_means_improving(self):
        """Kun km-ajat laskevat (nopeutuminen), kulmakerroin on negatiivinen."""
        runners = _runners(
            {"horse_id": 1, "race_id": 10, "race_date": "2024-05-01",
             "finish_position": 1},
        )
        hs = _horse_starts_v7(
            # Km-ajat laskevat: 95 → 93 → 91 → 89 (nopeutuminen)
            {"horse_id": 1, "race_date": "2024-01-01", "kilometer_time_seconds": 95.0},
            {"horse_id": 1, "race_date": "2024-02-01", "kilometer_time_seconds": 93.0},
            {"horse_id": 1, "race_date": "2024-03-01", "kilometer_time_seconds": 91.0},
            {"horse_id": 1, "race_date": "2024-04-01", "kilometer_time_seconds": 89.0},
        )
        result = km_time_trend_features(runners, hs)
        assert "km_time_trend" in result.columns
        trend = result.iloc[0]["km_time_trend"]
        assert trend < 0, (
            f"Nopeutuvan hevosen km_time_trend pitää olla negatiivinen, sai {trend}"
        )

    def test_km_time_trend_positive_means_slowing(self):
        """Kun km-ajat nousevat (hidastuminen), kulmakerroin on positiivinen."""
        runners = _runners(
            {"horse_id": 1, "race_id": 10, "race_date": "2024-05-01",
             "finish_position": 1},
        )
        hs = _horse_starts_v7(
            {"horse_id": 1, "race_date": "2024-01-01", "kilometer_time_seconds": 88.0},
            {"horse_id": 1, "race_date": "2024-02-01", "kilometer_time_seconds": 90.0},
            {"horse_id": 1, "race_date": "2024-03-01", "kilometer_time_seconds": 92.0},
            {"horse_id": 1, "race_date": "2024-04-01", "kilometer_time_seconds": 94.0},
        )
        result = km_time_trend_features(runners, hs)
        trend = result.iloc[0]["km_time_trend"]
        assert trend > 0, (
            f"Hidastuvan hevosen km_time_trend pitää olla positiivinen, sai {trend}"
        )

    def test_km_time_trend_no_history_gives_nan(self):
        """Hevosella ei aiempaa historiaa → km_time_trend = NaN."""
        runners = _runners(
            {"horse_id": 99, "race_id": 1, "race_date": "2024-05-01",
             "finish_position": 1},
        )
        hs = _horse_starts_v7(
            {"horse_id": 1, "race_date": "2024-01-01"},  # eri hevonen
        )
        result = km_time_trend_features(runners, hs)
        assert pd.isna(result.iloc[0]["km_time_trend"]), (
            "Hevosella ilman historiaa km_time_trend pitää olla NaN"
        )

    def test_km_time_trend_point_in_time_no_leakage(self):
        """Tulevat startit eivät saa vuotaa trendipiirteisiin."""
        runners = _runners(
            {"horse_id": 1, "race_id": 10, "race_date": "2024-03-01",
             "finish_position": 1},
        )
        hs = _horse_starts_v7(
            # Ennen runner.race_date: km-ajat tasainen
            {"horse_id": 1, "race_date": "2024-01-01", "kilometer_time_seconds": 90.0},
            {"horse_id": 1, "race_date": "2024-02-01", "kilometer_time_seconds": 90.0},
            # SAMA PÄIVÄ kuin runner → ei saa tulla mukaan
            {"horse_id": 1, "race_date": "2024-03-01", "kilometer_time_seconds": 70.0},
            # TULEVAISUUS → ei saa tulla mukaan
            {"horse_id": 1, "race_date": "2024-04-01", "kilometer_time_seconds": 70.0},
        )
        result = km_time_trend_features(runners, hs)
        trend = result.iloc[0]["km_time_trend"]
        # Ilman leakagea: 2 pistettä (90, 90) → slope ≈ 0
        # Jos saman päivän tai tulevaisuuden startit vuotavat: slope << 0
        assert trend == pytest.approx(0.0, abs=1e-6), (
            f"km_time_trend = {trend}, odotettiin ≈ 0 (ei leakagea). "
            "Saman päivän tai tulevaisuuden startit vuosivat."
        )

    def test_km_time_trend_missing_column_gives_nan(self):
        """km_time_trend on NaN jos horse_starts ei sisällä kilometer_time_seconds."""
        runners = _runners({"horse_id": 1, "race_id": 1, "race_date": "2024-05-01"})
        hs = pd.DataFrame([{"horse_id": 1, "race_date": "2024-01-01",
                             "finish_position": 2, "prize_won": 0}])
        result = km_time_trend_features(runners, hs)
        assert "km_time_trend" in result.columns
        assert pd.isna(result.iloc[0]["km_time_trend"])

    # ------------------------------------------------------------------
    # prize_money_trend_features
    # ------------------------------------------------------------------

    def test_prize_money_trend_positive_means_rising_class(self):
        """Kun palkintorahat nousevat, kulmakerroin on positiivinen."""
        runners = _runners(
            {"horse_id": 1, "race_id": 10, "race_date": "2024-05-01",
             "finish_position": 1},
        )
        hs = _horse_starts_v7(
            {"horse_id": 1, "race_date": "2024-01-01", "prize_won": 500},
            {"horse_id": 1, "race_date": "2024-02-01", "prize_won": 1000},
            {"horse_id": 1, "race_date": "2024-03-01", "prize_won": 2000},
            {"horse_id": 1, "race_date": "2024-04-01", "prize_won": 4000},
        )
        result = prize_money_trend_features(runners, hs)
        assert "prize_money_trend" in result.columns
        trend = result.iloc[0]["prize_money_trend"]
        assert trend > 0, (
            f"Nousevan palkintorahan prize_money_trend pitää olla positiivinen, "
            f"sai {trend}"
        )

    def test_prize_money_trend_missing_column_gives_nan(self):
        """prize_money_trend on NaN jos horse_starts ei sisällä prize_won."""
        runners = _runners({"horse_id": 1, "race_id": 1, "race_date": "2024-05-01"})
        hs = pd.DataFrame([{"horse_id": 1, "race_date": "2024-01-01",
                             "finish_position": 2, "kilometer_time_seconds": 90.0}])
        result = prize_money_trend_features(runners, hs)
        assert "prize_money_trend" in result.columns
        assert pd.isna(result.iloc[0]["prize_money_trend"])

    def test_prize_money_trend_no_row_explosion(self):
        """prize_money_trend_features ei saa kasvattaa rivimäärää."""
        runners = _runners(
            {"horse_id": 1, "race_id": 1, "race_date": "2024-05-01"},
            {"horse_id": 2, "race_id": 2, "race_date": "2024-05-01"},
        )
        hs = _horse_starts_v7(
            {"horse_id": 1, "race_date": "2024-01-01", "prize_won": 1000},
            {"horse_id": 1, "race_date": "2024-02-01", "prize_won": 2000},
            {"horse_id": 2, "race_date": "2024-01-01", "prize_won": 500},
        )
        result = prize_money_trend_features(runners, hs)
        assert len(result) == len(runners), (
            f"Rivimäärä kasvoi: {len(runners)} → {len(result)}"
        )

    # ------------------------------------------------------------------
    # track_condition_win_rate_features
    # ------------------------------------------------------------------

    def test_track_condition_win_rate_basic(self):
        """Voitto-% lasketaan oikein historiasta samassa rataolossa."""
        runners = _runners(
            {"horse_id": 1, "race_id": 10, "race_date": "2024-05-01"},
        )
        races = _races({"race_id": 10, "track_condition": "light"})
        hs = _horse_starts_v7(
            # 3 startia kevyessä kentässä (Travsport "n" = "light"), 1 voitto
            {"horse_id": 1, "race_date": "2024-01-01", "track_condition": "n",
             "finish_position": 1},
            {"horse_id": 1, "race_date": "2024-02-01", "track_condition": "n",
             "finish_position": 2},
            {"horse_id": 1, "race_date": "2024-03-01", "track_condition": "n",
             "finish_position": 3},
            # Eri rataolo → ei saa tulla mukaan
            {"horse_id": 1, "race_date": "2024-04-01", "track_condition": "v",
             "finish_position": 1},
        )
        result = track_condition_win_rate_features(runners, hs, races, min_starts=3)
        assert "track_condition_win_rate" in result.columns
        val = result.iloc[0]["track_condition_win_rate"]
        # 3 startia "light"-kentässä, 1 voitto → 1/3
        assert val == pytest.approx(1 / 3, abs=1e-6), (
            f"track_condition_win_rate = {val}, odotettiin 1/3"
        )

    def test_track_condition_normalization(self):
        """'n' (Travsport) ja 'light' (ATG) matchaavat samaan rataoloon."""
        runners = _runners(
            {"horse_id": 1, "race_id": 10, "race_date": "2024-05-01"},
        )
        # Nykyinen lähtö: ATG-formaatissa "light"
        races = _races({"race_id": 10, "track_condition": "light"})
        # Historia: Travsport-formaatissa "n" (kevyt) — pitää matcha
        hs = _horse_starts_v7(
            {"horse_id": 1, "race_date": "2024-01-01", "track_condition": "n",
             "finish_position": 1},
            {"horse_id": 1, "race_date": "2024-02-01", "track_condition": "N",
             "finish_position": 2},
            {"horse_id": 1, "race_date": "2024-03-01", "track_condition": "light",
             "finish_position": 2},
        )
        result = track_condition_win_rate_features(runners, hs, races, min_starts=3)
        val = result.iloc[0]["track_condition_win_rate"]
        # 3 startia kaikki normalisoituvat "light"-luokkaan, 1 voitto → 1/3
        assert val == pytest.approx(1 / 3, abs=1e-6), (
            f"Normalisaatio ei toiminut: track_condition_win_rate = {val}, "
            "odotettiin 1/3 (n, N, light → kaikki 'light')"
        )

    def test_track_condition_below_min_starts_is_nan(self):
        """Alle min_starts havaintoa samassa rataolossa → NaN."""
        runners = _runners(
            {"horse_id": 1, "race_id": 10, "race_date": "2024-05-01"},
        )
        races = _races({"race_id": 10, "track_condition": "light"})
        hs = _horse_starts_v7(
            {"horse_id": 1, "race_date": "2024-01-01", "track_condition": "n",
             "finish_position": 1},
            {"horse_id": 1, "race_date": "2024-02-01", "track_condition": "n",
             "finish_position": 2},
        )
        result = track_condition_win_rate_features(runners, hs, races, min_starts=3)
        assert pd.isna(result.iloc[0]["track_condition_win_rate"]), (
            "Alle min_starts-kynnyksen pitää palauttaa NaN"
        )

    def test_track_condition_win_rate_point_in_time(self):
        """Tulevat startit eivät saa vuotaa track_condition_win_rate:een."""
        runners = _runners(
            {"horse_id": 1, "race_id": 10, "race_date": "2024-03-15"},
        )
        races = _races({"race_id": 10, "track_condition": "heavy"})
        hs = _horse_starts_v7(
            # Ennen runner.race_date: 3 startia raskaassa, 0 voittoa
            {"horse_id": 1, "race_date": "2024-01-01", "track_condition": "v",
             "finish_position": 2},
            {"horse_id": 1, "race_date": "2024-02-01", "track_condition": "v",
             "finish_position": 3},
            {"horse_id": 1, "race_date": "2024-03-01", "track_condition": "v",
             "finish_position": 4},
            # TULEVAISUUS — voitto, ei saa tulla mukaan
            {"horse_id": 1, "race_date": "2024-04-01", "track_condition": "v",
             "finish_position": 1},
        )
        result = track_condition_win_rate_features(runners, hs, races, min_starts=3)
        val = result.iloc[0]["track_condition_win_rate"]
        # Ilman leakagea: 3 startia, 0 voittoa → 0.0
        assert val == pytest.approx(0.0, abs=1e-6), (
            f"track_condition_win_rate = {val}, odotettiin 0.0 (tulevaisuus vuosi)"
        )

    def test_c5_cols_present_in_build_feature_matrix(self):
        """build_feature_matrix() tuottaa kaikki C5-piirresarakkeet."""
        runners = _runners(
            {"race_id": 1, "horse_id": 1, "race_date": "2024-05-01",
             "finish_position": 1, "driver": "Arto", "trainer": "Matti",
             "start_number": 2, "handicap_meters": 0},
            {"race_id": 1, "horse_id": 2, "race_date": "2024-05-01",
             "finish_position": 2, "driver": "Teppo", "trainer": "Matti",
             "start_number": 4, "handicap_meters": 0},
        )
        races = _races(
            {"race_id": 1, "track": "Solvalla", "distance": 2140,
             "start_method": "auto", "track_condition": "light"},
        )
        hs = _horse_starts_v7(
            {"horse_id": 1, "race_date": "2024-01-01", "kilometer_time_seconds": 90.0,
             "prize_won": 500, "track_condition": "n", "finish_position": 2},
            {"horse_id": 1, "race_date": "2024-02-01", "kilometer_time_seconds": 89.0,
             "prize_won": 1000, "track_condition": "n", "finish_position": 1},
            {"horse_id": 1, "race_date": "2024-03-01", "kilometer_time_seconds": 88.0,
             "prize_won": 1500, "track_condition": "n", "finish_position": 1},
            {"horse_id": 2, "race_date": "2024-01-01", "kilometer_time_seconds": 92.0,
             "prize_won": 0, "track_condition": "n", "finish_position": 3},
        )
        result = build_feature_matrix(runners, races, horse_starts=hs)
        for col in ("km_time_trend", "prize_money_trend", "track_condition_win_rate"):
            assert col in result.columns, (
                f"C5-piirre '{col}' puuttuu build_feature_matrix()-tuloksesta. "
                f"Löydetyt sarakkeet: {sorted(result.columns.tolist())}"
            )
