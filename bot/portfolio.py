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


VELDNAMEN VAN https://data-api.polymarket.com/positions
──────────────────────────────────────────────────────────────────────────────
Vastgesteld op een echte respons van 8 augustus 2026. De volle sleutellijst per
positieregel:

    asset  avgPrice  cashPnl  conditionId  curPrice  currentValue  endDate
    eventId  eventSlug  icon  initialValue  mergeable  negativeRisk
    oppositeAsset  oppositeOutcome  outcome  outcomeIndex  percentPnl
    percentRealizedPnl  proxyWallet  realizedPnl  redeemable  size  slug
    title  totalBought

Wat deze module ervan gebruikt, en waarom:

    size            aantal aandelen, bijkopen en deelverkopen al verrekend
    avgPrice        gemiddelde instapprijs
    curPrice        prijs van dit token nu, dus van de kant die je aanhoudt
    outcome         "Yes" of "No"; outcomeIndex is de terugval
    slug            de slug van de losse markt, met het vak als staart. Let op:
                    eventSlug is de reeks zonder vak en mag hier niet voor door
    conditionId     alleen om de regel terug te vinden
    title           de HELE vraag ("Will the highest temperature in London be
                    30°C on August 9?"), niet de vaknaam. Het vak komt daarom
                    uit de staart van slug en niet hieruit; zie vak_uit_suffix
    redeemable      staat op waar zodra de markt heeft afgerekend
    endDate         niet gebruikt: de sluiting wordt gerekend als middernacht
                    in de tijdzone van de stad, zie uren_tot_sluiting

De tabel VELD_ALIAS hieronder houdt per logisch veld een lijst kandidaatnamen
aan. De eerste van elke lijst is de naam die de API nu geeft; de rest staat er
als vangnet voor als Polymarket iets hernoemt.

    python3 bot/portfolio.py --dump-raw

drukt de eerste regel ruw af plus, per logisch veld, welke kandidaat raak was
en welke sleutels nergens op aansluiten. Wijkt er iets af, dan is alleen deze
tabel het aanpassen waard; de rest van de module raakt de ruwe namen niet aan.

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
# datetime.time heet hier dtime: de naam time leest anders als de module
from datetime import datetime, date, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weer
import logger
import signalen as S
import waarneming as W

# Het adres dat de posities aanhoudt, niet per se het adres waarmee je tekent:
# op Polymarket is dat vaak een apart proxy-adres, en het positie-eindpunt keert
# op de houder. Vraag je het verkeerde op, dan komt er een lege lijst terug en
# meldt deze module doodleuk nul open posities. Met --wallet is hij per run te
# overschrijven, en met --dump-raw is in een commando te zien of er wat staat.
WALLET = "0x470b23A4b98C191e50b90AcD62D2cc18670A52AB"
DATA_API = "https://data-api.polymarket.com/positions"
WORTEL = Path(__file__).resolve().parent.parent
UIT_JSON = WORTEL / "portfolio.json"

MIN_SIZE = 0.5           # kleiner is een afgerond restje, geen positie
STAND_BREEDTE = {"°F": 2.0, "°C": 1.0}   # vakbreedte bij een open einde
WIN_DREMPEL = 0.55       # onder deze modelwinkans staat het licht op rood
DELTA_PROB_ORANJE = 15.0 # procentpunten kansstijging die oranje rechtvaardigt

# Prijzen waarbij de markt geen mening meer heeft maar een uitslag. Polymarket
# rekent af op het dagmaximum zodra dat binnen is, en de prijs schiet dan naar
# 0,0005 of 0,9995 terwijl de dag lokaal nog niet voorbij is. Een stoplicht over
# "schuift de verwachting nog op" zegt daar niets meer: er valt niets meer op te
# schuiven. Zonder deze grens kreeg een verloren positie groen mee plus een edge
# van tientallen procentpunten, omdat het model de afrekening niet kent.
BESLIST_LAAG = 0.02
BESLIST_HOOG = 0.98

# Wanneer een meningsverschil met de markt tegen het model pleit in plaats van
# voor een koopje. Beide drempels zijn gemeten op 230 afgerekende stad-dagen uit
# signalen.csv, met de Brier-score van model en markt per vak (lager is beter).
#
# De klok telt af naar het verwachte piekmoment en niet naar de sluiting, want
# dat is de klok die telt: de markt loopt voor omdat hij de al gemeten
# temperatuur van die dag ziet, en dat voordeel hangt aan het moment waarop het
# dagmaximum valt, niet aan middernacht.
#
#     meer dan 24u voor de piek   model 0,0655   markt 0,0599    markt  9% beter
#     12 tot 24u voor de piek     model 0,0673   markt 0,0592    markt 12% beter
#     6 tot 12u voor de piek      model 0,0666   markt 0,0505    markt 24% beter
#     0 tot 6u voor de piek       model 0,0640   markt 0,0370    markt 42% beter
#     piek voorbij                model 0,0670   markt 0,0098    markt 85% beter
#
# Het model verbetert nauwelijks naarmate de dag vordert; de markt gaat er een
# orde van grootte op vooruit. Bij twaalf uur voor de piek verdubbelt zijn
# voordeel, en daar ligt de grens.
#
# Het piekuur staat niet in signalen.csv, dus voor de meting is 15:00 lokaal
# aangenomen. De uitkomst hangt daar niet aan: over aangenomen piekuren van
# 13:00 tot 17:00 blijft het patroon 8-9% / 11-14% / 20-28% / 31-62% / 73-100%.
#
# De 20pp is net zo gemeten. Binnen twaalf uur voor de piek, uitgesplitst naar
# de grootte van het meningsverschil:
#
#     alle vakken     model 0,0661   markt 0,0367    markt 44% beter
#     meer dan 10pp   model 0,1869   markt 0,0809    markt 57% beter
#     meer dan 20pp   model 0,2983   markt 0,0991    markt 67% beter
#     meer dan 40pp   model 0,4962   markt 0,0736    markt 85% beter
#
# Hoe groter het verschil, hoe vaker de markt gelijk had. Lager dan 20pp zou
# verdedigbaar zijn, want ook daar wint de markt, maar dan markeert de vlag een
# op de drie vakken en zegt hij niets meer; bij 20pp is het ongeveer een op de
# acht.
#
# Sinds Kaapstad 11 augustus 2026 waardeert de vlag het stoplicht ook af als
# het model dicht op de piek een edge claimt die de markt niet ziet: groen
# wordt oranje, en na de piek rood. Toen bleef een dag-0 positie groen staan
# (en werd zelfs vergroot) terwijl de markt de al gemeten temperatuur zag en
# het model er 3° naast zat. Zie markeer_markt.
MARKT_VENSTER_UREN = 12.0
MARKT_VERSCHIL_PP = 20.0

# Herkansingen op de ensemblefetch. Eén hapering kost anders het hele
# modelbeeld van een stad en daarmee het licht van elke positie daar.
FETCH_POGINGEN = 3
FETCH_PAUZE = 10.0       # seconden, oplopend per poging

# Steden zonder betrouwbare biaskalibratie: het Open-Meteo raster zit er op de
# post waarop Polymarket afwikkelt ongeveer 2 °C naast. Het stoplicht kan daar
# een zekerheid suggereren die het niet waarmaakt, dus zetten we het erbij.
# CPT hoort er ook bij: het station op Kaapstad Internationaal loopt in het
# winterhalfjaar dikstaartig weg van het raster (fouten van -6° tot +5° binnen
# een maand, augustus 2026), en de modellen zien die lokale uitschieters samen
# niet aankomen.
HOGE_ONZEKERHEID = {"TYO", "SIN", "CPT"}

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
    "redeemable":    ["redeemable"],
}

# peak_hour staat achteraan en niet ertussen: de bestaande kolommen houden hun
# plek, zodat wie op index leest niets merkt. Oude regels zijn met
# bot/migratie_portfolio_history.py aangevuld met een leeg veld.
HIST_KOP = ["gelogd_utc", "key", "doel_datum", "bracket_label", "adj_mean_now",
            "model_prob_now", "current_bid", "city_bias_used", "light",
            "peak_hour",
            # vanaf de intraday-conditionering: zonder deze twee lijkt een kans
            # die verspringt doordat de meting binnenkwam later op een
            # weersverandering, net als bij city_bias_used. Ze staan ná
            # peak_hour omdat die al in het logboek op schijf staat.
            "observed_today", "restfactor"]


def hist_rij(r: dict, nu: str) -> list:
    """Een regel voor portfolio_history.csv, in de volgorde van HIST_KOP."""
    def leeg(x):
        return "" if x is None else x
    return [nu, r["city"], r["date"], r["bracket"],
            leeg(r["adj_mean_now"]), leeg(r["model_prob_now"]),
            leeg(r["current_bid"]), leeg(r["city_bias_used"]),
            r["light"], leeg(r["peak_hour"]),
            leeg(r["observed_today"]), leeg(r["restfactor"])]

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
    try:
        rijen = haal_posities(wallet)
    except Exception as ex:                        # noqa: BLE001
        print(f"Ophalen mislukt voor {wallet}: {ex}")
        return 1
    # Het adres in de uitvoer, zodat twee runs naast elkaar te leggen zijn.
    print(f"{len(rijen)} regels van {DATA_API} voor {wallet}")
    if not rijen:
        print("  Leeg. Dat kan kloppen, maar ook betekenen dat dit het adres is "
              "waarmee je tekent")
        print("  en niet het proxy-adres dat de posities aanhoudt.")
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
    """Stad, doeldatum, reeks en het vaksuffix uit een markt- of eventslug. Dit
    is slug_van uit signalen.py achterstevoren; die bouwt hem op als
    highest-temperature-in-{stad}-on-{maand}-{dag}-{jaar}, met bij een losse
    markt het vak erachter."""
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
    return {"key": key, "datum": dag.isoformat(), "soort": soort,
            "suffix": m.group(6) or ""}


# De zes vormen die het vaksuffix aanneemt, alle zes machinaal opgebouwd:
# 23c · 82-83f · 22corbelow · 32corhigher · 81forbelow · 100forhigher
_VAK_RE = re.compile(r"^(\d+)(?:-(\d+))?([cf])(orbelow|orlower|orless|orhigher|orabove|ormore)?$")


def vak_uit_suffix(suffix: str):
    """De vakgrenzen uit het suffix van een marktslug.

    Dit is de betrouwbare route, en niet de titel van de markt. De data-API
    geeft als titel de hele vraag ("Will the highest temperature in London be
    30°C on August 9?"), en daar staan twee getallen in: de temperatuur en de
    dag. Welke van de twee vooraan staat hangt af van hoe Polymarket de vraag
    formuleert, en die formulering is van hen. Het suffix bevat alleen het vak."""
    if not suffix:
        return None
    m = _VAK_RE.match(suffix.strip().lower())
    if not m:
        return None
    eerste, tweede, eenheid, open_kant = m.groups()
    eenheid = "°C" if eenheid == "c" else "°F"
    if open_kant in ("orbelow", "orlower", "orless"):
        return {"lo": None, "hi": int(eerste), "eenheid": eenheid}
    if open_kant:
        return {"lo": int(eerste), "hi": None, "eenheid": eenheid}
    return {"lo": int(eerste), "hi": int(tweede) if tweede else int(eerste),
            "eenheid": eenheid}


def vaklabel(lo, hi, eenheid: str) -> str:
    """Het vak zoals Polymarket het schrijft, uit de grenzen opgebouwd. Zo staat
    er in de tabel en in de reeks hetzelfde etiket als in signalen.csv, ook als
    de vraagtekst van de markt ooit anders geformuleerd wordt."""
    if lo is None and hi is None:
        return "?"
    if lo is None:
        return f"{hi}{eenheid} or below"
    if hi is None:
        return f"{lo}{eenheid} or higher"
    if lo == hi:
        return f"{lo}{eenheid}"
    return f"{lo}-{hi}{eenheid}"


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


def naam_van(key: str) -> str:
    """De stadsnaam bij een sleutel, voor in het tabblad. De sleutel zelf blijft
    overal het koppelveld: daarop sluiten signalen.csv, ensemble_log.csv en
    portfolio_history.csv op elkaar aan. Een naam is om te lezen, geen sleutel."""
    stad = weer.STAD_OP_KEY.get(key)
    return stad["naam"] if stad else key


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

    # Het vak komt uit het suffix van de slug, niet uit de titel: de titel is
    # de hele vraag en bevat naast de temperatuur ook de dag.
    vak = vak_uit_suffix(plek["suffix"])
    if not vak:
        vak = S.vak_uit(titel) if titel else None      # terugval
    if not vak or (vak["lo"] is None and vak["hi"] is None):
        return None, (f"vak niet te lezen uit slug {plek['suffix']!r} "
                      f"en ook niet uit de titel {titel!r}")

    eenheid = vak["eenheid"] or eenheid_van(plek["key"])
    return {
        "key": plek["key"], "datum": plek["datum"], "soort": plek["soort"],
        "label": vaklabel(vak["lo"], vak["hi"], eenheid),
        "titel_ruw": str(titel).strip() if titel else "",
        "lo": vak["lo"], "hi": vak["hi"],
        "eenheid": eenheid,
        "direction": richting,
        "size": size,
        "avg_price": _getal(_pak(rij, "avg_price")[0]),
        "current_bid": _getal(_pak(rij, "current_bid")[0]),
        "condition_id": _pak(rij, "condition_id")[0],
        "redeemable": bool(_pak(rij, "redeemable")[0]),
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

    def __init__(self, params: dict, waarnemingen: dict = None):
        self.params = params
        # Wat er vandaag al gemeten is op het afrekenstation, per stad. Leeg
        # gelaten rekent alles onvoorwaardelijk door, precies zoals daarvoor.
        self.waarnemingen = waarnemingen or {}
        self._per_stad: dict = {}     # key -> leden of Exception
        self._per_dag: dict = {}      # (key, datum, soort) -> beeld of None
        self._pieken: dict = {}       # key -> {datum: {uur, lo, hi}}

    def _leden(self, key: str):
        """De ensembleleden van een stad, met herkansingen.

        Zonder die herkansingen kost één hapering in de verbinding het hele
        modelbeeld van een stad, en staat elke positie daar die run zonder
        licht. Dat is in de eerste vijf runs twee keer gebeurd, op twee
        verschillende steden, allebei met een TLS-handshake die niet rond kwam:
        een kwaal van het moment, niet van de stad. Een tweede poging tien
        seconden later haalt het dan gewoon."""
        if key in self._per_stad:
            return self._per_stad[key]

        stad = weer.STAD_OP_KEY.get(key)
        if not stad:
            self._per_stad[key] = ValueError(f"{key} staat niet in weer.STEDEN")
            return self._per_stad[key]

        try:
            self._per_stad[key] = logger.met_herkansing(
                logger.haal_leden, stad, S.ENS_VELDEN,
                pogingen=FETCH_POGINGEN, pauze=FETCH_PAUZE)
        except RuntimeError as ex:
            # Op is op: de reden gaat mee de JSON in, zodat in het tabblad staat
            # waarom er geen licht is in plaats van dat de positie eruit valt.
            self._per_stad[key] = ex
        return self._per_stad[key]

    def piek(self, key: str, datum: str):
        """Het verwachte piekuur van een doeldag, of None. Eén uurcurve per
        stad, niet per positie: die dekt drie dagen tegelijk.

        Lukt de fetch ook met herkansingen niet, dan blijft het leeg en valt
        beoordeel() terug op de sluiting. Dat is een slechtere klok, maar wel
        een klok — en de reden staat in de run-uitvoer, want een stad die
        stilletjes op de grovere klok terugvalt is niet na te trekken."""
        if key not in self._pieken:
            stad = weer.STAD_OP_KEY.get(key)
            self._pieken[key] = {}
            if stad:
                try:
                    self._pieken[key] = pieken_uit(logger.met_herkansing(
                        weer._get_json, uur_url(stad),
                        pogingen=FETCH_POGINGEN, pauze=FETCH_PAUZE, timeout=45))
                except RuntimeError as ex:
                    print(f"  {key}: uurcurve mislukt ({ex}); "
                          f"de sluiting blijft over als klok")
        return (self._pieken[key] or {}).get(datum)

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
            # De meting van vandaag telt alleen op lead 0 en alleen voor deze
            # doeldag; voor_stad bewaakt allebei.
            "waarneming": W.voor_stad(self.waarnemingen, key, datum, lead, soort),
        }, None)
        self._per_dag[sleutel] = uit
        return uit

    def vak_kans(self, beeld: dict, lo, hi):
        """De modelkans op een vak, met dezelfde functie als het signalenlog:
        bracket_probs in de opdracht, onze_kansen hier.

        Staat er een meting van vandaag bij, dan is die kans geconditioneerd: een
        vak dat de dag al voorbij is gelopen krijgt nul. Juist hier telt dat, want
        dit getal gaat als model_win_prob het stoplicht in."""
        kansen = S.onze_kansen([{"lo": lo, "hi": hi}], beeld["eigen"],
                               beeld["markt_eenheid"], beeld["app_eenheid"],
                               beeld.get("waarneming"))
        return kansen[0] if kansen else None


# ── Stap 1d: het modelbeeld bij instap, uit logs/signalen.csv ─────────────────

def instap_index(pad: Path = None) -> dict:
    """De vroegst gelogde regel per (datum, stad, reeks, ondergrens, bovengrens)
    uit het signalenlog: het modelbeeld zoals het er bij de instap bij stond.

    De sleutel loopt over de grenzen en niet over het etiket. Het etiket is
    tekst die Polymarket schrijft en aan twee kanten anders kan luiden — de
    data-API geeft de hele vraag, het signalenlog de vaknaam — en dan vindt een
    koppeling op tekst nooit iets, zonder dat er ergens een fout valt. De
    grenzen zijn getallen en aan beide kanten hetzelfde.

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
            g_lo, g_hi = _getal(lo), _getal(hi)
            sleutel = (datum, key, soort,
                       None if g_lo is None else int(g_lo),
                       None if g_hi is None else int(g_hi))
            vorig = uit.get(sleutel)
            if vorig and vorig["gelogd"] <= gelogd:
                continue
            uit[sleutel] = {
                "gelogd": gelogd, "eenheid": eenheid, "label": label,
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


# De uurcurve waaruit het piektijdstip komt, dezelfde vijf systemen als
# UUR_MODELLEN in index.html.
UUR_MODELLEN = ["ecmwf_ifs025", "ecmwf_aifs025", "gfs_seamless",
                "icon_seamless", "gem_seamless"]


def uur_url(stad: dict) -> str:
    """Zelfde aanroep als bundelUren in index.html: de gewone forecast-API met
    uurwaarden, niet de ensemble-API. timezone=auto, dus de tijdstempels staan
    al in de lokale tijd van de stad."""
    unit = "fahrenheit" if stad["eenheid"] == "F" else "celsius"
    return ("https://api.open-meteo.com/v1/forecast"
            f"?latitude={stad['lat']}&longitude={stad['lon']}"
            f"&hourly=temperature_2m&models={','.join(UUR_MODELLEN)}"
            f"&temperature_unit={unit}&forecast_days=3&timezone=auto")


def pieken_uit(j: dict) -> dict:
    """{datum: {uur, lo, hi}} — het uur van de dagpiek in het modelgemiddelde,
    met lo/hi als spreiding van dat tijdstip tussen de modellen. Spiegelbeeld
    van piekenUit in index.html, inclusief de eis van minstens zes uurwaarden
    voordat een dag meetelt."""
    H = (j or {}).get("hourly") or {}
    tijden = H.get("time") or []
    reeksen = [v for k, v in H.items()
               if k.startswith("temperature_2m") and isinstance(v, list)]
    if not tijden or not reeksen:
        return {}

    per_dag: dict = {}
    for i, ts in enumerate(tijden):
        # Elk model houdt zijn eigen plek, met None waar het dit uur mist.
        # Compacteren per uur schoof de modellen in elkaar: vals[m] was dan
        # het ene uur ecmwf en het andere uur gfs, en de spreiding hieronder
        # rekende een argmax uit over een reeks die geen enkel model is.
        vals = [r[i] if i < len(r) else None for r in reeksen]
        waarden = [v for v in vals if v is not None]
        if not waarden:
            continue
        per_dag.setdefault(ts[:10], []).append(
            {"uur": int(ts[11:13]), "gem": sum(waarden) / len(waarden), "vals": vals})

    uit = {}
    for dag, rij in per_dag.items():
        if len(rij) < 6:              # te weinig uren, geen betrouwbare piek
            continue
        beste = max(rij, key=lambda x: x["gem"])
        arg = []
        for m in range(len(reeksen)):
            kandidaten = [x for x in rij if x["vals"][m] is not None]
            if kandidaten:
                arg.append(max(kandidaten, key=lambda x: x["vals"][m])["uur"])
        uit[dag] = {"uur": beste["uur"],
                    "lo": min(arg) if arg else beste["uur"],
                    "hi": max(arg) if arg else beste["uur"]}
    return uit


def uren_tot_piek(key: str, datum: str, uur: int):
    """Uren tot het verwachte warmste moment van de doeldag, in de lokale tijd
    van de stad. Negatief zodra de piek geweest is.

    Dit is de klok die er voor een positie toe doet. De markt sluit formeel om
    middernacht, maar de uitslag ligt er zodra het dagmaximum gevallen is: bij
    Busan rekende Polymarket af terwijl er lokaal nog uren op de klok stonden.
    Uren tot sluiting telde die uren mee alsof er nog iets kon veranderen."""
    stad = weer.STAD_OP_KEY.get(key)
    if not stad or uur is None:
        return None
    try:
        dag = date.fromisoformat(datum)
    except ValueError:
        return None
    piek = datetime.combine(dag, dtime(int(uur), 0), tzinfo=ZoneInfo(stad["tz"]))
    return (piek - datetime.now(timezone.utc)).total_seconds() / 3600


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
        "city": key, "city_name": naam_van(key), "date": datum, "soort": soort,
        "bracket": pos["label"], "direction": pos["direction"],
        "size": round(pos["size"], 4),
        "avg_price": pos["avg_price"], "current_bid": pos["current_bid"],
        "adj_mean_now": None, "adj_mean_entry": None, "city_bias_used": None,
        "model_prob_now": None, "model_prob_entry": None, "model_win_prob": None,
        "d": None, "b": round(b, 2), "delta_prob": None, "delta_mean": None,
        "fair_value": None, "edge_now": None,
        "hours_to_close": None, "hours_to_peak": None,
        "peak_hour": None, "peak_hour_spread": None,
        "light": "unknown", "reason": "",
        "entry_known": False,
        "market_decided": False,
        "market_disagrees": False, "market_note": "",
        # wat er vandaag al gemeten is op het afrekenstation, en de restfactor
        # die daaruit volgde; leeg op lead 1 en 2 en bij een gemist station
        "observed_today": None, "observed_hour": None, "restfactor": None,
        "high_uncertainty": key in HOGE_ONZEKERHEID,
        "unit": eenheid,
        "bracket_lo": lo, "bracket_hi": hi,
        "title_raw": pos.get("titel_ruw", ""),
        "slug": pos.get("slug"), "condition_id": pos.get("condition_id"),
    }

    uren = uren_tot_sluiting(key, datum)
    rij["hours_to_close"] = None if uren is None else round(uren, 2)

    # De klok die er voor de positie toe doet: tot het verwachte warmste moment,
    # niet tot middernacht. Zodra de piek geweest is ligt de uitslag er, ook al
    # loopt de dag lokaal nog uren door. In een functie, want de fetch erachter
    # is niet gratis: een afgerekende positie gebruikt deze klok niet meer, dus
    # daar blijft de aanroep achterwege.
    def vul_piek():
        p = cache.piek(key, datum)
        if p:
            rij["peak_hour"] = p["uur"]
            rij["peak_hour_spread"] = [p["lo"], p["hi"]]
            u = uren_tot_piek(key, datum, p["uur"])
            rij["hours_to_peak"] = None if u is None else round(u, 2)

    # 1c: het modelbeeld van nu.
    beeld, fout = cache.beeld(key, datum, soort)
    if not beeld:
        vul_piek()             # de klok blijft staan, alleen het licht ontbreekt
        rij["reason"] = fout or "modelbeeld ontbreekt"
        return rij

    mu = beeld["adj_mean"]
    kans = cache.vak_kans(beeld, lo, hi)
    rij["adj_mean_now"] = None if mu is None else round(mu, 2)
    rij["model_prob_now"] = None if kans is None else round(kans, 4)

    wn = beeld.get("waarneming")
    if wn:
        rij["observed_today"] = round(
            S.naar_eenheid(wn["m"], beeld["app_eenheid"], beeld["markt_eenheid"]), 2)
        rij["observed_hour"] = wn["uur"]
        rij["restfactor"] = round(W.restfactor(wn["uur"], soort), 4)
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

    # 1d: het modelbeeld bij instap, gezocht op de vakgrenzen.
    was = instap.get((datum, key, soort, lo, hi))
    if was and was.get("label"):
        rij["bracket"] = was["label"]     # het etiket zoals de markt het schrijft
    if was and was["model_prob"] is not None:
        rij["entry_known"] = True
        rij["adj_mean_entry"] = None if was["adj_mean"] is None else round(was["adj_mean"], 2)
        rij["model_prob_entry"] = round(was["model_prob"], 4)
        if kans is not None:
            rij["delta_prob"] = round((kans - was["model_prob"]) * 100, 2)
        if mu is not None and was["adj_mean"] is not None:
            rij["delta_mean"] = verschuiving(was["adj_mean"], mu, lo, hi)

    # 1f: het stoplicht.
    #
    # Eerst de vraag of er nog iets te signaleren valt. Staat de prijs op een
    # uiterste, dan heeft de markt al afgerekend en gaat het stoplicht uit: het
    # zou anders een verloren positie groen meegeven, want het model rekent op
    # de verwachting van de dag en niet op de uitslag die er al ligt.
    # redeemable is het antwoord van Polymarket zelf en gaat voor; de prijs is
    # de terugval, want tussen afrekenen en redeemable zit soms tijd.
    bied = pos["current_bid"]
    op_uiterste = bied is not None and (bied <= BESLIST_LAAG or bied >= BESLIST_HOOG)
    if pos.get("redeemable") or op_uiterste:
        rij["market_decided"] = True
        rij["light"] = "settled"
        gewonnen = bied is not None and bied >= BESLIST_HOOG
        bron = "redeemable staat aan" if pos.get("redeemable") else \
               f"de markt noteert dit vak op {bied:.4f}"
        rij["reason"] = (
            f"{bron}: afgerekend, {'gewonnen' if gewonnen else 'verloren'}. Het "
            f"stoplicht zegt hier niets meer, en edge_now is geen kans maar het "
            f"verschil tussen de uitslag en wat het model dacht")
        return rij

    vul_piek()

    if mu is None or kans is None:
        rij["reason"] = "modelbeeld onvolledig"
        return rij
    rij["light"], rij["reason"] = stoplicht(d, b, rij["model_win_prob"],
                                            rij["delta_prob"], d == 0, is_ja)
    if rij["high_uncertainty"]:
        rij["reason"] += " · let op: geen betrouwbare biaskalibratie voor deze stad"
    if rij["observed_today"] is not None:
        # Waarom dit erbij hoort: een kans van nul leest heel anders als het vak
        # de dag al voorbij gelopen is dan als het model het onwaarschijnlijk
        # vindt. Zonder deze regel is de kleur niet na te rekenen.
        rij["reason"] += (
            f" · geconditioneerd op {rij['observed_today']}{eenheid} die vandaag "
            f"tot {rij['observed_hour']:.0f} uur gemeten is")

    # De vlag telt naar de piek; ontbreekt die, dan blijft de sluiting over.
    markeer_markt(rij, rij["hours_to_peak"] if rij["hours_to_peak"] is not None
                  else rij["hours_to_close"],
                  piek_bekend=rij["hours_to_peak"] is not None)
    return rij


def markeer_markt(rij: dict, uren, piek_bekend: bool = True) -> None:
    """Zet de vlag als het model dicht op de piek sterk van de markt afwijkt.

    `uren` is de tijd tot het verwachte warmste moment, en mag negatief zijn:
    is de piek voorbij, dan is dat juist het sterkste geval — daar zit de markt
    85% dichter bij de uitkomst dan het model. Ontbreekt het piekuur, dan komt
    hier de tijd tot sluiting binnen met piek_bekend=False; die grens is grover
    maar dezelfde orde. Negatief betekent op die klok geen piek-geweest maar
    een al gesloten markt, en daarover valt niets meer te signaleren.

    Claimt het model in dit venster een edge die de markt niet ziet (edge_now
    positief), dan gaat het stoplicht ook omlaag: groen wordt oranje, en na de
    piek rood. In dit venster kijkt de markt naar de al gemeten temperatuur en
    is een grote claim van het model vaker een modelfout dan een kans; op 11
    augustus 2026 bleef een dag-0 positie op Kaapstad groen staan terwijl het
    model er 3° naast zat. Prijst de markt de positie juist hóger dan het model
    (edge_now negatief), dan blijft de kleur staan: het model is dan hooguit te
    somber, en daar loopt niets gevaar."""
    edge = rij["edge_now"]
    if edge is None or uren is None or uren > MARKT_VENSTER_UREN:
        return
    if not piek_bekend and uren < 0:
        return
    if abs(edge) <= MARKT_VERSCHIL_PP:
        return
    rij["market_disagrees"] = True
    if edge > 0:
        if uren < 0 and rij.get("light") in ("green", "amber"):
            rij["light"] = "red"
            rij["reason"] += (" · afgewaardeerd: de piek is voorbij en de markt "
                              "ziet de uitkomst al, maar prijst de positie "
                              f"{edge:.0f}pp onder het model")
        elif rij.get("light") == "green":
            rij["light"] = "amber"
            rij["reason"] += (" · afgewaardeerd: dicht op de piek claimt het "
                              f"model {edge:.0f}pp meer dan de markt betaalt")
    # de prijs van jouw kant is de kans die de markt jouw positie geeft
    markt = rij["current_bid"]
    # Hoe dichter op de piek, hoe groter het gemeten voordeel van de markt; na
    # de piek is er van voorspellen geen sprake meer. Zonder piekuur telt de
    # klok naar de sluiting en zegt de tekst dat ook: "tot de verwachte piek"
    # schrijven terwijl er tijd tot middernacht gemeten is, is een klok die
    # liegt. De 58% is de oudere meting op die grovere klok (binnen twaalf uur
    # voor sluiting, 167 stad-dagen).
    if not piek_bekend:
        wanneer = f"nog {uren:.1f} uur tot sluiting (piekuur niet beschikbaar)"
        hoeveel = "58%"
        venster = "Zo dicht op de sluiting"
    elif uren < 0:
        wanneer = f"de piek is {-uren:.1f} uur geleden verwacht"
        hoeveel = "85%"
        venster = "Zo dicht op de piek"
    elif uren <= 6:
        wanneer = f"nog {uren:.1f} uur tot de verwachte piek"
        hoeveel = "42%"
        venster = "Zo dicht op de piek"
    else:
        wanneer = f"nog {uren:.1f} uur tot de verwachte piek"
        hoeveel = "24%"
        venster = "Zo dicht op de piek"

    if edge > 0:
        rij["market_note"] = (
            f"{wanneer} en het model zit {edge:+.1f}pp boven de markt "
            f"({rij['model_win_prob'] * 100:.0f}% tegen {markt * 100:.0f}%). "
            f"{venster} zit de markt gemeten {hoeveel} dichter bij de "
            f"uitkomst dan het model, want die ziet de al gemeten temperatuur van "
            f"vandaag. Lees dit eerder als twijfel aan het model dan als een edge "
            f"om te pakken")
    else:
        rij["market_note"] = (
            f"{wanneer} en de markt prijst deze positie {-edge:.1f}pp hoger dan het "
            f"model ({markt * 100:.0f}% tegen {rij['model_win_prob'] * 100:.0f}%). "
            f"{venster} heeft de markt meestal gelijk ({hoeveel} dichter "
            f"bij de uitkomst), dus het model is hier waarschijnlijk te somber")


# ── Stap 1i: de uitvoer ───────────────────────────────────────────────────────

# Afgerekende posities onderaan: daar valt niets meer te beslissen.
VOLGORDE = {"red": 0, "amber": 1, "unknown": 2, "green": 3, "settled": 4}


def bouw(posities_ruw: list, params: dict, instap: dict, wallet: str,
         cache=None, waarnemingen=None) -> dict:
    """`waarnemingen` op None laat de metingen van vandaag ophalen; een dict
    (ook een lege) wordt gebruikt zoals hij is. Dat tweede is wat de zelftest
    doet: die hoort offline te draaien."""
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

    if cache is None:
        # Wat er vandaag al gemeten is, alleen voor de steden waar wat open
        # staat: dat scheelt tientallen verzoeken ten opzichte van alle 49. Valt
        # het om, dan blijft alles onvoorwaardelijk doorrekenen — hetzelfde
        # gedrag als voor de conditionering, dus een gemiste meting kost hooguit
        # scherpte en nooit een licht.
        wn = waarnemingen if waarnemingen is not None else {}
        keys = {p["key"] for p in open_posities}
        if waarnemingen is None and keys:
            try:
                wn = W.haal_vandaag([s for s in weer.STEDEN if s["key"] in keys])
                print(f"  waarnemingen van vandaag: {len(wn)} van "
                      f"{len(keys)} steden met posities")
            except Exception as ex:                # noqa: BLE001
                print(f"  waarnemingen mislukt ({ex}); "
                      "kansen blijven onvoorwaardelijk")
        cache = ModelCache(params, wn)
    rijen = [beoordeel(p, cache, instap) for p in open_posities]
    # Binnen een kleur op de klok die telt: tot de piek, en pas als die
    # ontbreekt tot de sluiting.
    def wanneer(r):
        for veld in ("hours_to_peak", "hours_to_close"):
            if r[veld] is not None:
                return r[veld]
        return math.inf

    rijen.sort(key=lambda r: (VOLGORDE.get(r["light"], 9), wanneer(r)))

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
            "n_settled": sum(1 for r in rijen if r["light"] == "settled"),
            "n_market_disagrees": sum(1 for r in rijen if r["market_disagrees"]),
            "n_unmapped": len(unmapped),
            "total_exposure": round(blootstelling, 2),
        },
    }


def schrijf_uit(payload: dict) -> None:
    UIT_JSON.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    nu = payload["generated_at"]
    rijen = [hist_rij(r, nu) for r in payload["positions"]]
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
        if not ruw:
            # Nul posities leest als "alles gedekt", terwijl het net zo goed het
            # verkeerde adres kan zijn. Dat verschil hoort hardop te staan.
            print(f"  let op: het eindpunt geeft niets terug voor {wallet}.")
            print("  Dat kan kloppen, maar ook betekenen dat dit het adres is "
                  "waarmee je tekent")
            print("  en niet het proxy-adres dat de posities aanhoudt. "
                  "Toets met --dump-raw.")

    payload = bouw(ruw, S.laad_params(), instap_index(), wallet)
    schrijf_uit(payload)

    s = payload["summary"]
    print(f"portfolio.json: {s['n_positions']} posities · "
          f"{s['n_red']} rood, {s['n_amber']} oranje, {s['n_green']} groen, "
          f"{s['n_unknown']} onbekend, {s['n_settled']} afgerekend · "
          f"{s['n_unmapped']} niet gekoppeld")
    for r in payload["positions"]:
        if r["light"] in ("red", "amber", "unknown", "settled"):
            print(f"  {r['light']:<7} {r['city']} {r['date']} {r['bracket']} "
                  f"{r['direction']}: {r['reason']}")
    for r in payload["positions"]:
        if r["market_disagrees"]:
            print(f"  markt   {r['city']} {r['date']} {r['bracket']}: {r['market_note']}")
    for u in payload["unmapped"]:
        print(f"  unmapped: {u['reason']}")
    return 0


def main(argv: list) -> int:
    # Eerst alle vlaggen lezen en pas daarna handelen. Anders hangt --dump-raw
    # af van de volgorde: staat hij voor --wallet, dan draait hij op het
    # standaardadres en meldt hij netjes nul posities voor het verkeerde adres.
    wallet, bestand, ruw = WALLET, None, False
    for i, a in enumerate(argv):
        if a == "--dump-raw":
            ruw = True
        elif a == "--wallet" and i + 1 < len(argv):
            wallet = argv[i + 1]
        elif a == "--positions-file" and i + 1 < len(argv):
            bestand = argv[i + 1]
    if ruw:
        return dump_raw(wallet)
    return run(wallet, bestand)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
