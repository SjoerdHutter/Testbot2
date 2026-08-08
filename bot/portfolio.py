#!/usr/bin/env python3
"""Portefeuillebewaking: welke open positie loopt gevaar doordat de verwachting
sinds de instap is verschoven?

Het faalgeval waar dit voor gebouwd is: NO op het vak 20 °C, de verwachting
kruipt richting 20 °C, en de marktprijs reageert pas de ochtend zelf. Het alarm
staat daarom in graden, niet in prijs.

Deze module plaatst geen orders en verkoopt niets. Hij schrijft twee bestanden:

    portfolio.json              de stand van nu, voor het tabblad Portefeuille
    logs/portfolio_history.csv  een regel per positie per run, de reeks

Draaien (vanuit de hoofdmap van de repo):

    python3 bot/signalen.py --portfolio       via de vlag op de bestaande bot
    python3 bot/portfolio.py                  los
    python3 bot/portfolio.py --dump-raw       ruwe API-respons, zie hieronder
    python3 bot/portfolio.py --positions-file posities.json

Alleen de standaardbibliotheek, en alleen leesverzoeken: naar de data-API van
Polymarket voor de posities en naar de ensemble-API van Open-Meteo voor het
modelbeeld van nu.


VELDNAMEN VAN https://data-api.polymarket.com/positions  ─ NIET GEVERIFIEERD ─
─────────────────────────────────────────────────────────────────────────────
De opdracht vraagt de responsvorm eerst met een live aanroep vast te stellen en
geen veldnamen te raden. Dat is in de omgeving waarin deze module geschreven is
niet gelukt: het uitgaand verkeer daar staat `data-api.polymarket.com` niet toe
(de proxy antwoordt 403 op CONNECT), net zomin als gamma-api.polymarket.com of
open-meteo. De mapping hieronder is dus opgeschreven als een lijst van
kandidaatnamen per logisch veld en is NOG NIET tegen een echte respons gelegd.

Verifieren kost een commando op een machine met netwerk:

    python3 bot/portfolio.py --dump-raw

Die drukt de eerste regel ruw af plus, per logisch veld, welke kandidaatnaam
raak was en welke sleutels in de respons nergens op aansluiten. Klopt er iets
niet, pas dan alleen de tabel VELD_ALIAS hieronder aan; de rest van de module
raakt de ruwe namen niet aan.

Kandidaatnamen per logisch veld, meest waarschijnlijke eerst:

    size            size, quantity, shares, amount
    avg_price       avgPrice, averagePrice, avg_price, entryPrice
    current_bid     curPrice, currentPrice, price, bid, lastPrice
    outcome         outcome, outcomeName          ("Yes" / "No")
    outcome_index   outcomeIndex, outcome_index   (0 = Yes, 1 = No)
    market_slug     slug, marketSlug, market_slug, eventSlug
    condition_id    conditionId, condition_id
    title           title, question, marketQuestion, groupItemTitle
    end_date        endDate, end_date, endDateIso

Een positie die op geen van die namen aansluit gaat NIET verloren: hij belandt
met zijn ruwe velden in de lijst `unmapped` in portfolio.json. Stil laten
vallen is hier de ergste fout, want dan lijkt een gat gedekt.
"""
import csv
import json
import math
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, date, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weer
import logger
import signalen as S

WALLET = "0xD13CC6b93B79555B5eEe81A5B932aFA0Bf675992"
DATA_API = "https://data-api.polymarket.com/positions"
WORTEL = Path(__file__).resolve().parent.parent
UIT_JSON = WORTEL / "portfolio.json"

MIN_SIZE = 0.5           # kleiner is een afgerond restje, geen positie
STAND_BREEDTE = {"°F": 2.0, "°C": 1.0}   # vakbreedte bij een open einde
WIN_DREMPEL = 0.55       # onder deze modelwinkans staat het licht op rood
DELTA_PROB_ORANJE = 15.0 # procentpunten kansstijging die oranje rechtvaardigt

# Steden zonder betrouwbare biaskalibratie: het Open-Meteo raster zit er op de
# post waarop Polymarket afwikkelt ongeveer 2 °C naast. Het stoplicht kan daar
# een zekerheid suggereren die het niet waarmaakt, dus zetten we het erbij.
HOGE_ONZEKERHEID = {"TYO", "SIN"}

# Zie het commentaarblok bovenaan: kandidaatnamen, nog niet tegen een echte
# respons gelegd. Alleen deze tabel hoeft bij een afwijking aangepast te worden.
VELD_ALIAS = {
    "size":          ["size", "quantity", "shares", "amount"],
    "avg_price":     ["avgPrice", "averagePrice", "avg_price", "entryPrice"],
    "current_bid":   ["curPrice", "currentPrice", "price", "bid", "lastPrice"],
    "outcome":       ["outcome", "outcomeName"],
    "outcome_index": ["outcomeIndex", "outcome_index"],
    "market_slug":   ["slug", "marketSlug", "market_slug", "eventSlug"],
    "condition_id":  ["conditionId", "condition_id"],
    "title":         ["title", "question", "marketQuestion", "groupItemTitle"],
    "end_date":      ["endDate", "end_date", "endDateIso"],
}

HIST_KOP = ["gelogd_utc", "key", "doel_datum", "bracket_label", "adj_mean_now",
            "model_prob_now", "current_bid", "city_bias_used", "light"]

# stadssleutel per slugdeel: precies de tabel uit polymarkt.js, omgedraaid.
KEY_VAN_SLUG = {v: k for k, v in S.SLUG.items()}
MAAND_NR = {m: i + 1 for i, m in enumerate(S.MAAND)}


# ── Stap 1a: de posities ──────────────────────────────────────────────────────

def _pak(rij: dict, veld: str):
    """De waarde van een logisch veld uit een ruwe positieregel, plus de naam
    die raak was. Geen van de kandidaten raak: (None, None)."""
    for naam in VELD_ALIAS[veld]:
        if naam in rij and rij[naam] not in (None, ""):
            return rij[naam], naam
    return None, None


def _getal(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def haal_posities(wallet: str = WALLET, timeout: int = 60) -> list:
    url = DATA_API + "?" + urllib.parse.urlencode({"user": wallet})
    req = urllib.request.Request(url, headers={"User-Agent": "weerbot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    if isinstance(data, dict):
        # sommige eindpunten verpakken de lijst; pak de eerste lijstwaarde
        for v in data.values():
            if isinstance(v, list):
                return v
        return []
    return data if isinstance(data, list) else []


def dump_raw(wallet: str = WALLET) -> int:
    """De eerste ruwe regel, plus welke kandidaatnaam per logisch veld raak was.
    Hiermee is de tabel VELD_ALIAS in een keer te controleren."""
    rijen = haal_posities(wallet)
    print(f"{len(rijen)} regels van {DATA_API}")
    if not rijen:
        return 1
    eerste = rijen[0]
    print("\n── eerste regel ruw ──")
    print(json.dumps(eerste, indent=2, sort_keys=True)[:4000])
    print("\n── mapping ──")
    geraakt = set()
    for veld in VELD_ALIAS:
        waarde, naam = _pak(eerste, veld)
        if naam:
            geraakt.add(naam)
            print(f"  {veld:<14} <- {naam:<18} = {waarde!r}")
        else:
            print(f"  {veld:<14} <- GEEN van {VELD_ALIAS[veld]}")
    rest = sorted(k for k in eerste if k not in geraakt)
    print(f"\n── sleutels die nergens op aansluiten ──\n  {', '.join(rest) or '(geen)'}")
    return 0


# ── Stap 1b: positie koppelen aan stad, datum en bracket ──────────────────────

# highest-temperature-in-{stad}-on-{maand}-{dag}-{jaar}, eventueel gevolgd door
# het vaksuffix van de losse markt.
_SLUG_RE = re.compile(
    r"^(highest|lowest)-temperature-in-(.+?)-on-([a-z]+)-(\d{1,2})-(\d{4})(?:-(.*))?$")


def uit_slug(slug: str):
    """Stad, doeldatum en reeks uit een markt- of eventslug. Dit is slug_van uit
    signalen.py achterstevoren; die bouwt hem op als
    highest-temperature-in-{stad}-on-{maand}-{dag}-{jaar}."""
    if not slug:
        return None
    m = _SLUG_RE.match(str(slug).strip().lower())
    if not m:
        return None
    soort = "min" if m.group(1) == "lowest" else "max"
    key = KEY_VAN_SLUG.get(m.group(2))
    maand = MAAND_NR.get(m.group(3))
    if not key or not maand:
        return None
    try:
        dag = date(int(m.group(5)), maand, int(m.group(4)))
    except ValueError:
        return None
    return {"key": key, "datum": dag.isoformat(), "soort": soort}


def richting_van(rij: dict):
    """YES of NO. De naam wint van de index; ontbreken ze allebei, dan niets."""
    naam, _ = _pak(rij, "outcome")
    if naam is not None:
        t = str(naam).strip().lower()
        if t in ("yes", "ja", "true"):
            return "YES"
        if t in ("no", "nee", "false"):
            return "NO"
    idx, _ = _pak(rij, "outcome_index")
    if idx is not None:
        try:
            return "YES" if int(idx) == 0 else "NO"
        except (TypeError, ValueError):
            return None
    return None


def eenheid_van(key: str) -> str:
    stad = weer.STAD_OP_KEY.get(key)
    return "°F" if stad and stad["eenheid"] == "F" else "°C"


def koppel(rij: dict):
    """Een ruwe positieregel naar (stad, datum, vak, richting), of een reden
    waarom dat niet lukt. De reden gaat mee de JSON in, zodat zichtbaar is wat
    er ontbreekt in plaats van dat de positie verdwijnt."""
    slug, _ = _pak(rij, "market_slug")
    titel, _ = _pak(rij, "title")
    size = _getal(_pak(rij, "size")[0])
    richting = richting_van(rij)

    if size is None:
        return None, "geen size in de regel"
    if richting is None:
        return None, "geen outcome (YES/NO) in de regel"

    plek = uit_slug(slug)
    if not plek:
        return None, f"slug niet te ontleden: {slug!r}"

    # Het vak staat in de titel van de losse markt; de slug van het vak is het
    # eventdeel plus een samengeperst suffix waar de graden niet los uit te
    # lezen zijn ("82-83f", "81forbelow"), dus die gebruiken we er niet voor.
    vak = S.vak_uit(titel) if titel else None
    if not vak or (vak["lo"] is None and vak["hi"] is None):
        return None, f"vak niet te lezen uit de titel: {titel!r}"

    return {
        "key": plek["key"], "datum": plek["datum"], "soort": plek["soort"],
        "label": str(titel).strip(),
        "lo": vak["lo"], "hi": vak["hi"],
        "eenheid": vak["eenheid"] or eenheid_van(plek["key"]),
        "direction": richting,
        "size": size,
        "avg_price": _getal(_pak(rij, "avg_price")[0]),
        "current_bid": _getal(_pak(rij, "current_bid")[0]),
        "condition_id": _pak(rij, "condition_id")[0],
        "slug": slug,
    }, None


def net_uit(posities: list) -> list:
    """Per (stad, datum, reeks, vak) een richting overhouden. De API geeft per
    token al een regel met bijkopen en deelverkopen verrekend, dus er wordt hier
    niet gemiddeld; alleen YES en NO op hetzelfde vak vallen tegen elkaar weg.

    De prijzen van de overblijvende kant blijven staan: die kant is wat er nog
    open ligt, en het gemiddelde van twee tegengestelde instappen zegt niets."""
    per: dict = {}
    for p in posities:
        per.setdefault((p["key"], p["datum"], p["soort"], p["lo"], p["hi"]), []).append(p)

    uit = []
    for _, groep in sorted(per.items(), key=lambda kv: str(kv[0])):
        ja = sum(p["size"] for p in groep if p["direction"] == "YES")
        nee = sum(p["size"] for p in groep if p["direction"] == "NO")
        netto = ja - nee
        richting = "YES" if netto > 0 else "NO"
        if abs(netto) < MIN_SIZE:
            continue
        kant = [p for p in groep if p["direction"] == richting]
        if not kant:
            continue
        # De grootste regel van de overblijvende kant draagt de prijzen; bij
        # meerdere regels op dezelfde kant (verschillende tokens op hetzelfde
        # vak) wegen we de instapprijs met de size.
        basis = dict(max(kant, key=lambda p: p["size"]))
        gewicht = sum(p["size"] for p in kant if p["avg_price"] is not None)
        if gewicht > 0:
            basis["avg_price"] = sum(p["avg_price"] * p["size"] for p in kant
                                     if p["avg_price"] is not None) / gewicht
        basis["size"] = abs(netto)
        basis["direction"] = richting
        uit.append(basis)
    return uit


# ── Stap 1c: het modelbeeld van nu ────────────────────────────────────────────

class ModelCache:
    """Een ensemblefetch per (stad, datum), niet per positie: vijf posities op
    dezelfde dag kosten samen een verzoek."""

    def __init__(self, params: dict):
        self.params = params
        self._per_stad: dict = {}     # key -> leden of Exception
        self._per_dag: dict = {}      # (key, datum, soort) -> beeld of None

    def _leden(self, key: str):
        if key not in self._per_stad:
            stad = weer.STAD_OP_KEY.get(key)
            if not stad:
                self._per_stad[key] = ValueError(f"{key} staat niet in weer.STEDEN")
            else:
                try:
                    self._per_stad[key] = logger.haal_leden(stad, S.ENS_VELDEN)
                except Exception as ex:            # noqa: BLE001 - reden gaat mee
                    self._per_stad[key] = ex
        return self._per_stad[key]

    def beeld(self, key: str, datum: str, soort: str):
        """De verwachting en de 80%-band van nu, in de eenheid van de markt.
        Geeft (beeld, fout): precies een van de twee is gevuld."""
        sleutel = (key, datum, soort)
        if sleutel in self._per_dag:
            return self._per_dag[sleutel]

        leden = self._leden(key)
        if isinstance(leden, Exception):
            uit = (None, f"ensemble mislukt: {leden}")
            self._per_dag[sleutel] = uit
            return uit

        stad = weer.STAD_OP_KEY[key]
        per_model = {m: w for (s, d, m), w in leden.items() if s == soort and d == datum}
        stat = S.leden_stat(per_model) if per_model else None
        if not stat:
            uit = (None, f"geen ensembleleden voor {datum}")
            self._per_dag[sleutel] = uit
            return uit

        vandaag_lokaal = datetime.now(ZoneInfo(stad["tz"])).date()
        lead = (date.fromisoformat(datum) - vandaag_lokaal).days
        pr = (self.params.get("steden") or {}).get(key) or {}
        hp = pr.get(str(lead)) or pr.get("1")
        kort = S.kort_map(stat["model_gem"])
        if soort == "max":
            eigen = S.dag_max(kort, stat, hp)
        else:
            eigen = S.dag_min(kort, stat, hp, (pr.get("min") or {}).get(str(lead)))

        app_eenheid = "°F" if stad["eenheid"] == "F" else "°C"
        markt_eenheid = eenheid_van(key)
        uit = ({
            "ensemble_mean": S.naar_eenheid(stat["pool_gem"], app_eenheid, markt_eenheid),
            "adj_mean": S.naar_eenheid(eigen["verwachting"], app_eenheid, markt_eenheid),
            "eigen": eigen, "app_eenheid": app_eenheid, "markt_eenheid": markt_eenheid,
            "lead": lead,
        }, None)
        self._per_dag[sleutel] = uit
        return uit

    def vak_kans(self, beeld: dict, lo, hi):
        """De modelkans op een vak, met dezelfde functie als het signalenlog:
        bracket_probs in de opdracht, onze_kansen hier."""
        kansen = S.onze_kansen([{"lo": lo, "hi": hi}], beeld["eigen"],
                               beeld["markt_eenheid"], beeld["app_eenheid"])
        return kansen[0] if kansen else None


# ── Stap 1d: het modelbeeld bij instap, uit logs/signalen.csv ─────────────────

def instap_index(pad: Path = None) -> dict:
    """De vroegst gelogde regel per (datum, stad, reeks, vak) uit het
    signalenlog: het modelbeeld zoals het er bij de instap bij stond.

    Er wordt op len(cells) gesplitst en niet met DictReader gelezen. Het bestand
    is in de loop van de tijd gegroeid, en een DictReader plakt de kop van nu op
    een kortere oude regel, waarmee elke kolom een plek opschuift zonder dat er
    iets misgaat waar je het aan ziet."""
    pad = pad or (logger.logmap() / "signalen.csv")
    uit: dict = {}
    if not pad.exists():
        return uit

    with open(pad, newline="") as f:
        for cellen in csv.reader(f):
            n = len(cellen)
            if n < 13 or cellen[0] == "gelogd_utc":
                continue
            # De eerste dertien kolommen staan er vanaf het begin en op dezelfde
            # plek; alles wat deze index nodig heeft zit daarbinnen.
            (gelogd, key, datum, _lead, soort, eenheid,
             label, lo, hi, verwachting, _p10, _p90, kans) = cellen[:13]
            sleutel = (datum, key, soort, label)
            vorig = uit.get(sleutel)
            if vorig and vorig["gelogd"] <= gelogd:
                continue
            uit[sleutel] = {
                "gelogd": gelogd, "eenheid": eenheid,
                "lo": _getal(lo), "hi": _getal(hi),
                "adj_mean": _getal(verwachting), "model_prob": _getal(kans),
                "kolommen": n,
            }
    return uit


# ── Stap 1e/1f: de kerngetallen en het stoplicht ──────────────────────────────

def afstand_tot_vak(mu, lo, hi):
    """De afstand in graden van de verwachting tot de dichtstbijzijnde
    vakrand, en 0 als de verwachting in het vak ligt. Bij een open einde telt
    alleen de rand die er is. De markt rekent af op hele graden, dus het vak
    loopt van lo-0,5 tot hi+0,5."""
    if mu is None:
        return None
    onder = None if lo is None else lo - 0.5
    boven = None if hi is None else hi + 0.5
    if (onder is None or mu >= onder) and (boven is None or mu <= boven):
        return 0.0
    if onder is not None and mu < onder:
        return onder - mu
    return mu - boven


def verschuiving(mu_instap, mu_nu, lo, hi):
    """Hoeveel de verwachting sinds de instap is opgeschoven, met een teken dat
    zegt welke kant op ten opzichte van het vak. De grootte is gewoon nu min
    instap; het teken komt uit de afstand tot het vak: positief is ernaartoe.

    Alleen een aantoonbare beweging van het vak af krijgt een minteken. Stond de
    verwachting bij de instap al in het vak, dan blijft de afstand nul en zou een
    tekenregel op afstand alleen niets zeggen; die positie stond toen al in het
    ergste geval en hoort niet geruststellend te lezen."""
    if mu_instap is None or mu_nu is None:
        return None
    grootte = abs(mu_nu - mu_instap)
    oud = afstand_tot_vak(mu_instap, lo, hi)
    nieuw = afstand_tot_vak(mu_nu, lo, hi)
    return round(-grootte if nieuw > oud else grootte, 2)


def vakbreedte(lo, hi, eenheid: str) -> float:
    """De buffer moet meeschalen met de vakbreedte, anders staat elke °C markt
    permanent op rood: daar is een vak een hele graad breed en in de °F markten
    twee. Bij een open einde bestaat de breedte niet, dan geldt de
    standaardbreedte van de markt."""
    if lo is None or hi is None:
        return STAND_BREEDTE.get(eenheid, 1.0)
    return float(hi) - float(lo) + 1.0


def stoplicht(d, b, model_win_prob, delta_prob, mu_in_vak: bool, is_ja=False):
    """De eerste voorwaarde die klopt wint. De reden gaat mee: zonder die regel
    is een kleur niet na te rekenen.

    Een afwijking van de opgeschreven tabel, met opzet. De twee afstandsregels
    ("verwachting in het vak" en "d < een half vak") zijn geschreven vanuit het
    faalgeval van een NO: de verwachting kruipt naar het vak toe en de positie
    loopt leeg. Voor een YES staat dat op zijn kop: daar is de verwachting in
    het vak juist de winnende stand, en zou de tabel letterlijk toegepast een
    winnende positie rood kleuren.

    Daarom slaan die twee regels over voor een YES waarvan de verwachting in het
    vak ligt. Alles daarbuiten is de tabel zoals hij er staat, en voor een YES
    blijft de modelwinkans hieronder de regel die het werk doet: ligt de
    verwachting ver van het vak, dan zakt die kans vanzelf onder de 55%."""
    veilig_binnen = is_ja and mu_in_vak
    if mu_in_vak and not veilig_binnen:
        return "red", "verwachting ligt in het vak zelf"
    if d is not None and d < 0.5 * b and not veilig_binnen:
        return "red", f"nog {d:.2f}° tot de vakrand, minder dan een half vak ({0.5 * b:.2f}°)"
    if model_win_prob is not None and model_win_prob < WIN_DREMPEL:
        return "red", (f"modelwinkans {model_win_prob * 100:.0f}%, onder "
                       f"{WIN_DREMPEL * 100:.0f}%")
    if d is not None and d <= 1.0 * b and not veilig_binnen:
        return "amber", (f"nog {d:.2f}° tot de vakrand, tussen een half en een heel "
                         f"vak ({0.5 * b:.2f}° tot {b:.2f}°)")
    if delta_prob is not None and delta_prob > DELTA_PROB_ORANJE:
        return "amber", (f"vakkans is sinds de instap {delta_prob:+.1f}pp gestegen, "
                         f"meer dan {DELTA_PROB_ORANJE:.0f}pp")
    if veilig_binnen:
        return "green", (f"verwachting ligt in het vak zelf en de positie is YES, "
                         f"modelwinkans {model_win_prob * 100:.0f}%")
    return "green", (f"nog {d:.2f}° tot de vakrand, meer dan een heel vak ({b:.2f}°)"
                     if d is not None else "geen aanleiding")


def uren_tot_sluiting(key: str, datum: str):
    """Middernacht aan het einde van de doeldag in de lokale tijdzone van de
    stad. Niet met een vaste UTC-offset: die klopt maar in een deel van het jaar
    en zit er in de andere helft een uur naast, wat op de laatste dag precies
    het verschil is tussen binnen en buiten het koopvenster."""
    stad = weer.STAD_OP_KEY.get(key)
    if not stad:
        return None
    try:
        dag = date.fromisoformat(datum)
    except ValueError:
        return None
    tz = ZoneInfo(stad["tz"])
    sluit = datetime.combine(dag + timedelta(days=1), dtime(0, 0), tzinfo=tz)
    return (sluit - datetime.now(timezone.utc)).total_seconds() / 3600


# ── De positie doorrekenen ────────────────────────────────────────────────────

def beoordeel(pos: dict, cache: ModelCache, instap: dict) -> dict:
    key, datum, soort = pos["key"], pos["datum"], pos["soort"]
    lo, hi, eenheid = pos["lo"], pos["hi"], pos["eenheid"]
    is_ja = pos["direction"] == "YES"
    b = vakbreedte(lo, hi, eenheid)

    rij = {
        "city": key, "date": datum, "soort": soort,
        "bracket": pos["label"], "direction": pos["direction"],
        "size": round(pos["size"], 4),
        "avg_price": pos["avg_price"], "current_bid": pos["current_bid"],
        "adj_mean_now": None, "adj_mean_entry": None, "city_bias_used": None,
        "model_prob_now": None, "model_prob_entry": None, "model_win_prob": None,
        "d": None, "b": round(b, 2), "delta_prob": None, "delta_mean": None,
        "fair_value": None, "edge_now": None,
        "hours_to_close": None, "light": "unknown", "reason": "",
        "entry_known": False,
        "high_uncertainty": key in HOGE_ONZEKERHEID,
        "unit": eenheid,
        "bracket_lo": lo, "bracket_hi": hi,
        "slug": pos.get("slug"), "condition_id": pos.get("condition_id"),
    }

    uren = uren_tot_sluiting(key, datum)
    rij["hours_to_close"] = None if uren is None else round(uren, 2)

    # 1c: het modelbeeld van nu.
    beeld, fout = cache.beeld(key, datum, soort)
    if not beeld:
        rij["reason"] = fout or "modelbeeld ontbreekt"
        return rij

    mu = beeld["adj_mean"]
    kans = cache.vak_kans(beeld, lo, hi)
    rij["adj_mean_now"] = None if mu is None else round(mu, 2)
    rij["model_prob_now"] = None if kans is None else round(kans, 4)
    # De correctie die de kalibratie op het kale ledengemiddelde legt. Expliciet
    # opslaan: die tabel wordt periodiek bijgesteld, en zonder dit veld lijkt
    # zo'n bijstelling later in de reeks op een weersverandering.
    if mu is not None and beeld["ensemble_mean"] is not None:
        rij["city_bias_used"] = round(mu - beeld["ensemble_mean"], 2)

    # 1e: de vier kerngetallen.
    d = afstand_tot_vak(mu, lo, hi)
    rij["d"] = None if d is None else round(d, 2)
    if kans is not None:
        win = (1 - kans) if not is_ja else kans
        rij["model_win_prob"] = round(win, 4)
        rij["fair_value"] = round(win, 4)          # 1g: los van het stoplicht
        if pos["current_bid"] is not None:
            rij["edge_now"] = round((win - pos["current_bid"]) * 100, 2)

    # 1d: het modelbeeld bij instap.
    was = instap.get((datum, key, soort, pos["label"]))
    if was and was["model_prob"] is not None:
        rij["entry_known"] = True
        rij["adj_mean_entry"] = None if was["adj_mean"] is None else round(was["adj_mean"], 2)
        rij["model_prob_entry"] = round(was["model_prob"], 4)
        if kans is not None:
            rij["delta_prob"] = round((kans - was["model_prob"]) * 100, 2)
        if mu is not None and was["adj_mean"] is not None:
            rij["delta_mean"] = verschuiving(was["adj_mean"], mu, lo, hi)

    # 1f: het stoplicht.
    if mu is None or kans is None:
        rij["reason"] = "modelbeeld onvolledig"
        return rij
    rij["light"], rij["reason"] = stoplicht(d, b, rij["model_win_prob"],
                                            rij["delta_prob"], d == 0, is_ja)
    if rij["high_uncertainty"]:
        rij["reason"] += " · let op: geen betrouwbare biaskalibratie voor deze stad"
    return rij


# ── Stap 1i: de uitvoer ───────────────────────────────────────────────────────

VOLGORDE = {"red": 0, "amber": 1, "unknown": 2, "green": 3}


def bouw(posities_ruw: list, params: dict, instap: dict, wallet: str,
         cache=None) -> dict:
    gekoppeld, unmapped = [], []
    for ruw in posities_ruw:
        size = _getal(_pak(ruw, "size")[0])
        if size is not None and abs(size) < MIN_SIZE:
            continue                     # afgerond restje, geen positie
        pos, reden = koppel(ruw)
        if pos is None:
            unmapped.append({"reason": reden, "raw": ruw})
            continue
        gekoppeld.append(pos)

    netto = net_uit(gekoppeld)

    # posities waarvan de marktdatum al voorbij is tellen niet meer mee
    open_posities = []
    for p in netto:
        stad = weer.STAD_OP_KEY.get(p["key"])
        if not stad:
            unmapped.append({"reason": f"{p['key']} staat niet in weer.STEDEN", "raw": p})
            continue
        if date.fromisoformat(p["datum"]) < datetime.now(ZoneInfo(stad["tz"])).date():
            continue
        open_posities.append(p)

    cache = cache or ModelCache(params)
    rijen = [beoordeel(p, cache, instap) for p in open_posities]
    rijen.sort(key=lambda r: (VOLGORDE.get(r["light"], 9),
                              r["hours_to_close"] if r["hours_to_close"] is not None
                              else math.inf))

    blootstelling = sum((r["size"] or 0) * (r["current_bid"] if r["current_bid"]
                                            is not None else (r["avg_price"] or 0))
                        for r in rijen)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wallet": wallet,
        "positions": rijen,
        "unmapped": unmapped,
        "summary": {
            "n_positions": len(rijen),
            "n_red": sum(1 for r in rijen if r["light"] == "red"),
            "n_amber": sum(1 for r in rijen if r["light"] == "amber"),
            "n_green": sum(1 for r in rijen if r["light"] == "green"),
            "n_unknown": sum(1 for r in rijen if r["light"] == "unknown"),
            "n_unmapped": len(unmapped),
            "total_exposure": round(blootstelling, 2),
        },
    }


def schrijf_uit(payload: dict) -> None:
    UIT_JSON.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    nu = payload["generated_at"]
    rijen = [[nu, r["city"], r["date"], r["bracket"],
              "" if r["adj_mean_now"] is None else r["adj_mean_now"],
              "" if r["model_prob_now"] is None else r["model_prob_now"],
              "" if r["current_bid"] is None else r["current_bid"],
              "" if r["city_bias_used"] is None else r["city_bias_used"],
              r["light"]] for r in payload["positions"]]
    if rijen:
        logger.schrijf(logger.logmap() / "portfolio_history.csv", HIST_KOP, rijen)


def run(wallet: str = WALLET, posities_bestand: str = None) -> int:
    if posities_bestand:
        ruw = json.loads(Path(posities_bestand).read_text())
        if isinstance(ruw, dict):
            ruw = next((v for v in ruw.values() if isinstance(v, list)), [])
        print(f"{len(ruw)} posities uit {posities_bestand}")
    else:
        try:
            ruw = haal_posities(wallet)
        except Exception as ex:                    # noqa: BLE001
            print(f"Posities ophalen mislukt: {ex}")
            return 1
        print(f"{len(ruw)} posities van de data-API")

    payload = bouw(ruw, S.laad_params(), instap_index(), wallet)
    schrijf_uit(payload)

    s = payload["summary"]
    print(f"portfolio.json: {s['n_positions']} open posities · "
          f"{s['n_red']} rood, {s['n_amber']} oranje, {s['n_green']} groen, "
          f"{s['n_unknown']} onbekend · {s['n_unmapped']} niet gekoppeld")
    for r in payload["positions"]:
        if r["light"] in ("red", "amber", "unknown"):
            print(f"  {r['light']:<7} {r['city']} {r['date']} {r['bracket']} "
                  f"{r['direction']}: {r['reason']}")
    for u in payload["unmapped"]:
        print(f"  unmapped: {u['reason']}")
    return 0


def main(argv: list) -> int:
    wallet, bestand = WALLET, None
    for i, a in enumerate(argv):
        if a == "--dump-raw":
            return dump_raw(wallet)
        if a == "--wallet" and i + 1 < len(argv):
            wallet = argv[i + 1]
        elif a == "--positions-file" and i + 1 < len(argv):
            bestand = argv[i + 1]
    return run(wallet, bestand)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
