#!/usr/bin/env python3
"""Maakt een logboek rechthoekig nadat er kolommen achteraan zijn bijgekomen.

Twee keer nodig gehad:

  logs/ensemble_log.csv   de acht spreidingskolommen achter n_leden
  logs/signalen.csv       uren_tot_sluiting en einde_api achter strat_a_signaal

Zonder migratie krijgt csv.DictReader rijen van twee verschillende lengtes: de
oude regels missen de nieuwe sleutels en leveren None op wisselende plekken. Dat
raakt bot/kalibratie.py, dat ensemble_log.csv zo inleest. De migratie zet de
nieuwe kop bovenaan en vult de oude regels aan met lege velden. Bestaande
waarden blijven exact zoals ze waren en er wordt niets nageschat: een lege
kolom betekent dat die meting er destijds niet was.

Draai vanuit de hoofdmap van de repo:

    python3 bot/migratie_logkoppen.py              beide logboeken in logs/
    python3 bot/migratie_logkoppen.py <pad>        een los bestand

Het is veilig om de migratie nog eens te draaien: staat de nieuwe kop er al,
dan blijft het bestand ongemoeid.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logger
import signalen

# bestandsnaam -> de kop zoals het schrijvende script hem nu neerzet
KOPPEN = {
    "ensemble_log.csv": logger.ENS_KOP,
    "signalen.csv": signalen.KOP,
}


def migreer(pad: Path) -> int:
    kop_nu = KOPPEN.get(pad.name)
    if kop_nu is None:
        print(f"  {pad}: onbekend logboek, ik weet niet welke kop erbij hoort")
        return 1
    if not pad.exists():
        print(f"  {pad} bestaat niet, niets te doen")
        return 0
    with open(pad, newline="") as f:
        rijen = list(csv.reader(f))
    if not rijen:
        print(f"  {pad} is leeg, niets te doen")
        return 0

    kop = rijen[0]
    if kop == kop_nu:
        print(f"  {pad} heeft de nieuwe kop al ({len(rijen) - 1} regels)")
        return 0
    if kop != kop_nu[:len(kop)]:
        print(f"  {pad} heeft een onbekende kop: {','.join(kop)}")
        return 1

    breed = len(kop_nu)
    uit = [kop_nu]
    for rij in rijen[1:]:
        if not rij:
            continue
        uit.append(rij + [""] * (breed - len(rij)))

    tijdelijk = pad.with_suffix(pad.suffix + ".nieuw")
    with open(tijdelijk, "w", newline="") as f:
        csv.writer(f).writerows(uit)
    tijdelijk.replace(pad)
    print(f"  {pad}: {len(uit) - 1} regels aangevuld van {len(kop)} naar {breed} kolommen")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(migreer(Path(sys.argv[1])))
    map_ = Path.cwd() / "logs"
    sys.exit(max(migreer(map_ / naam) for naam in KOPPEN))
