"""Testit src/data/track_codes.py -moduulille (A2-korjaus).

Varmistaa että:
  - TRACKCODE_TO_NAME-mappi on kattava (kaikki DB:ssä havaitut koodit löytyvät)
  - normalize_track() toimii oikein kaikille syötteille
  - Mappi ei sisällä tunnettuja virheitä (väärät koodit aiheuttavat 0-matcheja)
"""

from __future__ import annotations

import pytest

from src.data.track_codes import TRACKCODE_TO_NAME, normalize_track


# ---------------------------------------------------------------------------
# Empirisesti vahvistetut koodit (DB-ristiviitteestä 2026-04-27…2026-05-09)
# ---------------------------------------------------------------------------

EMPIRICALLY_VERIFIED = {
    "Å": "Åby",
    "S": "Solvalla",
    "Ax": "Axevalla",
    "L": "Lindesberg",
    "Bo": "Boden",
    "J": "Jägersro",
    "Bs": "Bollnäs",
    "Ro": "Romme",
    "G": "Gävle",
    "U": "Umåker",
    "D": "Dannero",
    "F": "Färjestad",
    "Ö": "Örebro",
    "Vi": "Visby",
    "Åm": "Åmål",
    "B": "Bergsåker",
    "E": "Eskilstuna",
    "Mp": "Mantorp",
    "Rä": "Rättvik",
    "Ös": "Östersund",
    "Hd": "Halmstad",
    "Ti": "Tingsryd",
    "År": "Årjäng",
    "Sk": "Skellefteå",
    "H": "Hagmyren",
    "Kr": "Kalmar",
}

# Tunnetut virheelliset koodit joita ei tule olla mapissa
# (nämä olivat TASK_PLAN_FIXES.md:n ehdotuksessa mutta ovat väärin)
KNOWN_WRONG_MAPPINGS = {
    "Ås": "Åby",    # väärin: "Å"="Åby" on oikein
    "Ma": "Mantorp", # väärin: "Mp"="Mantorp" on oikein
    "Lu": "Lindesberg", # epävarma: "L"="Lindesberg" on DB-vahvistettu
}


class TestTrackCodeMap:
    """Testit TRACKCODE_TO_NAME-mapille."""

    @pytest.mark.parametrize("code,expected", sorted(EMPIRICALLY_VERIFIED.items()))
    def test_empirically_verified_codes_present(self, code: str, expected: str):
        """Kaikki DB:stä havaitut koodit löytyvät mapista oikeilla nimillä."""
        assert code in TRACKCODE_TO_NAME, f"Koodi {code!r} puuttuu mapista"
        assert TRACKCODE_TO_NAME[code] == expected, (
            f"TRACKCODE_TO_NAME[{code!r}] = {TRACKCODE_TO_NAME[code]!r}, "
            f"odotettiin {expected!r}"
        )

    def test_no_duplicate_values_for_different_codes(self):
        """Kaksi eri koodia ei saa kuvata samaan rataniksi
        (jokainen ATG-nimi on uniikin koodi vastaus)."""
        values = list(TRACKCODE_TO_NAME.values())
        duplicates = [v for v in set(values) if values.count(v) > 1]
        assert not duplicates, (
            f"Duplikaattiarvot mapissa (kaksi koodia → sama nimi): {duplicates}"
        )

    def test_wrong_code_as_was_not_in_map(self):
        """'Ås' ei saa olla mapissa — oikea koodi on 'Å' (DB-vahvistettu)."""
        assert "Ås" not in TRACKCODE_TO_NAME, (
            "Väärä koodi 'Ås' on mapissa — poista se, oikea koodi on 'Å'=Åby"
        )

    def test_wrong_code_ma_not_in_map(self):
        """'Ma' ei saa olla mapissa — oikea koodi on 'Mp' (DB-vahvistettu)."""
        assert "Ma" not in TRACKCODE_TO_NAME, (
            "Väärä koodi 'Ma' on mapissa — poista se, oikea koodi on 'Mp'=Mantorp"
        )

    def test_all_values_are_strings(self):
        """Mappi sisältää vain string-arvoja."""
        for code, name in TRACKCODE_TO_NAME.items():
            assert isinstance(code, str), f"Avain {code!r} ei ole string"
            assert isinstance(name, str), f"Arvo {name!r} ei ole string"
            assert len(code) >= 1, f"Tyhjä avain mapissa"
            assert len(name) >= 3, f"Lyhyt arvo {name!r} — onko tämä oikea ratanimi?"

    def test_covers_all_26_verified_tracks(self):
        """Mappi kattaa kaikki 26 DB:ssä havaittua rataa."""
        for code in EMPIRICALLY_VERIFIED:
            assert code in TRACKCODE_TO_NAME, (
                f"Koodi {code!r} puuttuu — kaikki 26 DB-rataa pitää kattaa"
            )


class TestNormalizeTrack:
    """Testit normalize_track()-funktiolle."""

    def test_known_code_returns_atg_name(self):
        """Tunnettu koodi palautetaan ATG-nimenä."""
        assert normalize_track("S") == "Solvalla"
        assert normalize_track("Ax") == "Axevalla"
        assert normalize_track("Bo") == "Boden"

    def test_unknown_code_returned_as_is(self):
        """Tuntematon koodi palautetaan sellaisenaan (ei kaadu, ei poista dataa)."""
        assert normalize_track("XYZ") == "XYZ"
        assert normalize_track("???") == "???"

    def test_none_returns_none(self):
        """None-syöte palauttaa None — ei kaadu."""
        assert normalize_track(None) is None

    def test_already_atg_name_passthrough(self):
        """ATG-nimet (jo normalisoitu) läpäistään sellaisenaan."""
        # Jos horse_starts-dataa on jo normalisoitu ATG-nimelle,
        # funktio palauttaa sen sellaisenaan (ei mapissa → palautetaan raakakoodi)
        # Tämä on hyväksyttävä: normalisointi on idempotentti olemassa oleville nimille
        result = normalize_track("Solvalla")
        assert result == "Solvalla"  # ei löydy mapista → palautetaan sellaisenaan

    @pytest.mark.parametrize("code,expected", [
        ("B", "Bergsåker"),    # EI Boden — DB vahvisti
        ("Bo", "Boden"),       # Boden
        ("Bs", "Bollnäs"),     # EI Bergsåker — DB vahvisti
        ("Å", "Åby"),          # EI Ås
        ("Mp", "Mantorp"),     # EI Ma
    ])
    def test_db_verified_ambiguous_codes(self, code: str, expected: str):
        """Tarkistaa koodeja jotka TASK_PLAN_FIXES.md:ssä oli väärin.

        Nämä koodit olivat virheellisiä auditoijan ehdotuksessa —
        DB-datan perusteella vahvistettu oikeat kuvaukset.
        """
        assert normalize_track(code) == expected, (
            f"normalize_track({code!r}) = {normalize_track(code)!r}, "
            f"odotettiin {expected!r} (DB-vahvistettu)"
        )
