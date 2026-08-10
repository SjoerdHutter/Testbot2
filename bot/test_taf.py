#!/usr/bin/env python3
"""Zelftest voor de TAF-laag.

  python3 bot/test_taf.py

Controleert vijf dingen:

  ontleden   TX- en TN-groepen komen er goed uit, inclusief de M voor een
             minteken, en de geldigheidsperiode klopt.
  maandrand  Een TAF die op 31 januari wordt uitgegeven en over 1 februari gaat
             belandt in februari en niet elf maanden terug.
  doeldag    De omrekening van UTC naar de lokale kalenderdag van de stad klopt.
             Zonder die stap belandt een piek in Tokio op de verkeerde dag.
  mengen     Met gewicht nul verandert er niets, met een gewicht schuift de
             verwachting en de hele band mee, en een absurd verschil wordt
             genegeerd.
  leeg       Een lege of onzinnige TAF levert niets op in plaats van een fout.

Alles draait offline; er gaat geen verzoek uit.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import taf as T  # noqa: E402

LONDEN = ("TAF EGLC 101100Z 1012/1112 24010KT 9999 SCT030 TX24/1015Z TN14/1104Z "
          "TEMPO 1015/1018 25015G25KT BECMG 1100/1103 22008KT")
VRIES = "TAF EFHK 151700Z 1518/1618 18005KT 9999 BKN020 TXM02/1612Z TNM09/1603Z"
TOKIO = "TAF RJTT 101800Z 1018/1124 36008KT 9999 FEW030 TX33/1106Z TN26/1021Z"


def test_ontleden() -> bool:
    fouten = []
    o = T.ontleed(LONDEN, datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc))
    if not o["tx"] or o["tx"][0]["c"] != 24.0:
        fouten.append(f"tx {o.get('tx')}")
    if o["tx"][0]["op"] != datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc):
        fouten.append(f"tx-tijd {o['tx'][0]['op']}")
    if not o["tn"] or o["tn"][0]["c"] != 14.0:
        fouten.append(f"tn {o.get('tn')}")
    if o["geldig_van"] != datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc):
        fouten.append(f"geldig_van {o['geldig_van']}")
    if o["geldig_tot"] != datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc):
        fouten.append(f"geldig_tot {o['geldig_tot']}")
    soorten = [g["soort"] for g in o["groepen"]]
    if soorten != ["TEMPO", "BECMG"]:
        fouten.append(f"groepen {soorten}")

    # de M voor een minteken: TXM02 is min twee, niet plus twee
    v = T.ontleed(VRIES, datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc))
    if not v["tx"] or v["tx"][0]["c"] != -2.0:
        fouten.append(f"TXM02 geeft {v.get('tx')}")
    if not v["tn"] or v["tn"][0]["c"] != -9.0:
        fouten.append(f"TNM09 geeft {v.get('tn')}")

    ok = not fouten
    print(f"  ontleden   {'ok' if ok else 'MISLUKT'}: TX, TN, geldigheid en groepen"
          + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def test_maandrand() -> bool:
    """Een TAF noemt alleen de dag van de maand. Uitgegeven op 31 januari over
    1 februari hoort de eerste in februari te belanden."""
    fouten = []
    ruw = "TAF EHAM 311700Z 3118/0118 21010KT 9999 BKN015 TX07/0113Z TN02/0105Z"
    o = T.ontleed(ruw, datetime(2026, 1, 31, 17, 0, tzinfo=timezone.utc))
    if not o["tx"]:
        fouten.append("geen tx")
    elif o["tx"][0]["op"] != datetime(2026, 2, 1, 13, 0, tzinfo=timezone.utc):
        fouten.append(f"tx-tijd {o['tx'][0]['op']} in plaats van 1 februari 13:00")
    if o["geldig_tot"] != datetime(2026, 2, 1, 18, 0, tzinfo=timezone.utc):
        fouten.append(f"geldig_tot {o['geldig_tot']}")
    # en de andere kant op: 1 februari uitgegeven, groep op de 31e hoort januari
    o2 = T.ontleed("TAF EHAM 010500Z 0106/0206 21010KT TX09/3115Z",
                   datetime(2026, 2, 1, 5, 0, tzinfo=timezone.utc))
    if o2["tx"] and o2["tx"][0]["op"] != datetime(2026, 1, 31, 15, 0, tzinfo=timezone.utc):
        fouten.append(f"terugkijkend {o2['tx'][0]['op']}")
    ok = not fouten
    print(f"  maandrand  {'ok' if ok else 'MISLUKT'}: de dag van de maand landt in "
          "de goede maand" + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def test_doeldag() -> bool:
    """TX33/1106Z is 06:00 UTC op de elfde, en dat is 15:00 lokaal in Tokio op
    diezelfde elfde. Reken je niet om, dan zou hij op de tiende belanden bij een
    stad die westelijker ligt, of hier op de goede dag om de verkeerde reden."""
    fouten = []
    o = T.ontleed(TOKIO, datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc))
    per = T.per_doeldag(o, "Asia/Tokyo")
    if per.get("2026-08-11", {}).get("tx") != 33.0:
        fouten.append(f"tokio 11 augustus: {per.get('2026-08-11')}")
    # dezelfde TAF gelezen alsof de stad in Londen stond: 06:00 UTC blijft de 11e
    per_lon = T.per_doeldag(o, "Europe/London")
    if per_lon.get("2026-08-11", {}).get("tx") != 33.0:
        fouten.append(f"londen 11 augustus: {per_lon.get('2026-08-11')}")
    # een piek net na lokale middernacht hoort op de nieuwe dag te vallen
    laat = T.ontleed("TAF RJTT 101800Z 1018/1124 36008KT TX30/1016Z",
                     datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc))
    p = T.per_doeldag(laat, "Asia/Tokyo")
    if "2026-08-11" not in p:
        fouten.append(f"16:00 UTC hoort in Tokio op de 11e te vallen: {p}")
    ok = not fouten
    print(f"  doeldag    {'ok' if ok else 'MISLUKT'}: UTC naar de lokale "
          "kalenderdag" + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def test_mengen() -> bool:
    fouten = []
    basis = {"verwachting": 20.0, "p10": 17.0, "p90": 23.0}

    d = dict(basis)
    T.meng_taf(d, 24.0, 0)                       # standaardgewicht is nul
    if d != basis:
        fouten.append(f"met gewicht nul verandert er toch iets: {d}")

    d = dict(basis)
    T.meng_taf(d, 24.0, 0, gewicht=0.25)
    if abs(d["verwachting"] - 21.0) > 1e-9:
        fouten.append(f"verwachting {d['verwachting']} in plaats van 21,0")
    if abs(d["p10"] - 18.0) > 1e-9 or abs(d["p90"] - 24.0) > 1e-9:
        fouten.append(f"de band schuift niet mee: {d['p10']} tot {d['p90']}")

    d = dict(basis)
    T.meng_taf(d, 90.0, 0, gewicht=0.25)         # absurd ver weg
    if d != basis:
        fouten.append("een absurd verschil wordt toch bijgemengd")

    d = dict(basis)
    T.meng_taf(d, None, 0, gewicht=0.25)
    if d != basis:
        fouten.append("zonder TX verandert er toch iets")

    d = dict(basis)
    T.meng_taf(d, 24.0, 5, gewicht=0.25)         # buiten de horizon
    if d != basis:
        fouten.append("buiten de horizon wordt toch bijgemengd")

    ok = not fouten
    print(f"  mengen     {'ok' if ok else 'MISLUKT'}: verwachting en band samen"
          + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def test_leeg() -> bool:
    fouten = []
    for ruw in ("", None, "   ", "onzin zonder groepen", "TAF EGLC 101100Z"):
        try:
            o = T.ontleed(ruw)
        except Exception as ex:                   # noqa: BLE001
            fouten.append(f"{ruw!r} gooit {ex}")
            continue
        if o and o.get("tx"):
            fouten.append(f"{ruw!r} levert een tx op")
        if o:
            try:
                T.per_doeldag(o, "Europe/London")
                T.piekgroep(o, "2026-08-10", "Europe/London")
            except Exception as ex:               # noqa: BLE001
                fouten.append(f"{ruw!r} valt om na het ontleden: {ex}")
    ok = not fouten
    print(f"  leeg       {'ok' if ok else 'MISLUKT'}: lege invoer geeft niets, "
          "geen fout" + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def main() -> int:
    print("\n  Zelftest TAF-laag\n")
    goed = all([test_ontleden(), test_maandrand(), test_doeldag(),
                test_mengen(), test_leeg()])
    print("\n  " + ("Alles in orde.\n" if goed else "ER GING IETS MIS.\n"))
    return 0 if goed else 1


if __name__ == "__main__":
    sys.exit(main())
