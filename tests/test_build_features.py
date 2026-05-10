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
    build_feature_matrix,
    derived_features,
    driver_trainer_features,
    form_features,
    race_setup_features,
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
