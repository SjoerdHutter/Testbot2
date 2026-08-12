#!/usr/bin/env python3
"""Zelftest voor de inzetregel en de risicoplafonds.

  python3 bot/test_inzet.py

Controleert zes dingen:

  kelly      De Kelly-fractie klopt met de handberekening, staat op nul zonder
             voordeel en wordt negatief als de weddenschap de verkeerde kant op
             staat.
  mengen     Bij lambda 0 komt de marktprijs eruit, bij 1 de modelkans, en
             daartussen loopt het monotoon. Het mengen gebeurt op de
             logit-schaal.
  plafond    Geen enkele positie komt boven MAX_PER_POSITIE, en een inzet onder
             de ondergrens wordt geweigerd met een reden.
  verdelen   De blootstelling en het aantal posities worden gerespecteerd, en de
             standen met het meeste voordeel komen eerst.
  dagstop    Boven het dagverlies gaat alles op nul, en een onbekend dagverlies
             levert een waarschuwing op in plaats van stilte.
  meten      De mengfactor wordt op een verzonnen logboek teruggevonden: bij een
             logboek waarin het model altijd gelijk heeft komt lambda hoog uit,
             bij ruis laag.

Alles draait offline.
"""
import csv
import math
import random
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import inzet as I    # noqa: E402
import signalen as S  # noqa: E402


def test_kelly() -> bool:
    fouten = []
    # 60% kans op een contract van 40 cent: (0,6 - 0,4) / 0,6 = 1/3
    if abs(I.kelly(0.60, 0.40) - (0.2 / 0.6)) > 1e-12:
        fouten.append(f"0,60 tegen 0,40 geeft {I.kelly(0.60, 0.40)}")
    if I.kelly(0.40, 0.40) != 0.0:
        fouten.append("gelijke kans en prijs hoort nul te geven")
    if I.kelly(0.30, 0.40) >= 0:
        fouten.append("een slechtere kans dan de prijs hoort negatief te zijn")
    for prijs in (None, 0.0, 1.0, -0.2, 1.5):
        if I.kelly(0.6, prijs) != 0.0:
            fouten.append(f"prijs {prijs} hoort nul te geven")
    if I.kelly(None, 0.4) != 0.0:
        fouten.append("zonder kans hoort nul te komen")
    ok = not fouten
    print(f"  kelly      {'ok' if ok else 'MISLUKT'}: de fractie klopt"
          + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def test_mengen() -> bool:
    fouten = []
    model, prijs = 0.80, 0.30
    if abs(I.gemengd(model, prijs, 0.0) - prijs) > 1e-9:
        fouten.append("lambda 0 geeft niet de marktprijs")
    if abs(I.gemengd(model, prijs, 1.0) - model) > 1e-9:
        fouten.append("lambda 1 geeft niet de modelkans")
    vorige = None
    for stap in range(21):
        p = I.gemengd(model, prijs, stap / 20)
        if vorige is not None and p < vorige - 1e-12:
            fouten.append("het mengen loopt niet monotoon")
            break
        vorige = p
    # op de logit-schaal, dus het midden ligt niet op het rekenkundig gemiddelde
    midden = I.gemengd(model, prijs, 0.5)
    if abs(midden - (model + prijs) / 2) < 1e-6:
        fouten.append("het mengen gebeurt lineair in plaats van op de logit")
    # zonder prijs valt er niets te mengen
    if I.gemengd(0.7, None, 0.5) != 0.7:
        fouten.append("zonder prijs hoort de modelkans te blijven staan")
    if I.gemengd(None, 0.4, 0.5) != 0.4:
        fouten.append("zonder modelkans hoort de prijs te blijven staan")
    ok = not fouten
    print(f"  mengen     {'ok' if ok else 'MISLUKT'}: logit-meng tussen markt en "
          "model" + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def test_plafond() -> bool:
    fouten = []
    # een enorme edge hoort tegen het plafond per positie aan te lopen
    z = I.inzet(0.99, 0.02, 10000.0)
    if z["fractie"] > I.MAX_PER_POSITIE + 1e-9:
        fouten.append(f"fractie {z['fractie']} boven het plafond")
    if "afgetopt" not in z["reden"]:
        fouten.append(f"het aftoppen wordt niet gemeld: {z['reden']!r}")
    if abs(z["bedrag"] - I.MAX_PER_POSITIE * 10000.0) > 0.01:
        fouten.append(f"bedrag {z['bedrag']} klopt niet met het plafond")
    # geen voordeel: nul, met een reden die dat zegt
    z2 = I.inzet(0.30, 0.40, 10000.0)
    if z2["bedrag"] != 0.0 or "voordeel" not in z2["reden"]:
        fouten.append(f"zonder voordeel: {z2}")
    # een klein bankroll levert een bedrag onder de ondergrens op
    z3 = I.inzet(0.55, 0.50, 20.0)
    if z3["bedrag"] != 0.0 or "ondergrens" not in z3["reden"]:
        fouten.append(f"onder de ondergrens: {z3}")
    # zonder prijs een eigen reden, niet stilzwijgend nul
    z4 = I.inzet(0.8, None, 1000.0)
    if z4["bedrag"] != 0.0 or "marktprijs" not in z4["reden"]:
        fouten.append(f"zonder prijs: {z4}")
    ok = not fouten
    print(f"  plafond    {'ok' if ok else 'MISLUKT'}: per positie afgetopt met "
          "reden" + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def test_verdelen() -> bool:
    fouten = []
    bankroll = 10000.0
    # tien standen met flink voordeel: samen zouden ze over de blootstelling gaan
    kandidaten = [{"naam": f"k{i}", "win_kans": 0.90, "prijs": 0.10}
                  for i in range(30)]
    plan = I.verdeel(kandidaten, bankroll, dagverlies=0.0)
    if plan["toegewezen"] > I.MAX_BLOOTSTELLING * bankroll + 0.01:
        fouten.append(f"toegewezen {plan['toegewezen']} boven de blootstelling")
    if len(plan["posities"]) > I.MAX_POSITIES:
        fouten.append(f"{len(plan['posities'])} posities boven het plafond")
    if not any("blootstelling" in p["inzet"]["reden"] or
               "posities open" in p["inzet"]["reden"]
               for p in plan["posities"] + plan["geweigerd"]):
        fouten.append("er wordt nergens gemeld dat een plafond bond")

    # al bijna vol: er mag vrijwel niets meer bij
    plan2 = I.verdeel(kandidaten, bankroll,
                      open_blootstelling=I.MAX_BLOOTSTELLING * bankroll,
                      n_open=0, dagverlies=0.0)
    if plan2["toegewezen"] > 0.01:
        fouten.append(f"met een volle blootstelling komt er nog {plan2['toegewezen']} bij")
    plan3 = I.verdeel(kandidaten, bankroll, n_open=I.MAX_POSITIES, dagverlies=0.0)
    if plan3["posities"]:
        fouten.append("met het maximum aantal posities komt er nog wat bij")

    # volgorde: het meeste voordeel eerst
    gemengd_kandidaten = [{"naam": "klein", "win_kans": 0.52, "prijs": 0.50},
                          {"naam": "groot", "win_kans": 0.95, "prijs": 0.20},
                          {"naam": "midden", "win_kans": 0.70, "prijs": 0.50}]
    plan4 = I.verdeel(gemengd_kandidaten, bankroll, dagverlies=0.0)
    namen = [p["naam"] for p in plan4["posities"]]
    if namen and namen[0] != "groot":
        fouten.append(f"de volgorde begint met {namen[0]} in plaats van groot")
    ok = not fouten
    print(f"  verdelen   {'ok' if ok else 'MISLUKT'}: blootstelling, aantal en "
          "volgorde" + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def test_dagstop() -> bool:
    fouten = []
    bankroll = 1000.0
    kandidaten = [{"naam": "a", "win_kans": 0.9, "prijs": 0.2}]
    plan = I.verdeel(kandidaten, bankroll,
                     dagverlies=I.DAGVERLIES_STOP * bankroll)
    if not plan["stop"] or plan["posities"]:
        fouten.append("op de dagstop komt er toch nog wat bij")
    plan2 = I.verdeel(kandidaten, bankroll,
                      dagverlies=I.DAGVERLIES_STOP * bankroll - 1)
    if plan2["stop"] or not plan2["posities"]:
        fouten.append("net onder de dagstop wordt er niets ingezet")
    # onbekend dagverlies: waarschuwen, niet stilzwijgend doorgaan
    plan3 = I.verdeel(kandidaten, bankroll, dagverlies=None)
    if not plan3["waarschuwing"]:
        fouten.append("een onbekend dagverlies geeft geen waarschuwing")
    if plan3["stop"]:
        fouten.append("een onbekend dagverlies zet alles stil")
    ok = not fouten
    print(f"  dagstop    {'ok' if ok else 'MISLUKT'}: stopt op verlies, "
          "waarschuwt bij onbekend" + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def _verzin_log(pad: Path, rnd, model_weet_het: bool, n_reeksen: int = 60):
    """Een signalenlog met bekende structuur: elf vakken per reeks, één winnaar.

    Bij `model_weet_het` legt de modelkans het gewicht op de winnaar en de
    marktprijs niet; dan hoort de meting een hoge lambda terug te vinden. Anders
    is de modelkans ruis rond de marktprijs en hoort lambda laag uit te komen."""
    kop = S.KOP
    rijen = []
    for i in range(n_reeksen):
        # elke reeks een eigen doeldag. Eerst stond hier (i % 28) + 1, waardoor
        # zestig reeksen op achtentwintig datums vielen: dezelfde stad-dag kreeg
        # dan meerdere winnaars en de fit ging over onzin.
        dag = (date(2026, 3, 1) + timedelta(days=i)).isoformat()
        key = "NYC"
        win = rnd.randrange(11)
        # de marktprijs is een redelijke maar niet perfecte verdeling
        markt = [0.02] * 11
        markt[win] = 0.5
        buur = min(10, max(0, win + rnd.choice([-1, 1])))
        markt[buur] = 0.28
        som = sum(markt)
        markt = [p / som for p in markt]
        for gelogd, uur_voor in ((dag + "T00:00+00:00", 30),):
            for v in range(11):
                if model_weet_het:
                    mk = 0.80 if v == win else 0.02
                else:
                    mk = min(0.97, max(0.01, markt[v] + rnd.gauss(0, 0.05)))
                rij = {k: "" for k in kop}
                rij.update({
                    "gelogd_utc": gelogd, "key": key, "doel_datum": dag,
                    "lead": "0", "soort": "max", "eenheid": "°F",
                    "bracket_label": f"{60 + 2 * v}-{61 + 2 * v}°F",
                    "model_kans": f"{mk:.4f}",
                    "markt_prijs": f"{markt[v]:.4f}",
                })
                rijen.append([rij[k] for k in kop])
        # de afrekening: laatste regel per vak zet de winnaar op 0,9995
        for v in range(11):
            rij = {k: "" for k in kop}
            rij.update({
                "gelogd_utc": dag + "T23:00+00:00", "key": key,
                "doel_datum": dag, "lead": "0", "soort": "max", "eenheid": "°F",
                "bracket_label": f"{60 + 2 * v}-{61 + 2 * v}°F",
                "model_kans": "0.5000",
                "markt_prijs": "0.9995" if v == win else "0.0005",
            })
            rijen.append([rij[k] for k in kop])
    with open(pad, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(kop)
        w.writerows(rijen)


def test_meten() -> bool:
    fouten = []
    rnd = random.Random(5)
    with tempfile.TemporaryDirectory() as map_:
        goed_pad = Path(map_) / "goed.csv"
        ruis_pad = Path(map_) / "ruis.csv"
        _verzin_log(goed_pad, rnd, True)
        _verzin_log(ruis_pad, rnd, False)
        goed = I.meet(goed_pad, trekkingen=20)
        ruis = I.meet(ruis_pad, trekkingen=20)
    if not goed:
        fouten.append("op het logboek waar het model gelijk heeft komt niets terug")
    else:
        lam = max(r["lam"] for r in goed)
        if lam < 0.5:
            fouten.append(f"een model dat gelijk heeft geeft lambda {lam}, te laag")
    if not ruis:
        fouten.append("op het ruislogboek komt niets terug")
    else:
        lam = max(r["lam"] for r in ruis)
        if lam > 0.5:
            fouten.append(f"ruis geeft lambda {lam}, te hoog")
    ok = not fouten
    print(f"  meten      {'ok' if ok else 'MISLUKT'}: lambda wordt op een bekend "
          "logboek teruggevonden" + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def main() -> int:
    print("\n  Zelftest inzetregel\n")
    goed = all([test_kelly(), test_mengen(), test_plafond(), test_verdelen(),
                test_dagstop(), test_meten()])
    print("\n  " + ("Alles in orde.\n" if goed else "ER GING IETS MIS.\n"))
    return 0 if goed else 1


if __name__ == "__main__":
    sys.exit(main())
