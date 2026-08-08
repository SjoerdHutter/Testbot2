#!/usr/bin/env python3
"""Zelftest voor de portefeuillebewaking.

  python3 bot/test_portfolio.py

Controleert vijf dingen:

  slug      De slug van een markt valt terug uiteen in stad, doeldag en reeks,
            precies andersom dan slug_van in signalen.py hem opbouwt.
  afstand   De afstand tot de vakrand en de vakbreedte, ook bij een open einde
            en op een °C markt waar een vak een hele graad breed is.
  netto     YES en NO op hetzelfde vak vallen tegen elkaar weg, restjes onder
            een half aandeel tellen niet mee, en een positie die niet te
            koppelen is verdwijnt niet maar belandt in unmapped.
  stoplicht Elke tak van de kleurregels, met een verzonnen setje posities:
            in het vak, binnen een half vak, lage winkans, tussen een half en
            een heel vak, kansstijging boven 15pp, en de rest groen.
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
         {"key": "AMS", "datum": "2026-08-09", "soort": "max"}),
        ("highest-temperature-in-nyc-on-august-9-2026-84-85f",
         {"key": "NYC", "datum": "2026-08-09", "soort": "max"}),
        ("lowest-temperature-in-los-angeles-on-december-31-2026",
         {"key": "LAX", "datum": "2026-12-31", "soort": "min"}),
        ("highest-temperature-in-kuala-lumpur-on-march-1-2027-33corhigher",
         {"key": "KUL", "datum": "2027-03-01", "soort": "max"}),
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
    instap = {
        (d_ams, "AMS", "max", "20°C"):
            {"gelogd": "x", "adj_mean": 18.1, "model_prob": 0.11},
        (d_nyc, "NYC", "max", "84-85°F"):
            {"gelogd": "x", "adj_mean": 84.5, "model_prob": 0.28},
        (d_tyo, "TYO", "max", "33°C"):
            {"gelogd": "x", "adj_mean": 32.6, "model_prob": 0.24},
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


def main() -> int:
    print("\n  Zelftest portefeuille\n")
    goed = all([test_slug(), test_afstand(), test_netto(),
                test_stoplicht(), test_uitvoer()])
    print("\n  " + ("Alles in orde.\n" if goed else "ER GING IETS MIS.\n"))
    return 0 if goed else 1


if __name__ == "__main__":
    sys.exit(main())
