#!/usr/bin/env python3
"""Toetst de koppeling tussen de app en de ML-modellen.

Waarom dit bestand bestaat: de ML-modellen worden getraind door
weerbot-modellen/deel9_wekelijks.py en gebruikt door
weerbot-modellen/weerbot-ml-koppel.js, en die twee zaten uit elkaar gegroeid
zonder dat iets omviel. De modellen kregen live de gemiddelden van de
ensembleleden uit ensemble-api voorgeschoteld terwijl ze op de deterministische
previous-runs zijn gefit, en voor GEM en GFS waren dat zelfs andere
modelvarianten. Dat kost geen foutmelding, alleen een slechtere voorspelling.

Deze toetsen leggen de trainingsdefinities vast in code: welke modellen, welk
dagmaximum, welke spreiding, welke lagfout, welke terugval bij ontbrekende
invoer. Wijzigt deel9_wekelijks.py, dan hoort hier iets om te vallen.

Vereist node; zonder node worden de javascript-toetsen overgeslagen.
"""
import json
import math
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

WORTEL = Path(__file__).resolve().parent.parent
ML_JS = WORTEL / "weerbot-modellen" / "weerbot-ml.js"
KOPPEL_JS = WORTEL / "weerbot-modellen" / "weerbot-ml-koppel.js"
MODELLEN = WORTEL / "weerbot-modellen" / "modellen" / "modellen.json"
ACTIVATIE = WORTEL / "weerbot-modellen" / "ml_activatie.json"
INDEX = WORTEL / "index.html"
DEEL9 = WORTEL / "weerbot-modellen" / "deel9_wekelijks.py"

fouten = []


def toets(naam, voorwaarde, uitleg=""):
    if voorwaarde:
        print(f"  ok   {naam}")
    else:
        print(f"  FOUT {naam}{': ' + uitleg if uitleg else ''}")
        fouten.append(naam)


def dichtbij(a, b, marge=1e-9):
    return a is not None and b is not None and abs(a - b) <= marge


def heeft_node():
    try:
        return subprocess.run(["node", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


def draai_node(script):
    with tempfile.TemporaryDirectory() as map_:
        js = Path(map_) / "test.js"
        js.write_text(script)
        r = subprocess.run(["node", str(js)], capture_output=True, text=True,
                           cwd=str(WORTEL))
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return json.loads(r.stdout)


def js_config():
    """CONFIG.steden uit index.html, zonder de rest van de pagina te draaien."""
    tekst = INDEX.read_text()
    i = tekst.index("const CONFIG = ")
    j = tekst.index(";\n", i)
    return json.loads(tekst[i + len("const CONFIG = "):j])


def koppel_omgeving():
    """Stubs waarmee weerbot-ml.js en weerbot-ml-koppel.js in node draaien.

    De koppel praat met localStorage, fetch, CONFIG en WeerbotML. Alle vier
    staan hier nep, zodat de rekenstappen los van browser en netwerk toetsbaar
    zijn. fetch leest modellen.json en klim_vandaag.json van schijf, precies
    zoals de app ze zou ophalen; alle andere aanroepen geven leegte terug, want
    de previous-runs zet de test zelf via _intern.zet.
    """
    cfg = js_config()
    return """
const fs = require("fs");
const opslag = new Map();
global.localStorage = {
  getItem: (k) => (opslag.has(k) ? opslag.get(k) : null),
  setItem: (k, v) => opslag.set(k, String(v)),
  removeItem: (k) => opslag.delete(k),
};
global.window = {};
// de koppel meldt bij het laden dat WeerbotML klaar is; dat mag niet tussen
// de JSON van de toets terechtkomen
console.log = function () {}; console.warn = function () {};
global.CONFIG = %s;
global.fetch = function (url) {
  const lokaal = ["modellen/modellen.json", "klim_vandaag.json",
                  "ml_activatie.json"].find((n) => url.endsWith(n));
  if (!lokaal) return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
  const pad = url.replace(/^.*weerbot-modellen\\//, "weerbot-modellen/");
  return Promise.resolve({ ok: true, json: () => Promise.resolve(
    JSON.parse(fs.readFileSync(pad, "utf8"))) });
};
const WORTEL = process.cwd();
const WeerbotML = require(WORTEL + "/weerbot-modellen/weerbot-ml.js");
global.WeerbotML = WeerbotML;
const Koppel = require(WORTEL + "/weerbot-modellen/weerbot-ml-koppel.js");
const IN = Koppel._intern;
const uit = {};
""" % json.dumps(cfg)


# ── 1. sleutels en modelnamen ────────────────────────────────────────────────

def toets_sleutels():
    print("sleutels")
    cfg = js_config()
    app_keys = [s["key"] for s in cfg["steden"]]
    koppel = KOPPEL_JS.read_text()
    keymap = dict(__import__("re").findall(
        r'(\w+):"(\w+)"',
        __import__("re").search(r"var KEYMAP = \{(.*?)\};", koppel, __import__("re").S).group(1)))
    modellen = json.loads(MODELLEN.read_text())

    toets("KEYMAP dekt elke stad uit CONFIG",
          all(k in keymap for k in app_keys),
          "ontbreekt: " + ", ".join(k for k in app_keys if k not in keymap))
    toets("KEYMAP heeft geen steden die CONFIG niet kent",
          all(k in app_keys for k in keymap),
          "onbekend: " + ", ".join(k for k in keymap if k not in app_keys))
    toets("elke ML-sleutel staat in modellen.json",
          all(v in modellen for v in keymap.values()),
          "ontbreekt: " + ", ".join(v for v in keymap.values() if v not in modellen))

    # De modellen die de koppel opvraagt moeten die van de training zijn.
    prevmod = __import__("re").search(r'var PREVMOD = "([^"]+)" \+\n\s*"([^"]+)"', koppel)
    gevraagd = set((prevmod.group(1) + prevmod.group(2)).split(","))
    training = set(__import__("re").findall(r'"(\w+)"',
                   __import__("re").search(r"PREV\s*=\s*\{(.*?)\}", DEEL9.read_text(),
                                           __import__("re").S).group(1)))
    training = {t for t in training if "_" in t}
    toets("de koppel vraagt dezelfde modellen op als deel9_wekelijks.py",
          gevraagd == training, f"koppel {sorted(gevraagd)} tegen training {sorted(training)}")

    # De korte namen moeten de p1-features van de modellen dekken.
    feats = set()
    for e in modellen.values():
        for var in ("ridge", "ridge_klim"):
            if isinstance(e, dict) and e.get(var):
                feats |= {f for f in e[var]["features"] if f.startswith("p1_")}
    kort = set(__import__("re").findall(r'var KORT = \[([^\]]+)\]', koppel)[0].replace('"', "").replace(" ", "").split(","))
    toets("KORT dekt elke p1-feature in modellen.json",
          {"p1_" + k for k in kort} >= feats,
          f"features {sorted(feats)} tegen KORT {sorted(kort)}")


# ── 2. rekenstappen tegen de trainingsdefinities ─────────────────────────────

def toets_rekenstappen():
    print("rekenstappen")
    if not heeft_node():
        print("  overgeslagen (node ontbreekt)")
        return

    # dagmax: minder dan twaalf uurwaarden is geen dag (deel9_wekelijks.dagmax).
    tijden = [f"2026-08-13T{u:02d}:00" for u in range(24)] + \
             [f"2026-08-14T{u:02d}:00" for u in range(11)]
    waarden = [10 + u * 0.5 for u in range(24)] + [5.0] * 11
    p1 = {"ifs": 20.0, "aifs": 21.0, "gfs": 19.0, "icon": 22.0, "gem": 18.0}
    p2 = {"ifs": 19.0, "aifs": 20.5, "gfs": 18.0, "icon": 21.0, "gem": 17.5}
    script = koppel_omgeving() + """
uit.dagmax = IN.dagmax(%s, %s);
uit.spreiding = IN.spreidingVan(%s);
uit.gem_vier = IN.gemiddelde({ifs:1, aifs:2, gfs:3, icon:4});
uit.gem_drie = IN.gemiddelde({ifs:1, aifs:2, gfs:3});
IN.zet({ nyc: { "2026-08-13": { p1: %s, p2: %s } } }, null);
uit.invoer = IN.invoerVoor("nyc", "2026-08-13",
  { eenheid: "\\u00b0F" }, { mlx: { lag2: 1.8 } });
uit.geen_prev = IN.invoerVoor("nyc", "2026-08-99", { eenheid: "\\u00b0C" }, { mlx: {} });
uit.uitDeltaF = IN.uitDelta(1, "\\u00b0F");
process.stdout.write(JSON.stringify(uit));
""" % (json.dumps(tijden), json.dumps(waarden), json.dumps(p1),
       json.dumps(p1), json.dumps(p2))
    r = draai_node(script)

    toets("dagmax neemt de volle dag", dichtbij(r["dagmax"].get("2026-08-13"), 21.5))
    toets("dagmax laat een dag met elf uren vallen", "2026-08-14" not in r["dagmax"])
    toets("spreiding is de steekproef-sd (ddof=1) van de training",
          dichtbij(r["spreiding"], statistics.stdev(p1.values()), 1e-12),
          f'{r["spreiding"]} tegen {statistics.stdev(p1.values())}')
    toets("het modelgemiddelde vraagt vier modellen", r["gem_vier"] is not None
          and r["gem_drie"] is None)

    r2r = statistics.mean(p1.values()) - statistics.mean(p2.values())
    toets("run2run is p1-gemiddelde min p2-gemiddelde",
          dichtbij(r["invoer"]["run2run"], r2r, 1e-12))
    toets("de lagfout wordt van °F naar °C gerekend",
          dichtbij(r["invoer"]["lagFout"], 1.8 * 5 / 9, 1e-12))
    toets("de p1-waarden gaan ongewijzigd door (de reeks staat al in °C)",
          r["invoer"]["p1"] == p1)
    toets("zonder previous-runs wordt er niet voorspeld", r["geen_prev"] is None)
    toets("een verschil in °F gaat maal 9/5, zonder de 32",
          dichtbij(r["uitDeltaF"], 1.8))


# ── 3. terugval bij ontbrekende invoer ──────────────────────────────────────

def toets_terugval():
    print("ontbrekende invoer")
    if not heeft_node():
        print("  overgeslagen (node ontbreekt)")
        return

    # De training kent twee conventies naast elkaar en de app moet ze allebei
    # volgen. _matrix in deel9_wekelijks.py zet een ontbrekende run2run op 0.0
    # en een ontbrekende p1 op mm_gem, en pas wat daarna nog ontbreekt krijgt
    # de mediaan. _laad begint lag2_err als nulvector. Alles wat hier niet
    # genoemd staat -- de aux-features, doy, klim -- gaat dus wel naar de
    # mediaan. Loopt dat uit elkaar, dan rekent de app anders dan de training
    # zonder dat er iets omvalt.
    deel9 = DEEL9.read_text()
    toets("deel9_wekelijks.py vult een ontbrekende run2run met nul",
          'if k == "run2run":' in deel9 and "v, 0.0)" in deel9)
    toets("deel9_wekelijks.py begint lag2_err als nulvector",
          "lag = np.zeros(len(rijen))" in deel9)
    toets("deel9_wekelijks.py vult een ontbrekende p1 met mm_gem",
          "if k in P1:" in deel9 and 'kol["mm_gem"][idx]' in deel9)

    modellen = json.loads(MODELLEN.read_text())
    stad = next(k for k, v in modellen.items()
                if isinstance(v, dict) and v.get("ridge")
                and "run2run" in v["ridge"]["features"]
                and v["ridge"]["med"][v["ridge"]["features"].index("run2run")] != 0)
    p = modellen[stad]["ridge"]
    med_r2r = p["med"][p["features"].index("run2run")]
    med_aux = p["med"][p["features"].index("rh_gem")]

    basis = {"ifs": 20.0, "aifs": 21.0, "gfs": 19.0, "icon": 22.0, "gem": 18.0}
    script = koppel_omgeving() + """
const M = JSON.parse(require("fs").readFileSync(WORTEL + "/weerbot-modellen/modellen/modellen.json", "utf8"));
const K = JSON.parse(require("fs").readFileSync(WORTEL + "/weerbot-modellen/klim_vandaag.json", "utf8"));
WeerbotML._laadDirect(M, K);
const p1 = %s;
const grond = { p1: p1, spreiding: 1.5, run2run: 0.3, lagFout: 0.5,
                aux: { rh: 70, bewolking: 50, wind: 15, instraling: 18, neerslag: 0 } };
function mu(extra) {
  const inv = Object.assign({}, grond, extra);
  if (extra && extra.aux) inv.aux = Object.assign({}, grond.aux, extra.aux);
  const v = WeerbotML.voorspel("%s", "2026-08-13", inv);
  return v ? v.mu : null;
}
uit.r2r_weg    = mu({ run2run: null });
uit.r2r_nul    = mu({ run2run: 0 });
uit.r2r_med    = mu({ run2run: %s });
uit.lag_weg    = mu({ lagFout: null });
uit.lag_nul    = mu({ lagFout: 0 });
uit.aux_weg    = mu({ aux: { rh: null } });
uit.aux_med    = mu({ aux: { rh: %s } });
uit.p1_weg     = mu({ p1: { aifs: 21.0, gfs: 19.0, icon: 22.0, gem: 18.0 } });
process.stdout.write(JSON.stringify(uit));
""" % (json.dumps(basis), stad, json.dumps(med_r2r), json.dumps(med_aux))
    r = draai_node(script)

    toets("een ontbrekende run2run telt als nul, zoals in de training",
          dichtbij(r["r2r_weg"], r["r2r_nul"], 1e-9),
          f'{r["r2r_weg"]} tegen {r["r2r_nul"]}')
    toets("en dus niet als de mediaan",
          not dichtbij(r["r2r_weg"], r["r2r_med"], 1e-9),
          f"stad {stad}, mediaan {med_r2r}")
    toets("een ontbrekende lagfout telt ook als nul",
          dichtbij(r["lag_weg"], r["lag_nul"], 1e-9))
    toets("een ontbrekende aux-feature valt wel op de mediaan terug",
          dichtbij(r["aux_weg"], r["aux_med"], 1e-9),
          f'{r["aux_weg"]} tegen {r["aux_med"]}')
    toets("een ontbrekend model valt op het modelgemiddelde terug",
          r["p1_weg"] is not None)


# ── 4. schaduwlogboek en rapport ─────────────────────────────────────────────

def toets_schaduw():
    print("schaduwlogboek")
    if not heeft_node():
        print("  overgeslagen (node ontbreekt)")
        return
    script = koppel_omgeving() + """
// drie horizonnen op dezelfde doeldag: v1 hield er één over, v2 alle drie
WeerbotML.schaduw("NYC", "2026-08-13", 0, 80.0, 81.0, 1.8, "\\u00b0F");
WeerbotML.schaduw("NYC", "2026-08-13", 1, 79.0, 82.0, 1.8, "\\u00b0F");
WeerbotML.schaduw("NYC", "2026-08-13", 2, 78.0, 83.0, 1.8, "\\u00b0F");
WeerbotML.schaduw("AMS", "2026-08-13", 1, 20.0, 21.0, 1.0, "\\u00b0C");
uit.rapport = WeerbotML.schaduwRapport({ NYC: { "2026-08-13": 80.0 },
                                         AMS: { "2026-08-13": 20.0 } });
uit.crps_scherp = WeerbotML.crpsNormaal(0, 1e-12, 2.0);
uit.crps = WeerbotML.crpsNormaal(0, 1, 0);
process.stdout.write(JSON.stringify(uit));
"""
    r = draai_node(script)["rapport"]

    toets("alle drie de horizonnen blijven staan",
          sorted(r["perHorizon"].keys()) == ["0", "1", "2"],
          str(sorted(r["perHorizon"].keys())))
    toets("het aantal telt elke stad-horizon apart", r["n"] == 4, str(r["n"]))
    # NYC horizon 1: nieuw 82 tegen echt 80 = 2 °F = 1,111 °C
    toets("°F wordt naar °C gerekend voor het gemiddelde",
          dichtbij(r["perHorizon"]["1"]["maeNieuw"],
                   ((82 - 80) * 5 / 9 + 1.0) / 2, 1e-9),
          str(r["perHorizon"]["1"]["maeNieuw"]))
    toets("de bias houdt zijn teken", r["perHorizon"]["2"]["bias"] > 0)
    toets("per stad staat de eenheid erbij",
          r["perStad"]["NYC"]["eenheid"] == "°F")
    toets("per stad is er ook een uitsplitsing per horizon",
          sorted(r["perStad"]["NYC"]["perHorizon"].keys()) == ["0", "1", "2"])
    toets("de 80%-dekking wordt gemeten", r["dekking80"] is not None)
    toets("CRPS staat in het rapport", r["crps"] is not None)


def toets_crps():
    print("CRPS")
    if not heeft_node():
        print("  overgeslagen (node ontbreekt)")
        return
    script = koppel_omgeving() + """
uit.scherp = WeerbotML.crpsNormaal(0, 1e-12, 2.0);
uit.standaard = WeerbotML.crpsNormaal(0, 1, 0);
uit.eenheid = WeerbotML.crpsNormaal(0, 2, 0);
process.stdout.write(JSON.stringify(uit));
"""
    r = draai_node(script)
    toets("bij sigma naar nul loopt CRPS naar de absolute fout",
          dichtbij(r["scherp"], 2.0, 1e-6), str(r["scherp"]))
    # CRPS(N(0,1), 0) = 2*phi(0) - 1/sqrt(pi)
    verwacht = 2 / math.sqrt(2 * math.pi) - 1 / math.sqrt(math.pi)
    toets("CRPS van de standaardnormaal in het midden klopt",
          dichtbij(r["standaard"], verwacht, 1e-9),
          f'{r["standaard"]} tegen {verwacht}')
    toets("CRPS schaalt lineair met sigma",
          dichtbij(r["eenheid"], 2 * verwacht, 1e-9))


# ── 5. activatiepoort ────────────────────────────────────────────────────────

def toets_activatie():
    print("activatie")
    cfg = json.loads(ACTIVATIE.read_text())
    toets("het activatiebestand zet niets aan", not cfg.get("aan"),
          "aan: " + json.dumps(cfg.get("aan")))
    toets("LINEAIR staat op de nooit-lijst", "LINEAIR" in cfg.get("nooit_labels", []))

    if not heeft_node():
        print("  overgeslagen (node ontbreekt)")
        return
    modellen = json.loads(MODELLEN.read_text())
    ml_stad = next(k for k, v in modellen.items()
                   if isinstance(v, dict) and v.get("label") == "ML")
    lin_stad = next(k for k, v in modellen.items()
                    if isinstance(v, dict) and v.get("label") == "LINEAIR")
    script = koppel_omgeving() + """
const M = JSON.parse(require("fs").readFileSync("weerbot-modellen/modellen/modellen.json", "utf8"));
WeerbotML._laadDirect(M, null);
IN.zet({}, null);
uit.zonder_bestand = IN.magActief("%s", 1);
IN.zet({}, { nooit_labels: ["LINEAIR"], aan: {} });
uit.leeg = IN.magActief("%s", 1);
IN.zet({}, { nooit_labels: ["LINEAIR"], aan: { "%s": { "1": true } } });
uit.aan = IN.magActief("%s", 1);
uit.andere_horizon = IN.magActief("%s", 0);
IN.zet({}, { nooit_labels: ["LINEAIR"], aan: { "%s": { "1": true } } });
uit.lineair = IN.magActief("%s", 1);
process.stdout.write(JSON.stringify(uit));
""" % (ml_stad, ml_stad, ml_stad, ml_stad, ml_stad, lin_stad, lin_stad)
    r = draai_node(script)

    toets("zonder activatiebestand blijft alles uit", r["zonder_bestand"] is False)
    toets("een leeg bestand zet niets aan", r["leeg"] is False)
    toets("een aangezette stad-horizon gaat aan", r["aan"] is True)
    toets("een andere horizon van dezelfde stad blijft uit",
          r["andere_horizon"] is False)
    toets("een LINEAIR-stad gaat niet aan, ook niet als hij op de lijst staat",
          r["lineair"] is False, f"stad {lin_stad}")


def main():
    print("ML-koppeling")
    toets_sleutels()
    toets_rekenstappen()
    toets_terugval()
    toets_schaduw()
    toets_crps()
    toets_activatie()
    if fouten:
        print(f"\n{len(fouten)} toets(en) mislukt: " + ", ".join(fouten))
        return 1
    print("\nalles in orde")
    return 0


if __name__ == "__main__":
    sys.exit(main())
