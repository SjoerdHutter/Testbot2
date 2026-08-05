#!/usr/bin/env python3
"""Controleert of de bestanden gaaf in de repository staan.

Twee soorten:
  vast        onveranderlijk -> grootte en sha256 moeten exact kloppen
  veranderlijk  wordt door de workflows herschreven -> alleen bestaan en
                leesbaarheid worden gecontroleerd (json moet parsen)

Draai vanuit de hoofdmap van de repo:
  python weerbot-modellen/controleer_upload.py
"""
import hashlib, json, sys
from pathlib import Path

VERANDERLIJK = {
    "weerbot-modellen/modellen/modellen.json",
    "weerbot-modellen/modellen/pooled_gbm.pkl",
    "weerbot-modellen/klim_vandaag.json",
    "weerbot-modellen/klim_features.csv",
    "weerbot-modellen/features_alle.csv",
}

hier = Path(__file__).resolve().parent
manifest = next((p for p in (hier / "MANIFEST.txt", hier.parent / "MANIFEST.txt",
                             Path("MANIFEST.txt")) if p.exists()), None)
if manifest is None:
    print("MANIFEST.txt niet gevonden; upload dat bestand naar de hoofdmap.")
    sys.exit(1)
wortel = manifest.parent

goed = los = 0
fout, mist = [], []
for regel in manifest.read_text().splitlines():
    if not regel or regel.startswith("#"):
        continue
    h, n, pad = regel.split(None, 2)
    p = wortel / pad
    if not p.exists():
        mist.append(pad); continue
    if pad in VERANDERLIJK:
        if p.suffix == ".json":
            try:
                json.load(open(p))
            except Exception as e:
                fout.append(f"{pad}: geen geldige json ({e})"); continue
        elif p.stat().st_size == 0:
            fout.append(f"{pad}: leeg bestand"); continue
        los += 1
        continue
    b = p.read_bytes()
    if len(b) != int(n):
        fout.append(f"{pad}: {len(b)} bytes, verwacht {n}")
    elif hashlib.sha256(b).hexdigest()[:16] != h:
        fout.append(f"{pad}: zelfde grootte maar andere inhoud")
    else:
        goed += 1

print(f"  exact in orde : {goed}")
print(f"  aanwezig      : {los}  (veranderlijk, wordt door de workflows herschreven)")
if mist:
    print(f"  ONTBREEKT     : {len(mist)}")
    for p in mist:
        print(f"     {p}")
if fout:
    print(f"  FOUT          : {len(fout)}")
    for p in fout:
        print(f"     {p}")
if not mist and not fout:
    print("\n  Alles klopt.")
sys.exit(1 if (mist or fout) else 0)
