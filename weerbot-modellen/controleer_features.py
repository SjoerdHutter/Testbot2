#!/usr/bin/env python3
"""Controleert of de featurebestanden (uit_features) gaaf zijn geupload.
Draai vanuit de hoofdmap van de repo:  python weerbot-modellen/controleer_features.py
Let op: na een wekelijkse hertraining veranderen deze bestanden, dan meldt hij
terecht verschillen. Hij is bedoeld als eenmalige uploadcontrole; de workflow
draait hem daarom alleen op het aantal bestanden en de leesbaarheid.
"""
import csv, hashlib, sys
from pathlib import Path

hier = Path(__file__).resolve().parent
mf = hier / "MANIFEST-features.txt"
map_ = hier / "uit_features"
streng = "--streng" in sys.argv

if not map_.is_dir():
    print(f"FOUT: map {map_} bestaat niet. Is uit_features geupload?")
    sys.exit(1)

verwacht = {}
if mf.exists():
    for regel in mf.read_text().splitlines():
        if regel and not regel.startswith("#"):
            h, n, pad = regel.split(None, 2)
            verwacht[pad] = (h, int(n))

aanwezig = sorted(p.name for p in map_.glob("*.csv"))
mist = [p for p in verwacht if p not in aanwezig]
print(f"  gevonden : {len(aanwezig)} csv-bestanden"
      f"{f' (manifest verwacht {len(verwacht)})' if verwacht else ''}")
if mist:
    print(f"  ONTBREEKT: {len(mist)}")
    for p in mist[:10]:
        print(f"     {p}")
    sys.exit(1)

# leesbaarheid: kop en minstens een paar rijen, en de sleutelkolommen aanwezig
KOLOMMEN = {"datum", "mm_gem", "mm_spreiding", "doel", "rh_gem"}
stuk = []
for naam in aanwezig:
    p = map_ / naam
    try:
        with open(p, newline="") as f:
            r = csv.DictReader(f)
            ontbreekt = KOLOMMEN - set(r.fieldnames or [])
            n = sum(1 for _ in r)
        if ontbreekt:
            stuk.append(f"{naam}: kolommen ontbreken: {sorted(ontbreekt)}")
        elif n < 100:
            stuk.append(f"{naam}: maar {n} rijen")
    except Exception as e:
        stuk.append(f"{naam}: onleesbaar ({e})")

if streng and verwacht:
    for naam in aanwezig:
        b = (map_ / naam).read_bytes()
        h, n = verwacht.get(naam, ("", -1))
        if len(b) != n or hashlib.sha256(b).hexdigest()[:16] != h:
            stuk.append(f"{naam}: wijkt af van het manifest")

if stuk:
    print(f"  FOUT     : {len(stuk)}")
    for s in stuk[:15]:
        print(f"     {s}")
    sys.exit(1)
print("  Alle featurebestanden zijn leesbaar en compleet.")
