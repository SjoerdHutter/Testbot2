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
als er genoeg metingen zijn en het verschil plausibel is. Dit bestand doet
precies hetzelfde voor twee steden waar die reeks niet bestaat:

    Tokio       JMA AMeDAS, tien-minutenwaarden op 0,1 °C
    Singapore   NEA via data.gov.sg, ongeveer per minuut op 0,1 °C

Dat zijn niet toevallig de twee steden die in portfolio.py `HOGE_ONZEKERHEID`
dragen. De bias die daar zit is deels een raster-tegen-stationprobleem, dat de
kalibratie opvangt, en deels bemonsteringsruis, en dat laatste is precies wat
hier weggaat.

Verfijnen, niet vervangen
-------------------------
Het uitgangspunt blijft METAR. Deze reeksen mogen een dagmaximum alleen omhoog
bijstellen en een dagminimum alleen omlaag, en alleen binnen `MARGE` graden en
bij minstens `MIN_METINGEN` waarnemingen. Dat is dezelfde bewaking als
`verrijk_1min` en om dezelfde reden: valt de bron om of geeft hij onzin, dan
staat er hooguit het oude cijfer en nooit een verzonnen cijfer. Een verfijning
die de meting omlaag haalt zou betekenen dat METAR iets gezien heeft wat de
fijne reeks niet zag, en dat kan niet — dan is er iets anders mis en houden we
de veilige kant aan.

Geen geraden stationsnummers
----------------------------
Allebei de bronnen publiceren hun eigen stationstabel met coördinaten, dus het
station wordt op afstand tot de stad gezocht in plaats van als vast nummer in de
code gezet. Dat is dezelfde aanpak als `zoekStation` in index.html, en het
voorkomt de klassieke fout waarbij een verkeerd overgetypt nummer jarenlang de
verkeerde stad meet zonder dat iemand het merkt.

Gebruik:

    python3 bot/fijnmeting.py --stad TYO            welk station er gekozen wordt
    python3 bot/fijnmeting.py --stad TYO --dagen 3  de reeks van de laatste dagen
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

MIN_METINGEN = 60        # per dag; onder dit aantal is de reeks te dun
MARGE = 4.0              # graden; verder van METAR af is het geen verfijning
POGINGEN = 3

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
