#!/usr/bin/env python3
"""
Wekelijkse verversing voor de GitHub Action.

  1. update : haalt de laatste dagen op (voorspellingen, weersvariabelen,
              stationswaarnemingen) en werkt uit_features/<stad>.csv bij
  2. refit  : herberekent klim_features.csv en hertraint per stad de gekozen
              variant (ridge, ridge_klim of ref_lin) plus het gepoolde model;
              schrijft modellen/modellen.json en modellen/pooled_gbm.pkl

Labels en varianten veranderen hier NIET; dat gebeurt alleen bij de
maandelijkse volledige herevaluatie (deel 3 rapport + deel 5 + deel 8).
Ongeveer 18 lichte API-aanroepen; elk onderdeel dat faalt wordt overgeslagen
en de rest gaat door. Venster instelbaar via omgevingsvariabele
WEERBOT_VENSTER (standaard 25 dagen).
"""
import csv, json, math, os, pickle, time, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.ensemble import HistGradientBoostingRegressor

HIER   = Path(__file__).parent
STEDEN = json.load(open(HIER / "steden.json"))
TZ     = json.load(open(HIER / "tijdzones.json"))
UITF   = HIER / "uit_features"
AUXV   = ["relative_humidity_2m_mean", "cloud_cover_mean", "wind_speed_10m_max",
          "shortwave_radiation_sum", "precipitation_sum"]
AUXK   = ["rh_gem", "bewolking_gem", "wind_max", "instraling_som", "neerslag_som"]
P1     = ["p1_ifs", "p1_aifs", "p1_gfs", "p1_icon", "p1_gem"]
FEATS  = P1 + ["mm_spreiding", "run2run", "doy_sin", "doy_cos", "lag2_err"] + AUXK
PREV   = {"ifs": "ecmwf_ifs025", "aifs": "ecmwf_aifs025_single",
          "gfs": "gfs_seamless", "icon": "icon_seamless", "gem": "gem_seamless"}
VENSTER = int(os.environ.get("WEERBOT_VENSTER", "25"))

def haal_json(url, pogingen=4):
    for p in range(pogingen):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "weerbot-week/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except Exception as e:
            if p == pogingen - 1:
                print(f"    OVERGESLAGEN: {e}")
                return None
            time.sleep(10 * (p + 1))

def haal_tekst(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "weerbot-week/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read().decode(errors="replace")
    except Exception as e:
        print(f"    OVERGESLAGEN: {e}")
        return ""

def groepen():
    for i in range(0, len(STEDEN), 17):
        yield STEDEN[i:i + 17]

def coord(groep):
    return ("?latitude=" + ",".join(str(s["lat"]) for s in groep) +
            "&longitude=" + ",".join(str(s["lon"]) for s in groep))

def als_lijst(d):
    return d if isinstance(d, list) else [d]

def dagmax(tijden, waarden, min_uren=12):
    per = {}
    for t, v in zip(tijden, waarden):
        if v is not None:
            per.setdefault(t[:10], []).append(v)
    return {d: max(v) for d, v in per.items() if len(v) >= min_uren}

def update():
    gister = date.today() - timedelta(days=1)
    b = (gister - timedelta(days=VENSTER)).isoformat(); e = gister.isoformat()
    print(f"update {b} t/m {e}")
    nieuw = {s["key"]: {} for s in STEDEN}     # key → datum → dict

    for groep in groepen():                     # ERA5 + weersvariabelen
        d = haal_json("https://archive-api.open-meteo.com/v1/archive" + coord(groep) +
                      "&daily=temperature_2m_max," + ",".join(AUXV) +
                      f"&start_date={b}&end_date={e}&temperature_unit=celsius&timezone=auto")
        if not d:
            continue
        for s, res in zip(groep, als_lijst(d)):
            dd = res.get("daily", {})
            for i, t in enumerate(dd.get("time", [])):
                rij = nieuw[s["key"]].setdefault(t, {})
                v = dd.get("temperature_2m_max", [None]*99)[i]
                if v is not None:
                    rij["era5_max"] = round(v, 1)
                for lang, kort in zip(AUXV, AUXK):
                    v = dd.get(lang, [None]*99)[i]
                    if v is not None:
                        rij[kort] = v
        time.sleep(2)

    paren = [("gfs",), ("ifs", "aifs"), ("icon", "gem")]
    for paar in paren:                          # previous-runs p1 en p2
        modellen = ",".join(PREV[k] for k in paar)
        for groep in groepen():
            d = haal_json("https://previous-runs-api.open-meteo.com/v1/forecast" + coord(groep) +
                          "&hourly=temperature_2m_previous_day1,temperature_2m_previous_day2"
                          f"&models={modellen}&start_date={b}&end_date={e}"
                          "&temperature_unit=celsius&timezone=auto")
            if not d:
                continue
            for s, res in zip(groep, als_lijst(d)):
                h = res.get("hourly", {}); tijden = h.get("time", [])
                for sleutel, reeks in h.items():
                    if sleutel == "time":
                        continue
                    kort = paar[0] if len(paar) == 1 else \
                           next((k for k in paar if PREV[k] in sleutel), None)
                    if kort is None:
                        continue
                    lead = "p1" if "previous_day1" in sleutel else "p2"
                    for dg, mx in dagmax(tijden, reeks).items():
                        nieuw[s["key"]].setdefault(dg, {})[f"{lead}_{kort}"] = round(mx, 1)
            time.sleep(2)

    stations = [s for s in STEDEN if s["key"] != "hongkong"]
    for c in range(0, len(stations), 10):       # IEM stationswaarnemingen
        blok = stations[c:c + 10]
        bd = date.fromisoformat(b)
        t = haal_tekst("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?" +
                       "&".join("station=" + (s["icao"][1:] if s["icao"].startswith("K")
                                and len(s["icao"]) == 4 else s["icao"]) for s in blok) +
                       f"&data=tmpf&year1={bd.year}&month1={bd.month}&day1={bd.day}"
                       f"&year2={gister.year}&month2={gister.month}&day2={gister.day}"
                       "&tz=Etc%2FUTC&format=comma&latlon=no&missing=M&trace=T"
                       "&direct=no&report_type=3")
        idmap = {(s["icao"][1:] if s["icao"].startswith("K") and len(s["icao"]) == 4
                  else s["icao"]): s["key"] for s in blok}
        agg = {}
        for regel in t.splitlines():
            d = regel.split(",")
            if len(d) < 3 or regel.startswith(("#", "station,")) or d[2] in ("M", ""):
                continue
            key = idmap.get(d[0])
            if not key:
                continue
            try:
                v = d[1]
                lok = datetime(int(v[0:4]), int(v[5:7]), int(v[8:10]), int(v[11:13]),
                               int(v[14:16]), tzinfo=timezone.utc).astimezone(ZoneInfo(TZ[key]))
                ee = agg.setdefault((key, lok.date().isoformat()), [0, -999.0, False])
                ee[0] += 1; ee[1] = max(ee[1], float(d[2]))
                if 10 <= lok.hour <= 18:
                    ee[2] = True
            except (ValueError, IndexError):
                continue
        for (key, dg), (n, mx, middag) in agg.items():
            if n >= 8 and middag:
                nieuw[key].setdefault(dg, {})["station_max"] = round((mx - 32) * 5 / 9, 1)

    d = haal_json("https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
                  f"?dataType=CLMMAXT&rformat=json&station=HKO&year={gister.year}")
    if d:
        for rij in d.get("data", []):
            try:
                dg = f"{int(rij[0]):04d}-{int(rij[1]):02d}-{int(rij[2]):02d}"
                if b <= dg <= e:
                    nieuw["hongkong"].setdefault(dg, {})["station_max"] = float(rij[3])
            except (ValueError, IndexError):
                continue

    kolommen = None; totaal = 0                 # samensmelten in uit_features
    for s in STEDEN:
        key = s["key"]; pad = UITF / f"{key}.csv"
        rijen = {r["datum"]: r for r in csv.DictReader(open(pad))}
        kolommen = kolommen or list(next(iter(rijen.values())).keys())
        for dg, w in sorted(nieuw[key].items()):
            r = rijen.setdefault(dg, {k: "" for k in kolommen})
            r["datum"] = dg
            for k in ["era5_max", "station_max"] + AUXK:
                if k in w:
                    r[k] = w[k]
            for k in P1 + [p.replace("p1", "p2") for p in P1]:
                kort = k.split("_")[1]
                bronk = f"{k[:2]}_{kort}"
                if bronk in w:
                    r[k] = w[bronk]
            p1v = [float(r[k]) for k in P1 if r[k]]
            if len(p1v) >= 4:
                mm = sum(p1v) / len(p1v)
                r["mm_gem"] = round(mm, 2)
                r["mm_spreiding"] = round(float(np.std(p1v, ddof=1)), 2)
                p2v = [float(r[k.replace("p1", "p2")]) for k in P1 if r[k.replace("p1", "p2")]]
                if len(p2v) >= 4:
                    r["run2run"] = round(mm - sum(p2v) / len(p2v), 2)
            hoek = date.fromisoformat(dg).timetuple().tm_yday / 365.25 * 2 * math.pi
            r["doy_sin"] = round(math.sin(hoek), 4); r["doy_cos"] = round(math.cos(hoek), 4)
            if key == "jinan":
                r["doel"], r["doelbron"] = r.get("era5_max", ""), "era5" if r.get("era5_max") else ""
            elif r.get("station_max"):
                r["doel"], r["doelbron"] = r["station_max"], "station"
            totaal += 1
        with open(pad, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=kolommen)
            w.writeheader()
            for dg in sorted(rijen):
                w.writerow(rijen[dg])
    print(f"  {totaal} stad-dagen bijgewerkt in uit_features/")

def _laad(key):
    rijen = list(csv.DictReader(open(UITF / f"{key}.csv")))
    kol = {k: np.array([float(r[k]) if r[k] else np.nan for r in rijen])
           for k in ["mm_gem", "mm_spreiding", "run2run", "doy_sin", "doy_cos",
                     "doel", "era5_max"] + P1 + AUXK}
    fout = kol["doel"] - kol["mm_gem"]
    lag = np.zeros(len(rijen))
    for i in range(len(rijen)):
        for dd in (2, 3):
            if i - dd >= 0 and np.isfinite(fout[i - dd]):
                lag[i] = fout[i - dd]; break
    kol["lag2_err"] = lag
    return [r["datum"] for r in rijen], kol

def _matrix(kol, idx, feats, med=None):
    X = []
    for k in feats:
        v = kol[k][idx].copy()
        if k in P1:
            v = np.where(np.isfinite(v), v, kol["mm_gem"][idx])
        if k == "run2run":
            v = np.where(np.isfinite(v), v, 0.0)
        X.append(v)
    X = np.column_stack(X)
    if med is None:
        med = np.nanmedian(X, axis=0); med = np.where(np.isfinite(med), med, 0.0)
    return np.where(np.isfinite(X), X, med), med

def refit():
    mod = json.load(open(HIER / "modellen" / "modellen.json"))
    sa_lin = json.load(open(HIER / "modellen" / "stagea_lineair.json"))
    # 1. klim_features volledig herberekenen
    with open(HIER / "klim_features.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["key", "datum", "klimL", "klimG"])
        for s in STEDEN:
            key = s["key"]
            datums, kol = _laad(key)
            inp = np.column_stack([kol["mm_gem"]] + [kol[a] for a in AUXK] +
                                  [kol["doy_sin"], kol["doy_cos"]])
            geldig = np.all(np.isfinite(inp), axis=1)
            e = sa_lin.get(key)
            if e:
                z = (inp - np.array(e["mu"])) / np.array(e["sd"])
                kl = z @ np.array(e["coef"]) + e["intercept"]
            else:
                kl = np.zeros(len(inp))
            kg = kl.copy()
            pad = HIER / "modellen" / f"stagea_gbm_{key}.pkl"
            if pad.exists():
                try:
                    sa = pickle.load(open(pad, "rb"))
                    kg[geldig] = sa["model"].predict(inp[geldig])
                except Exception:
                    pass
            for i in np.where(geldig)[0]:
                w.writerow([key, datums[i], round(float(kl[i]), 3), round(float(kg[i]), 3)])
    kl = {(r["key"], r["datum"]): float(r["klimG"])
          for r in csv.DictReader(open(HIER / "klim_features.csv"))}
    # 2. per stad de gekozen variant hertrainen
    Xp, yp = [], []
    extra = {s["key"]: (s["lat"], abs(s["lat"]), s["lon"], i) for i, s in enumerate(STEDEN)}
    n_her = 0
    for s in STEDEN:
        key = s["key"]; e = mod.get(key)
        datums, kol = _laad(key)
        tr = np.where(np.isfinite(kol["mm_gem"]) & np.isfinite(kol["doel"]))[0]
        if len(tr) < 100 or not e:
            continue
        y = kol["doel"][tr]
        X, med = _matrix(kol, tr, FEATS)
        Xp.append(np.column_stack([X] + [np.full(len(tr), v) for v in extra[key]]))
        yp.append(y)
        def zet(naam, A):
            mu, sd = A.mean(0), A.std(0); sd[sd == 0] = 1
            ri = Ridge(alpha=1.0).fit((A - mu) / sd, y)
            e[naam].update({"mu": mu.round(4).tolist(), "sd": sd.round(4).tolist(),
                            "med": med.round(4).tolist(),
                            "coef": ri.coef_.round(5).tolist(),
                            "intercept": round(float(ri.intercept_), 4)})
        if e.get("ridge"):
            zet("ridge", X)
        if e.get("variant") == "ridge_klim" and e.get("ridge_klim"):
            zet("ridge_klim", np.column_stack([X, [kl.get((key, datums[i]), 0.0) for i in tr]]))
        if e.get("ref_lin"):
            A = np.column_stack([np.ones(len(tr)), kol["mm_gem"][tr], kol["lag2_err"][tr]])
            co, *_ = np.linalg.lstsq(A, y, rcond=None)
            e["ref_lin"] = {"a": round(float(co[0]), 4), "b": round(float(co[1]), 4),
                            "g": round(float(co[2]), 4)}
        e["hertraind"] = date.today().isoformat()
        n_her += 1
    pg = HistGradientBoostingRegressor(max_iter=150, learning_rate=0.07, max_leaf_nodes=31,
         min_samples_leaf=40, l2_regularization=1.0,
         categorical_features=[len(FEATS) + 3], random_state=1)
    pg.fit(np.vstack(Xp), np.concatenate(yp))
    pickle.dump({"model": pg, "features": FEATS + ["lat", "lat_abs", "lon", "stad_idx"],
                 "stad_idx": {s["key"]: i for i, s in enumerate(STEDEN)}},
                open(HIER / "modellen" / "pooled_gbm.pkl", "wb"))
    json.dump(mod, open(HIER / "modellen" / "modellen.json", "w"),
              ensure_ascii=False, indent=1)
    print(f"  {n_her} steden hertraind · pooled hertraind · modellen.json bijgewerkt")

if __name__ == "__main__":
    update()
    refit()
    print("wekelijkse verversing klaar")
