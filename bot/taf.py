#!/usr/bin/env python3
"""TAF als bevestigingslaag naast het ensemble.

Wat een TAF hier toevoegt
-------------------------
Een TAF is de luchthavenverwachting die voor datzelfde vliegveld wordt
uitgegeven waar de markt op afrekent, met een horizon van 24 tot 30 uur. Dat is
precies het koopvenster van strategie A (36 tot 12 uur voor sluiting), en het is
een oordeel dat niet uit hetzelfde ensemble komt als het onze.

Het bruikbare stuk zijn de TX- en TN-groepen: `TX24/1015Z` betekent een
verwachte maximumtemperatuur van 24 °C rond 15:00 UTC op de tiende. Dat is een
rechtstreekse voorspelling van het dagcijfer, geen omweg via bewolking.

Waar dit wel en niet werkt
--------------------------
Amerikaanse TAF's dragen die groepen meestal niet — daar is het gebruik anders.
Dat komt goed uit: voor de elf Amerikaanse steden mengt de app al de NWS
dagverwachting bij, en de achtendertig steden daarbuiten hadden zoiets niet. De
twee lagen vullen elkaar dus aan in plaats van dat ze elkaar overlappen.
`python3 bot/taf.py --dekking` laat zien welke stations de groepen werkelijk
meesturen.

Voorlopig alleen loggen
-----------------------
`TAF_GEWICHT` staat op nul. De TAF wordt vanaf nu wel gelogd in
`logs/taf_log.csv`, maar verandert nog geen enkel cijfer.

Dat is met opzet, en het is dezelfde les als in bot/inzet.py: daar bleek dat het
verschil tussen onze kans en de marktprijs in de gelogde reeks niets voorspelt.
Een tweede bron erbij zetten omdat hij plausibel klinkt is precies hoe je zoiets
niet merkt. `kalibratie.leer_taf` leert het gewicht per horizon zodra er veertig
gematchte dagen zijn, gekrompen richting nul — net zoals `leer_nws` dat richting
0,25 doet, alleen begint deze bij niets in plaats van bij een aanname.

Tot die veertig dagen er zijn kost deze laag één verzoek per stad per run en
verder niets.

De veranderingsgroepen
----------------------
FM, BECMG, TEMPO en PROB30/40 worden wel ontleed, maar alleen om de groep te
vinden die over het piekuur van de dag valt; bewolking en wind daaruit gaan mee
het logboek in als ruwe tekst. Ze gaan nergens in mee. Bewolking op het piekuur
is een aannemelijk signaal voor of een dag mee- of tegenvalt, maar het is niet te
kalibreren zolang het niet gemeten is, en dit logboek is waar dat begint.

Gebruik (vanuit de hoofdmap van de repo):

    python3 bot/taf.py                 alle steden, en schrijf logs/taf_log.csv
    python3 bot/taf.py --steden LON,AMS
    python3 bot/taf.py --dekking       welke stations TX/TN meesturen
"""
import json
import re
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weer
import logger

AWC = "https://aviationweather.gov/api/data/taf"
BUNDEL = 12              # stations per verzoek
TAF_GEWICHT = 0.0        # nul tot kalibratie.leer_taf hem verdient
TAF_MAX_VERSCHIL = 10.0  # graden; verder uit elkaar is het geen bijmenging maar ruis

KOP = ["gelogd_utc", "key", "station", "doel_datum", "lead", "tx_c", "tx_uur_utc",
       "tn_c", "tn_uur_utc", "geldig_van", "geldig_tot", "piekgroep", "ruw"]

# TX24/1015Z, TXM02/0206Z. De M staat voor een minteken.
_TX = re.compile(r"\bT([XN])(M?)(\d{1,2})/(\d{2})(\d{2})Z\b")
# 1012/1112 als geldigheid, en dezelfde vorm bij BECMG en TEMPO
_PERIODE = re.compile(r"\b(\d{2})(\d{2})/(\d{2})(\d{2})\b")
_FM = re.compile(r"\bFM(\d{2})(\d{2})(\d{2})\b")
_GROEP = re.compile(r"\b(FM\d{6}|BECMG|TEMPO|PROB[34]0)\b")


def _dagtijd(dag: int, uur: int, rond: datetime) -> datetime:
    """Een DDHH-stempel uit een TAF naar een echte tijd, met de maand van `rond`.

    TAF's noemen alleen de dag van de maand. Rond de maandwissel hoort 01 na 31
    te komen en niet elf maanden terug, dus we kiezen de maand waarvan de datum
    het dichtst bij het uitgiftemoment ligt."""
    extra, uur = divmod(uur, 24)      # /..24 komt voor als einde van een periode
    kandidaten = []
    for verschuif in (-1, 0, 1):
        maand = rond.month + verschuif
        jaar = rond.year + (maand - 1) // 12
        maand = (maand - 1) % 12 + 1
        try:
            # Niet met timedelta vanaf de eerste: dag 31 in februari zou dan
            # doorrollen naar maart in plaats van af te vallen.
            kandidaten.append(datetime(jaar, maand, dag, uur,
                                       tzinfo=rond.tzinfo) + timedelta(days=extra))
        except ValueError:
            continue                  # die dag bestaat niet in deze maand
    if not kandidaten:
        return None
    return min(kandidaten, key=lambda d: abs((d - rond).total_seconds()))


def ontleed(ruw: str, uitgifte: datetime = None) -> dict:
    """Eén TAF naar {"geldig_van", "geldig_tot", "tx": [...], "tn": [...],
    "groepen": [...]}.

    Losse functie omdat dit het enige stuk is dat offline te toetsen valt; zie
    bot/test_taf.py."""
    tekst = " ".join(str(ruw or "").split())
    if not tekst:
        return {}
    nu = uitgifte or datetime.now(timezone.utc)

    uit = {"geldig_van": None, "geldig_tot": None, "tx": [], "tn": [],
           "groepen": [], "ruw": tekst}

    perioden = _PERIODE.findall(tekst)
    if perioden:
        d1, u1, d2, u2 = perioden[0]
        van = _dagtijd(int(d1), int(u1), nu)
        # een TAF die tot 24:00 loopt schrijft dat als /..24
        tot = _dagtijd(int(d2), int(u2), nu)
        uit["geldig_van"], uit["geldig_tot"] = van, tot

    for soort, min_, waarde, dag, uur in _TX.findall(tekst):
        t = _dagtijd(int(dag), int(uur), nu)
        graden = -float(waarde) if min_ else float(waarde)
        (uit["tx"] if soort == "X" else uit["tn"]).append({"c": graden, "op": t})

    # De veranderingsgroepen, met de tekst die erbij hoort. Alleen om later te
    # kunnen zien welke groep over het piekuur viel; er wordt niets mee gerekend.
    merken = list(_GROEP.finditer(tekst))
    for i, m in enumerate(merken):
        eind = merken[i + 1].start() if i + 1 < len(merken) else len(tekst)
        blok = tekst[m.start():eind].strip()
        soort = m.group(1)
        van = tot = None
        if soort.startswith("FM"):
            f = _FM.search(soort)
            if f:
                van = _dagtijd(int(f.group(1)), int(f.group(2)), nu)
        else:
            p = _PERIODE.search(blok)
            if p:
                van = _dagtijd(int(p.group(1)), int(p.group(2)), nu)
                tot = _dagtijd(int(p.group(3)), int(p.group(4)), nu)
        uit["groepen"].append({"soort": soort, "van": van, "tot": tot,
                               "tekst": blok})
    return uit


def per_doeldag(ontleed_taf: dict, tznaam: str) -> dict:
    """De TX en TN per lokale doeldag van de stad.

    De tijdstempels in een TAF staan in UTC; de markt rekent af op een lokale
    kalenderdag. Voor Tokio scheelt dat negen uur, en zonder deze omrekening
    belandt een piek van 15:00 lokaal op de verkeerde dag."""
    tz = ZoneInfo(tznaam)
    uit = {}
    for soort in ("tx", "tn"):
        for g in ontleed_taf.get(soort) or []:
            if not g.get("op"):
                continue
            dag = g["op"].astimezone(tz).date().isoformat()
            e = uit.setdefault(dag, {})
            # bij meerdere groepen op dezelfde dag telt de hoogste TX en de
            # laagste TN: dat is wat de dag als geheel oplevert
            if soort == "tx":
                if e.get("tx") is None or g["c"] > e["tx"]:
                    e["tx"], e["tx_uur"] = g["c"], g["op"].strftime("%H")
            else:
                if e.get("tn") is None or g["c"] < e["tn"]:
                    e["tn"], e["tn_uur"] = g["c"], g["op"].strftime("%H")
    return uit


def piekgroep(ontleed_taf: dict, doeldag: str, tznaam: str, piekuur: int = 15) -> str:
    """De veranderingsgroep die over het piekuur van de doeldag valt, als tekst."""
    tz = ZoneInfo(tznaam)
    try:
        piek = datetime.fromisoformat(doeldag + "T00:00:00").replace(tzinfo=tz) \
            + timedelta(hours=piekuur)
    except ValueError:
        return ""
    piek = piek.astimezone(timezone.utc)
    beste = ""
    for g in ontleed_taf.get("groepen") or []:
        van, tot = g.get("van"), g.get("tot")
        if van and van <= piek and (tot is None or piek <= tot):
            beste = g["tekst"][:120]
    return beste


# ── Ophalen ───────────────────────────────────────────────────────────────────

def haal(stations: list, timeout: int = 60) -> dict:
    """{station: ruwe TAF-tekst}, in zo min mogelijk verzoeken.

    De API geeft json met een veld dat de ruwe tekst draagt, maar de naam
    daarvan is in de loop van de tijd veranderd. In plaats van er een te kiezen
    pakken we de eerste die op een TAF lijkt, en valt dat tegen dan lezen we de
    respons als kale tekst. Dezelfde aanpak als VELD_ALIAS in portfolio.py, en om
    dezelfde reden: een veldnaam die verschuift moet geen stille lege uitvoer
    geven."""
    uit = {}
    lijst = sorted(set(s for s in stations if s))
    for i in range(0, len(lijst), BUNDEL):
        deel = lijst[i:i + BUNDEL]
        url = (AWC + "?ids=" + urllib.parse.quote(",".join(deel))
               + "&format=json")
        tekst = ""
        try:
            tekst = weer._get(url, timeout=timeout)
        except Exception as ex:                    # noqa: BLE001
            print(f"  TAF mislukt voor {len(deel)} stations ({ex})")
            time.sleep(1)
            continue
        gevonden = False
        try:
            data = json.loads(tekst)
            for rij in data if isinstance(data, list) else []:
                st = rij.get("icaoId") or rij.get("station_id") or rij.get("stationId")
                ruw = None
                for veld in ("rawTAF", "rawOb", "raw_text", "rawText", "raw"):
                    if isinstance(rij.get(veld), str) and "TAF" in rij[veld].upper():
                        ruw = rij[veld]
                        break
                if st and ruw:
                    uit[st] = ruw
                    gevonden = True
        except ValueError:
            pass
        if not gevonden:
            # geen bruikbare json: de kale tekst per station uit elkaar halen
            for blok in re.split(r"\n(?=TAF\b|[A-Z]{4}\s+\d{6}Z)", tekst):
                for st in deel:
                    if st in blok:
                        uit.setdefault(st, " ".join(blok.split()))
        time.sleep(0.4)
    return uit


def meng_taf(dag: dict, tx_waarde, lead: int, gewicht: float = None) -> None:
    """De TAF-bijmenging op de verwachting, precies zoals meng_nws dat doet:
    verwachting en band schuiven samen op.

    Staat het gewicht op nul, dan gebeurt er niets. Dat is de huidige stand: de
    laag logt wel en rekent niet mee tot kalibratie.leer_taf hem verdiend heeft."""
    g = TAF_GEWICHT if gewicht is None else gewicht
    if tx_waarde is None or lead > 2 or not g:
        return
    if abs(tx_waarde - dag["verwachting"]) > TAF_MAX_VERSCHIL:
        return
    delta = g * (tx_waarde - dag["verwachting"])
    dag["verwachting"] += delta
    dag["p10"] += delta
    dag["p90"] += delta


# ── Loggen ────────────────────────────────────────────────────────────────────

def run(steden=None, pauze: float = 0.4) -> int:
    lijst = [s for s in weer.STEDEN
             if s.get("station") and (steden is None or s["key"] in steden)]
    ruwe = haal([s["station"] for s in lijst])
    nu = datetime.now(timezone.utc).isoformat(timespec="minutes")

    rijen, met_tx = [], 0
    for stad in lijst:
        ruw = ruwe.get(stad["station"])
        if not ruw:
            continue
        ont = ontleed(ruw)
        vandaag = datetime.now(ZoneInfo(stad["tz"])).date()
        per = per_doeldag(ont, stad["tz"])
        for dag, e in sorted(per.items()):
            lead = (date.fromisoformat(dag) - vandaag).days
            if not 0 <= lead <= 2:
                continue
            if e.get("tx") is not None:
                met_tx += 1
            rijen.append([
                nu, stad["key"], stad["station"], dag, lead,
                "" if e.get("tx") is None else f"{e['tx']:.1f}",
                e.get("tx_uur", ""),
                "" if e.get("tn") is None else f"{e['tn']:.1f}",
                e.get("tn_uur", ""),
                ont["geldig_van"].isoformat() if ont.get("geldig_van") else "",
                ont["geldig_tot"].isoformat() if ont.get("geldig_tot") else "",
                piekgroep(ont, dag, stad["tz"]),
                ont["ruw"][:300],
            ])
    if rijen:
        logger.schrijf(logger.logmap() / "taf_log.csv", KOP, rijen)
    print(f"TAF: {len(rijen)} regels over {len(set(r[1] for r in rijen))} steden, "
          f"{met_tx} met een TX-groep, {len(ruwe)} van de {len(lijst)} stations "
          f"gaf een TAF")
    if TAF_GEWICHT == 0:
        print("  gewicht staat op nul: dit logboek verandert nog geen enkel cijfer")
    return 0 if rijen else 1


def dekking(steden=None) -> int:
    """Welke stations TX/TN werkelijk meesturen. Amerikaanse TAF's meestal niet."""
    lijst = [s for s in weer.STEDEN
             if s.get("station") and (steden is None or s["key"] in steden)]
    ruwe = haal([s["station"] for s in lijst])
    met, zonder, leeg = [], [], []
    for stad in lijst:
        ruw = ruwe.get(stad["station"])
        if not ruw:
            leeg.append(stad["key"])
            continue
        ont = ontleed(ruw)
        (met if ont.get("tx") else zonder).append(stad["key"])
    print(f"\n  TX-groep aanwezig ({len(met)}): {', '.join(sorted(met))}")
    print(f"\n  geen TX-groep ({len(zonder)}): {', '.join(sorted(zonder))}")
    print(f"\n  geen TAF terug ({len(leeg)}): {', '.join(sorted(leeg))}\n")
    return 0


def main(argv: list) -> int:
    steden = None
    for i, a in enumerate(argv):
        if a == "--steden" and i + 1 < len(argv):
            steden = {s.strip().upper() for s in argv[i + 1].split(",") if s.strip()}
    if "--dekking" in argv:
        return dekking(steden)
    return run(steden)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
