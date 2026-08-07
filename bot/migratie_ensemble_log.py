#!/usr/bin/env python3
"""Eenmalige migratie van logs/ensemble_log.csv naar de bredere kop.

Tot en met de vier handmatig gedraaide dagen had het logboek zeven kolommen:

    gelogd_utc,key,doel_datum,lead,model,gemiddelde,n_leden

logger.py schrijft er nu acht spreidingskolommen achter. Zonder migratie krijgt
csv.DictReader in bot/kalibratie.py rijen van twee verschillende lengtes: de
oude regels missen de nieuwe sleutels en leveren None op wisselende plekken.
Deze migratie zet de nieuwe kop bovenaan en vult de oude regels aan met lege
velden, zodat het bestand rechthoekig is. De bestaande waarden blijven exact
zoals ze waren; er wordt niets herberekend.

Draai vanuit de hoofdmap van de repo:

    python3 bot/migratie_ensemble_log.py            logs/ensemble_log.csv
    python3 bot/migratie_ensemble_log.py <pad>      een ander bestand

Het is veilig om de migratie nog eens te draaien: staat de nieuwe kop er al,
dan blijft het bestand ongemoeid.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logger


def migreer(pad: Path) -> int:
    if not pad.exists():
        print(f"  {pad} bestaat niet, niets te doen")
        return 0
    with open(pad, newline="") as f:
        rijen = list(csv.reader(f))
    if not rijen:
        print(f"  {pad} is leeg, niets te doen")
        return 0

    kop = rijen[0]
    if kop == logger.ENS_KOP:
        print(f"  {pad} heeft de nieuwe kop al ({len(rijen) - 1} regels)")
        return 0
    if kop != logger.ENS_KOP[:len(kop)]:
        print(f"  {pad} heeft een onbekende kop: {','.join(kop)}")
        return 1

    breed = len(logger.ENS_KOP)
    uit = [logger.ENS_KOP]
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
    doel = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd() / "logs" / "ensemble_log.csv"
    sys.exit(migreer(doel))
