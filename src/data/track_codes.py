"""Travsportin trackCode → ATG:n ratanimi -mappaus.

Travsport käyttää 1–3 merkin lyhennettä (esim. "S" = Solvalla).
ATG käyttää koko nimeä. Tämä mappi normalisoi horse_starts-rivit
samaan ratamerkintään kuin races.track jotta track-historian
laskenta race_setup_features():ssa toimii.

Lähde: **empiirisesti vahvistettu** DB-ristiviitekyselyllä 2026-04-27…2026-05-09.
Kysely JOIN:i horse_starts.track (Travsport short code) ↔ races.track (ATG full name)
lähdöillä joissa (horse_id, race_date) matchaa molemmissa tauluissa.

HUOM: Auditoijan TASK_PLAN_FIXES.md:n ehdottama mappaus sisälsi virheitä
(esim. "B"="Boden", "Bs"="Bergsåker", "Bo"="Bollnäs", "Ås"="Åby", "Ma"="Mantorp").
Alla oleva mappaus on korjattu DB-datan perusteella.

Vahvistettu (n = starttimäärä per rata, 2026-04-27…2026-05-09):
  Å=Åby (311), S=Solvalla (206), Ax=Axevalla (198), L=Lindesberg (197),
  Bo=Boden (194), J=Jägersro (192), Bs=Bollnäs (188), Ro=Romme (183),
  G=Gävle (163), U=Umåker (140), D=Dannero (133), F=Färjestad (115),
  Ö=Örebro (110), Vi=Visby (107), Åm=Åmål (102), B=Bergsåker (94),
  E=Eskilstuna (93), Mp=Mantorp (93), Rä=Rättvik (93), Ös=Östersund (91),
  Hd=Halmstad (86), Ti=Tingsryd (81), År=Årjäng (79), Sk=Skellefteå (75),
  H=Hagmyren (68), Kr=Kalmar (52)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Kaikki 26 ruotsalaista ravirataa joita havaittu tuotantodatassa.
# Avain = Travsport-lyhenne, arvo = ATG:n koko ratanimi.
TRACKCODE_TO_NAME: dict[str, str] = {
    "Å":  "Åby",
    "S":  "Solvalla",
    "Ax": "Axevalla",
    "L":  "Lindesberg",
    "Bo": "Boden",
    "J":  "Jägersro",
    "Bs": "Bollnäs",
    "Ro": "Romme",
    "G":  "Gävle",
    "U":  "Umåker",
    "D":  "Dannero",
    "F":  "Färjestad",
    "Ö":  "Örebro",
    "Vi": "Visby",
    "Åm": "Åmål",
    "B":  "Bergsåker",
    "E":  "Eskilstuna",
    "Mp": "Mantorp",
    "Rä": "Rättvik",
    "Ös": "Östersund",
    "Hd": "Halmstad",
    "Ti": "Tingsryd",
    "År": "Årjäng",
    "Sk": "Skellefteå",
    "H":  "Hagmyren",
    "Kr": "Kalmar",
}

# Tunnetut gallopp-radat (ei ravilähtöjä, suodatetaan pois muualla).
# Sisällytetty tähän dokumentointia varten.
GALLOP_TRACK_CODES: frozenset[str] = frozenset({"Br", "GG", "JG"})

# Travsportin starttimuoto-koodit → ATG:n starttimuotonimet.
# Travsport käyttää lyhenteitä ("A", "V") kun ATG käyttää pitkiä nimiä ("auto", "volte").
# Käytetään form_features():ssa jotta B2-segmentointi (same_method) toimii.
# Lähde: Travsportin scraper-dokumentaatio + DB-vahvistus 2026-05-10.
START_METHOD_TO_ATG: dict[str, str] = {
    "A": "auto",      # autostart (aikastartti)
    "V": "volte",     # voltstart (seisova lähtö)
    "L": "auto",      # linjestart — harvinainen, käytännössä sama kuin auto
}


def normalize_track(code: str | None) -> str | None:
    """Muunna Travsport-koodi ATG:n rataniksi.

    Jos koodi ei löydy mappauksesta, palauttaa alkuperäisen arvon
    ja lokittaa varoituksen. Tällöin track-historia ei löydy,
    mutta data ei katoa.

    Args:
        code: Travsportin trackCode (esim. "S", "Ax", "Bo")
              TAI jo ATG-muodossa oleva ratanimi (palautetaan sellaisenaan).

    Returns:
        ATG:n koko ratanimi tai alkuperäinen koodi jos ei mappausta.
    """
    if code is None:
        return None
    normalized = TRACKCODE_TO_NAME.get(code)
    if normalized is None:
        # Säilytetään alkuperäinen — voi olla jo ATG-muodossa tai tuntematon koodi
        logger.debug("normalize_track: ei mappausta koodille %r — käytetään sellaisenaan", code)
        return code
    return normalized
