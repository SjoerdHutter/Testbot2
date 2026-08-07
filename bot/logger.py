"""Dagelijkse voorspellingslog voor de weerbot.

Legt per stad vast wat de app daadwerkelijk toont: het ledengemiddelde per
ensemblesysteem per doeldag, plus de NWS dagverwachting voor de Amerikaanse
steden. Na 75 gelogde dagen kalibreert kalibratie.py rechtstreeks op deze
reeks, waarmee het verschil tussen trainen (deterministisch archief) en tonen
(ensemblegemiddelden) verdwijnt. Na 40 dagen wordt het NWS gewicht geleerd.

Naast het gemiddelde gaat sinds deze versie ook de spreiding van de leden mee:
sd en zes kwantielen. Een gemiddelde alleen zegt niets over hoe eens de leden
het waren, en juist die onenigheid is wat een kans per temperatuurvak breed of
smal maakt. De ledenwaarden zelf blijven in haal_leden beschikbaar, zodat
signalen.py er de ledenfractie per vak uit kan halen zonder tweede aanroep.
"""
import csv, json, statistics, sys, time, urllib.parse, urllib.request
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

# De kop van logs/ensemble_log.csv. De eerste zeven kolommen staan er vanaf het
# begin en blijven op hun plek; de spreidingskolommen zijn erachter geplakt.
ENS_KOP = ["gelogd_utc", "key", "doel_datum", "lead", "model", "gemiddelde", "n_leden",
           "sd", "min", "p10", "p25", "p50", "p75", "p90", "max"]
KWANTIELEN = [0.10, 0.25, 0.50, 0.75, 0.90]


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


def ensemble_url(stad: dict, velden: str = "temperature_2m_max") -> str:
    unit = "fahrenheit" if stad["eenheid"] == "F" else "celsius"
    return ("https://ensemble-api.open-meteo.com/v1/ensemble"
            f"?latitude={stad['lat']}&longitude={stad['lon']}"
            f"&daily={velden}&models={','.join(ENS.values())}"
            f"&temperature_unit={unit}&forecast_days=3"
            f"&timezone={urllib.parse.quote(stad['tz'])}")


def haal_leden(stad: dict, velden: str = "temperature_2m_max", timeout: int = 60) -> dict:
    """De losse ensembleleden van een stad: {(soort, doeldag, model): [waarden]}.

    De leden worden hier bewust niet meteen samengevat. Het gemiddelde is maar
    een van de dingen die je eruit wilt halen; de spreiding hieronder en de
    ledenfractie per temperatuurvak in signalen.py hebben de reeks zelf nodig."""
    d = weer._get_json(ensemble_url(stad, velden), timeout=timeout)
    daily = d.get("daily", {})
    tijden = daily.get("time", [])
    per: dict = {}
    for kol, reeks in daily.items():
        if kol.startswith("temperature_2m_max"):
            soort = "max"
        elif kol.startswith("temperature_2m_min"):
            soort = "min"
        else:
            continue
        for model in ENS.values():
            if model in kol:
                for dag, wrd in zip(tijden, reeks):
                    if wrd is not None:
                        per.setdefault((soort, dag, model), []).append(wrd)
                break
    return per


def spreiding_van(leden: list) -> list:
    """De spreidingskolommen bij een ledenreeks: sd, min, p10, p25, p50, p75,
    p90, max. De sd is de steekproefstandaarddeviatie (deler n-1), de kwantielen
    interpoleren lineair tussen de ordestatistieken (weer.pctl, dezelfde
    definitie die numpy standaard gebruikt). Bij een enkel lid bestaat er geen
    sd; die blijft dan leeg en elk kwantiel is dat ene lid."""
    g = sorted(leden)
    sd = round(statistics.stdev(g), 2) if len(g) >= 2 else ""
    kwant = [round(weer.pctl(g, q), 2) for q in KWANTIELEN]
    return [sd, round(g[0], 2)] + kwant + [round(g[-1], 2)]


def haal_nws(nws_url: str, timeout: int = 45) -> dict:
    """De NWS dagverwachting per doeldatum: {datum: temperatuur in F}. Alleen de
    dagperiodes tellen mee, net als in de app."""
    req = urllib.request.Request(nws_url, headers={"User-Agent": "weerbot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        periodes = json.loads(r.read())["properties"]["periods"]
    uit = {}
    for p in periodes:
        if p.get("isDaytime") and isinstance(p.get("temperature"), (int, float)):
            uit[str(p["startTime"])[:10]] = p["temperature"]   # laatste wint, net als in de app
    return uit


def run() -> int:
    nu = datetime.now(timezone.utc).isoformat(timespec="minutes")
    ens_rijen, nws_rijen, fouten = [], [], 0
    for stad in weer.STEDEN:
        try:
            per = haal_leden(stad)
        except Exception as e:
            print(f"  {stad['key']}: ensemble mislukt ({e})")
            fouten += 1
            time.sleep(1)
            continue
        vandaag_lokaal = datetime.now(ZoneInfo(stad["tz"])).date()
        for (soort, dag, model), leden in sorted(per.items()):
            if soort != "max":
                continue
            lead = (date.fromisoformat(dag) - vandaag_lokaal).days
            if 0 <= lead <= 2:
                ens_rijen.append([nu, stad["key"], dag, lead, model,
                                  round(sum(leden) / len(leden), 2), len(leden)]
                                 + spreiding_van(leden))
        time.sleep(0.6)

    for key, nws_url in NWS_URLS.items():
        stad = weer.STAD_OP_KEY.get(key)
        if not stad:
            continue
        try:
            per_dag = haal_nws(nws_url)
        except Exception as e:
            print(f"  {key}: NWS mislukt ({e})")
            fouten += 1
            continue
        vandaag_lokaal = datetime.now(ZoneInfo(stad["tz"])).date()
        for dag in sorted(per_dag):
            lead = (date.fromisoformat(dag) - vandaag_lokaal).days
            if 0 <= lead <= 1:
                nws_rijen.append([nu, key, dag, lead, per_dag[dag]])
        time.sleep(0.6)

    if ens_rijen:
        schrijf(logmap() / "ensemble_log.csv", ENS_KOP, ens_rijen)
    if nws_rijen:
        schrijf(logmap() / "nws_log.csv",
                ["gelogd_utc", "key", "doel_datum", "lead", "temp_f"], nws_rijen)
    print(f"Gelogd: {len(ens_rijen)} ensembleregels, {len(nws_rijen)} NWS regels, {fouten} fouten")
    return 0 if len(ens_rijen) > 100 else 1


if __name__ == "__main__":
    sys.exit(run())
