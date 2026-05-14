"""Testit sire_features()-funktiolle (B2 Vaihe B + Vaihe 3.7 LOO-korjaus).

Kattaa:
  - Oikea LOO (leave-one-out) win_rate-laskenta — hevosen omat startit
    poistetaan sire-aggregaatista (Vaihe 3.7 -korjaus)
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

    def test_sire_win_rate_computed_correctly_loo(self):
        """sire_lifetime_win_rate lasketaan leave-one-out -periaatteella.

        3 hevosta samalla isällä (Big Sire), yhteensä 90 starttia: 30+30+30.
        Voittoja: 10 (h10) + 5 (h11) + 0 (h12) = 15 yhteensä.

        Hevonen 10 on runner. LOO poistaa sen omat 30 starttia (10 voittoa):
          LOO-starts  = 90 - 30 = 60
          LOO-wins    = 15 - 10 = 5
          LOO-win_rate = 5 / 60 ≈ 0.0833
        """
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
        # LOO: hevosen 10 omat 30 starttia vähennetty → 60 jäljellä
        assert result.iloc[0]["sire_lifetime_starts"] == 60, (
            f"LOO-starts pitäisi olla 60 (90-30), sain {result.iloc[0]['sire_lifetime_starts']}"
        )
        expected_wr = 5 / 60  # 5 voittoa jäljellä (15-10) / 60 starttia
        assert abs(result.iloc[0]["sire_lifetime_win_rate"] - expected_wr) < 0.005, (
            f"sire_lifetime_win_rate = {result.iloc[0]['sire_lifetime_win_rate']:.4f}, "
            f"odotettiin LOO-arvoa {expected_wr:.4f}"
        )

    def test_small_sample_win_rate_is_nan(self):
        """Alle 30 LOO-starttia → sire_lifetime_win_rate on NaN.

        Hevonen 20 on ainoa sireen "Rare Sire" jälkeläinen. LOO poistaa sen
        omat 5 starttia → LOO-starts=0 → alle _SIRE_MIN_STARTS → NaN.
        """
        horses = _horses_df(
            {"horse_id": "20", "sire": "Rare Sire", "dam_sire": "X"},
        )
        hs = pd.DataFrame(_make_starts("20", 5, 2))  # vain 5 starttia
        runners = _runners_df({"horse_id": "20"})

        result = sire_features(runners, horses, hs)

        # LOO: ainoa jälkeläinen, kaikki omat startit poistetaan → 0
        assert result.iloc[0]["sire_lifetime_starts"] == 0, (
            "LOO ainoa jälkeläinen: oman kontribuution jälkeen 0 starttia jäljellä"
        )
        assert pd.isna(result.iloc[0]["sire_lifetime_win_rate"]), (
            "Pienellä LOO-otoksella (<30) win_rate pitää olla NaN"
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
        """dam_sire_lifetime_win_rate lasketaan emänisältä, ei isältä (LOO).

        Hevoset 40 ja 41 jakavat saman siren ja dam_siren.
        Runner on hevonen 40 (30 starttia, 5 voittoa).

        LOO sire/dam_sire-rate hevoselle 40:
          Kokonaistilasto: 60 starttia (40+41), 20 voittoa (5+15)
          Hevonen 40:n omat: 30 starttia, 5 voittoa
          LOO: 60-30=30 starttia, 20-5=15 voittoa → rate = 15/30 = 0.500
        """
        horses = _horses_df(
            {"horse_id": "40", "sire": "Sire A", "dam_sire": "Dam Sire B"},
            {"horse_id": "41", "sire": "Sire A", "dam_sire": "Dam Sire B"},
        )
        hs = pd.DataFrame(
            _make_starts("40", 30, 5) + _make_starts("41", 30, 15)
        )
        runners = _runners_df({"horse_id": "40"})

        result = sire_features(runners, horses, hs)

        sire_wr = result.iloc[0]["sire_lifetime_win_rate"]
        dam_sire_wr = result.iloc[0]["dam_sire_lifetime_win_rate"]

        # LOO sire: 15/30 = 0.500 (poistettu hevonen 40:n omat 30/5)
        assert abs(sire_wr - 15 / 30) < 0.01, (
            f"LOO sire_wr odotettiin 0.500, saatiin {sire_wr:.4f}"
        )
        # LOO dam_sire: sama → 15/30 = 0.500
        assert abs(dam_sire_wr - 15 / 30) < 0.01, (
            f"LOO dam_sire_wr odotettiin 0.500, saatiin {dam_sire_wr:.4f}"
        )

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

    def test_loo_excludes_own_starts(self):
        """LOO-invariantti: hevosen omia startteja ei lasketa sen sire-ratessa.

        Hevonen 60 on ainoa hyvä jälkeläinen (30 starttia, 20 voittoa → 66 %).
        Hevonen 61 on ainoa heikko jälkeläinen (30 starttia, 0 voittoa → 0 %).

        LOO hevoselle 60: poistetaan omat 30/20 → jäljellä vain 61:n data
          → 30 starttia, 0 voittoa → win_rate = 0.0
        LOO hevoselle 61: poistetaan omat 30/0 → jäljellä vain 60:n data
          → 30 starttia, 20 voittoa → win_rate ≈ 0.667

        Tämä EROAA vahvasti pooled-laskennasta (30/60 = 0.333 molemmille).
        """
        horses = _horses_df(
            {"horse_id": "60", "sire": "Shared", "dam_sire": "X"},
            {"horse_id": "61", "sire": "Shared", "dam_sire": "Y"},
        )
        hs = pd.DataFrame(
            _make_starts("60", 30, 20)   # hyvä hevonen: 20/30 voittoa
            + _make_starts("61", 30, 0)  # heikko hevonen: 0/30 voittoa
        )
        runners = _runners_df(
            {"horse_id": "60", "start_number": 1},
            {"horse_id": "61", "start_number": 2},
        )

        result = sire_features(runners, horses, hs)
        wr_60 = result.loc[result["horse_id"] == "60", "sire_lifetime_win_rate"].iloc[0]
        wr_61 = result.loc[result["horse_id"] == "61", "sire_lifetime_win_rate"].iloc[0]

        # LOO hevoselle 60: jäljellä vain h61 (0/30) → rate=0.0
        assert abs(wr_60 - 0.0) < 0.01, (
            f"LOO h60: odotettiin 0.000 (vain h61 jäljellä), saatiin {wr_60:.4f}"
        )
        # LOO hevoselle 61: jäljellä vain h60 (20/30) → rate≈0.667
        assert abs(wr_61 - 20 / 30) < 0.01, (
            f"LOO h61: odotettiin 0.667 (vain h60 jäljellä), saatiin {wr_61:.4f}"
        )
        # LOO-rate EI ole sama molemmille (tämä olisi virhe pooled-laskennassa)
        assert abs(wr_60 - wr_61) > 0.5, (
            "LOO-raten pitäisi erota merkittävästi hevosten välillä tässä tapauksessa"
        )

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
