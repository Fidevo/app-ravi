"""
Bankroll management.

Mallin edge ei ole ainoa muuttuja - panostuskoko ratkaisee survival-todennäköisyyden.
Voittavat ammattilaiset rikkovat fraktio-Kellyn vielä alaspäin riskirajoilla.

KESKEISET SÄÄNNÖT:
  1. Fractional Kelly (0.25x) - täysi Kelly olettaa täydellisen kalibroinnin
  2. Hard cap per peli: max 2% bankrollista
  3. Hard cap per päivä: max 5% bankrollista
  4. Stop-loss: -15% viikossa → 14 päivän pakollinen tauko
  5. Korreloituneet pelit: jos pelaat useaa hevosta samasta lähdöstä
     tai V-systeemistä, Kelly-summa pitää jakaa
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd


@dataclass
class BankrollConfig:
    """Konservatiiviset oletukset - säädä omaan riskinsietoon."""

    starting_bankroll: float = 10000.0
    kelly_fraction: float = 0.25            # 1/4 Kelly
    max_bet_pct: float = 0.02               # max 2% per peli
    max_daily_pct: float = 0.05             # max 5% per päivä
    weekly_stop_loss_pct: float = 0.15      # -15%/vko -> tauko
    stop_loss_cooldown_days: int = 14
    min_edge_pct: float = 0.05              # älä pelaa alle 5% edgella


@dataclass
class BankrollState:
    """Live-tila joka päivittyy jokaisen pelin myötä."""

    config: BankrollConfig
    current_bankroll: float = 0.0
    daily_staked: dict[date, float] = field(default_factory=dict)
    weekly_pnl: dict[str, float] = field(default_factory=dict)  # ISO-week
    locked_until: date | None = None        # asetettu jos stop-loss

    def __post_init__(self) -> None:
        if self.current_bankroll == 0.0:
            self.current_bankroll = self.config.starting_bankroll


def kelly_stake(
    win_prob: float,
    odds: float,
    bankroll: float,
    config: BankrollConfig,
) -> float:
    """Laske panos: fractional Kelly + kovat ylärajat.

    Returns 0 jos:
      - edge alle min_edge_pct
      - kerroin <= 1.0
      - kelly negatiivinen
    """
    if odds <= 1.0 or win_prob <= 0:
        return 0.0

    # Edge-tarkistus
    expected_value = win_prob * odds
    edge = expected_value - 1.0
    if edge < config.min_edge_pct:
        return 0.0

    # Kelly
    b = odds - 1
    q = 1 - win_prob
    full_kelly = (b * win_prob - q) / b
    if full_kelly <= 0:
        return 0.0

    fractional = full_kelly * config.kelly_fraction
    fractional = min(fractional, config.max_bet_pct)

    return round(bankroll * fractional, 2)


def can_place_bet(
    state: BankrollState,
    proposed_stake: float,
    bet_date: date,
) -> tuple[bool, str]:
    """Tarkista riskirajojen vastainen peli."""
    cfg = state.config

    if state.locked_until and bet_date <= state.locked_until:
        return False, f"🛑 Stop-loss aktiivinen {state.locked_until} asti"

    daily_used = state.daily_staked.get(bet_date, 0.0)
    daily_limit = state.current_bankroll * cfg.max_daily_pct
    if daily_used + proposed_stake > daily_limit:
        return False, (
            f"📉 Päiväraja ylittyy ({daily_used + proposed_stake:.0f} > "
            f"{daily_limit:.0f} SEK)"
        )

    if proposed_stake > state.current_bankroll * cfg.max_bet_pct:
        return False, "📉 Yksittäisen pelin yläraja ylittyy"

    return True, "OK"


def correlated_kelly_adjust(
    bets_in_race: list[tuple[float, float]],
    config: BankrollConfig,
) -> list[float]:
    """Säädä Kelly-panokset jos pelaat useaa hevosta samasta lähdöstä.

    bets_in_race: lista (win_prob, odds) -tupleja
    Yksinkertainen approksimaatio: jaa kunkin pelin Kelly suhteessa
    yhteenlaskettuun voittotodennäköisyyteen (vain yksi voi voittaa).
    """
    if len(bets_in_race) <= 1:
        return [
            kelly_stake(p, o, 1.0, config)
            for p, o in bets_in_race
        ]

    # Jaa Kelly suhteessa kokonaistodennäköisyyteen
    total_prob = sum(p for p, _ in bets_in_race)
    raw = [kelly_stake(p, o, 1.0, config) for p, o in bets_in_race]

    if total_prob >= 1.0:
        # Pelaat käytännössä kentän - vähennä rajusti
        return [r * 0.5 for r in raw]
    return raw


def update_after_settlement(
    state: BankrollState,
    bet_date: date,
    pnl: float,
) -> None:
    """Päivitä bankroll ja tarkista stop-loss."""
    state.current_bankroll += pnl

    week_key = bet_date.strftime("%Y-W%V")
    state.weekly_pnl[week_key] = state.weekly_pnl.get(week_key, 0.0) + pnl

    threshold = state.config.starting_bankroll * state.config.weekly_stop_loss_pct
    if state.weekly_pnl[week_key] < -threshold:
        state.locked_until = bet_date + timedelta(
            days=state.config.stop_loss_cooldown_days
        )


def daily_summary(state: BankrollState, target_date: date) -> dict:
    """Päivän tilanne yhteenvetona dashboardia varten."""
    cfg = state.config
    used = state.daily_staked.get(target_date, 0.0)
    limit = state.current_bankroll * cfg.max_daily_pct
    return {
        "date": target_date,
        "bankroll": state.current_bankroll,
        "daily_used": used,
        "daily_limit": limit,
        "daily_remaining": max(0, limit - used),
        "locked": state.locked_until is not None and target_date <= state.locked_until,
    }
