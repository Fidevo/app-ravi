"""
LightGBM-pohjainen voittotodennäköisyysmalli.

Ratkaiseva valinta: käytetään LambdaRank-objectivea (learning to rank),
ei binääristä luokittelua. Syy: lähdössä on KILPAILU - hevoset eivät
ole riippumattomia. LambdaRank oppii järjestämään hevoset todenmukaisesti
saman lähdön sisällä.

Pisteet -> todennäköisyydet softmaxilla per lähtö.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import log_loss

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Piirremäärittelyt
# ---------------------------------------------------------------------------
# HUOM: horse_age vaatii birth_year-sarakkeen runners-DataFramessa (JOIN
# horses-tauluun ennen build_feature_matrix()-kutsua). Jos sarake puuttuu,
# train_ranker() / predict_win_probabilities() ohittaa sen automaattisesti
# ja kirjaa varoituksen. Muut piirteet tulevat runners- tai races-taulusta
# suoraan eikä niille tarvita erillistä JOIN:ia.
# ---------------------------------------------------------------------------

FEATURE_COLS: list[str] = [
    # --- Hevosen muoto (build_features.form_features) ---
    "form_avg_finish_5",
    "form_win_rate_5",
    "form_top3_rate_5",
    "form_avg_km_time_5",
    "form_best_km_time_5",
    "form_market_avg_5",
    "form_days_since_last",
    # B2: segmentoidut muotopiirteet — vain sama starttimuoto / matkaluokka
    "form_avg_finish_5_same_method",
    "form_avg_finish_5_same_dist",
    # --- ATG-aggregaatit hevosesta: koko ura (runners-taulusta suoraan) ---
    "atg_lifetime_win_rate",
    "atg_lifetime_top3_rate",
    "atg_lifetime_starts",
    # A3: K1-vuoto-pollutoidut kentät — kommentoitu pois 2026-05-10.
    # ATG päivittää nämä post-race → arvot olivat n+1-race-tilassa eikä pre-race.
    # backfill_correct_atg_aggregates() korjasi lifetime-kentät mutta ei näitä
    # (nimittäjä ei tiedossa). Aktivoi takaisin kun >= 600 puhdasta lähtöä
    # on kerätty K1-korjauksen (2026-05-10) jälkeen — eli n. 2026-09.
    # "atg_current_year_win_rate",  # K1-pollutoitu
    # "atg_driver_win_pct",         # K1-pollutoitu
    # "atg_driver_starts",          # K1-pollutoitu
    # "atg_trainer_win_pct",        # K1-pollutoitu
    # "atg_trainer_starts",         # K1-pollutoitu
    "atg_best_km_for_this_setup",   # paras km tämä matka+starttimuoto
    # --- Meistä lasketut rolling-tilastot (kasvavat ajan myötä, parempia V4+) ---
    "driver_win_rate_365d",
    "driver_starts_365d",
    "driver_top3_rate_365d",
    "trainer_win_rate_365d",
    "trainer_top3_rate_365d",
    # --- Lähtöasetelma (build_features.race_setup_features) ---
    "inside_post",
    "back_row",
    "handicap_meters",
    "track_horse_starts",
    "track_horse_win_rate",
    # --- Lähdön luokka (races-taulusta) ---
    "race_min_earnings",
    "race_max_earnings",
    # --- Kengät ja sulky: muutossignaalit (runners-taulusta suoraan) ---
    "shoes_changed_front",
    "shoes_changed_back",
    "sulky_changed",
    # --- Johdetut piirteet (build_features.derived_features) ---
    "barfota_law_active",
    "horse_age",   # Vaatii birth_year runners-DataFramessa — ohitetaan jos puuttuu
    # --- B2: Sukutaulupiirteet (build_features.sire_features) ---
    # Vaatii horses-taulun horses-parametrina build_feature_matrix():lle.
    # NaN jos isä/emänisä tuntematon tai liian pieni otos (< 30 starttia).
    "sire_lifetime_win_rate",
    "sire_lifetime_starts",
    "dam_sire_lifetime_win_rate",
    "dam_sire_lifetime_starts",
]

CATEGORICAL_COLS: list[str] = [
    "distance_category",   # sprint / middle / long
    "start_method",        # auto / voltstart
    "race_age_group",      # 2yo / 3yo / 3yo+ / 4yo+ / 5yo+
    "track_condition",     # light / heavy (ATG races.condition)
    "sulky_type",          # VA / AM
]


def _resolve_cols(
    df: pd.DataFrame,
    feature_cols: Sequence[str],
    categorical_cols: Sequence[str],
    log_missing: bool = True,
) -> tuple[list[str], list[str]]:
    """Suodata piirrelistat df:ssä olemassa oleviin sarakkeisiin.

    Ohitetaan puuttuvat sarakkeet. Tämä mahdollistaa valinnaisten piirteiden
    (esim. horse_age) lisäämisen FEATURE_COLS:iin ilman että kaikki ympäristöt
    vaativat kyseistä saraketta.

    Args:
        log_missing: Kirjaako WARNING puuttuvista sarakkeista (P3-korjaus).
            True treeniajossa (train_ranker), False ennustamisessa (predict_win_probabilities).
            Näin log ei täyty toistuvista identtisistä varoituksista joka lähdön
            ennusteessa, mutta koulutusajon puutteet näkyvät selvästi.
    """
    avail_feat = [c for c in feature_cols if c in df.columns]
    avail_cat = [c for c in categorical_cols if c in df.columns]
    missing_feat = set(feature_cols) - set(avail_feat)
    missing_cat = set(categorical_cols) - set(avail_cat)
    if log_missing and (missing_feat or missing_cat):
        logger.warning(
            "Puuttuvat piirteet ohitetaan — lisää birth_year JOIN:lla tai "
            "tarkista data: numeeriset=%s, kategoriset=%s",
            sorted(missing_feat), sorted(missing_cat),
        )
    return avail_feat, avail_cat


def train_ranker(
    train_df: pd.DataFrame,
    feature_cols: Sequence[str] = FEATURE_COLS,
    categorical_cols: Sequence[str] = CATEGORICAL_COLS,
    num_boost_round: int = 500,
) -> lgb.Booster:
    """Treenaa LightGBM lambdarank-objectivella.

    Args:
        train_df: pitää sisältää race_id, finish_position, ja feature-sarakkeet.
        feature_cols: numeeriset piirteet (puuttuvat ohitetaan automaattisesti)
        categorical_cols: kategoriset piirteet (puuttuvat ohitetaan automaattisesti)
    """
    df = train_df.dropna(subset=["finish_position"]).copy()

    # Suodata saatavilla oleviin sarakkeisiin — valinnaisia piirteitä
    # (esim. horse_age) ei vaadita kaikissa ympäristöissä.
    avail_feat, avail_cat = _resolve_cols(df, feature_cols, categorical_cols)

    # Ranker-target: käännetään sijoitus pisteeksi (1. -> korkein)
    max_pos = df.groupby("race_id")["finish_position"].transform("max")
    df["relevance"] = (max_pos - df["finish_position"] + 1).astype(int)

    # Ryhmäkoot per lähtö (lambdarankin vaatimus)
    group_sizes = df.groupby("race_id").size().values

    X = df[avail_feat + avail_cat].copy()
    for col in avail_cat:
        X[col] = X[col].astype("category")

    y = df["relevance"].values

    train_set = lgb.Dataset(
        X,
        label=y,
        group=group_sizes,
        categorical_feature=avail_cat,
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


def calibrate_temperature(
    predictions: pd.DataFrame,
) -> float:
    """Opi optimaalinen temperature-kerroin T softmax-kalibroinnille (B3).

    LambdaRankin raw-pisteiden skaala on mielivaltainen — softmax voi
    ali- tai ylikalibroida systemaattisesti. Temperature scaling oppii yhden
    parametrin T validointidatalta minimoimalla NLL (negatiivinen log-likelihood).

    Matemaattisesti: P_i = exp(score_i / T) / sum(exp(score_j / T)) per lähtö.
      T < 1 → terävöittää jakaumaa (yksi vahva suosikki)
      T > 1 → tasoittaa jakaumaa (tasaisempi kilpailu)

    Tarvittavat sarakkeet predictions-DataFramessa:
      race_id, score, finish_position

    Returns:
        float: optimaalinen T (tyypillisesti välillä 0.5–3.0)
    """
    from scipy.optimize import minimize_scalar

    df = predictions.dropna(subset=["finish_position", "score"]).copy()
    actual_win = (df["finish_position"] == 1).astype(float).values
    scores = df["score"].values
    race_ids = df["race_id"].values

    def neg_log_likelihood(T: float) -> float:
        scaled_scores = scores / T
        # Numeerisesti vakaa softmax per lähtö
        probs = np.empty_like(scaled_scores)
        for rid in np.unique(race_ids):
            mask = race_ids == rid
            s = scaled_scores[mask]
            s_stable = s - s.max()
            probs[mask] = np.exp(s_stable) / np.exp(s_stable).sum()
        # NLL: vain voittaneiden hevosten todennäköisyydet
        return -np.sum(actual_win * np.log(probs.clip(1e-9)))

    result = minimize_scalar(neg_log_likelihood, bounds=(0.1, 10.0), method="bounded")
    T_opt: float = float(result.x)
    logger.info("calibrate_temperature: T=%.4f (NLL=%.4f)", T_opt, result.fun)
    return T_opt


def calibrate_isotonic(
    predictions: pd.DataFrame,
) -> IsotonicRegression:
    """Opi ei-parametrinen kalibrointikäyrä softmax-ennusteille (B1).

    Vaihtoehto temperature scalingille: monotoninen mutta ei-parametrinen,
    osaa korjata epälineaarista miskalibrointia. Temperature scaling olettaa
    yhtenäisen kertoimen koko todennäköisyysavaruudelle — isotonic regression
    oppii eri korjauksen eri alueille (esim. tiukemman korjauksen 40–70 %
    alueelle jossa mallit usein ylikalibroituvat).

    Vaatii vähintään ~500 validointiriviä luotettavaan oppimiseen. Pienemmällä
    datalla on ylisovittumisriski — suosi temperature scalingia jos n < 500.

    Tarvittavat sarakkeet predictions-DataFramessa:
      race_id, win_prob, finish_position

    Returns:
        IsotonicRegression-objekti joka on sovitettu validointidataan.
        Tallenna mallin metatietoihin calibration_method="isotonic".
        Soveltaminen: apply_isotonic(predictions, iso).
    """
    df = predictions.dropna(subset=["finish_position", "win_prob"]).copy()
    actual_win = (df["finish_position"] == 1).astype(int).values
    raw_probs = df["win_prob"].values
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(raw_probs, actual_win)
    logger.info(
        "calibrate_isotonic: sovitettu %d validointiriville", len(df)
    )
    return iso


def apply_isotonic(
    predictions: pd.DataFrame,
    iso: IsotonicRegression,
) -> pd.DataFrame:
    """Sovella isotonic-kalibrointi ennusteisiin ja re-normalisoi per lähtö.

    Isotonic regression voi rikkoa summautuvuuden (∑P_i ≠ 1.0 lähdössä)
    koska se on hevoskohtainen. Re-normalisoidaan per lähtö jotta
    todennäköisyydet summautuvat 1.0:aan.

    Args:
        predictions: DataFrame jossa race_id, win_prob
        iso: calibrate_isotonic():sta saatu sovitettu malli

    Returns:
        Kopio DataFramesta jossa win_prob korvattu kalibroituilla arvoilla.
    """
    out = predictions.copy()
    out["win_prob"] = iso.transform(out["win_prob"].values)
    # Re-normalisoi per lähtö — summautuvuus vaatii tämän
    out["win_prob"] = out.groupby("race_id")["win_prob"].transform(
        lambda s: s / s.sum() if s.sum() > 0 else s
    )
    return out


def predict_win_probabilities(
    model: lgb.Booster,
    race_df: pd.DataFrame,
    feature_cols: Sequence[str] = FEATURE_COLS,
    categorical_cols: Sequence[str] = CATEGORICAL_COLS,
    temperature: float = 1.0,
) -> pd.DataFrame:
    """Ennusta voittotodennäköisyydet kullekin hevoselle lähdössä.

    Muunto pisteistä todennäköisyyksiksi: temperature-scaled softmax per lähtö.
    Tämä takaa että todennäköisyydet summautuvat 1.0:aan per lähtö.

    Args:
        temperature: Softmax-lämpötilakerroin (B3). Oletusarvo 1.0 = ei skaalausta.
            Hae optimaalinen T calibrate_temperature()-funktiolla validointidatasta
            ja tallenna mallin metatietoihin. T < 1 terävöittää, T > 1 tasoittaa.
    """
    # P3-korjaus: ei logita puuttuvia sarakkeita ennustamisessa —
    # samat varoitukset toistuisivat joka lähdön kohdalla.
    avail_feat, avail_cat = _resolve_cols(
        race_df, feature_cols, categorical_cols, log_missing=False
    )
    X = race_df[avail_feat + avail_cat].copy()
    for col in avail_cat:
        X[col] = X[col].astype("category")

    raw_scores = model.predict(X)

    out = race_df[["race_id", "horse_id", "start_number"]].copy()
    out["score"] = raw_scores / temperature  # B3: temperature scaling

    # Numeerisesti vakaa softmax per lähtö (max-normalization)
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
