#!/usr/bin/env python3
"""Zelftest voor de portefeuillebewaking.

  python3 bot/test_portfolio.py

Controleert elf dingen:

  slug      De slug van een markt valt terug uiteen in stad, doeldag, reeks en
            vak, precies andersom dan slug_van in signalen.py hem opbouwt.
  afstand   De afstand tot de vakrand en de vakbreedte, ook bij een open einde
            en op een °C markt waar een vak een hele graad breed is.
  netto     YES en NO op hetzelfde vak vallen tegen elkaar weg, restjes onder
            een half aandeel tellen niet mee, en een positie die niet te
            koppelen is verdwijnt niet maar belandt in unmapped.
  stoplicht Elke tak van de kleurregels, met een verzonnen setje posities:
            in het vak, binnen een half vak, lage winkans, tussen een half en
            een heel vak, kansstijging boven 15pp, en de rest groen. Plus de
            uitzondering voor een YES waarvan de verwachting in het vak ligt.
  vak       De zes vormen van het vaksuffix, dat het suffix wint van de
            vraagtekst van de markt, en dat een positie met een instapregel
            ook echt gevulde deltavelden krijgt. Die laatste zou de fout
            gevangen hebben waarbij de koppeling over het etiket liep en
            daardoor nooit iets vond.
  beslist   Een markt die al afgerekend heeft valt buiten het stoplicht. Zonder
            die tak kreeg een verloren positie groen mee plus een edge van
            tientallen procentpunten, omdat het model de uitslag niet kent.
  herkans   De ensemblefetch krijgt herkansingen, want één hapering kostte
            anders het hele modelbeeld van een stad. Blijft het misgaan, dan
            blijft de positie staan met unknown en de reden erbij.
  markt     Dicht op sluiting wordt een groot verschil met de markt gemarkeerd,
            want daar is de markt gemeten nauwkeuriger dan het model. Het
            stoplicht blijft daarbij ongemoeid: dat staat in graden.
  piek      Het uur van de dagpiek uit de uurcurve, spiegel van piekenUit in
            index.html, en de klok die daaruit volgt. Uren tot sluiting telde
            door tot middernacht terwijl de uitslag er ligt zodra het
            dagmaximum gevallen is.
  reeks     De kop en de regel van portfolio_history.csv zijn even breed, en
            het bestand op schijf ook. Een kolom erbij zonder migratie schuift
            alle waarden een plek op zonder dat er iets omvalt.
  uitvoer   De hele keten op datzelfde setje: de JSON heeft de afgesproken
            velden, is op kleur en uren tot sluiting gesorteerd, en een positie
            zonder instapregel houdt lege deltavelden met entry_known false.

Alles draait offline: het modelbeeld wordt hier verzonnen in plaats van bij
Open-Meteo opgehaald, zodat de test geen netwerk nodig heeft.
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import portfolio as P   # noqa: E402
import weer             # noqa: E402


def _dag(key: str, over: int = 1) -> str:
    """Een doeldag die zeker nog niet voorbij is, in de tijdzone van de stad."""
    tz = ZoneInfo(weer.STAD_OP_KEY[key]["tz"])
    return (datetime.now(tz).date() + timedelta(days=over)).isoformat()


def _slug(key: str, datum: str, staart: str) -> str:
    return P.S.slug_van(key, datum, "max") + "-" + staart


# ── Een verzonnen modelbeeld ──────────────────────────────────────────────────

class NepCache(P.ModelCache):
    """Dezelfde vorm als ModelCache, maar met een opgegeven verwachting per
    (stad, dag) in plaats van een ensemblefetch. De kans per vak komt uit een
    rechte normale verdeling met een vaste band, zodat de getallen in de test
    met de hand na te rekenen zijn."""

    def __init__(self, beelden: dict, band: float = 3.0):
        super().__init__({})
        self.beelden = beelden        # (key, datum) -> adj_mean, of None = fout
        self.band = band

    def piek(self, key, datum):
        """Geen uurcurve, zonder het netwerk op te gaan. Zonder deze override
        valt de test terug op de echte fetch en wacht hij per stad drie
        pogingen met pauzes af — een test hoort nooit aan een API te hangen."""
        return None

    def beeld(self, key, datum, soort):
        mu = self.beelden.get((key, datum))
        if mu is None:
            return None, "ensemble mislukt: verzonnen storing"
        eenheid = P.eenheid_van(key)
        eigen = {"verwachting": mu, "p10": mu - self.band, "p90": mu + self.band}
        return {"ensemble_mean": mu - 0.4, "adj_mean": mu, "eigen": eigen,
                "app_eenheid": eenheid, "markt_eenheid": eenheid, "lead": 1}, None


# ── 1. slug ───────────────────────────────────────────────────────────────────

def test_slug() -> bool:
    goed = True
    proeven = [
        ("highest-temperature-in-amsterdam-on-august-9-2026",
         {"key": "AMS", "datum": "2026-08-09", "soort": "max", "suffix": ""}),
        ("highest-temperature-in-nyc-on-august-9-2026-84-85f",
         {"key": "NYC", "datum": "2026-08-09", "soort": "max", "suffix": "84-85f"}),
        ("lowest-temperature-in-los-angeles-on-december-31-2026",
         {"key": "LAX", "datum": "2026-12-31", "soort": "min", "suffix": ""}),
        ("highest-temperature-in-kuala-lumpur-on-march-1-2027-33corhigher",
         {"key": "KUL", "datum": "2027-03-01", "soort": "max", "suffix": "33corhigher"}),
        ("will-it-rain-in-amsterdam-tomorrow", None),
        ("highest-temperature-in-atlantis-on-august-9-2026", None),
    ]
    for slug, verwacht in proeven:
        uit = P.uit_slug(slug)
        if uit != verwacht:
            print(f"  slug      MISLUKT: {slug} -> {uit}, verwacht {verwacht}")
            goed = False

    # heen en terug over alle steden die polymarkt.js kent
    for key in P.S.SLUG:
        s = P.S.slug_van(key, "2026-08-09", "max")
        if (P.uit_slug(s) or {}).get("key") != key:
            print(f"  slug      MISLUKT: {key} overleeft heen en terug niet ({s})")
            goed = False
    if goed:
        print(f"  slug      ok: {len(proeven)} proeven en {len(P.S.SLUG)} steden heen en terug")
    return goed


# ── 2. afstand en vakbreedte ──────────────────────────────────────────────────

def test_afstand() -> bool:
    goed = True
    # het vak loopt van lo-0,5 tot hi+0,5, want de markt rekent op hele graden
    proeven = [
        # (mu, lo, hi, verwachte afstand)
        (20.0, 20, 20, 0.0),        # midden in een °C vak
        (20.4, 20, 20, 0.0),        # nog net binnen de halve-graadrand
        (21.0, 20, 20, 0.5),        # een halve graad erbuiten
        (18.2, 20, 20, 1.3),        # eronder
        (85.0, 84, 85, 0.0),        # °F vak van twee graden breed
        (87.0, 84, 85, 1.5),
        (24.0, 29, None, 4.5),      # open bovenkant: alleen de ondergrens telt
        (35.0, 29, None, 0.0),      # ver boven een open bovenkant: erin
        (12.0, None, 19, 0.0),      # open onderkant: erin
        (21.0, None, 19, 1.5),
    ]
    for mu, lo, hi, verwacht in proeven:
        uit = P.afstand_tot_vak(mu, lo, hi)
        if abs(uit - verwacht) > 1e-9:
            print(f"  afstand   MISLUKT: mu={mu} vak=({lo},{hi}) -> {uit}, verwacht {verwacht}")
            goed = False

    breedtes = [
        ((20, 20, "°C"), 1.0),        # °C markt: een vak is een graad
        ((84, 85, "°F"), 2.0),        # °F markt: twee graden
        ((29, None, "°C"), 1.0),      # open einde: standaardbreedte van de markt
        ((None, 81, "°F"), 2.0),
    ]
    for (lo, hi, eenheid), verwacht in breedtes:
        uit = P.vakbreedte(lo, hi, eenheid)
        if abs(uit - verwacht) > 1e-9:
            print(f"  afstand   MISLUKT: breedte ({lo},{hi},{eenheid}) -> {uit}, "
                  f"verwacht {verwacht}")
            goed = False

    # het teken van delta_mean: positief is naar het vak toe
    tekens = [
        # (instap, nu, lo, hi, verwacht)
        (18.0, 19.2, 20, 20, +1.2),   # opgeschoven richting een vak erboven
        (22.0, 21.0, 20, 20, +1.0),   # gedaald richting een vak eronder
        (19.0, 18.0, 20, 20, -1.0),   # verder weg gezakt
        (20.0, 20.3, 20, 20, +0.3),   # stond al in het vak: niet geruststellend
    ]
    for instap, nu, lo, hi, verwacht in tekens:
        uit = P.verschuiving(instap, nu, lo, hi)
        if abs(uit - verwacht) > 1e-9:
            print(f"  afstand   MISLUKT: verschuiving {instap}->{nu} vak=({lo},{hi}) "
                  f"-> {uit}, verwacht {verwacht}")
            goed = False
    if goed:
        print(f"  afstand   ok: {len(proeven)} afstanden, {len(breedtes)} breedtes, "
              f"{len(tekens)} tekens")
    return goed


# ── 3. netteren, restjes en niet te koppelen regels ───────────────────────────

def test_netto() -> bool:
    goed = True
    dag = _dag("AMS")
    ruw = [
        # YES 10 en NO 4 op hetzelfde vak: netto YES 6
        {"size": 10, "avgPrice": 0.40, "curPrice": 0.35, "outcome": "Yes",
         "slug": _slug("AMS", dag, "20c"), "title": "20°C"},
        {"size": 4, "avgPrice": 0.55, "curPrice": 0.60, "outcome": "No",
         "slug": _slug("AMS", dag, "20c"), "title": "20°C"},
        # een afgerond restje
        {"size": 0.3, "avgPrice": 0.5, "curPrice": 0.5, "outcome": "Yes",
         "slug": _slug("AMS", dag, "21c"), "title": "21°C"},
        # niet te koppelen: de slug is geen temperatuurmarkt
        {"size": 5, "avgPrice": 0.5, "curPrice": 0.5, "outcome": "Yes",
         "slug": "will-the-fed-cut-in-september", "title": "Fed cut"},
        # niet te koppelen: geen outcome
        {"size": 5, "avgPrice": 0.5, "curPrice": 0.5,
         "slug": _slug("AMS", dag, "22c"), "title": "22°C"},
        # doeldag al voorbij: telt niet meer mee, en is ook geen unmapped
        {"size": 8, "avgPrice": 0.5, "curPrice": 0.5, "outcome": "No",
         "slug": _slug("AMS", "2020-01-02", "5c"), "title": "5°C"},
    ]
    cache = NepCache({("AMS", dag): 19.0})
    uit = P.bouw(ruw, {}, {}, "0xtest", cache)

    posities = uit["positions"]
    if len(posities) != 1:
        print(f"  netto     MISLUKT: {len(posities)} posities, verwacht 1")
        goed = False
    elif posities[0]["direction"] != "YES" or abs(posities[0]["size"] - 6) > 1e-9:
        print(f"  netto     MISLUKT: netto {posities[0]['direction']} "
              f"{posities[0]['size']}, verwacht YES 6")
        goed = False
    if len(uit["unmapped"]) != 2:
        print(f"  netto     MISLUKT: {len(uit['unmapped'])} unmapped, verwacht 2")
        for u in uit["unmapped"]:
            print(f"            {u['reason']}")
        goed = False
    if goed:
        print("  netto     ok: netto YES 6, restje weg, dag voorbij weg, 2 in unmapped")
    return goed


# ── 4. het stoplicht, elke tak ────────────────────────────────────────────────

def test_stoplicht() -> bool:
    """Rechtstreeks op de kleurregels, zodat elke tak los aanwijsbaar is."""
    goed = True
    proeven = [
        # (d, b, winkans, delta_prob, in het vak, verwachte kleur, kernwoord)
        (0.0, 1.0, 0.9, None, True, "red", "in het vak"),
        (0.3, 1.0, 0.9, None, False, "red", "half vak"),
        (0.4, 1.0, 0.9, None, False, "red", "half vak"),
        (3.0, 1.0, 0.42, None, False, "red", "modelwinkans"),
        (0.7, 1.0, 0.9, None, False, "amber", "half en een heel"),
        (1.0, 1.0, 0.9, None, False, "amber", "half en een heel"),
        (0.8, 2.0, 0.9, None, False, "red", "half vak"),      # °F: b is twee
        (1.5, 2.0, 0.9, None, False, "amber", "half en een heel"),
        (3.0, 1.0, 0.9, 22.0, False, "amber", "gestegen"),
        (3.0, 1.0, 0.9, 8.0, False, "green", "meer dan een heel vak"),
        (3.0, 1.0, 0.9, None, False, "green", "meer dan een heel vak"),
    ]
    for d, b, win, dp, erin, kleur, woord in proeven:
        licht, reden = P.stoplicht(d, b, win, dp, erin)
        if licht != kleur or woord not in reden:
            print(f"  stoplicht MISLUKT: d={d} b={b} win={win} dprob={dp} erin={erin} "
                  f"-> {licht} ({reden}), verwacht {kleur} met {woord!r}")
            goed = False
    # de volgorde telt: de eerste voorwaarde die klopt wint
    licht, _ = P.stoplicht(0.2, 1.0, 0.42, 40.0, False)
    if licht != "red":
        print(f"  stoplicht MISLUKT: samenval van rode regels -> {licht}")
        goed = False

    # Een YES waarvan de verwachting in het vak ligt is de winnende stand, geen
    # faalgeval: de twee afstandsregels slaan daar over. Zonder die uitzondering
    # kleurt een breed of open vak dat gewoon goed staat rood.
    ja_proeven = [
        (0.0, 1.0, 0.91, None, True, "green"),    # YES, erin, hoge winkans
        (0.0, 1.0, 0.31, None, True, "red"),      # YES, erin, maar de kans is laag
        (0.0, 1.0, 0.91, None, True, "red", False),   # dezelfde stand met NO
    ]
    for proef in ja_proeven:
        d, b, win, dp, erin, kleur = proef[:6]
        is_ja = proef[6] if len(proef) > 6 else True
        licht, reden = P.stoplicht(d, b, win, dp, erin, is_ja)
        if licht != kleur:
            print(f"  stoplicht MISLUKT: {'YES' if is_ja else 'NO'} in het vak, "
                  f"win={win} -> {licht} ({reden}), verwacht {kleur}")
            goed = False
    if goed:
        print(f"  stoplicht ok: {len(proeven)} takken, de volgorde van de regels, "
              f"en {len(ja_proeven)} keer YES in het vak")
    return goed


# ── 5. de hele keten ──────────────────────────────────────────────────────────

def test_uitvoer() -> bool:
    """Een verzonnen portefeuille die elke kleur raakt, met een open einde, een
    °C markt, een °F markt, een stad zonder biaskalibratie, een positie zonder
    instapregel en een positie waarvan de ensemblefetch omvalt."""
    goed = True
    d_ams, d_nyc = _dag("AMS", 1), _dag("NYC", 2)
    d_tyo, d_lon = _dag("TYO", 1), _dag("LON", 3)
    d_sin = _dag("SIN", 1)

    ruw = [
        # ROOD: NO op 20 °C en de verwachting kruipt er precies naartoe.
        # Dit is het faalgeval waar de hele module voor bestaat.
        {"size": 40, "avgPrice": 0.82, "curPrice": 0.79, "outcome": "No",
         "slug": _slug("AMS", d_ams, "20c"), "title": "20°C",
         "conditionId": "0xams20"},
        # ORANJE: NO op een °F vak, verwachting anderhalve graad van de rand.
        # Op een °F markt is b twee graden, dus anderhalf ligt tussen een half
        # en een heel vak; op een °C markt zou datzelfde getal groen zijn.
        {"size": 25, "avgPrice": 0.66, "curPrice": 0.72, "outcome": "No",
         "slug": _slug("NYC", d_nyc, "84-85f"), "title": "84-85°F",
         "conditionId": "0xnyc84"},
        # GROEN met een open einde, en zonder instapregel in het signalenlog.
        {"size": 12, "avgPrice": 0.06, "curPrice": 0.05, "outcome": "No",
         "slug": _slug("LON", d_lon, "32corhigher"), "title": "32°C or higher",
         "conditionId": "0xlon32"},
        # TYO: hoge onzekerheid, moet expliciet gemarkeerd staan.
        {"size": 30, "avgPrice": 0.45, "curPrice": 0.48, "outcome": "Yes",
         "slug": _slug("TYO", d_tyo, "33c"), "title": "33°C",
         "conditionId": "0xtyo33"},
        # SIN: de ensemblefetch valt om; de positie hoort te blijven staan.
        {"size": 9, "avgPrice": 0.20, "curPrice": 0.22, "outcome": "Yes",
         "slug": _slug("SIN", d_sin, "33c"), "title": "33°C",
         "conditionId": "0xsin33"},
    ]

    cache = NepCache({
        ("AMS", d_ams): 19.9,     # in het vak 20 -> rood
        ("NYC", d_nyc): 87.0,     # 1,5 °F van de rand van 84-85, b=2 -> oranje
        ("LON", d_lon): 24.0,     # 7,5 ° van de rand van 32 of hoger -> groen
        ("TYO", d_tyo): 33.0,     # in het vak, YES -> rood, met de vlag erbij
        ("SIN", d_sin): None,     # fetch mislukt -> unknown
    })
    # De sleutel loopt over de vakgrenzen, niet over het etiket: het etiket
    # luidt aan de kant van de data-API anders dan in het signalenlog.
    instap = {
        (d_ams, "AMS", "max", 20, 20):
            {"gelogd": "x", "adj_mean": 18.1, "model_prob": 0.11, "label": "20°C"},
        (d_nyc, "NYC", "max", 84, 85):
            {"gelogd": "x", "adj_mean": 84.5, "model_prob": 0.28, "label": "84-85°F"},
        (d_tyo, "TYO", "max", 33, 33):
            {"gelogd": "x", "adj_mean": 32.6, "model_prob": 0.24, "label": "33°C"},
        # LON en SIN staan er bewust niet in
    }
    uit = P.bouw(ruw, {}, instap, "0xtest", cache)
    op_stad = {r["city"]: r for r in uit["positions"]}

    verwacht = {"AMS": "red", "NYC": "amber", "LON": "green",
                "TYO": "red", "SIN": "unknown"}
    for key, kleur in verwacht.items():
        r = op_stad.get(key)
        if not r:
            print(f"  uitvoer   MISLUKT: {key} ontbreekt in de uitvoer")
            goed = False
        elif r["light"] != kleur:
            print(f"  uitvoer   MISLUKT: {key} -> {r['light']} ({r['reason']}), "
                  f"verwacht {kleur}")
            goed = False

    # elke regel heeft een reden
    for r in uit["positions"]:
        if not r["reason"]:
            print(f"  uitvoer   MISLUKT: {r['city']} heeft geen reason")
            goed = False

    # alle velden uit de afspraak staan erin
    velden = ["city", "date", "bracket", "direction", "size", "avg_price",
              "current_bid", "adj_mean_now", "adj_mean_entry", "city_bias_used",
              "model_prob_now", "model_prob_entry", "model_win_prob", "d", "b",
              "delta_prob", "delta_mean", "fair_value", "edge_now",
              "hours_to_close", "light", "reason", "entry_known",
              "high_uncertainty"]
    ontbreekt = [v for v in velden if any(v not in r for r in uit["positions"])]
    if ontbreekt:
        print(f"  uitvoer   MISLUKT: velden ontbreken: {ontbreekt}")
        goed = False

    # de NO op 20 °C: winkans is 1 min de vakkans, en de verwachting is sinds
    # de instap naar het vak toe geschoven, dus delta_mean is positief
    ams = op_stad.get("AMS") or {}
    if ams.get("model_win_prob") is not None and ams.get("model_prob_now") is not None:
        if abs(ams["model_win_prob"] - (1 - ams["model_prob_now"])) > 1e-4:
            print("  uitvoer   MISLUKT: NO-winkans is niet 1 min de vakkans")
            goed = False
    if not ams.get("delta_mean", 0) > 0:
        print(f"  uitvoer   MISLUKT: AMS delta_mean {ams.get('delta_mean')}, "
              f"verwacht positief (naar het vak toe)")
        goed = False
    if ams.get("fair_value") != ams.get("model_win_prob"):
        print("  uitvoer   MISLUKT: fair_value wijkt af van de modelwinkans")
        goed = False
    if ams.get("edge_now") is None:
        print("  uitvoer   MISLUKT: AMS heeft geen edge_now")
        goed = False

    # zonder instapregel blijven de deltavelden leeg
    lon = op_stad.get("LON") or {}
    if lon.get("entry_known") is not False or lon.get("delta_prob") is not None \
            or lon.get("delta_mean") is not None or lon.get("model_prob_entry") is not None:
        print(f"  uitvoer   MISLUKT: LON heeft geen instapregel maar wel deltavelden: "
              f"{lon.get('entry_known')}, {lon.get('delta_prob')}, {lon.get('delta_mean')}")
        goed = False

    # de open-eindepositie heeft een standaardbreedte
    if lon.get("b") != 1.0:
        print(f"  uitvoer   MISLUKT: LON open einde heeft b={lon.get('b')}, verwacht 1.0")
        goed = False
    if (op_stad.get("NYC") or {}).get("b") != 2.0:
        print(f"  uitvoer   MISLUKT: NYC °F vak heeft b="
              f"{(op_stad.get('NYC') or {}).get('b')}, verwacht 2.0")
        goed = False

    # hoge onzekerheid alleen voor TYO en SIN
    for key, r in op_stad.items():
        if r["high_uncertainty"] != (key in ("TYO", "SIN")):
            print(f"  uitvoer   MISLUKT: high_uncertainty bij {key} is "
                  f"{r['high_uncertainty']}")
            goed = False

    # de gevallen positie staat er nog, met een reden
    sin = op_stad.get("SIN") or {}
    if sin.get("light") != "unknown" or "mislukt" not in sin.get("reason", ""):
        print(f"  uitvoer   MISLUKT: SIN {sin.get('light')} / {sin.get('reason')}")
        goed = False

    # sortering: eerst op kleur, daarbinnen op uren tot sluiting oplopend
    rang = [P.VOLGORDE.get(r["light"], 9) for r in uit["positions"]]
    if rang != sorted(rang):
        print(f"  uitvoer   MISLUKT: niet op kleur gesorteerd: "
              f"{[r['light'] for r in uit['positions']]}")
        goed = False
    for a, b in zip(uit["positions"], uit["positions"][1:]):
        if a["light"] == b["light"] and a["hours_to_close"] > b["hours_to_close"]:
            print("  uitvoer   MISLUKT: binnen een kleur niet op uren gesorteerd")
            goed = False

    # uren tot sluiting: middernacht na de doeldag, dus altijd positief en
    # binnen het aantal dagen dat we vooruit kijken
    for r in uit["positions"]:
        u = r["hours_to_close"]
        if u is None or not 0 < u < 24 * 5:
            print(f"  uitvoer   MISLUKT: {r['city']} uren tot sluiting {u}")
            goed = False

    s = uit["summary"]
    if (s["n_positions"], s["n_red"], s["n_amber"], s["n_green"]) != (5, 2, 1, 1):
        print(f"  uitvoer   MISLUKT: samenvatting {s}")
        goed = False

    # en de JSON is echt serialiseerbaar
    try:
        json.dumps(uit)
    except (TypeError, ValueError) as ex:
        print(f"  uitvoer   MISLUKT: niet serialiseerbaar ({ex})")
        goed = False

    if goed:
        print(f"  uitvoer   ok: {s['n_positions']} posities, {s['n_red']} rood, "
              f"{s['n_amber']} oranje, {s['n_green']} groen, "
              f"{s['n_unknown']} onbekend")
    return goed


# ── 6. het vak uit het slugsuffix, en de koppeling met het signalenlog ────────

def test_vak() -> bool:
    """De zes vormen die het vaksuffix aanneemt, en de koppeling aan een
    instapregel. Die koppeling liep eerder over het etiket; de data-API geeft
    als titel de hele vraag ("… be 30°C on August 9?") en het signalenlog de
    vaknaam ("30°C"), dus vond hij nooit iets — zonder dat er een fout viel.
    Sinds die koppeling over de vakgrenzen loopt, vangt deze test hem."""
    goed = True
    suffixen = [
        ("23c",          {"lo": 23,   "hi": 23,   "eenheid": "°C"}, "23°C"),
        ("82-83f",       {"lo": 82,   "hi": 83,   "eenheid": "°F"}, "82-83°F"),
        ("22corbelow",   {"lo": None, "hi": 22,   "eenheid": "°C"}, "22°C or below"),
        ("32corhigher",  {"lo": 32,   "hi": None, "eenheid": "°C"}, "32°C or higher"),
        ("81forbelow",   {"lo": None, "hi": 81,   "eenheid": "°F"}, "81°F or below"),
        ("100forhigher", {"lo": 100,  "hi": None, "eenheid": "°F"}, "100°F or higher"),
        ("onzin",        None, None),
        ("",             None, None),
    ]
    for suf, verwacht, label in suffixen:
        uit = P.vak_uit_suffix(suf)
        if uit != verwacht:
            print(f"  vak       MISLUKT: {suf!r} -> {uit}, verwacht {verwacht}")
            goed = False
        elif verwacht and P.vaklabel(uit["lo"], uit["hi"], uit["eenheid"]) != label:
            print(f"  vak       MISLUKT: etiket van {suf!r} -> "
                  f"{P.vaklabel(uit['lo'], uit['hi'], uit['eenheid'])!r}, verwacht {label!r}")
            goed = False

    # De titel van de data-API is de hele vraag, met de dag erin. Het vak mag
    # daar niet uit gelezen worden; het suffix moet winnen.
    dag = _dag("LON")
    ruw = [{"size": 10, "avgPrice": 0.6, "curPrice": 0.58, "outcome": "No",
            "slug": _slug("LON", dag, "30c"),
            "title": "Will the highest temperature in London be 30°C on August 9?"}]
    instap = {(dag, "LON", "max", 30, 30):
              {"gelogd": "x", "adj_mean": 27.4, "model_prob": 0.12, "label": "30°C"}}
    uit = P.bouw(ruw, {}, instap, "0xtest", NepCache({("LON", dag): 29.0}))
    r = (uit["positions"] or [{}])[0]
    if r.get("bracket_lo") != 30 or r.get("bracket_hi") != 30:
        print(f"  vak       MISLUKT: grenzen uit de vraagtekst gelezen: "
              f"{r.get('bracket_lo')}-{r.get('bracket_hi')}, verwacht 30-30")
        goed = False
    if r.get("bracket") != "30°C":
        print(f"  vak       MISLUKT: etiket {r.get('bracket')!r}, verwacht '30°C'")
        goed = False
    if not r.get("entry_known"):
        print("  vak       MISLUKT: instapregel niet gevonden, entry_known is false")
        goed = False
    if r.get("delta_mean") is None or r.get("delta_prob") is None:
        print(f"  vak       MISLUKT: deltavelden leeg terwijl er een instapregel is: "
              f"delta_mean={r.get('delta_mean')} delta_prob={r.get('delta_prob')}")
        goed = False
    if r.get("title_raw") != ruw[0]["title"]:
        print("  vak       MISLUKT: de ruwe vraagtekst is niet bewaard")
        goed = False

    # en de index uit het echte signalenlog gebruikt dezelfde sleutelvorm
    echt = P.instap_index()
    if echt:
        sleutel = next(iter(echt))
        if len(sleutel) != 5 or not isinstance(sleutel[0], str):
            print(f"  vak       MISLUKT: sleutelvorm van de index is {sleutel}")
            goed = False

    if goed:
        print(f"  vak       ok: {len(suffixen)} suffixen, suffix wint van de vraagtekst, "
              f"instap gekoppeld op de grenzen")
    return goed


# ── 7. afgerekende markten ───────────────────────────────────────────────────

def test_beslist() -> bool:
    """Polymarket rekent af zodra het dagmaximum binnen is, en de prijs schiet
    dan naar 0,0005 of 0,9995 terwijl de dag lokaal nog loopt. Het model kent
    die uitslag niet en blijft op de verwachting rekenen.

    Dit is de stand die op 8 augustus echt langskwam: NO op 36 °C in Busan, de
    markt had al op 36 afgerekend, en het model gaf de positie 98% winkans mee.
    Zonder deze tak stond een verloren positie groen in de tabel, met een edge
    van +98pp erbij."""
    goed = True
    dag = _dag("PUS", 0)
    ruw = [
        # verloren: de markt noteert het vak dat wij NO hadden op bijna 1,
        # dus onze NO is bijna niets meer waard
        {"size": 12.27, "avgPrice": 0.8146, "curPrice": 0.0005, "outcome": "No",
         "slug": _slug("PUS", dag, "36c"), "title": "36°C"},
        # gewonnen: hetzelfde, andere kant op
        {"size": 10, "avgPrice": 0.60, "curPrice": 0.9995, "outcome": "No",
         "slug": _slug("PUS", dag, "31c"), "title": "31°C"},
        # nog niet afgerekend, gewoon een prijs
        {"size": 10, "avgPrice": 0.60, "curPrice": 0.72, "outcome": "No",
         "slug": _slug("PUS", dag, "35c"), "title": "35°C"},
    ]
    cache = NepCache({("PUS", dag): 32.56})
    uit = P.bouw(ruw, {}, {}, "0xtest", cache)
    op_vak = {r["bracket"]: r for r in uit["positions"]}

    verwacht = {"36°C": "settled", "31°C": "settled", "35°C": "green"}
    for vak, kleur in verwacht.items():
        r = op_vak.get(vak)
        if not r:
            print(f"  beslist   MISLUKT: {vak} ontbreekt")
            goed = False
        elif r["light"] != kleur:
            print(f"  beslist   MISLUKT: {vak} -> {r['light']} ({r['reason']}), "
                  f"verwacht {kleur}")
            goed = False

    verloren = op_vak.get("36°C") or {}
    if not verloren.get("market_decided"):
        print("  beslist   MISLUKT: market_decided staat niet aan")
        goed = False
    if "verloren" not in verloren.get("reason", ""):
        print(f"  beslist   MISLUKT: reden zegt niet dat het verloren is: "
              f"{verloren.get('reason')}")
        goed = False
    if "gewonnen" not in (op_vak.get("31°C") or {}).get("reason", ""):
        print("  beslist   MISLUKT: de gewonnen kant wordt niet als gewonnen gemeld")
        goed = False
    if (op_vak.get("35°C") or {}).get("market_decided") is not False:
        print("  beslist   MISLUKT: een gewone prijs geldt als afgerekend")
        goed = False

    # afgerekende posities horen onderaan, niet tussen de levende in
    lichten = [r["light"] for r in uit["positions"]]
    if lichten[-2:] != ["settled", "settled"]:
        print(f"  beslist   MISLUKT: sortering {lichten}")
        goed = False
    if uit["summary"].get("n_settled") != 2:
        print(f"  beslist   MISLUKT: n_settled is {uit['summary'].get('n_settled')}")
        goed = False

    if goed:
        print("  beslist   ok: verloren en gewonnen apart van groen, onderaan, geteld")
    return goed


# ── 8. herkansingen op de ensemblefetch ──────────────────────────────────────

def test_herkansing() -> bool:
    """Eén hapering in de verbinding mag niet het hele modelbeeld van een stad
    kosten. Dat gebeurde in de eerste vijf runs twee keer, op twee verschillende
    steden, allebei met een TLS-handshake die niet rond kwam; elke positie daar
    stond die run zonder licht.

    Blijft het misgaan, dan blijft het unknown mét reden: een stad stilletjes
    laten verdwijnen is erger dan een gat dat zichzelf meldt."""
    goed = True
    echte_pauze, P.FETCH_PAUZE = P.FETCH_PAUZE, 0.0     # niet echt wachten
    echte_haal = P.logger.haal_leden
    try:
        # eerst twee keer stuk, dan goed: de derde poging moet tellen
        pogingen = {"n": 0}

        def hapert(stad, velden, timeout=60):
            pogingen["n"] += 1
            if pogingen["n"] < 3:
                raise OSError("_ssl.c:993: The handshake operation timed out")
            return echte_haal_nep(stad, velden)

        def echte_haal_nep(stad, velden):
            dag = _dag(stad["key"], 1)
            return {("max", dag, "ecmwf_ifs025"): [30.0, 30.4, 29.6]}

        P.logger.haal_leden = hapert
        cache = P.ModelCache({})
        beeld, fout = cache.beeld("AMS", _dag("AMS", 1), "max")
        if beeld is None:
            print(f"  herkans   MISLUKT: na twee haperingen nog geen beeld ({fout})")
            goed = False
        elif pogingen["n"] != 3:
            print(f"  herkans   MISLUKT: {pogingen['n']} pogingen, verwacht 3")
            goed = False

        # en blijft het stuk, dan unknown met de reden erbij
        P.logger.haal_leden = lambda *a, **k: (_ for _ in ()).throw(
            OSError("_ssl.c:993: The handshake operation timed out"))
        dag = _dag("SHA", 1)
        ruw = [{"size": 19.4, "avgPrice": 0.84, "curPrice": 0.81, "outcome": "No",
                "slug": _slug("SHA", dag, "28c"), "title": "28°C"}]
        # waarnemingen expliciet leeg: deze zelftest hoort offline te draaien,
        # en zonder dat argument gaat bouw de metingen van vandaag ophalen
        uit = P.bouw(ruw, {}, {}, "0xtest", waarnemingen={})
        r = (uit["positions"] or [{}])[0]
        if r.get("light") != "unknown":
            print(f"  herkans   MISLUKT: blijvende storing geeft {r.get('light')}")
            goed = False
        if "handshake" not in r.get("reason", "") or "pogingen" not in r.get("reason", ""):
            print(f"  herkans   MISLUKT: reden zegt niet wat er misging: {r.get('reason')}")
            goed = False
        if r.get("city") != "SHA" or r.get("size") != 19.4:
            print("  herkans   MISLUKT: de positie zelf is niet blijven staan")
            goed = False
    finally:
        P.logger.haal_leden = echte_haal
        P.FETCH_PAUZE = echte_pauze

    if goed:
        print(f"  herkans   ok: herstelt na twee haperingen, blijft anders unknown "
              f"met reden ({P.FETCH_POGINGEN} pogingen)")
    return goed


# ── 9. de markt is het oneens ────────────────────────────────────────────────

def test_markt_oneens() -> bool:
    """Dicht op sluiting is een groot verschil met de markt vaker het model dat
    ernaast zit dan een edge om te pakken. Gemeten op 167 afgerekende stad-dagen
    is de markt in de laatste twaalf uur 58% nauwkeuriger.

    De cijfers hieronder zijn die van Shanghai op 9 augustus: het model gaf de
    NO 84% terwijl de markt op 8% stond, en de markt kreeg gelijk. De kolom edge
    zette daar +76pp neer alsof het gratis geld was.

    Het aantal uur wordt hier rechtstreeks meegegeven en niet uit een doeldag
    afgeleid. Een eerdere versie gebruikte "vandaag in Shanghai", en of dat
    binnen het venster van twaalf uur valt hangt af van het tijdstip waarop de
    test draait: hij slaagde bij het schrijven en viel om zodra hij 's ochtends
    liep. Een test die van de klok afhangt bewaakt niets."""
    goed = True

    def proef(uren, win, bied, piek_bekend=True):
        rij = {"model_win_prob": win, "current_bid": bied, "fair_value": win,
               "edge_now": round((win - bied) * 100, 2),
               "market_disagrees": False, "market_note": ""}
        P.markeer_markt(rij, uren, piek_bekend=piek_bekend)
        return rij

    gevallen = [
        # (uren tot de piek, modelwinkans, bied, moet gemarkeerd, kernwoord)
        (2.0,   0.84, 0.08, True,  "twijfel aan het model"),  # Shanghai zelf
        (11.9,  0.84, 0.08, True,  "twijfel aan het model"),  # net binnen
        (12.1,  0.84, 0.08, False, ""),                       # net buiten
        (36.0,  0.84, 0.08, False, ""),                       # ruim buiten
        (2.0,   0.86, 0.72, False, ""),                       # laat, klein verschil
        (2.0,   0.86, 0.65, True,  "twijfel aan het model"),  # laat, net boven 20pp
        (2.0,   0.55, 0.90, True,  "te somber"),              # markt prijst hoger
        # De piek voorbij is het sterkste geval en niet het zwakste: daar zit de
        # markt 85% dichter bij de uitkomst. Op de oude klok viel dit buiten de
        # vlag omdat er een ondergrens van nul stond.
        (-1.0,  0.84, 0.08, True,  "piek is 1.0 uur geleden"),
        (-6.0,  0.84, 0.08, True,  "85%"),
    ]
    for uren, win, bied, moet, woord in gevallen:
        r = proef(uren, win, bied)
        if r["market_disagrees"] != moet:
            print(f"  markt     MISLUKT: {uren}u, {r['edge_now']:+.1f}pp -> "
                  f"gemarkeerd={r['market_disagrees']}, verwacht {moet}")
            goed = False
        elif moet and woord not in r["market_note"]:
            print(f"  markt     MISLUKT: reden mist {woord!r}: {r['market_note']}")
            goed = False

    # De terugval zonder piekuur telt naar de sluiting, en dat hoort de tekst
    # dan ook te zeggen: "tot de verwachte piek" schrijven terwijl er tijd tot
    # middernacht gemeten is, is een klok die liegt.
    r = proef(2.0, 0.84, 0.08, piek_bekend=False)
    if not r["market_disagrees"] or "tot sluiting" not in r["market_note"]:
        print(f"  markt     MISLUKT: terugval op de sluitingsklok markeert niet "
              f"of noemt de sluiting niet: {r['market_note']}")
        goed = False
    elif "verwachte piek" in r["market_note"] or "geleden verwacht" in r["market_note"]:
        print(f"  markt     MISLUKT: de terugvaltekst doet zich voor als "
              f"piekklok: {r['market_note']}")
        goed = False
    # Negatief is op die klok geen piek-geweest maar een al gesloten markt:
    # daarover valt niets meer te signaleren. De oude ondergrens van nul
    # hoort in de terugval te blijven bestaan.
    r = proef(-1.0, 0.84, 0.08, piek_bekend=False)
    if r["market_disagrees"]:
        print("  markt     MISLUKT: een al gesloten markt (negatieve "
              "sluitingsklok zonder piekuur) krijgt de vlag")
        goed = False

    # de vlag mag het stoplicht niet aanraken: dat blijft in graden
    dag = _dag("SHA", 1)
    ruw = [{"size": 38.3, "avgPrice": 0.70, "curPrice": 0.08, "outcome": "No",
            "slug": _slug("SHA", dag, "28c"), "title": "28°C"}]
    uit = P.bouw(ruw, {}, {}, "0xtest", NepCache({("SHA", dag): 29.22}))
    r = (uit["positions"] or [{}])[0]
    zonder = P.stoplicht(r.get("d"), r.get("b"), r.get("model_win_prob"),
                         r.get("delta_prob"), r.get("d") == 0, False)
    if r.get("light") != zonder[0]:
        print(f"  markt     MISLUKT: het stoplicht is verschoven naar {r.get('light')}")
        goed = False
    if "market_disagrees" not in r or "market_note" not in r:
        print("  markt     MISLUKT: de velden ontbreken in de uitvoer")
        goed = False
    if uit["summary"].get("n_market_disagrees") is None:
        print("  markt     MISLUKT: de teller ontbreekt in de samenvatting")
        goed = False

    if goed:
        print(f"  markt     ok: {len(gevallen)} gevallen rond {P.MARKT_VENSTER_UREN:.0f}u "
              f"en {P.MARKT_VERSCHIL_PP:.0f}pp, stoplicht blijft ongemoeid")
    return goed


# ── 10. het piektijdstip ─────────────────────────────────────────────────────

def test_piek() -> bool:
    """Het uur van de dagpiek uit de uurcurve, en de klok die daaruit volgt.

    Uren tot sluiting telde door tot middernacht, terwijl de uitslag er ligt
    zodra het dagmaximum gevallen is. Bij Busan rekende Polymarket af met nog
    bijna drie uur op de klok."""
    goed = True

    # twee modellen, twee dagen: piek op 15u en op 13u, met de modellen een uur
    # uit elkaar op de tweede dag
    tijden, a, b = [], [], []
    for dag, (piek_a, piek_b) in (("2026-08-11", (15, 15)), ("2026-08-12", (13, 14))):
        for u in range(24):
            tijden.append(f"{dag}T{u:02d}:00")
            a.append(20 - abs(u - piek_a))
            b.append(20 - abs(u - piek_b))
    j = {"hourly": {"time": tijden,
                    "temperature_2m_ecmwf_ifs025": a,
                    "temperature_2m_gfs_seamless": b}}
    p = P.pieken_uit(j)
    if (p.get("2026-08-11") or {}).get("uur") != 15:
        print(f"  piek      MISLUKT: dag 1 -> {p.get('2026-08-11')}, verwacht uur 15")
        goed = False
    tweede = p.get("2026-08-12") or {}
    if tweede.get("lo") != 13 or tweede.get("hi") != 14:
        print(f"  piek      MISLUKT: spreiding dag 2 -> {tweede}, verwacht 13-14")
        goed = False

    # een gat in het ene model mag de spreiding niet uit het andere model
    # laten putten: vals is per model uitgelijnd, niet per uur gecompacteerd.
    # Model a piekt op 15u maar mist 04u; model b piekt op 04u. Gecompacteerd
    # schoof b's 04u-waarde op de plek van a en werd b's eigen piekuur juist
    # overgeslagen: spreiding 3-4 in plaats van 4-15.
    tijden2, a2, b2 = [], [], []
    for u in range(24):
        tijden2.append(f"2026-08-14T{u:02d}:00")
        a2.append(None if u == 4 else 20 - abs(u - 15))
        b2.append(25 - 2 * abs(u - 4))
    j2 = {"hourly": {"time": tijden2,
                     "temperature_2m_ecmwf_ifs025": a2,
                     "temperature_2m_gfs_seamless": b2}}
    p2 = (P.pieken_uit(j2) or {}).get("2026-08-14") or {}
    if p2.get("lo") != 4 or p2.get("hi") != 15:
        print(f"  piek      MISLUKT: spreiding met een gat -> {p2}, verwacht 4-15")
        goed = False

    # een dag met te weinig uren telt niet mee, net als in de app
    kort = {"hourly": {"time": [f"2026-08-13T{u:02d}:00" for u in range(4)],
                       "temperature_2m_ecmwf_ifs025": [10, 11, 12, 11]}}
    if P.pieken_uit(kort):
        print("  piek      MISLUKT: een dag met vier uurwaarden telt mee")
        goed = False
    if P.pieken_uit({}) != {} or P.pieken_uit({"hourly": {}}) != {}:
        print("  piek      MISLUKT: lege invoer geeft geen lege uitkomst")
        goed = False

    # de klok: morgen om 15u ligt in de toekomst, gisteren om 15u erachter
    morgen = _dag("AMS", 1)
    u = P.uren_tot_piek("AMS", morgen, 15)
    if u is None or not 0 < u < 48:
        print(f"  piek      MISLUKT: uren tot piek morgen = {u}")
        goed = False
    gisteren = (date.fromisoformat(_dag("AMS", 0)) - timedelta(days=1)).isoformat()
    if (P.uren_tot_piek("AMS", gisteren, 15) or 0) >= 0:
        print("  piek      MISLUKT: een piek van gisteren telt niet als geweest")
        goed = False

    # en de hele keten: de piek komt in de rij terecht en stuurt de sortering
    class PiekCache(NepCache):
        def piek(self, key, datum):
            return {"uur": 15, "lo": 14, "hi": 16}

    dag = _dag("AMS", 1)
    ruw = [{"size": 10, "avgPrice": 0.6, "curPrice": 0.55, "outcome": "No",
            "slug": _slug("AMS", dag, "20c"), "title": "20°C"}]
    uit = P.bouw(ruw, {}, {}, "0xtest", PiekCache({("AMS", dag): 17.0}))
    r = (uit["positions"] or [{}])[0]
    if r.get("peak_hour") != 15 or r.get("peak_hour_spread") != [14, 16]:
        print(f"  piek      MISLUKT: piekvelden {r.get('peak_hour')}, "
              f"{r.get('peak_hour_spread')}")
        goed = False
    if r.get("hours_to_peak") is None or r.get("hours_to_close") is None:
        print("  piek      MISLUKT: een van beide klokken ontbreekt")
        goed = False
    elif not r["hours_to_peak"] < r["hours_to_close"]:
        print(f"  piek      MISLUKT: piek ({r['hours_to_peak']}) ligt niet voor "
              f"sluiting ({r['hours_to_close']})")
        goed = False

    # valt de uurcurve weg, dan blijft de sluiting over in plaats van niets
    uit2 = P.bouw(ruw, {}, {}, "0xtest", NepCache({("AMS", dag): 17.0}))
    r2 = (uit2["positions"] or [{}])[0]
    if r2.get("hours_to_peak") is not None or r2.get("hours_to_close") is None:
        print(f"  piek      MISLUKT: zonder uurcurve {r2.get('hours_to_peak')} / "
              f"{r2.get('hours_to_close')}")
        goed = False

    # een afgerekende positie gebruikt de piekklok niet meer, dus daar hoort
    # ook geen uurcurve-fetch te gebeuren: die kost in het echt tot drie
    # pogingen met een timeout van 45 seconden per stuk
    class TelCache(NepCache):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.piek_aanroepen = 0

        def piek(self, key, datum):
            self.piek_aanroepen += 1
            return None

    tel = TelCache({("AMS", dag): 17.0})
    beslist = [{"size": 10, "avgPrice": 0.6, "curPrice": 0.99, "outcome": "No",
                "slug": _slug("AMS", dag, "20c"), "title": "20°C"}]
    P.bouw(beslist, {}, {}, "0xtest", tel)
    if tel.piek_aanroepen:
        print(f"  piek      MISLUKT: een afgerekende positie haalt toch een "
              f"uurcurve ({tel.piek_aanroepen}x)")
        goed = False

    if goed:
        print("  piek      ok: uur en spreiding uit de curve, klok telt naar de piek, "
              "sluiting als terugval")
    return goed


# ── 11. de reeks ─────────────────────────────────────────────────────────────

def test_reeks() -> bool:
    """De kop en de regel van portfolio_history.csv moeten even breed zijn, en
    het bestand op schijf ook.

    logger.schrijf zet de kop alleen als het bestand nieuw is. Een kolom erbij
    zonder migratie levert dus regels van tien velden onder een kop van negen,
    en dat valt nergens om: het schuift alleen alle waarden een plek op zodra
    iemand er met een DictReader op leest."""
    goed = True
    rij = {"city": "AMS", "date": "2026-08-11", "bracket": "20°C",
           "adj_mean_now": 19.9, "model_prob_now": 0.21, "current_bid": 0.79,
           "city_bias_used": 0.4, "light": "red", "peak_hour": 15,
           "observed_today": 18.4, "restfactor": 0.4}
    r = P.hist_rij(rij, "2026-08-11T09:00:00+00:00")
    if len(r) != len(P.HIST_KOP):
        print(f"  reeks     MISLUKT: regel telt {len(r)} velden, kop {len(P.HIST_KOP)}")
        goed = False
    else:
        # Op naam en niet op positie: welke kolom achteraan staat verschuift bij
        # elke uitbreiding, maar dat elke waarde onder de juiste naam terechtkomt
        # moet blijven gelden. Precies dat gaat stuk als iemand een kolom in
        # HIST_KOP ertussen zet zonder hist_rij mee te verhuizen.
        op_naam = dict(zip(P.HIST_KOP, r))
        for kolom, hoort in (("peak_hour", 15), ("observed_today", 18.4),
                             ("restfactor", 0.4), ("light", "red"),
                             ("adj_mean_now", 19.9)):
            if op_naam.get(kolom) != hoort:
                print(f"  reeks     MISLUKT: {kolom} staat op {op_naam.get(kolom)!r}, "
                      f"verwacht {hoort!r}")
                goed = False

    # lege waarden worden lege velden, niet de tekst None
    leeg = dict(zip(P.HIST_KOP, P.hist_rij(
        {**rij, "peak_hour": None, "adj_mean_now": None,
         "observed_today": None, "restfactor": None}, "x")))
    mis = [k for k in ("peak_hour", "adj_mean_now", "observed_today", "restfactor")
           if leeg[k] != ""]
    if mis:
        print(f"  reeks     MISLUKT: ontbrekende waarden geven geen leeg veld: "
              f"{ {k: leeg[k] for k in mis} }")
        goed = False

    # en het bestand in de repo is rechthoekig
    pad = Path(__file__).resolve().parent.parent / "logs" / "portfolio_history.csv"
    if pad.exists():
        import csv
        regels = list(csv.reader(open(pad, newline="")))
        breedtes = {len(x) for x in regels}
        if breedtes != {len(P.HIST_KOP)}:
            print(f"  reeks     MISLUKT: portfolio_history.csv heeft regels van "
                  f"{sorted(breedtes)} velden, kop telt {len(P.HIST_KOP)}. "
                  f"Draai bot/migratie_portfolio_history.py")
            goed = False
        elif regels and regels[0] != P.HIST_KOP:
            print(f"  reeks     MISLUKT: de kop op schijf wijkt af: {regels[0]}")
            goed = False

    # De migratie verbreedt alleen een kop die letterlijk het begin van
    # HIST_KOP is. Een even brede kop met verwisselde namen is geen oude
    # versie maar iets anders; die herschrijven zou elke kolom stilzwijgend
    # een verkeerd etiket geven.
    import csv
    import importlib
    import tempfile
    M = importlib.import_module("migratie_portfolio_history")
    with tempfile.TemporaryDirectory() as td:
        oud = Path(td) / "portfolio_history.csv"
        oud.write_text(",".join(P.HIST_KOP[:-1]) + "\n" +
                       ",".join(["x"] * (len(P.HIST_KOP) - 1)) + "\n")
        if M.migreer(oud) != 0:
            print("  reeks     MISLUKT: de migratie weigert een echte oude kop")
            goed = False
        else:
            regels = list(csv.reader(open(oud, newline="")))
            if regels[0] != P.HIST_KOP or len(regels[1]) != len(P.HIST_KOP):
                print(f"  reeks     MISLUKT: migratie leverde {regels[:2]}")
                goed = False

        vreemd = Path(td) / "vreemd.csv"
        kop = list(P.HIST_KOP)
        kop[3], kop[4] = kop[4], kop[3]     # even breed, andere volgorde
        inhoud = ",".join(kop) + "\n" + ",".join(["x"] * len(kop)) + "\n"
        vreemd.write_text(inhoud)
        if M.migreer(vreemd) == 0 or vreemd.read_text() != inhoud:
            print("  reeks     MISLUKT: een even brede kop met andere namen "
                  "wordt herschreven in plaats van geweigerd")
            goed = False

    if goed:
        print(f"  reeks     ok: {len(P.HIST_KOP)} kolommen, kop en regel gelijk, "
              f"bestand rechthoekig, migratie weigert een vreemde kop")
    return goed


def main() -> int:
    print("\n  Zelftest portefeuille\n")
    goed = all([test_slug(), test_afstand(), test_netto(), test_stoplicht(),
                test_vak(), test_beslist(), test_herkansing(),
                test_markt_oneens(), test_piek(), test_reeks(),
                test_uitvoer()])
    print("\n  " + ("Alles in orde.\n" if goed else "ER GING IETS MIS.\n"))
    return 0 if goed else 1


if __name__ == "__main__":
    sys.exit(main())
