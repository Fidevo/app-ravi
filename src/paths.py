"""Projektin absoluuttiset polut.

Käytä näitä koodissa suhteellisten "data/..."-polkujen sijaan jotta
scheduler/CLI toimii myös jos käynnistetään muusta CWD:stä (systemd
WorkingDirectory:n unohduksessa, tmux-session väärässä paikassa, jne).

Polut lasketaan __file__:stä, joten ne ovat aina oikeita riippumatta
työhakemistosta.
"""

from __future__ import annotations

from pathlib import Path

# src/paths.py -> src/ -> ravit-edge/  (parents[1])
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
DATA_DIR: Path = PROJECT_ROOT / "data"
LOG_DIR: Path = DATA_DIR / "logs"
RAW_DIR: Path = DATA_DIR / "raw"
DB_PATH: Path = DATA_DIR / "ravit.db"

# Varmista että hakemistot ovat olemassa heti modulin importissa.
# Idempotentti, halpaa, eikä tarvitse muistaa kutsua erillistä
# init-funktiota CLI:ssä tai testeissä.
for _d in (DATA_DIR, LOG_DIR, RAW_DIR):
    _d.mkdir(parents=True, exist_ok=True)
