"""
Walk-forward backtest.

KRIITTINEN: Random train/test split antaa AINA liian optimistisia tuloksia
ravimallissa. Concept drift on todellinen (säännöt muuttuvat, kuski-piiri
muuttuu, ratasuhteet muuttuvat).

OIKEA TAPA:
  1. Treenaa data 2020-2023
  2. Ennusta 2024 Q1 (ei ole nähnyt)
  3. Lisää 2024 Q1 treenidataan
  4. Ennusta 2024 Q2
  5. Toista jokaisesta kvartaalista

  -> Tämä simuloi miten mallisi käyttäytyy LIVENÄ.

MITTARIT:
  - Per-quarter ROI (paljonko olisit voittanut/hävinnyt)
  - Per-quarter CLV (jos closing odds saatavilla)
  - AUC ja Brier score kalibroinnista
  - Edge decay: pieneneekö mallin edge ajan myötä?
    Jos kyllä -> retraining-frekvenssi pitää nostaa.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.models.ranker import (
    FEATURE_COLS,
    CATEGORICAL_COLS,
    train_ranker,
    predict_win_probabilities,
)


@dataclass
class BacktestResult:
    period: str
    n_races: int
    n_value_bets: int
    total_staked: float
    total_pnl: float
    roi_pct: float
    avg_edge_pct: float
    win_rate: float
    brier_score: float


def quarterly_walk_forward(
    runners_with_features: pd.DataFrame,
    races: pd.DataFrame,
    initial_train_end: str = "2023-12-31",
    edge_threshold: float = 0.05,
    flat_stake: float = 100.0,
) -> pd.DataFrame:
    """Aja walk-forward backtest neljännesvuosittain.

    Args:
        runners_with_features: kaikki historian runnerit + feature-sarakkeet
                              + finish_position + win_odds_final
        races: race-master-data (race_date)
        initial_train_end: ensimmäisen treeniperiodin loppu
        edge_threshold: minimi edge value-pelille (5%)
        flat_stake: tasapanos per peli (yksinkertaistus, vaihda Kellyyn myöhemmin)
    """
    df = runners_with_features.merge(
        races[["race_id", "race_date"]],
        on="race_id",
        how="left",
    )
    df["race_date"] = pd.to_datetime(df["race_date"])

    # Quarterit treenin alkupisteen jälkeen
    test_start = pd.to_datetime(initial_train_end) + pd.Timedelta(days=1)
    test_end = df["race_date"].max()
    quarters = pd.date_range(test_start, test_end, freq="QS")

    results: list[BacktestResult] = []

    for q_start in quarters:
        q_end = q_start + pd.offsets.QuarterEnd(0)
        train_df = df[df["race_date"] < q_start]
        test_df = df[
            (df["race_date"] >= q_start) & (df["race_date"] <= q_end)
        ]

        if len(train_df) < 1000 or len(test_df) < 50:
            continue

        # Treenaa
        model = train_ranker(train_df)

        # Ennusta
        preds = predict_win_probabilities(model, test_df)
        merged = test_df.merge(
            preds[["race_id", "horse_id", "win_prob"]],
            on=["race_id", "horse_id"],
        )

        # Value-pelit
        merged["expected_value"] = merged["win_prob"] * merged["win_odds_final"]
        merged["edge"] = merged["expected_value"] - 1.0
        bets = merged[merged["edge"] >= edge_threshold].copy()

        if bets.empty:
            continue

        bets["pnl"] = np.where(
            bets["finish_position"] == 1,
            flat_stake * (bets["win_odds_final"] - 1),
            -flat_stake,
        )

        total_staked = len(bets) * flat_stake
        total_pnl = bets["pnl"].sum()

        # Brier kalibroinnista (kaikilla runnereilla, ei vain peleillä)
        merged["actual_win"] = (merged["finish_position"] == 1).astype(int)
        brier = ((merged["win_prob"] - merged["actual_win"]) ** 2).mean()

        results.append(
            BacktestResult(
                period=f"{q_start.strftime('%Y-Q%q' if False else '%Y-%m')}",
                n_races=test_df["race_id"].nunique(),
                n_value_bets=len(bets),
                total_staked=total_staked,
                total_pnl=total_pnl,
                roi_pct=100 * total_pnl / total_staked if total_staked else 0,
                avg_edge_pct=bets["edge"].mean() * 100,
                win_rate=(bets["finish_position"] == 1).mean(),
                brier_score=brier,
            )
        )

    return pd.DataFrame([r.__dict__ for r in results])


def edge_decay_analysis(backtest_df: pd.DataFrame) -> dict:
    """Onko edge pienenemässä ajan myötä? Jos kyllä → retraining tiheämmin."""
    if len(backtest_df) < 4:
        return {"verdict": "ei tarpeeksi dataa", "trend_slope": None}

    backtest_df = backtest_df.reset_index(drop=True)
    backtest_df["period_idx"] = range(len(backtest_df))

    slope = np.polyfit(backtest_df["period_idx"], backtest_df["roi_pct"], 1)[0]

    if slope < -1.0:
        verdict = "❌ Edge pienenee selvästi - retreenaa kuukausittain"
    elif slope < -0.3:
        verdict = "🟡 Lievää edge decayta - retreenaa neljännesvuosittain"
    else:
        verdict = "✅ Edge stabiili - puolivuosittainen retraining riittää"

    return {
        "verdict": verdict,
        "trend_slope": slope,
        "first_half_roi": backtest_df["roi_pct"].head(len(backtest_df) // 2).mean(),
        "second_half_roi": backtest_df["roi_pct"].tail(len(backtest_df) // 2).mean(),
    }
