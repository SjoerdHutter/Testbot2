#!/usr/bin/env python3
"""Letterlijke tabellen uit een javascriptbestand lezen.

Stond eerst als `_js_letterlijk` in signalen.py. Nu ook waarneming.py de
restfactortabel uit polymarkt.js haalt, hoort de lezer op een plek te staan die
allebei kunnen importeren: anders staat dezelfde parser twee keer in de repo en
loopt de ene versie ooit uit de pas met de andere.

Het punt van dit bestand is dat de app en de python-kant dezelfde getallen
gebruiken. Wat in polymarkt.js staat is de waarheid; python leest mee.
"""
import json
import re
from pathlib import Path

WORTEL = Path(__file__).resolve().parent.parent
POLY_JS = WORTEL / "weerbot-modellen" / "polymarkt.js"


def letterlijk(naam: str, tekst: str):
    """De waarde van `var <naam> = {...};` of `var <naam> = [...];` uit een
    javascriptbestand, als python-waarde. Genoeg voor de tabellen die we delen:
    sleutels zonder aanhalingstekens, verder alleen getallen en teksten."""
    m = re.search(r"\bvar\s+" + re.escape(naam) + r"\s*=\s*", tekst)
    if not m:
        raise ValueError(f"{naam} niet gevonden in {POLY_JS.name}")
    begin = m.end()
    open_teken = tekst[begin]
    sluit = {"{": "}", "[": "]"}.get(open_teken)
    if not sluit:
        # Een kaal getal, zoals `var W_DEMPING = 0.7;`. Alles tot de
        # puntkomma, zonder commentaar erachter.
        rest = tekst[begin:tekst.index(";", begin)]
        rest = re.sub(r"/\*.*?\*/", "", rest, flags=re.S).strip()
        try:
            return json.loads(rest)
        except ValueError:
            raise ValueError(f"{naam} is geen object, lijst of getal")
    diep, eind = 0, None
    for i in range(begin, len(tekst)):
        if tekst[i] == open_teken:
            diep += 1
        elif tekst[i] == sluit:
            diep -= 1
            if diep == 0:
                eind = i + 1
                break
    if eind is None:
        raise ValueError(f"{naam} loopt niet netjes af")
    blok = tekst[begin:eind]
    blok = re.sub(r"/\*.*?\*/", "", blok, flags=re.S)        # blokcommentaar eruit
    blok = re.sub(r"//[^\n]*", "", blok)                     # regelcommentaar eruit
    # Sleutels zonder aanhalingstekens alsnog quoten. Ook getallen: de
    # restfactortabellen in polymarkt.js zijn per uur genummerd (`0: 1.00`), en
    # json wil daar een tekst zien.
    blok = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*|\d+)\s*:", r'\1"\2":', blok)
    blok = re.sub(r",\s*([}\]])", r"\1", blok)               # komma voor het slot
    return json.loads(blok)


def poly_tekst() -> str:
    """De inhoud van polymarkt.js. Losse functie zodat een test hem kan vervangen."""
    return POLY_JS.read_text()
