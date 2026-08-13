#!/usr/bin/env python3
"""Wat levert de meting van vandaag op als je hem in de puntvoorspelling stopt?

  python3 bot/meet_meting.py --zelftest      de rekenkant, zonder netwerk
  python3 bot/meet_meting.py --dagen 400     de echte meting (haalt IEM op)

De vraag
--------
De app conditioneert al op wat er vandaag gemeten is, maar alleen in de kansen
per temperatuurvak: `waarneming.cdf` kapt de verdeling af op `m` en
`waarneming.conditioneer` krimpt de rest. Het getal dat op het scherm staat —
`d.verwachting` — weet van niets. Op horizon 0 voorspelt de app dus nog steeds
alsof de dag moet beginnen, ook al staat er om drie uur 's middags al een
cijfer op de meter.

Uit de restfactorcurve volgt dat dat veel kan schelen: bij een MAE van 0,634 °C
op h0 zou de winst oplopen van niets 's ochtends tot een halve graad laat op de
dag. Maar die curve is geleend. Hij komt uit de entropie van de márktprijzen en
staat er met een dempingsfactor overheen, juist omdat de markt om meer redenen
scherper is dan alleen de meting. De kop van bot/waarneming.py zegt het zelf:
zodra `waarneming` lang genoeg in het logboek staat hoort die curve op de eigen
reeks gekalibreerd te worden en kan de demping eruit.

Dit script wacht daar niet op. Het IEM-archief heeft de uurlijkse METAR's van
jaren terug, en waarneming.haal_stations haalt ze per datumbereik op. Daaruit is
voor elke stad-dag te reconstrueren wat er om elk lokaal uur op de meter stond,
en dat naast `doel` uit features_alle.csv gelegd geeft het antwoord op de eigen
reeks in plaats van op die van de markt.

Wat er gemeten wordt
--------------------
Per lokaal uur u, over alle stad-dagen:

  kaal        MAE van de rekenkern zonder meting (de walk-forward van de app
              zelf, via kalibratie.walk_forward, net als schaduw_backtest.py)
  met meting  MAE van E[max(m, R)], met R uit waarneming.conditioneer
  ondergrens  MAE van max(m, kaal): alleen de fysieke ondergrens, zonder de
              krimp. Dat deel is gratis en vraagt geen enkele kalibratie.

De derde kolom is de eerlijkste ondergrens van wat de meting waard is: hij
gebruikt geen restfactor en dus geen geleende curve. Het verschil tussen de
tweede en de derde is wat de krimp bovenop de afkapping doet, en precies daar
zit de dubbeltelling waar de demping voor bedoeld is.

Ook uit dit script komt de curve zelf: per uur de sd van de restfout van de
kale verwachting, geteld over de dagen waarop de piek nog niet gevallen was.
Dat is `w` op de eigen reeks, om naast W_REST_MAX in polymarkt.js te leggen.

Draait niet in de tweewekelijkse actie: het is een studie en geen bewaking, en
de IEM-aanroepen zijn te zwaar om twee keer per week te herhalen. Zie
.github/workflows/meting-studie.yml, met de hand te starten.
"""
import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

WORTEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORTEL / "bot"))
sys.path.insert(0, str(WORTEL / "weerbot-modellen"))
import kalibratie as K        # noqa: E402  walk_forward: de rekenkern van de app
import waarneming as W        # noqa: E402  conditioneer, restfactor, IEM
import weer                   # noqa: E402  STEDEN

FEATURES = WORTEL / "weerbot-modellen" / "features_alle.csv"
UIT = WORTEL / "weerbot-modellen" / "monitoring" / "meting_studie.json"
UREN = list(range(6, 23))


# ── de rekenkant, los toetsbaar ──────────────────────────────────────────────

def phi(z):
    return math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def Phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def verwachting_max(m, mu_r, sig_r):
    """E[max(m, R)] met R ~ N(mu_r, sig_r).

    = m * P(R <= m) + E[R ; R > m]
    = m * Phi(z) + mu_r * (1 - Phi(z)) + sig_r * phi(z),  z = (m - mu_r)/sig_r

    Bij sig_r naar nul loopt hij naar max(m, mu_r), en bij m ver onder mu_r naar
    mu_r zelf: dan zegt de meting niets meer dan dat de dag nog moet komen.
    """
    if m is None:
        return mu_r
    if sig_r <= 0:
        return max(m, mu_r)
    z = (m - mu_r) / sig_r
    return m * Phi(z) + mu_r * (1 - Phi(z)) + sig_r * phi(z)


Z80 = 1.2815515655446004


def band80(m, mu_r, sig_r):
    """De 10- en 90-procentgrens van de afgekapte verdeling uit waarneming.cdf.

    F(t) is nul onder `m` en Phi((t-mu_r)/sig_r) daarboven, dus er zit een
    puntmassa op `m` ter grootte p0 = Phi((m-mu_r)/sig_r): de kans dat de piek
    al geweest is. Is die massa groter dan het kwantiel, dan valt de grens
    samen met `m`.
    """
    if sig_r <= 0:
        return (m, m) if m is not None else (mu_r, mu_r)
    if m is None:
        return mu_r - Z80 * sig_r, mu_r + Z80 * sig_r
    p0 = Phi((m - mu_r) / sig_r)
    lo = m if p0 >= 0.10 else mu_r - Z80 * sig_r
    hi = m if p0 >= 0.90 else mu_r + Z80 * sig_r
    return lo, hi


def loopmax(uurwaarden):
    """{uur: hoogste waarde tot en met dat uur}. uurwaarden is {uur: temp}."""
    uit, hoog = {}, None
    for u in range(24):
        v = uurwaarden.get(u)
        if v is not None:
            hoog = v if hoog is None else max(hoog, v)
        if hoog is not None:
            uit[u] = hoog
    return uit


def zelftest():
    """De rekenkant zonder netwerk. Draait mee in de zelftest."""
    fouten = []

    def toets(naam, ok, uitleg=""):
        print(f"  {'ok  ' if ok else 'FOUT'} {naam}" + ("" if ok else f": {uitleg}"))
        if not ok:
            fouten.append(naam)

    # E[max(m,R)] tegen de twee grensgevallen
    toets("bij een scherpe R is E[max] gewoon max(m, mu)",
          abs(verwachting_max(5.0, 3.0, 1e-9) - 5.0) < 1e-6
          and abs(verwachting_max(1.0, 3.0, 1e-9) - 3.0) < 1e-6)
    # m diep onder mu: de meting voegt bijna niets toe
    toets("een lage meting laat de verwachting vrijwel staan",
          abs(verwachting_max(-10.0, 0.0, 1.0) - 0.0) < 1e-3,
          str(verwachting_max(-10.0, 0.0, 1.0)))
    # m gelijk aan mu: E[max] = mu + sigma/sqrt(2pi)
    got = verwachting_max(0.0, 0.0, 1.0)
    toets("bij m gelijk aan mu komt er sigma/sqrt(2pi) bij",
          abs(got - 1 / math.sqrt(2 * math.pi)) < 1e-9, str(got))
    # E[max] ligt altijd boven allebei
    toets("E[max] ligt nooit onder m en nooit onder mu",
          all(verwachting_max(m, mu, 1.0) >= max(m, mu) - 1e-9
              for m in (-3, 0, 3) for mu in (-3, 0, 3)))
    # loopmax
    lm = loopmax({6: 10.0, 9: 14.0, 12: 13.0, 15: 18.0})
    toets("de loopmax loopt niet terug",
          lm[6] == 10.0 and lm[9] == 14.0 and lm[11] == 14.0
          and lm[12] == 14.0 and lm[15] == 18.0 and lm[23] == 18.0, str(lm))
    toets("uren voor de eerste waarneming blijven leeg",
          0 not in loopmax({6: 10.0}))
    # conditioneer uit waarneming.py moet monotoon zijn in het uur
    ws = [W.restfactor(u) for u in range(6, 23)]
    toets("de restfactor daalt over de dag", all(a >= b for a, b in zip(ws, ws[1:])))

    # band80: de 80%-grenzen van de afgekapte verdeling
    lo, hi = band80(None, 10.0, 2.0)
    toets("zonder meting is het de gewone 80%-band",
          abs(lo - (10 - Z80 * 2)) < 1e-12 and abs(hi - (10 + Z80 * 2)) < 1e-12)
    lo, hi = band80(0.0, 10.0, 2.0)
    toets("een meting ver onder mu laat de band staan",
          abs(lo - (10 - Z80 * 2)) < 1e-12 and abs(hi - (10 + Z80 * 2)) < 1e-12)
    lo, hi = band80(20.0, 10.0, 2.0)
    toets("een meting ver boven mu klapt de band op de meting",
          lo == 20.0 and hi == 20.0)
    m = 10.0 + 2.0 * -1.2                      # p0 = Phi(-1,2) = 0,115 > 0,10
    lo, hi = band80(m, 10.0, 2.0)
    toets("zodra de puntmassa het kwantiel haalt valt de ondergrens op m",
          abs(lo - m) < 1e-12 and hi > m)
    toets("de band is nooit omgekeerd",
          all(band80(mm, 10.0, 2.0)[0] <= band80(mm, 10.0, 2.0)[1] + 1e-12
              for mm in (0, 5, 9, 10, 11, 15, 20)))

    # De ontleding op hetzelfde voorbeeld als bot/test_waarneming.py, zodat de
    # uurversie en de dagversie niet uit elkaar kunnen lopen.
    import test_waarneming as TW
    u = ontleed_uren(TW.IEM_VOORBEELD, ["LGA", "ORD"])
    lga = u.get("LGA", {}).get("2026-08-10", {})
    toets("de uurontleding houdt het uur vast",
          lga.get(6) == 71.60 and lga.get(13) == 84.20 and lga.get(14) == 86.00,
          str(lga))
    toets("een ontbrekende meting (M) telt niet mee", 12 not in lga, str(lga))
    toets("de dagen blijven gescheiden",
          u["LGA"].get("2026-08-09", {}).get(15) == 90.0)
    toets("een station dat niet gevraagd is sluipt er niet in",
          "ORD" not in ontleed_uren(TW.IEM_VOORBEELD, ["LGA"]))
    # de dagmax uit de uurreeks moet gelijk zijn aan die van ontleed_iem
    dag = W.ontleed_iem(TW.IEM_VOORBEELD, ["LGA"])["LGA"]["2026-08-10"]
    toets("de dagmax uit de uurreeks klopt met waarneming.ontleed_iem",
          max(lga.values()) == dag["maxf"], f'{max(lga.values())} tegen {dag["maxf"]}')
    return 1 if fouten else 0


# ── de meting ────────────────────────────────────────────────────────────────

def laad_features():
    """{(mlKey, datum): rij} met de p1-reeks en het doel."""
    per = defaultdict(list)
    for r in csv.DictReader(open(FEATURES)):
        fc = {}
        for m in ("ifs", "aifs", "gfs", "icon", "gem"):
            v = r.get("p1_" + m)
            if v:
                fc[m] = float(v)
        per[r["stad"]].append({"datum": r["datum"], "fc": fc,
                               "doel": float(r["doel"]) if r["doel"] else None})
    for v in per.values():
        v.sort(key=lambda x: x["datum"])
    return per


def ontleed_uren(tekst, stations):
    """{station: {datum: {uur: hoogste tmpf in dat uur}}} uit de IEM-uitvoer.

    waarneming.ontleed_iem kan dit niet leveren en dat is daar terecht: die
    module wil de dagwaarde en gooit het uurverloop meteen weg. Hier is juist
    dat verloop het onderwerp, dus staat de ontleding hier apart. De vorm van
    de regels is dezelfde en bot/test_ml.py toetst hem op hetzelfde voorbeeld
    als bot/test_waarneming.py, zodat de twee niet uit elkaar kunnen lopen.

    De tijdstempel staat in de tijdzone die _iem_url meegeeft, dus het uur is
    het lokale uur. Temperaturen blijven hier in Fahrenheit, net als bij IEM;
    de omrekening hoort bij de stad omdat niet elke stad in °C rekent.
    """
    uit = {}
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
            continue                       # "M" ontbrekend, "T" spoor
        stamp = delen[1].strip()
        try:
            uur = int(stamp[11:13])
        except (ValueError, IndexError):
            continue
        per = uit.setdefault(st, {}).setdefault(stamp[:10], {})
        if uur not in per or t > per[uur]:
            per[uur] = t
    return uit


def haal_uurreeksen(steden, d1, d2):
    """{key: {datum: {uur: temp in de eenheid van de stad}}} uit het IEM-archief.

    Zelfde bundeling en herkansingen als waarneming.haal_stations — per
    tijdzone, want IEM kent er één per verzoek, en dat is meteen de goede
    indeling omdat de lokale kalenderdag per tijdzone verschilt.
    """
    import time
    per_tz = defaultdict(list)
    for s in steden:
        per_tz[s["tz"]].append(s)

    def haal_een(deel, tz):
        for poging in range(W.IEM_POGINGEN):
            try:
                return weer._get(W._iem_url(deel, tz, d1, d2), timeout=W.IEM_TIMEOUT)
            except Exception:                      # noqa: BLE001
                if poging + 1 < W.IEM_POGINGEN:
                    time.sleep(2 + poging * 3)
        return ""

    uit = {}
    for tz, groep in per_tz.items():
        stations = sorted({s["station"] for s in groep})
        rauw = {}
        for i in range(0, len(stations), W.IEM_BUNDEL):
            deel = stations[i:i + W.IEM_BUNDEL]
            rauw.update(ontleed_uren(haal_een(deel, tz), deel))
            time.sleep(0.5)
        gemist = [s for s in stations if s not in rauw]
        for st in gemist:                          # bundel half terug: los erachteraan
            rauw.update(ontleed_uren(haal_een([st], tz), [st]))
            time.sleep(0.5)
        print(f"  {tz}: {len(stations)} stations, {len(rauw)} gevuld"
              + (f", gemist {', '.join(gemist)}" if gemist else ""), flush=True)
        for s in groep:
            per_dag = {}
            for datum, uren in (rauw.get(s["station"]) or {}).items():
                # Altijd naar °C, ook voor de steden die de app in °F toont:
                # features_alle.csv staat volledig in °C en daar wordt tegen
                # gemeten. De weergave-eenheid van de stad speelt hier niet.
                per_dag[datum] = {u: weer.c_van_f(t) for u, t in uren.items()}
            if per_dag:
                uit[s["key"]] = per_dag
    return uit


def meet(dagen):
    # Hongkong loopt via het observatorium en niet via IEM; die valt hier af.
    steden = [s for s in weer.STEDEN
              if s.get("bron") == "iem" and s.get("station")]
    d2 = date.today() - timedelta(days=1)
    d1 = d2 - timedelta(days=dagen)
    print(f"IEM-uurreeksen {d1} t/m {d2} voor {len(steden)} steden")
    reeksen = haal_uurreeksen(steden, d1, d2)
    feats = laad_features()

    # app-sleutel -> ml-sleutel, uit de koppel
    import re
    koppel = (WORTEL / "weerbot-modellen" / "weerbot-ml-koppel.js").read_text()
    keymap = dict(re.findall(r'(\w+):"(\w+)"',
                  re.search(r"var KEYMAP = \{(.*?)\};", koppel, re.S).group(1)))

    per_uur = {u: {"kaal": [], "met": [], "ondergrens": []} for u in UREN}
    rest = {u: [] for u in UREN}
    # De dekking van de geconditioneerde band, gesplitst naar of de piek al
    # gevallen was. Op de dagen dat hij al viel doet de afkapping het werk en
    # is dekking geen vraag; de krimp moet zich bewijzen op de andere.
    dek = {u: {"alles": [], "voor": [], "kaal": [], "breedte": []} for u in UREN}
    n_dagen = 0
    for s in steden:
        mlk = keymap.get(s["key"])
        rijen = feats.get(mlk) or []
        if not rijen:
            continue
        records = [(date.fromisoformat(r["datum"]).toordinal(), r["fc"], r["doel"])
                   for r in rijen if r["doel"] is not None and r["fc"]]
        if len(records) < K.BURN_EVALUATIE + 60:
            continue
        wf = K.walk_forward(records, lag_dagen=1)
        yhat = wf.get("yhat_per_dag") or {}
        # de spreiding van de restfout: de sigma waarmee geconditioneerd wordt
        echt = {date.fromisoformat(r["datum"]).toordinal(): r["doel"]
                for r in rijen if r["doel"] is not None}
        resid = [yhat[o] - echt[o] for o in yhat if o in echt]
        if len(resid) < 30:
            continue
        sig = statistics.pstdev(resid) or 1.0

        for r in rijen:
            o = date.fromisoformat(r["datum"]).toordinal()
            y, mu = echt.get(o), yhat.get(o)
            if y is None or mu is None:
                continue
            uurw = (reeksen.get(s["key"]) or {}).get(r["datum"])
            if not uurw:
                continue
            lm = loopmax(uurw)
            n_dagen += 1
            for u in UREN:
                m = lm.get(u)
                if m is None:
                    continue
                mu_r, sig_r = W.conditioneer(mu, sig, m, u, "max")
                per_uur[u]["kaal"].append(abs(mu - y))
                per_uur[u]["met"].append(abs(verwachting_max(m, mu_r, sig_r) - y))
                per_uur[u]["ondergrens"].append(abs(max(m, mu) - y))
                lo, hi = band80(m, mu_r, sig_r)
                raak = 1.0 if lo - 1e-9 <= y <= hi + 1e-9 else 0.0
                dek[u]["alles"].append(raak)
                dek[u]["breedte"].append(hi - lo)
                # de onvoorwaardelijke band als ijkpunt: die hoort op 80% te
                # zitten, en zit hij daar, dan is elke afwijking verderop van
                # de krimp en niet van de basisband
                dek[u]["kaal"].append(
                    1.0 if mu - Z80 * sig <= y <= mu + Z80 * sig else 0.0)
                if m < y - 1e-9:           # de piek was nog niet gevallen
                    rest[u].append(y - mu)
                    dek[u]["voor"].append(raak)

    print(f"\n{n_dagen} stad-dagen met een uurreeks\n")
    print(f"{'uur':>4s} {'n':>6s} {'kaal':>7s} {'ondergrens':>11s} {'winst':>7s} "
          f"{'met krimp':>10s} {'winst':>7s} {'w eigen':>8s} {'w app':>7s}")
    tabel = {}
    sig0 = statistics.pstdev(rest[UREN[0]]) if len(rest[UREN[0]]) > 5 else None
    for u in UREN:
        b = per_uur[u]
        if len(b["kaal"]) < 30:
            continue
        kaal = statistics.mean(b["kaal"])
        onder = statistics.mean(b["ondergrens"])
        met = statistics.mean(b["met"])
        w_eigen = (statistics.pstdev(rest[u]) / sig0) if (sig0 and len(rest[u]) > 5) else None
        tabel[u] = {"n": len(b["kaal"]), "mae_kaal": kaal, "mae_ondergrens": onder,
                    "mae_met_krimp": met, "w_eigen": w_eigen,
                    "w_app": W.restfactor(u)}
        print(f"{u:4d} {len(b['kaal']):6d} {kaal:7.3f} {onder:11.3f} {kaal-onder:+7.3f} "
              f"{met:10.3f} {kaal-met:+7.3f} "
              f"{(f'{w_eigen:8.3f}' if w_eigen is not None else '       -')} "
              f"{W.restfactor(u):7.3f}")

    # ── de dekking van de geconditioneerde band ──
    print(f"\n{'uur':>4s} {'n voor':>7s} {'piek af':>8s} {'dek kaal':>9s} "
          f"{'dek alles':>10s} {'dek voor':>9s} {'breedte':>8s}")
    for u in UREN:
        b = dek[u]
        if len(b["alles"]) < 30:
            continue
        voor = gem(b["voor"])
        deel_af = 1 - len(b["voor"]) / len(b["alles"])
        tabel[u].update({"dekking_alles": gem(b["alles"]), "dekking_piek_voor": voor,
                         "dekking_kaal": gem(b["kaal"]), "n_piek_voor": len(b["voor"]),
                         "aandeel_piek_af": deel_af, "breedte80": gem(b["breedte"])})
        print(f"{u:4d} {len(b['voor']):7d} {deel_af*100:7.1f}% "
              f"{gem(b['kaal'])*100:8.1f}% {gem(b['alles'])*100:9.1f}% "
              f"{(voor*100 if voor is not None else 0):8.1f}% {gem(b['breedte']):8.3f}")

    UIT.parent.mkdir(exist_ok=True)
    UIT.write_text(json.dumps({"gegenereerd": date.today().isoformat(),
                               "dagen": dagen, "stad_dagen": n_dagen,
                               "per_uur": tabel}, indent=1) + "\n")
    print(f"\ngeschreven naar {UIT.relative_to(WORTEL)}")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--zelftest", action="store_true")
    p.add_argument("--dagen", type=int, default=400)
    a = p.parse_args()
    if a.zelftest:
        print("meet_meting: rekenkant")
        return zelftest()
    return meet(a.dagen)


if __name__ == "__main__":
    sys.exit(main())
