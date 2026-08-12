#!/usr/bin/env python3
"""De restfactorcurve opnieuw uitrekenen uit logs/signalen.csv.

W_REST_MAX in weerbot-modellen/polymarkt.js zegt hoeveel van de onzekerheid over
het dagmaximum er om een bepaald lokaal uur nog over is. Dit script is waar dat
getal vandaan komt, zodat het narekenbaar is en niet als aanname in de code
blijft staan.

Hoe het werkt
-------------
Voor elke lead-0 reeks in het signalenlog staat per logmoment de marktprijs per
temperatuurvak. Die prijzen tellen op tot ongeveer 1 en vormen dus een verdeling
over de vakken. De entropie daarvan is één getal voor "hoe onzeker is de markt
nu", en is terug te rekenen naar de sigma van een normale verdeling die over
dezelfde vakbreedte uitgesmeerd zou zijn. Uitgezet tegen het lokale uur van het
logmoment geeft dat de curve waarmee de onzekerheid over de dag instort.

Sigma komt in vakbreedtes te staan, niet in graden: een Amerikaanse markt heeft
vakken van 2 °F en een Aziatische van 1 °C, en zonder die normalisatie zou de
curve vooral meten welke steden er die dag toevallig in het logboek stonden.

Wat je moet weten voor je de uitkomst overneemt
-----------------------------------------------
* **De late uren zijn gecensureerd.** Een Polymarket-prijs loopt niet verder dan
  0,9995, dus de entropie van een afgerekende reeks komt niet onder een bodem
  die niets met het weer te maken heeft. Vanaf het uur waarop de gemeten sigma
  op die bodem blijft plakken is de curve geen meting meer. Het script markeert
  die uren; in polymarkt.js zijn ze ingevuld door de daling ervoor door te
  trekken.
* **Dit is de onzekerheid van de markt, niet de onze.** De markt is in dit
  venster domweg een beter model, en heeft de al gemeten temperatuur al in zijn
  prijzen zitten. Daarom gaat er in polymarkt.js een demping overheen. Zie de
  kop van bot/waarneming.py.
* **Het aantal reeksen per uur is klein.** Er wordt vier keer per dag gelogd, dus
  een uur met minder dan een stuk of tien reeksen zegt weinig. Het script drukt
  n erbij af; kijk daarnaar voor je iets aanpast.

Gebruik (vanuit de hoofdmap van de repo):

    python3 bot/kalibreer_restfactor.py            de curve, met n per uur
    python3 bot/kalibreer_restfactor.py --js       als tabel voor polymarkt.js
    python3 bot/kalibreer_restfactor.py --soort min

De uitkomst wordt niet automatisch weggeschreven. De tabel in polymarkt.js is
met de hand bijgewerkt en hoort dat te blijven: het is een curve waar een oordeel
over censurering in zit, en dat oordeel hoort niet in een cronjob.
"""
import collections
import csv
import math
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weer
import waarneming as W

MIN_VAKKEN = 5           # minder vakken is geen bruikbare verdeling
SOM_MARGE = (0.8, 1.25)  # prijzen die hier niet in optellen zijn onbruikbaar
OCHTEND = range(0, 11)   # de uren waarop de curve op 1 genormeerd wordt


def entropie_van_sigma(sigma: float, stap: float) -> float:
    """De entropie van een normale verdeling die over vakken van `stap` graden
    verdeeld is. Monotoon in sigma, dus omkeerbaar."""
    ps = []
    for i in range(-60, 61):
        lo, hi = (i - 0.5) * stap, (i + 0.5) * stap
        p = 0.5 * (math.erf(hi / (sigma * math.sqrt(2))) -
                   math.erf(lo / (sigma * math.sqrt(2))))
        if p > 1e-12:
            ps.append(p)
    som = sum(ps)
    return -sum((p / som) * math.log(p / som) for p in ps)


def sigma_van_entropie(e: float, stap: float) -> float:
    """Terug van entropie naar sigma, met bisectie. Zestig stappen is ruim."""
    lo, hi = 0.01, 20.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if entropie_van_sigma(mid, stap) < e:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def lees(pad: Path, soort: str) -> dict:
    """Per lokaal uur de lijst met marktsigma's, in vakbreedtes."""
    tz = {s["key"]: s["tz"] for s in weer.STEDEN}
    groepen = collections.defaultdict(list)
    with open(pad, newline="") as f:
        for r in csv.DictReader(f):
            if r["lead"] != "0" or r["soort"] != soort or not r["markt_prijs"]:
                continue
            sleutel = (r["key"], r["doel_datum"], r["gelogd_utc"], r["eenheid"])
            groepen[sleutel].append(float(r["markt_prijs"]))

    per_uur = collections.defaultdict(list)
    for (key, dag, t, eenheid), prijzen in groepen.items():
        som = sum(prijzen)
        if len(prijzen) < MIN_VAKKEN or not SOM_MARGE[0] < som < SOM_MARGE[1]:
            continue
        if key not in tz:
            continue
        lokaal = datetime.fromisoformat(t).astimezone(ZoneInfo(tz[key]))
        # Alleen logmomenten op de doeldag zelf: een reeks die 's avonds voor de
        # dag ervoor gelogd is heeft een lokaal uur dat niets betekent.
        if lokaal.date().isoformat() != dag:
            continue
        genormeerd = [p / som for p in prijzen]
        ent = -sum(p * math.log(p) for p in genormeerd if p > 0)
        stap = 2.0 if eenheid == "°F" else 1.0
        per_uur[lokaal.hour].append(sigma_van_entropie(ent, stap) / stap)
    return per_uur


def curve(per_uur: dict):
    """De mediane sigma per uur, en de verhouding tot de ochtend."""
    mediaan = {}
    for u, waarden in per_uur.items():
        v = sorted(waarden)
        mediaan[u] = v[len(v) // 2]
    basis = [mediaan[u] for u in OCHTEND if u in mediaan]
    if not basis:
        return mediaan, None, {}
    b = sum(basis) / len(basis)
    return mediaan, b, {u: min(1.0, s / b) for u, s in mediaan.items()}


def main(argv: list) -> int:
    soort = "max"
    for i, a in enumerate(argv):
        if a == "--soort" and i + 1 < len(argv):
            soort = argv[i + 1]
    pad = Path.cwd() / "logs" / "signalen.csv"
    if not pad.exists():
        print(f"  {pad} bestaat niet")
        return 1

    per_uur = lees(pad, soort)
    if not per_uur:
        print(f"  geen bruikbare {soort}-reeksen in {pad}")
        return 1
    mediaan, basis, verhouding = curve(per_uur)
    n_totaal = sum(len(v) for v in per_uur.values())

    # De censuurbodem: het laagste sigma dat de prijsresolutie nog toelaat. Uren
    # die daar tegenaan zitten zijn geen meting meer.
    laagste = min(mediaan.values())
    gecensureerd = {u for u, s in mediaan.items() if s <= laagste * 1.05}

    if "--js" in argv:
        print(f"  /* {n_totaal} reeksen uit logs/signalen.csv, "
              f"ochtendbasis {basis:.3f} vakbreedtes.")
        print("     De uren met een * waren gecensureerd door de prijsbodem en "
              "zijn hier niet\n     ingevuld; zie de kop van dit blok in "
              "polymarkt.js. */")
        regels = []
        for u in range(24):
            w = verhouding.get(u)
            regels.append(f"{u}: " + ("%.2f" % w if w is not None else "????")
                          + ("" if u not in gecensureerd else " /*censuur*/"))
        for i in range(0, 24, 6):
            print("    " + ", ".join(regels[i:i + 6]) + ("," if i < 18 else ""))
        return 0

    print(f"\n  Restfactor uit {n_totaal} {soort}-reeksen op lead 0 "
          f"({len(per_uur)} uren bezet)")
    print(f"  Ochtendbasis (uur {OCHTEND.start}-{OCHTEND.stop - 1}): "
          f"{basis:.3f} vakbreedtes\n")
    print("   uur |   n  | sigma markt | verhouding | in polymarkt.js")
    for u in range(24):
        if u not in mediaan:
            print(f"    {u:2d} |    - |           - |          - |"
                  f"     {W.W_REST_MAX[u] if soort == 'max' else W.W_REST_MIN[u]:.2f}")
            continue
        merk = " *gecensureerd" if u in gecensureerd else ""
        staat = W.W_REST_MAX[u] if soort == "max" else W.W_REST_MIN[u]
        print(f"    {u:2d} | {len(per_uur[u]):4d} |       {mediaan[u]:.3f} |"
              f"      {verhouding[u]:.3f} |     {staat:.2f}{merk}")
    print("\n  * de prijsbodem van 0,9995 maakt deze uren onmeetbaar; in "
          "polymarkt.js zijn\n    ze ingevuld door de daling ervoor door te "
          "trekken.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
