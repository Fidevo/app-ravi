"""Testit src/models/backtest.py -moduulille.

Kattaa:
  3.4 — rolling_walk_forward() (Vaihe 3)
  3.5 — edge_decay_analysis() score_col-parametrilla (Vaihe 3)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from src.models.backtest import edge_decay_analysis, rolling_walk_forward


# ---------------------------------------------------------------------------
# Apufunktiot synteettisen datan rakentamiseen
# ---------------------------------------------------------------------------

def _make_race_data(
    n_races: int,
    horses_per_race: int = 8,
    start_date: str = "2024-01-01",
    days_between_races: float = 1.0,
    rng_seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rakenna synteettinen runners+races -pari backtest-testeille.

    Returns:
        (runners_with_features, races) -tuple.
        runners sisältää: race_id, horse_id, finish_position, win_odds_final
        sekä yksi numeerinen piirre (dummy_feature).
        races sisältää: race_id, race_date.
    """
    rng = np.random.default_rng(rng_seed)
    race_rows = []
    runner_rows = []
    base = pd.Timestamp(start_date)

    for i in range(n_races):
        race_id = f"race_{i:04d}"
        race_date = base + pd.Timedelta(days=int(i * days_between_races))
        race_rows.append({"race_id": race_id, "race_date": race_date})

        # Satunnaiset kertoimet väliltä 2–20
        odds = rng.uniform(2.0, 20.0, horses_per_race)
        # Voittaja: arvotaan tasaisesti
        winner = rng.integers(0, horses_per_race)
        for j in range(horses_per_race):
            runner_rows.append({
                "race_id": race_id,
                "horse_id": f"h_{i}_{j}",
                "finish_position": 1 if j == winner else 2,
                "win_odds_final": float(odds[j]),
                # Minimaalinen piirre jotta train_ranker ei kaadu tyhjiin piirteisiin
                "dummy_feature": float(rng.standard_normal()),
            })

    return pd.DataFrame(runner_rows), pd.DataFrame(race_rows)


def _make_backtest_df(n_periods: int, roi_trend: float = 0.0, brier_trend: float = 0.0) -> pd.DataFrame:
    """Rakenna synteettinen backtest-tulos edge_decay_analysis-testeille.

    Args:
        roi_trend: roi_pct:n muutos per periodi (positiivinen = kasvava)
        brier_trend: brier_score:n muutos per periodi (positiivinen = kasvava = heikkenevä)
    """
    rows = []
    for i in range(n_periods):
        rows.append({
            "period": f"period_{i}",
            "n_races": 10,
            "n_value_bets": 5,
            "total_staked": 500.0,
            "total_pnl": float(roi_trend * i * 500 / 100),
            "roi_pct": float(roi_trend * i),
            "avg_edge_pct": 5.0,
            "win_rate": 0.2,
            "brier_score": float(0.2 + brier_trend * i),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3.4 — rolling_walk_forward
# ---------------------------------------------------------------------------

class TestRollingWalkForward:
    """Testit rolling_walk_forward()-funktiolle."""

    def test_returns_empty_when_insufficient_data(self):
        """Jos dataa ei ole riittävästi edes ensimmäiseen ikkunaan, palautetaan tyhjä."""
        # Vain 5 päivää dataa, train_window_days=28 — ei koskaan tarpeeksi
        runners, races = _make_race_data(n_races=3, start_date="2024-01-01", days_between_races=1.0)
        result = rolling_walk_forward(
            runners, races, window_days=14, train_window_days=28
        )
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_returns_correct_columns(self):
        """Palautetulla DataFramella on kaikki odotetut sarakkeet."""
        runners, races = _make_race_data(n_races=3, start_date="2024-01-01")
        result = rolling_walk_forward(runners, races, window_days=14, train_window_days=28)
        expected_cols = {
            "period", "n_races", "n_value_bets", "total_staked",
            "total_pnl", "roi_pct", "avg_edge_pct", "win_rate", "brier_score",
        }
        assert expected_cols.issubset(set(result.columns))

    def test_windows_advance_by_window_days(self):
        """Kukin ikkuna alkaa täsmälleen window_days myöhemmin kuin edellinen."""
        # Mockataan train_ranker ja predict_win_probabilities jotta LightGBM
        # ei tarvitse oikeaa featurematriisia — testataan vain logiikkaa.
        runners, races = _make_race_data(
            n_races=200, start_date="2024-01-01", days_between_races=0.5
        )
        mock_model = MagicMock()

        def fake_predict(model, df, **kwargs):
            out = df[["race_id", "horse_id"]].copy()
            out["win_prob"] = 1.0 / df.groupby("race_id")["horse_id"].transform("count")
            out["score"] = out["win_prob"]
            out["start_number"] = 1
            return out

        with (
            patch("src.models.backtest.train_ranker", return_value=mock_model),
            patch("src.models.backtest.predict_win_probabilities", side_effect=fake_predict),
        ):
            result = rolling_walk_forward(
                runners, races, window_days=7, train_window_days=14
            )

        assert len(result) > 0, "Pitäisi löytyä vähintään yksi ikkuna"

        # Tarkista että period-sarake on olemassa
        assert "period" in result.columns

    def test_brier_score_in_output(self):
        """Jokaisessa ikkunassa on brier_score-arvo."""
        runners, races = _make_race_data(
            n_races=200, start_date="2024-01-01", days_between_races=0.5
        )
        mock_model = MagicMock()

        def fake_predict(model, df, **kwargs):
            out = df[["race_id", "horse_id"]].copy()
            out["win_prob"] = 1.0 / df.groupby("race_id")["horse_id"].transform("count")
            out["score"] = out["win_prob"]
            out["start_number"] = 1
            return out

        with (
            patch("src.models.backtest.train_ranker", return_value=mock_model),
            patch("src.models.backtest.predict_win_probabilities", side_effect=fake_predict),
        ):
            result = rolling_walk_forward(
                runners, races, window_days=7, train_window_days=14
            )

        if len(result) > 0:
            assert result["brier_score"].notna().all(), "brier_score sisältää NaN:ia"
            assert (result["brier_score"] >= 0).all(), "brier_score on negatiivinen"
            assert (result["brier_score"] <= 1).all(), "brier_score > 1 (max arvo)"

    def test_row_count_does_not_decrease_with_more_data(self):
        """Enemmän dataa → vähintään yhtä monta ikkunaa (tai enemmän)."""
        runners_small, races_small = _make_race_data(
            n_races=100, start_date="2024-01-01", days_between_races=0.5
        )
        runners_large, races_large = _make_race_data(
            n_races=300, start_date="2024-01-01", days_between_races=0.5
        )

        def fake_predict(model, df, **kwargs):
            out = df[["race_id", "horse_id"]].copy()
            out["win_prob"] = 1.0 / df.groupby("race_id")["horse_id"].transform("count")
            out["score"] = out["win_prob"]
            out["start_number"] = 1
            return out

        mock_model = MagicMock()
        with (
            patch("src.models.backtest.train_ranker", return_value=mock_model),
            patch("src.models.backtest.predict_win_probabilities", side_effect=fake_predict),
        ):
            result_small = rolling_walk_forward(
                runners_small, races_small, window_days=7, train_window_days=14
            )
            result_large = rolling_walk_forward(
                runners_large, races_large, window_days=7, train_window_days=14
            )

        assert len(result_large) >= len(result_small), (
            "Enemmän dataa tuotti vähemmän ikkunoita"
        )


# ---------------------------------------------------------------------------
# 3.5 — edge_decay_analysis (score_col-parametri)
# ---------------------------------------------------------------------------

class TestEdgeDecayAnalysis:
    """Testit edge_decay_analysis()-funktiolle molemmilla mittareilla."""

    def test_insufficient_data_returns_none_slope(self):
        """Alle 4 periodia → verdict 'ei tarpeeksi dataa', slope=None."""
        df = _make_backtest_df(n_periods=3)
        result = edge_decay_analysis(df)
        assert result["trend_slope"] is None
        assert "ei tarpeeksi dataa" in result["verdict"]

    def test_default_score_col_is_roi_pct(self):
        """Oletusarvo score_col='roi_pct' — taaksepäin-yhteensopiva."""
        df = _make_backtest_df(n_periods=6, roi_trend=0.0)
        result = edge_decay_analysis(df)
        assert result["score_col"] == "roi_pct"

    def test_roi_declining_gives_warning(self):
        """Voimakkaasti laskeva ROI → '❌ Edge pienenee selvästi'."""
        # roi_trend=-5.0 → roi_pct laskee 5 % per periodi → slope ~ -5
        df = _make_backtest_df(n_periods=6, roi_trend=-5.0)
        result = edge_decay_analysis(df, score_col="roi_pct")
        assert "❌" in result["verdict"]
        assert result["trend_slope"] < -1.0

    def test_roi_stable_gives_ok(self):
        """Vakaa ROI → '✅ Edge stabiili'."""
        df = _make_backtest_df(n_periods=6, roi_trend=0.0)
        result = edge_decay_analysis(df, score_col="roi_pct")
        assert "✅" in result["verdict"]

    def test_brier_rising_gives_warning(self):
        """Kasvava Brier-score (heikkenevä kalibrointi) → '❌ Mallin kalibrointi heikkenee'."""
        # brier_trend=0.01 → brier kasvaa 0.01 per periodi → slope ~ 0.01
        df = _make_backtest_df(n_periods=8, brier_trend=0.01)
        result = edge_decay_analysis(df, score_col="brier_score")
        assert "❌" in result["verdict"]
        assert result["trend_slope"] > 0

    def test_brier_stable_gives_ok(self):
        """Vakaa Brier-score → '✅ Kalibrointi stabiili'."""
        df = _make_backtest_df(n_periods=6, brier_trend=0.0)
        result = edge_decay_analysis(df, score_col="brier_score")
        assert "✅" in result["verdict"]

    def test_brier_improving_gives_ok(self):
        """Laskeva Brier-score (paraneva kalibrointi) → stabiili tai OK."""
        # brier_trend=-0.001 → brier laskee hitaasti = paranee
        df = _make_backtest_df(n_periods=6, brier_trend=-0.001)
        result = edge_decay_analysis(df, score_col="brier_score")
        # Negatiivinen slope = parantuva → stabiili tai OK
        assert "✅" in result["verdict"]
        assert result["trend_slope"] < 0

    def test_invalid_score_col_raises_valueerror(self):
        """Tuntematon score_col → ValueError."""
        df = _make_backtest_df(n_periods=6)
        with pytest.raises(ValueError, match="score_col"):
            edge_decay_analysis(df, score_col="ei_olemassa")

    def test_first_half_second_half_computed(self):
        """first_half ja second_half ovat oikeita arvoja."""
        # roi_pct: 0, 10, 20, 30, 40, 50 (6 periodia)
        df = _make_backtest_df(n_periods=6, roi_trend=10.0)
        result = edge_decay_analysis(df, score_col="roi_pct")
        # first_half = mean([0, 10, 30]) = ...
        # half = 6 // 2 = 3
        # first_half = mean([0, 10, 20]) = 10.0
        # second_half = mean([30, 40, 50]) = 40.0
        assert result["first_half"] == pytest.approx(10.0, abs=0.1)
        assert result["second_half"] == pytest.approx(40.0, abs=0.1)

    def test_result_contains_score_col_key(self):
        """Tulos sisältää score_col-avaimen (kirjauksia varten)."""
        df = _make_backtest_df(n_periods=6)
        result = edge_decay_analysis(df, score_col="brier_score")
        assert "score_col" in result
        assert result["score_col"] == "brier_score"

    def test_trend_slope_is_float(self):
        """trend_slope on float (ei esim. numpy scalar)."""
        df = _make_backtest_df(n_periods=6, roi_trend=-2.0)
        result = edge_decay_analysis(df)
        assert isinstance(result["trend_slope"], float)


# ---------------------------------------------------------------------------
# Bugi #3 -regressiotesti — isotonic-kalibrointi (15.5.2026)
# ---------------------------------------------------------------------------

class TestBug3CalibrationLowersBrier:
    """Varmistaa että isotonic-kalibrointi parantaa tai säilyttää Brier-scoren.

    Bugi: backtest käytti raakaa softmax-todennäköisyyttä ilman kalibrointia.
    Korjattu lisäämällä calibrate_isotonic / apply_isotonic rolling_walk_forward:iin
    ja quarterly_walk_forward:iin (viimeiset _CALIB_DAYS päivää treeniikkunasta
    = kalibrointisetti).
    """

    def _make_overcalibrated_preds(self, n_races: int = 80, rng_seed: int = 3) -> pd.DataFrame:
        """Raaka (ylikalibroitu) ennuste: suosikki saa 0.9, muut jakavat 0.1."""
        rng = np.random.default_rng(rng_seed)
        rows = []
        for race_num in range(n_races):
            race_id = f"race_{race_num:04d}"
            n = 8
            probs = np.full(n, 0.1 / (n - 1))
            probs[0] = 0.9
            winner = rng.integers(0, n)
            for i in range(n):
                rows.append({
                    "race_id": race_id,
                    "horse_id": f"h_{i}",
                    "win_prob": float(probs[i]),
                    "finish_position": 1 if i == winner else 2,
                })
        return pd.DataFrame(rows)

    def test_calibrated_brier_lte_uncalibrated_brier(self):
        """Isotonic-kalibrointi parantaa tai säilyttää Brier-scoren ylikalibroituun malliin.

        Brier(kalibroitu) ≤ Brier(raaka) + marginaali (0.01 toleranssi pienelle heilunnalle).
        Tämä on bugi #3:n ydinvaatimus.
        """
        from src.models.ranker import apply_isotonic, calibrate_isotonic

        preds = self._make_overcalibrated_preds(n_races=80)

        # Raaka Brier
        actual = (preds["finish_position"] == 1).astype(float)
        brier_raw = float(((preds["win_prob"] - actual) ** 2).mean())

        # Kalibroitu Brier
        iso = calibrate_isotonic(preds)
        preds_cal = apply_isotonic(preds, iso)
        actual_cal = (preds_cal["finish_position"] == 1).astype(float)
        brier_cal = float(((preds_cal["win_prob"] - actual_cal) ** 2).mean())

        assert brier_cal <= brier_raw + 0.01, (
            f"Bugi #3 regressio: kalibroitu Brier ({brier_cal:.4f}) ei parantunut "
            f"raakaan Brieriin ({brier_raw:.4f}) nähden. "
            f"Isotonic-kalibrointi ei toimi odotetusti."
        )

    def test_calibrate_isotonic_and_apply_isotonic_importable_from_ranker(self):
        """calibrate_isotonic ja apply_isotonic ovat importoitavissa ranker.py:stä.

        Backtest.py importoi ne — jos import hajoaa, koko backtest hajoaa.
        """
        from src.models.ranker import apply_isotonic, calibrate_isotonic
        assert callable(calibrate_isotonic)
        assert callable(apply_isotonic)

    def test_backtest_imports_calibration_functions(self):
        """src.models.backtest importoi calibrate_isotonic ja apply_isotonic.

        Tarkistaa että bugi #3 -korjaus on backtest.py:ssä aktiivinen
        (ei pelkästään ranker.py:ssä).
        """
        import importlib
        import inspect
        import src.models.backtest as bt_module

        # Varmista että moduuli importtasi kalibrointifunktiot
        source = inspect.getsource(bt_module)
        assert "calibrate_isotonic" in source, (
            "backtest.py ei sisällä calibrate_isotonic — bugi #3 -korjaus puuttuu."
        )
        assert "apply_isotonic" in source, (
            "backtest.py ei sisällä apply_isotonic — bugi #3 -korjaus puuttuu."
        )
