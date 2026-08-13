#!/usr/bin/env python3
"""Wat er vandaag al gemeten is, en wat dat met de verwachting doet.

Het gat dat dit dicht
---------------------
De Brier-tabel in README.md zegt het onomwonden: naarmate de dag vordert
verbetert het model nauwelijks (0,0662 → 0,0648) terwijl de markt zijn fout
meer dan halveert (0,0612 → 0,0270). De oorzaak staat er ook bij — binnen twaalf
uur ziet de markt de al gemeten temperatuur van die dag, en voorspelt het model
nog steeds alsof de dag nog moet beginnen.

Dat is geen modelfout maar een ontbrekende voorwaarde. Het dagmaximum kan niet
lager uitvallen dan wat er om drie uur 's middags al op de meter staat, en de
markt weet dat wel en wij niet. Deze module levert die meting aan en rekent hem
door.

De wiskunde
-----------
Noem `m` de hoogste temperatuur die vandaag tot nu toe gemeten is op het station
waarop de markt afrekent, en `R` het maximum over de uren die nog komen. Het
dagmaximum is dan

    T = max(m, R)

Meer is het niet. Modelleren we `R` als normaal verdeeld, dan volgt de
verdelingsfunctie van `T` er meteen uit:

    F(t) = 0                       voor t < m
    F(t) = Phi((t - mu_R) / sig_R) voor t >= m

Die ene knik doet al het werk. Een vak dat helemaal onder `m` ligt krijgt kans
nul — het is niet onwaarschijnlijk meer, het is onmogelijk. En het vak waar `m`
in valt krijgt er vanzelf de puntmassa `Phi((m - mu_R)/sig_R)` bij: precies de
kans dat de piek al geweest is. Er is geen aparte tak voor nodig; het volgt uit
de afkapping.

Voor de laagstetemperatuurreeks staat alles op zijn kop: `T = min(m, R)` en
`F(t) = 1` boven `m`.

Wat er van `R` overblijft
-------------------------
Twee getallen beschrijven `R`, allebei uit dezelfde restfactor `w` tussen 0 en 1:

    mu_R = mu * w + m * (1 - w)
    sig_R = max(sig * w, 0,05)

Bij `w` = 1 is er nog een hele dag te gaan: `R` is de onveranderde verwachting en
`m` doet alleen dienst als ondergrens. Bij `w` → 0 is de dag gelopen: `mu_R` valt
samen met `m`, de spreiding verdwijnt en alle massa komt op het vak van `m` te
liggen. Daartussen schuift het geleidelijk op.

Waar de restfactor vandaan komt
-------------------------------
Niet uit een aanname over hoe laat het warmst is, maar uit `logs/signalen.csv`.
Voor elke lead-0 reeks is per logmoment de marktverdeling over de vakken bekend;
de entropie daarvan is terug te rekenen naar een sigma in vakbreedtes. Uitgezet
tegen het lokale uur geeft dat de curve waarmee de onzekerheid over de dag
instort (326 reeksen, 49 steden):

    lokaal uur   0-10    11     12     13     14     15     16    17+
    sigma markt  0,774  0,625  0,561  0,528  0,454  0,291  0,201  0,18
    verhouding    1,00   0,81   0,72   0,68   0,59   0,38   0,26  0,24

Die verhouding staat als `W_REST_MAX` in polymarkt.js — daar en niet hier, zodat
de app en dit logboek met dezelfde tabel rekenen. `bot/kalibreer_restfactor.py`
rekent hem opnieuw uit het logboek.

Twee kanttekeningen, allebei reden tot voorzichtigheid:

* De curve is de onzekerheid van de *markt*, en die is scherper dan de onze om
  meer redenen dan alleen de meting van vandaag — de markt is domweg een beter
  model in dat venster. Hem onverkort overnemen zou ons scherper maken dan we
  kunnen waarmaken.
* De krimp en de afkapping doen deels hetzelfde werk. De markt heeft de
  ondergrens `m` al in zijn prijzen zitten, dus wie de volle curve op sigma legt
  én daarna afkapt, telt een deel van het effect dubbel.

Daarom gaat er `DEMPING` overheen:

    w = w_ruw * (1 + DEMPING * (1 - w_ruw))

Om vier uur 's middags komt 0,26 daarmee op 0,40, waar de markt op 0,26 zit:
ruimer dus, en de afkapping mag de rest doen. Overschatte scherpte is hier de
gevaarlijke kant op — daar hangt in inzet.py een weddenschap aan.

De demping is met opzet geen vaste factor. Hij is het grootst in de
overgangsuren, waar de afkapping het meeste bijdraagt en de dubbeltelling dus
zit, en valt 's avonds vanzelf weg: dan is 0,06 geen gecensureerde marktmeting
meer maar natuurkunde, en zou ophogen betekenen dat we een afgelopen dag nog
open verklaren. Omdat w_ruw tussen 0 en 1 ligt geldt `w >= w_ruw` hoe dan ook —
we zijn nooit krapper dan de markt, en bot/test_waarneming.py dwingt dat af.

Zodra `waarneming` lang genoeg in signalen.csv staat is de restfactor op de
eigen reeks te kalibreren en kan de demping eruit. Tot die tijd is dit een
gedempte marktcurve, en dat hoort er expliciet bij te staan.

De meting zelf
--------------
`m` komt van hetzelfde station waarop de markt afrekent, via het uurlijkse
METAR-archief van IEM — dezelfde bron en dezelfde `report_type=3` als de
dagelijkse controle in weer.py, zodat de ondergrens in dezelfde reeks staat als
het cijfer waarop afgerekend wordt.

Dat het uurlijkse waarnemingen zijn maakt `m` een *ondergrens* van wat er
werkelijk gemeten is: tussen twee meldingen door kan het warmer geweest zijn.
Die fout staat de goede kant op. Een te lage `m` kapt te weinig af en laat een
vak staan dat eigenlijk al onmogelijk was; een te hoge `m` zou een vak wegstrepen
dat nog kon vallen. Onderschatten is dus veilig, en daarom wordt er niets bij
opgeteld.

Gebruik:

    python3 bot/waarneming.py                 wat er nu gemeten is, alle steden
    python3 bot/waarneming.py --steden NYC    één stad
    python3 bot/waarneming.py --toon-curve    de restfactortabel uitschrijven
"""
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weer
import jslezer

_POLY = jslezer.poly_tekst()
W_REST_MAX = {int(k): v for k, v in jslezer.letterlijk("W_REST_MAX", _POLY).items()}
W_REST_MIN = {int(k): v for k, v in jslezer.letterlijk("W_REST_MIN", _POLY).items()}
DEMPING = jslezer.letterlijk("W_DEMPING", _POLY)
W_SIG_MAX = {int(k): v for k, v in jslezer.letterlijk("W_SIG_MAX", _POLY).items()}
W_SIG_MIN = {int(k): v for k, v in jslezer.letterlijk("W_SIG_MIN", _POLY).items()}

SIGMA_MIN = 0.05         # zelfde ondergrens als onzeKansen in polymarkt.js
IEM_BUNDEL = 12          # stations per verzoek; IEM accepteert er meerdere
IEM_POGINGEN = 3
# Korter dan de 90 s die hier eerst stond, om dezelfde reden als FETCH_TIMEOUT
# in logger.py: een geslaagde archiefaanroep is in enkele seconden terug, dus
# wie na 30 s nog niets heeft krijgt het ook op poging drie niet. Met 90 s kostte
# één onbereikbare tijdzone 270 seconden voordat de volgende aan de beurt was.
IEM_TIMEOUT = 30

# Een harde wandklokgrens over het hele ophalen heen. IEM bundelt per tijdzone,
# en 26 van de 48 steden zitten in een tijdzone waar ze alleen in staan: dat zijn
# 32 verzoeken die bij een trage bron elk hun volle herkansingen opmaken. Op
# 12 augustus 2026 liep de actie Signalenlog daardoor tegen zijn timeout van
# 60 minuten aan en werd afgebroken vóór de commitstap, waarmee ook het al
# opgehaalde ensemblelog van die ronde verloren ging.
#
# Deze grens kiest de veilige kant. Wat op is, is op: de steden die nog niet
# opgehaald zijn krijgen geen ondergrens en rekenen onvoorwaardelijk door,
# precies zoals voor de conditionering. Een ronde zonder conditionering kost
# scherpte; een afgebroken ronde kost de reeks zelf, en die komt niet terug.
BUDGET_S = 240.0


# ── De restfactor ─────────────────────────────────────────────────────────────

def restfactor(uur: float, soort: str = "max") -> float:
    """Hoeveel van de onzekerheid over het dagcijfer er om `uur` lokale tijd nog
    over is, tussen 0 en 1. Lineair geïnterpoleerd tussen hele uren, en gedempt;
    zie de kop van deze module.

    Exact dezelfde functie als restFactor in polymarkt.js — bewaakt door
    bot/test_kern.py, want anders rekent het logboek een andere kans dan de app
    toont."""
    tabel = W_REST_MIN if soort == "min" else W_REST_MAX
    if uur is None:
        return 1.0
    u = float(uur)
    if u <= 0:
        u = 0.0
    if u >= 23:
        u = 23.0
    onder = int(u)
    boven = min(23, onder + 1)
    deel = u - onder
    ruw = tabel[onder] * (1 - deel) + tabel[boven] * deel
    return ruw * (1.0 + DEMPING * (1.0 - ruw))


def sigfactor(uur: float, soort: str = "max") -> float:
    """De verbreding uit W_SIG_MAX, exact dezelfde functie als sigFactor in
    polymarkt.js. Alleen tussen drie en vijf uur 's middags wijkt hij van 1 af;
    de afleiding staat bij de tabel in polymarkt.js."""
    tabel = W_SIG_MIN if soort == "min" else W_SIG_MAX
    if uur is None:
        return 1.0
    u = float(uur)
    if u <= 0:
        u = 0.0
    if u >= 23:
        u = 23.0
    onder = int(u)
    boven = min(23, onder + 1)
    deel = u - onder
    return tabel[onder] * (1 - deel) + tabel[boven] * deel


def conditioneer(mu: float, sigma: float, m: float, uur: float, soort: str):
    """De parameters van `R`, het cijfer over de uren die nog komen.

    Geeft (mu_R, sigma_R). De afkapping op `m` zit hier niet in: die hoort in de
    verdelingsfunctie thuis, want alleen daar is bekend welke kant op afgekapt
    wordt."""
    w = restfactor(uur, soort)
    return (mu * w + m * (1.0 - w),
            max(sigma * w * sigfactor(uur, soort), SIGMA_MIN))


def cdf(t: float, mu_r: float, sigma_r: float, m, soort: str, phi) -> float:
    """F(t) uit de kop van deze module. `phi` wordt meegegeven zodat zowel
    signalen.py als een test dezelfde reeksbenadering kan gebruiken als
    polymarkt.js; math.erf geeft andere laatste cijfers."""
    if m is not None:
        if soort == "min":
            if t > m:
                return 1.0
        elif t < m:
            return 0.0
    return phi((t - mu_r) / sigma_r)


# ── De meting ophalen ─────────────────────────────────────────────────────────

def _iem_url(stations, tznaam: str, d1, d2, soorten=("3",)) -> str:
    """`soorten` zijn de report_type-waarden van IEM: 3 is de routinemelding van
    het hele uur, 4 zijn de specials en 1 is de MADIS-HFMETAR-stroom met
    sub-uurlijkse meldingen.

    Standaard blijft het alleen 3. Dat is met opzet: dat is de reeks die
    Wunderground toont en waar de afrekening op rust. Zie bot/fijnmeting.py voor
    wanneer de andere twee wél mogen meedoen — dat hangt ervan af of de markt op
    het fijne record afrekent of op het uurlijkse."""
    d2p = d2 + timedelta(days=1)          # eindgrens ruim nemen
    delen = "&".join("station=" + urllib.parse.quote(s) for s in stations)
    types = "".join("&report_type=" + urllib.parse.quote(str(t)) for t in soorten)
    return (
        "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
        f"?{delen}&data=tmpf"
        f"&year1={d1.year}&month1={d1.month}&day1={d1.day}"
        f"&year2={d2p.year}&month2={d2p.month}&day2={d2p.day}"
        f"&tz={urllib.parse.quote(tznaam)}&format=comma&latlon=no"
        f"&missing=M&trace=T&direct=no{types}"
    )


def ontleed_iem(tekst: str, stations) -> dict:
    """De komma-uitvoer van IEM naar {station: {datum: {...}}}.

    Losse functie omdat dit het enige stuk is dat offline te toetsen valt; het
    verzoek eromheen niet. Zie bot/test_waarneming.py."""
    uit: dict = {}
    gezocht = set(stations)
    for regel in tekst.splitlines():
        if not regel or regel.startswith("#"):
            continue
        delen = regel.split(",")
        if len(delen) < 3:
            continue
        st = delen[0].strip()
        if st not in gezocht:
            continue
        try:
            t = float(delen[2])
        except ValueError:
            continue                       # "M" voor ontbrekend, "T" voor spoor
        stamp = delen[1].strip()
        dag = stamp[:10]
        try:
            uur = int(stamp[11:13])
            minuut = int(stamp[14:16])
        except (ValueError, IndexError):
            uur, minuut = 0, 0
        e = uit.setdefault(st, {}).setdefault(
            dag, {"maxf": None, "minf": None, "n": 0, "laatste_uur": 0.0})
        e["n"] += 1
        e["laatste_uur"] = max(e["laatste_uur"], uur + minuut / 60.0)
        if e["maxf"] is None or t > e["maxf"]:
            e["maxf"] = t
        if e["minf"] is None or t < e["minf"]:
            e["minf"] = t
    return uit


def haal_stations(stations, tznaam: str, d1, d2, pauze: float = 0.5,
                  soorten=("3",), tot=None) -> dict:
    """Eén verzoek per bundel stations in plaats van één per station.

    IEM accepteert meerdere `station=`-parameters. Blijft er een station leeg,
    dan volgt er alsnog een los verzoek voor dat station: een bundel die
    stilletjes half terugkomt zou anders steden zonder ondergrens laten, en dat
    is precies het geval waarin je denkt gedekt te zijn en het niet bent.

    `tot` is een monotone deadline (time.monotonic). Is die verstreken, dan
    stopt het ophalen en komt terug wat er tot dan toe binnen was. Zonder die
    grens vermenigvuldigde één trage bron zich: een bundel die helemaal faalt
    zet elk station erin op de losse lijst, en die doen hun herkansingen nog
    eens over."""
    def op_tijd():
        return tot is None or time.monotonic() < tot

    def haal_een(deel):
        for poging in range(IEM_POGINGEN):
            try:
                return weer._get(_iem_url(deel, tznaam, d1, d2, soorten),
                                 timeout=IEM_TIMEOUT)
            except Exception:                      # noqa: BLE001
                if poging + 1 < IEM_POGINGEN and op_tijd():
                    time.sleep(2 + poging * 3)
                else:
                    return ""
        return ""

    uit: dict = {}
    lijst = sorted(set(stations))
    for i in range(0, len(lijst), IEM_BUNDEL):
        if not op_tijd():
            return uit
        deel = lijst[i:i + IEM_BUNDEL]
        uit.update(ontleed_iem(haal_een(deel), deel))
        time.sleep(pauze)
    for st in [s for s in lijst if s not in uit]:
        if not op_tijd():
            return uit
        uit.update(ontleed_iem(haal_een([st]), [st]))
        time.sleep(pauze)
    return uit


def haal_vandaag(steden: list, pauze: float = 0.5, budget: float = BUDGET_S) -> dict:
    """Wat er vandaag tot nu toe gemeten is, per stad, in de eenheid van die stad.

    Per stad: {"max": .., "min": .., "n": .., "laatste_uur": .., "uur": ..,
               "datum": .., "station": ..}
    `uur` is het lokale tijdstip van nú (waar de restfactor mee rekent),
    `laatste_uur` dat van de laatste melding. Lopen die ver uiteen, dan is het
    station uitgevallen en is `m` ouder dan hij lijkt.

    Steden zonder meting komen niet in de uitvoer voor. Dat is met opzet: de
    beller moet het verschil zien tussen "niets gemeten" en "nul graden".

    Omdat IEM per verzoek één tijdzone kent, gaan de steden per tijdzone in
    bundels. Dat is ook meteen de goede indeling, want de lokale kalenderdag
    verschilt per tijdzone. Het is wél een magere bundeling: 26 van de 48 steden
    zitten als enige in hun tijdzone, dus het zijn vooral losse verzoeken.
    Daarom hangt er een wandklokgrens omheen; zie BUDGET_S."""
    per_tz: dict = {}
    for s in steden:
        if s.get("bron") != "iem" or not s.get("station"):
            continue
        per_tz.setdefault(s["tz"], []).append(s)

    tot = time.monotonic() + budget if budget else None
    uit: dict = {}
    overgeslagen = 0
    for tznaam, groep in per_tz.items():
        if tot is not None and time.monotonic() >= tot:
            overgeslagen += len(groep)
            continue
        nu = datetime.now(ZoneInfo(tznaam))
        vandaag = nu.date()
        stations = [s["station"] for s in groep]
        # gisteren meepakken: rond middernacht staan er nog nauwelijks metingen
        # van vandaag, en de eindgrens van IEM is exclusief.
        rauw = haal_stations(stations, tznaam, vandaag - timedelta(days=1),
                             vandaag, pauze, tot=tot)
        for s in groep:
            e = (rauw.get(s["station"]) or {}).get(vandaag.isoformat())
            if not e or e["maxf"] is None:
                continue
            naar_c = s["eenheid"] != "F"
            uit[s["key"]] = {
                "max": weer.c_van_f(e["maxf"]) if naar_c else e["maxf"],
                "min": weer.c_van_f(e["minf"]) if naar_c else e["minf"],
                "n": e["n"],
                "laatste_uur": round(e["laatste_uur"], 2),
                "uur": round(nu.hour + nu.minute / 60.0, 2),
                "datum": vandaag.isoformat(),
                "station": s["station"],
            }
        verfijn_vandaag(groep, uit, vandaag, tot=tot)
    if overgeslagen:
        # Hoort hardop: een stad zonder ondergrens rekent onvoorwaardelijk door
        # en dat is aan de uitvoer niet te zien.
        print(f"  waarneming: tijdbudget van {budget:.0f}s op, "
              f"{overgeslagen} steden zonder ondergrens deze ronde")
    return uit


def verfijn_vandaag(steden: list, uit: dict, vandaag, tot=None) -> None:
    """De ondergrens bijstellen met een fijnmaziger reeks, waar die er is.

    Hier telt de verfijning harder dan bij de dagelijkse controle. `m` kapt de
    kansen af, dus een `m` die twee tienden te laag staat laat een vak open dat
    de dag al voorbij is gelopen. De bewaking blijft dezelfde: alleen omhoog voor
    het maximum, alleen omlaag voor het minimum, en binnen de marge. Zie
    bot/fijnmeting.py.

    Valt de bron om, dan blijft de METAR-waarde staan. Dat is de veilige kant:
    een te lage ondergrens kapt te weinig af, een te hoge zou een vak wegstrepen
    dat nog kon vallen."""
    doel = [s for s in steden if s.get("fijn") and s["key"] in uit]
    if not doel:
        return
    import fijnmeting
    for s in doel:
        if tot is not None and time.monotonic() >= tot:
            return          # het budget is op; de METAR-waarde blijft staan
        w = uit[s["key"]]
        for soort in ("max", "min"):
            los = {vandaag.isoformat(): w[soort]}
            try:
                if fijnmeting.verfijn(s, los, [vandaag], soort):
                    w[soort] = round(los[vandaag.isoformat()], 2)
                    w["fijn"] = s["fijn"]
            except Exception as ex:                # noqa: BLE001
                print(f"  {s['key']}: fijne reeks {s['fijn']} mislukt ({ex}); "
                      "de METAR-ondergrens blijft staan")
                break


def voor_stad(waarnemingen: dict, key: str, doel_datum: str, lead: int,
              soort: str) -> dict:
    """De waarneming die bij deze reeks hoort, of niets.

    Alleen lead 0 telt: voor morgen is er nog niets gemeten. En de datum moet
    kloppen — draait de run over lokale middernacht heen, dan gaat `lead` naar 0
    terwijl de meting nog van gisteren is.

    De restfactor rekent met `laatste_uur` en niet met de klok. Dat is het
    moment waarop we voor het laatst iets wisten, en precies waar de onzekerheid
    over het restant vanaf loopt. Bij een station dat normaal doorgeeft scheelt
    dat een half uur en niets wezenlijks; valt het station om twee uur 's middags
    uit, dan blijft de spreiding staan op wat hij om twee uur was in plaats van
    dicht te knijpen op een dag die we niet gezien hebben. De ondergrens `m`
    blijft in dat geval gewoon gelden — die is gemeten, alleen ouder."""
    if lead != 0:
        return None
    w = waarnemingen.get(key)
    if not w or w.get("datum") != doel_datum:
        return None
    m = w.get("max") if soort == "max" else w.get("min")
    if m is None:
        return None
    return {"m": m, "uur": w["laatste_uur"], "soort": soort, "n": w["n"],
            "klok": w["uur"], "laatste_uur": w["laatste_uur"],
            "station": w["station"]}


# ── Overzicht op de terminal ──────────────────────────────────────────────────

def toon_curve() -> None:
    print("\n  Restfactor per lokaal uur "
          f"(demping {DEMPING}, ruw uit de marktentropie)\n")
    print("   uur |  max ruw   max gedempt |  min ruw   min gedempt")
    for u in range(24):
        rm, rn = W_REST_MAX[u], W_REST_MIN[u]
        print(f"    {u:2d} |   {rm:.2f}        {restfactor(u, 'max'):.2f}     "
              f"|   {rn:.2f}        {restfactor(u, 'min'):.2f}")
    print()


def main(argv: list) -> int:
    if "--toon-curve" in argv:
        toon_curve()
        return 0
    steden = weer.STEDEN
    for i, a in enumerate(argv):
        if a == "--steden" and i + 1 < len(argv):
            keys = {s.strip().upper() for s in argv[i + 1].split(",") if s.strip()}
            steden = [s for s in weer.STEDEN if s["key"] in keys]
    uit = haal_vandaag(steden)
    if not uit:
        print("Niets gemeten. Dat kan het station zijn, of het net na "
              "middernacht zijn in elke tijdzone die je opvroeg.")
        return 1
    print(f"\n  Vandaag tot nu toe, {len(uit)} van de {len(steden)} steden\n")
    print("  stad  station   max     min    n   laatste   nu     w(max)")
    for key in sorted(uit):
        w = uit[key]
        eh = "°F" if any(s["key"] == key and s["eenheid"] == "F"
                         for s in steden) else "°C"
        oud = "  (oud)" if w["uur"] - w["laatste_uur"] > 2.5 else ""
        print(f"  {key:5s} {w['station']:6s} {w['max']:6.1f}{eh} "
              f"{w['min']:6.1f}  {w['n']:3d}   {w['laatste_uur']:5.2f}  "
              f"{w['uur']:5.2f}   {restfactor(w['uur'], 'max'):.2f}{oud}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
