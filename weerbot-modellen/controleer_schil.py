#!/usr/bin/env python3
"""Controleert of het versienummer van de servicewerker de schil nog dekt.

  python3 weerbot-modellen/controleer_schil.py          controleren
  python3 weerbot-modellen/controleer_schil.py --zet    bijwerken

sw.js serveert de schil cache-first en ruimt oude caches op zodra VERSIE
verandert. Blijft dat nummer staan terwijl een schilbestand wijzigt, dan houdt
elke bezoeker de oude versie: de nieuwe komt pas binnen als hij de app een
tweede keer opent, en bij een servicewerker die al draait vaak helemaal niet.

Dat is geen theorie. Tussen v9 en v10 wijzigde portefeuille.html vier keer
zonder dat het nummer meeging, en dat was op het scherm niet te zien — de
pagina laadde, hij was alleen oud.

Daarom draagt VERSIE een vingerafdruk van de schil, en faalt deze controle
zodra die niet meer klopt. Het versienummer is dan niet iets om aan te denken
maar iets wat de zelftest afdwingt.

Buiten de vingerafdruk blijven de bestanden die de workflows zelf herschrijven:
app_params.js en modellen.json veranderen wekelijks zonder dat er aan de schil
iets verandert, en zouden de controle elke kalibratie laten omvallen.
"""
import hashlib
import re
import sys
from pathlib import Path

WORTEL = Path(__file__).resolve().parent.parent
SW = WORTEL / "sw.js"

# De schilbestanden die code zijn. app_params.js en modellen/modellen.json
# staan wel in SCHIL maar niet hier: die worden wekelijks herschreven.
VAST = [
    "index.html",
    "portefeuille.html",
    "manifest.webmanifest",
    "weerbot-modellen/polymarkt.js",
    "weerbot-modellen/weerbot-ml.js",
    "weerbot-modellen/weerbot-ml-koppel.js",
]


def vingerafdruk() -> str:
    h = hashlib.sha256()
    for pad in VAST:
        p = WORTEL / pad
        if not p.exists():
            print(f"  schil: {pad} ontbreekt")
            sys.exit(1)
        h.update(pad.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:8]


def huidige_versie(tekst: str) -> str:
    m = re.search(r'VERSIE\s*=\s*VOORVOEGSEL\s*\+\s*"([^"]+)"', tekst)
    if not m:
        print("  schil: VERSIE niet gevonden in sw.js")
        sys.exit(1)
    return m.group(1)


def main(argv: list) -> int:
    tekst = SW.read_text()
    versie = huidige_versie(tekst)
    afdruk = vingerafdruk()

    if argv and argv[0] == "--zet":
        # het nummer voor het streepje blijft van de mens, de afdruk erachter
        # komt uit de bestanden
        basis = versie.split("-")[0]
        nieuw = f"{basis}-{afdruk}"
        if nieuw == versie:
            print(f"  schil: al bij ({versie})")
            return 0
        SW.write_text(tekst.replace(f'"{versie}"', f'"{nieuw}"', 1))
        print(f"  schil: {versie} -> {nieuw}")
        return 0

    if versie.endswith("-" + afdruk):
        print(f"  schil in orde : {versie}")
        return 0

    print(f"  schil: VERSIE is {versie}, maar de schil hoort op -{afdruk}")
    print("     Een schilbestand is gewijzigd zonder dat de servicewerker een")
    print("     nieuwe versie kreeg. Bezoekers houden dan de oude pagina.")
    print("     Bijwerken: python3 weerbot-modellen/controleer_schil.py --zet")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
