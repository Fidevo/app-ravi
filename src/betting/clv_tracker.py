"""
CLV-tracker (Closing Line Value).

CLV on yksittäinen tärkein metriikka joka erottaa voittavan vedonlyöjän
häviävästä. Voitto/häviö-tilastot vaativat 1000+ pelaa luotettavaan
signaaliin, mutta CLV antaa luotettavan signaalin jo 50-100 pelin jälkeen.

LASKENTA:
  CLV = (your_odds / closing_odds) - 1
  Esim: pelasit @5.0, sulkeutui @4.0
        CLV = 5.0/4.0 - 1 = +25%

Vig-poistettu CLV (rehellisempi, koska bookkereiden marginaali vääristää):
  fair_closing = closing_odds * (1 - vig)
  CLV_devig = your_odds / fair_closing - 1

PIENI MUTTA TÄRKEÄ:
  - Pinnacle/Betfair = sharp markkinat → näiden closing line on
    paras "true probability" -approksimaatio
  - Käytä SHARP-markkinan closing odds:ia CLV-vertailuun, EI sen
    bookkerin omaa closing odds:ia jolla pelasit

TULKINTA:
  > +3% pitkällä aikavälillä  → tuottava systeemi (pidä menossa)
  -3% to +3%                  → break-even, mahdollisesti kohinaa
  < -3% pitkällä aikavälillä  → häviävä systeemi (LOPETA, älä jatka)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass
class Bet:
    """Yksi peli paperitestaukseen tai live-trackingiin."""

    bet_id: str
    timestamp: datetime
    race_id: str
    horse_id: str
    horse_name: str
    bookmaker: str               # "unibet", "betfair", "atg" jne.
    odds_taken: float            # kerroin jolla pelasit
    stake: float                 # panos (yksiköissä, esim. % bankrollista)
    model_prob: float            # mallin antama todennäköisyys
    closing_odds_sharp: float | None = None    # täytetään lähdön alkaessa
    actual_result: int | None = None            # 1 = voitto, 0 = häviö
    payout: float | None = None


def calculate_vig(odds_list: Sequence[float]) -> float:
    """Laske bookkerin marginaali (overround).

    Hyvä bookkeri (Pinnacle): 2-3%
    ATG toto: 15-25%
    Betfair Exchange: 0-2% (komissio päälle)
    """
    if not odds_list or any(o <= 1.0 for o in odds_list):
        return 0.0
    return sum(1.0 / o for o in odds_list) - 1.0


def devig_odds(odds: float, all_odds_in_race: Sequence[float]) -> float:
    """Poista vig kertoimesta -> "fair odds" approksimaatio.

    Käytetään multiplikatiivista (proportional) devig-metodia.
    """
    vig = calculate_vig(all_odds_in_race)
    if vig <= 0:
        return odds
    fair_prob = (1.0 / odds) / (1.0 + vig)
    return 1.0 / fair_prob if fair_prob > 0 else odds


def calculate_clv(bet_odds: float, closing_odds: float) -> float:
    """CLV % - paljonko parempaa kerrointa sait verrattuna closingiin.

    Positiivinen = sait paremman kertoimen kuin closing → hyvä signaali.
    """
    if closing_odds <= 1.0:
        return 0.0
    return bet_odds / closing_odds - 1.0


def calculate_clv_devig(
    bet_odds: float,
    closing_odds: float,
    all_closing_odds: Sequence[float],
) -> float:
    """Vig-poistettu CLV - rehellisempi versio kun closing tulee
    bookkerilta jolla on marginaali."""
    fair_closing = devig_odds(closing_odds, all_closing_odds)
    return calculate_clv(bet_odds, fair_closing)


def summarize_clv(bets: pd.DataFrame) -> dict[str, float]:
    """Yhteenveto CLV-suorituksesta.

    Olettaa että `bets` sisältää: odds_taken, closing_odds_sharp,
                                 stake, actual_result, model_prob
    """
    settled = bets.dropna(subset=["closing_odds_sharp"]).copy()
    if settled.empty:
        return {"n_bets": 0}

    settled["clv"] = settled.apply(
        lambda r: calculate_clv(r["odds_taken"], r["closing_odds_sharp"]),
        axis=1,
    )

    # ROI vain jos lähtö on jo ajettu
    finished = settled.dropna(subset=["actual_result"])
    roi = None
    if not finished.empty:
        finished["pnl"] = np.where(
            finished["actual_result"] == 1,
            finished["stake"] * (finished["odds_taken"] - 1),
            -finished["stake"],
        )
        roi = finished["pnl"].sum() / finished["stake"].sum()

    return {
        "n_bets": len(settled),
        "clv_mean_pct": settled["clv"].mean() * 100,
        "clv_median_pct": settled["clv"].median() * 100,
        "clv_positive_rate": (settled["clv"] > 0).mean(),
        "roi_pct": roi * 100 if roi is not None else None,
        "n_settled": len(finished),
    }


def clv_health_check(summary: dict[str, float]) -> str:
    """Annetaan käyttäjälle suora arvio onko systeemi tuottava.

    Tämä on se viesti joka pelastaa pelikassan: jos CLV on negatiivinen
    pitkällä aikavälillä, voitot ovat tuuria ja systeemi häviää lopulta.
    """
    n = summary.get("n_bets", 0)
    if n < 30:
        return f"⏳ Liian aikaista ({n} peliä) - kerää vähintään 50-100 ennen tulkintaa"

    clv = summary.get("clv_mean_pct", 0.0)
    if clv > 3.0:
        return f"✅ CLV +{clv:.1f}% - vahva merkki tuottavasta edgesta, jatka"
    elif clv > 0.0:
        return f"🟡 CLV +{clv:.1f}% - lievä edge, tarvitaan lisää dataa varmuudeksi"
    elif clv > -3.0:
        return f"🟠 CLV {clv:.1f}% - break-even, edge on kyseenalainen"
    else:
        return (
            f"❌ CLV {clv:.1f}% - HÄVIÄVÄ SYSTEEMI. Lopeta paperitestaus, "
            "korjaa malli ennen oikeaa rahaa."
        )
