/* weerbot-ml.js · client-rekenkern voor de per-stad ML-modellen.
 *
 * Gebruik in index.html:
 *   <script src="weerbot-modellen/weerbot-ml.js"></script>
 *   WeerbotML.init({ basis: "weerbot-modellen/" }).then(...)
 *   const uit = WeerbotML.voorspel("nyc", "2026-07-30", invoer);
 *
 * invoer (alles in °C, ontbrekend mag null zijn):
 *   { p1: { ifs, aifs, gfs, icon, gem },   // daghoogste per modelsysteem
 *     spreiding,                            // std over de beschikbare p1's
 *     run2run,                              // mm huidige run − mm vorige run
 *     lagFout,                              // waarneming(t−2) − mm(t−2)
 *     aux: { rh, bewolking, wind, instraling, neerslag } }
 *
 * Retour: { mu, sigma, variant, mm } in °C, of null als er geen model of
 * onvoldoende invoer is (de app houdt dan zijn eigen berekening).
 * Terugvalketen: ridge_klim → ridge → ref_lin → null.
 */
(function (wortel) {
  "use strict";
  /* Weerbot 2 deelt de herkomst met de eerste versie; SLEUTEL geeft elke
   * opslagnaam een eigen voorvoegsel zodat de twee apps elkaar niet overschrijven. */
  function SLEUTEL(naam) {
    return (window.WEERBOT2_OPSLAG ? window.WEERBOT2_OPSLAG(naam) : naam);
  }

  const S = { modellen: null, klim: null };

  const NAAMMAP = {
    p1_ifs: (i, mm) => eersteGeldig(i.p1 && i.p1.ifs, mm),
    p1_aifs: (i, mm) => eersteGeldig(i.p1 && i.p1.aifs, mm),
    p1_gfs: (i, mm) => eersteGeldig(i.p1 && i.p1.gfs, mm),
    p1_icon: (i, mm) => eersteGeldig(i.p1 && i.p1.icon, mm),
    p1_gem: (i, mm) => eersteGeldig(i.p1 && i.p1.gem, mm),
    mm_spreiding: (i) => i.spreiding,
    /* Deze twee vallen terug op nul en niet op de mediaan, anders dan elke
       andere feature hieronder. Dat is geen slordigheid maar de conventie van
       de training, en de twee moeten gelijk lopen: _matrix in
       deel9_wekelijks.py zet een ontbrekende run2run op 0.0 vóórdat het de
       medianen uitrekent, en _laad begint lag2_err als een nulvector die
       alleen wordt gevuld waar er een fout van twee of drie dagen terug is.
       Een ontbrekende waarde is in beide gevallen dus als nul gefit, en dan is
       nul ook wat hij hier moet zijn. Het gaat om 0,1% van de trainingsrijen
       voor run2run. bot/test_ml.py legt dit vast tegen deel9_wekelijks.py. */
    run2run: (i) => eersteGeldig(i.run2run, 0),
    lag2_err: (i) => eersteGeldig(i.lagFout, 0),
    rh_gem: (i) => i.aux && i.aux.rh,
    bewolking_gem: (i) => i.aux && i.aux.bewolking,
    wind_max: (i) => i.aux && i.aux.wind,
    instraling_som: (i) => i.aux && i.aux.instraling,
    neerslag_som: (i) => i.aux && i.aux.neerslag,
  };

  function eersteGeldig(a, b) { return isGetal(a) ? a : b; }
  function isGetal(x) { return typeof x === "number" && isFinite(x); }

  function doy(datumISO) {
    const d = new Date(datumISO + "T00:00:00Z");
    const begin = Date.UTC(d.getUTCFullYear(), 0, 0);
    const n = (d.getTime() - begin) / 86400000;
    return (n / 365.25) * 2 * Math.PI;
  }

  function mmVan(p1) {
    if (!p1) return null;
    const v = ["ifs", "aifs", "gfs", "icon", "gem"].map((k) => p1[k]).filter(isGetal);
    if (v.length < 4) return null;
    return v.reduce((a, b) => a + b, 0) / v.length;
  }

  function bouwVector(params, stadKey, datumISO, invoer) {
    const mm = mmVan(invoer.p1);
    if (!isGetal(mm)) return null;
    const hoek = doy(datumISO);
    const x = [];
    for (let f = 0; f < params.features.length; f++) {
      const naam = params.features[f];
      let w;
      if (naam === "doy_sin") w = Math.sin(hoek);
      else if (naam === "doy_cos") w = Math.cos(hoek);
      else if (naam === "klim") w = klimWaarde(stadKey, datumISO, "klim");
      else if (NAAMMAP[naam]) w = NAAMMAP[naam](invoer, mm);
      if (!isGetal(w)) {
        if (naam === "klim") return null;          // klim verplicht voor ridge_klim
        w = params.med && isGetal(params.med[f]) ? params.med[f] : 0;
      }
      x.push(w);
    }
    return { x, mm };
  }

  function ridgeUit(params, stadKey, datumISO, invoer) {
    const b = bouwVector(params, stadKey, datumISO, invoer);
    if (!b) return null;
    let mu = params.intercept;
    for (let f = 0; f < b.x.length; f++) {
      const sd = params.sd[f] || 1;
      mu += params.coef[f] * ((b.x[f] - params.mu[f]) / sd);
    }
    return { mu, mm: b.mm };
  }

  function klimWaarde(stadKey, datumISO, veld) {
    const s = S.klim && S.klim.steden && S.klim.steden[stadKey];
    const r = s && s[datumISO];
    return r && isGetal(r[veld]) ? r[veld] : null;
  }

  function erf(x) {
    // Abramowitz en Stegun 7.1.26, nauwkeurig tot ~1,5e-7
    const t = 1 / (1 + 0.3275911 * Math.abs(x));
    const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t -
                    0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
    return x >= 0 ? y : -y;
  }
  function Phi(z) { return 0.5 * (1 + erf(z / Math.SQRT2)); }

  const WeerbotML = {
    init: function (opties) {
      const basis = (opties && opties.basis) || "weerbot-modellen/";
      const fx = (opties && opties.fetchFn) || ((u) => fetch(u, { cache: "no-store" }).then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status + " bij " + u);
        return r.json();
      }));
      return fx(basis + "modellen/modellen.json").then(function (m) {
        S.modellen = m;
        return fx(basis + "klim_vandaag.json").catch(() => null);
      }).then(function (k) {
        S.klim = k;
        return { modellen: S.modellen ? Object.keys(S.modellen).length : 0,
                 klimSteden: k && k.steden ? Object.keys(k.steden).length : 0 };
      });
    },

    _laadDirect: function (modellen, klim) { S.modellen = modellen; S.klim = klim; },

    label: function (stadKey) {
      const e = S.modellen && S.modellen[stadKey];
      return e ? e.label : null;
    },

    voorspel: function (stadKey, datumISO, invoer) {
      const e = S.modellen && S.modellen[stadKey];
      if (!e || !invoer) return null;
      let uit = null, variant = null;
      if (e.variant === "ridge_klim" && e.ridge_klim) {
        uit = ridgeUit(e.ridge_klim, stadKey, datumISO, invoer);
        if (uit) variant = "ridge_klim";
      }
      if (!uit && e.ridge) {
        uit = ridgeUit(e.ridge, stadKey, datumISO, invoer);
        if (uit) variant = "ridge";
      }
      if (!uit && e.label === "GEPOOLD") {
        const p = klimWaarde(stadKey, datumISO, "pooled");
        const mm = mmVan(invoer.p1);
        if (isGetal(p)) { uit = { mu: p, mm: mm }; variant = "pooled"; }
      }
      if (!uit && e.ref_lin) {
        const mm = mmVan(invoer.p1);
        if (!isGetal(mm)) return null;
        const lag = eersteGeldig(invoer.lagFout, 0);
        uit = { mu: e.ref_lin.a + e.ref_lin.b * mm + e.ref_lin.g * lag, mm: mm };
        variant = "ref_lin";
      }
      if (!uit) return null;
      let sigma = null;
      if (e.ngr && isGetal(e.ngr.c) && isGetal(e.ngr.d) && isGetal(invoer.spreiding)) {
        sigma = Math.sqrt(e.ngr.c * e.ngr.c +
                          Math.pow(e.ngr.d * invoer.spreiding, 2));
      }
      return { mu: uit.mu, sigma: sigma, variant: variant, mm: uit.mm };
    },

    kansen: function (mu, sigma, brackets) {
      if (!isGetal(mu) || !isGetal(sigma) || sigma <= 0) return null;
      return brackets.map(function (b) {
        const lo = isGetal(b.lo) ? Phi((b.lo - mu) / sigma) : 0;
        const hi = isGetal(b.hi) ? Phi((b.hi - mu) / sigma) : 1;
        return Math.max(0, hi - lo);
      });
    },

    /* Schaduwlogboek. v2 legt twee dingen vast die v1 miste.
     *
     * De horizon. v1 schreef per stad per doeldag één regel, dus de
     * voorspelling van overmorgen werd de volgende dag overschreven door die
     * van morgen en een dag later door die van vandaag. Wat er overbleef was
     * altijd lead 0, terwijl de app op drie horizonnen voorspelt en de
     * ML-modellen op lead 1 zijn getraind. Een vergelijking per horizon was
     * daarmee onmogelijk, en juist die is nodig: activeren gebeurt per stad en
     * horizon, niet in één keer voor alles.
     *
     * De sigma en de eenheid. Zonder sigma valt er alleen MAE te rekenen en
     * blijft de band ongetoetst; zonder eenheid telt een graad Fahrenheit even
     * zwaar mee als een graad Celsius in het gemiddelde over alle steden.
     * Beide staan er nu bij, en het rapport rekent alles naar °C.
     *
     * Nieuwe sleutel, want de vorm verschilt. Het oude logboek blijft staan
     * maar wordt niet gelezen: die regels zijn gemaakt met de p1-invoer uit de
     * ensemble-API en dus met andere features dan de modellen kennen. Als
     * vergelijkingsmateriaal zijn ze onbruikbaar. */
    schaduw: function (stadKey, datumISO, horizon, oud, nieuw, sigma, eenheid) {
      try {
        const K = SLEUTEL("weerbot-ml-schaduw-v2");
        const d = JSON.parse(localStorage.getItem(K) || "{}");
        const s = d[stadKey] = d[stadKey] || { e: eenheid || "°C", d: {} };
        s.e = eenheid || s.e;
        const dag = s.d[datumISO] = s.d[datumISO] || {};
        dag[String(horizon)] = { o: oud, n: nieuw,
                                 s: isGetal(sigma) ? sigma : null };
        const dagen = Object.keys(s.d).sort();
        while (dagen.length > 120) delete s.d[dagen.shift()];
        localStorage.setItem(K, JSON.stringify(d));
      } catch (e) { /* opslag vol of uit: schaduw is optioneel */ }
    },

    /* CRPS van een normale verdeling, in dezelfde eenheid als sigma. Kleiner is
       beter, en anders dan de MAE straft hij ook een band die te ruim of te
       krap staat. Bij sigma naar nul loopt hij naar de absolute fout. */
    crpsNormaal: function (mu, sigma, y) {
      if (!isGetal(mu) || !isGetal(y)) return null;
      if (!isGetal(sigma) || sigma <= 0) return Math.abs(mu - y);
      const z = (y - mu) / sigma;
      const phi = Math.exp(-0.5 * z * z) / Math.sqrt(2 * Math.PI);
      return sigma * (z * (2 * Phi(z) - 1) + 2 * phi - 1 / Math.sqrt(Math.PI));
    },

    /* actuals: {stadKey: {datum: waarneming}} in de eenheid van de stad, zoals
       de verificatietabel ze bewaart. Retour: totaal, per stad en per horizon,
       alles in °C. */
    schaduwRapport: function (actuals) {
      let d = {};
      try { d = JSON.parse(localStorage.getItem(SLEUTEL("weerbot-ml-schaduw-v2")) || "{}"); }
      catch (e) {}
      const naarC = (v, e) => (e === "°F" ? v * 5 / 9 : v);   // verschillen, geen niveaus
      const leeg = () => ({ fo: [], fn: [], e: [], crps: [], dek: [] });
      const vul = (b, oud, nieuw, sig, echt, een) => {
        b.fo.push(Math.abs(naarC(oud - echt, een)));
        b.fn.push(Math.abs(naarC(nieuw - echt, een)));
        b.e.push(naarC(nieuw - echt, een));
        if (isGetal(sig)) {
          const sC = naarC(sig, een);
          b.crps.push(WeerbotML.crpsNormaal(naarC(nieuw - echt, een), sC, 0));
          b.dek.push(Math.abs(naarC(nieuw - echt, een)) <= 1.2816 * sC ? 1 : 0);
        }
      };
      const gem = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
      const uit = (b) => ({ n: b.fo.length, maeOud: gem(b.fo), maeNieuw: gem(b.fn),
                            bias: gem(b.e), crps: gem(b.crps), dekking80: gem(b.dek) });

      const totaal = leeg(), perHorizon = {}, perStad = {};
      Object.keys(d).forEach(function (stad) {
        const een = d[stad].e || "°C";
        const bs = leeg(), bsh = {};
        Object.keys(d[stad].d || {}).forEach(function (dag) {
          const a = actuals && actuals[stad] && actuals[stad][dag];
          if (!isGetal(a)) return;
          const perH = d[stad].d[dag];
          Object.keys(perH).forEach(function (h) {
            const r = perH[h];
            if (!r || !isGetal(r.o) || !isGetal(r.n)) return;
            [totaal, bs, perHorizon[h] = perHorizon[h] || leeg(),
             bsh[h] = bsh[h] || leeg()].forEach(function (b) {
              vul(b, r.o, r.n, r.s, a, een);
            });
          });
        });
        if (bs.fo.length) {
          perStad[stad] = uit(bs);
          perStad[stad].eenheid = een;
          perStad[stad].perHorizon = {};
          Object.keys(bsh).forEach(function (h) { perStad[stad].perHorizon[h] = uit(bsh[h]); });
        }
      });
      const r = uit(totaal);
      r.perStad = perStad;
      r.perHorizon = {};
      Object.keys(perHorizon).forEach(function (h) { r.perHorizon[h] = uit(perHorizon[h]); });
      return r;
    },
  };

  if (typeof module !== "undefined" && module.exports) module.exports = WeerbotML;
  else wortel.WeerbotML = WeerbotML;
})(typeof self !== "undefined" ? self : this);
