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

  const S = { modellen: null, klim: null };

  const NAAMMAP = {
    p1_ifs: (i, mm) => eersteGeldig(i.p1 && i.p1.ifs, mm),
    p1_aifs: (i, mm) => eersteGeldig(i.p1 && i.p1.aifs, mm),
    p1_gfs: (i, mm) => eersteGeldig(i.p1 && i.p1.gfs, mm),
    p1_icon: (i, mm) => eersteGeldig(i.p1 && i.p1.icon, mm),
    p1_gem: (i, mm) => eersteGeldig(i.p1 && i.p1.gem, mm),
    mm_spreiding: (i) => i.spreiding,
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

    schaduw: function (stadKey, datumISO, oudC, nieuwC) {
      try {
        const K = "weerbot-ml-schaduw-v1";
        const d = JSON.parse(localStorage.getItem(K) || "{}");
        d[stadKey] = d[stadKey] || {};
        d[stadKey][datumISO] = { oud: oudC, nieuw: nieuwC };
        const dagen = Object.keys(d[stadKey]).sort();
        while (dagen.length > 120) delete d[stadKey][dagen.shift()];
        localStorage.setItem(K, JSON.stringify(d));
      } catch (e) { /* opslag vol of uit: schaduw is optioneel */ }
    },

    schaduwRapport: function (actuals) {
      let d = {};
      try { d = JSON.parse(localStorage.getItem("weerbot-ml-schaduw-v1") || "{}"); }
      catch (e) {}
      const per = {}; let no = [], nn = [];
      Object.keys(d).forEach(function (stad) {
        const fo = [], fn = [];
        Object.keys(d[stad]).forEach(function (dag) {
          const a = actuals && actuals[stad] && actuals[stad][dag];
          const r = d[stad][dag];
          if (isGetal(a) && isGetal(r.oud) && isGetal(r.nieuw)) {
            fo.push(Math.abs(r.oud - a)); fn.push(Math.abs(r.nieuw - a));
          }
        });
        if (fo.length) {
          per[stad] = { n: fo.length,
                        maeOud: fo.reduce((x, y) => x + y, 0) / fo.length,
                        maeNieuw: fn.reduce((x, y) => x + y, 0) / fn.length };
          no = no.concat(fo); nn = nn.concat(fn);
        }
      });
      return { perStad: per, n: no.length,
               maeOud: no.length ? no.reduce((x, y) => x + y, 0) / no.length : null,
               maeNieuw: nn.length ? nn.reduce((x, y) => x + y, 0) / nn.length : null };
    },
  };

  if (typeof module !== "undefined" && module.exports) module.exports = WeerbotML;
  else wortel.WeerbotML = WeerbotML;
})(typeof self !== "undefined" ? self : this);
