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

# Herkansingen op een haperende verbinding. Zonder deze verloor elke run vijf
# tot zeven van de 49 steden aan een TLS-handshake die niet rond kwam
# (`_ssl.c:993: The handshake operation timed out`): een kwaal van het moment,
# niet van de stad, want het waren elke keer andere steden. Een tweede poging
# even later haalt het gewoon. portfolio.py deed dit al voor zichzelf; hier
# staat het nu voor logger.py en signalen.py, die het allebei misten.
FETCH_POGINGEN = 3
FETCH_PAUZE    = 4.0     # seconden, oplopend per poging
# Korter dan de 60 s van haal_leden: een geslaagde aanroep is in een seconde
# terug, dus een handshake die na 30 s nog niet staat komt niet meer. Zonder
# die kortere grens kost elke mislukking een volle minuut en duurt de run met
# herkansingen langer dan hij nu al doet.
FETCH_TIMEOUT  = 30


def met_herkansing(haal, *args, pogingen: int = FETCH_POGINGEN,
                   pauze: float = FETCH_PAUZE, **kw):
    """Voert `haal` uit en probeert het opnieuw zolang de verbinding hapert.

    Blijft het misgaan, dan gaat de laatste reden mee omhoog met het aantal
    pogingen erbij, zodat in het logboek staat wat er misging en hoe vaak."""
    laatste = None
    for poging in range(pogingen):
        try:
            return haal(*args, **kw)
        except Exception as ex:                # noqa: BLE001 - reden gaat mee
            laatste = ex
            if poging + 1 < pogingen:
                time.sleep(pauze * (poging + 1))
    raise RuntimeError(f"{laatste} (na {pogingen} pogingen)") from laatste


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


# ── Gedeelde logboeken ────────────────────────────────────────────────────────
#
# GitHub weigert elk bestand boven de 100 MB. Dat is geen waarschuwing maar een
# pre-receive hook: de push wordt afgewezen en de hele run valt om.
#
# logs/signalen.csv liep daar op 19 september 2026 tegenaan, op 100,32 MB. Vanaf
# dat moment faalde de actie Signalenlog vier keer per dag met GH001. Het werk
# gebeurde wel -- achttien minuten markten ophalen, 3.702 regels wegschrijven --
# en werd bij het pushen weggegooid. Sinds die dag staat er geen enkele nieuwe
# regel meer in signalen.csv, ensemble_log.csv, taf_log.csv of nws_log.csv, want
# die vier gaan in dezelfde commit mee.
#
# Een groter logboek is dus niet traag maar kapot, en het herschrijven van de
# geschiedenis helpt daar niet tegen: het probleem is het bestand van vandaag.
# Daarom gaat een gedeeld logboek in stukken.
#
# De grens staat op 40 MB en niet vlak onder de 100. signalen.csv groeit met
# 2,96 MB per dag, dus per maand delen zou 89 MB geven -- elf procent marge, en
# dat is te krap voor een reeks die nog een kolom kan krijgen. Op 40 MB rolt hij
# om de dertien dagen om en zijn het er zo'n achtentwintig per jaar.
#
# De naam is de datum waarop een deel begon, zodat sorteren op naam hetzelfde is
# als sorteren op tijd en je aan de bestandsnaam ziet welk venster erin zit.
DEEL_GRENS = 40 * 1024 * 1024


def delen(naam: str, pad: Path = None) -> list:
    """De deelbestanden van een gedeeld logboek, oudste eerst.

    `pad` overschrijft alles en geeft precies dat ene bestand terug; de tests
    van inzet.py en portfolio.py geven op die manier hun eigen logboek mee.

    Een nog niet opgedeeld logs/<naam>.csv komt er vooraan bij. Daardoor blijft
    een oude werkkopie gewoon lezen, en hoeft de migratie niet in dezelfde
    commit te zitten als de code die ervan uitgaat."""
    if pad is not None:
        return [Path(pad)]
    m = logmap()
    uit = []
    oud = m / f"{naam}.csv"
    if oud.exists():
        uit.append(oud)
    return uit + sorted((m / naam).glob("*.csv"))


def _nieuw_deel(map_: Path) -> Path:
    """Een vrije naam voor een nieuw deel, op de datum van vandaag."""
    vandaag = datetime.now(timezone.utc).date().isoformat()
    pad = map_ / f"{vandaag}.csv"
    n = 2
    while pad.exists():               # twee keer omrollen op één dag
        pad = map_ / f"{vandaag}-{n}.csv"
        n += 1
    return pad


def schrijf_deel(naam: str, velden: list, rijen: list,
                 grens: int = DEEL_GRENS) -> Path:
    """Als schrijf(), maar naar het laatste deel van een gedeeld logboek.

    Rolt om zodra dat deel de grens haalt. De grens wordt gemeten vóór het
    schrijven, dus een deel mag er met één ronde overheen gaan; bij 40 MB en
    een ronde van 0,8 MB is dat ruim binnen de 100 die telt."""
    map_ = logmap() / naam
    map_.mkdir(exist_ok=True)
    bestaand = sorted(map_.glob("*.csv"))
    actief = bestaand[-1] if bestaand else None
    if actief is None or actief.stat().st_size >= grens:
        actief = _nieuw_deel(map_)
    schrijf(actief, velden, rijen)
    return actief


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
            per = met_herkansing(haal_leden, stad, timeout=FETCH_TIMEOUT)
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
            per_dag = met_herkansing(haal_nws, nws_url, timeout=FETCH_TIMEOUT)
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
