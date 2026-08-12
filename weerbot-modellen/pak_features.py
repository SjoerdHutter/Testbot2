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
    zo'n verwisseling betrapt. Vergeleken wordt de gemiddelde doeltemperatuur
    van juni-augustus met die van december-februari: op het noordelijk halfrond
    hoort de eerste ruim warmer te zijn, op het zuidelijk halfrond de tweede.
    De tropengordel (|lat| < 10) heeft geen bruikbaar seizoen en doet niet mee."""
    if not STEDEN.exists():
        return [f"{STEDEN.name} ontbreekt: klimaatprofiel niet te controleren"]
    lat_van = {s["key"]: s.get("lat") for s in json.load(open(STEDEN))}
    fouten = []
    for stad, rijen in sorted(per.items()):
        lat = lat_van.get(stad)
        if lat is None or abs(lat) < 10:
            continue
        jja, djf = [], []
        for rij in rijen:
            try:
                v = float(rij["doel"])
            except (KeyError, TypeError, ValueError):
                continue
            m = rij["datum"][5:7]
            if m in ("06", "07", "08"):
                jja.append(v)
            elif m in ("12", "01", "02"):
                djf.append(v)
        if len(jja) < 30 or len(djf) < 30:
            continue
        zomer = jja if lat > 0 else djf
        winter = djf if lat > 0 else jja
        z = sum(zomer) / len(zomer)
        w = sum(winter) / len(winter)
        if z < w + 1.0:
            fouten.append(f"{stad}: zomer {z:.1f}° tegen winter {w:.1f}° past "
                          f"niet bij breedtegraad {lat} — data van een andere stad?")
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
