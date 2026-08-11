#!/usr/bin/env python3
"""Zelftest voor de herkansingen op de ensemblefetch.

  python3 bot/test_logger.py

Controleert drie dingen:

  door      Een aanroep die meteen lukt gaat er ongewijzigd doorheen, met
            argumenten en al, en kost precies een poging.
  herstel   Twee haperingen achter elkaar mogen de stad niet kosten: de derde
            poging telt en het antwoord komt alsnog terug. Dit is de fout die
            elke run vijf tot zeven van de 49 steden uit het ensemblelog liet
            vallen, altijd met een TLS-handshake die niet rond kwam.
  reden     Blijft het misgaan, dan gaat de laatste reden mee omhoog met het
            aantal pogingen erbij. Een stad die stilletjes verdwijnt is erger
            dan een gat dat zichzelf meldt.

Draait offline: er wordt hier niets opgehaald, alleen geteld hoe vaak
met_herkansing zijn opdracht opnieuw aanbiedt.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import logger as L   # noqa: E402


def _zonder_wachten():
    """De pauze tussen de pogingen op nul; de test hoeft niet echt te wachten."""
    echte, L.FETCH_PAUZE = L.FETCH_PAUZE, 0.0
    return echte


def test_door() -> bool:
    goed = True
    n = {"x": 0}

    def haal(stad, velden, timeout=60):
        n["x"] += 1
        return {"stad": stad, "velden": velden, "timeout": timeout}

    uit = L.met_herkansing(haal, "AMS", "temperature_2m_max", timeout=30)
    if uit != {"stad": "AMS", "velden": "temperature_2m_max", "timeout": 30}:
        print(f"  door      MISLUKT: argumenten komen niet ongewijzigd aan: {uit}")
        goed = False
    if n["x"] != 1:
        print(f"  door      MISLUKT: {n['x']} pogingen voor een aanroep die lukt")
        goed = False
    if goed:
        print("  door      ok: lukt het meteen, dan blijft het bij een poging")
    return goed


def test_herstel() -> bool:
    goed = True
    echte_pauze = _zonder_wachten()
    try:
        n = {"x": 0}

        def hapert(stad):
            n["x"] += 1
            if n["x"] < 3:
                raise OSError("_ssl.c:993: The handshake operation timed out")
            return {("max", "2026-08-12", "ecmwf_ifs025"): [30.0, 30.4, 29.6]}

        uit = L.met_herkansing(hapert, "AMS")
        if not uit:
            print("  herstel   MISLUKT: na twee haperingen nog geen leden")
            goed = False
        if n["x"] != 3:
            print(f"  herstel   MISLUKT: {n['x']} pogingen, verwacht 3")
            goed = False
    finally:
        L.FETCH_PAUZE = echte_pauze
    if goed:
        print(f"  herstel   ok: herstelt na twee haperingen "
              f"({L.FETCH_POGINGEN} pogingen, {L.FETCH_TIMEOUT} s per poging)")
    return goed


def test_reden() -> bool:
    goed = True
    echte_pauze = _zonder_wachten()
    try:
        n = {"x": 0}

        def stuk():
            n["x"] += 1
            raise OSError("_ssl.c:993: The handshake operation timed out")

        try:
            L.met_herkansing(stuk)
        except Exception as ex:   # noqa: BLE001 - dat is precies wat we toetsen
            tekst = str(ex)
            if "handshake" not in tekst or "pogingen" not in tekst:
                print(f"  reden     MISLUKT: de reden zegt niet wat er misging: {tekst}")
                goed = False
        else:
            print("  reden     MISLUKT: een blijvende storing kwam er stil doorheen")
            goed = False
        if n["x"] != L.FETCH_POGINGEN:
            print(f"  reden     MISLUKT: {n['x']} pogingen, verwacht {L.FETCH_POGINGEN}")
            goed = False
    finally:
        L.FETCH_PAUZE = echte_pauze
    if goed:
        print("  reden     ok: blijft het stuk, dan meldt de fout zich met reden")
    return goed


def main() -> int:
    print("\n  Zelftest herkansingen\n")
    goed = all([test_door(), test_herstel(), test_reden()])
    print("\n  " + ("Alles in orde.\n" if goed else "ER GING IETS MIS.\n"))
    return 0 if goed else 1


if __name__ == "__main__":
    sys.exit(main())
