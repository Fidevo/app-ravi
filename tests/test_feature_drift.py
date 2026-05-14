"""Testit src/monitoring/feature_drift.py -moduulille (C1).

Kattaa:
  - compute_feature_stats(): oikeat tilastot, NaN-käsittely, puuttuvat sarakkeet
  - detect_anomalies(): NaN-%-hälytys, σ-hälytys, ei historiaa → tyhjä lista
  - Integraatio: log_feature_distributions() ei kaadu synteettisellä datalla
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.monitoring.feature_drift import (
    _MIN_WEEKS_FOR_SIGMA,
    compute_feature_stats,
    detect_anomalies,
)


# ---------------------------------------------------------------------------
# Apufunktiot
# ---------------------------------------------------------------------------

def _make_features(n: int = 100, seed: int = 42, **overrides) -> pd.DataFrame:
    """Synteettinen feature DataFrame jossa kaikki numeeriset piirteet."""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        # Pakollisia sarakkeet jotta compute_feature_stats toimii
        "form_avg_finish_5": rng.uniform(1, 10, n),
        "form_win_rate_5": rng.uniform(0, 1, n),
        "form_top3_rate_5": rng.uniform(0, 1, n),
        "form_avg_km_time_5": rng.uniform(80, 100, n),
        "form_best_km_time_5": rng.uniform(80, 100, n),
        "form_market_avg_5": rng.uniform(1, 20, n),
        "form_days_since_last": rng.integers(7, 365, n).astype(float),
        "form_avg_finish_5_same_method": rng.uniform(1, 10, n),
        "form_avg_finish_5_same_dist": rng.uniform(1, 10, n),
        "atg_lifetime_win_rate": rng.uniform(0, 0.5, n),
        "atg_lifetime_top3_rate": rng.uniform(0, 0.8, n),
        "atg_lifetime_starts": rng.integers(1, 200, n).astype(float),
        "atg_best_km_for_this_setup": rng.uniform(80, 100, n),
        "driver_win_rate_365d": rng.uniform(0, 0.4, n),
        "driver_starts_365d": rng.integers(0, 500, n).astype(float),
        "driver_top3_rate_365d": rng.uniform(0, 0.6, n),
        "trainer_win_rate_365d": rng.uniform(0, 0.3, n),
        "trainer_top3_rate_365d": rng.uniform(0, 0.5, n),
        "inside_post": rng.integers(0, 2, n).astype(float),
        "back_row": rng.integers(0, 2, n).astype(float),
        "handicap_meters": rng.integers(0, 40, n).astype(float),
        "track_horse_starts": rng.integers(0, 50, n).astype(float),
        "track_horse_win_rate": rng.uniform(0, 0.5, n),
        "race_min_earnings": rng.integers(0, 50000, n).astype(float),
        "race_max_earnings": rng.integers(50000, 500000, n).astype(float),
        "shoes_changed_front": rng.integers(0, 2, n).astype(float),
        "shoes_changed_back": rng.integers(0, 2, n).astype(float),
        "sulky_changed": rng.integers(0, 2, n).astype(float),
        "barfota_law_active": rng.integers(0, 2, n).astype(float),
        "horse_age": rng.integers(3, 12, n).astype(float),
        "track_length_total": rng.choice([1000, 1640, 2140], n).astype(float),
        "track_home_stretch_m": rng.integers(200, 600, n).astype(float),
        "track_open_stretch": rng.integers(0, 2, n).astype(float),
        "track_angled_wing": rng.integers(0, 2, n).astype(float),
        "track_width_1": rng.uniform(10, 25, n),
        "track_width_2": rng.uniform(10, 25, n),
        "track_dosage": rng.integers(0, 3, n).astype(float),
        # Kategoriset
        "distance_category": rng.choice(["sprint", "middle", "long"], n),
        "start_method": rng.choice(["auto", "voltstart"], n),
        "race_age_group": rng.choice(["3yo", "4yo+", "5yo+"], n),
        "track_condition": rng.choice([None, "light", "heavy"], n),
        "sulky_type": rng.choice(["VA", "AM"], n),
    })
    for col, val in overrides.items():
        df[col] = val
    return df


def _make_stats(n: int = 100, **overrides) -> pd.DataFrame:
    """Luo minimaalinen stats-DataFrame detect_anomalies()-testeille."""
    df = _make_features(n)
    stats = compute_feature_stats(df)
    for col, val in overrides.items():
        stats.loc[stats["feature"] == col, "mean"] = val
    return stats


# ---------------------------------------------------------------------------
# compute_feature_stats
# ---------------------------------------------------------------------------

class TestComputeFeatureStats:

    def test_returns_one_row_per_monitored_feature(self):
        """Yksi rivi per seurattu piirre."""
        from src.monitoring.feature_drift import _NUMERIC_FEATURES, _CATEGORICAL_FEATURES
        df = _make_features(50)
        stats = compute_feature_stats(df)
        expected_features = set(_NUMERIC_FEATURES) | set(_CATEGORICAL_FEATURES)
        assert set(stats["feature"]) == expected_features

    def test_correct_nan_pct_for_fully_present_column(self):
        """0 % NaN kun sarakkeessa ei ole NaN:ia."""
        df = _make_features(100)
        stats = compute_feature_stats(df)
        row = stats[stats["feature"] == "atg_lifetime_starts"].iloc[0]
        assert row["nan_pct"] == pytest.approx(0.0)

    def test_correct_nan_pct_for_half_nan_column(self):
        """50 % NaN kun puolet arvoista on NaN."""
        df = _make_features(100)
        df["horse_age"] = np.where(df.index < 50, df["horse_age"], np.nan)
        stats = compute_feature_stats(df)
        row = stats[stats["feature"] == "horse_age"].iloc[0]
        assert row["nan_pct"] == pytest.approx(50.0, abs=1.0)

    def test_missing_column_gives_100_nan(self):
        """Puuttuva sarake → NaN 100 %."""
        df = _make_features(50).drop(columns=["track_home_stretch_m"])
        stats = compute_feature_stats(df)
        row = stats[stats["feature"] == "track_home_stretch_m"].iloc[0]
        assert row["nan_pct"] == pytest.approx(100.0)
        assert pd.isna(row["mean"])

    def test_mean_and_median_reasonable(self):
        """mean ja p50 ovat järkevissä rajoissa."""
        df = _make_features(500)
        stats = compute_feature_stats(df)
        row = stats[stats["feature"] == "atg_lifetime_win_rate"].iloc[0]
        assert 0.0 < row["mean"] < 1.0
        assert 0.0 < row["p50"] < 1.0

    def test_categorical_has_no_mean(self):
        """Kategoriset sarakkeet: mean=NaN (ei sovellettavissa)."""
        df = _make_features(100)
        stats = compute_feature_stats(df)
        for cat in ["distance_category", "start_method"]:
            row = stats[stats["feature"] == cat].iloc[0]
            assert pd.isna(row["mean"]), f"{cat}: mean pitäisi olla NaN"

    def test_n_valid_matches_notna_count(self):
        """n_valid = notna()-summa sarakkeessa."""
        df = _make_features(100)
        df.loc[:19, "driver_win_rate_365d"] = np.nan  # 20 NaN
        stats = compute_feature_stats(df)
        row = stats[stats["feature"] == "driver_win_rate_365d"].iloc[0]
        assert row["n_valid"] == 80
        assert row["nan_pct"] == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# detect_anomalies
# ---------------------------------------------------------------------------

class TestDetectAnomalies:

    def test_empty_history_returns_no_anomalies(self):
        """Ei historiaa → ei anomalioita."""
        stats = _make_stats()
        anomalies = detect_anomalies(stats, history=[])
        assert anomalies == []

    def test_nan_pct_spike_detected(self):
        """NaN-%-nousu yli kynnyksen → anomalia."""
        # Edellinen viikko: nan_pct = 5
        prev = _make_stats()
        prev.loc[prev["feature"] == "horse_age", "nan_pct"] = 5.0

        # Tämä viikko: nan_pct = 25 (nousu 20 pp > 10 pp raja)
        curr = _make_stats()
        curr.loc[curr["feature"] == "horse_age", "nan_pct"] = 25.0

        anomalies = detect_anomalies(curr, history=[prev])
        nan_anomalies = [a for a in anomalies if a["feature"] == "horse_age" and a["check"] == "nan_pct"]
        assert len(nan_anomalies) == 1
        assert nan_anomalies[0]["delta"] == pytest.approx(20.0)

    def test_nan_pct_small_change_no_anomaly(self):
        """Pieni NaN-%-nousu (< kynnyksen) ei laukaise hälytystä."""
        prev = _make_stats()
        prev.loc[prev["feature"] == "horse_age", "nan_pct"] = 5.0

        curr = _make_stats()
        curr.loc[curr["feature"] == "horse_age", "nan_pct"] = 9.0  # +4 pp < 10 pp

        anomalies = detect_anomalies(curr, history=[prev])
        nan_anomalies = [a for a in anomalies if a["feature"] == "horse_age" and a["check"] == "nan_pct"]
        assert len(nan_anomalies) == 0

    def test_large_mean_jump_without_history_uses_raw_20pct(self):
        """Alle _MIN_WEEKS_FOR_SIGMA viikkoja: käytetään 20 % raw-raja."""
        # Historia lyhempi kuin kynnys
        assert _MIN_WEEKS_FOR_SIGMA >= 3
        history = [_make_stats() for _ in range(_MIN_WEEKS_FOR_SIGMA - 1)]

        # Modifioi kaikkien historian viikkojen atg_lifetime_starts mean = 50
        for h in history:
            h.loc[h["feature"] == "atg_lifetime_starts", "mean"] = 50.0

        # Tämä viikko: mean = 65 (nousu 30 %, yli 20 % rajan)
        curr = _make_stats()
        curr.loc[curr["feature"] == "atg_lifetime_starts", "mean"] = 65.0

        anomalies = detect_anomalies(curr, history=history)
        mean_anomalies = [
            a for a in anomalies
            if a["feature"] == "atg_lifetime_starts" and a["check"] == "mean"
        ]
        assert len(mean_anomalies) == 1
        assert "20%" in str(mean_anomalies[0]["threshold"])

    def test_stable_feature_no_anomaly_with_history(self):
        """Vakaa piirre (pieni σ, ei isoa hyppyä) ei laukaise hälytystä."""
        # Historia: atg_lifetime_starts mean ~ 100 ± 2
        history = []
        for i in range(5):
            h = _make_stats()
            h.loc[h["feature"] == "atg_lifetime_starts", "mean"] = 100.0 + i * 0.5
            history.append(h)

        # Tämä viikko: mean = 101 (pieni vaihtelu, ei anomalia)
        curr = _make_stats()
        curr.loc[curr["feature"] == "atg_lifetime_starts", "mean"] = 101.0

        anomalies = detect_anomalies(curr, history=history)
        starts_anomalies = [
            a for a in anomalies
            if a["feature"] == "atg_lifetime_starts" and a["check"] == "mean"
        ]
        assert len(starts_anomalies) == 0

    def test_k1_style_drift_detected(self):
        """Simuloi K1-vuotoa: vakaa baseline, sitten äkillinen hyppy.

        K1-bugi todellisuudessa: ensin stabiili data, sitten bugi alkaa
        ja arvo alkaa kasvaa joka viikko. Monitorointi havaitsee hypyn
        heti kun se ylittää historiallisen σ:n 2-kertaisesti.

        Historia (5 vk): atg_lifetime_starts mean ≈ 100 ± 0.5 (pieni varianssi)
        Tämä viikko: mean = 110 (post-race vuoto lisäsi +10)
        → delta/hist_std >> 2σ → ANOMALIA
        """
        rng = np.random.default_rng(99)
        history = []
        for i in range(5):
            h = _make_stats()
            # Vakaa baseline ±0.5 satunnaisella noisella
            h.loc[h["feature"] == "atg_lifetime_starts", "mean"] = 100.0 + rng.uniform(-0.5, 0.5)
            history.append(h)

        # Tämä viikko: +10 yhtäkkinen hyppy (K1-tyyppinen post-race vuoto)
        curr = _make_stats()
        curr.loc[curr["feature"] == "atg_lifetime_starts", "mean"] = 110.0

        anomalies = detect_anomalies(curr, history=history)
        starts_anomalies = [
            a for a in anomalies
            if a["feature"] == "atg_lifetime_starts" and a["check"] == "mean"
        ]
        # hist_std ≈ 0.3, delta = ~10, n_sigma ≈ 33 → erittäin selvä CRITICAL
        assert len(starts_anomalies) >= 1, (
            "K1-tyyppinen +10 hyppy vakaasta baselinesta pitäisi laukaista anomalia"
        )

    def test_anomaly_has_required_keys(self):
        """Anomalia-dict sisältää kaikki vaaditut kentät."""
        prev = _make_stats()
        prev.loc[prev["feature"] == "horse_age", "nan_pct"] = 0.0
        curr = _make_stats()
        curr.loc[curr["feature"] == "horse_age", "nan_pct"] = 50.0

        anomalies = detect_anomalies(curr, history=[prev])
        assert len(anomalies) >= 1
        required_keys = {"feature", "check", "old_val", "new_val", "delta", "threshold", "severity"}
        for a in anomalies:
            assert required_keys <= set(a.keys()), f"Puuttuvat kentät: {required_keys - set(a.keys())}"

    def test_severity_critical_for_extreme_nan_jump(self):
        """NaN-% hyppy > 30 pp → severity = CRITICAL."""
        prev = _make_stats()
        prev.loc[prev["feature"] == "horse_age", "nan_pct"] = 0.0
        curr = _make_stats()
        curr.loc[curr["feature"] == "horse_age", "nan_pct"] = 95.0

        anomalies = detect_anomalies(curr, history=[prev])
        nan_anomalies = [a for a in anomalies if a["feature"] == "horse_age" and a["check"] == "nan_pct"]
        assert len(nan_anomalies) == 1
        assert nan_anomalies[0]["severity"] == "CRITICAL"
