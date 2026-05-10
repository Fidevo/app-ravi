"""Testit sire_features()-funktiolle (B2 Vaihe B).

Kattaa:
  - Oikea win_rate-laskenta isäoriin jälkeläisille
  - Pienen otoksen suodatus (< 30 starts → NaN)
  - Tuntematon sire → NaN
  - Rivisäilyvyys
  - Integraatio build_feature_matrix():iin
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.build_features import build_feature_matrix, sire_features


# ---------------------------------------------------------------------------
# Apufunktiot
# ---------------------------------------------------------------------------

def _runners_df(*rows: dict) -> pd.DataFrame:
    defaults: dict = {
        "horse_id": "1",
        "race_id": 1,
        "race_date": "2024-01-01",
        "finish_position": 2,
        "kilometer_time_seconds": 90.0,
        "win_odds_final": 4.0,
        "driver": "Arto",
        "trainer": "Matti",
        "start_number": 1,
        "handicap_meters": 0,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _races_df(*rows: dict) -> pd.DataFrame:
    defaults: dict = {
        "race_id": 1,
        "track": "Solvalla",
        "distance": 2140,
        "start_method": "auto",
        "track_condition": None,
        "race_min_earnings": None,
        "race_max_earnings": None,
        "race_age_group": None,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _horses_df(*rows: dict) -> pd.DataFrame:
    defaults: dict = {
        "horse_id": "1",
        "name": "Test Horse",
        "sire": "Famous Sire",
        "dam_sire": "Famous Dam Sire",
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _make_starts(horse_id: str, n: int, wins: int, base_date: str = "2023") -> list[dict]:
    """Luo n starttia horse_id:lle, joista wins on voittoja."""
    rows = []
    for i in range(n):
        rows.append({
            "horse_id": horse_id,
            "race_date": f"{base_date}-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
            "track": "S",
            "finish_position": 1 if i < wins else 2,
            "kilometer_time_seconds": 89.0,
            "win_odds_final": 4.0,
            "start_method": "A",
            "distance": 2140,
        })
    return rows


# ---------------------------------------------------------------------------
# Testit sire_features()
# ---------------------------------------------------------------------------

class TestSireFeatures:
    """Testit sire_features()-funktiolle."""

    def test_sire_win_rate_computed_correctly(self):
        """sire_lifetime_win_rate lasketaan oikein."""
        # 3 hevosta samalla isällä, yhteensä 90 starttia: 30+30+30
        # Voittoja: 10 + 5 + 0 = 15 → win_rate = 15/90 ≈ 0.167
        horses = _horses_df(
            {"horse_id": "10", "sire": "Big Sire", "dam_sire": "X"},
            {"horse_id": "11", "sire": "Big Sire", "dam_sire": "X"},
            {"horse_id": "12", "sire": "Big Sire", "dam_sire": "X"},
        )
        hs = pd.DataFrame(
            _make_starts("10", 30, 10)
            + _make_starts("11", 30, 5)
            + _make_starts("12", 30, 0)
        )
        runners = _runners_df(
            {"horse_id": "10", "race_date": "2024-05-01"},
        )

        result = sire_features(runners, horses, hs)

        assert "sire_lifetime_win_rate" in result.columns
        assert "sire_lifetime_starts" in result.columns
        assert result.iloc[0]["sire_lifetime_starts"] == 90
        expected_wr = 15 / 90
        assert abs(result.iloc[0]["sire_lifetime_win_rate"] - expected_wr) < 0.005, (
            f"sire_lifetime_win_rate = {result.iloc[0]['sire_lifetime_win_rate']:.4f}, "
            f"odotettiin {expected_wr:.4f}"
        )

    def test_small_sample_win_rate_is_nan(self):
        """Alle 30 starttia → sire_lifetime_win_rate on NaN."""
        horses = _horses_df(
            {"horse_id": "20", "sire": "Rare Sire", "dam_sire": "X"},
        )
        hs = pd.DataFrame(_make_starts("20", 5, 2))  # vain 5 starttia
        runners = _runners_df({"horse_id": "20"})

        result = sire_features(runners, horses, hs)

        assert result.iloc[0]["sire_lifetime_starts"] == 5
        assert pd.isna(result.iloc[0]["sire_lifetime_win_rate"]), (
            "Pienellä otoksella (<30) win_rate pitää olla NaN"
        )

    def test_unknown_sire_gives_nan(self):
        """Hevosen sire on None → sire_lifetime_win_rate on NaN."""
        horses = _horses_df(
            {"horse_id": "30", "sire": None, "dam_sire": None},
        )
        hs = pd.DataFrame(_make_starts("30", 5, 2))
        runners = _runners_df({"horse_id": "30"})

        result = sire_features(runners, horses, hs)
        assert pd.isna(result.iloc[0]["sire_lifetime_win_rate"])

    def test_dam_sire_computed_separately(self):
        """dam_sire_lifetime_win_rate lasketaan emänisältä, ei isältä."""
        horses = _horses_df(
            {"horse_id": "40", "sire": "Sire A", "dam_sire": "Dam Sire B"},
            {"horse_id": "41", "sire": "Sire A", "dam_sire": "Dam Sire B"},
        )
        # Sire A: 60 starttia, 5 voittoa
        # Dam Sire B: sama 60 starttia, 20 voittoa
        hs = pd.DataFrame(
            _make_starts("40", 30, 5) + _make_starts("41", 30, 15)
        )
        runners = _runners_df({"horse_id": "40"})

        result = sire_features(runners, horses, hs)

        sire_wr = result.iloc[0]["sire_lifetime_win_rate"]
        dam_sire_wr = result.iloc[0]["dam_sire_lifetime_win_rate"]

        # Sire: 20 voittoa / 60 startista = 0.333
        assert abs(sire_wr - 20 / 60) < 0.01
        # Dam sire: sama data (sama hevosjoukko) → sama luku
        assert abs(dam_sire_wr - 20 / 60) < 0.01

    def test_row_count_preserved(self):
        """sire_features ei lisää ylimääräisiä rivejä."""
        horses = _horses_df(
            {"horse_id": "50", "sire": "S", "dam_sire": "D"},
            {"horse_id": "51", "sire": "S", "dam_sire": "D"},
        )
        hs = pd.DataFrame(
            _make_starts("50", 30, 10) + _make_starts("51", 30, 5)
        )
        runners = _runners_df(
            {"horse_id": "50", "start_number": 1},
            {"horse_id": "51", "start_number": 2},
        )

        result = sire_features(runners, horses, hs)
        assert len(result) == len(runners)

    def test_same_sire_runners_get_same_rate(self):
        """Saman isäoriin eri jälkeläiset saavat saman sire_lifetime_win_rate."""
        horses = _horses_df(
            {"horse_id": "60", "sire": "Shared", "dam_sire": "X"},
            {"horse_id": "61", "sire": "Shared", "dam_sire": "Y"},
        )
        hs = pd.DataFrame(
            _make_starts("60", 30, 6) + _make_starts("61", 30, 6)
        )
        runners = _runners_df(
            {"horse_id": "60", "start_number": 1},
            {"horse_id": "61", "start_number": 2},
        )

        result = sire_features(runners, horses, hs)
        rates = result["sire_lifetime_win_rate"].values
        assert abs(rates[0] - rates[1]) < 1e-9, \
            f"Saman siren eri jälkeläisillä eri rate: {rates[0]:.4f} vs {rates[1]:.4f}"

    def test_no_horse_starts_data_gives_nan(self):
        """Tyhjä horse_starts DataFrame → kaikki NaN (ei kaadu)."""
        horses = _horses_df({"horse_id": "70", "sire": "Test", "dam_sire": "Test"})
        hs = pd.DataFrame(columns=["horse_id", "race_date", "finish_position",
                                   "track", "kilometer_time_seconds", "win_odds_final"])
        runners = _runners_df({"horse_id": "70"})

        result = sire_features(runners, horses, hs)
        assert "sire_lifetime_win_rate" in result.columns
        # Tyhjällä horse_starts:lla ei löydy aggregaatteja → NaN tai 0 starts
        assert pd.isna(result.iloc[0]["sire_lifetime_win_rate"]) or \
               result.iloc[0]["sire_lifetime_starts"] == 0


class TestSireFeaturesInPipeline:
    """Integraatiotestit sire_features() + build_feature_matrix()."""

    def test_sire_features_added_when_horses_given(self):
        """build_feature_matrix lisää sire-sarakkeet kun horses annetaan."""
        horses = _horses_df(
            {"horse_id": "80", "sire": "Pipeline Sire", "dam_sire": "Pipeline Dam"},
        )
        hs = pd.DataFrame(_make_starts("80", 35, 8))
        runners = _runners_df({"horse_id": "80"})
        races = _races_df({})

        result = build_feature_matrix(runners, races, horse_starts=hs, horses=horses)

        assert "sire_lifetime_win_rate" in result.columns
        assert "dam_sire_lifetime_win_rate" in result.columns
        assert "sire_lifetime_starts" in result.columns
        assert "dam_sire_lifetime_starts" in result.columns

    def test_sire_features_absent_without_horses_param(self):
        """Ilman horses-parametria sire-sarakkeet puuttuvat (ei kaadu)."""
        hs = pd.DataFrame(_make_starts("90", 10, 3))
        runners = _runners_df({"horse_id": "90"})
        races = _races_df({})

        result = build_feature_matrix(runners, races, horse_starts=hs)

        assert "sire_lifetime_win_rate" not in result.columns

    def test_sire_features_absent_without_horse_starts(self):
        """Ilman horse_starts-parametria sire-sarakkeet puuttuvat (ei kaadu)."""
        horses = _horses_df({"horse_id": "91", "sire": "Test", "dam_sire": "Test"})
        runners = _runners_df({"horse_id": "91"})
        races = _races_df({})

        result = build_feature_matrix(runners, races, horses=horses)

        assert "sire_lifetime_win_rate" not in result.columns

    def test_no_column_conflicts_with_sire_features(self):
        """Sire-piirteet eivät aiheuta _x/_y-suffiksikonflikteja."""
        horses = _horses_df(
            {"horse_id": "95", "sire": "Clean Sire", "dam_sire": "Clean Dam"},
        )
        hs = pd.DataFrame(_make_starts("95", 35, 8))
        runners = _runners_df({"horse_id": "95"})
        races = _races_df({})

        result = build_feature_matrix(runners, races, horse_starts=hs, horses=horses)

        conflicts = [c for c in result.columns if c.endswith("_x") or c.endswith("_y")]
        assert not conflicts, f"Suffiksikonfliktit: {conflicts}"
