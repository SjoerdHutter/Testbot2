#!/usr/bin/env python3
"""Eenmalige migratie van logs/signalen.csv naar de bredere kop.

Tot en met de intraday-conditionering had het signalenlog twintig kolommen en
eindigde het op `strat_a_signaal`. signalen.py schrijft er nu vijf kolommen
achter: wat er op het moment van loggen al gemeten was, de restfactor die
daaruit volgde, en de kale kans van vóór de conditionering.

Zonder migratie krijgt csv.DictReader in de analyses rijen van twee lengtes: de
oude regels missen de nieuwe sleutels en leveren None op wisselende plekken.
Deze migratie zet de nieuwe kop bovenaan en vult de oude regels aan met lege
velden, zodat het bestand rechthoekig is.

De oude regels krijgen expliciet géén waarde in `model_kans_kaal`, ook al is de
kans in `model_kans` daar per definitie de kale kans — er was toen immers geen
waarneming. Dat is met opzet: leeg betekent "deze regel is van voor de
conditionering", en dat onderscheid moet blijven staan, anders lijkt het later
alsof er kaal én geconditioneerd gemeten is terwijl er maar één getal was.

Draai vanuit de hoofdmap van de repo:

    python3 bot/migratie_signalen_log.py            logs/signalen.csv
    python3 bot/migratie_signalen_log.py <pad>      een ander bestand

Het is veilig om de migratie nog eens te draaien: staat de nieuwe kop er al,
dan blijft het bestand ongemoeid.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import signalen


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
    if kop == signalen.KOP:
        print(f"  {pad} heeft de nieuwe kop al ({len(rijen) - 1} regels)")
        return 0
    if kop != signalen.KOP[:len(kop)]:
        print(f"  {pad} heeft een onbekende kop: {','.join(kop)}")
        return 1

    breed = len(signalen.KOP)
    uit = [signalen.KOP]
    for rij in rijen[1:]:
        if not rij:
            continue
        uit.append(rij + [""] * (breed - len(rij)))

    tijdelijk = pad.with_suffix(pad.suffix + ".nieuw")
    with open(tijdelijk, "w", newline="") as f:
        csv.writer(f).writerows(uit)
    tijdelijk.replace(pad)
    print(f"  {pad}: {len(uit) - 1} regels aangevuld van {len(kop)} naar "
          f"{breed} kolommen")
    return 0


if __name__ == "__main__":
    doel = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path.cwd() / "logs" / "signalen.csv"
    sys.exit(migreer(doel))
