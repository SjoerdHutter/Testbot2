#!/usr/bin/env python3
"""
weer.py  ·  Maximumtemperatuur voor 15 wereldsteden, met zekerheid en fouthistorie.

Gebruik:
  python3 weer.py                 dagelijkse run: verifieren, voorspellen, dashboard verversen
  python3 weer.py --backtest 45   eenmalig: fouthistorie opbouwen over de laatste N dagen
  python3 weer.py --serve         dashboard serveren voor je telefoon (zelfde wifi)
  python3 weer.py --stats         volledige fouttabel in de terminal

Alleen de Python standaardbibliotheek, geen pip installs. Python 3.9 of nieuwer.

Bronnen:
  Voorspelling : Open-Meteo ensemble (ECMWF IFS + AIFS, NCEP GEFS, ICON; ~173 leden)
  Backtest     : Open-Meteo previous-runs API (echte leadtime-runs van 1 en 2 dagen terug)
  Werkelijkheid: uurlijkse METAR-waarnemingen van het vliegveldstation dat Wunderground
                 voor die stad toont (via het archief van Iowa State / IEM)
  WU-proxy     : wttr.in (data van The Weather Company, het moederbedrijf van Wunderground)
"""

import csv
import json
import math
import socket
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    sys.exit("Python 3.9 of nieuwer is nodig (de module zoneinfo ontbreekt).")

MAP          = Path(__file__).parent
CSV_BESTAND  = MAP / "weer_forecasts.csv"
JS_BESTAND   = MAP / "weer_data.js"
JSON_BESTAND = MAP / "weer_data.json"

# ── Steden ────────────────────────────────────────────────────────────────────
# station  = de METAR-code van het vliegveld dat Wunderground voor die stad toont.
#            Amerikaanse stations gaan zonder K het IEM-archief in (LGA, LAX, SFO),
#            alle andere met de volledige ICAO-code.
# lat/lon  = de coordinaten van dat vliegveld (niet het stadscentrum), zodat het
#            model zo dicht mogelijk op het resolutiestation zit.
# eenheid  = wat er lokaal gebruikt wordt: F alleen voor de drie Amerikaanse steden.
# Twijfelgevallen, pas gerust aan: Bangkok kan ook VTBS (Suvarnabhumi) zijn en
# Seoul ook RKSI (Incheon); vergelijk een paar dagen met wunderground.com.
STEDEN = [
    {"key": "NYC", "naam": "New York", "station": "LGA", "lat": 40.7794, "lon": -73.8803, "tz": "America/New_York", "eenheid": "F", "bron": "iem"},
    {"key": "CHI", "naam": "Chicago", "station": "ORD", "lat": 41.9602, "lon": -87.9316, "tz": "America/Chicago", "eenheid": "F", "bron": "iem"},
    {"key": "MIA", "naam": "Miami", "station": "MIA", "lat": 25.7880, "lon": -80.3169, "tz": "America/New_York", "eenheid": "F", "bron": "iem"},
    {"key": "LAX", "naam": "Los Angeles", "station": "LAX", "lat": 33.9382, "lon": -118.3865, "tz": "America/Los_Angeles", "eenheid": "F", "bron": "iem"},
    {"key": "SFO", "naam": "San Francisco", "station": "SFO", "lat": 37.6190, "lon": -122.3749, "tz": "America/Los_Angeles", "eenheid": "F", "bron": "iem"},
    {"key": "SEA", "naam": "Seattle", "station": "SEA", "lat": 47.4447, "lon": -122.3144, "tz": "America/Los_Angeles", "eenheid": "F", "bron": "iem"},
    {"key": "DEN", "naam": "Denver", "station": "BKF", "lat": 39.7017, "lon": -104.7517, "tz": "America/Denver", "eenheid": "F", "bron": "iem"},
    {"key": "DAL", "naam": "Dallas", "station": "DAL", "lat": 32.8471, "lon": -96.8518, "tz": "America/Chicago", "eenheid": "F", "bron": "iem"},
    {"key": "HOU", "naam": "Houston", "station": "HOU", "lat": 29.6375, "lon": -95.2824, "tz": "America/Chicago", "eenheid": "F", "bron": "iem"},
    {"key": "AUS", "naam": "Austin", "station": "AUS", "lat": 30.1830, "lon": -97.6799, "tz": "America/Chicago", "eenheid": "F", "bron": "iem"},
    {"key": "ATL", "naam": "Atlanta", "station": "ATL", "lat": 33.6301, "lon": -84.4418, "tz": "America/New_York", "eenheid": "F", "bron": "iem"},
    {"key": "LON", "naam": "Londen", "station": "EGLC", "lat": 51.5053, "lon": 0.0553, "tz": "Europe/London", "eenheid": "C", "bron": "iem"},
    {"key": "PAR", "naam": "Parijs", "station": "LFPB", "lat": 48.9672, "lon": 2.4272, "tz": "Europe/Paris", "eenheid": "C", "bron": "iem"},
    {"key": "AMS", "naam": "Amsterdam", "station": "EHAM", "lat": 52.3154, "lon": 4.7902, "tz": "Europe/Amsterdam", "eenheid": "C", "bron": "iem"},
    {"key": "MAD", "naam": "Madrid", "station": "LEMD", "lat": 40.4667, "lon": -3.5556, "tz": "Europe/Madrid", "eenheid": "C", "bron": "iem"},
    {"key": "MIL", "naam": "Milaan", "station": "LIMC", "lat": 45.6300, "lon": 8.7231, "tz": "Europe/Rome", "eenheid": "C", "bron": "iem"},
    {"key": "MUC", "naam": "München", "station": "EDDM", "lat": 48.3583, "lon": 11.8092, "tz": "Europe/Berlin", "eenheid": "C", "bron": "iem"},
    {"key": "WAW", "naam": "Warschau", "station": "EPWA", "lat": 52.1628, "lon": 20.9611, "tz": "Europe/Warsaw", "eenheid": "C", "bron": "iem"},
    {"key": "HEL", "naam": "Helsinki", "station": "EFHK", "lat": 60.3172, "lon": 24.9633, "tz": "Europe/Helsinki", "eenheid": "C", "bron": "iem"},
    {"key": "ANK", "naam": "Ankara", "station": "LTAC", "lat": 40.1281, "lon": 32.9951, "tz": "Europe/Istanbul", "eenheid": "C", "bron": "iem"},
    {"key": "IST", "naam": "Istanbul", "station": "LTFM", "lat": 41.2629, "lon": 28.7413, "tz": "Europe/Istanbul", "eenheid": "C", "bron": "iem"},
    {"key": "MOW", "naam": "Moskou", "station": "UUWW", "lat": 55.5915, "lon": 37.2615, "tz": "Europe/Moscow", "eenheid": "C", "bron": "iem"},
    {"key": "TYO", "naam": "Tokio", "station": "RJTT", "lat": 35.5533, "lon": 139.7811, "tz": "Asia/Tokyo", "eenheid": "C", "bron": "iem"},
    {"key": "SEL", "naam": "Seoul", "station": "RKSI", "lat": 37.4667, "lon": 126.4500, "tz": "Asia/Seoul", "eenheid": "C", "bron": "iem"},
    {"key": "PUS", "naam": "Busan", "station": "RKPK", "lat": 35.1795, "lon": 128.9382, "tz": "Asia/Seoul", "eenheid": "C", "bron": "iem"},
    {"key": "TPE", "naam": "Taipei", "station": "RCSS", "lat": 25.0694, "lon": 121.5517, "tz": "Asia/Taipei", "eenheid": "C", "bron": "iem"},
    {"key": "PEK", "naam": "Peking", "station": "ZBAA", "lat": 40.0741, "lon": 116.5870, "tz": "Asia/Shanghai", "eenheid": "C", "bron": "iem"},
    {"key": "SHA", "naam": "Shanghai", "station": "ZSPD", "lat": 31.1167, "lon": 121.7667, "tz": "Asia/Shanghai", "eenheid": "C", "bron": "iem"},
    {"key": "CAN", "naam": "Guangzhou", "station": "ZGGG", "lat": 23.3964, "lon": 113.3008, "tz": "Asia/Shanghai", "eenheid": "C", "bron": "iem"},
    {"key": "SZX", "naam": "Shenzhen", "station": "ZGSZ", "lat": 22.5500, "lon": 114.1000, "tz": "Asia/Shanghai", "eenheid": "C", "bron": "iem"},
    {"key": "CTU", "naam": "Chengdu", "station": "ZUUU", "lat": 30.6667, "lon": 104.0167, "tz": "Asia/Shanghai", "eenheid": "C", "bron": "iem"},
    {"key": "CKG", "naam": "Chongqing", "station": "ZUCK", "lat": 29.5200, "lon": 106.4800, "tz": "Asia/Shanghai", "eenheid": "C", "bron": "iem"},
    {"key": "WUH", "naam": "Wuhan", "station": "ZHHH", "lat": 30.6200, "lon": 114.1300, "tz": "Asia/Shanghai", "eenheid": "C", "bron": "iem"},
    {"key": "TAO", "naam": "Qingdao", "station": "ZSQD", "lat": 36.0667, "lon": 120.3333, "tz": "Asia/Shanghai", "eenheid": "C", "bron": "iem"},
    {"key": "TNA", "naam": "Jinan", "station": "ZSJN", "lat": 36.8555, "lon": 117.2060, "tz": "Asia/Shanghai", "eenheid": "C", "bron": "era5"},
    {"key": "CGO", "naam": "Zhengzhou", "station": "ZHCC", "lat": 34.7167, "lon": 113.6500, "tz": "Asia/Shanghai", "eenheid": "C", "bron": "iem"},
    {"key": "HKG", "naam": "Hongkong", "station": "VHHH", "lat": 22.3094, "lon": 113.9219, "tz": "Asia/Hong_Kong", "eenheid": "C", "bron": "hko"},
    {"key": "MNL", "naam": "Manila", "station": "RPLL", "lat": 14.5069, "lon": 121.0042, "tz": "Asia/Manila", "eenheid": "C", "bron": "iem"},
    {"key": "KUL", "naam": "Kuala Lumpur", "station": "WMKK", "lat": 2.7167, "lon": 101.7000, "tz": "Asia/Kuala_Lumpur", "eenheid": "C", "bron": "iem"},
    {"key": "SIN", "naam": "Singapore", "station": "WSSS", "lat": 1.3667, "lon": 103.9833, "tz": "Asia/Singapore", "eenheid": "C", "bron": "iem"},
    {"key": "KHI", "naam": "Karachi", "station": "OPKC", "lat": 24.8456, "lon": 67.1614, "tz": "Asia/Karachi", "eenheid": "C", "bron": "iem"},
    {"key": "LKO", "naam": "Lucknow", "station": "VILK", "lat": 26.7606, "lon": 80.8893, "tz": "Asia/Kolkata", "eenheid": "C", "bron": "iem"},
    {"key": "JED", "naam": "Jeddah", "station": "OEJN", "lat": 21.6598, "lon": 39.1222, "tz": "Asia/Riyadh", "eenheid": "C", "bron": "iem"},
    {"key": "TLV", "naam": "Tel Aviv", "station": "LLBG", "lat": 32.0114, "lon": 34.8867, "tz": "Asia/Jerusalem", "eenheid": "C", "bron": "iem"},
    {"key": "TOR", "naam": "Toronto", "station": "CYYZ", "lat": 43.6772, "lon": -79.6306, "tz": "America/Toronto", "eenheid": "C", "bron": "iem"},
    {"key": "MEX", "naam": "Mexico-Stad", "station": "MMMX", "lat": 19.4363, "lon": -99.0721, "tz": "America/Mexico_City", "eenheid": "C", "bron": "iem"},
    {"key": "PTY", "naam": "Panama-Stad", "station": "MPMG", "lat": 8.9833, "lon": -79.5167, "tz": "America/Panama", "eenheid": "C", "bron": "iem"},
    {"key": "BUE", "naam": "Buenos Aires", "station": "SAEZ", "lat": -34.8222, "lon": -58.5358, "tz": "America/Argentina/Buenos_Aires", "eenheid": "C", "bron": "iem"},
    {"key": "SAO", "naam": "São Paulo", "station": "SBGR", "lat": -23.4321, "lon": -46.4695, "tz": "America/Sao_Paulo", "eenheid": "C", "bron": "iem"},
    {"key": "CPT", "naam": "Kaapstad", "station": "FACT", "lat": -33.9667, "lon": 18.6000, "tz": "Africa/Johannesburg", "eenheid": "C", "bron": "iem"},
    {"key": "WLG", "naam": "Wellington", "station": "NZWN", "lat": -41.3272, "lon": 174.8053, "tz": "Pacific/Auckland", "eenheid": "C", "bron": "iem"},
]
STAD_OP_KEY = {s["key"]: s for s in STEDEN}

ENSEMBLE_MODELLEN = "ecmwf_ifs025,ecmwf_aifs025,ncep_gefs025,icon_seamless"
BACKTEST_MODELLEN = "ecmwf_ifs025,icon_seamless,gfs_seamless"

VELDEN = [
    "datum", "stad", "station", "eenheid", "horizon",
    "ens_mean", "bias", "adj_mean", "p10", "p90", "spreiding", "n_leden",
    "wu_proxy", "actual_max", "fout", "bron", "gelogd_op",
]

# Correctieregels (zelfde principe als de inspiratiebot): pas een lerende
# biascorrectie pas toe zodra er genoeg geverifieerde punten zijn.
MIN_LIVE_VOOR_CORRECTIE     = 5
MIN_BACKTEST_VOOR_CORRECTIE = 8

# ── Kleine helpers ────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 45) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "weerbot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()

def _get_json(url: str, timeout: int = 45):
    return json.loads(_get(url, timeout))

def f_van_c(c: float) -> float:
    return c * 9 / 5 + 32

def c_van_f(f: float) -> float:
    return (f - 32) * 5 / 9

def nl(x: float, dec: int = 1) -> str:
    """Nederlandse notatie: komma als decimaalteken."""
    return f"{x:.{dec}f}".replace(".", ",")

def pctl(gesorteerd: list, q: float) -> float:
    """Percentiel met lineaire interpolatie; verwacht een gesorteerde lijst."""
    k = (len(gesorteerd) - 1) * q
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return gesorteerd[int(k)]
    return gesorteerd[f] + (gesorteerd[c] - gesorteerd[f]) * (k - f)

def vandaag_in(tz: str) -> date:
    """De lokale datum in die stad, niet de datum bij jou thuis."""
    return datetime.now(ZoneInfo(tz)).date()

# ── Databronnen ───────────────────────────────────────────────────────────────

def fetch_ensemble(stad: dict) -> dict:
    """Per datum een gesorteerde lijst van de daghoogste temperatuur van elk
    ensemblelid (~173), in de lokale eenheid van de stad."""
    tz = urllib.parse.quote(stad["tz"])
    url = (
        "https://ensemble-api.open-meteo.com/v1/ensemble"
        f"?latitude={stad['lat']}&longitude={stad['lon']}"
        f"&hourly=temperature_2m&models={ENSEMBLE_MODELLEN}"
        f"&temperature_unit=celsius&forecast_days=4&timezone={tz}"
    )
    d = _get_json(url)
    h = d["hourly"]
    per_dag: dict = {}
    for i, t in enumerate(h["time"]):
        per_dag.setdefault(t[:10], []).append(i)

    uit: dict = {}
    for dag, idx in per_dag.items():
        maxen = []
        for sleutel, reeks in h.items():
            if not sleutel.startswith("temperature_2m"):
                continue
            waarden = [reeks[i] for i in idx if reeks[i] is not None]
            if len(waarden) >= 12:
                maxen.append(max(waarden))
        if maxen:
            if stad["eenheid"] == "F":
                maxen = [f_van_c(x) for x in maxen]
            uit[dag] = sorted(maxen)
    return uit


def fetch_wu_proxy(stad: dict) -> dict:
    """De daghoogste voorspelling van The Weather Company (wttr.in), als proxy
    voor wat Wunderground zelf verwacht. Mag stilletjes falen."""
    try:
        d = _get_json(f"https://wttr.in/{stad['lat']},{stad['lon']}?format=j1", timeout=12)
        sleutel = "maxtempF" if stad["eenheid"] == "F" else "maxtempC"
        return {w["date"]: float(w[sleutel]) for w in d.get("weather", [])}
    except Exception:
        return {}


def fetch_backtest(stad: dict, dagen: int) -> dict:
    """Echte leadtime-voorspellingen uit het verleden via de previous-runs API.
    previous_day1 = wat de run van 1 dag eerder voorspelde (horizon 'morgen'),
    previous_day2 = 2 dagen eerder (horizon 'overmorgen').
    Geeft {(datum, horizon): gemiddelde_daghoogste} in de lokale eenheid."""
    tz = urllib.parse.quote(stad["tz"])
    basis = (
        "https://previous-runs-api.open-meteo.com/v1/forecast"
        f"?latitude={stad['lat']}&longitude={stad['lon']}"
        "&hourly=temperature_2m_previous_day1,temperature_2m_previous_day2"
        f"&temperature_unit=celsius&past_days={dagen}&forecast_days=1&timezone={tz}"
    )
    try:
        d = _get_json(basis + f"&models={BACKTEST_MODELLEN}")
    except Exception:
        d = _get_json(basis)  # terugval op het standaardmodel

    h = d["hourly"]
    per_dag: dict = {}
    for i, t in enumerate(h["time"]):
        per_dag.setdefault(t[:10], []).append(i)

    grens = vandaag_in(stad["tz"]).isoformat()
    uit: dict = {}
    for horizon in (1, 2):
        tag = f"previous_day{horizon}"
        reeksen = [v for k, v in h.items() if tag in k]
        for dag, idx in per_dag.items():
            if dag >= grens:
                continue  # alleen afgeronde dagen
            maxen = []
            for reeks in reeksen:
                waarden = [reeks[i] for i in idx if reeks[i] is not None]
                if len(waarden) >= 12:
                    maxen.append(max(waarden))
            if maxen:
                g = sum(maxen) / len(maxen)
                if stad["eenheid"] == "F":
                    g = f_van_c(g)
                uit[(dag, horizon)] = g
    return uit


def fetch_station_maxen(station: str, tznaam: str, d1: date, d2: date) -> dict:
    """Werkelijk gemeten daghoogste per lokale kalenderdag, uit de uurlijkse
    METAR-waarnemingen van het station (report_type=3, dus geen 5-minuten
    metingen: precies de reeks die Wunderground registreert).
    Geeft {datum: {"maxf": .., "n": .., "laatste_uur": ..}}."""
    tz = urllib.parse.quote(tznaam)
    d2p = d2 + timedelta(days=1)  # eindgrens ruim nemen
    url = (
        "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
        f"?station={station}&data=tmpf"
        f"&year1={d1.year}&month1={d1.month}&day1={d1.day}"
        f"&year2={d2p.year}&month2={d2p.month}&day2={d2p.day}"
        f"&tz={tz}&format=comma&latlon=no&missing=M&trace=T&direct=no&report_type=3"
    )
    tekst = ""
    for poging in range(3):
        try:
            tekst = _get(url, timeout=90)
            if station + "," in tekst:
                break
        except Exception:
            pass
        time.sleep(2 + poging * 3)  # even wachten en opnieuw proberen
    if station + "," not in tekst:
        print(f"      [let op] geen data van {station}, probeer het later opnieuw")
        return {}
    per_dag: dict = {}
    for regel in tekst.splitlines():
        if not regel.startswith(station + ","):
            continue
        delen = regel.split(",")
        if len(delen) < 3:
            continue
        try:
            t = float(delen[2])
        except ValueError:
            continue
        stamp = delen[1]
        dag = stamp[:10]
        try:
            uur = int(stamp[11:13])
        except (ValueError, IndexError):
            uur = 0
        e = per_dag.setdefault(dag, {"maxf": -999.0, "n": 0, "laatste_uur": 0})
        e["n"] += 1
        e["laatste_uur"] = max(e["laatste_uur"], uur)
        if t > e["maxf"]:
            e["maxf"] = t
    return per_dag

# ── CSV opslag ────────────────────────────────────────────────────────────────

def laad_rijen() -> dict:
    rijen: dict = {}
    if CSV_BESTAND.exists():
        with open(CSV_BESTAND, newline="") as f:
            for r in csv.DictReader(f):
                rijen[(r["datum"], r["stad"], r["horizon"], r["bron"])] = r
    return rijen

def bewaar_rijen(rijen: dict) -> None:
    with open(CSV_BESTAND, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=VELDEN, extrasaction="ignore")
        w.writeheader()
        for k in sorted(rijen):
            w.writerow(rijen[k])

# ── Leren en meten ────────────────────────────────────────────────────────────

def leer_correcties(rijen: dict) -> dict:
    """Per stad de lerende biascorrectie, op basis van de ruwe fout
    (werkelijk minus ruwe modelgemiddelde). Live metingen wegen zwaarder dan
    de backtest zodra er genoeg zijn. Geeft {stadkey: (offset, basis, n)}."""
    per: dict = {}
    for r in rijen.values():
        if not r["actual_max"] or not r["ens_mean"]:
            continue
        fout = float(r["actual_max"]) - float(r["ens_mean"])
        per.setdefault(r["stad"], {"live": [], "backtest": []})[r["bron"]].append(fout)
    corr: dict = {}
    for stad, d in per.items():
        if len(d["live"]) >= MIN_LIVE_VOOR_CORRECTIE:
            corr[stad] = (sum(d["live"]) / len(d["live"]), "live", len(d["live"]))
        elif len(d["backtest"]) >= MIN_BACKTEST_VOOR_CORRECTIE:
            corr[stad] = (sum(d["backtest"]) / len(d["backtest"]), "backtest", len(d["backtest"]))
    return corr


def statistieken(rijen: dict) -> dict:
    """Fouthistorie per stad en per horizon, uit alle geverifieerde rijen.
    bias     = gemiddelde ruwe fout (positief: model was te koud)
    mae      = gemiddelde absolute fout van het ruwe model
    mae_corr = gemiddelde absolute fout nadat de systematische bias eruit is
               (schatting van wat de gecorrigeerde voorspelling doet)."""
    per: dict = {}
    for r in rijen.values():
        if not r["actual_max"] or not r["ens_mean"]:
            continue
        e = float(r["actual_max"]) - float(r["ens_mean"])
        per.setdefault(r["stad"], {}).setdefault(r["horizon"], []).append(e)
    uit: dict = {}
    for stad, horizonnen in per.items():
        uit[stad] = {}
        for hor, fouten in horizonnen.items():
            b = sum(fouten) / len(fouten)
            uit[stad][hor] = {
                "n":        len(fouten),
                "bias":     round(b, 1),
                "mae":      round(sum(abs(x) for x in fouten) / len(fouten), 1),
                "mae_corr": round(sum(abs(x - b) for x in fouten) / len(fouten), 1),
            }
    return uit

# ── Stappen ───────────────────────────────────────────────────────────────────

def los_op(rijen: dict) -> int:
    """Vul werkelijke daghoogsten in voor elke afgeronde dag zonder meting.
    Een IEM-aanvraag per station dekt in een keer alle openstaande datums."""
    per_station: dict = {}
    for k, r in rijen.items():
        if r["actual_max"]:
            continue
        stad = STAD_OP_KEY.get(r["stad"])
        if not stad:
            continue
        if date.fromisoformat(r["datum"]) >= vandaag_in(stad["tz"]):
            continue  # die dag loopt daar nog
        per_station.setdefault((stad["station"], stad["tz"]), []).append(k)

    ingevuld = 0
    for (station, tznaam), keys in sorted(per_station.items()):
        datums = [date.fromisoformat(rijen[k]["datum"]) for k in keys]
        print(f"    metingen ophalen: {station} ({min(datums)} tot {max(datums)})")
        maxen = fetch_station_maxen(station, tznaam, min(datums), max(datums))
        time.sleep(0.7)  # het archief niet overvragen
        for k in keys:
            r = rijen[k]
            e = maxen.get(r["datum"])
            # Alleen accepteren als de dag redelijk compleet gemeten is.
            if not e or e["n"] < 8 or e["laatste_uur"] < 18:
                continue
            stad = STAD_OP_KEY[r["stad"]]
            a = e["maxf"] if stad["eenheid"] == "F" else c_van_f(e["maxf"])
            r["actual_max"] = f"{a:.1f}"
            try:
                r["fout"] = f"{a - float(r['adj_mean']):+.1f}"
            except (ValueError, TypeError):
                r["fout"] = ""
            ingevuld += 1
    return ingevuld


def voorspel(rijen: dict, corr: dict) -> None:
    """Log voor elke stad de voorspelling voor vandaag, morgen en overmorgen
    (lokale datums), met zekerheidsband en toegepaste correctie."""
    nu = datetime.now().strftime("%Y-%m-%d %H:%M")
    for stad in STEDEN:
        try:
            per_dag = fetch_ensemble(stad)
        except Exception as ex:
            print(f"    {stad['naam']:<15} ensemble mislukt: {ex}")
            continue
        wu = fetch_wu_proxy(stad)
        time.sleep(0.3)  # wttr.in niet overvragen

        offset, basis, n_corr = corr.get(stad["key"], (0.0, "geen", 0))
        v0 = vandaag_in(stad["tz"])
        eenheid = "\u00b0" + stad["eenheid"]
        samenvatting = []
        for h in range(3):
            dag = (v0 + timedelta(days=h)).isoformat()
            maxen = per_dag.get(dag)
            if not maxen:
                continue
            m   = sum(maxen) / len(maxen)
            adj = m + offset
            p10 = pctl(maxen, 0.10) + offset
            p90 = pctl(maxen, 0.90) + offset
            spr = statistics.pstdev(maxen) if len(maxen) > 1 else 0.0
            rijen[(dag, stad["key"], str(h), "live")] = {
                "datum": dag, "stad": stad["key"], "station": stad["station"],
                "eenheid": stad["eenheid"], "horizon": str(h),
                "ens_mean": f"{m:.1f}", "bias": f"{offset:+.1f}", "adj_mean": f"{adj:.1f}",
                "p10": f"{p10:.1f}", "p90": f"{p90:.1f}", "spreiding": f"{spr:.1f}",
                "n_leden": str(len(maxen)),
                "wu_proxy": f"{wu[dag]:.0f}" if dag in wu else "",
                "actual_max": "", "fout": "", "bron": "live", "gelogd_op": nu,
            }
            samenvatting.append(f"{adj:.0f}{eenheid}")
        corrtekst = f"corr {offset:+.1f} ({basis}, n={n_corr})" if basis != "geen" else "nog geen correctie"
        print(f"    {stad['naam']:<15} " + "  ".join(samenvatting) + f"   {corrtekst}")


def draai_backtest(rijen: dict, dagen: int) -> None:
    """Eenmalige opbouw van de fouthistorie: haal voor de laatste N dagen de
    echte leadtime-voorspellingen op (horizon morgen en overmorgen) en zet ze
    als bron 'backtest' in de CSV. Daarna meteen verifieren tegen de stations."""
    print(f"\n  Backtest over de laatste {dagen} dagen (horizon morgen en overmorgen)\n")
    nu = datetime.now().strftime("%Y-%m-%d %H:%M")
    nieuw = 0
    for stad in STEDEN:
        try:
            bt = fetch_backtest(stad, dagen)
        except Exception as ex:
            print(f"    {stad['naam']:<15} backtest mislukt: {ex}")
            continue
        toegevoegd = 0
        for (dag, horizon), m in bt.items():
            k = (dag, stad["key"], str(horizon), "backtest")
            if k in rijen:
                continue  # bestaande backtestrijen niet overschrijven
            rijen[k] = {
                "datum": dag, "stad": stad["key"], "station": stad["station"],
                "eenheid": stad["eenheid"], "horizon": str(horizon),
                "ens_mean": f"{m:.1f}", "bias": "+0.0", "adj_mean": f"{m:.1f}",
                "p10": "", "p90": "", "spreiding": "", "n_leden": "",
                "wu_proxy": "", "actual_max": "", "fout": "",
                "bron": "backtest", "gelogd_op": nu,
            }
            toegevoegd += 1
        nieuw += toegevoegd
        print(f"    {stad['naam']:<15} {toegevoegd} voorspeldagen opgehaald")
    print(f"\n  {nieuw} backtestrijen toegevoegd. Nu verifieren tegen de stations...\n")
    n = los_op(rijen)
    print(f"\n  {n} metingen ingevuld.")

# ── Export voor het dashboard ─────────────────────────────────────────────────

def exporteer(rijen: dict) -> dict:
    st   = statistieken(rijen)
    corr = leer_correcties(rijen)

    steden_uit = []
    for stad in STEDEN:
        v0 = vandaag_in(stad["tz"])
        dagen = []
        for h in range(3):
            dag = (v0 + timedelta(days=h)).isoformat()
            r = rijen.get((dag, stad["key"], str(h), "live"))
            if not r:
                # geen verse rij: pak de nieuwste (het dashboard markeert dat als verouderd)
                kandidaten = [x for x in rijen.values()
                              if x["stad"] == stad["key"] and x["horizon"] == str(h)
                              and x["bron"] == "live"]
                r = max(kandidaten, key=lambda x: x["datum"]) if kandidaten else None
            if not r:
                continue
            hist = st.get(stad["key"], {}).get(str(h))
            dagen.append({
                "datum":       r["datum"],
                "verwachting": float(r["adj_mean"]),
                "p10":         float(r["p10"]) if r["p10"] else None,
                "p90":         float(r["p90"]) if r["p90"] else None,
                "spreiding":   float(r["spreiding"]) if r["spreiding"] else None,
                "n_leden":     int(r["n_leden"]) if r["n_leden"] else None,
                "wu":          float(r["wu_proxy"]) if r["wu_proxy"] else None,
                "hist":        hist,
            })

        opgelost = [x for x in rijen.values() if x["stad"] == stad["key"] and x["actual_max"]]
        recent = []
        for x in sorted(opgelost, key=lambda r: (r["datum"], r["horizon"]), reverse=True)[:5]:
            recent.append({
                "datum":     x["datum"],
                "horizon":   int(x["horizon"]),
                "voorspeld": float(x["adj_mean"]),
                "echt":      float(x["actual_max"]),
                "fout":      round(float(x["actual_max"]) - float(x["adj_mean"]), 1),
                "bron":      x["bron"],
            })

        c = corr.get(stad["key"])
        steden_uit.append({
            "key": stad["key"], "naam": stad["naam"], "station": stad["station"],
            "tz": stad["tz"], "eenheid": "\u00b0" + stad["eenheid"],
            "correctie": {"waarde": round(c[0], 1), "basis": c[1], "n": c[2]} if c else None,
            "dagen": dagen,
            "recent": recent,
        })

    payload = {
        "gegenereerd": datetime.now().isoformat(timespec="minutes"),
        "steden": steden_uit,
    }
    with open(JSON_BESTAND, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    with open(JS_BESTAND, "w") as f:
        f.write("window.WEER_DATA = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\n")
    return payload

# ── Terminaloverzichten ───────────────────────────────────────────────────────

def print_overzicht(payload: dict) -> None:
    print(f"\n  {'Stad':<15}{'vandaag':>12}{'morgen':>12}{'overmorgen':>12}    fouthistorie (gem. misser)")
    print("  " + "\u2500" * 78)
    for s in payload["steden"]:
        cellen = {0: "", 1: "", 2: ""}
        histdelen = []
        for i, d in enumerate(s["dagen"]):
            cellen[i] = f"{d['verwachting']:.0f}{s['eenheid']}"
            if d.get("hist"):
                naamh = ["vandaag", "morgen", "overm."][i]
                histdelen.append(f"{naamh} \u00b1{nl(d['hist']['mae_corr'])}{s['eenheid']}")
        print(f"  {s['naam']:<15}{cellen[0]:>12}{cellen[1]:>12}{cellen[2]:>12}    " + "  ".join(histdelen))
    print()


def print_stats(rijen: dict) -> None:
    st   = statistieken(rijen)
    corr = leer_correcties(rijen)
    namen = {"0": "vandaag", "1": "morgen", "2": "overmorgen"}
    kop_ruw, kop_corr = "ruw \u00b1", "na corr \u00b1"
    print(f"\n  {'Stad':<15}{'horizon':<12}{'n':>4}{'bias':>8}{kop_ruw:>8}{kop_corr:>11}")
    print("  " + "\u2500" * 60)
    for stad in STEDEN:
        per = st.get(stad["key"], {})
        eenheid = "\u00b0" + stad["eenheid"]
        for hor in ("0", "1", "2"):
            d = per.get(hor)
            if not d:
                continue
            biastekst = f"{d['bias']:+.1f}".replace(".", ",")
            print(f"  {stad['naam']:<15}{namen[hor]:<12}{d['n']:>4}"
                  f"{biastekst:>8}{nl(d['mae']):>8}{nl(d['mae_corr']):>11}  {eenheid}")
        c = corr.get(stad["key"])
        if c:
            ctekst = f"{c[0]:+.1f}".replace(".", ",")
            print(f"  {'':15}toegepaste correctie: {ctekst}{eenheid}  (basis: {c[1]}, n={c[2]})")
    print("\n  bias: positief betekent dat het model te koud zat (werkelijk was warmer).")
    print("  na corr: verwachte misser nadat de systematische bias is weggecorrigeerd.\n")

# ── Server voor je telefoon ───────────────────────────────────────────────────

def serveer(poort: int = 8765) -> None:
    import functools
    import http.server

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(MAP))
    ip = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    print(f"\n  Dashboard draait. Laat dit venster open staan.")
    print(f"    op deze computer : http://localhost:{poort}")
    if ip:
        print(f"    op je telefoon   : http://{ip}:{poort}   (zelfde wifinetwerk)")
    else:
        print(f"    op je telefoon   : http://<ip van deze computer>:{poort}")
    print(f"  Stoppen met Ctrl+C.\n")
    try:
        with http.server.ThreadingHTTPServer(("0.0.0.0", poort), handler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Gestopt.\n")

# ── Hoofdprogramma ────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--serve" in args:
        poort = 8765
        i = args.index("--serve")
        if i + 1 < len(args) and args[i + 1].isdigit():
            poort = int(args[i + 1])
        serveer(poort)
        return

    if "--stats" in args:
        print_stats(laad_rijen())
        return

    if "--backtest" in args:
        dagen = 45
        i = args.index("--backtest")
        if i + 1 < len(args) and args[i + 1].isdigit():
            dagen = min(int(args[i + 1]), 90)
        rijen = laad_rijen()
        draai_backtest(rijen, dagen)
        bewaar_rijen(rijen)
        exporteer(rijen)
        print_stats(rijen)
        print(f"  Klaar. Draai nu `python3 weer.py` voor de voorspellingen van vandaag.\n")
        return

    # Standaard: de dagelijkse run.
    rijen = laad_rijen()
    print("\n  Stap 1/3: afgeronde dagen verifieren tegen de stations...")
    n = los_op(rijen)
    print(f"    {n} nieuwe metingen ingevuld." if n else "    niets om te verifieren.")

    corr = leer_correcties(rijen)
    print("\n  Stap 2/3: verse voorspellingen ophalen (15 steden, even geduld)...")
    voorspel(rijen, corr)

    bewaar_rijen(rijen)
    print("\n  Stap 3/3: dashboard verversen...")
    payload = exporteer(rijen)
    print_overzicht(payload)
    print("  Bekijken: `python3 weer.py --serve` en open het adres op je telefoon.")
    print("  Details : `python3 weer.py --stats`\n")


if __name__ == "__main__":
    main()
