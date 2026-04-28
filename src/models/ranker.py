"""
LightGBM-pohjainen voittotodennäköisyysmalli.

Ratkaiseva valinta: käytetään LambdaRank-objectivea (learning to rank),
ei binääristä luokittelua. Syy: lähdössä on KILPAILU - hevoset eivät
ole riippumattomia. LambdaRank oppii järjestämään hevoset todenmukaisesti
saman lähdön sisällä.

Pisteet -> todennäköisyydet softmaxilla per lähtö.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

# Piirteet joita käytetään mallissa
FEATURE_COLS: list[str] = [
    # Muoto
    "form_avg_finish_5",
    "form_win_rate_5",
    "form_top3_rate_5",
    "form_avg_km_time_5",
    "form_best_km_time_5",
    "form_market_avg_5",
    "form_days_since_last",
    # Ohjastaja & valmentaja
    "driver_is_win_mean",
    "driver_is_win_count",
    "driver_is_top3_mean",
    "trainer_is_win_mean",
    "trainer_is_top3_mean",
    # Lähtöasetelma
    "inside_post",
    "back_row",
    "handicap_meters",
    "track_horse_starts",
    "track_horse_win_rate",
]

CATEGORICAL_COLS: list[str] = ["distance_category", "start_method"]


def train_ranker(
    train_df: pd.DataFrame,
    feature_cols: Sequence[str] = FEATURE_COLS,
    categorical_cols: Sequence[str] = CATEGORICAL_COLS,
    num_boost_round: int = 500,
) -> lgb.Booster:
    """Treenaa LightGBM lambdarank-objectivella.

    Args:
        train_df: pitää sisältää race_id, finish_position, ja feature-sarakkeet.
        feature_cols: numeeriset piirteet
        categorical_cols: kategoriset piirteet (label-encodataan)
    """
    df = train_df.dropna(subset=["finish_position"]).copy()

    # Ranker-target: käännetään sijoitus pisteeksi (1. -> korkein)
    max_pos = df.groupby("race_id")["finish_position"].transform("max")
    df["relevance"] = (max_pos - df["finish_position"] + 1).astype(int)

    # Ryhmäkoot per lähtö (lambdarankin vaatimus)
    group_sizes = df.groupby("race_id").size().values

    X = df[list(feature_cols) + list(categorical_cols)].copy()
    for col in categorical_cols:
        X[col] = X[col].astype("category")

    y = df["relevance"].values

    train_set = lgb.Dataset(
        X,
        label=y,
        group=group_sizes,
        categorical_feature=list(categorical_cols),
    )

    params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_at": [1, 3],
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_data_in_leaf": 20,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "verbose": -1,
    }

    return lgb.train(params, train_set, num_boost_round=num_boost_round)


def predict_win_probabilities(
    model: lgb.Booster,
    race_df: pd.DataFrame,
    feature_cols: Sequence[str] = FEATURE_COLS,
    categorical_cols: Sequence[str] = CATEGORICAL_COLS,
) -> pd.DataFrame:
    """Ennusta voittotodennäköisyydet kullekin hevoselle lähdössä.

    Muunto pisteistä todennäköisyyksiksi: softmax per lähtö.
    Tämä takaa että todennäköisyydet summautuvat 1.0:aan per lähtö.
    """
    X = race_df[list(feature_cols) + list(categorical_cols)].copy()
    for col in categorical_cols:
        X[col] = X[col].astype("category")

    raw_scores = model.predict(X)

    out = race_df[["race_id", "horse_id", "start_number"]].copy()
    out["score"] = raw_scores

    # Softmax per lähtö
    out["win_prob"] = (
        out.groupby("race_id")["score"]
        .transform(lambda s: np.exp(s - s.max()) / np.exp(s - s.max()).sum())
    )
    return out


def detect_value_bets(
    predictions: pd.DataFrame,
    odds: pd.DataFrame,
    edge_threshold: float = 0.05,
) -> pd.DataFrame:
    """Yhdistä mallin ennusteet bookkerin kertoimiin ja etsi value-pelit.

    Value = mallin P(voitto) * kerroin > 1 + edge_threshold
    Eli odotettu tuotto per pelattu kruunu > 5%.
    """
    df = predictions.merge(
        odds[["race_id", "horse_id", "win_odds"]],
        on=["race_id", "horse_id"],
    )
    df["expected_value"] = df["win_prob"] * df["win_odds"]
    df["edge_pct"] = (df["expected_value"] - 1.0) * 100
    df["is_value_bet"] = df["expected_value"] > (1.0 + edge_threshold)
    return df.sort_values("edge_pct", ascending=False)


def kelly_fraction(
    win_prob: float, odds: float, fraction: float = 0.25
) -> float:
    """Fraktioitu Kelly-panostussuositus.

    Käytä neljäsosa-Kellyä alussa - täysi Kelly on kalibrointivirheille
    aivan liian aggressiivinen kun mallisi on vasta uusi.
    """
    if odds <= 1.0 or win_prob <= 0:
        return 0.0
    b = odds - 1
    q = 1 - win_prob
    full_kelly = (b * win_prob - q) / b
    return max(0.0, full_kelly * fraction)


def evaluate_calibration(
    predictions: pd.DataFrame, n_bins: int = 10
) -> pd.DataFrame:
    """Kalibrointitaulu: P(voitto) ennustettu vs. toteutunut voitto-%.

    Hyvä malli: ennustettu 20% -> toteutunut ~20%.
    """
    df = predictions.dropna(subset=["finish_position"]).copy()
    df["actual_win"] = (df["finish_position"] == 1).astype(int)
    df["bin"] = pd.cut(df["win_prob"], bins=n_bins)
    return df.groupby("bin").agg(
        n=("actual_win", "size"),
        pred_mean=("win_prob", "mean"),
        actual_mean=("actual_win", "mean"),
    )


def save_model(model: lgb.Booster, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(path))


def load_model(path: str | Path) -> lgb.Booster:
    return lgb.Booster(model_file=str(path))
