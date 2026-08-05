#!/usr/bin/env python3
"""Zelftest voor de correctiekern.

  python3 bot/test_kern.py

Controleert vijf dingen:

  ridge     De online ridge met vergeten reproduceert de gewone kleinste
            kwadraten, en de strafterm krimpt de coefficienten richting nul.
  pariteit  De rekenkern in index.html (tussen de bakens kern:start en
            kern:einde) geeft op dezelfde invoer exact dezelfde getallen als
            kalibratie.py. Vereist node; zonder node wordt dit overgeslagen.
  backtest  walkForwardJS, de backtest die de app zelf in de browser draait,
            levert dezelfde parameters op als kalibratie.py.
  contract  De geexporteerde parameters, toegepast zoals de app dat live doet,
            geven exact de voorspelling van de backtest.
  nut       Op de historische featurebundel (weerbot-modellen/features_alle.csv)
            is de kern gemiddeld beter dan de oude correctie, en de keuze per
            stad laat nooit een slechtere variant door.

Alles draait offline op bestanden uit de repository.
"""

import csv
import json
import math
import random
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kalibratie as K   # noqa: E402

WORTEL = Path(__file__).resolve().parent.parent
BUNDEL = WORTEL / "weerbot-modellen" / "features_alle.csv"
LANG_VAN_KORT = {"ifs": "p1_ifs", "aifs": "p1_aifs", "gfs": "p1_gfs",
                 "icon": "p1_icon", "gem": "p1_gem"}


def kern_js() -> str:
    """De rekenkern uit index.html, klaar om in node te draaien."""
    tekst = (WORTEL / "index.html").read_text()
    begin = tekst.index("/* ── kern:start")
    einde = tekst.index("/* ── kern:einde")
    return tekst[begin:einde]


def functie_js(naam: str) -> str:
    """Een losse functie uit index.html knippen op accolades."""
    tekst = (WORTEL / "index.html").read_text()
    i = tekst.index("function " + naam + "(")
    diep = 0
    for k in range(tekst.index("{", i), len(tekst)):
        if tekst[k] == "{":
            diep += 1
        elif tekst[k] == "}":
            diep -= 1
            if diep == 0:
                return tekst[i:k + 1]
    raise ValueError(naam)


def heeft_node() -> bool:
    try:
        return subprocess.run(["node", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


def draai_node(script: str, invoer) -> dict:
    with tempfile.TemporaryDirectory() as map_:
        js = Path(map_) / "test.js"
        dat = Path(map_) / "invoer.json"
        js.write_text(script)
        dat.write_text(json.dumps(invoer))
        r = subprocess.run(["node", str(js), str(dat)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return json.loads(r.stdout)


def verzin_records(seed: int, n: int = 240) -> list:
    """Nepdata met een bekende structuur: elk model heeft een eigen bias en ruis."""
    rnd = random.Random(seed)
    bias = {m: rnd.uniform(-2, 2) for m in K.MODELLEN}
    records = []
    o0 = date(2025, 1, 1).toordinal()
    for i in range(n):
        seizoen = 12 * math.sin((o0 + i) / 365.25 * 2 * math.pi)
        waar = 15 + seizoen + rnd.gauss(0, 3)
        fc = {}
        for m in K.MODELLEN:
            if rnd.random() < 0.05:
                continue          # af en toe ontbreekt een model
            fc[m] = waar + bias[m] + rnd.gauss(0, 1.5)
        if len(fc) < 2:
            fc = {m: waar + bias[m] for m in K.MODELLEN}
        records.append((o0 + i, fc, waar))
    return records


# ── 1. pariteit tussen python en javascript ───────────────────────────────────

def test_pariteit() -> bool:
    if not heeft_node():
        print("  pariteit  overgeslagen (node ontbreekt)")
        return True

    records = verzin_records(7)
    # In python: fit de kern op alle dagen behalve de laatste, voorspel de laatste.
    kern = K.OnlineRidge(len(K.KERN_FEATURES), K.HALFWAARDE_KERN, K.ALPHA_KERN)
    resid: dict = {}
    py_reeks = []
    for o, fc, y in records:
        spreiding = K.pstdev_van(fc)
        mu = sum(fc.values()) / len(fc)
        x = K.kern_vector(mu, K.lag_van(resid, o, 2), spreiding, fc)
        yhat = mu + kern.voorspel(x)
        py_reeks.append(yhat)
        resid[o] = y - yhat
        kern.voeg_toe(o, x, y - mu)

    invoer = [{"o": o, "fc": fc, "y": y} for o, fc, y in records]
    script = kern_js() + """
const KORT_VAN = { ifs: "ifs", aifs: "aifs", gfs: "gfs", icon: "icon", gem: "gem" };
const KERN_MODELLEN = ["ifs", "aifs", "gfs", "icon", "gem"];
const records = JSON.parse(require("fs").readFileSync(process.argv[2], "utf8"));
const kern = maakKern(3 + KERN_MODELLEN.length, %(half)s, %(alpha)s);
const resid = {}, uit = [];
records.forEach(function (r) {
  let som = 0, n = 0;
  for (const m in r.fc) { som += r.fc[m]; n++; }
  const mu = som / n;
  const x = kernVector(mu, kernLag(resid, r.o, 2), kernSpreiding(r.fc), r.fc);
  const yhat = mu + kern.voorspel(x);
  uit.push(yhat);
  resid[r.o] = r.y - yhat;
  kern.voegToe(r.o, x, r.y - mu);
});
console.log(JSON.stringify(uit));
""" % {"half": K.HALFWAARDE_KERN, "alpha": K.ALPHA_KERN}

    try:
        js_reeks = draai_node(script, invoer)
    except RuntimeError as ex:
        print("  pariteit  MISLUKT: node gaf een fout\n" + str(ex))
        return False

    grootste = max(abs(a - b) for a, b in zip(py_reeks, js_reeks))
    ok = grootste < 1e-9
    print(f"  pariteit  {'ok' if ok else 'MISLUKT'}: grootste verschil "
          f"python/javascript over {len(py_reeks)} dagen is {grootste:.2e}")
    return ok


# ── 1b. dezelfde backtest in de browser ───────────────────────────────────────

def test_kalibratie_pariteit() -> bool:
    """walkForwardJS (de backtest die de app zelf in de browser draait voor
    zelf toegevoegde en herijkte steden) moet dezelfde parameters opleveren als
    kalibratie.py. Anders krijgt zo'n stad stiekem een andere wiskunde."""
    if not heeft_node():
        print("  backtest  overgeslagen (node ontbreekt)")
        return True

    records = verzin_records(11, n=200)
    py = K.walk_forward(records, lag_dagen=2)

    script = "\n".join([
        'const KORT_VAN = { ifs: "ifs", aifs: "aifs", gfs: "gfs", icon: "icon", gem: "gem" };',
        'const KERN_MODELLEN = ["ifs", "aifs", "gfs", "icon", "gem"];',
        'const KERN_FEATURES = ["mu", "lag", "spreiding"].concat('
        '  KERN_MODELLEN.map(function (m) { return "d_" + m; }));',
        f'const KAL = {{ half: {K.HALFWAARDE}, burnG: {K.BURN_GEWICHT}, '
        f'burnR: {K.BURN_REGRESSIE}, burnE: {K.BURN_EVALUATIE}, krimp: {K.KRIMP_N}, '
        f'ridge: 0.25, halfKern: {K.HALFWAARDE_KERN}, alphaKern: {K.ALPHA_KERN} }};',
        'const LAGV = 2;',
        'const CONFIG = { params: { band_factor: 1 } };',
        'const PARAMS = { spreidingsband: false };',
        kern_js(),
        functie_js("ewmaW"), functie_js("wGem2"), functie_js("wKwant2"),
        functie_js("nEff2"), functie_js("walkForwardJS"),
        'const rec = JSON.parse(require("fs").readFileSync(process.argv[2], "utf8"));',
        'console.log(JSON.stringify(walkForwardJS(rec, 2)));',
    ])
    invoer = [{"o": o, "fc": fc, "y": y} for o, fc, y in records]
    try:
        js = draai_node(script, invoer)
    except RuntimeError as ex:
        print("  backtest  MISLUKT: node gaf een fout\n" + str(ex))
        return False

    verschillen = []

    def vergelijk(naam, a, b, tol=5e-3):
        if a is None or b is None:
            if a is not None or b is not None:
                verschillen.append(f"{naam}: {a} vs {b}")
            return
        if abs(a - b) > tol:
            verschillen.append(f"{naam}: {a} vs {b}")

    for veld in ("a", "b", "g", "mae_oud", "mae_kern", "mae_nieuw"):
        vergelijk(veld, py.get(veld), js.get(veld))
    vergelijk("n_totaal", py["n_totaal"], js["n_totaal"], 0)
    for m in K.MODELLEN:
        vergelijk("gewicht " + m, (py["gewichten"] or {}).get(m),
                  (js["gewichten"] or {}).get(m), 2e-3)
    if ("kern" in py) != ("kern" in js):
        verschillen.append(f"kern gekozen: python {'kern' in py}, js {'kern' in js}")
    elif "kern" in py:
        vergelijk("kern intercept", py["kern"]["intercept"], js["kern"]["intercept"])
        for i, naam in enumerate(K.KERN_FEATURES):
            vergelijk("kern " + naam, py["kern"]["coef"][i], js["kern"]["coef"][i])
    # de band: python rondt af voor de bandfactor, js met factor 1 in deze test
    vergelijk("res_q10", py["res_q10"], js["res_q10"], 1e-2)
    vergelijk("res_q90", py["res_q90"], js["res_q90"], 1e-2)

    ok = not verschillen
    print(f"  backtest  {'ok' if ok else 'MISLUKT'}: kalibratie.py en "
          f"walkForwardJS geven dezelfde parameters"
          + ("" if ok else "\n            " + "\n            ".join(verschillen)))
    return ok


# ── 1c. het contract tussen app_params en de app ──────────────────────────────

def test_toepassing() -> bool:
    """De app past de geexporteerde kern toe op verse modelwaarden. Die
    toepassing (kernVoorspel in index.html) moet exact hetzelfde uitkomen als
    kalibratie.py met dezelfde coefficienten: zelfde featurevolgorde, zelfde
    gewichten, zelfde lagvenster."""
    if not heeft_node():
        print("  contract  overgeslagen (node ontbreekt)")
        return True

    records = verzin_records(23, n=180)
    hp = K.walk_forward(records[:-1], lag_dagen=2)
    if "kern" not in hp:
        print("  contract  MISLUKT: geen kern in de parameters van de testreeks")
        return False
    hp = {k: v for k, v in hp.items() if k not in ("yhat_per_dag", "dekkingsreeks",
                                                   "dekkingsreeks_s", "breedte_o", "breedte_s")}

    o, fc, _ = records[-1]
    lag = -0.7
    # python: pas de geexporteerde coefficienten toe
    gew = hp["gewichten"]
    W = sum(gew[m] for m in fc if m in gew)
    mu = sum(gew[m] * fc[m] for m in fc if m in gew) / W
    x = K.kern_vector(mu, lag, K.pstdev_van(fc), fc)
    py = mu + hp["kern"]["intercept"] + sum(c * v for c, v in zip(hp["kern"]["coef"], x))

    script = "\n".join([
        'const KORT_VAN = { ifs: "ifs", aifs: "aifs", gfs: "gfs", icon: "icon", gem: "gem",'
        '  ecmwf_ifs025: "ifs", ecmwf_aifs025: "aifs", ecmwf_aifs025_single: "aifs",'
        '  gfs_seamless: "gfs", ncep_gefs025: "gfs", icon_seamless: "icon",'
        '  gem_seamless: "gem", gem_global: "gem" };',
        'const KERN_MODELLEN = ["ifs", "aifs", "gfs", "icon", "gem"];',
        kern_js(),
        'const inv = JSON.parse(require("fs").readFileSync(process.argv[2], "utf8"));',
        'console.log(JSON.stringify(kernVoorspel(inv.hp, inv.fc, inv.lag)));',
    ])
    try:
        js = draai_node(script, {"hp": hp, "fc": fc, "lag": lag})
    except RuntimeError as ex:
        print("  contract  MISLUKT: node gaf een fout\n" + str(ex))
        return False

    ok = abs(py - js) < 1e-9
    print(f"  contract  {'ok' if ok else 'MISLUKT'}: app_params toegepast in de app "
          f"geeft {js:.6f}, in de backtest {py:.6f}")
    return ok


# ── 2. de ridge zelf ──────────────────────────────────────────────────────────

def test_ridge() -> bool:
    """Zonder straf en zonder vergeten moet de ridge de gewone kleinste
    kwadraten reproduceren op data die exact lineair is."""
    rnd = random.Random(3)
    coef = [0.7, -0.4, 1.3]
    mod = K.OnlineRidge(3, half=1e9, alpha=0.0)
    punten = []
    for i in range(60):
        x = [rnd.uniform(-3, 3) for _ in range(3)]
        y = 2.5 + sum(c * v for c, v in zip(coef, x))
        punten.append((x, y))
        mod.voeg_toe(1000 + i, x, y)
    a, b = mod.coef()
    fout = max([abs(a - 2.5)] + [abs(b[i] - coef[i]) for i in range(3)])
    ok = fout < 1e-6
    print(f"  ridge     {'ok' if ok else 'MISLUKT'}: grootste coefficientfout {fout:.2e}")

    # met straf moet elke coefficient kleiner in absolute zin worden
    streng = K.OnlineRidge(3, half=1e9, alpha=1000.0)
    for x, y in punten:
        streng.voeg_toe(1000, x, y)
    _, bs = streng.coef()
    gekrompen = all(abs(bs[i]) < abs(b[i]) for i in range(3))
    print(f"  krimp     {'ok' if gekrompen else 'MISLUKT'}: "
          f"alpha trekt de coefficienten naar nul")
    return ok and gekrompen


# ── 3. doet de kern het beter op echte data ───────────────────────────────────

def laad_bundel(max_steden: int) -> dict:
    per: dict = {}
    if not BUNDEL.exists():
        return per
    with open(BUNDEL, newline="") as f:
        for r in csv.DictReader(f):
            if not r["doel"]:
                continue
            fc = {}
            for kort, kolom in LANG_VAN_KORT.items():
                if r.get(kolom):
                    fc[kort] = float(r[kolom])
            if len(fc) < 4:
                continue
            per.setdefault(r["stad"], []).append(
                (date.fromisoformat(r["datum"]).toordinal(), fc, float(r["doel"])))
    keuze = sorted(per)[:max_steden]
    return {s: sorted(per[s]) for s in keuze}


def test_data(max_steden: int = 6) -> bool:
    per = laad_bundel(max_steden)
    if not per:
        print("  data      overgeslagen (features_alle.csv ontbreekt)")
        return True
    oud, nieuw, gekozen, slechter = [], [], 0, 0
    for stad, records in per.items():
        r = K.walk_forward(records, lag_dagen=2)
        oud.append(r["mae_oud"])
        nieuw.append(r["mae_nieuw"])
        if "kern" in r:
            gekozen += 1
        if r["mae_nieuw"] > r["mae_oud"] + 1e-9:
            slechter += 1
    gem_o = sum(oud) / len(oud)
    gem_n = sum(nieuw) / len(nieuw)
    ok = gem_n <= gem_o and slechter == 0
    print(f"  data      {'ok' if ok else 'MISLUKT'}: {len(per)} steden, MAE "
          f"{gem_o:.3f} -> {gem_n:.3f} ({(gem_n / gem_o - 1) * 100:+.1f}%), "
          f"kern gekozen bij {gekozen}, slechter bij {slechter}")
    return ok


def main() -> int:
    print("\n  Zelftest correctiekern\n")
    goed = all([test_ridge(), test_pariteit(), test_kalibratie_pariteit(),
                test_toepassing(), test_data()])
    print("\n  " + ("Alles in orde.\n" if goed else "ER GING IETS MIS.\n"))
    return 0 if goed else 1


if __name__ == "__main__":
    sys.exit(main())
