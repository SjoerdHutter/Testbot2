#!/usr/bin/env python3
"""Signalenlog: wat het model dacht en wat de markt vroeg, per temperatuurvak.

Schrijft logs/signalen.csv: een regel per doeldag, stad, reeks (hoogste of
laagste) en temperatuurvak, ongeacht of er gehandeld is. Juist de niet-genomen
vakjes horen erbij: zonder die regels meet je alleen de eigen selectie en niets
over het model.

Per regel staan er twee onafhankelijke kansschattingen naast de marktprijs:

  model_kans      de normale verdeling uit de verwachting en de 80%-band,
                  cijfer voor cijfer dezelfde functie als onzeKansen in
                  weerbot-modellen/polymarkt.js (bewaakt door bot/test_kern.py)
  leden_fractie   de kale fractie ensembleleden die in het vak valt

Achteraf is daarmee te zien welke van de twee beter kalibreert, en of de
gerealiseerde hitrate boven de betaalde prijs ligt, uitgesplitst per edge.

De tabel met stadssleutels, de maandnamen en de drempels van strategie A worden
uit polymarkt.js gelezen in plaats van hier overgetypt: één bron van waarheid,
zodat een wijziging in de app niet stilletjes langs dit logboek loopt.

Gebruik (vanuit de hoofdmap van de repo):

    python3 bot/signalen.py              alle steden, drie doeldagen
    python3 bot/signalen.py --steden NYC,LON
    python3 bot/signalen.py --dagen 1    alleen vandaag

Alleen de standaardbibliotheek, en alleen leesverzoeken: naar de ensemble-API
van Open-Meteo, naar api.weather.gov voor de Amerikaanse bijmenging en naar de
publieke Gamma-API van Polymarket.
"""
import json
import math
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weer
import logger
import kalibratie as K

WORTEL = Path(__file__).resolve().parent.parent
POLY_JS = WORTEL / "weerbot-modellen" / "polymarkt.js"
GAMMA = "https://gamma-api.polymarket.com/events"
GAMMA_BUNDEL = 20        # aantal slugs per verzoek; de API accepteert er meerdere
ENS_VELDEN = "temperature_2m_max,temperature_2m_min"
MINKAL_MIN_N = 8         # MINKAL.minN in index.html: onder dit aantal geen ijking
NWS_GEWICHT = 0.25       # CONFIG.nws_gewicht in index.html
NWS_MAX_VERSCHIL = 15    # verder uit elkaar dan dit mengt de app niet bij

KOP = ["gelogd_utc", "key", "doel_datum", "lead", "soort", "eenheid",
       "bracket_label", "bracket_lo", "bracket_hi", "verwachting", "p10", "p90",
       "model_kans", "leden_fractie", "markt_prijs", "edge_pp", "volume_24u",
       "event_slug", "markt_slug", "strat_a_signaal",
       # Achteraan bijgeplakt, zodat de regels van voor deze kolommen geldig
       # blijven. Die krijgen hier lege velden en worden niet nageschat.
       "uren_tot_sluiting", "einde_api"]


# ── polymarkt.js als bron van waarheid ────────────────────────────────────────

def _js_letterlijk(naam: str, tekst: str):
    """De waarde van `var <naam> = {...};` of `var <naam> = [...];` uit een
    javascriptbestand, als python-waarde. Genoeg voor de drie tabellen die we
    delen: sleutels zonder aanhalingstekens, verder alleen getallen en teksten."""
    m = re.search(r"\bvar\s+" + re.escape(naam) + r"\s*=\s*", tekst)
    if not m:
        raise ValueError(f"{naam} niet gevonden in {POLY_JS.name}")
    begin = m.end()
    open_teken = tekst[begin]
    sluit = {"{": "}", "[": "]"}.get(open_teken)
    if not sluit:
        raise ValueError(f"{naam} is geen object of lijst")
    diep, eind = 0, None
    for i in range(begin, len(tekst)):
        if tekst[i] == open_teken:
            diep += 1
        elif tekst[i] == sluit:
            diep -= 1
            if diep == 0:
                eind = i + 1
                break
    if eind is None:
        raise ValueError(f"{naam} loopt niet netjes af")
    blok = tekst[begin:eind]
    blok = re.sub(r"/\*.*?\*/", "", blok, flags=re.S)        # blokcommentaar eruit
    blok = re.sub(r"//[^\n]*", "", blok)                     # regelcommentaar eruit
    blok = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', blok)
    blok = re.sub(r",\s*([}\]])", r"\1", blok)               # komma voor het slot
    return json.loads(blok)


_POLY = POLY_JS.read_text()
SLUG = _js_letterlijk("SLUG", _POLY)
MAAND = _js_letterlijk("MAAND", _POLY)
STRAT_A = _js_letterlijk("STRAT_A", _POLY)


def slug_van(stad_key: str, datum_iso: str, soort: str):
    """Zelfde slug als slugVan in polymarkt.js."""
    c = SLUG.get(stad_key)
    if not c:
        return None
    d = int(datum_iso[8:10])
    m = MAAND[int(datum_iso[5:7]) - 1]
    j = datum_iso[0:4]
    return ("lowest" if soort == "min" else "highest") + \
           "-temperature-in-" + c + "-on-" + m + "-" + str(d) + "-" + j


# ── Vakken lezen en kansen rekenen (port van polymarkt.js) ────────────────────

def vak_uit(titel):
    """De grenzen van een temperatuurvak uit de vaknaam, zoals vakUit in
    polymarkt.js. De markt rekent af op hele graden, dus het echte vak loopt van
    lo-0,5 tot hi+0,5; dat gebeurt pas in onze_kansen."""
    t = re.sub(r"\s+", " ", str(titel if titel is not None else "")
               .replace("−", "-").replace("–", "-").replace("—", "-")).strip()
    e = "°C" if re.search(r"°\s*C", t, re.I) else \
        ("°F" if re.search(r"°\s*F", t, re.I) else None)
    laag = bool(re.search(r"below|lower|under|or less", t, re.I))
    hoog = bool(re.search(r"higher|above|over|or more", t, re.I))
    if not laag and not hoog:
        r = re.search(r"(-?\d+)\s*-\s*(-?\d+)\s*°", t)
        if r:
            return {"lo": int(r.group(1)), "hi": int(r.group(2)), "eenheid": e}
    g = re.findall(r"-?\d+", t)
    if not g:
        return None
    n = int(g[0])
    if laag:
        return {"lo": None, "hi": n, "eenheid": e}
    if hoog:
        return {"lo": n, "hi": None, "eenheid": e}
    return {"lo": n, "hi": n, "eenheid": e}


def phi(z: float) -> float:
    """Normale verdeling, Abramowitz & Stegun 7.1.26. Exact dezelfde reeks als
    Phi in polymarkt.js; math.erf zou hier andere laatste cijfers geven."""
    t = 1 / (1 + 0.2316419 * abs(z))
    d = 0.3989422804014327 * math.exp(-z * z / 2)
    p = d * t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 +
                 t * (-1.821255978 + t * 1.330274429))))
    return 1 - p if z > 0 else p


def naar_eenheid(v, van, naar):
    if v is None or van == naar:
        return v
    return v * 9 / 5 + 32 if naar == "°F" else (v - 32) * 5 / 9


def delta_naar(v, van, naar):
    if v is None or van == naar:
        return v
    return v * 9 / 5 if naar == "°F" else v * 5 / 9


def onze_kansen(vakken: list, dag: dict, markt_eenheid, app_eenheid):
    """De eigen kans per vak, cijfer voor cijfer gelijk aan onzeKansen in
    polymarkt.js: een normale verdeling met sigma = (p90 - p10) / (2 * 1,2816),
    ondergrens 0,05, en de halve-graad randcorrectie op lo en hi."""
    if not dag or not vakken:
        return None
    mu = naar_eenheid(dag.get("verwachting"), app_eenheid, markt_eenheid)
    breedte = delta_naar(dag.get("p90") - dag.get("p10"), app_eenheid, markt_eenheid)
    if mu is None or not (breedte > 0):
        return None
    sigma = breedte / (2 * 1.2815515655446004)
    if not (sigma > 0.05):
        sigma = 0.05
    uit = []
    for b in vakken:
        boven = 1 if b["hi"] is None else phi((b["hi"] + 0.5 - mu) / sigma)
        onder = 0 if b["lo"] is None else phi((b["lo"] - 0.5 - mu) / sigma)
        uit.append(max(0, min(1, boven - onder)))
    return uit


# ── Markt ophalen ─────────────────────────────────────────────────────────────

def _getal(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _lijst_uit(x):
    if isinstance(x, list):
        return x
    if not isinstance(x, str):
        return []
    try:
        return json.loads(x) or []
    except ValueError:
        return []


def verwerk_event(e: dict, slug: str) -> dict:
    """Hetzelfde vakoverzicht als verwerk() in polymarkt.js: per markt de
    grenzen, de Ja-prijs, het volume en de spread, op temperatuur gesorteerd."""
    vakken = []
    for m in e.get("markets", []):
        namen = _lijst_uit(m.get("outcomes"))
        prijzen = [_getal(p) for p in _lijst_uit(m.get("outcomePrices"))]
        i = 0
        for k, naam in enumerate(namen):
            if str(naam).lower() == "yes":
                i = k
                break
        v = vak_uit(m.get("groupItemTitle") or m.get("question"))
        if not v or (v["lo"] is None and v["hi"] is None):
            continue
        vakken.append({
            "label": m.get("groupItemTitle") or m.get("question") or "?",
            "slug": m.get("slug") or "",
            "lo": v["lo"], "hi": v["hi"], "eenheid": v["eenheid"],
            "ja": prijzen[i] if len(prijzen) > i else None,
            "volume": _getal(m.get("volumeNum") if m.get("volumeNum") is not None
                             else m.get("volume")) or 0,
            "spread": _getal(m.get("spread")),
        })
    vakken.sort(key=lambda b: -math.inf if b["lo"] is None else b["lo"])
    eenheid = next((b["eenheid"] for b in vakken if b["eenheid"]), None)
    return {"slug": slug, "eenheid": eenheid, "vakken": vakken,
            "volume24": _getal(e.get("volume24hr")),
            "liquiditeit": _getal(e.get("liquidity")),
            "einde": e.get("endDate"), "gesloten": bool(e.get("closed"))}


def haal_markten(slugs: list, pauze: float = 0.6) -> dict:
    """Alle events in zo min mogelijk verzoeken: de Gamma-API accepteert
    meerdere slug-parameters tegelijk, dus per bundel van GAMMA_BUNDEL slugs is
    er één verzoek nodig in plaats van één per stad en dag."""
    uit: dict = {}
    uniek = sorted(set(s for s in slugs if s))
    for i in range(0, len(uniek), GAMMA_BUNDEL):
        deel = uniek[i:i + GAMMA_BUNDEL]
        url = GAMMA + "?" + "&".join("slug=" + urllib.parse.quote(s) for s in deel)
        try:
            events = weer._get_json(url, timeout=60)
        except Exception as ex:
            print(f"    Gamma mislukt voor {len(deel)} slugs ({ex})")
            continue
        for e in events if isinstance(events, list) else []:
            s = e.get("slug")
            if s:
                uit[s] = verwerk_event(e, s)
        time.sleep(pauze)
    return uit


# ── De eigen voorspelling, zoals de app hem toont ─────────────────────────────

def laad_params() -> dict:
    """De parameters die de app zelf ook inleest (app_params.js)."""
    pad = WORTEL / "app_params.js"
    tekst = pad.read_text()
    return json.loads(tekst[tekst.index("=") + 1:].strip().rstrip(";"))


def _kwant(pool: list, p: float) -> float:
    """Het ledenkwantiel zoals ledenUit in index.html het pakt: de dichtstbijzijnde
    ordestatistiek, geen interpolatie. Math.round rondt halve waarden omhoog."""
    return pool[int(math.floor((len(pool) - 1) * p + 0.5))]


def leden_stat(per_model: dict) -> dict:
    """Modelgemiddelden, de gepoolde leden en de 10/90 ledenkwantielen als
    afstand tot het poolgemiddelde. Spiegelbeeld van ledenUit in index.html."""
    model_gem, pool = {}, []
    for m, waarden in per_model.items():
        if not waarden:
            continue
        model_gem[m] = sum(waarden) / len(waarden)
        pool.extend(waarden)
    if not pool:
        return None
    pool.sort()
    pool_gem = sum(pool) / len(pool)
    return {"model_gem": model_gem, "pool": pool, "pool_gem": pool_gem,
            "d10": _kwant(pool, 0.1) - pool_gem, "d90": _kwant(pool, 0.9) - pool_gem}


def kort_map(model_gem: dict) -> dict:
    uit = {}
    for m, v in model_gem.items():
        kort = K.KORT_VAN.get(m)
        if kort and v is not None:
            uit[kort] = v
    return uit


def spreiding_kort(kort: dict):
    """kernSpreiding in index.html: de populatiespreiding tussen de
    modelsystemen, of niets bij minder dan twee modellen."""
    return K.pstdev_van(kort) if len(kort) >= 2 else None


def dag_max(kort: dict, stat: dict, hp) -> dict:
    """De verwachting en de 80%-band van het dagmaximum, zoals haalStad in
    index.html ze opbouwt: de gedeelde correctiekern voor de verwachting, en
    daaromheen de gekalibreerde band die nooit smaller wordt dan de leden.

    De lagterm staat op nul. De app vult die met haar eigen verificatiereeks uit
    de browser; buiten de browser bestaat die reeks niet."""
    mu_w = K.kern_mu(hp, kort)
    verw = stat["pool_gem"] if mu_w is None else mu_w
    lo, hi = stat["d10"], stat["d90"]
    s_live = spreiding_kort(kort)
    gekalibreerd = False
    if hp and (hp.get("qz10") is not None or hp.get("res_q10") is not None):
        k = K.kern_voorspel(hp, kort, 0.0)
        if k is not None:
            verw = k
        b_lo, b_hi = hp.get("res_q10"), hp.get("res_q90")
        if hp.get("qz10") is not None and hp.get("sig_c") is not None:
            sp = s_live if s_live is not None else (hp.get("s_gem") or 0)
            sig = max(0.2, hp["sig_c"] + hp["sig_d"] * sp)
            b_lo, b_hi = hp["qz10"] * sig, hp["qz90"] * sig
        if b_lo is not None and b_hi is not None:
            if hp.get("band_lokaal"):
                b_lo *= hp["band_lokaal"]
                b_hi *= hp["band_lokaal"]
            lo, hi = min(b_lo, stat["d10"]), max(b_hi, stat["d90"])
            gekalibreerd = True
    return {"verwachting": verw, "p10": verw + lo, "p90": verw + hi,
            "pool": stat["pool"], "gekalibreerd": gekalibreerd}


def dag_min(kort: dict, stat: dict, hp, mk) -> dict:
    """Idem voor het dagminimum (minDagUit en minPas in index.html): de
    modelgewichten uit de maximumkalibratie, en pas een bias en band zodra er
    min-parameters zijn. Die staan er nu niet in app_params.js, dus in de
    praktijk is dit het gewogen modelgemiddelde met de ledenspreiding."""
    mu_w = K.kern_mu(hp, kort)
    verw = stat["pool_gem"] if mu_w is None else mu_w
    lo, hi = stat["d10"], stat["d90"]
    gekalibreerd = False
    n = 0
    if mk:
        n = mk.get("n") if mk.get("n") is not None else (mk.get("n_eval") or 0)
    if mk and n >= MINKAL_MIN_N:
        verw = verw + (mk.get("bias") or 0)
        q10 = mk.get("q10") if mk.get("q10") is not None else mk.get("res_q10")
        q90 = mk.get("q90") if mk.get("q90") is not None else mk.get("res_q90")
        if q10 is not None and q90 is not None:
            f = mk.get("band_lokaal") or 1
            lo, hi = min(q10 * f, stat["d10"]), max(q90 * f, stat["d90"])
        gekalibreerd = True
    return {"verwachting": verw, "p10": verw + lo, "p90": verw + hi,
            "pool": stat["pool"], "gekalibreerd": gekalibreerd}


def meng_nws(dag: dict, nws_waarde, lead: int) -> None:
    """De NWS-bijmenging voor de Amerikaanse steden op dag 0 en 1, zoals haalNws
    in index.html: verwachting en band schuiven samen op."""
    if nws_waarde is None or lead > 1:
        return
    if abs(nws_waarde - dag["verwachting"]) > NWS_MAX_VERSCHIL:
        return
    delta = NWS_GEWICHT * (nws_waarde - dag["verwachting"])
    dag["verwachting"] += delta
    dag["p10"] += delta
    dag["p90"] += delta


# ── Strategie A ───────────────────────────────────────────────────────────────

def vak_van_mu(vakken: list, mu: float) -> int:
    """Index van het vakje waar het gecorrigeerde gemiddelde in valt."""
    for i, b in enumerate(vakken):
        onder = b["lo"] is None or mu >= b["lo"] - 0.5
        boven = b["hi"] is None or mu <= b["hi"] + 0.5
        if onder and boven:
            return i
    return 0 if mu < 0 else len(vakken) - 1


def uren_tot(doel_datum: str, tz: str):
    """Uren tot het einde van de doeldag in de stad zelf: middernacht na
    doel_datum in de tijdzone van die stad.

    Niet het veld endDate uit de Gamma-API. Dat staat voor elke stad op 12:00
    UTC van de doeldag, en dat is alleen voor Wellington het einde van de lokale
    dag. Voor New York scheelt het 16 uur, voor San Francisco 19 uur en voor
    Amsterdam 10 uur. Met endDate zou de tijdpoort van strategie A per stad op
    een ander werkelijk moment staan, en zou het logboek niet te vergelijken
    zijn met de handmatig afgewikkelde posities, die op het einde van de lokale
    dag zijn gemeten. De tijdzone komt uit weer.STEDEN; er is geen tweede
    tabel."""
    try:
        eind = date.fromisoformat(doel_datum) + timedelta(days=1)
    except ValueError:
        return None
    sluit = datetime(eind.year, eind.month, eind.day, tzinfo=ZoneInfo(tz))
    return (sluit - datetime.now(timezone.utc)).total_seconds() / 3600


def beoordeel_a(d: dict, onze, eigen, markt_eenheid, app_eenheid, uren) -> list:
    """Per vak of strategie A het op dit moment zou aanmerken: alle regels van A
    gehaald, beide poorten open en binnen het koopvenster. Dezelfde regels en
    dezelfde drempels als beoordeelA in polymarkt.js; de drempels komen daar
    letterlijk vandaan (STRAT_A), ze staan hier niet nog eens. `uren` telt tot
    middernacht lokaal, zie uren_tot."""
    leeg = [False] * len(d["vakken"])
    venster = uren is not None and uren <= STRAT_A["uurVroeg"] and uren >= STRAT_A["uurLaat"]
    liquide_genoeg = (STRAT_A["liquiditeit"] <= 0 or d["liquiditeit"] is None
                      or d["liquiditeit"] >= STRAT_A["liquiditeit"])
    if not onze or not eigen or not venster:
        return leeg
    mu = naar_eenheid(eigen["verwachting"], app_eenheid, markt_eenheid)
    i_mu = vak_van_mu(d["vakken"], mu)
    uit = []
    for i, b in enumerate(d["vakken"]):
        p = onze[i]
        redenen = 0
        if abs(i - i_mu) < STRAT_A["vakAfstand"]:
            redenen += 1
        if b["ja"] is None:
            redenen += 1
        elif b["ja"] < STRAT_A["prijsMin"] or b["ja"] > STRAT_A["prijsMax"]:
            redenen += 1
        if p is None:
            redenen += 1
        elif b["ja"] is not None and p > b["ja"] - STRAT_A["rand"]:
            redenen += 1
        poorten = 0 if liquide_genoeg else 1
        if b["spread"] is not None and b["spread"] > STRAT_A["spreadMax"] + 1e-9:
            poorten += 1
        uit.append(redenen == 0 and poorten == 0)
    return uit


# ── De regels bij elkaar ──────────────────────────────────────────────────────

def _tekst(x, dec=None):
    if x is None:
        return ""
    return f"{x:.{dec}f}" if dec is not None else str(x)


def rijen_voor_stad(nu: str, stad: dict, leden: dict, markten: dict,
                    params: dict, nws: dict) -> list:
    """Alle regels van één stad: per reeks, per doeldag en per vak één."""
    key = stad["key"]
    app_eenheid = "°F" if stad["eenheid"] == "F" else "°C"
    pr = (params.get("steden") or {}).get(key) or {}
    vandaag_lokaal = datetime.now(ZoneInfo(stad["tz"])).date()

    per_dag: dict = {}
    for (soort, dag, model), waarden in leden.items():
        per_dag.setdefault((soort, dag), {})[model] = waarden

    rijen = []
    for soort in ("max", "min"):
        for (s, dag) in sorted(k for k in per_dag if k[0] == soort):
            lead = (date.fromisoformat(dag) - vandaag_lokaal).days
            if not 0 <= lead <= 2:
                continue
            slug = slug_van(key, dag, soort)
            d = markten.get(slug)
            if not d or not d["vakken"]:
                continue

            stat = leden_stat(per_dag[(s, dag)])
            eigen = None
            if stat:
                kort = kort_map(stat["model_gem"])
                hp = pr.get(str(lead)) or pr.get("1")
                if soort == "max":
                    eigen = dag_max(kort, stat, hp)
                    meng_nws(eigen, (nws or {}).get(dag), lead)
                else:
                    mk = (pr.get("min") or {}).get(str(lead))
                    eigen = dag_min(kort, stat, hp, mk)

            markt_eenheid = d["eenheid"] or app_eenheid
            onze = onze_kansen(d["vakken"], eigen, markt_eenheid, app_eenheid) if eigen else None
            uren = uren_tot(dag, stad["tz"])
            merk = beoordeel_a(d, onze, eigen, markt_eenheid, app_eenheid, uren)

            # Alles wat in de regel staat is in de eenheid van de markt, zodat
            # de kans uit de regel zelf na te rekenen is.
            verw = p10 = p90 = None
            pool = []
            if eigen:
                verw = naar_eenheid(eigen["verwachting"], app_eenheid, markt_eenheid)
                p10 = naar_eenheid(eigen["p10"], app_eenheid, markt_eenheid)
                p90 = naar_eenheid(eigen["p90"], app_eenheid, markt_eenheid)
                pool = [naar_eenheid(v, app_eenheid, markt_eenheid) for v in eigen["pool"]]

            for i, b in enumerate(d["vakken"]):
                kans = onze[i] if onze else None
                fractie = None
                if pool:
                    onder = -math.inf if b["lo"] is None else b["lo"] - 0.5
                    boven = math.inf if b["hi"] is None else b["hi"] + 0.5
                    fractie = sum(1 for v in pool if onder <= v <= boven) / len(pool)
                edge = None if (kans is None or b["ja"] is None) else (kans - b["ja"]) * 100
                rijen.append([
                    nu, key, dag, lead, soort, markt_eenheid,
                    b["label"], _tekst(b["lo"]), _tekst(b["hi"]),
                    _tekst(verw, 2), _tekst(p10, 2), _tekst(p90, 2),
                    _tekst(kans, 4), _tekst(fractie, 4), _tekst(b["ja"], 4),
                    _tekst(edge, 2), _tekst(d["volume24"], 2),
                    d["slug"], b["slug"], 1 if merk[i] else 0,
                    _tekst(uren, 2), d["einde"] or "",
                ])
    return rijen


def logmap() -> Path:
    return logger.logmap()


def run(steden=None, dagen: int = 3, pauze: float = 0.6) -> int:
    nu = datetime.now(timezone.utc).isoformat(timespec="minutes")
    params = laad_params()
    lijst = [s for s in weer.STEDEN if steden is None or s["key"] in steden]

    # 1. de eigen kant: één ensembleverzoek per stad, met maxima en minima in
    #    dezelfde aanroep, en de NWS-verwachting voor de Amerikaanse steden.
    leden_per_stad, nws_per_stad, fouten = {}, {}, 0
    zonder_slug = []
    for stad in lijst:
        if stad["key"] not in SLUG:
            zonder_slug.append(stad["key"])
            continue
        try:
            leden_per_stad[stad["key"]] = logger.haal_leden(stad, ENS_VELDEN)
        except Exception as ex:
            print(f"  {stad['key']}: ensemble mislukt ({ex})")
            fouten += 1
        time.sleep(pauze)
    for key, url in logger.NWS_URLS.items():
        if key not in leden_per_stad:
            continue
        try:
            nws_per_stad[key] = logger.haal_nws(url)
        except Exception as ex:
            print(f"  {key}: NWS mislukt ({ex})")
        time.sleep(pauze)

    # 2. de marktkant: alle slugs in bundels ophalen.
    slugs = []
    for stad in lijst:
        if stad["key"] not in leden_per_stad:
            continue
        vandaag_lokaal = datetime.now(ZoneInfo(stad["tz"])).date()
        for i in range(max(1, min(3, dagen))):
            datum = (vandaag_lokaal + timedelta(days=i)).isoformat()
            for soort in ("max", "min"):
                slugs.append(slug_van(stad["key"], datum, soort))
    markten = haal_markten(slugs, pauze)

    # 3. de regels.
    rijen, zonder_markt = [], []
    for stad in lijst:
        leden = leden_per_stad.get(stad["key"])
        if leden is None:
            continue
        deel = rijen_voor_stad(nu, stad, leden, markten, params,
                               nws_per_stad.get(stad["key"]))
        if not deel:
            zonder_markt.append(stad["key"])
        rijen.extend(deel)

    if rijen:
        logger.schrijf(logmap() / "signalen.csv", KOP, rijen)
    met_prijs = sum(1 for r in rijen if r[14] != "")
    print(f"Gelogd: {len(rijen)} signaalregels over "
          f"{len(set(r[1] for r in rijen))} steden, {met_prijs} met marktprijs, "
          f"{fouten} fouten")
    if zonder_slug:
        print(f"  geen slug in polymarkt.js: {', '.join(zonder_slug)}")
    if zonder_markt:
        print(f"  geen markt gevonden: {', '.join(sorted(zonder_markt))}")
    return 0 if rijen else 1


def main(argv: list) -> int:
    steden = None
    dagen = 3
    for i, a in enumerate(argv):
        if a == "--steden" and i + 1 < len(argv):
            steden = {s.strip().upper() for s in argv[i + 1].split(",") if s.strip()}
        elif a == "--dagen" and i + 1 < len(argv):
            dagen = int(argv[i + 1])
    return run(steden, dagen)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
