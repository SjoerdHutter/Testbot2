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

En sinds logs/signalen.csv op de 100 MB-limiet van GitHub stukliep, ook de
opdeling van een logboek in stukken:

  rollen    schrijf_deel begint een nieuw deel zodra het huidige de grens
            haalt, en geen seconde eerder.
  kop       elk deel krijgt zijn eigen kop, anders leest een DictReader het
            tweede deel als data.
  volgorde  delen() geeft de stukken op tijdsvolgorde terug, met een nog niet
            opgedeeld logs/<naam>.csv vooraan.
  eigen pad delen(pad=...) geeft precies dat ene bestand; daarop leunen de
            tests van inzet.py en portfolio.py.

Draait offline: er wordt hier niets opgehaald, alleen geteld hoe vaak
met_herkansing zijn opdracht opnieuw aanbiedt, en er wordt in een tijdelijke
map met nepregels geschreven.
"""
import csv
import sys
import tempfile
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


def _in_tijdelijke_map(fn):
    """logger.logmap() leest Path.cwd(); draai de test in een lege map."""
    import os
    with tempfile.TemporaryDirectory() as m:
        hier = os.getcwd()
        try:
            os.chdir(m)
            return fn(Path(m))
        finally:
            os.chdir(hier)


def test_delen() -> bool:
    def draai(_m):
        goed = True
        kop = ["a", "b"]
        rij = [["x" * 40, "y" * 40]]          # ongeveer 85 bytes per regel
        grens = 400

        # rollen: blijven schrijven tot er meer dan één deel is
        paden = [L.schrijf_deel("proef", kop, rij, grens=grens) for _ in range(40)]
        uniek = sorted({p.name for p in paden})
        if len(uniek) < 2:
            print(f"  rollen    MISLUKT: alles in één deel ({uniek})")
            goed = False
        else:
            print(f"  rollen    ok: {len(uniek)} delen bij een grens van {grens} bytes")

        # geen enkel deel mag ver over de grens gaan
        te_groot = [p.name for p in set(paden)
                    if p.stat().st_size > grens + 200]
        if te_groot:
            print(f"  rollen    MISLUKT: {te_groot} ver over de grens")
            goed = False

        # kop: elk deel begint met de kop en heeft er maar één
        for naam in uniek:
            rijen = list(csv.reader(open(L.logmap() / "proef" / naam, newline="")))
            if rijen[0] != kop:
                print(f"  kop       MISLUKT: {naam} begint met {rijen[0]}")
                goed = False
            elif sum(1 for r in rijen if r == kop) != 1:
                print(f"  kop       MISLUKT: {naam} heeft meer dan één kop")
                goed = False
        if goed:
            print("  kop       ok: elk deel heeft precies één kop")

        # volgorde, met een nog niet opgedeeld logboek ervoor
        gevonden = [p.name for p in L.delen("proef")]
        if gevonden != uniek:
            print(f"  volgorde  MISLUKT: {gevonden} tegen {uniek}")
            goed = False
        else:
            oud = L.logmap() / "proef.csv"
            L.schrijf(oud, kop, rij)
            met_oud = [p.name for p in L.delen("proef")]
            if met_oud != ["proef.csv"] + uniek:
                print(f"  volgorde  MISLUKT: oud bestand niet vooraan ({met_oud})")
                goed = False
            else:
                print("  volgorde  ok: op tijd gesorteerd, oud logboek vooraan")

        # een eigen pad overschrijft alles
        eigen = L.logmap() / "ergens_anders.csv"
        if [p.name for p in L.delen("proef", eigen)] != ["ergens_anders.csv"]:
            print("  eigen pad MISLUKT: meegegeven pad genegeerd")
            goed = False
        else:
            print("  eigen pad ok: een meegegeven pad wint")
        return goed

    return _in_tijdelijke_map(draai)


def main() -> int:
    print("\n  Zelftest herkansingen\n")
    goed = all([test_door(), test_herstel(), test_reden(), test_delen()])
    print("\n  " + ("Alles in orde.\n" if goed else "ER GING IETS MIS.\n"))
    return 0 if goed else 1


if __name__ == "__main__":
    sys.exit(main())
