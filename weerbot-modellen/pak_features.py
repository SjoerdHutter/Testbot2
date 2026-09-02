#!/usr/bin/env python3
"""Wisselt tussen één bundelbestand en de map uit_features.

  python3 pak_features.py uit   features_alle.csv  ->  uit_features/*.csv
  python3 pak_features.py in    uit_features/*.csv ->  features_alle.csv
  python3 pak_features.py check controleert het bundelbestand

In de repository staat alleen `features_alle.csv`. De workflow pakt hem uit op
de runner, laat de hertraining draaien en pakt hem daarna weer in. Zo hoeft er
nooit een map met losse bestanden geupload te worden.
"""
import csv, json, sys
from pathlib import Path

HIER   = Path(__file__).resolve().parent
BUNDEL = HIER / "features_alle.csv"
MAP    = HIER / "uit_features"
STEDEN = HIER / "steden.json"

def _lees_bundel():
    with open(BUNDEL, newline="") as f:
        r = csv.DictReader(f)
        kolommen = [k for k in (r.fieldnames or []) if k != "stad"]
        per = {}
        for rij in r:
            per.setdefault(rij["stad"], []).append(rij)
    return kolommen, per

def uitpakken():
    if not BUNDEL.exists():
        print(f"FOUT: {BUNDEL.name} ontbreekt."); sys.exit(1)
    kolommen, per = _lees_bundel()
    MAP.mkdir(exist_ok=True)
    for stad, rijen in per.items():
        with open(MAP / f"{stad}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=kolommen, extrasaction="ignore")
            w.writeheader()
            for rij in sorted(rijen, key=lambda x: x["datum"]):
                w.writerow(rij)
    print(f"  uitgepakt: {len(per)} steden, {sum(len(v) for v in per.values())} rijen")

def inpakken():
    bestanden = sorted(MAP.glob("*.csv"))
    if not bestanden:
        print("FOUT: uit_features is leeg."); sys.exit(1)
    kolommen, n = None, 0
    with open(BUNDEL, "w", newline="") as uit:
        w = None
        for p in bestanden:
            with open(p, newline="") as f:
                r = csv.DictReader(f)
                if w is None:
                    kolommen = list(r.fieldnames or [])
                    w = csv.DictWriter(uit, fieldnames=["stad"] + kolommen,
                                       extrasaction="ignore")
                    w.writeheader()
                for rij in r:
                    rij["stad"] = p.stem
                    w.writerow(rij); n += 1
    print(f"  ingepakt: {len(bestanden)} steden, {n} rijen -> {BUNDEL.name}")

def klimaatprofiel_fouten(per: dict) -> list:
    """Steden waarvan het seizoensprofiel niet bij hun halfrond past.

    Bestaat omdat het een keer echt gebeurd is: een setje stads-csv's bleek een
    permutatie van de verkeerde steden (kaapstad.csv droeg een noordelijk
    binnenlandklimaat, singapore.csv 4° in januari). De koppeling stad->rijen
    is aan geen enkele kolom te zien; het klimaat zelf is de enige controle die
    zo'n verwisseling betrapt.

    Getoetst wordt in welke maand het warmst is. Op het noordelijk halfrond
    hoort die piek in het zomerhalfjaar te liggen (maart tot en met oktober), op
    het zuidelijk halfrond in het winterhalfjaar van het noorden (september tot
    en met april). Een verwisselde stad valt daar hard doorheen: de kaapstad.csv
    van toen piekte in juli terwijl Kaapstad in januari hoort te pieken.

    Hiervoor stond er een vergelijking van juni-augustus met december-februari,
    met een marge van een graad. Die deugde niet dicht bij de evenaar. Manila
    (14,5° N) piekt in april op 35,4° en houdt tussen die twee blokken maar 0,9°
    over, want juni-augustus is daar het regenseizoen; de controle riep dus dat
    de data van een andere stad was terwijl het maandprofiel onmiskenbaar
    Manila is. De piekmaand heeft dat probleem niet: die ligt in april en dus
    gewoon in het noordelijke zomerhalfjaar.

    De tropengordel (|lat| < 10) doet nog steeds niet mee: daar is ook de
    piekmaand ruis, want het verschil tussen de maanden valt weg tegen de
    spreiding binnen een maand."""
    if not STEDEN.exists():
        return [f"{STEDEN.name} ontbreekt: klimaatprofiel niet te controleren"]
    lat_van = {s["key"]: s.get("lat") for s in json.load(open(STEDEN))}
    # Het halfjaar waarin de piek hoort te vallen, per halfrond.
    NOORD = {"03", "04", "05", "06", "07", "08", "09", "10"}
    ZUID = {"09", "10", "11", "12", "01", "02", "03", "04"}
    fouten = []
    for stad, rijen in sorted(per.items()):
        lat = lat_van.get(stad)
        if lat is None or abs(lat) < 10:
            continue
        per_maand: dict = {}
        for rij in rijen:
            try:
                v = float(rij["doel"])
            except (KeyError, TypeError, ValueError):
                continue
            per_maand.setdefault(rij["datum"][5:7], []).append(v)
        # genoeg maanden met genoeg dagen, anders is de piek niet te bepalen
        bruikbaar = {m: sum(v) / len(v) for m, v in per_maand.items() if len(v) >= 10}
        if len(bruikbaar) < 8:
            continue
        piek = max(bruikbaar, key=lambda m: bruikbaar[m])
        hoort = NOORD if lat > 0 else ZUID
        if piek not in hoort:
            halfrond = "noordelijk" if lat > 0 else "zuidelijk"
            fouten.append(
                f"{stad}: warmste maand is {piek} ({bruikbaar[piek]:.1f}°), en dat "
                f"past niet bij het {halfrond} halfrond (breedtegraad {lat}) "
                f"— data van een andere stad?")
    return fouten


def controle():
    if not BUNDEL.exists():
        print(f"FOUT: {BUNDEL.name} staat niet in de repository."); sys.exit(1)
    try:
        kolommen, per = _lees_bundel()
    except Exception as e:
        print(f"FOUT: {BUNDEL.name} is onleesbaar ({e})."); sys.exit(1)
    nodig = {"datum", "mm_gem", "mm_spreiding", "doel", "rh_gem"}
    mist = nodig - set(kolommen)
    n = sum(len(v) for v in per.values())
    print(f"  {len(per)} steden · {n} rijen · {len(kolommen)} kolommen")
    fouten = []
    if mist:
        fouten.append(f"kolommen ontbreken: {sorted(mist)}")
    if len(per) < 45:
        fouten.append(f"maar {len(per)} steden gevonden, verwacht 51")
    dun = [s for s, v in per.items() if len(v) < 100]
    if dun:
        fouten.append(f"te weinig rijen voor: {dun[:8]}")
    fouten += klimaatprofiel_fouten(per)
    if fouten:
        print("  FOUT:")
        for f in fouten:
            print(f"     {f}")
        sys.exit(1)
    print("  Bundelbestand is in orde.")

if __name__ == "__main__":
    opdracht = (sys.argv[1:] or ["check"])[0]
    {"uit": uitpakken, "in": inpakken, "check": controle}[opdracht]()
