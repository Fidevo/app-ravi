"""
Scratch handler - perutun hevosen aiheuttaman uudelleenlaskennan logiikka.

YLEISIN BUGI: malli ennustaa 12 hevosen lähdölle, hevonen perutaan 5 min
ennen lähtöä, käyttäjä pelaa 11 hevosen lähtöön mutta vanhoilla
todennäköisyyksillä. Tulos: todennäköisyydet eivät enää summaudu 1.0:aan
ja value-laskenta vääristyy.

OIKEA TOIMINTA:
  1. Tunnista perutut hevoset
  2. Renormalisoi muiden hevosten todennäköisyydet pro rata
     (yksinkertaisin tapa - oletus että relatiiviset voimasuhteet säilyvät)
  3. Laske kertoimet uudelleen jos bookkeri tarjoaa "rule 4"-vähennystä

EDISTYNEEMPI VAIHTOEHTO:
  Aja malli uudelleen ilman perutua hevosta. Tämä on rehellisempi
  jos perutu hevonen muutti pace-asetelmaa (esim. ainoa keulahevonen
  perutaan -> jäljellä olevien dynamiikka muuttuu).
"""

from __future__ import annotations

import pandas as pd


def renormalize_after_scratch(
    predictions: pd.DataFrame,
    scratched_horse_ids: list[str],
) -> pd.DataFrame:
    """Yksinkertainen pro rata -renormalisointi.

    Args:
        predictions: sis. race_id, horse_id, win_prob
        scratched_horse_ids: peruttujen hevosten id:t

    Returns:
        Päivitetty predictions-frame jossa scratchatut on poistettu
        ja muiden todennäköisyydet skaalattu summautumaan 1.0:aan per lähtö.
    """
    df = predictions[~predictions["horse_id"].isin(scratched_horse_ids)].copy()

    df["win_prob"] = df.groupby("race_id")["win_prob"].transform(
        lambda s: s / s.sum() if s.sum() > 0 else s
    )
    df["was_renormalized"] = True
    return df


def apply_rule_4_deduction(
    odds: float, scratched_win_prob: float
) -> float:
    """Bookkereiden "Rule 4" -vähennys peruttujen aiheuttamaan kertoimeen.

    Yksinkertaistettu kaava: jos perutu hevonen oli market-todennäköisyydeltään
    p, vähennetään muiden kertoimista karkeasti suhteessa p:hen.

    Tarkka kaava vaihtelee bookkerien välillä - tarkista oman bookkerisi
    Rule 4 -taulukko tarkkoja arvoja varten. Tämä on approksimaatio.
    """
    if scratched_win_prob <= 0:
        return odds
    deduction = scratched_win_prob
    return 1.0 + (odds - 1.0) * (1.0 - deduction)


def detect_pace_changes_after_scratch(
    runners_in_race: pd.DataFrame,
    scratched_horse_ids: list[str],
) -> dict:
    """Tarkista muuttiko scratch lähdön pace-asetelmaa olennaisesti.

    Jos ainoa "leader"-tyyppinen hevonen perutaan, tempo muuttuu pehmeäksi
    -> kannattaa ajaa malli uudelleen, ei vain renormalisoida.

    Returns dict jossa:
      - pace_change: bool
      - reason: str
      - recommend_full_rerun: bool
    """
    scratched = runners_in_race[
        runners_in_race["horse_id"].isin(scratched_horse_ids)
    ]
    remaining = runners_in_race[
        ~runners_in_race["horse_id"].isin(scratched_horse_ids)
    ]

    if "style_dominant" not in scratched.columns:
        return {
            "pace_change": False,
            "reason": "Pace-piirteet puuttuvat",
            "recommend_full_rerun": False,
        }

    scratched_styles = scratched["style_dominant"].tolist()
    leaders_remaining = (remaining["style_dominant"] == "leader").sum()

    if "leader" in scratched_styles and leaders_remaining <= 1:
        return {
            "pace_change": True,
            "reason": "Leader-tyyppinen perutu, tempo muuttuu pehmeäksi",
            "recommend_full_rerun": True,
        }

    return {
        "pace_change": False,
        "reason": "Scratch ei muuta pace-asetelmaa olennaisesti",
        "recommend_full_rerun": False,
    }
