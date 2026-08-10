#!/usr/bin/env python3
"""Fijnmazige stationsreeksen, voor steden waar het uurlijkse METAR te grof is.

Waarom dit nodig is
-------------------
De dagelijkse controle en de ondergrens in waarneming.py komen uit het
uurlijkse METAR-archief. Dat is één meting per uur, en op veel stations in hele
graden. Het echte dagmaximum valt zelden precies op het hele uur, dus die reeks
onderschat hem structureel — en juist op dat cijfer rekent Polymarket af.

Voor de Amerikaanse steden loste kalibratie.py dat al op met `verrijk_1min`: de
1-minuut ASOS-reeks van hetzelfde station, die een dagmax alleen omhoog bijstelt
als er genoeg metingen zijn en het verschil plausibel is. Die reeks bestaat
alleen voor de VS — ASOS is een Amerikaans netwerk. Dit bestand levert drie
alternatieven:

    hfmetar   de MADIS-stroom, hetzelfde IEM-eindpunt met report_type=1 erbij
    amedas    JMA, tien-minutenwaarden op 0,1 °C — voor Tokio
    nea       data.gov.sg, ongeveer per minuut op 0,1 °C — voor Singapore

De eerste is verreweg de goedkoopste: hetzelfde verzoek aan hetzelfde archief.
Voor stations die in MADIS zitten komen er vijf- of twintigminutenwaarden terug
in plaats van één melding per uur; voor stations die er niet in zitten verandert
er niets, en dan valt de verfijning vanzelf af op de eis van MIN_METINGEN. Welke
stations meedoen en wat het oplevert wijst `--hfmetar-dekking` uit. Blind
aanzetten heeft geen zin — zie de waarschuwing hieronder.

De andere twee staan aan voor Tokio en Singapore, en dat zijn niet toevallig de
twee steden die in portfolio.py `HOGE_ONZEKERHEID` dragen. De bias die daar zit
is deels een raster-tegen-stationprobleem, dat de kalibratie opvangt, en deels
bemonsteringsruis, en dat laatste is precies wat hier weggaat.

Verfijnen, niet vervangen
-------------------------
Het uitgangspunt blijft de gewone uurlijkse reeks. Deze bronnen mogen een
dagmaximum alleen omhoog bijstellen en een dagminimum alleen omlaag, en alleen
binnen `MARGE` graden en bij minstens `MIN_METINGEN` waarnemingen. Dat is
dezelfde bewaking als `verrijk_1min` en om dezelfde reden: valt de bron om of
geeft hij onzin, dan staat er hooguit het oude cijfer en nooit een verzonnen
cijfer. Een verfijning die de meting omlaag haalt zou betekenen dat METAR iets
gezien heeft wat de fijne reeks niet zag, en dat kan niet — dan is er iets anders
mis en houden we de veilige kant aan.

Fijner is niet vanzelf beter
----------------------------
De reeks die je wilt is die waarop *afgerekend* wordt, niet de fijnste die
bestaat. In weer.py staat bij `report_type=3` met zoveel woorden dat het de reeks
is die Wunderground toont, en dat was een keuze en geen toeval.

Voor de Amerikaanse stations lopen de twee samen: ASOS rekent het dagmaximum uit
vijfsecondegemiddelden en dat is wat de NWS publiceert, dus `verrijk_1min` haalt
je dichter bij de afrekening. Buiten de VS hangt het van de nationale dienst af.

En voor de ondergrens in waarneming.py telt het dubbel: `m` te laag schatten kapt
te weinig af en is onschuldig, `m` te hoog schatten streept een vak weg dat nog
kon vallen. Een piek meenemen die niet in de afrekening terechtkomt is precies
die gevaarlijke kant op. Daarom staat `fijn` per stad uit tot iemand gemeten
heeft dat het klopt.

Geen geraden stationsnummers
----------------------------
AMeDAS en NEA publiceren hun eigen stationstabel met coördinaten, dus het station
wordt op afstand tot de stad gezocht in plaats van als vast nummer in de code
gezet. Dat is dezelfde aanpak als `zoekStation` in index.html, en het voorkomt de
klassieke fout waarbij een verkeerd overgetypt nummer jarenlang de verkeerde stad
meet zonder dat iemand het merkt. De MADIS-stroom gebruikt gewoon de ICAO-code
die al in weer.STEDEN staat.

Gebruik:

    python3 bot/fijnmeting.py --stad TYO            welk station er gekozen wordt
    python3 bot/fijnmeting.py --stad TYO --dagen 3  de reeks van de laatste dagen
    python3 bot/fijnmeting.py --hfmetar-dekking     welke stations sub-uurlijks
                                                    melden, en wat het toevoegt
"""
import json
import math
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weer
import waarneming as W

MIN_METINGEN = 60        # per dag; onder dit aantal is de reeks te dun
MARGE = 4.0              # graden; verder van METAR af is het geen verfijning
POGINGEN = 3

# De report_type-waarden van IEM. 3 is de routinemelding van het hele uur, 4 de
# specials, 1 de MADIS-HFMETAR-stroom met sub-uurlijkse meldingen. Zie
# hfmetar_reeks hieronder voor waarom die drie samen worden opgevraagd.
HFMETAR_SOORTEN = ("1", "3", "4")

AMEDAS_TABEL = "https://www.jma.go.jp/bosai/amedas/const/amedastable.json"
AMEDAS_PUNT = "https://www.jma.go.jp/bosai/amedas/data/point"
AMEDAS_BLOKKEN = ("00", "03", "06", "09", "12", "15", "18", "21")
NEA = "https://api.data.gov.sg/v1/environment/air-temperature"

_tabel_bak: dict = {}
_station_bak: dict = {}


def _afstand(lat1, lon1, lat2, lon2) -> float:
    """Grootcirkelafstand in kilometers. Ruim genoeg om stations te ordenen."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


# ── JMA AMeDAS ────────────────────────────────────────────────────────────────

def amedas_tabel() -> dict:
    """De stationstabel van het JMA. Coördinaten staan er als [graden, minuten]."""
    if "amedas" not in _tabel_bak:
        _tabel_bak["amedas"] = weer._get_json(AMEDAS_TABEL, timeout=60)
    return _tabel_bak["amedas"]


def _amedas_coord(deel) -> float:
    return float(deel[0]) + float(deel[1]) / 60.0


def amedas_stations(stad: dict, hoeveel: int = 3) -> list:
    """De dichtstbijzijnde stations, dichtstbij eerst.

    Er komen er meerdere terug omdat niet elk AMeDAS-punt temperatuur meet; de
    beller probeert ze op volgorde tot er cijfers uit komen. Welk veld dat
    aangeeft is niet gedocumenteerd op een manier waar ik op wil bouwen, dus we
    kijken gewoon of er temperatuur in de respons zit."""
    tabel = amedas_tabel()
    kandidaten = []
    for code, rij in tabel.items():
        try:
            lat = _amedas_coord(rij["lat"])
            lon = _amedas_coord(rij["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        kandidaten.append((_afstand(stad["lat"], stad["lon"], lat, lon), code,
                           rij.get("kjName") or rij.get("enName") or code))
    kandidaten.sort()
    return kandidaten[:hoeveel]


def amedas_reeks(code: str, dagen: list, tznaam: str) -> dict:
    """{datum: {"max": .., "min": .., "n": ..}} in °C, uit de tien-minutenwaarden.

    Het JMA publiceert per blok van drie uur in lokale tijd. De sleutels in de
    respons zijn tijdstempels als YYYYMMDDHHMMSS, ook lokaal, dus de dag is er
    rechtstreeks uit te lezen."""
    per: dict = {}
    for dag in dagen:
        stempel = dag.strftime("%Y%m%d")
        for blok in AMEDAS_BLOKKEN:
            url = f"{AMEDAS_PUNT}/{code}/{stempel}_{blok}.json"
            data = None
            for poging in range(POGINGEN):
                try:
                    data = weer._get_json(url, timeout=45)
                    break
                except Exception:
                    # Een blok dat nog niet bestaat (later vandaag) is geen fout.
                    if poging + 1 == POGINGEN:
                        data = None
                    else:
                        time.sleep(1 + poging)
            if not isinstance(data, dict):
                continue
            for stempel_tijd, rij in data.items():
                temp = (rij or {}).get("temp")
                if not isinstance(temp, list) or not temp:
                    continue
                try:
                    waarde = float(temp[0])
                except (TypeError, ValueError):
                    continue
                d = stempel_tijd[:8]
                d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                e = per.setdefault(d, {"max": None, "min": None, "n": 0})
                e["n"] += 1
                if e["max"] is None or waarde > e["max"]:
                    e["max"] = waarde
                if e["min"] is None or waarde < e["min"]:
                    e["min"] = waarde
            time.sleep(0.2)
    return per


# ── NEA Singapore ─────────────────────────────────────────────────────────────

def nea_reeks(stad: dict, dagen: list) -> dict:
    """{datum: {"max": .., "min": .., "n": .., "station": ..}} in °C.

    data.gov.sg geeft per dag alle metingen van alle stations, met de
    stationstabel in dezelfde respons. Het station wordt daaruit op afstand
    gekozen — één verzoek per dag, geen aparte tabelaanroep."""
    per: dict = {}
    for dag in dagen:
        url = NEA + "?date=" + dag.isoformat()
        data = None
        for poging in range(POGINGEN):
            try:
                data = weer._get_json(url, timeout=60)
                break
            except Exception:
                time.sleep(1 + poging * 2)
        if not isinstance(data, dict):
            continue
        stations = ((data.get("metadata") or {}).get("stations")) or []
        beste, best_d = None, math.inf
        for st in stations:
            loc = st.get("location") or {}
            try:
                d = _afstand(stad["lat"], stad["lon"],
                             float(loc["latitude"]), float(loc["longitude"]))
            except (KeyError, TypeError, ValueError):
                continue
            if d < best_d:
                beste, best_d = st.get("id"), d
        if not beste:
            continue
        for item in data.get("items") or []:
            stempel = str(item.get("timestamp") or "")
            d = stempel[:10]
            if not d:
                continue
            for lezing in item.get("readings") or []:
                if lezing.get("station_id") != beste:
                    continue
                try:
                    waarde = float(lezing.get("value"))
                except (TypeError, ValueError):
                    continue
                e = per.setdefault(d, {"max": None, "min": None, "n": 0,
                                       "station": beste})
                e["n"] += 1
                if e["max"] is None or waarde > e["max"]:
                    e["max"] = waarde
                if e["min"] is None or waarde < e["min"]:
                    e["min"] = waarde
        time.sleep(0.3)
    return per


# ── MADIS HFMETAR: hetzelfde station, vaker gemeld ────────────────────────────

def hfmetar_reeks(stad: dict, dagen: list) -> dict:
    """{datum: {"max": .., "min": .., "n": ..}} in °C uit de sub-uurlijkse
    METAR-stroom van hetzelfde station.

    Dit is dezelfde bron en dezelfde parser als de gewone waarneming, alleen met
    `report_type=1` erbij. Voor stations die in MADIS zitten levert dat vijf- of
    twintigminutenwaarden in plaats van één melding per uur; voor stations die er
    niet in zitten komt er precies hetzelfde terug als anders.

    Dat laatste is meteen de bewaking. `verfijn` eist MIN_METINGEN waarnemingen
    per dag, en een station dat alleen het hele uur meldt haalt er hooguit
    vierentwintig. Zo'n station wordt dus vanzelf overgeslagen, zonder dat er
    ergens een lijst met "wie doet er mee" bijgehouden hoeft te worden.

    De routinemeldingen (3) en de specials (4) gaan mee in dezelfde aanroep. Dat
    scheelt niet alleen een verzoek: zonder 3 zou een dag waarop MADIS een gat
    heeft ineens uit minder metingen bestaan dan de gewone reeks, en dan zou de
    verfijning een lagere piek zien dan het cijfer dat hij moet bijstellen."""
    if not stad.get("station"):
        return {}
    rauw = W.haal_stations([stad["station"]], stad["tz"], dagen[0], dagen[-1],
                           soorten=HFMETAR_SOORTEN)
    per = {}
    for dag, e in (rauw.get(stad["station"]) or {}).items():
        if e.get("maxf") is None:
            continue
        per[dag] = {"max": weer.c_van_f(e["maxf"]),
                    "min": weer.c_van_f(e["minf"]),
                    "n": e["n"], "station": stad["station"]}
    return per


# ── De verfijning zelf ────────────────────────────────────────────────────────

def reeks_voor(stad: dict, dagen: list) -> dict:
    """De fijne reeks van een stad, of niets als er geen bron voor is."""
    fijn = stad.get("fijn")
    if fijn == "amedas":
        sleutel = stad["key"]
        if sleutel not in _station_bak:
            kandidaten = amedas_stations(stad)
            _station_bak[sleutel] = kandidaten
        for _afst, code, _naam in _station_bak[sleutel]:
            per = amedas_reeks(code, dagen, stad["tz"])
            if per:
                for e in per.values():
                    e["station"] = code
                return per
        return {}
    if fijn == "nea":
        return nea_reeks(stad, dagen)
    if fijn == "hfmetar":
        return hfmetar_reeks(stad, dagen)
    return {}


def verfijn(stad: dict, uit: dict, dagen: list, soort: str = "max") -> int:
    """Stelt de dagcijfers in `uit` bij met de fijne reeks. Geeft het aantal
    dagen dat aangepast is.

    De bewaking is dezelfde als verrijk_1min in kalibratie.py: genoeg metingen,
    de goede richting op, en niet verder dan MARGE. `uit` staat in de eenheid van
    de stad, de fijne reeks altijd in °C."""
    per = reeks_voor(stad, dagen)
    if not per:
        return 0
    naar_f = stad["eenheid"] == "F"
    aangepast = 0
    for dag, e in per.items():
        if dag not in uit or e["n"] < MIN_METINGEN:
            continue
        waarde = e["min"] if soort == "min" else e["max"]
        if waarde is None:
            continue
        if naar_f:
            waarde = weer.f_van_c(waarde)
        oud = uit[dag]
        if soort == "min":
            if oud - MARGE <= waarde < oud:
                uit[dag] = waarde
                aangepast += 1
        elif oud < waarde <= oud + MARGE:
            uit[dag] = waarde
            aangepast += 1
    return aangepast


def hfmetar_dekking(steden: list, aantal: int = 7) -> list:
    """Per station: levert MADIS sub-uurlijkse meldingen, en wat voegt dat toe?

    Twee aanroepen per tijdzone — één met alleen de routinemeldingen en één met
    de HFMETAR-stroom erbij — over dezelfde afgeronde dagen. Vandaag doet niet
    mee: een halve dag geeft een verschil dat over het tijdstip gaat en niet over
    de bron.

    Het getal dat telt is `gemiddeld`: hoeveel hoger het dagmaximum uitkomt met
    de fijne reeks. Positief is wat je verwacht, want het uurlijkse METAR mist de
    piek tussen twee meldingen door. Rond nul betekent dat het station niets
    extra's meldt of dat de piek toevallig op het hele uur viel. Structureel
    negatief kan niet — dan klopt er iets niet aan de vergelijking."""
    per_tz: dict = {}
    for s in steden:
        if s.get("bron") != "iem" or not s.get("station"):
            continue
        per_tz.setdefault(s["tz"], []).append(s)

    uit = []
    for tznaam, groep in per_tz.items():
        vandaag = datetime.now(ZoneInfo(tznaam)).date()
        d1 = vandaag - timedelta(days=aantal)
        d2 = vandaag - timedelta(days=1)
        stations = [s["station"] for s in groep]
        try:
            routine = W.haal_stations(stations, tznaam, d1, d2)
            hoogfreq = W.haal_stations(stations, tznaam, d1, d2,
                                       soorten=HFMETAR_SOORTEN)
        except Exception as ex:                    # noqa: BLE001
            print(f"    [let op] {tznaam} mislukt: {ex}")
            continue
        for s in groep:
            r = routine.get(s["station"]) or {}
            h = hoogfreq.get(s["station"]) or {}
            dagen = sorted(d for d in r if d in h
                           and d1.isoformat() <= d <= d2.isoformat())
            if not dagen:
                uit.append({"key": s["key"], "station": s["station"],
                            "dagen": 0, "n_routine": 0, "n_hf": 0,
                            "gemiddeld": None, "grootste": None,
                            "eenheid": s["eenheid"]})
                continue
            n_r = sum(r[d]["n"] for d in dagen) / len(dagen)
            n_h = sum(h[d]["n"] for d in dagen) / len(dagen)
            verschillen = []
            for d in dagen:
                if r[d]["maxf"] is None or h[d]["maxf"] is None:
                    continue
                delta = h[d]["maxf"] - r[d]["maxf"]      # allebei in °F
                verschillen.append(delta if s["eenheid"] == "F" else delta * 5 / 9)
            uit.append({
                "key": s["key"], "station": s["station"], "dagen": len(dagen),
                "n_routine": round(n_r, 1), "n_hf": round(n_h, 1),
                "gemiddeld": (sum(verschillen) / len(verschillen)
                              if verschillen else None),
                "grootste": max(verschillen) if verschillen else None,
                "eenheid": s["eenheid"],
            })
    return uit


def toon_dekking(rijen: list, aantal: int) -> None:
    """Het overzicht, plus de regel die je in weer.STEDEN zou plakken."""
    print(f"\n  MADIS HFMETAR-dekking over de laatste {aantal} afgeronde dagen\n")
    print("  stad  station  dagen  meldingen/dag      verschil op het dagmax")
    print("                        routine    hf      gemiddeld   grootste")
    kandidaten, fijner, niets, leeg = [], [], [], []
    for r in sorted(rijen, key=lambda x: -(x["gemiddeld"] or -9)):
        if not r["dagen"]:
            leeg.append(r["key"])
            print(f"  {r['key']:5s} {r['station']:7s}      -        -       -"
                  "            -          -   (geen data)")
            continue
        eh = "°F" if r["eenheid"] == "F" else "°C"
        gem = r["gemiddeld"]
        gr = r["grootste"]
        # Meer dan een vijfde extra meldingen telt als een echte sub-uurlijkse
        # stroom; daaronder is het ruis op het aantal specials.
        heeft_stroom = r["n_hf"] > r["n_routine"] * 1.2
        if not heeft_stroom:
            oordeel = "alleen uurlijks"
            niets.append(r["key"])
        elif gem is None or gem < 0.05:
            oordeel = "fijner, maar voegt niets toe"
            fijner.append(r["key"])
        else:
            oordeel = "KANDIDAAT"
            kandidaten.append((r["key"], gem, eh))
        print(f"  {r['key']:5s} {r['station']:7s}  {r['dagen']:4d}   "
              f"{r['n_routine']:7.1f}  {r['n_hf']:6.1f}   "
              f"{'' if gem is None else f'{gem:+7.2f}'}{eh}   "
              f"{'' if gr is None else f'{gr:+6.2f}'}   {oordeel}")

    print(f"\n  {len(kandidaten)} kandidaten, {len(fijner)} wel fijner maar zonder "
          f"effect, {len(niets)} alleen uurlijks, {len(leeg)} zonder data.")
    if kandidaten:
        print("\n  Kandidaten, en hoeveel het dagmaximum omhoog gaat:")
        for key, gem, eh in kandidaten:
            print(f"    {key}  {gem:+.2f}{eh}")
        print("\n  Zet bij die steden in bot/weer.py `\"fijn\": \"hfmetar\"` achter"
              " de regel.\n  fijnmeting.verfijn houdt de bewaking erop: alleen "
              "omhoog voor het maximum,\n  minstens "
              f"{MIN_METINGEN} metingen per dag en binnen {MARGE:g} graden.")
    print("\n  Let op voor je dat doet: fijner is alleen beter als de markt op "
          "het fijne\n  record afrekent en niet op de uurlijkse reeks. Voor de "
          "Amerikaanse stations\n  loopt dat samen (ASOS rekent het dagmax uit "
          "vijfsecondegemiddelden en dat is\n  wat de NWS publiceert); buiten de "
          "VS hangt het van de nationale dienst af.\n  Een te hoge ondergrens "
          "streept in waarneming.py een vak weg dat nog kon\n  vallen, en dat is "
          "de gevaarlijke kant op.\n")


def dagreeks(tznaam: str, aantal: int) -> list:
    vandaag = datetime.now(ZoneInfo(tznaam)).date()
    return [vandaag - timedelta(days=i) for i in range(aantal - 1, -1, -1)]


def main(argv: list) -> int:
    key, aantal = None, 1
    for i, a in enumerate(argv):
        if a == "--stad" and i + 1 < len(argv):
            key = argv[i + 1].upper()
        elif a == "--dagen" and i + 1 < len(argv):
            aantal = max(1, int(argv[i + 1]))

    if "--hfmetar-dekking" in argv:
        if "--dagen" not in argv:
            aantal = 7
        lijst = [s for s in weer.STEDEN if key is None or s["key"] == key]
        toon_dekking(hfmetar_dekking(lijst, aantal), aantal)
        return 0

    steden = [s for s in weer.STEDEN if s.get("fijn") and (key is None or s["key"] == key)]
    if not steden:
        print("Geen stad met een fijne bron gevonden. Steden met `fijn` in "
              "weer.STEDEN: " + ", ".join(s["key"] for s in weer.STEDEN if s.get("fijn")))
        return 1
    for stad in steden:
        print(f"\n  {stad['key']} ({stad['naam']}) · bron {stad['fijn']} · "
              f"METAR-station {stad['station']}")
        if stad["fijn"] == "amedas":
            try:
                for afst, code, naam in amedas_stations(stad):
                    print(f"    kandidaat {code} op {afst:.1f} km ({naam})")
            except Exception as ex:                # noqa: BLE001
                print(f"    stationstabel mislukt: {ex}")
        dagen = dagreeks(stad["tz"], aantal)
        try:
            per = reeks_voor(stad, dagen)
        except Exception as ex:                    # noqa: BLE001
            print(f"    reeks mislukt: {ex}")
            continue
        if not per:
            print("    geen cijfers terug")
            continue

        # METAR ernaast: dat is het cijfer waar de verfijning op ingrijpt, dus
        # zonder die kolom is niet te zien of de moeite iets oplevert.
        try:
            metar = weer.fetch_station_maxen(stad["station"], stad["tz"],
                                             dagen[0], dagen[-1])
        except Exception as ex:                    # noqa: BLE001
            print(f"    METAR ernaast leggen mislukt: {ex}")
            metar = {}
        print("      datum        METAR max   fijn max   verschil   n     station")
        opgeteld, geteld = 0.0, 0
        for dag in sorted(per):
            e = per[dag]
            mm = metar.get(dag)
            ruw = None if not mm or mm["maxf"] is None else weer.c_van_f(mm["maxf"])
            if ruw is None:
                print(f"      {dag}          -     {e['max']:6.1f}°C        -    "
                      f"{e['n']:4d}  {e.get('station', '?')}")
                continue
            verschil = e["max"] - ruw
            if e["n"] >= MIN_METINGEN:
                opgeteld += verschil
                geteld += 1
            print(f"      {dag}     {ruw:6.1f}°C   {e['max']:6.1f}°C   "
                  f"{verschil:+6.2f}    {e['n']:4d}  {e.get('station', '?')}")
        if geteld:
            print(f"\n      gemiddeld {opgeteld / geteld:+.2f} °C over {geteld} "
                  f"dagen met genoeg metingen.")
            print("      Positief is wat je verwacht: het uurlijkse METAR mist de "
                  "piek tussen\n      twee meldingen door. Structureel negatief "
                  "betekent dat je het verkeerde\n      station te pakken hebt.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
