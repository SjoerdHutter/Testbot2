#!/usr/bin/env python3
"""Zelftest voor de intraday-conditionering.

  python3 bot/test_waarneming.py

Controleert tien dingen:

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
  fijn         Een fijnmaziger reeks stelt een dagcijfer alleen de goede kant op
               bij, en alleen bij genoeg metingen binnen de marge.
  hfmetar      De MADIS-stroom vraagt de goede report_type-waarden op, rekent
               naar °C om, en valt vanzelf af voor een station dat alleen het
               hele uur meldt — zonder dat er een deelnemerslijst bijgehouden
               hoeft te worden.
  bronnen      De ontleders van FMI, KNMI en KMA halen de goede cijfers uit een
               vaste voorbeeldrespons, geven niets terug op rommel, en een bron
               met een sleutel doet niets zolang die sleutel ontbreekt.

Alles draait offline; er gaat geen verzoek uit.
"""
import math
import sys
from datetime import date
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


def test_fijn() -> bool:
    """De verfijning uit bot/fijnmeting.py mag een dagcijfer alleen de goede kant
    op bijstellen, en alleen bij genoeg metingen binnen de marge. Alles wat daar
    niet aan voldoet moet het uurlijkse METAR-cijfer laten staan — dat is de
    veilige kant, want een te hoge ondergrens streept een vak weg dat nog kon
    vallen."""
    import fijnmeting as F
    stad = {"key": "TYO", "fijn": "amedas", "eenheid": "C", "naam": "Tokio",
            "lat": 35.55, "lon": 139.78, "tz": "Asia/Tokyo", "station": "RJTT"}
    dag = "2026-08-10"
    echt = F.reeks_voor
    fouten = []

    def proef(reeks, start, soort, verwacht, wat):
        F.reeks_voor = lambda s, d, b=None: {dag: reeks}
        uit = {dag: start}
        F.verfijn(stad, uit, [], soort)
        if abs(uit[dag] - verwacht) > 1e-9:
            fouten.append(f"{wat}: {uit[dag]} in plaats van {verwacht}")

    vol = {"max": 31.4, "min": 24.1, "n": 144}
    try:
        proef(vol, 30.0, "max", 31.4, "een hoger maximum hoort door te komen")
        proef(vol, 32.0, "max", 32.0, "een lager maximum mag niet overschrijven")
        proef(vol, 25.0, "max", 25.0, "meer dan de marge erboven hoort te blijven staan")
        proef(vol, 25.0, "min", 24.1, "een lager minimum hoort door te komen")
        proef(vol, 23.0, "min", 23.0, "een hoger minimum mag niet overschrijven")
        proef({"max": 31.4, "min": 24.1, "n": 3}, 30.0, "max", 30.0,
              "te weinig metingen hoort te blijven staan")
        proef({"max": None, "min": None, "n": 144}, 30.0, "max", 30.0,
              "een lege reeks hoort te blijven staan")
        # een stad zonder fijne bron mag er nooit een krijgen
        F.reeks_voor = echt
        kaal = {"key": "AMS", "eenheid": "C", "lat": 52.3, "lon": 4.8,
                "tz": "Europe/Amsterdam", "station": "EHAM"}
        uit = {dag: 20.0}
        if F.verfijn(kaal, uit, []) != 0 or uit[dag] != 20.0:
            fouten.append("een stad zonder `fijn` wordt toch verfijnd")
    finally:
        F.reeks_voor = echt

    ok = not fouten
    print(f"  fijn         {'ok' if ok else 'MISLUKT'}: verfijnen stelt alleen "
          "de goede kant op bij" + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def test_hfmetar() -> bool:
    """De MADIS-stroom is hetzelfde verzoek met report_type=1 erbij, en valt
    vanzelf af voor een station dat alleen het hele uur meldt."""
    import fijnmeting as F
    fouten = []

    # 1. de URL draagt de goede report_type-waarden, en de standaard blijft 3
    from datetime import date as _date
    kaal = W._iem_url(["LGA"], "America/New_York", _date(2026, 8, 9), _date(2026, 8, 10))
    if kaal.count("report_type=") != 1 or "report_type=3" not in kaal:
        fouten.append(f"de standaard-URL is veranderd: {kaal[-40:]}")
    hf = W._iem_url(["LGA"], "America/New_York", _date(2026, 8, 9),
                    _date(2026, 8, 10), F.HFMETAR_SOORTEN)
    for soort in ("1", "3", "4"):
        if f"report_type={soort}" not in hf:
            fouten.append(f"report_type={soort} ontbreekt in de HFMETAR-URL")

    # 2. de reeks komt in °C terug, ook al geeft IEM °F
    echt = W.haal_stations
    stad = {"key": "AMS", "station": "EHAM", "tz": "Europe/Amsterdam",
            "eenheid": "C", "lat": 52.3, "lon": 4.8, "bron": "iem",
            "fijn": "hfmetar"}
    W.haal_stations = lambda st, tz, d1, d2, pauze=0.5, soorten=("3",): {
        "EHAM": {"2026-08-10": {"maxf": 77.0, "minf": 59.0, "n": 288,
                                "laatste_uur": 23.5}}}
    try:
        per = F.hfmetar_reeks(stad, [_date(2026, 8, 10)])
        e = per.get("2026-08-10") or {}
        if abs((e.get("max") or 0) - 25.0) > 1e-9:
            fouten.append(f"77 °F hoort 25 °C te zijn, kreeg {e.get('max')}")
        if e.get("n") != 288:
            fouten.append(f"het aantal metingen komt niet mee: {e.get('n')}")

        # 3. het belangrijkste: een station dat alleen uurlijks meldt haalt
        #    MIN_METINGEN niet en wordt dus niet verfijnd, zonder dat er ergens
        #    een lijst met deelnemers bijgehouden hoeft te worden.
        W.haal_stations = lambda st, tz, d1, d2, pauze=0.5, soorten=("3",): {
            "EHAM": {"2026-08-10": {"maxf": 77.0, "minf": 59.0, "n": 24,
                                    "laatste_uur": 23.0}}}
        uit = {"2026-08-10": 24.0}
        if F.verfijn(stad, uit, [_date(2026, 8, 10)], "max") != 0:
            fouten.append("een station met 24 meldingen per dag wordt toch verfijnd")
        if uit["2026-08-10"] != 24.0:
            fouten.append("het uurlijkse cijfer is aangepast terwijl dat niet mocht")
    finally:
        W.haal_stations = echt

    # 4. het oordeel in de dekkingstabel: meer meldingen én effect is pas een
    #    kandidaat, alleen meer meldingen niet.
    rijen = [
        {"key": "AAA", "station": "A", "dagen": 7, "n_routine": 24.0,
         "n_hf": 288.0, "gemiddeld": 0.4, "grootste": 0.9, "eenheid": "C"},
        {"key": "BBB", "station": "B", "dagen": 7, "n_routine": 24.0,
         "n_hf": 288.0, "gemiddeld": 0.01, "grootste": 0.1, "eenheid": "C"},
        {"key": "CCC", "station": "C", "dagen": 7, "n_routine": 24.0,
         "n_hf": 25.0, "gemiddeld": 0.0, "grootste": 0.0, "eenheid": "C"},
        {"key": "DDD", "station": "D", "dagen": 0, "n_routine": 0, "n_hf": 0,
         "gemiddeld": None, "grootste": None, "eenheid": "C"},
    ]
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        F.toon_dekking(rijen, 7)
    tekst = buf.getvalue()
    if "1 kandidaten" not in tekst:
        fouten.append("de telling van kandidaten klopt niet")
    for stuk in ("KANDIDAAT", "voegt niets toe", "alleen uurlijks", "geen data"):
        if stuk not in tekst:
            fouten.append(f"het oordeel {stuk!r} ontbreekt in de tabel")

    ok = not fouten
    print(f"  hfmetar      {'ok' if ok else 'MISLUKT'}: report_type, omrekening en "
          "de eis van genoeg metingen" + ("" if ok else "; " + "; ".join(fouten)))
    return ok


FMI_VOORBEELD = """<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
                       xmlns:BsWfs="http://xml.fmi.fi/schema/wfs/2.0">
  <wfs:member><BsWfs:BsWfsElement>
    <BsWfs:Time>2026-08-10T11:00:00Z</BsWfs:Time>
    <BsWfs:ParameterName>temperature</BsWfs:ParameterName>
    <BsWfs:ParameterValue>21.4</BsWfs:ParameterValue>
  </BsWfs:BsWfsElement></wfs:member>
  <wfs:member><BsWfs:BsWfsElement>
    <BsWfs:Time>2026-08-10T12:10:00Z</BsWfs:Time>
    <BsWfs:ParameterName>temperature</BsWfs:ParameterName>
    <BsWfs:ParameterValue>23.9</BsWfs:ParameterValue>
  </BsWfs:BsWfsElement></wfs:member>
  <wfs:member><BsWfs:BsWfsElement>
    <BsWfs:Time>2026-08-10T13:10:00Z</BsWfs:Time>
    <BsWfs:ParameterName>temperature</BsWfs:ParameterName>
    <BsWfs:ParameterValue>NaN</BsWfs:ParameterValue>
  </BsWfs:BsWfsElement></wfs:member>
  <wfs:member><BsWfs:BsWfsElement>
    <BsWfs:Time>2026-08-09T14:00:00Z</BsWfs:Time>
    <BsWfs:ParameterName>temperature</BsWfs:ParameterName>
    <BsWfs:ParameterValue>18.0</BsWfs:ParameterValue>
  </BsWfs:BsWfsElement></wfs:member>
</wfs:FeatureCollection>"""

KNMI_VOORBEELD = """# BRON: KONINKLIJK NEDERLANDS METEOROLOGISCH INSTITUUT
# STN,YYYYMMDD,   TX,   TN
  240,20260810,  254,  138
  240,20260811,  231,
  240,20260812,     ,  145
"""

KMA_VOORBEELD = {"response": {"body": {"items": {"item": [
    {"tm": "2026-08-10 13:00", "ta": "29.4"},
    {"tm": "2026-08-10 14:00", "ta": "31.1"},
    {"tm": "2026-08-10 15:00", "ta": ""},
    {"tm": "2026-08-11 03:00", "ta": "24.0"},
]}}}}


def test_bronnen() -> bool:
    """De drie nieuwe ontleders tegen een vaste voorbeeldrespons.

    Het eindpunt zelf is hier niet te bereiken, dus wat hier getoetst wordt is
    het ontleden: krijgt een onverwachte of lege respons geen cijfer door, en
    komt een goede respons op de goede lokale dag terecht. Blijkt het echte
    formaat straks anders, dan geeft de ontleder niets terug en meldt
    `--dekking` dat als 'geen data' — nooit een verzonnen cijfer."""
    import fijnmeting as F
    fouten = []

    # FMI: GML met naamruimtes, een NaN ertussen, en twee lokale dagen
    per = F._uit_tijdreeks(F._fmi_punten(FMI_VOORBEELD), "Europe/Helsinki")
    d = per.get("2026-08-10") or {}
    if abs((d.get("max") or 0) - 23.9) > 1e-9:
        fouten.append(f"fmi max {d.get('max')} != 23,9")
    if abs((d.get("min") or 0) - 21.4) > 1e-9:
        fouten.append(f"fmi min {d.get('min')} != 21,4")
    if d.get("n") != 2:
        fouten.append(f"fmi telt {d.get('n')} in plaats van 2 (NaN hoort eruit)")
    if "2026-08-09" not in per:
        fouten.append("fmi verliest de tweede dag")
    for rommel in ("", "<html>fout</html>", "geen xml"):
        if F._fmi_punten(rommel):
            fouten.append(f"fmi haalt cijfers uit {rommel!r}")

    # KNMI: tienden van graden, en lege velden
    k = F._knmi_ontleed(KNMI_VOORBEELD)
    if (k.get("2026-08-10") or {}).get("max") != 25.4:
        fouten.append(f"knmi 254 wordt {k.get('2026-08-10')}")
    if (k.get("2026-08-10") or {}).get("min") != 13.8:
        fouten.append("knmi tn komt niet mee")
    if (k.get("2026-08-11") or {}).get("min") is not None:
        fouten.append("knmi verzint een tn waar er geen staat")
    if (k.get("2026-08-12") or {}).get("max") is not None:
        fouten.append("knmi verzint een tx waar er geen staat")
    if (k.get("2026-08-10") or {}).get("n", 0) < F.MIN_METINGEN:
        fouten.append("knmi komt niet door de metingeneis, terwijl het het "
                      "officiele dagcijfer is en geen bemonstering")
    if F._knmi_ontleed("# alleen commentaar\n"):
        fouten.append("knmi levert iets op een lege respons")

    # KMA: json met lege waarden, tijden in lokale tijd
    m = F._kma_ontleed(KMA_VOORBEELD, "Asia/Seoul")
    if abs(((m.get("2026-08-10") or {}).get("max") or 0) - 31.1) > 1e-9:
        fouten.append(f"kma max {m.get('2026-08-10')}")
    if (m.get("2026-08-10") or {}).get("n") != 2:
        fouten.append("kma telt de lege waarde mee")
    if "2026-08-11" not in m:
        fouten.append("kma verliest de tweede dag")
    for rommel in (None, {}, {"response": {}}, {"response": {"body": None}}):
        if F._kma_ontleed(rommel, "Asia/Seoul"):
            fouten.append(f"kma levert iets op {rommel!r}")

    # de registratie zelf: elke bron kent zijn steden, en een bron met een
    # sleutel levert niets zonder die sleutel
    import os
    for naam, b in F.BRONNEN.items():
        if not callable(b.get("reeks")):
            fouten.append(f"{naam} heeft geen reeksfunctie")
        if b["steden"]:
            for k2 in b["steden"]:
                if not any(s["key"] == k2 for s in F.weer.STEDEN):
                    fouten.append(f"{naam} noemt onbekende stad {k2}")
    oud = os.environ.pop("KMA_SLEUTEL", None)
    try:
        seoul = [s for s in F.weer.STEDEN if s["key"] == "SEL"][0]
        if F.kma_reeks(seoul, [date(2026, 8, 10)]) != {}:
            fouten.append("kma haalt data op zonder sleutel")
    finally:
        if oud is not None:
            os.environ["KMA_SLEUTEL"] = oud

    # een stad krijgt geen bron die niet voor haar bedoeld is
    ams = [s for s in F.weer.STEDEN if s["key"] == "AMS"][0]
    if F.reeks_voor(ams, [date(2026, 8, 10)], "amedas") != {}:
        fouten.append("amsterdam krijgt de tokiobron")

    ok = not fouten
    print(f"  bronnen      {'ok' if ok else 'MISLUKT'}: FMI, KNMI en KMA "
          "ontleden, en de registratie" + ("" if ok else "; " + "; ".join(fouten)))
    return ok


def main() -> int:
    print("\n  Zelftest intraday-conditionering\n")
    goed = all([test_onveranderd(), test_behoudend(), test_verloop(),
                test_onmogelijk(), test_puntmassa(), test_spiegel(), test_iem(),
                test_fijn(), test_hfmetar(), test_bronnen()])
    print("\n  " + ("Alles in orde.\n" if goed else "ER GING IETS MIS.\n"))
    return 0 if goed else 1


if __name__ == "__main__":
    sys.exit(main())
