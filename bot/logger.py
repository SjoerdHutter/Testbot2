"""Dagelijkse voorspellingslog voor de weerbot.

Legt per stad vast wat de app daadwerkelijk toont: het ledengemiddelde per
ensemblesysteem per doeldag, plus de NWS dagverwachting voor de Amerikaanse
steden. Na 75 gelogde dagen kalibreert kalibratie.py rechtstreeks op deze
reeks, waarmee het verschil tussen trainen (deterministisch archief) en tonen
(ensemblegemiddelden) verdwijnt. Na 40 dagen wordt het NWS gewicht geleerd.
"""
import csv, json, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weer

ENS = {"ecmwf_ifs025": "ecmwf_ifs025", "ecmwf_aifs025": "ecmwf_aifs025",
       "gfs_seamless": "ncep_gefs025", "icon_seamless": "icon_seamless",
       "gem_seamless": "gem_global"}
NWS_URLS = json.loads((Path(__file__).resolve().parent / "nws_urls.json").read_text()) \
    if (Path(__file__).resolve().parent / "nws_urls.json").exists() else {}


def logmap() -> Path:
    m = Path.cwd() / "logs"
    m.mkdir(exist_ok=True)
    return m


def schrijf(pad: Path, velden: list, rijen: list) -> None:
    nieuw = not pad.exists()
    with open(pad, "a", newline="") as f:
        w = csv.writer(f)
        if nieuw:
            w.writerow(velden)
        w.writerows(rijen)


def run() -> int:
    nu = datetime.now(timezone.utc).isoformat(timespec="minutes")
    ens_rijen, nws_rijen, fouten = [], [], 0
    for stad in weer.STEDEN:
        unit = "fahrenheit" if stad["eenheid"] == "F" else "celsius"
        url = ("https://ensemble-api.open-meteo.com/v1/ensemble"
               f"?latitude={stad['lat']}&longitude={stad['lon']}"
               f"&daily=temperature_2m_max&models={','.join(ENS.values())}"
               f"&temperature_unit={unit}&forecast_days=3"
               f"&timezone={urllib.parse.quote(stad['tz'])}")
        try:
            d = weer._get_json(url, timeout=60)
        except Exception as e:
            print(f"  {stad['key']}: ensemble mislukt ({e})")
            fouten += 1
            time.sleep(1)
            continue
        vandaag_lokaal = datetime.now(ZoneInfo(stad["tz"])).date()
        per: dict = {}
        for kol, reeks in d.get("daily", {}).items():
            if not kol.startswith("temperature_2m_max"):
                continue
            for model in ENS.values():
                if model in kol:
                    for dag, wrd in zip(d["daily"]["time"], reeks):
                        if wrd is not None:
                            per.setdefault((dag, model), []).append(wrd)
                    break
        for (dag, model), leden in sorted(per.items()):
            lead = (date.fromisoformat(dag) - vandaag_lokaal).days
            if 0 <= lead <= 2:
                ens_rijen.append([nu, stad["key"], dag, lead, model,
                                  round(sum(leden) / len(leden), 2), len(leden)])
        time.sleep(0.6)

    for key, nws_url in NWS_URLS.items():
        stad = weer.STAD_OP_KEY.get(key)
        if not stad:
            continue
        try:
            req = urllib.request.Request(nws_url, headers={"User-Agent": "weerbot/1.0"})
            with urllib.request.urlopen(req, timeout=45) as r:
                periodes = json.loads(r.read())["properties"]["periods"]
        except Exception as e:
            print(f"  {key}: NWS mislukt ({e})")
            fouten += 1
            continue
        vandaag_lokaal = datetime.now(ZoneInfo(stad["tz"])).date()
        for p in periodes:
            if not p.get("isDaytime"):
                continue
            dag = p["startTime"][:10]
            lead = (date.fromisoformat(dag) - vandaag_lokaal).days
            if 0 <= lead <= 1 and isinstance(p.get("temperature"), (int, float)):
                nws_rijen.append([nu, key, dag, lead, p["temperature"]])
        time.sleep(0.6)

    if ens_rijen:
        schrijf(logmap() / "ensemble_log.csv",
                ["gelogd_utc", "key", "doel_datum", "lead", "model", "gemiddelde", "n_leden"],
                ens_rijen)
    if nws_rijen:
        schrijf(logmap() / "nws_log.csv",
                ["gelogd_utc", "key", "doel_datum", "lead", "temp_f"], nws_rijen)
    print(f"Gelogd: {len(ens_rijen)} ensembleregels, {len(nws_rijen)} NWS regels, {fouten} fouten")
    return 0 if len(ens_rijen) > 100 else 1


if __name__ == "__main__":
    sys.exit(run())
