#!/usr/bin/env python3
"""
Dagelijkse berekening voor de app: schrijft klim_vandaag.json met per stad en
per lokale datum (vandaag, morgen, overmorgen):
  · de stage-A microklimaatdelta (°C) voor de ridge_klim-steden
  · de pooled-voorspelling (°C) voor de GEPOOLD-steden (nu alleen Austin)

Draait in de dagelijkse GitHub Action; vier lichte API-aanroepen totaal.
Alles in graden Celsius; de app rekent zelf om naar de markteenheid.
"""
import json, math, pickle, time, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np

HIER   = Path(__file__).parent
STEDEN = {s["key"]: s for s in json.load(open(HIER / "steden.json"))}
TZ     = json.load(open(HIER / "tijdzones.json"))
MOD    = json.load(open(HIER / "modellen" / "modellen.json"))
AUXV   = ["relative_humidity_2m_mean", "cloud_cover_mean", "wind_speed_10m_max",
          "shortwave_radiation_sum", "precipitation_sum"]
MODELLEN = ["ecmwf_ifs025", "ecmwf_aifs025_single", "gfs_seamless",
            "icon_seamless", "gem_seamless"]
FEATS = (["p1_ecmwf_ifs025", "p1_ecmwf_aifs025", "p1_gfs_seamless",
          "p1_icon_seamless", "p1_gem_seamless", "mm_spreiding", "run2run",
          "doy_sin", "doy_cos", "lag2_err", "rh_gem", "bewolking_gem",
          "wind_max", "instraling_som", "neerslag_som"])

KLIM_STEDEN   = [k for k, m in MOD.items() if m.get("variant") == "ridge_klim"]
POOLED_STEDEN = [k for k, m in MOD.items() if m.get("label") == "GEPOOLD"]
ALLE = KLIM_STEDEN + POOLED_STEDEN

# Herkansingen op elke aanroep. Zonder deze wikkel kostte één hapering van de
# weer-API de hele dagberekening: op 21 september 2026 gaf Open-Meteo een
# HTTP 503 en viel de actie klim-dagelijks daarop om, terwijl de volgende poging
# het gewoon gedaan zou hebben. bot/logger.py had zo'n wikkel al (met_herkansing);
# dit bestand miste hem als enige van de ophalende scripts.
#
# Drie pogingen met oplopende pauze. Een 503 en een dichtgevallen verbinding zijn
# allebei tijdelijk en allebei een uitzondering, dus er wordt niet op soort
# gefilterd; blijft het misgaan, dan gaat de laatste reden met het aantal
# pogingen erbij omhoog en valt de run alsnog om -- zichtbaar, en met de oorzaak
# erbij.
POGINGEN = 3
PAUZE    = 4.0          # seconden, oplopend per poging

def met_herkansing(fn, *args, pogingen: int = POGINGEN, pauze: float = PAUZE):
    laatste = None
    for poging in range(pogingen):
        try:
            return fn(*args)
        except Exception as ex:               # noqa: BLE001 - reden gaat mee
            laatste = ex
            if poging + 1 < pogingen:
                time.sleep(pauze * (poging + 1))
    raise RuntimeError(f"{laatste} (na {pogingen} pogingen)") from laatste

def _haal_een(url):
    req = urllib.request.Request(url, headers={"User-Agent": "weerbot-klim/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read()) if url.split("?")[0].endswith(("forecast", "archive")) \
               or "open-meteo" in url else r.read().decode()

def haal(url):
    return met_herkansing(_haal_een, url)

def _haal_tekst_een(url):
    req = urllib.request.Request(url, headers={"User-Agent": "weerbot-klim/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode(errors="replace")

def haal_tekst(url):
    return met_herkansing(_haal_tekst_een, url)

def coord(keys):
    la = ",".join(str(STEDEN[k]["lat"]) for k in keys)
    lo = ",".join(str(STEDEN[k]["lon"]) for k in keys)
    return f"?latitude={la}&longitude={lo}"

def als_lijst(d):
    return d if isinstance(d, list) else [d]

def dagmax(tijden, waarden):
    per = {}
    for t, v in zip(tijden, waarden):
        if v is not None:
            per.setdefault(t[:10], []).append(v)
    return {d: max(v) for d, v in per.items() if len(v) >= 12}

def main():
    if not ALLE:
        print("geen klim- of pooled-steden in modellen.json; niets te doen")
        return
    # 1. live voorspelling per model (daghoogsten, °C)
    d1 = haal("https://api.open-meteo.com/v1/forecast" + coord(ALLE) +
              "&daily=temperature_2m_max&models=" + ",".join(MODELLEN) +
              "&forecast_days=4&temperature_unit=celsius&timezone=auto")
    # 2. weersvariabelen (best match)
    d2 = haal("https://api.open-meteo.com/v1/forecast" + coord(ALLE) +
              "&daily=" + ",".join(AUXV) +
              "&forecast_days=4&temperature_unit=celsius&timezone=auto")
    # 3. vorige run (voor run2run en de lagfout van de pooled-steden)
    vandaag = date.today()
    d3 = haal("https://previous-runs-api.open-meteo.com/v1/forecast" + coord(ALLE) +
              "&hourly=temperature_2m_previous_day1,temperature_2m_previous_day2"
              f"&start_date={(vandaag - timedelta(days=4)).isoformat()}"
              f"&end_date={(vandaag + timedelta(days=2)).isoformat()}"
              "&temperature_unit=celsius&timezone=auto")
    # 4. recente stationswaarnemingen voor de lagfout van de pooled-steden
    obs = {}
    for k in POOLED_STEDEN:
        ic = STEDEN[k]["icao"]
        iem = ic[1:] if ic.startswith("K") and len(ic) == 4 else ic
        b = (vandaag - timedelta(days=5))
        t = haal_tekst("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
                       f"?station={iem}&data=tmpf&year1={b.year}&month1={b.month}&day1={b.day}"
                       f"&year2={vandaag.year}&month2={vandaag.month}&day2={vandaag.day}"
                       "&tz=Etc%2FUTC&format=comma&latlon=no&missing=M&trace=T"
                       "&direct=no&report_type=3")
        tz = ZoneInfo(TZ[k]); per = {}
        for regel in t.splitlines():
            d = regel.split(",")
            if len(d) < 3 or regel.startswith(("#", "station,")) or d[2] in ("M", ""):
                continue
            try:
                v = d[1]
                lok = datetime(int(v[0:4]), int(v[5:7]), int(v[8:10]), int(v[11:13]),
                               int(v[14:16]), tzinfo=timezone.utc).astimezone(tz)
                per.setdefault(lok.date().isoformat(), []).append(float(d[2]))
            except (ValueError, IndexError):
                continue
        obs[k] = {d: round((max(v) - 32) * 5 / 9, 1) for d, v in per.items() if len(v) >= 8}

    sa_lin = json.load(open(HIER / "modellen" / "stagea_lineair.json"))
    uit = {"gegenereerd": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "eenheid": "°C", "steden": {}}
    for i, k in enumerate(ALLE):
        r1, r2, r3 = als_lijst(d1)[i], als_lijst(d2)[i], als_lijst(d3)[i]
        tijden = r1["daily"]["time"]
        p1 = {m: r1["daily"].get(f"temperature_2m_max_{m}",
                                 r1["daily"].get("temperature_2m_max", []))
              for m in MODELLEN}
        aux = {v: r2["daily"].get(v, [None] * len(tijden)) for v in AUXV}
        prev1 = dagmax(r3["hourly"]["time"], r3["hourly"]["temperature_2m_previous_day1"])
        prev2 = dagmax(r3["hourly"]["time"], r3["hourly"]["temperature_2m_previous_day2"])
        tz = ZoneInfo(TZ[k]); lokaal = datetime.now(tz).date()
        doel_datums = [(lokaal + timedelta(days=n)).isoformat() for n in range(3)]
        stad_uit = {}
        for dg in doel_datums:
            if dg not in tijden:
                continue
            ix = tijden.index(dg)
            p1v = {m: p1[m][ix] for m in MODELLEN if ix < len(p1[m]) and p1[m][ix] is not None}
            if len(p1v) < 4:
                continue
            mm = float(np.mean(list(p1v.values())))
            sp = float(np.std(list(p1v.values()), ddof=1))
            doy = date.fromisoformat(dg).timetuple().tm_yday / 365.25 * 2 * math.pi
            a = [aux[v][ix] for v in AUXV]
            rij = {}
            if k in KLIM_STEDEN and all(x is not None for x in a):
                sa_pad = HIER / "modellen" / f"stagea_gbm_{k}.pkl"
                X = np.array([[mm] + a + [math.sin(doy), math.cos(doy)]])
                try:
                    sa = pickle.load(open(sa_pad, "rb"))
                    rij["klim"] = round(float(sa["model"].predict(X)[0]), 3)
                except Exception:
                    e = sa_lin.get(k)
                    if e:
                        z = (X[0] - np.array(e["mu"])) / np.array(e["sd"])
                        rij["klim"] = round(float(z @ np.array(e["coef"]) + e["intercept"]), 3)
            if k in POOLED_STEDEN:
                pg = pickle.load(open(HIER / "modellen" / "pooled_gbm.pkl", "rb"))
                r2r = mm - float(np.mean([prev1.get(dg, mm)]))  # huidige run t.o.v. vorige
                d2d = (date.fromisoformat(dg) - timedelta(days=2)).isoformat()
                lag = 0.0
                if d2d in obs.get(k, {}) and d2d in prev1:
                    lag = obs[k][d2d] - prev1[d2d]
                w = {"p1_ecmwf_ifs025": p1v.get("ecmwf_ifs025", mm),
                     "p1_ecmwf_aifs025": p1v.get("ecmwf_aifs025_single", mm),
                     "p1_gfs_seamless": p1v.get("gfs_seamless", mm),
                     "p1_icon_seamless": p1v.get("icon_seamless", mm),
                     "p1_gem_seamless": p1v.get("gem_seamless", mm),
                     "mm_spreiding": sp, "run2run": r2r,
                     "doy_sin": math.sin(doy), "doy_cos": math.cos(doy), "lag2_err": lag,
                     "rh_gem": a[0], "bewolking_gem": a[1], "wind_max": a[2],
                     "instraling_som": a[3], "neerslag_som": a[4]}
                if all(w[f] is not None for f in FEATS):
                    s = STEDEN[k]
                    idx = pg["stad_idx"][k]
                    Xp = np.array([[w[f] for f in FEATS] +
                                   [s["lat"], abs(s["lat"]), s["lon"], idx]])
                    rij["pooled"] = round(float(pg["model"].predict(Xp)[0]), 2)
            if rij:
                stad_uit[dg] = rij
        uit["steden"][k] = stad_uit
    with open(HIER / "klim_vandaag.json", "w") as f:
        json.dump(uit, f, ensure_ascii=False, indent=1)
    print("klim_vandaag.json geschreven:")
    for k, v in uit["steden"].items():
        print(f"  {k:<10} {v}")

if __name__ == "__main__":
    main()
