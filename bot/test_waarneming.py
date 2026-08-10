#!/usr/bin/env python3
"""Zelftest voor de intraday-conditionering.

  python3 bot/test_waarneming.py

Controleert zeven dingen:

  onveranderd  Zonder waarneming rekent onze_kansen exact zoals daarvoor. Dat is
               de belangrijkste toets van allemaal: de conditionering mag lead 1
               en 2, en elke stad zonder meting, met geen cijfer raken.
  behoudend    De restfactor ligt nooit onder de marktcurve waar hij op rust.
               Onze spreiding over de resterende uren is daarmee per constructie
               nooit krapper dan die van de markt over de hele dag.
  verloop      De restfactor is dalend over de dag en blijft tussen 0 en 1.
  onmogelijk   Een vak dat helemaal onder de al gemeten waarde ligt krijgt kans
               nul, en de kansen tellen nog steeds op tot 1.
  puntmassa    Het vak waar de meting in valt krijgt er precies de kans
               "de piek is al geweest" bij, en bij w -> 0 komt alle massa daar
               terecht.
  spiegel      Bij de laagstereeks staat alles op zijn kop: onmogelijk is dan
               boven de meting in plaats van eronder.
  iem          De komma-uitvoer van IEM wordt goed ontleed, inclusief de
               ontbrekende waarden, en voor_stad laat een meting van gisteren of
               van lead 1 niet door.

Alles draait offline; er gaat geen verzoek uit.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signalen as S     # noqa: E402
import waarneming as W   # noqa: E402
import jslezer           # noqa: E402

VAKKEN_F = [{"lo": None, "hi": 81, "eenheid": "°F"},
            {"lo": 82, "hi": 83, "eenheid": "°F"},
            {"lo": 84, "hi": 85, "eenheid": "°F"},
            {"lo": 86, "hi": 87, "eenheid": "°F"},
            {"lo": 88, "hi": None, "eenheid": "°F"}]
DAG = {"verwachting": 84.0, "p10": 80.0, "p90": 88.0}


def kaal_normaal(vakken, mu, sigma):
    """De onvoorwaardelijke kans per vak, los uitgeschreven. Dit is de definitie
    waar onze_kansen zonder waarneming aan moet voldoen; hem hier herhalen is
    het hele punt van de toets."""
    uit = []
    for b in vakken:
        boven = 1 if b["hi"] is None else S.phi((b["hi"] + 0.5 - mu) / sigma)
        onder = 0 if b["lo"] is None else S.phi((b["lo"] - 0.5 - mu) / sigma)
        uit.append(max(0, min(1, boven - onder)))
    return uit


def test_onveranderd() -> bool:
    sigma = (DAG["p90"] - DAG["p10"]) / (2 * 1.2815515655446004)
    verwacht = kaal_normaal(VAKKEN_F, DAG["verwachting"], sigma)
    grootste = 0.0
    for wn in (None, {}, {"m": None, "uur": 15, "soort": "max"}):
        gekregen = S.onze_kansen(VAKKEN_F, DAG, "°F", "°F", wn)
        for a, b in zip(verwacht, gekregen):
            grootste = max(grootste, abs(a - b))
    ok = grootste == 0.0
    print(f"  onveranderd  {'ok' if ok else 'MISLUKT'}: zonder waarneming "
          f"grootste afwijking {grootste:.2e}")
    return ok


def test_behoudend() -> bool:
    """w = 1 - demping * (1 - ruw) mag nooit onder ruw zakken. Anders zouden we
    over de resterende uren scherper zijn dan de markt over de hele dag, en dan
    telt de afkapping op de meting dubbel."""
    slechtste, waar = 0.0, None
    for soort, tabel in (("max", W.W_REST_MAX), ("min", W.W_REST_MIN)):
        for u in range(24):
            marge = W.restfactor(u, soort) - tabel[u]
            if marge < slechtste:
                slechtste, waar = marge, (soort, u)
    ok = slechtste >= 0
    print(f"  behoudend    {'ok' if ok else 'MISLUKT'}: kleinste marge boven de "
          f"marktcurve {slechtste:+.4f}" + (f" bij {waar}" if waar else ""))
    return ok


def test_verloop() -> bool:
    fouten = []
    for soort in ("max", "min"):
        vorige = None
        for stap in range(0, 231):
            u = stap / 10.0
            w = W.restfactor(u, soort)
            if not (0 < w <= 1):
                fouten.append(f"{soort} {u}u buiten bereik: {w}")
            if vorige is not None and w > vorige + 1e-12:
                fouten.append(f"{soort} stijgt bij {u}u: {vorige} -> {w}")
            vorige = w
    # buiten bereik en zonder uur mag niets omvallen
    for u in (None, -5, 99):
        w = W.restfactor(u, "max")
        if not (0 < w <= 1):
            fouten.append(f"uur={u} geeft {w}")
    ok = not fouten
    print(f"  verloop      {'ok' if ok else 'MISLUKT'}: dalend en begrensd"
          + ("" if ok else "; " + "; ".join(fouten[:3])))
    return ok


def test_onmogelijk() -> bool:
    """Om vier uur 's middags staat er al 85 op de meter. Alles onder 83,5 is dan
    geen kwestie van kans meer."""
    wn = {"m": 85.0, "uur": 16, "soort": "max"}
    k = S.onze_kansen(VAKKEN_F, DAG, "°F", "°F", wn)
    onder = k[0] + k[1]                      # "81 of lager" en "82-83"
    som = sum(k)
    ok = onder == 0.0 and abs(som - 1) < 1e-9
    print(f"  onmogelijk   {'ok' if ok else 'MISLUKT'}: kans onder de meting "
          f"{onder:.2e}, som {som:.6f}")
    return ok


def test_puntmassa() -> bool:
    """Het vak waar de meting in valt krijgt de kans dat de piek al geweest is.
    Bij een verdwijnende restfactor is dat het hele verhaal."""
    m = 85.0
    fouten = []

    # 1. de puntmassa zelf: F(85 - 0,5) = 0, dus het vak 84-85 krijgt alles tot
    #    85,5 mee, inclusief Phi((m - mu_R)/sig_R).
    wn = {"m": m, "uur": 14, "soort": "max"}
    k = S.onze_kansen(VAKKEN_F, DAG, "°F", "°F", wn)
    sigma = (DAG["p90"] - DAG["p10"]) / (2 * 1.2815515655446004)
    mu_r, sig_r = W.conditioneer(DAG["verwachting"], sigma, m, 14, "max")
    verwacht = S.phi((85 + 0.5 - mu_r) / sig_r)
    if abs(k[2] - verwacht) > 1e-12:
        fouten.append(f"vak met de meting {k[2]:.6f} != {verwacht:.6f}")

    # 2. laat op de avond hoort vrijwel alles op dat ene vak te staan
    laat = S.onze_kansen(VAKKEN_F, DAG, "°F", "°F",
                         {"m": m, "uur": 23, "soort": "max"})
    if not (laat[2] > 0.90):
        fouten.append(f"om 23u staat er maar {laat[2]:.3f} op het vak van de meting")

    # 3. een meting die ver boven de verwachting ligt schuift alles mee omhoog
    hoog = S.onze_kansen(VAKKEN_F, DAG, "°F", "°F",
                         {"m": 89.0, "uur": 16, "soort": "max"})
    if sum(hoog[:3]) != 0.0 or hoog[4] < 0.5:
        fouten.append(f"meting 89 geeft {[round(x, 3) for x in hoog]}")

    ok = not fouten
    print(f"  puntmassa    {'ok' if ok else 'MISLUKT'}: "
          + ("de piek-is-geweest massa komt op het goede vak"
             if ok else "; ".join(fouten)))
    return ok


def test_spiegel() -> bool:
    """Laagstereeks: T = min(m, R), dus boven de meting is het onmogelijk."""
    wn = {"m": 70.0, "uur": 9, "soort": "min"}
    dag = {"verwachting": 72.0, "p10": 68.0, "p90": 76.0}
    vakken = [{"lo": None, "hi": 67}, {"lo": 68, "hi": 69}, {"lo": 70, "hi": 71},
              {"lo": 72, "hi": 73}, {"lo": 74, "hi": None}]
    k = S.onze_kansen(vakken, dag, "°F", "°F", wn)
    boven = k[3] + k[4]
    som = sum(k)
    ok = boven == 0.0 and abs(som - 1) < 1e-9 and k[2] > 0
    print(f"  spiegel      {'ok' if ok else 'MISLUKT'}: kans boven de gemeten "
          f"minimum {boven:.2e}, som {som:.6f}")
    return ok


IEM_VOORBEELD = """\
#DEBUG: request
station,valid,tmpf
LGA,2026-08-10 06:51,71.60
LGA,2026-08-10 12:51,M
LGA,2026-08-10 13:51,84.20
LGA,2026-08-10 14:51,86.00
LGA,2026-08-09 15:51,90.00
ORD,2026-08-10 13:51,79.00
"""


def test_iem() -> bool:
    fouten = []
    uit = W.ontleed_iem(IEM_VOORBEELD, ["LGA", "ORD"])
    lga = uit.get("LGA", {}).get("2026-08-10")
    if not lga:
        fouten.append("LGA van vandaag ontbreekt")
    else:
        if lga["maxf"] != 86.0:
            fouten.append(f"max {lga['maxf']} != 86.0")
        if lga["minf"] != 71.6:
            fouten.append(f"min {lga['minf']} != 71.6")
        if lga["n"] != 3:
            fouten.append(f"n {lga['n']} != 3 (de M hoort niet mee te tellen)")
        if abs(lga["laatste_uur"] - 14.85) > 0.01:
            fouten.append(f"laatste_uur {lga['laatste_uur']}")
    if "2026-08-09" not in uit.get("LGA", {}):
        fouten.append("gisteren van LGA ontbreekt")
    if uit.get("ORD", {}).get("2026-08-10", {}).get("maxf") != 79.0:
        fouten.append("ORD niet los gehouden van LGA")
    # een station dat niet gevraagd is hoort er niet in te sluipen
    if W.ontleed_iem(IEM_VOORBEELD, ["LGA"]).get("ORD"):
        fouten.append("ORD komt mee terwijl er alleen om LGA gevraagd is")

    # voor_stad: alleen lead 0, alleen dezelfde dag, en de restfactor rekent
    # met het laatste meetmoment en niet met de klok.
    wns = {"NYC": {"max": 86.0, "min": 71.6, "n": 3, "laatste_uur": 14.85,
                   "uur": 20.0, "datum": "2026-08-10", "station": "LGA"}}
    if W.voor_stad(wns, "NYC", "2026-08-10", 1, "max") is not None:
        fouten.append("lead 1 krijgt een waarneming mee")
    if W.voor_stad(wns, "NYC", "2026-08-11", 0, "max") is not None:
        fouten.append("een andere doeldag krijgt de meting van vandaag mee")
    if W.voor_stad(wns, "AMS", "2026-08-10", 0, "max") is not None:
        fouten.append("een stad zonder meting krijgt er een")
    w = W.voor_stad(wns, "NYC", "2026-08-10", 0, "max")
    if not w or w["m"] != 86.0:
        fouten.append("de meting van NYC komt niet door")
    elif w["uur"] != 14.85:
        fouten.append(f"restfactor rekent met {w['uur']} in plaats van 14.85: "
                      "een uitgevallen station knijpt de band dan ten onrechte dicht")
    wmin = W.voor_stad(wns, "NYC", "2026-08-10", 0, "min")
    if not wmin or wmin["m"] != 71.6:
        fouten.append("het minimum komt niet door")

    ok = not fouten
    print(f"  iem          {'ok' if ok else 'MISLUKT'}: ontleden en poortjes"
          + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def main() -> int:
    print("\n  Zelftest intraday-conditionering\n")
    goed = all([test_onveranderd(), test_behoudend(), test_verloop(),
                test_onmogelijk(), test_puntmassa(), test_spiegel(), test_iem()])
    print("\n  " + ("Alles in orde.\n" if goed else "ER GING IETS MIS.\n"))
    return 0 if goed else 1


if __name__ == "__main__":
    sys.exit(main())
