"""Testit src/models/ranker.py -moduulille.

Kattaa:
  B1 — calibrate_isotonic() ja apply_isotonic() (Vaihe B)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.models.ranker import (
    apply_isotonic,
    calibrate_isotonic,
    calibrate_temperature,
)


# ---------------------------------------------------------------------------
# Apufunktiot
# ---------------------------------------------------------------------------

def _make_predictions(
    n_races: int = 20,
    horses_per_race: int = 8,
    rng_seed: int = 42,
) -> pd.DataFrame:
    """Luo synteettinen predictions-DataFrame kalibrointitesteille.

    Jokainen lähtö: yksi voittaja (finish_position=1), loput saavat 2–N.
    win_prob generoidaan softmaxilla satunnaisista pisteistä jotta
    kalibrointivirhe on realistinen.
    """
    rng = np.random.default_rng(rng_seed)
    rows = []
    for race_num in range(n_races):
        race_id = f"race_{race_num:03d}"
        scores = rng.standard_normal(horses_per_race)
        scores_stable = scores - scores.max()
        probs = np.exp(scores_stable) / np.exp(scores_stable).sum()
        winner = rng.choice(horses_per_race, p=probs)
        for i in range(horses_per_race):
            rows.append({
                "race_id": race_id,
                "horse_id": f"h_{race_num}_{i}",
                "finish_position": 1 if i == winner else 2,
                "win_prob": float(probs[i]),
                "score": float(scores[i]),
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# B1 — calibrate_isotonic
# ---------------------------------------------------------------------------

class TestCalibrateIsotonic:
    """Testit calibrate_isotonic()-funktiolle."""

    def test_returns_isotonic_regression_object(self):
        """calibrate_isotonic palauttaa IsotonicRegression-olion."""
        from sklearn.isotonic import IsotonicRegression
        preds = _make_predictions(n_races=30)
        iso = calibrate_isotonic(preds)
        assert isinstance(iso, IsotonicRegression)

    def test_fitted_model_has_expected_transform(self):
        """Sovitettu malli voidaan soveltaa uusiin todennäköisyyksiin."""
        preds = _make_predictions(n_races=50)
        iso = calibrate_isotonic(preds)
        # Sovellettaessa pitää saada arvoja väliltä [0, 1]
        test_probs = np.array([0.05, 0.1, 0.2, 0.5])
        result = iso.transform(test_probs)
        assert len(result) == len(test_probs)
        assert (result >= 0.0).all(), "Isotonic antoi negatiivisia todennäköisyyksiä"
        assert (result <= 1.0).all(), "Isotonic antoi > 1.0 todennäköisyyksiä"

    def test_monotonic_nondecreasing(self):
        """Isotonic regression on monotoninen: suurempi sisääntulo → suurempi ulostulo."""
        preds = _make_predictions(n_races=50)
        iso = calibrate_isotonic(preds)
        test_probs = np.linspace(0.01, 0.99, 50)
        result = iso.transform(test_probs)
        # Monotoninen ei-vähenevä
        assert (np.diff(result) >= -1e-10).all(), (
            "Isotonic ei ole monotoninen — transform tuottaa vähenevän jonon"
        )

    def test_works_with_minimal_data(self):
        """calibrate_isotonic ei kaadu kun dataa on vähän (mutta luo mallinn)."""
        preds = _make_predictions(n_races=5, horses_per_race=4)
        iso = calibrate_isotonic(preds)
        assert iso is not None

    def test_handles_nan_finish_positions(self):
        """NaN finish_position-arvot suodatetaan pois ennen sovitusta."""
        preds = _make_predictions(n_races=20)
        # Lisää NaN-rivejä
        preds.loc[preds.index[:10], "finish_position"] = np.nan
        iso = calibrate_isotonic(preds)  # ei saa kaatua
        assert iso is not None


# ---------------------------------------------------------------------------
# B1 — apply_isotonic
# ---------------------------------------------------------------------------

class TestApplyIsotonic:
    """Testit apply_isotonic()-funktiolle."""

    def test_probabilities_sum_to_one_per_race(self):
        """apply_isotonic tuottaa todennäköisyydet jotka summautuvat 1.0:aan per lähtö.

        Tämä on tärkein invariantti — isotonic voi rikkoa summautuvuuden
        ja re-normalisointi korjaa sen.
        """
        preds = _make_predictions(n_races=30)
        iso = calibrate_isotonic(preds)
        result = apply_isotonic(preds, iso)

        race_sums = result.groupby("race_id")["win_prob"].sum()
        for race_id, total in race_sums.items():
            assert abs(total - 1.0) < 1e-9, (
                f"Lähdön {race_id} todennäköisyydet summautuvat {total:.6f}, "
                f"odotettiin 1.0 (re-normalisointi ei toimi)"
            )

    def test_returns_copy_does_not_modify_original(self):
        """apply_isotonic palauttaa kopion — ei muokkaa alkuperäistä DataFramea."""
        preds = _make_predictions(n_races=10)
        original_probs = preds["win_prob"].copy()
        iso = calibrate_isotonic(preds)
        _ = apply_isotonic(preds, iso)
        pd.testing.assert_series_equal(preds["win_prob"], original_probs)

    def test_all_probabilities_non_negative(self):
        """Kaikki todennäköisyydet ovat ei-negatiivisia."""
        preds = _make_predictions(n_races=20)
        iso = calibrate_isotonic(preds)
        result = apply_isotonic(preds, iso)
        assert (result["win_prob"] >= 0).all(), "Negatiivisia todennäköisyyksiä"

    def test_output_has_same_rows_as_input(self):
        """Rivimäärä ei muutu."""
        preds = _make_predictions(n_races=15)
        iso = calibrate_isotonic(preds)
        result = apply_isotonic(preds, iso)
        assert len(result) == len(preds)

    def test_race_id_preserved(self):
        """race_id-sarake säilyy muuttumattomana."""
        preds = _make_predictions(n_races=10)
        iso = calibrate_isotonic(preds)
        result = apply_isotonic(preds, iso)
        pd.testing.assert_series_equal(
            preds["race_id"].reset_index(drop=True),
            result["race_id"].reset_index(drop=True),
        )

    def test_overcalibrated_model_gets_corrected(self):
        """Ylikalibroitu malli (liian itsevarmoja suosikkeja) saa matalammat suosikki-arvot.

        Testataan mallilla joka asettaa yhden hevosen 0.95:een ja muille 0.05/N.
        Isotonic pitäisi madaltaa suosikin arvoa (realistinen win-rate on noin 20-30 %).
        """
        rng = np.random.default_rng(99)
        rows = []
        for race_num in range(100):
            race_id = f"race_{race_num:03d}"
            n = 8
            # "Ylikalibroitu" malli: yksi hevonen saa 0.9, muut jakavat 0.1
            probs = np.full(n, 0.1 / (n - 1))
            probs[0] = 0.9
            # Todellisuudessa hevonen 0 voittaa n. 25 % ajasta (ei 90 %)
            winner = rng.choice(n, p=np.full(n, 1 / n))
            for i in range(n):
                rows.append({
                    "race_id": race_id,
                    "horse_id": f"h_{i}",
                    "finish_position": 1 if i == winner else 2,
                    "win_prob": float(probs[i]),
                    "score": float(probs[i]),
                })
        preds = pd.DataFrame(rows)
        iso = calibrate_isotonic(preds)
        result = apply_isotonic(preds, iso)

        # Ylikalibroitu suosikki: alkuperäinen win_prob=0.9
        # Korjatun pitäisi olla alempi (isotonic oppi todellisen win-raten)
        orig_fav_prob = preds[preds["horse_id"] == "h_0"]["win_prob"].iloc[0]
        new_fav_prob = result[result["horse_id"] == "h_0"]["win_prob"].iloc[0]
        # Emme vaadi tiettyä lukua, vain suunnan: isotonic madaltaa ylikalibroitua
        assert new_fav_prob < orig_fav_prob, (
            f"Isotonic ei madallanut ylikalibroitua suosikkia: "
            f"{orig_fav_prob:.3f} → {new_fav_prob:.3f}"
        )

    def test_well_calibrated_model_changes_little(self):
        """Hyvin kalibroitu malli muuttuu vähän (isotonic on lähes identiteetti)."""
        preds = _make_predictions(n_races=50)
        iso = calibrate_isotonic(preds)
        result = apply_isotonic(preds, iso)

        # Hyvin kalibroituna muutos on pieni (ei zero, mutta ei valtava)
        delta = (result["win_prob"] - preds["win_prob"]).abs().mean()
        assert delta < 0.1, (
            f"Kalibrointi muutti todennäköisyyksiä keskimäärin {delta:.3f} — "
            "liian suuri muutos hyvin kalibroituneelle mallille"
        )


# ---------------------------------------------------------------------------
# Vertailu temperature vs. isotonic — molemmat saatavilla
# ---------------------------------------------------------------------------

class TestTemperatureVsIsotonic:
    """Varmistaa että molemmat kalibrointimenetelmät ovat saatavilla ja toimivat."""

    def test_both_calibrations_available(self):
        """calibrate_temperature ja calibrate_isotonic ovat molemmat importoitavissa."""
        from src.models.ranker import calibrate_isotonic, calibrate_temperature
        assert callable(calibrate_temperature)
        assert callable(calibrate_isotonic)

    def test_temperature_returns_float(self):
        """calibrate_temperature palauttaa floatin."""
        preds = _make_predictions(n_races=20)
        T = calibrate_temperature(preds)
        assert isinstance(T, float)
        assert T > 0

    def test_isotonic_probabilities_sum_to_one(self):
        """apply_isotonic palauttaa summautuvat todennäköisyydet."""
        preds = _make_predictions(n_races=20)
        iso = calibrate_isotonic(preds)
        result = apply_isotonic(preds, iso)
        race_sums = result.groupby("race_id")["win_prob"].sum()
        assert (race_sums - 1.0).abs().max() < 1e-9
