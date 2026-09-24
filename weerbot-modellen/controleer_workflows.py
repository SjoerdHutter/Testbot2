#!/usr/bin/env python3
"""Controleert of elk pad in een `git add` van een workflow ook bestaat.

  python3 weerbot-modellen/controleer_workflows.py

Waarom dit bestaat: op 24 september 2026 is logs/signalen.csv opgedeeld in
logs/signalen/, maar de commitstap van signalen-log.yml voegde nog het oude pad
toe. `git add` valt dan om op "pathspec did not match any files", en omdat de
stappen met `bash -e` draaien sterft de hele stap -- in nul seconden, na
vijftien minuten markten ophalen die daarmee in de prullenbak gingen.

Dat is precies het soort fout dat de zelftest niet zag: de python-kant klopte,
de toetsen waren groen, en het enige wat niet meer bestond stond in yaml.

Alleen paden uit `git add`-regels. Dat is bewust smal: dat zijn de paden waar
een verkeerde waarde een geslaagde run alsnog laat omvallen, en ze zijn zonder
yaml-ontleder betrouwbaar te herkennen.
"""
import re
import sys
from pathlib import Path

WORTEL = Path(__file__).resolve().parent.parent
MAP = WORTEL / ".github" / "workflows"


def paden_uit(tekst: str) -> list:
    """De paden uit elke `git add`-regel, met voortzetting op een backslash."""
    uit, verder = [], False
    for regel in tekst.splitlines():
        kaal = regel.strip()
        if kaal.startswith("#"):
            continue
        if verder or kaal.startswith("git add "):
            deel = kaal[len("git add "):] if kaal.startswith("git add ") else kaal
            verder = deel.endswith("\\")
            deel = deel.rstrip("\\").strip()
            deel = deel.split("#", 1)[0]            # commentaar achter het pad
            uit += [w for w in deel.split() if not w.startswith("-")]
        else:
            verder = False
    return uit


def main() -> int:
    fouten = 0
    for pad in sorted(MAP.glob("*.yml")):
        for p in paden_uit(pad.read_text()):
            if not (WORTEL / p).exists():
                print(f"  FOUT {pad.name}: `git add {p}` maar dat pad bestaat niet")
                fouten += 1
    if fouten:
        print(f"\n  {fouten} pad(en) in een git add die niet bestaan.")
        print("  Een run die daarop stuit verliest zijn werk in de commitstap.")
        return 1
    print("  workflows in orde: elk pad in een git add bestaat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
