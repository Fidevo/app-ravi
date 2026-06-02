"""Bloodstock-signaalin validointi: ennustaako sukutaulu uraa, ja lisääkö se
arvoa HINNAN päälle (= onko huutokauppamarkkina tehoton → exploitoitava value)?

Sama kurinalaisuus kuin vedon ratkaisevassa testissä: todistetaan signaali
ENNEN kuin rakennetaan tuotetta. Kaksi erillistä kysymystä:

  Q1  Ennustaako sukutaulu (isän/emänisän jälkeläisten historiallinen ura)
      hevosen ura-ansaitan ylipäätään?  (Spearman-korrelaatio)
  Q2  RATKAISEVA: lisääkö sukutaulu ennustevoimaa HINNAN päälle?
      Jos ei → markkina hinnoittelee sukutaulun jo → ei edgeä (kuten veto).
      Jos kyllä → ostohinta jättää signaalia käyttämättä → potentiaalinen value.

Lisäksi value-backtest: jos rankkaa erät (sukutaulu-ennuste / hinta) -suhteella,
ansaitsevatko "value"-erät enemmän per maksettu kruunu kuin muut?

AJETAAN PROD-DB:llä (ei paikallinen sample → join osuisi ~nollaan).
  cd /home/ravi/app-ravi && python scripts/eval_bloodstock_signal.py
Paikallinen rakenne-/syntaksitesti:
  RAVI_DATA_DIR=data python scripts/eval_bloodstock_signal.py

Syöte: {DATA_DIR}/asvt_auction_lots.csv (scrape_asvt_auctions.py) + ravit.db
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys

import numpy as np
import pandas as pd

DATA_DIR = os.environ.get("RAVI_DATA_DIR", "data")
DB_PATH = f"{DATA_DIR}/ravit.db"
CSV_PATH = f"{DATA_DIR}/asvt_auction_lots.csv"

# Vain täydet urat validointiin: syntynyt viimeistään tänä vuonna (~10 v uraa).
MATURE_BIRTH_YEAR_MAX = int(os.environ.get("MATURE_MAX", "2016"))
# Sukutaulupiirteen min. jälkeläismäärä (point-in-time) — alle → NaN (kohina).
MIN_PROGENY = 3


def norm_name(s: object) -> str | None:
    """Normalisoi hevosen nimi joiniin: pien-kirjaimet, poista maakoodi '(US)',
    poista välimerkit, tiivistä välit."""
    if not isinstance(s, str) or not s.strip():
        return None
    s = re.sub(r"\(.*?\)", " ", s)          # maakoodi (US),(IT),(FR)...
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s or None


def _spearman(a: pd.Series, b: pd.Series) -> tuple[float, int]:
    m = a.notna() & b.notna()
    n = int(m.sum())
    if n < 10:
        return float("nan"), n
    from scipy.stats import spearmanr
    rho, _ = spearmanr(a[m], b[m])
    return float(rho), n


def main() -> int:
    if not os.path.exists(CSV_PATH):
        print(f"VIRHE: {CSV_PATH} puuttuu — aja ensin scrape_asvt_auctions.py")
        return 2

    # ---- 1. Lataa huutokauppaerät (vain aidot kaupat) ----
    lots = pd.read_csv(CSV_PATH)
    lots = lots[(lots["status"] == "sold") & lots["price_sek"].notna()].copy()
    lots["price_sek"] = lots["price_sek"].astype(float)
    lots["norm"] = lots["name"].map(norm_name)
    lots = lots.dropna(subset=["norm"])
    print(f"[1] Myytyjä eriä hinnalla: {len(lots)}")

    # ---- 2. Lataa DB: hevoset + ura-ansaita (horse_starts.prize_won summa) ----
    con = sqlite3.connect(DB_PATH)
    horses = pd.read_sql("SELECT horse_id, name, birth_year, sire, dam_sire FROM horses", con)
    earn = pd.read_sql(
        "SELECT horse_id, COALESCE(SUM(prize_won),0) AS career_earnings, "
        "COUNT(*) AS career_starts FROM horse_starts GROUP BY horse_id", con
    )
    con.close()
    horses["norm"] = horses["name"].map(norm_name)
    horses = horses.dropna(subset=["norm"])
    # Ura-ansaita KAIKILLE hevosille (käytetään myös sukutaulu-aggregaateissa)
    horses = horses.merge(earn, on="horse_id", how="left")
    horses["career_earnings"] = horses["career_earnings"].fillna(0.0)
    horses["career_starts"] = horses["career_starts"].fillna(0).astype(int)
    horses["birth_year"] = pd.to_numeric(horses["birth_year"], errors="coerce")

    # ---- 3. Join: huutokauppaerä → DB-hevonen (nimi + syntymävuosi) ----
    # Disambiguointi: ensisijaisesti (norm, birth_year), muuten pelkkä norm.
    h_by_nameyear = horses.dropna(subset=["birth_year"]).copy()
    h_by_nameyear["birth_year"] = h_by_nameyear["birth_year"].astype(int)
    # Pudota moniselitteiset (sama norm+vuosi → useita horse_id) → ei luotettava join
    dup = h_by_nameyear.duplicated(["norm", "birth_year"], keep=False)
    amb = int(dup.sum())
    h_uni = h_by_nameyear[~dup]

    lots_y = lots.dropna(subset=["birth_year"]).copy()
    lots_y["birth_year"] = lots_y["birth_year"].astype(int)
    merged = lots_y.merge(
        h_uni[["norm", "birth_year", "horse_id", "sire", "dam_sire",
               "career_earnings", "career_starts"]],
        on=["norm", "birth_year"], how="inner",
    )
    print(f"[2] DB-hevosia: {len(horses)} | moniselitteisiä nimi+vuosi-pareja: {amb}")
    print(f"[3] Join osui (nimi+vuosi): {len(merged)} / {len(lots_y)} "
          f"({100*len(merged)//max(len(lots_y),1)}%)")

    # ---- 4. Kypsyyssuodatin: vain täydet urat validointiin ----
    val = merged[merged["birth_year"] <= MATURE_BIRTH_YEAR_MAX].copy()
    print(f"[4] Kypsiä (syntynyt <= {MATURE_BIRTH_YEAR_MAX}) joinattuja eriä: {len(val)}")
    if len(val) < 30:
        print("\nLiian vähän joinattuja kypsiä eriä luotettavaan analyysiin "
              "(tarve >= 30). Tämä on odotettua paikallisella sample-DB:llä — "
              "aja PROD-DB:llä. Skripti on rakenteellisesti valmis.")
        return 0

    # ---- 5. Point-in-time sukutaulupiirteet (LOO: vain aiemmin syntyneet jälkeläiset) ----
    # sire_progeny_earn = isän MUIDEN, ENNEN tätä hevosta syntyneiden jälkeläisten
    # ura-ansaitan keskiarvo (tieto joka oli "tiedossa" huutokauppahetkellä).
    def progeny_mean(parent_col: str) -> pd.Series:
        out = []
        grp = {k: g.sort_values("birth_year") for k, g in
               horses.dropna(subset=[parent_col, "birth_year"]).groupby(parent_col)}
        for _, row in val.iterrows():
            g = grp.get(row[parent_col])
            if g is None:
                out.append(np.nan); continue
            prior = g[(g["birth_year"] < row["birth_year"]) & (g["horse_id"] != row["horse_id"])]
            out.append(prior["career_earnings"].mean() if len(prior) >= MIN_PROGENY else np.nan)
        return pd.Series(out, index=val.index)

    val["sire_progeny_earn"] = progeny_mean("sire")
    val["dam_sire_progeny_earn"] = progeny_mean("dam_sire")

    val["log_earn"] = np.log1p(val["career_earnings"])
    val["log_price"] = np.log1p(val["price_sek"])

    # ---- Q1: Ennustaako sukutaulu uraa ylipäätään? ----
    print("\n=== Q1: Sukutaulu vs ura-ansaita (Spearman) ===")
    for col in ("sire_progeny_earn", "dam_sire_progeny_earn"):
        rho, n = _spearman(val[col], val["career_earnings"])
        print(f"  {col:24s} rho={rho:+.3f}  (n={n})")
    rho_p, n_p = _spearman(val["price_sek"], val["career_earnings"])
    print(f"  {'price_sek (vertailu)':24s} rho={rho_p:+.3f}  (n={n_p})")

    # ---- Q2 (RATKAISEVA): lisääkö sukutaulu ennustevoimaa HINNAN päälle? ----
    print("\n=== Q2: Lisääkö sukutaulu signaalia hinnan päälle? (5-fold CV R^2) ===")
    from sklearn.linear_model import LinearRegression
    from sklearn.model_selection import cross_val_score

    feat = val.dropna(subset=["sire_progeny_earn", "dam_sire_progeny_earn",
                              "log_price", "log_earn"]).copy()
    print(f"  täydet rivit (sukutaulu+hinta+ura): {len(feat)}")
    if len(feat) >= 50:
        feat["log_sire"] = np.log1p(feat["sire_progeny_earn"])
        feat["log_damsire"] = np.log1p(feat["dam_sire_progeny_earn"])
        y = feat["log_earn"].values
        def cvr2(cols):
            X = feat[cols].values
            return float(np.mean(cross_val_score(LinearRegression(), X, y, cv=5, scoring="r2")))
        r2_price = cvr2(["log_price"])
        r2_both = cvr2(["log_price", "log_sire", "log_damsire"])
        r2_ped = cvr2(["log_sire", "log_damsire"])
        print(f"  A  hinta yksin           R^2 = {r2_price:+.3f}")
        print(f"  B  hinta + sukutaulu     R^2 = {r2_both:+.3f}   (delta vs A: {r2_both-r2_price:+.3f})")
        print(f"  C  pelkkä sukutaulu      R^2 = {r2_ped:+.3f}")
        print("\n  TULKINTA:")
        if r2_both - r2_price < 0.01:
            print("  -> Sukutaulu EI lisää ennustevoimaa hinnan päälle. Markkina")
            print("     hinnoittelee sukutaulun jo → ei edgeä (sama kuin veto). HYLKÄÄ.")
        else:
            print(f"  -> Sukutaulu lisää +{r2_both-r2_price:.3f} R^2 hinnan päälle → markkina")
            print("     jättää signaalia käyttämättä → POTENTIAALINEN value. Jatka value-backtestiin.")
    else:
        print("  Liian vähän täysiä rivejä (>=50) CV:hen — aja PROD-DB:llä.")

    # ---- Value-backtest: ansaitsevatko "alihinnoitellut" erät enemmän per kruunu? ----
    print("\n=== Value-backtest: (sukutaulu-ennuste / hinta) -kvintiilit ===")
    vb = val.dropna(subset=["sire_progeny_earn"]).copy()
    vb = vb[vb["price_sek"] > 0]
    if len(vb) >= 50:
        vb["value_score"] = vb["sire_progeny_earn"] / vb["price_sek"]
        vb["earn_per_sek"] = vb["career_earnings"] / vb["price_sek"]
        vb["q"] = pd.qcut(vb["value_score"], 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
        tab = vb.groupby("q", observed=True).agg(
            n=("earn_per_sek", "size"),
            median_earn=("career_earnings", "median"),
            mean_earn_per_sek=("earn_per_sek", "mean"),
        )
        print(tab.to_string())
        print("  (Jos kvintiili 5 = korkein value tuottaa selvästi parhaan")
        print("   earn_per_sek:n → sukutaulu/hinta-suhde löytää alihinnoittelua.)")
    else:
        print(f"  Liian vähän rivejä ({len(vb)}) — aja PROD-DB:llä.")

    print("\nValmis. Päätöskriteeri = Q2 delta-R^2 + value-backtestin monotonisuus.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
