#!/usr/bin/env python3
"""Eenmalige migratie van logs/portfolio_history.csv naar de bredere kop.

De eerste dagen had de reeks negen kolommen:

    gelogd_utc,key,doel_datum,bracket_label,adj_mean_now,model_prob_now,
    current_bid,city_bias_used,light

portfolio.py schrijft daar peak_hour achter. Zonder migratie krijgt een lezer
regels van twee lengtes: de oude missen de nieuwe sleutel, en een DictReader
zet dan stilletjes None neer waar een getal hoort. Deze migratie plaatst de
nieuwe kop en vult de oude regels aan met een leeg veld, zodat het bestand
rechthoekig is. Bestaande waarden blijven exact zoals ze waren; er wordt niets
herberekend en het piekuur van toen is niet meer te achterhalen.

peak_hour staat achteraan en niet ertussen, zodat de bestaande kolommen hun
plek houden. Wie op index leest merkt er niets van.

Draai vanuit de hoofdmap van de repo:

    python3 bot/migratie_portfolio_history.py            logs/portfolio_history.csv
    python3 bot/migratie_portfolio_history.py <pad>      een ander bestand

Het is veilig om de migratie nog eens te draaien: staat de nieuwe kop er al,
dan gebeurt er niets.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import portfolio as P   # noqa: E402


def migreer(pad: Path) -> int:
    if not pad.exists():
        print(f"  {pad} bestaat niet; niets te doen")
        return 0

    rijen = list(csv.reader(open(pad, newline="")))
    if not rijen:
        print(f"  {pad} is leeg; niets te doen")
        return 0

    kop, data = rijen[0], rijen[1:]
    if kop == P.HIST_KOP:
        print(f"  {pad.name}: kop is al bij ({len(data)} regels)")
        return 0

    breed = len(P.HIST_KOP)
    # Alleen een kop die letterlijk het begin van HIST_KOP is mag verbreed
    # worden. Een even brede of bredere kop met andere namen of een andere
    # volgorde is geen oude versie van dit bestand maar iets anders; de kop
    # vervangen zou dan elke kolom stilzwijgend een verkeerd etiket geven.
    if kop != P.HIST_KOP[:len(kop)]:
        print(f"  {pad.name}: de kop ({len(kop)} kolommen) is geen begin van de "
              f"{breed} kolommen die portfolio.py schrijft. Niets aangeraakt.")
        return 1

    aangevuld = 0
    uit = []
    for r in data:
        if len(r) < breed:
            r = r + [""] * (breed - len(r))
            aangevuld += 1
        uit.append(r[:breed])

    with open(pad, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(P.HIST_KOP)
        w.writerows(uit)
    print(f"  {pad.name}: kop verbreed van {len(kop)} naar {breed} kolommen, "
          f"{aangevuld} van de {len(data)} regels aangevuld")
    return 0


def main(argv: list) -> int:
    pad = Path(argv[0]) if argv else (Path.cwd() / "logs" / "portfolio_history.csv")
    return migreer(pad)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
