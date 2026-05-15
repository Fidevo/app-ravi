"""Testit src/features/travronden_features.py -moduulille (D2).

Kattaa:
  - parse_travronden_race(): oikea piirteiden purku, int-flag-muunnos,
    speed_record-skaala, game_percent-purku, tyhjä vastaus
  - merge_travronden_features(): LEFT JOIN, tyhjä df, dedup
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.travronden_features import (
    TRAVRONDEN_FEATURE_COLS,
    merge_travronden_features,
    parse_travronden_race,
    _parse_game_percent,
    _parse_speed_record,
    _to_int_flag,
)


# ---------------------------------------------------------------------------
# Apufunktiot
# ---------------------------------------------------------------------------

def _make_start(
    atg_id: int = 785880,
    interval_group: int | None = 11,
    is_castration: bool = False,
    is_new_driver: bool = False,
    is_new_trainer: bool = False,
    is_first_shoes: bool = False,
    is_first_carriage: bool = False,
    speed_k: int | None = 7530,
    speed_m: int | None = 7370,
    speed_l: int | None = None,
    expected_odds: int | None = 568,
    game_key: str = "V64",
    game_pct: int | None = 2498,
) -> dict:
    """Rakennetaan minimaalinen Travronden starts[]-elementti."""
    speed_records = {}
    if speed_k is not None:
        speed_records["K"] = {"record_type": "K", "speed": speed_k}
    if speed_m is not None:
        speed_records["M"] = {"record_type": "M", "speed": speed_m}
    if speed_l is not None:
        speed_records["L"] = {"record_type": "L", "speed": speed_l}

    game_percent: dict = {}
    if game_pct is not None:
        game_percent = {"providers": {"ATG": {game_key: {"percent": game_pct}}}}

    return {
        "horse": {"atg_id": atg_id, "name": "Test Horse", "speed_records": speed_records},
        "start_interval_group": interval_group,
        "is_first_after_castration": is_castration,
        "is_first_new_driver": is_new_driver,
        "is_first_new_trainer": is_new_trainer,
        "is_first_shoes": is_first_shoes,
        "is_first_carriage": is_first_carriage,
        "expected_odds": expected_odds,
        "game_percent": game_percent,
        # Post-race kentät — EI pitäisi päätyä piirteisiin
        "speed": 7490,     # post-race km-aika
        "comment": "Ledn, slagen 200 kv",  # post-race kommentti
        "placement": 1,
    }


def _make_race_response(starts: list[dict]) -> dict:
    return {"starts": starts}


# ---------------------------------------------------------------------------
# parse_travronden_race
# ---------------------------------------------------------------------------

class TestParseTravrondenRace:

    def test_returns_one_row_per_horse(self):
        """Yksi rivi per hevonen."""
        starts = [_make_start(atg_id=100), _make_start(atg_id=200)]
        df = parse_travronden_race(_make_race_response(starts))
        assert len(df) == 2

    def test_horse_id_is_string(self):
        """horse_id on merkkijono (str(atg_id))."""
        df = parse_travronden_race(_make_race_response([_make_start(atg_id=785880)]))
        assert df.iloc[0]["horse_id"] == "785880"
        assert isinstance(df.iloc[0]["horse_id"], str)

    def test_start_interval_group_preserved(self):
        """start_interval_group kopioidaan sellaisenaan."""
        df = parse_travronden_race(_make_race_response([_make_start(interval_group=21)]))
        assert df.iloc[0]["tr_start_interval_group"] == 21

    def test_start_interval_group_none_when_missing(self):
        """None interval_group → tr_start_interval_group on None/NaN."""
        df = parse_travronden_race(_make_race_response([_make_start(interval_group=None)]))
        assert pd.isna(df.iloc[0]["tr_start_interval_group"])

    def test_bool_flags_converted_to_int(self):
        """Boolean is_first_*-kentät → 0/1."""
        df = parse_travronden_race(_make_race_response([
            _make_start(is_castration=True, is_new_driver=False)
        ]))
        assert df.iloc[0]["tr_is_first_after_castration"] == 1
        assert df.iloc[0]["tr_is_first_new_driver"] == 0

    def test_speed_record_scaled_correctly(self):
        """speed_records.speed (int×100) → sekunteja (float÷100).
        Esim. 7530 → 75.30 s."""
        df = parse_travronden_race(_make_race_response([_make_start(speed_k=7530, speed_m=7370)]))
        assert df.iloc[0]["tr_speed_record_k"] == pytest.approx(75.30, abs=0.001)
        assert df.iloc[0]["tr_speed_record_m"] == pytest.approx(73.70, abs=0.001)

    def test_missing_speed_record_is_nan(self):
        """Puuttuva speed_records-koodi → NaN."""
        df = parse_travronden_race(_make_race_response([_make_start(speed_l=None)]))
        assert pd.isna(df.iloc[0]["tr_speed_record_l"])

    def test_expected_odds_scaled_correctly(self):
        """expected_odds int×100 → kerroin (568 → 5.68)."""
        df = parse_travronden_race(_make_race_response([_make_start(expected_odds=568)]))
        assert df.iloc[0]["tr_expected_odds"] == pytest.approx(5.68, abs=0.001)

    def test_expected_odds_none_when_missing(self):
        """expected_odds=None → NaN."""
        df = parse_travronden_race(_make_race_response([_make_start(expected_odds=None)]))
        assert pd.isna(df.iloc[0]["tr_expected_odds"])

    def test_game_percent_scaled_correctly(self):
        """game_percent.providers.ATG.V64.percent 2498 → 24.98."""
        df = parse_travronden_race(_make_race_response([
            _make_start(game_key="V64", game_pct=2498)
        ]))
        assert df.iloc[0]["tr_game_percent_v"] == pytest.approx(24.98, abs=0.01)

    def test_post_race_speed_not_in_columns(self):
        """speed (post-race km-aika) EI päädy piirreisiin."""
        df = parse_travronden_race(_make_race_response([_make_start()]))
        assert "speed" not in df.columns
        assert "comment" not in df.columns

    def test_all_feature_cols_present(self):
        """Kaikki TRAVRONDEN_FEATURE_COLS sarakkeet löytyvät tuloksesta."""
        df = parse_travronden_race(_make_race_response([_make_start()]))
        for col in TRAVRONDEN_FEATURE_COLS:
            assert col in df.columns, f"Puuttuva sarake: {col}"

    def test_empty_starts_returns_empty_df(self):
        """Tyhjä starts-lista → tyhjä DataFrame, ei kaadu."""
        df = parse_travronden_race({"starts": []})
        assert len(df) == 0
        assert "horse_id" in df.columns

    def test_missing_starts_key_returns_empty_df(self):
        """Puuttuva starts-avain → tyhjä DataFrame."""
        df = parse_travronden_race({})
        assert len(df) == 0

    def test_zero_atg_id_skipped(self):
        """atg_id=0 (tuntematon) ohitetaan."""
        starts = [_make_start(atg_id=0), _make_start(atg_id=123)]
        df = parse_travronden_race(_make_race_response(starts))
        assert len(df) == 1
        assert df.iloc[0]["horse_id"] == "123"

    def test_multiple_horses_different_groups(self):
        """Usealla hevosella voi olla eri start_interval_group."""
        starts = [
            _make_start(atg_id=1, interval_group=1),
            _make_start(atg_id=2, interval_group=11),
            _make_start(atg_id=3, interval_group=31),
        ]
        df = parse_travronden_race(_make_race_response(starts))
        assert list(df["tr_start_interval_group"]) == [1, 11, 31]


# ---------------------------------------------------------------------------
# Apufunktioiden yksikkötestit
# ---------------------------------------------------------------------------

class TestHelperFunctions:

    def test_to_int_flag_true(self):
        assert _to_int_flag(True) == 1

    def test_to_int_flag_false(self):
        assert _to_int_flag(False) == 0

    def test_to_int_flag_none(self):
        assert _to_int_flag(None) is None

    def test_parse_speed_record_valid(self):
        assert _parse_speed_record({"speed": 7490}) == pytest.approx(74.90)

    def test_parse_speed_record_none_speed(self):
        assert _parse_speed_record({"speed": None}) is None

    def test_parse_speed_record_not_dict(self):
        assert _parse_speed_record(None) is None
        assert _parse_speed_record("invalid") is None

    def test_parse_game_percent_v64(self):
        gp = {"providers": {"ATG": {"V64": {"percent": 2498}}}}
        assert _parse_game_percent(gp) == pytest.approx(24.98)

    def test_parse_game_percent_priority_v75_over_v64(self):
        """V75 otetaan ennen V64 (suurempi peli → parempi signaali)."""
        gp = {"providers": {"ATG": {
            "V64": {"percent": 1000},
            "V75": {"percent": 2000},
        }}}
        assert _parse_game_percent(gp) == pytest.approx(20.00)

    def test_parse_game_percent_no_data(self):
        assert _parse_game_percent(None) is None
        assert _parse_game_percent({}) is None


# ---------------------------------------------------------------------------
# merge_travronden_features
# ---------------------------------------------------------------------------

class TestMergeTravrondenFeatures:

    def _runners(self, horse_ids: list[str]) -> pd.DataFrame:
        return pd.DataFrame({
            "horse_id": horse_ids,
            "race_id": ["r1"] * len(horse_ids),
        })

    def test_merge_adds_tr_columns(self):
        """Onnistunut yhdistys lisää tr_*-sarakkeet."""
        runners = self._runners(["100", "200"])
        tr = pd.DataFrame([
            {"horse_id": "100", "tr_start_interval_group": 11,
             **{c: None for c in TRAVRONDEN_FEATURE_COLS if c != "tr_start_interval_group"}},
        ])
        result = merge_travronden_features(runners, tr)
        assert "tr_start_interval_group" in result.columns
        assert result.loc[result["horse_id"] == "100", "tr_start_interval_group"].iloc[0] == 11

    def test_unmatched_horse_gets_nan(self):
        """Hevonen jota ei ole Travronden-datassa saa NaN."""
        runners = self._runners(["100", "999"])
        tr = pd.DataFrame([{"horse_id": "100", **{c: 1.0 for c in TRAVRONDEN_FEATURE_COLS}}])
        result = merge_travronden_features(runners, tr)
        assert pd.isna(result.loc[result["horse_id"] == "999", "tr_start_interval_group"].iloc[0])

    def test_empty_travronden_df_adds_nan_columns(self):
        """Tyhjä Travronden-df → tr_*-sarakkeet NaN, ei kaadu."""
        runners = self._runners(["100", "200"])
        result = merge_travronden_features(runners, pd.DataFrame())
        for col in TRAVRONDEN_FEATURE_COLS:
            assert col in result.columns
            assert result[col].isna().all()

    def test_row_count_preserved(self):
        """Rivimäärä ei muutu mergessä."""
        runners = self._runners(["1", "2", "3"])
        tr = pd.DataFrame([{"horse_id": "1", **{c: None for c in TRAVRONDEN_FEATURE_COLS}}])
        result = merge_travronden_features(runners, tr)
        assert len(result) == 3

    def test_dedup_keeps_last(self):
        """Duplikaatti horse_id Travronden-df:ssä: viimeisin otetaan."""
        runners = self._runners(["50"])
        tr = pd.DataFrame([
            {"horse_id": "50", "tr_start_interval_group": 1,
             **{c: None for c in TRAVRONDEN_FEATURE_COLS if c != "tr_start_interval_group"}},
            {"horse_id": "50", "tr_start_interval_group": 31,
             **{c: None for c in TRAVRONDEN_FEATURE_COLS if c != "tr_start_interval_group"}},
        ])
        result = merge_travronden_features(runners, tr)
        # keep="last" → 31
        assert result.loc[result["horse_id"] == "50", "tr_start_interval_group"].iloc[0] == 31
