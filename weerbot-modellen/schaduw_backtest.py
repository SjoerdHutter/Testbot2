#!/usr/bin/env python3
"""Walk-forward backtest van de ML-modellen tegen de rekenkern van de app.

  python3 weerbot-modellen/schaduw_backtest.py            lead 1, alle steden
  python3 weerbot-modellen/schaduw_backtest.py --lead 2   op de p2-reeks
  python3 weerbot-modellen/schaduw_backtest.py --stad nyc

Waarom dit bestand er is. Het schaduwlogboek in de browser moest zestig dagen
vollopen voordat er iets over de ML-modellen te zeggen viel, en het begon op
13 augustus 2026 opnieuw omdat de invoer ervoor uit de verkeerde API kwam.
Maar de gegevens waarop die vraag te beantwoorden is staan al in de repository:
features_alle.csv heeft 44.668 stad-dagen met bruikbare p1-reeks en waarneming,
over ruim tweeenhalf jaar. Dat is per stad zo'n negenhonderd dagen tegenover de
zestig waar op gewacht werd.

Waarom je die niet zomaar mag gebruiken. modellen.json is door refit() in
deel9_wekelijks.py gefit op precies dezelfde rijen: `tr` is daar elke rij met
een eindige mm_gem en doel, dus de hele geschiedenis. De modellen op die
geschiedenis nakijken is in de eigen trainingsdata kijken, en dat vleit. Deze
backtest fit daarom opnieuw, wekelijks, steeds alleen op de dagen ervoor, en
voorspelt de week erna. Dat is dezelfde cadans als de echte hertraining op
maandag.

De referentie is niet verzonnen maar geleend: kalibratie.walk_forward is de
walk-forward van de app zelf en geeft per dag de voorspelling van de rekenkern
terug (yhat_per_dag). Beide kanten worden dus op dezelfde dagen en met dezelfde
regel "alleen kennis van daarvoor" gescoord.

Wat deze backtest niet kan. Twee dingen blijven aan de live-reeks hangen:
of previous-runs-api de p1-waarden ook voor de kómende dagen levert, en of die
dan gelijk zijn aan de waarden die je achteraf voor diezelfde dag terugkrijgt.
Dat is een vraag over de API en niet over het model; het schaduwlogboek
beantwoordt hem binnen een dag. Zie REVIEW.md.
"""
import argparse
import csv
import json
import math
import sys
from datetime import date
from pathlib import Path

HIER = Path(__file__).resolve().parent
WORTEL = HIER.parent
sys.path.insert(0, str(WORTEL / "bot"))
import kalibratie as K   # noqa: E402  (walk_forward: de referentie van de app)

P1 = ["p1_ifs", "p1_aifs", "p1_gfs", "p1_icon", "p1_gem"]
AUXK = ["rh_gem", "bewolking_gem", "wind_max", "instraling_som", "neerslag_som"]
FEATS = P1 + ["mm_spreiding", "run2run", "doy_sin", "doy_cos", "lag2_err"] + AUXK
KORT = ["ifs", "aifs", "gfs", "icon", "gem"]
ALPHA = 1.0           # Ridge(alpha=1.0) in deel9_wekelijks.refit
HERFIT_OM = 7         # dagen; de echte hertraining draait wekelijks
MIN_TRAIN = 180       # geen model op minder; de eerste maanden zijn opwarmen
Z80 = 1.2816


# ── de gegevens ──────────────────────────────────────────────────────────────

def getal(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def laad_rijen(lead):
    """Per stad de featurerijen op datumvolgorde.

    lead 1 gebruikt de p1-kolommen, lead 2 de p2-kolommen. De modellen zijn op
    p1 gefit; lead 2 meet dus wat er gebeurt als je ze buiten hun
    trainingsafstand gebruikt, en dat is precies waarom ml_activatie.json per
    horizon gaat.
    """
    kop = "p1_" if lead == 1 else "p2_"
    per = {}
    for r in csv.DictReader(open(HIER / "features_alle.csv")):
        v = {m: getal(r[kop + m]) for m in KORT}
        beschikbaar = [x for x in v.values() if x is not None]
        rij = {"datum": r["datum"], "fc": {m: x for m, x in v.items() if x is not None},
               "doel": getal(r["doel"]),
               "doy_sin": getal(r["doy_sin"]), "doy_cos": getal(r["doy_cos"])}
        for a in AUXK:
            rij[a] = getal(r[a])
        if len(beschikbaar) >= 4:
            rij["mm_gem"] = sum(beschikbaar) / len(beschikbaar)
            rij["mm_spreiding"] = pstdev1(beschikbaar)
        else:
            rij["mm_gem"] = rij["mm_spreiding"] = None
        # run2run hoort bij de laatste ronde tegenover de vorige. Op lead 1 is
        # dat p1 tegen p2 en staat hij in het bestand; op lead 2 zou je p3
        # nodig hebben en die is er niet, dus dan ontbreekt hij -- wat in de
        # training als nul is gefit.
        rij["run2run"] = getal(r["run2run"]) if lead == 1 else None
        per.setdefault(r["stad"], []).append(rij)
    for rijen in per.values():
        rijen.sort(key=lambda x: x["datum"])
    return per


def pstdev1(v):
    """Steekproef-sd, ddof=1, zoals np.std(p1v, ddof=1) in deel9_wekelijks."""
    if len(v) < 2:
        return None
    g = sum(v) / len(v)
    return math.sqrt(sum((x - g) ** 2 for x in v) / (len(v) - 1))


def zet_lag2(rijen):
    """lag2_err zoals _laad in deel9_wekelijks: de fout van het kale
    modelgemiddelde van twee dagen terug, anders die van drie, anders nul."""
    fout = [None] * len(rijen)
    for i, r in enumerate(rijen):
        if r["mm_gem"] is not None and r["doel"] is not None:
            fout[i] = r["doel"] - r["mm_gem"]
    for i, r in enumerate(rijen):
        r["lag2_err"] = 0.0
        for dd in (2, 3):
            if i - dd >= 0 and fout[i - dd] is not None:
                r["lag2_err"] = fout[i - dd]
                break


def laad_klim():
    kl = {}
    pad = HIER / "klim_features.csv"
    if pad.exists():
        for r in csv.DictReader(open(pad)):
            kl[(r["key"], r["datum"])] = getal(r["klimG"])
    return kl


def vector(rij, feats, klim, stad, med=None):
    """Een featurevector volgens _matrix in deel9_wekelijks: een ontbrekende p1
    wordt mm_gem, een ontbrekende run2run wordt nul, en pas wat daarna nog
    ontbreekt krijgt de mediaan."""
    x = []
    for f in feats:
        if f in P1:
            v = rij["fc"].get(f[3:])
            v = v if v is not None else rij["mm_gem"]
        elif f == "run2run":
            v = rij["run2run"] if rij["run2run"] is not None else 0.0
        elif f == "klim":
            v = klim.get((stad, rij["datum"]))
        else:
            v = rij.get(f)
        if v is None and med is not None:
            v = med[len(x)]
        x.append(v)
    return x if all(v is not None for v in x) else None


# ── ridge, precies zoals sklearn hem hier zou fitten ─────────────────────────

class Ophoper:
    """Lopende sommen waaruit de ridge op elk moment te sluiten valt.

    deel9_wekelijks standaardiseert eerst met de mu en sd van het trainingsdeel
    en fit dan Ridge(alpha=1.0) met intercept. Op gestandaardiseerde kolommen is
    het gemiddelde per constructie nul, dus komt dat neer op
    (Z'Z + alpha*I) b = Z'(y - ybar) met intercept ybar. Dat is uit deze sommen
    exact te reconstrueren, zonder de rijen te bewaren en zonder numpy.
    """

    def __init__(self, p):
        self.p = p
        self.n = 0
        self.s1 = [0.0] * p
        self.s2 = [[0.0] * p for _ in range(p)]
        self.sy = [0.0] * p
        self.sy0 = 0.0

    def voeg_toe(self, x, y):
        self.n += 1
        self.sy0 += y
        for j in range(self.p):
            self.s1[j] += x[j]
            self.sy[j] += x[j] * y
            rij = self.s2[j]
            for k in range(j, self.p):
                rij[k] += x[j] * x[k]

    def fit(self):
        n = self.n
        if n < 2:
            return None
        mu = [s / n for s in self.s1]
        sd = []
        for j in range(self.p):
            var = self.s2[j][j] / n - mu[j] * mu[j]
            s = math.sqrt(var) if var > 1e-12 else 0.0
            sd.append(s if s > 0 else 1.0)
        A, b = [], []
        for j in range(self.p):
            rij = []
            for k in range(self.p):
                s2 = self.s2[j][k] if k >= j else self.s2[k][j]
                rij.append((s2 - n * mu[j] * mu[k]) / (sd[j] * sd[k]))
            rij[j] += ALPHA
            A.append(rij)
            b.append((self.sy[j] - mu[j] * self.sy0) / sd[j])
        co = K.los_op(A, b)
        if co is None:
            return None
        return {"mu": mu, "sd": sd, "coef": co, "intercept": self.sy0 / n}


def voorspel(par, x):
    y = par["intercept"]
    for j, v in enumerate(x):
        y += par["coef"][j] * (v - par["mu"][j]) / par["sd"][j]
    return y


# ── scoren ───────────────────────────────────────────────────────────────────

def crps_normaal(mu, sigma, y):
    if sigma is None or sigma <= 0:
        return abs(mu - y)
    z = (y - mu) / sigma
    phi = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    Phi = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return sigma * (z * (2 * Phi - 1) + 2 * phi - 1 / math.sqrt(math.pi))


def gem(v):
    return sum(v) / len(v) if v else None


def stad_backtest(stad, rijen, model, klim, lead):
    """Walk forward voor een stad. Geeft None als er te weinig materiaal is."""
    variant = model.get("variant")
    feats = list(FEATS)
    if variant == "ridge_klim" and model.get("ridge_klim"):
        feats = feats + ["klim"]
    elif not model.get("ridge"):
        return None                     # LINEAIR en GEPOOLD: geen eigen ridge

    zet_lag2(rijen)

    # 1. de referentie: de rekenkern van de app, walk forward, via kalibratie.py
    records = [(date.fromisoformat(r["datum"]).toordinal(), r["fc"], r["doel"])
               for r in rijen if r["doel"] is not None and r["fc"]]
    if len(records) < MIN_TRAIN + K.BURN_EVALUATIE:
        return None
    basis = K.walk_forward(records, lag_dagen=lead + 1)
    yhat_basis = basis.get("yhat_per_dag") or {}

    # 2. de ML-kant: wekelijks hertrainen op uitsluitend de dagen ervoor
    op = Ophoper(len(feats))
    par = None
    sinds = 0
    ml_per_dag = {}
    for r in rijen:
        x = vector(r, feats, klim, stad)
        if r["doel"] is None or x is None:
            continue
        if par is not None:
            ml_per_dag[date.fromisoformat(r["datum"]).toordinal()] = voorspel(par, x)
        op.voeg_toe(x, r["doel"])
        sinds += 1
        if op.n >= MIN_TRAIN and sinds >= HERFIT_OM:
            nieuw = op.fit()
            if nieuw:
                par = nieuw
            sinds = 0

    # 3. scoren op de dagen die allebei hebben
    ngr = model.get("ngr") or {}
    sig_per_dag = {}
    for r in rijen:
        o = date.fromisoformat(r["datum"]).toordinal()
        if r["mm_spreiding"] is not None and ngr.get("c") is not None:
            sig_per_dag[o] = math.sqrt(ngr["c"] ** 2 + (ngr["d"] * r["mm_spreiding"]) ** 2)
    echt = {date.fromisoformat(r["datum"]).toordinal(): r["doel"]
            for r in rijen if r["doel"] is not None}

    fb, fm, bias, crps, dek = [], [], [], [], []
    for o in sorted(set(yhat_basis) & set(ml_per_dag) & set(echt)):
        y = echt[o]
        fb.append(abs(yhat_basis[o] - y))
        fm.append(abs(ml_per_dag[o] - y))
        bias.append(ml_per_dag[o] - y)
        s = sig_per_dag.get(o)
        if s:
            crps.append(crps_normaal(ml_per_dag[o], s, y))
            dek.append(1.0 if abs(ml_per_dag[o] - y) <= Z80 * s else 0.0)
    if len(fb) < 45:
        return None
    return {"label": model.get("label"), "variant": variant, "n": len(fb),
            "mae_basis": gem(fb), "mae_ml": gem(fm), "winst": gem(fb) - gem(fm),
            "bias_ml": gem(bias), "crps_ml": gem(crps), "dekking80": gem(dek),
            "eerste": date.fromordinal(min(set(yhat_basis) & set(ml_per_dag) & set(echt))).isoformat(),
            "laatste": date.fromordinal(max(set(yhat_basis) & set(ml_per_dag) & set(echt))).isoformat()}


def oordeel(u, drempels):
    """Haalt deze stad-horizon de drempels uit ml_activatie.json?"""
    lo, hi = drempels["dekking80_tussen"]
    redenen = []
    if u["n"] < drempels["min_n"]:
        redenen.append("te weinig dagen")
    if u["winst"] < drempels["min_mae_winst_c"]:
        redenen.append(f'winst {u["winst"]:+.3f}')
    if abs(u["bias_ml"]) > drempels["max_abs_bias_c"]:
        redenen.append(f'bias {u["bias_ml"]:+.3f}')
    if u["dekking80"] is not None and not (lo <= u["dekking80"] <= hi):
        redenen.append(f'dekking {u["dekking80"]*100:.0f}%')
    return redenen


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lead", type=int, default=1, choices=(1, 2))
    p.add_argument("--stad")
    p.add_argument("--schrijf", action="store_true",
                   help="uitkomst wegschrijven naar monitoring/backtest_lead<N>.json")
    p.add_argument("--controleer", action="store_true",
                   help="vergelijken met de vorige uitkomst en de omslagen melden")
    a = p.parse_args()
    vorig = {}
    pad_vorig = HIER / "monitoring" / f"backtest_lead{a.lead}.json"
    if a.controleer and pad_vorig.exists():
        vorig = json.loads(pad_vorig.read_text())

    modellen = json.loads((HIER / "modellen" / "modellen.json").read_text())
    activatie = json.loads((HIER / "ml_activatie.json").read_text())
    drempels = activatie["drempels"]
    klim = laad_klim()
    per_stad = laad_rijen(a.lead)

    print(f"Walk-forward backtest, lead {a.lead}, hertraining elke {HERFIT_OM} dagen "
          f"op minimaal {MIN_TRAIN} dagen ervoor\n")
    print(f"{'stad':16s} {'label':8s} {'n':>5s} {'kern':>6s} {'ML':>6s} "
          f"{'winst':>7s} {'bias':>7s} {'CRPS':>6s} {'dekk':>6s}  oordeel")

    uit, haalt, valt = {}, [], []
    for stad in sorted(per_stad):
        if a.stad and stad != a.stad:
            continue
        model = modellen.get(stad)
        if not isinstance(model, dict):
            continue
        r = stad_backtest(stad, per_stad[stad], model, klim, a.lead)
        if not r:
            continue
        uit[stad] = r
        redenen = oordeel(r, drempels)
        if model.get("label") in activatie.get("nooit_labels", []):
            merk = "label " + model["label"]
        elif redenen:
            merk = ", ".join(redenen)
            valt.append(stad)
        elif r["variant"] == "ridge_klim":
            # De klim-term komt uit klim_features.csv, en refit() rekent die uit
            # met stagea-modellen die op de hele geschiedenis zijn gefit. Deze
            # steden kijken dus via die ene feature in hun eigen toekomst. De
            # hertraining hier is wel walk forward, de klimwaarde niet.
            merk = "haalt ze, maar klim lekt"
            r["klim_lekt"] = True
            valt.append(stad)
        else:
            merk = "HAALT DE DREMPELS"
            haalt.append(stad)
        d = r["dekking80"]
        print(f'{stad:16s} {str(r["label"]):8s} {r["n"]:5d} {r["mae_basis"]:6.3f} '
              f'{r["mae_ml"]:6.3f} {r["winst"]:+7.3f} {r["bias_ml"]:+7.3f} '
              f'{(r["crps_ml"] or 0):6.3f} {(d*100 if d else 0):5.1f}%  {merk}')

    if uit:
        n = len(uit)
        print(f"\n{n} steden met een eigen ridge en genoeg materiaal")
        print(f"  gemiddelde MAE kern {gem([r['mae_basis'] for r in uit.values()]):.3f} "
              f"tegen ML {gem([r['mae_ml'] for r in uit.values()]):.3f} "
              f"({gem([r['winst'] for r in uit.values()]):+.3f})")
        print(f"  haalt de drempels: {len(haalt)}"
              + (" · " + ", ".join(haalt) if haalt else ""))
        print(f"  haalt ze niet   : {len(valt)}")

    if a.controleer:
        # Wat de tweewekelijkse controle moet opleveren is niet de tabel maar de
        # verandering: een stad die de drempels nu wel haalt en aangezet kan
        # worden, en -- belangrijker -- een stad die al aanstaat maar ze niet
        # meer haalt. Dat tweede is een reden om iets uit te zetten en dat mag
        # niet in een tabel van 34 regels verdwijnen.
        eerder = set(vorig.get("haalt_drempels") or [])
        nu = set(haalt)
        aan = {s for s, per in (activatie.get("aan") or {}).items()
               if per.get(str(a.lead)) is True}
        print(f"\n── controle lead {a.lead} ──")
        nieuw = sorted(nu - eerder - aan)
        weg = sorted(aan - nu)
        if nieuw:
            print("  NIEUW: haalt de drempels en staat nog uit: " + ", ".join(nieuw))
            for s in nieuw:
                r = uit[s]
                print(f'     {s}: winst {r["winst"]:+.3f} °C over {r["n"]} dagen, '
                      f'bias {r["bias_ml"]:+.3f}, dekking {(r["dekking80"] or 0)*100:.0f}%')
            print("     Aanzetten in weerbot-modellen/ml_activatie.json, daarna")
            print("     python3 weerbot-modellen/controleer_schil.py --zet")
        if weg:
            print("  LET OP: staat aan maar haalt de drempels niet meer: " + ", ".join(weg))
            for s in weg:
                r = uit.get(s)
                if r:
                    print(f'     {s}: ' + ", ".join(oordeel(r, drempels)))
            print("     Overweeg uit te zetten in ml_activatie.json.")
        if not nieuw and not weg:
            print("  geen omslagen sinds de vorige controle")

    if a.schrijf:
        map_ = HIER / "monitoring"
        map_.mkdir(exist_ok=True)
        pad = map_ / f"backtest_lead{a.lead}.json"
        pad.write_text(json.dumps(
            {"gegenereerd": date.today().isoformat(), "lead": a.lead,
             "herfit_om": HERFIT_OM, "min_train": MIN_TRAIN,
             "steden": uit, "haalt_drempels": haalt}, indent=1) + "\n")
        print(f"\ngeschreven naar {pad.relative_to(WORTEL)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
