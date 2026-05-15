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
    calibrate_isotonic,
    apply_isotonic,
)

# Bugi #3 -korjaus (15.5.2026): kalibrointijakson pituus päivissä.
# Viimeiset CALIB_DAYS päivää treeniikkunasta käytetään isotonic-kalibrointiin.
# Loput (vanhempi data) ovat puhdas treenisetti.
_CALIB_DAYS = 14
_CALIB_MIN_ROWS = 50   # minimi kalibrointiriveille (alle → ei kalibrointia)
_PURE_TRAIN_MIN_ROWS = 100  # minimi puhtaalle treenille (alle → ei kalibrointia)


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
    # race_date voi jo olla features-DataFramessa (build_feature_matrix lisää sen).
    # Mergetään vain jos puuttuu — näin vältetään _x/_y-konflikti.
    if "race_date" in runners_with_features.columns:
        df = runners_with_features.copy()
    else:
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

        # Bugi #3 -korjaus: käytä isotonic-kalibrointia jos riittävästi dataa.
        # Viimeiset CALIB_DAYS päivää treeniikkunasta = kalibrointisetti.
        train_df["race_date"] = pd.to_datetime(train_df["race_date"])
        calib_start = train_df["race_date"].max() - pd.Timedelta(days=_CALIB_DAYS)
        pure_train_df = train_df[train_df["race_date"] < calib_start]
        calib_df = train_df[train_df["race_date"] >= calib_start]

        if len(pure_train_df) < _PURE_TRAIN_MIN_ROWS or len(calib_df) < _CALIB_MIN_ROWS:
            # Liian vähän dataa kalibrointiin — käytä raaka softmax
            model = train_ranker(train_df)
            preds = predict_win_probabilities(model, test_df)
        else:
            model = train_ranker(pure_train_df)
            calib_preds = predict_win_probabilities(model, calib_df)
            calib_with_truth = calib_preds.merge(
                calib_df[["race_id", "horse_id", "finish_position"]],
                on=["race_id", "horse_id"],
            )
            iso = calibrate_isotonic(calib_with_truth)
            preds_raw = predict_win_probabilities(model, test_df)
            preds = apply_isotonic(preds_raw, iso)

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
                period=f"{q_start.year}-Q{(q_start.month - 1) // 3 + 1}",
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


def rolling_walk_forward(
    runners_with_features: pd.DataFrame,
    races: pd.DataFrame,
    window_days: int = 14,
    train_window_days: int = 28,
    edge_threshold: float = 0.05,
    flat_stake: float = 100.0,
) -> pd.DataFrame:
    """Walk-forward backtest mukautuvalla ikkunan pituudella.

    Kvartaali-ikkuna (`quarterly_walk_forward`) on liian karkea kun dataa on
    vain viikkoja — ensimmäinen kvarttaali-ikkuna voi jäädä kokonaan tyhjäksi.
    Tämä funktio toimii heti kun `train_window_days` verran historiaa on
    saatavilla, ja etenee `window_days`:n pituisissa askeleissa.

    Käyttö alkuvaiheessa (< 6 kk dataa):
        results = rolling_walk_forward(features, races, window_days=14, train_window_days=28)
    Myöhemmin (≥ 6 kk dataa): vaihda quarterly_walk_forward:iin.

    TÄRKEÄ RAJOITUS: Vaikka tämä tuottaa tuloksia jo 14 vrk:n ikkunalla,
    stop/go-päätöstä EI tehdä alle 8 viikon (≥ 4 × window_days) tuloksesta
    (tilastollinen luotettavuusraja, C2-vaatimus).

    Args:
        runners_with_features: kaikki historian runnerit + feature-sarakkeet
                              + finish_position + win_odds_final
        races: race-master-data (race_date)
        window_days: testijoukon pituus päivissä (oletus 14)
        train_window_days: treenidatan minimipituus ennen ensimmäistä testiä (oletus 28).
            Kun historiaa on > 60 vrk, harkitse train_window_days=56 tai 84 luotettavammille
            tuloksille — pienellä ikkunalla LightGBM voi ylioppia kausivaihtelua.
        edge_threshold: minimi edge value-pelille (5 %)
        flat_stake: tasapanos per peli

    Returns:
        DataFrame jossa per ikkuna: period, n_races, n_value_bets,
        total_staked, total_pnl, roi_pct, avg_edge_pct, win_rate, brier_score.
        Tyhjä DataFrame jos dataa ei ole tarpeeksi.
    """
    # race_date voi jo olla features-DataFramessa (build_feature_matrix lisää sen).
    # Mergetään vain jos puuttuu — näin vältetään _x/_y-konflikti.
    if "race_date" in runners_with_features.columns:
        df = runners_with_features.copy()
    else:
        df = runners_with_features.merge(
            races[["race_id", "race_date"]],
            on="race_id",
            how="left",
        )
    df["race_date"] = pd.to_datetime(df["race_date"])

    date_min = df["race_date"].min()
    date_max = df["race_date"].max()

    train_start = date_min
    first_test_start = train_start + pd.Timedelta(days=train_window_days)

    if first_test_start >= date_max:
        # Ei tarpeeksi dataa edes ensimmäiseen ikkunaan
        return pd.DataFrame(columns=[
            "period", "n_races", "n_value_bets", "total_staked",
            "total_pnl", "roi_pct", "avg_edge_pct", "win_rate", "brier_score",
        ])

    results: list[BacktestResult] = []

    window_start = first_test_start
    while window_start < date_max:
        window_end = window_start + pd.Timedelta(days=window_days - 1)

        train_df = df[df["race_date"] < window_start]
        test_df = df[
            (df["race_date"] >= window_start) & (df["race_date"] <= window_end)
        ]

        # Tarvitaan riittävästi dataa treenaukseen ja testiin
        if len(train_df) < 100 or len(test_df) < 10:
            window_start += pd.Timedelta(days=window_days)
            continue

        # Treenaa uudelleen joka ikkunalle — simuloi live-retraining-sykliä.
        # Bugi #3 -korjaus: käytä isotonic-kalibrointia jos riittävästi dataa.
        calib_start_dt = window_start - pd.Timedelta(days=_CALIB_DAYS)
        pure_train_df = train_df[train_df["race_date"] < calib_start_dt]
        calib_df = train_df[train_df["race_date"] >= calib_start_dt]

        if len(pure_train_df) < _PURE_TRAIN_MIN_ROWS or len(calib_df) < _CALIB_MIN_ROWS:
            # Liian vähän dataa kalibrointiin — käytä raaka softmax
            model = train_ranker(train_df)
            preds = predict_win_probabilities(model, test_df)
        else:
            model = train_ranker(pure_train_df)
            calib_preds = predict_win_probabilities(model, calib_df)
            calib_with_truth = calib_preds.merge(
                calib_df[["race_id", "horse_id", "finish_position"]],
                on=["race_id", "horse_id"],
            )
            iso = calibrate_isotonic(calib_with_truth)
            preds_raw = predict_win_probabilities(model, test_df)
            preds = apply_isotonic(preds_raw, iso)

        merged = test_df.merge(
            preds[["race_id", "horse_id", "win_prob"]],
            on=["race_id", "horse_id"],
        )

        merged["expected_value"] = merged["win_prob"] * merged["win_odds_final"]
        merged["edge"] = merged["expected_value"] - 1.0
        bets = merged[merged["edge"] >= edge_threshold].copy()

        merged["actual_win"] = (merged["finish_position"] == 1).astype(int)
        brier = float(((merged["win_prob"] - merged["actual_win"]) ** 2).mean())

        if bets.empty:
            # Merkitään ikkuna vaikka pelejä ei syntynyt — brier on silti hyödyllinen
            results.append(BacktestResult(
                period=f"{window_start.date()}–{window_end.date()}",
                n_races=test_df["race_id"].nunique(),
                n_value_bets=0,
                total_staked=0.0,
                total_pnl=0.0,
                roi_pct=0.0,
                avg_edge_pct=0.0,
                win_rate=0.0,
                brier_score=brier,
            ))
        else:
            bets["pnl"] = np.where(
                bets["finish_position"] == 1,
                flat_stake * (bets["win_odds_final"] - 1),
                -flat_stake,
            )
            total_staked = len(bets) * flat_stake
            total_pnl = float(bets["pnl"].sum())
            results.append(BacktestResult(
                period=f"{window_start.date()}–{window_end.date()}",
                n_races=test_df["race_id"].nunique(),
                n_value_bets=len(bets),
                total_staked=total_staked,
                total_pnl=total_pnl,
                roi_pct=100 * total_pnl / total_staked if total_staked else 0.0,
                avg_edge_pct=float(bets["edge"].mean() * 100),
                win_rate=float((bets["finish_position"] == 1).mean()),
                brier_score=brier,
            ))

        window_start += pd.Timedelta(days=window_days)

    return pd.DataFrame([r.__dict__ for r in results])


def edge_decay_analysis(
    backtest_df: pd.DataFrame,
    score_col: str = "roi_pct",
) -> dict:
    """Onko mallin laatu tai edge pienenemässä ajan myötä?

    Suositeltu mittari: `brier_score` (pienempi = parempi kalibrointi).
    Brier-score on vähemmän varianssinen kuin ROI ja kuvaa suoraan
    mallin todennäköisyyksien tarkkuutta — ei markkinakerroin-melua.

    Taaksepäin-yhteensopiva: oletusarvo `roi_pct` säilyttää vanhan käyttäytymisen.

    Args:
        backtest_df: quarterly_walk_forward() tai rolling_walk_forward():n tulos.
        score_col: sarake jota analysoidaan. Suositukset:
            "brier_score" — mallin kalibrointi (pienempi on parempi)
            "roi_pct"     — taloudellinen tuotto (suurempi on parempi, default)

    Returns:
        dict jossa:
            verdict      — tekstimuotoinen tulos
            trend_slope  — regressiokulmakerroin (None jos dataa ei tarpeeksi)
            score_col    — käytetty mittari (kirjauksia varten)
            first_half   — mittarin keskiarvo ensimmäisellä puoliskolla
            second_half  — mittarin keskiarvo toisella puoliskolla
    """
    if len(backtest_df) < 4:
        return {
            "verdict": "ei tarpeeksi dataa",
            "trend_slope": None,
            "score_col": score_col,
            "first_half": None,
            "second_half": None,
        }

    if score_col not in backtest_df.columns:
        raise ValueError(
            f"score_col='{score_col}' ei löydy backtest_df:stä. "
            f"Saatavilla: {list(backtest_df.columns)}"
        )

    df = backtest_df.reset_index(drop=True).copy()

    # Parannus #8: suodata tyhjät viikot ROI-modessa
    # (Brier lasketaan aina kaikille viikoille, joten suodatus vain ROI-modessa)
    if score_col == "roi_pct" and "n_value_bets" in df.columns:
        df = df[df["n_value_bets"] > 0].reset_index(drop=True)
        if len(df) < 4:
            return {
                "verdict": "ei tarpeeksi pelillisiä viikkoja",
                "trend_slope": None,
                "score_col": score_col,
                "first_half": None,
                "second_half": None,
            }

    df["period_idx"] = range(len(df))

    slope = float(np.polyfit(df["period_idx"], df[score_col], 1)[0])
    half = len(df) // 2
    first_half = float(df[score_col].head(half).mean())
    second_half = float(df[score_col].tail(half).mean())

    # Suunnan tulkinta: Brier-score → pienempi parempi (negatiivinen slope = paranee)
    #                  roi_pct      → suurempi parempi (positiivinen slope = paranee)
    brier_mode = score_col == "brier_score"

    if brier_mode:
        # Brier: slope < 0 tarkoittaa paranemista (ei driftiä)
        # TODO: tarkenna kynnysarvoja kun C1-monitoroinnista on muutaman kuukauden
        # tuotantodataa joka näyttää luonnollisen viikkovaihtelun. Nykyiset arvot
        # (0.002, 0.0005) ovat alkuvaiheen heuristiikkoja. (auditoija 10.5.2026)
        if slope > 0.002:
            verdict = "❌ Mallin kalibrointi heikkenee (brier nousee) — retreenaa kuukausittain"
        elif slope > 0.0005:
            verdict = "🟡 Lievää kalibraation heikkenemistä — retreenaa neljännesvuosittain"
        else:
            verdict = "✅ Kalibrointi stabiili — puolivuosittainen retraining riittää"
    else:
        # roi_pct: slope < 0 tarkoittaa heikkenemistä
        if slope < -1.0:
            verdict = "❌ Edge pienenee selvästi — retreenaa kuukausittain"
        elif slope < -0.3:
            verdict = "🟡 Lievää edge decayta — retreenaa neljännesvuosittain"
        else:
            verdict = "✅ Edge stabiili — puolivuosittainen retraining riittää"

    return {
        "verdict": verdict,
        "trend_slope": slope,
        "score_col": score_col,
        "first_half": first_half,
        "second_half": second_half,
    }
