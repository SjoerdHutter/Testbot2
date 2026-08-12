#!/usr/bin/env python3
"""Hoeveel je zou inzetten, en waar de rem zit.

Strategie A zegt wélke vakjes, nooit hoevéél. Deze module rekent dat uit met
fractionele Kelly, en zet er de plafonds omheen die voorkomen dat één stand het
hele boek meeneemt.

Eerst het ongemakkelijke deel
-----------------------------
Kelly is alleen optimaal als de kans klopt. Voor je hem op de modelkans loslaat
hoort de vraag op tafel of die kans überhaupt beter is dan de marktprijs. Die
vraag is te beantwoorden met logs/signalen.csv, en het antwoord is op dit moment
nee.

Gemeten over de afgerekende reeksen in het logboek, met de mengfactor `lam` in

    logit(p) = logit(prijs) + lam * (logit(modelkans) - logit(prijs))

komt de best passende `lam` op nul uit, in elk venster, met een 95%
bootstrap-interval (geclusterd per reeks, want regels binnen één reeks hangen
samen) dat nul ruim omvat:

    venster          n     reeksen   lam*    95%-interval      brier lam*  brier markt
    meer dan 24u     6655     123    0,046   [-0,15, +0,34]      0,0595      0,0595
    12 tot 24u       3223     147   -0,100   [-0,24, +0,07]      0,0484      0,0485
    minder dan 12u   4323     175    0,029   [-0,21, +0,20]      0,0107      0,0107

De Brier-score met de best passende meng is tot in vier decimalen gelijk aan die
van de kale marktprijs. Anders gezegd: het verschil tussen onze kans en de prijs
voorspelt in deze steekproef niets. Een logit-regressie met model en markt los
ernaast geeft hetzelfde beeld — in het koopvenster van strategie A (12 tot 36 uur
voor sluiting) staat de modelcoëfficiënt op +0,10 met z ≈ 1,1, en de standaardfout
daarvan is nog te klein opgeschreven omdat regels binnen een reeks samenhangen.

Wat dat wel en niet betekent
----------------------------
Wel: met wat er nu in het logboek staat is er geen aantoonbare edge, en een
inzetregel die op die edge stuurt hoort dus kleine bedragen te noemen. Dat is
geen voorzichtigheid die ik erin gedraaid heb, het komt uit de meting.

Niet: dat het model niets waard is. Drie dingen om in gedachten te houden.

* De steekproef is klein — ruim honderd onafhankelijke reeksen, een kleine twee
  weken. Een edge van een paar procentpunt is hier niet van nul te onderscheiden;
  het interval sluit +0,34 net zo min uit als nul.
* De conditionering op de meting van vandaag zit in deze cijfers *niet*. Alle
  gelogde modelkansen zijn van vóór die wijziging. Juist in het venster waar de
  markt het hardst won kwam dat doordat de markt de al gemeten temperatuur zag
  en het model niet, en dat gat is nu dicht. Deze tabel meet het oude model.
* `lam` is per venster gemeten, niet per stad of per liquiditeit. Strategie A
  koopt de staart op dunne markten, en juist daar zou een prijs minder scherp
  kunnen zijn dan gemiddeld.

Daarom is de standaard `LAMBDA` niet nul (dan zou er nooit iets ingezet worden en
was er niets meer te meten) en niet één (dan geloven we een edge die de data niet
laat zien), maar de bovenkant van wat het interval nog toestaat. Met
`--lam` is hij per run te overschrijven, en `--meet` rekent hem opnieuw uit het
logboek. Zodra de geconditioneerde kansen lang genoeg gelogd zijn is dat de eerste
som die je wilt draaien.

De inzet zelf
-------------
Voor een contract dat je koopt tegen prijs `c` en dat 1 uitkeert met kans `w`:

    Kelly f* = (w - c) / (1 - c)

Bij een NO op een vak is `w` de kans dat het vak *niet* valt en `c` de NO-prijs,
dus 1 min de Ja-prijs. Daar gaat `KELLY_FRACTIE` overheen — een kwart Kelly, wat
gebruikelijk is zodra de kans zelf onzeker is, en dat is hij hier nadrukkelijk.

Daarna komen de plafonds. Die staan er niet om de opbrengst te knijpen maar omdat
Kelly bij een verkeerd geschatte kans hard kan doorschieten, en omdat 49 steden
op dezelfde dag geen 49 onafhankelijke weddenschappen zijn: een hittegolf zit in
alle posities tegelijk.

Gebruik (vanuit de hoofdmap van de repo):

    python3 bot/inzet.py --meet                de mengfactor uit het logboek
    python3 bot/inzet.py --bankroll 500        de inzetten bij de huidige stand
    python3 bot/inzet.py --bankroll 500 --lam 1.0
    python3 bot/inzet.py --bankroll 500 --dagverlies 20

Er wordt niets geplaatst en niets verkocht. Dit is een berekening, net als de
rest van deze repo.
"""
import collections
import csv
import json
import math
import random
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weer

WORTEL = Path(__file__).resolve().parent.parent

# Hoeveel van het verschil tussen modelkans en marktprijs we geloven. Zie de kop:
# gemeten komt dit op nul uit, dit is de bovenkant van het bootstrap-interval.
LAMBDA = 0.30
KELLY_FRACTIE = 0.25     # kwart Kelly; de kans zelf is onzeker

# De plafonds, als fractie van het bankroll tenzij anders vermeld.
MAX_PER_POSITIE = 0.02
MAX_BLOOTSTELLING = 0.20
MAX_POSITIES = 20
DAGVERLIES_STOP = 0.05
MIN_INZET = 1.0          # dollar; kleiner is de moeite en de spread niet waard

VENSTERS = (("meer dan 24u", 24, math.inf),
            ("12 tot 24u", 12, 24),
            ("minder dan 12u", 0, 12))


# ── De kans waar je op inzet ──────────────────────────────────────────────────

def logit(p: float) -> float:
    p = min(max(p, 0.002), 0.998)
    return math.log(p / (1 - p))


def sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def gemengd(model_kans, markt_prijs, lam: float = None) -> float:
    """De kans waar de inzet op rust: de marktprijs, `lam` van de weg opgeschoven
    richting onze eigen kans, op de logit-schaal.

    Op de logit-schaal en niet lineair, omdat het verschil tussen 1% en 2% een
    heel andere weddenschap is dan tussen 50% en 51% terwijl het lineair even
    groot lijkt.

    Zonder marktprijs valt er niets te mengen en blijft de modelkans staan; dan
    is er ook geen prijs om tegen af te zetten, dus komt er verderop toch geen
    inzet uit."""
    if markt_prijs is None:
        return model_kans
    if model_kans is None:
        return markt_prijs
    l = LAMBDA if lam is None else lam
    return sigmoid(logit(markt_prijs) + l * (logit(model_kans) - logit(markt_prijs)))


def kelly(win_kans: float, prijs: float) -> float:
    """De volle Kelly-fractie voor een contract van 1 dollar tegen `prijs`.

    Negatief betekent dat de weddenschap de verkeerde kant op staat; die geven we
    terug zoals hij is en niet als nul, zodat de beller het verschil ziet tussen
    "geen voordeel" en "voordeel aan de andere kant"."""
    if prijs is None or not 0 < prijs < 1 or win_kans is None:
        return 0.0
    return (win_kans - prijs) / (1 - prijs)


def inzet(win_kans, prijs, bankroll: float, lam: float = None,
          fractie: float = None) -> dict:
    """Wat je op deze ene stand zou zetten, voor de plafonds over het geheel.

    Geeft de fractie, het bedrag en de reden waarom er niets uitkomt als dat zo
    is. Die reden hoort erbij: een inzet van nul omdat er geen voordeel is leest
    heel anders dan een inzet van nul omdat de prijs ontbreekt."""
    f_vol = kelly(win_kans, prijs)
    f = f_vol * (KELLY_FRACTIE if fractie is None else fractie)
    uit = {"kelly_vol": round(f_vol, 4), "fractie": 0.0, "bedrag": 0.0,
           "reden": ""}
    if prijs is None:
        uit["reden"] = "geen marktprijs"
        return uit
    if f_vol <= 0:
        uit["reden"] = "geen voordeel tegen deze prijs"
        return uit
    f = min(f, MAX_PER_POSITIE)
    bedrag = f * bankroll
    if bedrag < MIN_INZET:
        uit["reden"] = f"onder de ondergrens van {MIN_INZET:.0f}"
        return uit
    uit["fractie"] = round(f, 4)
    uit["bedrag"] = round(bedrag, 2)
    if f >= MAX_PER_POSITIE - 1e-9:
        uit["reden"] = f"afgetopt op {MAX_PER_POSITIE:.0%} van het bankroll"
    return uit


def verdeel(kandidaten: list, bankroll: float, open_blootstelling: float = 0.0,
            n_open: int = 0, dagverlies: float = None) -> dict:
    """De plafonds over het geheel, op volgorde van de grootste Kelly-fractie.

    `kandidaten` is een lijst van dicts met minstens `win_kans` en `prijs`; er
    komt per stuk een `inzet` bij. De volgorde is niet willekeurig: raakt het
    plafond op, dan houd je liever de standen met het meeste voordeel over.

    `dagverlies` is het gerealiseerde verlies van vandaag als positief getal.
    Staat het op None, dan is het niet vast te stellen en zegt de uitvoer dat
    met zoveel woorden — stil doorgaan zou hier het ergste zijn, want dan lijkt
    de rem te werken terwijl hij niet eens gemeten is."""
    uit = {"bankroll": bankroll, "toegewezen": 0.0, "posities": [],
           "geweigerd": [], "stop": None, "waarschuwing": None}

    if dagverlies is None:
        uit["waarschuwing"] = ("het dagverlies is niet vast te stellen, dus de "
                               "stop van {:.0%} is niet getoetst".format(DAGVERLIES_STOP))
    elif dagverlies >= DAGVERLIES_STOP * bankroll:
        uit["stop"] = (f"dagverlies {dagverlies:.2f} is {DAGVERLIES_STOP:.0%} van "
                       f"het bankroll of meer: vandaag niets meer bij")
        uit["geweigerd"] = [dict(k, inzet={"fractie": 0.0, "bedrag": 0.0,
                                           "reden": "dagstop"}) for k in kandidaten]
        return uit

    ruimte_bedrag = MAX_BLOOTSTELLING * bankroll - open_blootstelling
    ruimte_aantal = MAX_POSITIES - n_open

    op_volgorde = sorted(kandidaten,
                         key=lambda k: -kelly(k.get("win_kans"), k.get("prijs")))
    for k in op_volgorde:
        r = dict(k)
        r["inzet"] = inzet(k.get("win_kans"), k.get("prijs"), bankroll)
        bedrag = r["inzet"]["bedrag"]
        if bedrag <= 0:
            uit["geweigerd"].append(r)
            continue
        if ruimte_aantal <= 0:
            r["inzet"] = {"fractie": 0.0, "bedrag": 0.0, "kelly_vol": r["inzet"]["kelly_vol"],
                          "reden": f"al {MAX_POSITIES} posities open"}
            uit["geweigerd"].append(r)
            continue
        if bedrag > ruimte_bedrag:
            bedrag = max(0.0, ruimte_bedrag)
            if bedrag < MIN_INZET:
                r["inzet"] = {"fractie": 0.0, "bedrag": 0.0,
                              "kelly_vol": r["inzet"]["kelly_vol"],
                              "reden": f"blootstelling zit op {MAX_BLOOTSTELLING:.0%}"}
                uit["geweigerd"].append(r)
                continue
            r["inzet"]["bedrag"] = round(bedrag, 2)
            r["inzet"]["fractie"] = round(bedrag / bankroll, 4)
            r["inzet"]["reden"] = f"afgetopt op de blootstelling van {MAX_BLOOTSTELLING:.0%}"
        ruimte_bedrag -= bedrag
        ruimte_aantal -= 1
        uit["toegewezen"] += bedrag
        uit["posities"].append(r)
    uit["toegewezen"] = round(uit["toegewezen"], 2)
    return uit


# ── De mengfactor meten ───────────────────────────────────────────────────────

def _uitslagen(rijen: list) -> dict:
    """Per reeks het vak dat gewonnen heeft, afgeleid uit de laatst gelogde
    prijzen: precies één vak boven 0,97 en de rest onder 0,03."""
    laatste = {}
    for r in rijen:
        k = (r["key"], r["doel_datum"], r["soort"], r["bracket_label"])
        if k not in laatste or r["gelogd_utc"] > laatste[k][0]:
            laatste[k] = (r["gelogd_utc"], r)
    per = collections.defaultdict(dict)
    for (key, dag, soort, lab), (_t, r) in laatste.items():
        if r["markt_prijs"] != "":
            per[(key, dag, soort)][lab] = float(r["markt_prijs"])
    uit = {}
    for k, d in per.items():
        hoog = [l for l, p in d.items() if p >= 0.97]
        laag = [l for l, p in d.items() if p <= 0.03]
        if len(hoog) == 1 and len(laag) == len(d) - 1:
            uit[k] = hoog[0]
    return uit


def _sluiting(key: str, dag: str) -> float:
    tz = ZoneInfo(weer.STAD_OP_KEY[key]["tz"])
    d = datetime.fromisoformat(dag + "T00:00:00").replace(tzinfo=tz)
    return d.timestamp() + 86400


def _brier(punten: list, lam: float) -> float:
    s = 0.0
    for m, k, y in punten:
        s += (sigmoid(k + lam * (m - k)) - y) ** 2
    return s / len(punten)


def _beste_lam(punten: list) -> float:
    lo, hi = -0.5, 1.5
    for _ in range(80):
        a, b = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        if _brier(punten, a) < _brier(punten, b):
            hi = b
        else:
            lo = a
    return (lo + hi) / 2


def meet(pad: Path = None, trekkingen: int = 120) -> list:
    """De mengfactor per venster, met een bootstrap-interval.

    De bootstrap trekt hele reeksen en geen losse regels. Elf vakken van dezelfde
    stad-dag zijn één waarneming, geen elf: doe je dat niet, dan komt er een
    interval uit dat een factor drie te smal is en lijkt een toevalstreffer
    significant."""
    pad = pad or (Path.cwd() / "logs" / "signalen.csv")
    with open(pad, newline="") as f:
        rijen = list(csv.DictReader(f))
    winnaar = _uitslagen(rijen)

    # Momenten waarop de markt al afgerekend had tellen niet mee. Polymarket
    # schiet naar 0,0005 of 0,9995 zodra het dagcijfer binnen is, terwijl de dag
    # lokaal nog loopt en het logboek gewoon doortikt. Zulke regels zijn geen
    # voorspelling maar een uitslag: de markt heeft er per definitie gelijk in en
    # het model per definitie niet. Ze meetellen zou de meting bijna helemaal
    # over de afrekening laten gaan in plaats van over vooruitkijken — dezelfde
    # val die portfolio.py met `market_decided` al ontwijkt.
    beslist = set()
    for r in rijen:
        if not r["markt_prijs"]:
            continue
        if float(r["markt_prijs"]) >= 0.98:
            beslist.add((r["key"], r["doel_datum"], r["soort"], r["gelogd_utc"]))

    per_venster = {naam: [] for naam, _, _ in VENSTERS}
    reeksen = {naam: collections.defaultdict(list) for naam, _, _ in VENSTERS}
    for r in rijen:
        k = (r["key"], r["doel_datum"], r["soort"])
        if k not in winnaar or not r["markt_prijs"] or not r["model_kans"]:
            continue
        if r["key"] not in weer.STAD_OP_KEY:
            continue
        if (r["key"], r["doel_datum"], r["soort"], r["gelogd_utc"]) in beslist:
            continue
        uren = (_sluiting(r["key"], r["doel_datum"]) -
                datetime.fromisoformat(r["gelogd_utc"]).timestamp()) / 3600
        if uren < 0:
            continue
        punt = (logit(float(r["model_kans"])), logit(float(r["markt_prijs"])),
                1.0 if r["bracket_label"] == winnaar[k] else 0.0)
        for naam, lo, hi in VENSTERS:
            if lo <= uren < hi:
                per_venster[naam].append(punt)
                reeksen[naam][k].append(punt)

    rnd = random.Random(7)
    uit = []
    for naam, _lo, _hi in VENSTERS:
        punten = per_venster[naam]
        if len(punten) < 200:
            continue
        lam = _beste_lam(punten)
        sleutels = list(reeksen[naam])
        trek = []
        for _ in range(trekkingen):
            pak = []
            for _ in range(len(sleutels)):
                pak.extend(reeksen[naam][rnd.choice(sleutels)])
            trek.append(_beste_lam(pak))
        trek.sort()
        uit.append({
            "venster": naam, "n": len(punten), "reeksen": len(sleutels),
            "lam": round(lam, 3),
            "lo95": round(trek[int(0.025 * len(trek))], 3),
            "hi95": round(trek[int(0.975 * len(trek))], 3),
            "brier_lam": round(_brier(punten, lam), 4),
            "brier_markt": round(_brier(punten, 0.0), 4),
            "brier_model": round(_brier(punten, 1.0), 4),
        })
    return uit


def toon_meting(rijen: list) -> None:
    print("\n  Hoeveel van het verschil met de markt is echt?\n")
    print("  venster           n   reeksen   lam*    95%-interval      "
          "brier: lam*  markt   model")
    for r in rijen:
        print(f"  {r['venster']:15s} {r['n']:5d} {r['reeksen']:8d} "
              f"{r['lam']:+7.3f}   [{r['lo95']:+.2f}, {r['hi95']:+.2f}]"
              f"      {r['brier_lam']:.4f}  {r['brier_markt']:.4f}  {r['brier_model']:.4f}")
    nul_erin = all(r["lo95"] <= 0 <= r["hi95"] for r in rijen)
    print()
    if nul_erin:
        print("  Nul valt in elk interval: het verschil tussen onze kans en de "
              "prijs voorspelt\n  in deze steekproef niets. LAMBDA staat daarom "
              f"op {LAMBDA}, de bovenkant van wat het\n  interval nog toelaat, en "
              "niet op 1.")
    else:
        print("  Er zit een venster bij waar nul buiten het interval valt. Dat is "
              "het moment om\n  LAMBDA bij te stellen — en om te kijken of het "
              "aan de conditionering ligt.")
    print("\n  Let op: deze cijfers gaan over gelogde kansen van vóór de "
          "conditionering op de\n  meting van vandaag. Zodra `waarneming` lang "
          "genoeg in het logboek staat is dit\n  de eerste som die je opnieuw "
          "wilt draaien.\n")


# ── Aan de portefeuille hangen ────────────────────────────────────────────────

def uit_portfolio(pad: Path = None) -> list:
    """De open posities uit portfolio.json als kandidaten voor de inzetregel.

    `model_win_prob` is de kans dat de positie wint en `current_bid` wat er nu
    voor betaald wordt; dat zijn precies de twee getallen die Kelly nodig heeft.
    De kans wordt eerst naar de markt toe gemengd."""
    pad = pad or (WORTEL / "portfolio.json")
    if not pad.exists():
        return []
    data = json.loads(pad.read_text())
    kandidaten = []
    for r in data.get("positions", []):
        if r.get("light") == "settled" or r.get("model_win_prob") is None:
            continue
        prijs = r.get("current_bid")
        kandidaten.append({
            "city": r.get("city"), "date": r.get("date"),
            "bracket": r.get("bracket"), "direction": r.get("direction"),
            "prijs": prijs,
            "model_win_kans": r.get("model_win_prob"),
            "win_kans": gemengd(r.get("model_win_prob"), prijs),
            "light": r.get("light"),
        })
    return kandidaten


def main(argv: list) -> int:
    bankroll = None
    lam = None
    dagverlies = None
    for i, a in enumerate(argv):
        if a == "--bankroll" and i + 1 < len(argv):
            bankroll = float(argv[i + 1])
        elif a == "--lam" and i + 1 < len(argv):
            lam = float(argv[i + 1])
        elif a == "--dagverlies" and i + 1 < len(argv):
            # Als positief getal. Blijft hij weg, dan is de dagstop niet
            # getoetst en zegt de uitvoer dat met zoveel woorden: de data-API
            # geeft een doorlopende gerealiseerde winst, niet die van vandaag.
            dagverlies = abs(float(argv[i + 1]))
    if lam is not None:
        globals()["LAMBDA"] = lam

    if "--meet" in argv:
        rijen = meet()
        if not rijen:
            print("  te weinig afgerekende reeksen in logs/signalen.csv")
            return 1
        toon_meting(rijen)
        return 0

    if bankroll is None:
        print(__doc__.strip().splitlines()[0])
        print("\n  Geef een bankroll mee: python3 bot/inzet.py --bankroll 500")
        print("  Of meet eerst of er iets te winnen valt: "
              "python3 bot/inzet.py --meet\n")
        return 1

    kandidaten = uit_portfolio()
    if not kandidaten:
        print("  Geen open posities in portfolio.json. Draai eerst "
              "python3 bot/portfolio.py")
        return 1
    plan = verdeel(kandidaten, bankroll, dagverlies=dagverlies)
    print(f"\n  Inzet bij een bankroll van {bankroll:.2f}, lambda {LAMBDA}, "
          f"{KELLY_FRACTIE:g} Kelly\n")
    if plan["waarschuwing"]:
        print(f"  let op: {plan['waarschuwing']}\n")
    if plan["stop"]:
        print(f"  STOP: {plan['stop']}\n")
        return 0
    print("  stad  datum       vak            richting  prijs   onze kans  "
          "gemengd  inzet")
    for r in plan["posities"] + plan["geweigerd"]:
        z = r["inzet"]
        prijs = "  -  " if r["prijs"] is None else f"{r['prijs']:.3f}"
        bedrag = f"{z['bedrag']:7.2f}" if z["bedrag"] else "      -"
        print(f"  {r['city']:5s} {r['date']} {str(r['bracket'])[:14]:14s} "
              f"{r['direction']:8s} {prijs}   {r['model_win_kans']:.3f}    "
              f"{r['win_kans']:.3f}  {bedrag}"
              + (f"   ({z['reden']})" if z["reden"] else ""))
    print(f"\n  Samen {plan['toegewezen']:.2f} over {len(plan['posities'])} "
          f"posities, plafond {MAX_BLOOTSTELLING:.0%} van het bankroll "
          f"({MAX_BLOOTSTELLING * bankroll:.2f}).")
    print("  Dit is een berekening, geen order. Er wordt niets geplaatst.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
