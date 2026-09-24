#!/usr/bin/env python3
"""logs/signalen.csv opdelen in logs/signalen/<begindatum>.csv.

  python3 bot/migratie_signalen_delen.py --droog    tonen wat er zou gebeuren
  python3 bot/migratie_signalen_delen.py            uitvoeren

Waarom dit moet
---------------
GitHub weigert elk bestand boven de 100 MB met een pre-receive hook. Op
19 september 2026 ging logs/signalen.csv daar op 100,32 MB overheen, en sindsdien
faalde de actie Signalenlog vier keer per dag met GH001: het werk gebeurde wel en
werd bij het pushen weggegooid. Omdat de vier logboeken in één commit meegaan,
staat er sinds die dag ook niets nieuws meer in ensemble_log.csv, taf_log.csv en
nws_log.csv.

Het bestand groeit met 2,96 MB per dag en loopt dus niet vanzelf weer goed. De
geschiedenis opschonen helpt evenmin -- dat is in september al eens gedaan en gaf
één dag lucht, want het probleem is het bestand van vandaag en niet de historie.

Hoe het opdeelt
---------------
Regel voor regel, zodat er nooit meer dan één deel tegelijk in het geheugen
staat. Er rolt een nieuw deel zodra het huidige logger.DEEL_GRENS haalt, en elk
deel heet naar de datum van zijn eerste regel. Sorteren op naam is daarmee
hetzelfde als sorteren op tijd, en logger.delen() leest ze in die volgorde terug.

De kop gaat in elk deel mee. Regels die zelf een kop zijn -- die zitten in de
reeks, want migratie_logkoppen.py heeft er ooit een tussengezet -- worden
overgeslagen, zodat er per deel precies één kop staat.

Het oude bestand wordt pas verwijderd als alle delen geschreven zijn en het
aantal regels klopt. Gaat er iets mis, dan staat het er nog.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logger

WORTEL = Path(__file__).resolve().parent.parent
OUD = WORTEL / "logs" / "signalen.csv"
MAP = WORTEL / "logs" / "signalen"


def deelnaam(gelogd: str, gebruikt: set) -> str:
    """<datum>.csv, met een volgnummer als die datum al een deel heeft."""
    datum = (gelogd or "onbekend")[:10]
    naam = f"{datum}.csv"
    n = 2
    while naam in gebruikt:
        naam = f"{datum}-{n}.csv"
        n += 1
    gebruikt.add(naam)
    return naam


def migreer(oud: Path = OUD, map_: Path = MAP, grens: int = None,
            droog: bool = False) -> int:
    grens = logger.DEEL_GRENS if grens is None else grens
    if not oud.exists():
        print(f"  {oud} bestaat niet, niets te doen")
        return 0
    if map_.exists() and any(map_.glob("*.csv")):
        print(f"  {map_} heeft al delen; eerst opruimen of met de hand nakijken")
        return 1

    with open(oud, newline="") as f:
        lezer = csv.reader(f)
        try:
            kop = next(lezer)
        except StopIteration:
            print(f"  {oud} is leeg")
            return 1

        if not droog:
            map_.mkdir(parents=True, exist_ok=True)
        gebruikt: set = set()
        delen, n_rijen, n_koppen = [], 0, 0
        huidig = uit = schrijver = None
        gezien = 0

        for rij in lezer:
            if not rij:
                continue
            if rij[0] == kop[0]:          # een tussengezette kop
                n_koppen += 1
                continue
            gezien += 1
            if huidig is None or (uit is not None and uit.tell() >= grens):
                if uit is not None:
                    uit.close()
                naam = deelnaam(rij[0] if rij else "", gebruikt)
                huidig = map_ / naam
                delen.append([naam, 0])
                if droog:
                    uit, schrijver = None, None
                else:
                    uit = open(huidig, "w", newline="")
                    schrijver = csv.writer(uit)
                    schrijver.writerow(kop)
            if schrijver is not None:
                schrijver.writerow(rij)
            delen[-1][1] += 1
            n_rijen += 1
            # in een droge run is er geen tell(); rol dan op een schatting
            if droog and delen[-1][1] * 190 >= grens:
                huidig = None
        if uit is not None:
            uit.close()

    print(f"  {n_rijen} regels uit {oud.name}"
          + (f", {n_koppen} tussengezette koppen overgeslagen" if n_koppen else ""))
    for naam, n in delen:
        maat = (map_ / naam).stat().st_size / 1048576 if not droog else 0
        print(f"    {naam:20s} {n:8d} regels" + (f"  {maat:6.1f} MB" if not droog else ""))
    if droog:
        print("  droge run: er is niets geschreven of verwijderd")
        return 0

    terug = sum(sum(1 for _ in csv.reader(open(map_ / naam, newline=""))) - 1
                for naam, _ in delen)
    if terug != n_rijen:
        print(f"  FOUT: {terug} regels teruggelezen tegen {n_rijen} geschreven; "
              f"{oud.name} blijft staan")
        return 1
    oud.unlink()
    try:
        waar = map_.relative_to(WORTEL)
    except ValueError:                 # een toetsmap buiten de repo
        waar = map_
    print(f"  {oud.name} verwijderd; {len(delen)} delen in {waar}")
    return 0


def main(argv: list) -> int:
    return migreer(droog="--droog" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
