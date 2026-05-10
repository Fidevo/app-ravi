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
    fill_finish_positions,
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
