#!/usr/bin/env python3
"""
Kalibratie voor de Weerbot app.

Haalt een lange backtest op (standaard 240 dagen) met vijf modellen via de
previous runs API van Open-Meteo, verifieert tegen de METAR waarnemingen en
leert daaruit per stad:

  1. modelgewichten op basis van recente skill (EWMA, halfwaardetijd 14 dagen)
  2. de correctiekern: een gewogen ridge met vergeten die uit het gewogen
     modelgemiddelde, de recentste geverifieerde fout, de spreiding tussen de
     modellen en de afwijking van elk afzonderlijk model de verwachting maakt
  3. gekalibreerde onzekerheidsbanden uit de empirische kwantielen van de
     walk forward restfouten, zodat het 80% interval ook echt 80% dekt

De oude correctie (actual = a + b * modelgemiddelde, plus g maal de laatste
fout) loopt er als controle naast. Per stad en horizon gaat alleen de kern mee
naar de app als hij op het evaluatievenster ook echt beter is; zo niet, dan
blijven de oude parameters staan.

Alles wordt walk forward gevalideerd: elke dag wordt voorspeld met uitsluitend
kennis van voor die dag. Op horizon h is de verste bekende geverifieerde fout
die van h+1 dagen voor de doeldag, en daar wordt ook op gefit.

Uitvoer: app_params.js en app_params.json voor de app, plus een rapport.

Gebruik: python3 kalibratie.py [dagen]
"""

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import weer  # STEDEN, fetch_station_maxen, c_van_f, _get_json, nl

# De API-namen verschillen per endpoint (de ensemble-API kent ecmwf_aifs025,
# de deterministische APIs ecmwf_aifs025_single). Intern werkt alles daarom met
# korte namen; KORT_VAN vertaalt elke variant die we tegenkomen.
KORT_VAN = {
    "ecmwf_ifs025": "ifs",
    "ecmwf_aifs025": "aifs", "ecmwf_aifs025_single": "aifs",
    "gfs_seamless": "gfs", "ncep_gefs025": "gfs",
    "icon_seamless": "icon",
    "gem_seamless": "gem", "gem_global": "gem",
}
MODELLEN = ["ifs", "aifs", "gfs", "icon", "gem"]
# Wat er bij de deterministische APIs (previous-runs, historical-forecast) wordt
# opgevraagd. AIFS heet daar ecmwf_aifs025_single; onder de naam ecmwf_aifs025
# kwam er nooit een reeks terug, waardoor AIFS jarenlang buiten de weging viel.
API_MODELLEN = ["ecmwf_ifs025", "ecmwf_aifs025_single", "gfs_seamless",
                "icon_seamless", "gem_seamless"]
AIFS_ALTERNATIEF = "ecmwf_aifs025"   # terugval als _single niets oplevert
HALFWAARDE = 14.0      # dagen, voor de EWMA weging van de modelgewichten
KRIMP_N = 30.0         # hoe sterk b richting 1 wordt getrokken bij weinig data
BURN_GEWICHT = 10      # minimaal aantal dagen voor er modelgewichten zijn
BURN_REGRESSIE = 30    # minimaal aantal dagen voor de regressie meedoet
BURN_EVALUATIE = 60    # evaluatie start hier zodat er restfoutkwantielen zijn

# ── De correctiekern ──────────────────────────────────────────────────────────
# De oude correctie was actual = a + b * modelgemiddelde (+ g * lagfout). De kern
# generaliseert dat naar een gewogen ridge over meer voorspellers die live ook
# beschikbaar zijn: het gewogen gemiddelde zelf, de recentste geverifieerde fout,
# de spreiding tussen de modelsystemen en per model de afwijking van het
# gemiddelde. Die laatste groep laat het model leren dat bijvoorbeeld "ICON
# hoger dan de rest" iets anders betekent dan "IFS hoger dan de rest".
#
# Onderzocht en verworpen (walk forward op de featurebundel, 2026-07): een
# gradient-boosting-model op dezelfde features, elke 30 dagen hertraind,
# verliest ruim van deze ridge (MAE 0,87 tegen 0,80 over 17 steden) en maakt
# ook elk mengsel slechter: de restfout is in essentie lineair. Ook zonder
# effect: een globale coefficientenprior over steden, debias-weging per model
# en de mediaan in plaats van het gewogen gemiddelde. ERA5-weersvariabelen
# (bewolking, instraling) lijken 3% te geven maar dat is lek: het effect
# verdwijnt zodra je ze een dag verschuift, dus het is analyse van de doeldag
# zelf, geen voorspelling.
KERN_FEATURES = ["mu", "lag", "spreiding"] + ["d_" + m for m in MODELLEN]
HALFWAARDE_KERN = 60.0   # tragere vergeetsnelheid: meer coefficienten om te schatten
ALPHA_KERN = 30.0        # ridge-straf op gestandaardiseerde features
# De lagfeature is geen enkele dag maar een EWMA van de recente geverifieerde
# fouten: dat dempt de meetruis van een dag en volgt een blokkade van een paar
# dagen (hittegolf, verkeerd ingeschatte luchtmassa) net zo goed. Het venster
# van 24 dagen is precies wat de app live aan verificatie bijhoudt, zodat de
# backtest niets gebruikt wat de browser niet heeft.
EWMA_LAG_HALF = 8.0
EWMA_LAG_VENSTER = 24


# ── Ophalen ───────────────────────────────────────────────────────────────────

def uitvoermap() -> Path:
    """Waar app_params.js hoort te staan. Ligt dit script in een submap naast
    de webbestanden (de GitHub indeling bot/kalibratie.py), dan is dat de map
    erboven; anders gewoon naast het script."""
    hier = Path(__file__).resolve().parent
    boven = hier.parent
    if (boven / "index.html").exists() and not (hier / "index.html").exists():
        return boven
    return hier


_AIFS_NAAM: dict = {}   # endpoint -> de AIFS-naam die daar data oplevert


def modelnamen(aifs: str) -> list:
    """API_MODELLEN met deze naam voor AIFS."""
    return [aifs if KORT_VAN.get(m) == "aifs" else m for m in API_MODELLEN]


def _haal_met_aifs(soort: str, bouw_url, heeft_aifs):
    """Haalt op met de voorkeursnaam voor AIFS en probeert eenmalig de andere
    naam als die geen reeks oplevert (een onbekende modelnaam geeft bovendien
    een 400). De winnende naam wordt onthouden, dus per endpoint is er hooguit
    een extra aanroep. Geeft (json, gebruikte aifs-naam)."""
    if soort in _AIFS_NAAM:
        naam = _AIFS_NAAM[soort]
        return weer._get_json(bouw_url(modelnamen(naam)), timeout=90), naam

    reserve = None
    laatste_fout = None
    for naam in (API_MODELLEN[1], AIFS_ALTERNATIEF):
        try:
            data = weer._get_json(bouw_url(modelnamen(naam)), timeout=90)
        except Exception as ex:
            laatste_fout = ex
            continue
        if heeft_aifs(data, naam):
            _AIFS_NAAM[soort] = naam
            return data, naam
        if reserve is None:
            reserve = (data, naam)
    if reserve is None:
        raise laatste_fout if laatste_fout else RuntimeError("geen antwoord")
    # Geen van beide namen leverde AIFS: verder zoeken heeft geen zin.
    _AIFS_NAAM[soort] = reserve[1]
    print("      [let op] geen AIFS-reeks van deze API, verder met vier modellen")
    return reserve


def haal_previous_runs(stad: dict, d1: date, d2: date) -> dict:
    """{(horizon, datum): {kort model: dagmax}} uit de previous runs API."""
    eenheid = "fahrenheit" if stad["eenheid"] == "F" else "celsius"

    def bouw(modellen):
        return (
            "https://previous-runs-api.open-meteo.com/v1/forecast"
            f"?latitude={stad['lat']}&longitude={stad['lon']}"
            "&hourly=temperature_2m_previous_day1,temperature_2m_previous_day2"
            f"&models={','.join(modellen)}"
            f"&temperature_unit={eenheid}"
            f"&start_date={d1.isoformat()}&end_date={d2.isoformat()}"
            f"&timezone={urllib.parse.quote(stad['tz'])}"
        )

    def heeft_aifs(data, naam):
        reeks = (data.get("hourly") or {}).get(f"temperature_2m_previous_day1_{naam}")
        return bool(reeks) and any(v is not None for v in reeks)

    data, aifs = _haal_met_aifs("prev", bouw, heeft_aifs)
    hourly = data["hourly"]
    tijden = hourly["time"]
    uit: dict = {}
    for h in (1, 2):
        for m in modelnamen(aifs):
            reeks = hourly.get(f"temperature_2m_previous_day{h}_{m}")
            if not reeks:
                continue
            per_dag: dict = {}
            for t, v in zip(tijden, reeks):
                if v is None:
                    continue
                per_dag.setdefault(t[:10], []).append(v)
            for dag, waarden in per_dag.items():
                if len(waarden) >= 12:
                    uit.setdefault((h, dag), {})[KORT_VAN[m]] = max(waarden)
    return uit


def haal_hist_forecast(stad: dict, d1: date, d2: date) -> dict:
    """{datum: {model: dagmax}} uit de historische forecast API: de voorspelling
    zoals die op de dag zelf gold (leadtime 0 tot 24 uur). Empirisch getoetst:
    de structurele modelafwijking blijft staan (Los Angeles dag 0 nog -3,6 van de
    -4,1 op dag 1), dus dit is een echte voorspelling en niet de analyse."""
    eenheid = "fahrenheit" if stad["eenheid"] == "F" else "celsius"

    def bouw(modellen):
        return (
            "https://historical-forecast-api.open-meteo.com/v1/forecast"
            f"?latitude={stad['lat']}&longitude={stad['lon']}"
            "&daily=temperature_2m_max"
            f"&models={','.join(modellen)}"
            f"&temperature_unit={eenheid}"
            f"&start_date={d1.isoformat()}&end_date={d2.isoformat()}"
            f"&timezone={urllib.parse.quote(stad['tz'])}"
        )

    def heeft_aifs(data, naam):
        reeks = (data.get("daily") or {}).get(f"temperature_2m_max_{naam}")
        return bool(reeks) and any(v is not None for v in reeks)

    data, aifs = _haal_met_aifs("hist", bouw, heeft_aifs)
    daily = data.get("daily", {})
    uit: dict = {}
    for i, dag in enumerate(daily.get("time", [])):
        per = {}
        for m in modelnamen(aifs):
            reeks = daily.get(f"temperature_2m_max_{m}")
            if reeks and i < len(reeks) and reeks[i] is not None:
                per[KORT_VAN[m]] = reeks[i]
        if per:
            uit[dag] = per
    return uit


def haal_actuals_hko(stad: dict, d1: date, d2: date) -> dict:
    """Dagmaxima van het hoofdstation van het Hong Kong Observatory, de bron
    waarop de markt afrekent. Let op: HKO publiceert per maand, dus de laatste
    weken ontbreken meestal."""
    uit = {}
    for jaar in range(d1.year, d2.year + 1):
        url = ("https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
               f"?dataType=CLMMAXT&year={jaar}&rformat=csv&station=HKO")
        try:
            tekst = weer._get(url, timeout=60)
        except Exception as ex:
            print(f"      [let op] HKO {jaar} mislukt: {ex}")
            continue
        for regel in tekst.splitlines():
            delen = regel.strip().lstrip("\ufeff").split(",")
            if len(delen) < 4 or not delen[0].strip().isdigit():
                continue
            try:
                j, m, d = int(delen[0]), int(delen[1]), int(delen[2])
                waarde = float(delen[3])
            except ValueError:
                continue
            if len(delen) > 4 and delen[4].strip() in ("***", "#"):
                continue
            dag = date(j, m, d)
            if d1 <= dag <= d2:
                uit[dag.isoformat()] = waarde
    return uit


def haal_actuals_era5(stad: dict, d1: date, d2: date) -> dict:
    """Terugval voor plaatsen zonder METAR archief: het ERA5 raster."""
    eenheid = "fahrenheit" if stad["eenheid"] == "F" else "celsius"
    url = ("https://archive-api.open-meteo.com/v1/archive"
           f"?latitude={stad['lat']}&longitude={stad['lon']}"
           "&daily=temperature_2m_max"
           f"&temperature_unit={eenheid}"
           f"&start_date={d1.isoformat()}&end_date={d2.isoformat()}"
           f"&timezone={urllib.parse.quote(stad['tz'])}")
    data = weer._get_json(url, timeout=60)
    daily = data.get("daily", {})
    uit = {}
    for dag, waarde in zip(daily.get("time", []), daily.get("temperature_2m_max", [])):
        if waarde is not None:
            uit[dag] = waarde
    return uit


def verrijk_1min(stad: dict, uit_f: dict, d1: date, d2: date) -> int:
    """Verfijnt Amerikaanse dagmaxima met de 1-minuut ASOS reeks. Verhoogt een
    dagmax alleen bij voldoende samples en een plausibel verschil (QC)."""
    from zoneinfo import ZoneInfo
    from datetime import datetime, timezone as _tz
    tz = ZoneInfo(stad["tz"])
    per: dict = {}
    blok = d1
    while blok <= d2:
        eind = min(d2, blok + timedelta(days=30))
        url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py"
               f"?station={stad['station']}&vars=tmpf"
               f"&sts={blok.isoformat()}T00:00Z&ets={(eind + timedelta(days=1)).isoformat()}T00:00Z"
               "&sample=1min&what=download&tz=UTC&delim=comma")
        tekst = None
        for poging in range(3):
            try:
                tekst = weer._get(url, timeout=150)
                break
            except Exception:
                time.sleep(3 + 3 * poging)
        if tekst:
            for regel in tekst.splitlines():
                if not regel.startswith(stad["station"] + ","):
                    continue
                dele = regel.split(",")
                if len(dele) < 4:
                    continue
                try:
                    tv = float(dele[3])
                    ts = datetime.strptime(dele[2], "%Y-%m-%d %H:%M").replace(tzinfo=_tz.utc)
                except ValueError:
                    continue
                dag = ts.astimezone(tz).date().isoformat()
                e = per.setdefault(dag, {"maxf": -999.0, "n": 0})
                e["n"] += 1
                if tv > e["maxf"]:
                    e["maxf"] = tv
        blok = eind + timedelta(days=1)
        time.sleep(0.8)
    verrijkt = 0
    for dag, e in per.items():
        if dag in uit_f and e["n"] >= 100 and uit_f[dag] < e["maxf"] <= uit_f[dag] + 4.0:
            uit_f[dag] = e["maxf"]
            verrijkt += 1
    return verrijkt


def haal_actuals(stad: dict, d1: date, d2: date) -> dict:
    """{datum: werkelijke dagmax in de stadseenheid}, volgens de resolutiebron
    die bij deze stad hoort."""
    bron = stad.get("bron", "iem")
    if bron == "hko":
        return haal_actuals_hko(stad, d1, d2)
    if bron == "era5":
        return haal_actuals_era5(stad, d1, d2)
    ruw = weer.fetch_station_maxen(stad["station"], stad["tz"], d1, d2)
    uit = {}
    for dag, e in ruw.items():
        if e["n"] >= 8 and e["laatste_uur"] >= 18:
            uit[dag] = e["maxf"] if stad["eenheid"] == "F" else weer.c_van_f(e["maxf"])
    if stad["eenheid"] == "F":
        stad["_1min"] = verrijk_1min(stad, uit, d1, d2)
    return uit


def laad_log(pad: Path) -> list:
    import csv
    if not pad.exists():
        return []
    try:
        with open(pad, newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def records_uit_enslog(rijen: list, act: dict, key: str, h: int) -> list:
    """Records op basis van de dagelijkse ensemblelog: {model: ledengemiddelde}.
    Laatste logregel per (doeldatum, model) wint. De log gebruikt de namen van de
    ensemble-API, die hier naar dezelfde korte namen gaan als de backtest."""
    per: dict = {}
    for r in rijen:
        if r.get("key") != key or r.get("lead") != str(h):
            continue
        kort = KORT_VAN.get(r.get("model", ""))
        if not kort:
            continue
        try:
            per.setdefault(r["doel_datum"], {})[kort] = float(r["gemiddelde"])
        except (KeyError, ValueError):
            continue
    uit = []
    for dag in sorted(per):
        if dag in act and per[dag]:
            uit.append((date.fromisoformat(dag).toordinal(), per[dag], act[dag]))
    return uit


def leer_nws(rijen: list, act: dict, stad_uit: dict, key: str) -> dict:
    """Leert per horizon het blendgewicht voor de NWS verwachting, gekrompen
    richting de prior 0,25. Vereist minstens 40 gematchte dagen."""
    uit = {}
    for h in ("0", "1"):
        if h not in stad_uit or "yhat_per_dag" not in stad_uit[h]:
            continue
        yh = stad_uit[h]["yhat_per_dag"]
        paren = []
        for r in rijen:
            if r.get("key") != key or r.get("lead") != h:
                continue
            try:
                dag = r["doel_datum"]
                nws = float(r["temp_f"])
            except (KeyError, ValueError):
                continue
            o = date.fromisoformat(dag).toordinal()
            if dag in act and o in yh:
                paren.append((ewma_gewicht(date.today().toordinal() - o),
                              yh[o], nws, act[dag]))
        if len(paren) < 40:
            continue
        beste, beste_m = 0.25, None
        for wtest in [i * 0.02 for i in range(31)]:
            m = sum(w * ((1 - wtest) * y + wtest * nv - a) ** 2
                    for w, y, nv, a in paren) / sum(w for w, *_ in paren)
            if beste_m is None or m < beste_m:
                beste_m, beste = m, wtest
        ne = n_eff([w for w, *_ in paren])
        uit[h] = round((ne * beste + 30 * 0.25) / (ne + 30), 2)
        uit["n"] = len(paren)
    return uit


# ── Gewogen statistiek ────────────────────────────────────────────────────────

def ewma_gewicht(leeftijd_dagen: float) -> float:
    return 0.5 ** (leeftijd_dagen / HALFWAARDE)


def gewogen_gem(paren):
    """paren = [(w, x)] -> gewogen gemiddelde of None."""
    W = sum(w for w, _ in paren)
    if W <= 0:
        return None
    return sum(w * x for w, x in paren) / W


def gewogen_kwantiel(paren, q: float):
    """paren = [(w, x)] -> gewogen empirisch kwantiel."""
    paren = sorted(paren, key=lambda p: p[1])
    W = sum(w for w, _ in paren)
    if W <= 0:
        return None
    cum = 0.0
    for w, x in paren:
        cum += w
        if cum / W >= q:
            return x
    return paren[-1][1]


def n_eff(gewichten) -> float:
    s1 = sum(gewichten)
    s2 = sum(w * w for w in gewichten)
    return (s1 * s1 / s2) if s2 > 0 else 0.0


def crps_ensemble(restfouten_paren, y_min_yhat: float) -> float:
    """CRPS met de gewogen restfouten als voorspellend ensemble rond nul.
    y_min_yhat is de werkelijke fout van vandaag."""
    p = restfouten_paren[-60:]  # recentste 60 volstaan
    W = sum(w for w, _ in p)
    if W <= 0:
        return abs(y_min_yhat)
    t1 = sum(w * abs(x - y_min_yhat) for w, x in p) / W
    t2 = 0.0
    for wi, xi in p:
        for wj, xj in p:
            t2 += wi * wj * abs(xi - xj)
    t2 /= 2 * W * W
    return t1 - t2


# ── De correctiekern: gewogen ridge met vergeten ──────────────────────────────

LAG_DAGEN = 2      # standaard; per horizon h is de verse fout die van h+1 dagen
KRIMP_G = 40.0     # krimp van de lagcoefficient richting 0


def los_op(A: list, b: list):
    """Los A x = b op met Gauss-eliminatie en partiele pivotering.
    Geeft None als het stelsel singulier is."""
    k = len(b)
    M = [rij[:] + [b[i]] for i, rij in enumerate(A)]
    for kol in range(k):
        piv = max(range(kol, k), key=lambda r: abs(M[r][kol]))
        if abs(M[piv][kol]) < 1e-12:
            return None
        M[kol], M[piv] = M[piv], M[kol]
        deler = M[kol][kol]
        for j in range(kol, k + 1):
            M[kol][j] /= deler
        for r in range(k):
            if r != kol and M[r][kol]:
                f = M[r][kol]
                for j in range(kol, k + 1):
                    M[r][j] -= f * M[kol][j]
    return [M[i][k] for i in range(k)]


class OnlineRidge:
    """Gewogen kleinste kwadraten met EWMA-vergeten en een ridge-straf.

    De sommen worden recursief bijgewerkt (elke nieuwe dag laat het verleden met
    0,5^(dagen/halfwaarde) krimpen), dus de fit is O(1) per dag. Features worden
    intern gestandaardiseerd zodat alpha voor elke feature hetzelfde betekent:
    een coefficient wordt met factor Sw/(Sw+alpha) naar nul getrokken, precies
    zoals de oude krimp van b richting 1."""

    def __init__(self, k: int, half: float, alpha: float):
        self.k = k
        self.half = half
        self.alpha = alpha
        self.M = [[0.0] * (k + 1) for _ in range(k + 1)]   # [1, x] x [1, x]
        self.v = [0.0] * (k + 1)                           # [1, x] * y
        self.n = 0
        self.dag = None

    def _verval(self, dag):
        if self.dag is not None and dag > self.dag:
            f = 0.5 ** ((dag - self.dag) / self.half)
            for i in range(self.k + 1):
                self.v[i] *= f
                rij = self.M[i]
                for j in range(self.k + 1):
                    rij[j] *= f
        self.dag = dag

    def voeg_toe(self, dag, x, y):
        self._verval(dag)
        z = [1.0] + list(x)
        for i in range(self.k + 1):
            zi = z[i]
            if zi:
                rij = self.M[i]
                for j in range(self.k + 1):
                    rij[j] += zi * z[j]
            self.v[i] += zi * y
        self.n += 1

    def coef(self):
        """(intercept, coefficienten) in ruwe eenheden, of None."""
        Sw = self.M[0][0]
        if self.n < 10 or Sw <= 0:
            return None
        k = self.k
        m = [self.M[0][j + 1] / Sw for j in range(k)]
        ybar = self.v[0] / Sw
        C = [[self.M[i + 1][j + 1] - Sw * m[i] * m[j] for j in range(k)] for i in range(k)]
        vc = [self.v[i + 1] - Sw * m[i] * ybar for i in range(k)]
        sd = [math.sqrt(max(C[i][i] / Sw, 1e-9)) for i in range(k)]
        A = [[C[i][j] / (sd[i] * sd[j]) + (self.alpha if i == j else 0.0)
              for j in range(k)] for i in range(k)]
        opl = los_op(A, [vc[i] / sd[i] for i in range(k)])
        if opl is None:
            return None
        coef = [opl[i] / sd[i] for i in range(k)]
        return ybar - sum(coef[i] * m[i] for i in range(k)), coef

    def voorspel(self, x) -> float:
        c = self.coef()
        if c is None:
            return 0.0
        a, coef = c
        return a + sum(coef[i] * x[i] for i in range(self.k))


def lag_van(resid: dict, o: int, lag_dagen: int) -> float:
    """EWMA (halfwaarde EWMA_LAG_HALF) van de restfouten die op de doeldag o al
    geverifieerd zijn: dagen o-lag_dagen tot en met o-EWMA_LAG_VENSTER.
    Nul als er niets bekend is."""
    W = S = 0.0
    for stap in range(lag_dagen, EWMA_LAG_VENSTER + 1):
        r = resid.get(o - stap)
        if r is not None:
            w = 0.5 ** (stap / EWMA_LAG_HALF)
            W += w
            S += w * r
    return S / W if W > 0 else 0.0


def pstdev_van(fc: dict):
    """Spreiding tussen de modelwaarden van een dag, of None bij minder dan twee."""
    from statistics import pstdev
    vals = list(fc.values())
    return pstdev(vals) if len(vals) >= 2 else None


def kern_vector(mu: float, lag: float, spreiding, fc: dict) -> list:
    """De featurevector van de kern, in de volgorde van KERN_FEATURES.
    Een ontbrekend model levert afwijking nul: dat model telt dan niet mee."""
    x = [mu, lag, spreiding if spreiding is not None else 0.0]
    for m in MODELLEN:
        x.append(fc[m] - mu if m in fc else 0.0)
    return x


# ── Walk forward per stad en horizon ──────────────────────────────────────────

def _banden(records: list, rez: list, spreid: list) -> dict:
    """Walk forward de onzekerheidsbanden bij een gegeven reeks restfouten.
    Elke dag gebruikt uitsluitend restfouten van daarvoor."""
    n = len(records)
    sigma = [None] * n
    crps_o, crps_n, dek_o, dek_n, br_o, br_n = [], [], [], [], [], []

    def sig_fit(t, ref):
        paren = [(ewma_gewicht(ref - records[s][0]), spreid[s], abs(rez[s]))
                 for s in range(t) if rez[s] is not None and spreid[s] is not None]
        if len(paren) < 20:
            return None, None
        xw = gewogen_gem([(w, x) for w, x, _ in paren])
        yw = gewogen_gem([(w, y) for w, _, y in paren])
        sxx = sum(w * (x - xw) ** 2 for w, x, _ in paren)
        sxy = sum(w * (x - xw) * (y - yw) for w, x, y in paren)
        d = max(0.0, sxy / sxx) if sxx > 0.05 else 0.0
        return max(0.1, yw - d * xw), d

    for t in range(n):
        d_t = records[t][0]
        c, d = sig_fit(t, d_t)
        if c is not None:
            sp = spreid[t]
            if sp is None:
                sps = [x for x in spreid[:t] if x is not None]
                sp = sum(sps) / len(sps) if sps else 0.0
            sigma[t] = max(0.2, c + d * sp)

        if t < BURN_EVALUATIE or rez[t] is None:
            continue
        fout = rez[t]
        klaar = [(records[s][0], rez[s]) for s in range(t) if rez[s] is not None]
        if len(klaar) < 20:
            continue
        paren = [(ewma_gewicht(d_t - o), r) for o, r in klaar]
        q10 = gewogen_kwantiel(paren, 0.10)
        q90 = gewogen_kwantiel(paren, 0.90)
        dek_o.append((q10, q90, fout)); br_o.append(q90 - q10)
        crps_o.append(crps_ensemble(paren, fout))
        if sigma[t] is not None:
            zp = [(ewma_gewicht(d_t - records[s][0]), rez[s] / sigma[s])
                  for s in range(t) if rez[s] is not None and sigma[s]]
            if len(zp) >= 20:
                z10 = gewogen_kwantiel(zp, 0.10)
                z90 = gewogen_kwantiel(zp, 0.90)
                dek_n.append((z10 * sigma[t], z90 * sigma[t], fout))
                br_n.append((z90 - z10) * sigma[t])
                crps_n.append(crps_ensemble([(w2, zz * sigma[t]) for w2, zz in zp], fout))

    ref = records[-1][0] + 1
    klaar = [(records[s][0], rez[s], sigma[s]) for s in range(n) if rez[s] is not None]
    paren_r = [(ewma_gewicht(ref - o), r) for o, r, _ in klaar]
    q10 = gewogen_kwantiel(paren_r, 0.10) if len(paren_r) >= 20 else None
    q90 = gewogen_kwantiel(paren_r, 0.90) if len(paren_r) >= 20 else None
    zp = [(ewma_gewicht(ref - o), r / sg) for o, r, sg in klaar if sg]
    c_e, d_e = sig_fit(n, ref)
    sps = [(ewma_gewicht(ref - records[s][0]), spreid[s])
           for s in range(n) if spreid[s] is not None]
    uit = {
        "res_q10": None if q10 is None else round(q10, 2),
        "res_q90": None if q90 is None else round(q90, 2),
        "crps_oud": round(sum(crps_o) / len(crps_o), 2) if crps_o else None,
        "crps": round(sum(crps_n) / len(crps_n), 2) if crps_n else None,
        "dekkingsreeks": dek_o, "dekkingsreeks_s": dek_n,
        "breedte_o": br_o, "breedte_s": br_n,
    }
    if len(zp) >= 20 and c_e is not None:
        uit["sig_c"] = round(c_e, 3); uit["sig_d"] = round(d_e, 3)
        uit["qz10"] = round(gewogen_kwantiel(zp, 0.10), 3)
        uit["qz90"] = round(gewogen_kwantiel(zp, 0.90), 3)
        uit["s_gem"] = round(gewogen_gem(sps), 2) if sps else None
    return uit


def walk_forward(records: list, lag_dagen: int = LAG_DAGEN) -> dict:
    """Volledig walk forward: gewichten, correctie, lagterm en band gebruiken per
    dag uitsluitend kennis van daarvoor.

    Er lopen twee correcties naast elkaar: de oude (a + b*mu, plus g maal de
    laatste fout) en de kern (ridge over mu, lagfout, spreiding en de afwijking
    van elk model). De kern wint alleen als hij over het evaluatievenster ook
    echt beter is; anders blijft de oude staan. De band wordt gemaakt van de
    restfouten van de winnaar."""
    n = len(records)
    mu_raw = [None] * n
    spreid = [None] * n     # spreiding tussen de modellen: onzekerheidsproxy
    yhat_b = [None] * n     # a + b*mu
    yhat_n = [None] * n     # plus g * recente fout
    yhat_k = [None] * n     # de kern
    rb = [None] * n
    rn = [None] * n
    rk = [None] * n
    fouten_b, fouten_n, fouten_k = [], [], []
    yhat_per_dag = {}
    laatste = {"gew": None, "ab": (0.0, 1.0)}
    kern = OnlineRidge(len(KERN_FEATURES), HALFWAARDE_KERN, ALPHA_KERN)
    resid_kern: dict = {}   # ordinaal -> restfout van de kern

    def lag_index(t):
        for s in range(t - 1, -1, -1):
            if records[s][0] <= records[t][0] - lag_dagen and rn[s] is not None:
                return s
        return None

    def g_fit(t, ref):
        paren = []
        for s in range(t):
            li = lag_index(s)
            if li is not None and rb[s] is not None:
                paren.append((ewma_gewicht(ref - records[s][0]), rn[li], rb[s]))
        if len(paren) < 15:
            return 0.0
        sxx = sum(w * x * x for w, x, _ in paren)
        if sxx <= 0.2:
            return 0.0
        g = sum(w * x * y for w, x, y in paren) / sxx
        ne = n_eff([w for w, _, _ in paren])
        return min(0.5, max(0.0, g * ne / (ne + KRIMP_G)))

    for t in range(n):
        d_t, fc_t, y_t = records[t]

        if t >= BURN_GEWICHT:
            mse = {}
            for m in MODELLEN:
                paren = [(ewma_gewicht(d_t - records[s][0]),
                          (records[s][2] - records[s][1][m]) ** 2)
                         for s in range(t) if m in records[s][1]]
                if len(paren) >= 10:
                    mse[m] = gewogen_gem(paren) + 0.25
            gewichten = {m: 1.0 / v for m, v in mse.items()} if mse else {}
        else:
            gewichten = {}
        g_ = {m: gewichten.get(m, 0.0) for m in fc_t}
        if sum(g_.values()) <= 0:
            g_ = {m: 1.0 for m in fc_t}
        W = sum(g_.values())
        mu_raw[t] = sum(g_[m] * fc_t[m] for m in fc_t) / W
        if gewichten:
            laatste["gew"] = gewichten
        spreid[t] = pstdev_van(fc_t)

        if t >= BURN_REGRESSIE:
            paren = [(ewma_gewicht(d_t - records[s][0]), mu_raw[s], records[s][2])
                     for s in range(t)]
            xw = gewogen_gem([(w, x) for w, x, _ in paren])
            yw = gewogen_gem([(w, y) for w, _, y in paren])
            sxx = sum(w * (x - xw) ** 2 for w, x, _ in paren)
            sxy = sum(w * (x - xw) * (y - yw) for w, x, y in paren)
            b_ols = sxy / sxx if sxx > 0.5 else 1.0
            ne = n_eff([w for w, _, _ in paren])
            bb = 1.0 + (b_ols - 1.0) * ne / (ne + KRIMP_N)
            laatste["ab"] = (yw - bb * xw, bb)
            yhat_b[t] = laatste["ab"][0] + bb * mu_raw[t]
        else:
            yhat_b[t] = mu_raw[t]

        g = g_fit(t, d_t) if t >= BURN_REGRESSIE + 10 else 0.0
        li = lag_index(t)
        yhat_n[t] = yhat_b[t] + g * (rn[li] if li is not None else 0.0)

        x_t = kern_vector(mu_raw[t], lag_van(resid_kern, d_t, lag_dagen),
                          spreid[t], fc_t)
        yhat_k[t] = mu_raw[t] + kern.voorspel(x_t)

        if t >= BURN_EVALUATIE:
            fouten_b.append(abs(y_t - yhat_b[t]))
            fouten_n.append(abs(y_t - yhat_n[t]))
            fouten_k.append(abs(y_t - yhat_k[t]))
            yhat_per_dag[d_t] = yhat_n[t]

        resid_kern[d_t] = y_t - yhat_k[t]
        kern.voeg_toe(d_t, x_t, y_t - mu_raw[t])
        if t >= BURN_REGRESSIE:
            rb[t] = y_t - yhat_b[t]
            rn[t] = y_t - yhat_n[t]
            rk[t] = y_t - yhat_k[t]

    mae_n = sum(fouten_n) / len(fouten_n) if fouten_n else None
    mae_k = sum(fouten_k) / len(fouten_k) if fouten_k else None
    # De kern moet zich bewijzen op het evaluatievenster; zo niet, dan blijft de
    # oude correctie staan en verandert er voor die stad en horizon niets.
    gebruik_kern = mae_k is not None and mae_n is not None and mae_k <= mae_n
    if gebruik_kern:
        yhat_per_dag = {records[t][0]: yhat_k[t] for t in range(BURN_EVALUATIE, n)}

    ref = records[-1][0] + 1
    aandelen = None
    if laatste["gew"]:
        S = sum(laatste["gew"].values())
        aandelen = {m: round(v / S, 3) for m, v in laatste["gew"].items()}
    uit = {
        "a": round(laatste["ab"][0], 3), "b": round(laatste["ab"][1], 3),
        "g": round(g_fit(n, ref), 3),
        "gewichten": aandelen,
        "lag_dagen": lag_dagen,
        "mae_basis": round(sum(fouten_b) / len(fouten_b), 2) if fouten_b else None,
        "mae_oud": round(mae_n, 2) if mae_n is not None else None,
        "mae_kern": round(mae_k, 2) if mae_k is not None else None,
        "mae_nieuw": round(mae_k if gebruik_kern else mae_n, 2) if mae_n is not None else None,
        "n_eval": len(fouten_n), "n_totaal": n,
        "yhat_per_dag": yhat_per_dag,
    }
    if gebruik_kern:
        c = kern.coef()
        if c is not None:
            uit["kern"] = {
                "features": KERN_FEATURES,
                "intercept": round(c[0], 4),
                "coef": [round(x, 5) for x in c[1]],
                "lag_dagen": lag_dagen,
            }
    uit.update(_banden(records, rk if gebruik_kern else rn, spreid))
    return uit


# ── Hoofdprogramma ────────────────────────────────────────────────────────────

def laad_vorige_params():
    """De parameters van de vorige ronde, of None. In de repository staat alleen
    app_params.js, dus als het json-bestand ontbreekt wordt de js-variant
    uitgepakt. Steden die deze keer mislukken houden zo hun oude parameters."""
    map_uit = uitvoermap()
    pad = map_uit / "app_params.json"
    if pad.exists():
        try:
            return json.load(open(pad))
        except Exception:
            pass
    pad = map_uit / "app_params.js"
    if pad.exists():
        try:
            tekst = pad.read_text()
            return json.loads(tekst[tekst.index("=") + 1:].strip().rstrip(";"))
        except Exception:
            pass
    return None


def run(dagen: int = 240):
    d2 = date.today() - timedelta(days=2)   # gisteren kan nog METAR vertraging hebben
    d1 = d2 - timedelta(days=dagen)
    print(f"\n  Kalibratie over {dagen} dagen ({d1} tot {d2}), 5 modellen, walk forward.\n")

    bestaand = {}
    oude_factor = None
    vorig = laad_vorige_params()
    if vorig:
        bestaand = vorig.get("steden", {})
        oude_factor = vorig.get("band_factor")
        print(f"    bestaand bestand gevonden met {len(bestaand)} steden\n")

    enslog = laad_log(uitvoermap() / "logs" / "ensemble_log.csv")
    nwslog = laad_log(uitvoermap() / "logs" / "nws_log.csv")
    if enslog or nwslog:
        print(f"    logboeken: {len(enslog)} ensembleregels, {len(nwslog)} NWS regels\n")

    resultaat = {}
    for stad in weer.STEDEN:
        print(f"    {stad['naam']:<15}", end="", flush=True)
        try:
            fc = haal_previous_runs(stad, d1, d2)
        except Exception as ex:
            print(f"previous runs mislukt: {ex}")
            continue
        try:
            hf = haal_hist_forecast(stad, d1, d2)
        except Exception as ex:
            print(f"hist-forecast mislukt: {ex}")
            hf = {}
        try:
            act = haal_actuals(stad, d1, d2)
        except Exception as ex:
            print(f"METAR mislukt: {ex}")
            continue
        time.sleep(0.7)

        stad_uit = {}
        train = {}
        for h in (0, 1, 2):
            records = records_uit_enslog(enslog, act, stad["key"], h)
            if len(records) >= BURN_EVALUATIE + 15:
                train[str(h)] = f"ensemblelog({len(records)})"
            else:
                records = []
                if h == 0:
                    for dag, modellen in sorted(hf.items()):
                        if dag in act:
                            records.append((date.fromisoformat(dag).toordinal(), modellen, act[dag]))
                else:
                    for (hh, dag), modellen in sorted(fc.items()):
                        if hh != h or dag not in act:
                            continue
                        records.append((date.fromisoformat(dag).toordinal(), modellen, act[dag]))
            if len(records) < BURN_EVALUATIE + 10:
                print(f"h{h}: te weinig data ({len(records)})  ", end="")
                continue
            # Op horizon h is de verste geverifieerde dag die van h+1 dagen terug:
            # bij de run van vandaag is gisteren de laatste afgeronde dag.
            stad_uit[str(h)] = walk_forward(records, lag_dagen=h + 1)

        if stad_uit:
            stad_uit["bron"] = stad.get("bron", "iem")
            stad_uit["station"] = stad.get("station", "")
            if train:
                stad_uit["train"] = train
            nws = leer_nws(nwslog, act, stad_uit, stad["key"]) if stad["eenheid"] == "F" else {}
            if nws:
                stad_uit["nws"] = nws
            resultaat[stad["key"]] = stad_uit
            r1 = stad_uit.get("1", {})
            extra1m = f"  1min+{stad.pop('_1min')}" if "_1min" in stad else ""
            print(f"h1: {r1.get('mae_basis')}\u2192{r1.get('mae_nieuw')}  n={r1.get('n_totaal')}{extra1m}")
        else:
            print("overgeslagen")

    # ── Bandfactoren: gefit op de eerste helft, dekking gemeten op de tweede ──
    def kies_factor(veld):
        eerste, tweede = [], []
        for r in resultaat.values():
            for h in ("0", "1", "2"):
                reeks = r.get(h, {}).get(veld)
                if reeks:
                    m = len(reeks) // 2
                    eerste += reeks[:m]
                    tweede += reeks[m:]
        if not eerste:
            return 1.1, None
        fac = 1.6
        for k in [1.0 + 0.05 * i for i in range(13)]:
            raak = sum(1 for lo, hi, f in eerste if k * lo <= f <= k * hi)
            if raak / len(eerste) >= 0.795:
                fac = round(k, 2)
                break
        oos = None
        if tweede:
            oos = sum(1 for lo, hi, f in tweede if fac * lo <= f <= fac * hi) / len(tweede)
        return fac, oos

    if not any(h in r for r in resultaat.values() for h in ("0", "1", "2")):
        print("\n  Geen enkele stad leverde genoeg data op. Bestaand bestand blijft staan.")
        return 1
    fac_o, oos_o = kies_factor("dekkingsreeks")
    fac_s, oos_s = kies_factor("dekkingsreeks_s")
    print(f"\n  Bandfactor vast {weer.nl(fac_o, 2)} (dekking tweede helft "
          f"{weer.nl((oos_o or 0) * 100, 1)}%) \u00b7 spreidingsband {weer.nl(fac_s, 2)} "
          f"(dekking tweede helft {weer.nl((oos_s or 0) * 100, 1)}%)")

    breedtes_o, breedtes_s = [], []
    for r in resultaat.values():
        for h in ("0", "1", "2"):
            if h not in r:
                continue
            x = r[h]
            reeks_s = x.pop("dekkingsreeks_s")
            reeks_o = x.pop("dekkingsreeks")
            br_o = x.pop("breedte_o"); br_s = x.pop("breedte_s")
            x.pop("yhat_per_dag", None)
            m = len(reeks_s) // 2
            tw = reeks_s[m:]
            x["dekking"] = round(sum(1 for lo, hi, f in tw
                                     if fac_s * lo <= f <= fac_s * hi) / len(tw), 3) if tw else None
            if br_o and br_s:
                breedtes_o.append(sum(br_o) / len(br_o) * fac_o)
                breedtes_s.append(sum(br_s) / len(br_s) * fac_s)
            if x["res_q10"] is not None:
                x["res_q10"] = round(x["res_q10"] * fac_o, 2)
                x["res_q90"] = round(x["res_q90"] * fac_o, 2)
            if "qz10" in x:
                x["qz10"] = round(x["qz10"] * fac_s, 3)
                x["qz90"] = round(x["qz90"] * fac_s, 3)
    if breedtes_o:
        print(f"  Gemiddelde bandbreedte: vast {weer.nl(sum(breedtes_o) / len(breedtes_o), 2)}\u00b0 "
              f"\u2192 spreidingsband {weer.nl(sum(breedtes_s) / len(breedtes_s), 2)}\u00b0")

    # Alleen overstappen op de spreidingsband als de validatie dat rechtvaardigt
    co, cn, nc = 0.0, 0.0, 0
    for stad in weer.STEDEN:
        r = resultaat.get(stad["key"])
        if not r:
            continue
        f = 1.8 if stad["eenheid"] == "F" else 1.0
        for h in ("0", "1", "2"):
            x = r.get(h)
            if x and x.get("crps") and x.get("crps_oud"):
                co += x["crps_oud"] / f
                cn += x["crps"] / f
                nc += 1
    gebruik_s = (oos_s is not None and oos_o is not None
                 and abs(oos_s - 0.80) <= abs(oos_o - 0.80) + 0.02
                 and (nc == 0 or cn / nc <= co / nc + 0.02))
    if nc:
        print(f"  CRPS genormaliseerd: vast {weer.nl(co / nc, 2)} \u2192 "
              f"spreidingsband {weer.nl(cn / nc, 2)}")
    print(f"  Besluit: de app gebruikt de "
          f"{'SPREIDINGSBAND' if gebruik_s else 'VASTE band (spreidingsvariant won de validatie niet)'}")
    if not gebruik_s:
        for r in resultaat.values():
            for h in ("0", "1", "2"):
                if h in r:
                    for veld in ("qz10", "qz90", "sig_c", "sig_d", "s_gem"):
                        r[h].pop(veld, None)

    # ── Rapport ──
    crps_kop = "crps o\u2192n"
    print(f"\n  {'Stad':<15}{'hor':<4}{'oud':>7}{'kern':>7}{'winst':>7}"
          f"{'dekking':>9}{crps_kop:>12}{'n':>5}")
    print("  " + "\u2500" * 66)
    winsten, n_kern, n_tot = [], 0, 0
    for stad in weer.STEDEN:
        r = resultaat.get(stad["key"])
        if not r:
            continue
        for h in ("0", "1", "2"):
            if h not in r:
                continue
            x = r[h]
            n_tot += 1
            if "kern" in x:
                n_kern += 1
            winst = None
            if x["mae_oud"] and x["mae_nieuw"]:
                winst = (1 - x["mae_nieuw"] / x["mae_oud"]) * 100
                winsten.append(winst)
            if h == "1":
                merk = "*" if "kern" in x else " "
                print(f"  {stad['naam']:<15}{h:<4}"
                      f"{weer.nl(x['mae_oud']):>7}{weer.nl(x['mae_kern']) + merk:>7}"
                      f"{(weer.nl(winst, 0) + '%') if winst is not None else '?':>7}"
                      f"{(weer.nl(x['dekking'] * 100, 0) + '%') if x['dekking'] is not None else '?':>9}"
                      f"{weer.nl(x.get('crps_oud')):>6}\u2192{weer.nl(x.get('crps')):<5}{x['n_eval']:>5}")
    if winsten:
        print("  " + "\u2500" * 66)
        print(f"  Kern gekozen bij {n_kern} van de {n_tot} stad-horizonnen (*), "
              f"gemiddelde winst tegenover de oude correctie "
              f"{weer.nl(sum(winsten) / len(winsten), 1)}%\n")

    behouden = [k for k in bestaand if k not in resultaat]
    samen = dict(bestaand)
    samen.update(resultaat)
    if behouden:
        print(f"  Let op: {', '.join(sorted(behouden))} mislukte(n) deze keer, "
              f"de vorige parameters blijven voor die stad(en) staan.")

    payload = {"gegenereerd": date.today().isoformat(), "halfwaarde_dagen": HALFWAARDE,
               "band_factor": fac_o, "band_factor_s": fac_s,
               "spreidingsband": gebruik_s,
               "modellen": MODELLEN, "steden": samen}
    map_uit = uitvoermap()
    uit_json = map_uit / "app_params.json"
    uit_js = map_uit / "app_params.js"
    with open(uit_json, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    with open(uit_js, "w") as f:
        f.write("window.APP_PARAMS = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    print(f"  Geschreven in {map_uit}: app_params.json en app_params.js "
          f"({len(samen)} steden, {len(resultaat)} vernieuwd)\n")
    return 0


if __name__ == "__main__":
    n = 240
    if len(sys.argv) > 1:
        try:
            n = int(sys.argv[1])
        except ValueError:
            pass
    sys.exit(run(n))
