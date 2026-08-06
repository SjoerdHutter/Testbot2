/* Polymarket-koppeling voor Weerbot 2.
 *
 * Polymarket noteert elke dag per stad twee reeksen weermarkten: "Highest
 * temperature in <stad> on <datum>?" en voor een handvol steden ook "Lowest
 * temperature in ...". Elke reeks bestaat uit elf elkaar uitsluitende markten,
 * een per temperatuurvak, en elk vak heeft een Ja-prijs die je als kans mag
 * lezen. De afrekenbron is steeds hetzelfde weerstation dat de app zelf al
 * gebruikt (LGA voor New York, EGLC voor Londen, en zo verder), dus de markt en
 * de voorspelling gaan over exact hetzelfde getal.
 *
 * Dit bestand haalt die cijfers op bij de publieke Gamma-API en tekent er een
 * venster mee: totaal verhandeld volume, volume over 24 uur, open interest en
 * de Ja-kans per temperatuurvak, met daarnaast de kans die uit de eigen
 * voorspelling en de gekalibreerde band volgt.
 *
 * Er wordt niets gehandeld en er gaat niets naar buiten: alleen leesverzoeken
 * naar gamma-api.polymarket.com.
 */
(function () {
  "use strict";

  var API = "https://gamma-api.polymarket.com/events?slug=";
  var SITE = "https://polymarket.com/event/";
  var TTL = 90 * 1000;              // hoe lang een antwoord vers heet
  var MAAND = ["january", "february", "march", "april", "may", "june", "july",
               "august", "september", "october", "november", "december"];

  /* Stadssleutel van de app -> stadsdeel van de Polymarket-slug. */
  var SLUG = {
    NYC: "nyc", CHI: "chicago", MIA: "miami", LAX: "los-angeles",
    SFO: "san-francisco", SEA: "seattle", DEN: "denver", DAL: "dallas",
    HOU: "houston", AUS: "austin", ATL: "atlanta", LON: "london", PAR: "paris",
    AMS: "amsterdam", MAD: "madrid", MIL: "milan", MUC: "munich", WAW: "warsaw",
    HEL: "helsinki", ANK: "ankara", IST: "istanbul", MOW: "moscow", TYO: "tokyo",
    SEL: "seoul", PUS: "busan", TPE: "taipei", PEK: "beijing", SHA: "shanghai",
    CAN: "guangzhou", SZX: "shenzhen", CTU: "chengdu", CKG: "chongqing",
    WUH: "wuhan", TAO: "qingdao", HKG: "hong-kong", MNL: "manila",
    KUL: "kuala-lumpur", SIN: "singapore", KHI: "karachi", LKO: "lucknow",
    JED: "jeddah", TLV: "tel-aviv", TOR: "toronto", MEX: "mexico-city",
    PTY: "panama-city", BUE: "buenos-aires", SAO: "sao-paulo", CPT: "cape-town",
    WLG: "wellington"
  };

  var bak = {};                     // slug -> {t, data of null}
  var toestand = null;              // wat er nu in het venster staat

  /* ── Kleine hulpjes ── */

  function slugVan(stadKey, datumISO, soort) {
    var c = SLUG[stadKey];
    if (!c) return null;
    var d = parseInt(datumISO.slice(8, 10), 10);
    var m = MAAND[parseInt(datumISO.slice(5, 7), 10) - 1];
    var j = datumISO.slice(0, 4);
    return (soort === "min" ? "lowest" : "highest") +
           "-temperature-in-" + c + "-on-" + m + "-" + d + "-" + j;
  }

  function getal(x) {
    var v = typeof x === "string" ? parseFloat(x) : x;
    return (typeof v === "number" && isFinite(v)) ? v : null;
  }

  function lijstUit(x) {
    if (Array.isArray(x)) return x;
    if (typeof x !== "string") return [];
    try { return JSON.parse(x) || []; } catch (e) { return []; }
  }

  function geld(v) {
    if (v === null || v === undefined) return "?";
    var a = Math.abs(v);
    if (a >= 1e6) return "$" + (v / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace(".", ",") + " mln";
    if (a >= 1e3) return "$" + Math.round(v / 1e3) + "k";
    return "$" + Math.round(v);
  }

  function procent(p) {
    if (p === null || p === undefined) return "?";
    if (p <= 0) return "0%";
    if (p < 0.005) return "<1%";
    if (p < 1 && p > 0.995) return ">99%";
    return (p * 100).toFixed(p < 0.1 ? 1 : 0).replace(".", ",") + "%";
  }

  /* Verschil tussen onze kans en de marktkans, in procentpunten. */
  function edgeHtml(edge) {
    if (edge === null) return "<td>·</td>";
    var pt = Math.round(edge * 100);
    if (pt === 0) return '<td style="color:#5E6B82">0pt</td>';
    return '<td class="markt-edge-' + (pt > 0 ? "pos" : "neg") + '">' +
           (pt > 0 ? "+" : "−") + Math.abs(pt) + "pt</td>";
  }

  function nlGetal(x, dec) {
    if (x === null || x === undefined) return "?";
    return x.toFixed(dec === undefined ? 1 : dec).replace(".", ",").replace("-", "−");
  }

  function veilig(t) {
    return String(t === null || t === undefined ? "" : t)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* Normale verdeling: Abramowitz & Stegun 7.1.26, ruim nauwkeurig genoeg om
     kansen per graad mee uit te rekenen. */
  function Phi(z) {
    var t = 1 / (1 + 0.2316419 * Math.abs(z));
    var d = 0.3989422804014327 * Math.exp(-z * z / 2);
    var p = d * t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 +
            t * (-1.821255978 + t * 1.330274429))));
    return z > 0 ? 1 - p : p;
  }

  /* ── Vakken lezen ──
     Polymarket schrijft de vaknamen als "73°F or below", "74-75°F", "26°C" of
     "90°F or higher". Terug komt de grens in hele graden plus de eenheid; de
     markt rekent af op hele graden, dus het echte vak loopt van lo-0,5 tot
     hi+0,5. */
  function vakUit(titel) {
    var t = String(titel || "").replace(/[−–—]/g, "-").replace(/\s+/g, " ").trim();
    var e = /°\s*C/i.test(t) ? "°C" : (/°\s*F/i.test(t) ? "°F" : null);
    var laag = /below|lower|under|or less/i.test(t);
    var hoog = /higher|above|over|or more/i.test(t);
    /* Eerst het bereik: in "78-79" is het streepje een scheidingsteken, niet een
       minteken, en in "-2--1" is het allebei. Pas daarna losse getallen zoeken,
       anders leest een simpele getalregex de 79 uit "78-79" als -79. */
    if (!laag && !hoog) {
      var r = t.match(/(-?\d+)\s*-\s*(-?\d+)\s*°/);
      if (r) return { lo: parseInt(r[1], 10), hi: parseInt(r[2], 10), eenheid: e };
    }
    var g = t.match(/-?\d+/g);
    if (!g || !g.length) return null;
    var n = parseInt(g[0], 10);
    if (laag) return { lo: null, hi: n, eenheid: e };
    if (hoog) return { lo: n, hi: null, eenheid: e };
    return { lo: n, hi: n, eenheid: e };
  }

  function naarEenheid(v, van, naar) {
    if (v === null || v === undefined || van === naar) return v;
    return naar === "°F" ? v * 9 / 5 + 32 : (v - 32) * 5 / 9;
  }
  function deltaNaar(v, van, naar) {
    if (v === null || v === undefined || van === naar) return v;
    return naar === "°F" ? v * 9 / 5 : v * 5 / 9;
  }

  /* ── Ophalen ── */

  function haal(slug) {
    var c = bak[slug];
    if (c && Date.now() - c.t < TTL) return Promise.resolve(c.data);
    return fetch(API + encodeURIComponent(slug)).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (j) {
      var e = Array.isArray(j) && j.length ? j[0] : null;
      var uit = e ? verwerk(e, slug) : null;
      bak[slug] = { t: Date.now(), data: uit };
      return uit;
    });
  }

  function verwerk(e, slug) {
    var vakken = (e.markets || []).map(function (m) {
      var namen = lijstUit(m.outcomes);
      var prijzen = lijstUit(m.outcomePrices).map(getal);
      var i = 0;
      for (var k = 0; k < namen.length; k++) {
        if (String(namen[k]).toLowerCase() === "yes") { i = k; break; }
      }
      var v = vakUit(m.groupItemTitle || m.question);
      return {
        label: m.groupItemTitle || m.question || "?",
        lo: v ? v.lo : null, hi: v ? v.hi : null, eenheid: v ? v.eenheid : null,
        ja: prijzen.length > i ? prijzen[i] : null,
        volume: getal(m.volumeNum !== undefined ? m.volumeNum : m.volume) || 0,
        bied: getal(m.bestBid), laat: getal(m.bestAsk), spread: getal(m.spread)
      };
    }).map(function (b) {
      /* Van de elf vakken staan er meestal maar een handvol echt open. De rest
         noteert de bodemprijs zonder koper: wel volume uit het verleden, maar
         geen bod meer. Die tellen als niet verhandeld. */
      b.verhandeld = (b.ja !== null && b.ja >= 0.005) || (b.bied !== null && b.bied > 0.001);
      return b;
    }).filter(function (b) { return b.lo !== null || b.hi !== null; });

    /* op temperatuur sorteren: het "of lager"-vak vooraan, "of hoger" achteraan */
    vakken.sort(function (a, b) {
      var x = a.lo === null ? -Infinity : a.lo, y = b.lo === null ? -Infinity : b.lo;
      return x - y;
    });

    var eenheid = null;
    vakken.forEach(function (b) { if (!eenheid && b.eenheid) eenheid = b.eenheid; });

    var somJa = 0, somVol = 0;
    vakken.forEach(function (b) {
      if (b.ja !== null) somJa += b.ja;
      somVol += b.volume;
    });

    return {
      slug: slug,
      titel: e.title || slug,
      eenheid: eenheid,
      volume: getal(e.volume),
      liquiditeit: getal(e.liquidity),
      volume24: getal(e.volume24hr),
      openInterest: getal(e.openInterest),
      vakVolume: somVol,
      somJa: somJa,
      einde: e.endDate || null,
      gesloten: !!e.closed,
      bron: e.resolutionSource || null,
      vakken: vakken,
      opgehaald: Date.now()
    };
  }

  /* ── Eigen kans per vak ──
     De app levert een verwachting met een 80%-band. Daaruit volgt een normale
     verdeling (de band beslaat 2 x 1,2816 standaardafwijking) en daaruit de kans
     dat de afrekening in een vak valt. De band is gekalibreerd op de restfout,
     dus dit is de eerlijkste vertaling die de app kan maken; scheve verdelingen
     vangt ze niet. */
  function onzeKansen(vakken, dag, marktEenheid, appEenheid) {
    if (!dag || !vakken.length) return null;
    var mu = naarEenheid(dag.verwachting, appEenheid, marktEenheid);
    var breedte = deltaNaar(dag.p90 - dag.p10, appEenheid, marktEenheid);
    if (mu === null || !(breedte > 0)) return null;
    var sigma = breedte / (2 * 1.2815515655446004);
    if (!(sigma > 0.05)) sigma = 0.05;
    return vakken.map(function (b) {
      var boven = b.hi === null ? 1 : Phi((b.hi + 0.5 - mu) / sigma);
      var onder = b.lo === null ? 0 : Phi((b.lo - 0.5 - mu) / sigma);
      return Math.max(0, Math.min(1, boven - onder));
    });
  }


  /* ── Strategie A: de staart faden ──
     NO kopen op een vakje ver van de eigen verwachting, waar de markt nog een
     redelijke prijs vraagt. De regels komen uit de strategiekaart:

       richting     NO kopen
       welk vakje   minimaal 2 vakjes (4 °F of 2 °C) van het gecorrigeerde
                    gemiddelde; dichterbij is de verboden zone
       prijs        Ja tussen 12¢ en 45¢, anders overslaan
       voorwaarde   modelkans minstens 5 procentpunt onder de marktprijs
       moment       36 tot 12 uur voor afloop

     NO is ondergewaardeerd als de markt het vakje duurder prijst dan ons model:
     de NO-prijs is dan 1 - ja, terwijl onze kans op "niet dit vakje" 1 - onze is.
     Die twee vergelijken komt neer op onze kans minstens 5 punt onder de Ja-prijs. */
  var STRAT_A = { vakAfstand: 2, prijsMin: 0.12, prijsMax: 0.45, rand: 0.05,
                  uurVroeg: 36, uurLaat: 12,
                  /* Poorten uit strategie E. De spread is een absoluut bedrag:
                     3 cent op de prijs van het vakje, niet 3 procent daarvan.
                     Liquiditeit en dagkeuze zijn instelbaar vanuit de app. */
                  liquiditeit: 5000, spreadMax: 0.03, dagKeuze: "auto" };

  /* Instellingen vanuit de balk. liquiditeit in dollars, 0 zet de poort uit;
     dag is "auto" (de dag die binnen het koopvenster sluit) of 0, 1, 2. */
  function instellen(o) {
    if (!o) return;
    if (o.liquiditeit !== undefined && o.liquiditeit !== null) {
      STRAT_A.liquiditeit = Math.max(0, o.liquiditeit);
    }
    if (o.dag !== undefined && o.dag !== null) STRAT_A.dagKeuze = o.dag;
  }

  /* Index van het vakje waar het gecorrigeerde gemiddelde in valt. */
  function vakVanMu(vakken, mu) {
    for (var i = 0; i < vakken.length; i++) {
      var b = vakken[i];
      var onder = b.lo === null || mu >= b.lo - 0.5;
      var boven = b.hi === null || mu <= b.hi + 0.5;
      if (onder && boven) return i;
    }
    return mu < 0 ? 0 : vakken.length - 1;
  }

  /* Uren tot afloop; negatief als de markt al dicht is. */
  function urenTot(einde) {
    var t = Date.parse(einde);
    if (isNaN(t)) return null;
    return (t - Date.now()) / 3600000;
  }

  /* Per vakje het oordeel voor strategie A. Geeft ook de reden van afwijzing,
     zodat de tabel kan laten zien waaróm een vakje afvalt. */
  function beoordeelA(d, onze, eigen, marktEenheid, appEenheid) {
    var uit = { vakken: [], geschikt: [], gestrand: [], uren: urenTot(d.einde), venster: false,
                liquiditeit: d.liquiditeit, grens: STRAT_A.liquiditeit,
                liquideGenoeg: STRAT_A.liquiditeit <= 0 || d.liquiditeit === null ||
                               d.liquiditeit >= STRAT_A.liquiditeit };
    uit.venster = uit.uren !== null && uit.uren <= STRAT_A.uurVroeg && uit.uren >= STRAT_A.uurLaat;
    if (!onze || !eigen) return uit;
    var mu = naarEenheid(eigen.verwachting, appEenheid, marktEenheid);
    var iMu = vakVanMu(d.vakken, mu);
    d.vakken.forEach(function (b, i) {
      var p = onze[i];
      var afstand = Math.abs(i - iMu);
      var o = { afstand: afstand, kans: p, ja: b.ja, redenen: [] };
      if (afstand < STRAT_A.vakAfstand) o.redenen.push("verboden zone");
      if (b.ja === null) o.redenen.push("geen prijs");
      else if (b.ja < STRAT_A.prijsMin) o.redenen.push("te goedkoop");
      else if (b.ja > STRAT_A.prijsMax) o.redenen.push("te duur");
      if (p === null) o.redenen.push("geen modelkans");
      else if (b.ja !== null && p > b.ja - STRAT_A.rand) o.redenen.push("te weinig voordeel");
      /* De regels van A zijn gehaald; nu nog de twee poorten die over de markt
         zelf gaan. Die houd ik apart, zodat de tabel laat zien dat een vakje op
         de handel afvalt en niet op de voorspelling. */
      o.aRegels = o.redenen.length === 0;
      o.poorten = [];
      if (!uit.liquideGenoeg) o.poorten.push("te weinig liquiditeit");
      if (b.spread !== null) {
        o.spread = b.spread;
        o.spreiding = b.ja ? b.spread / b.ja : null;
        if (b.spread > STRAT_A.spreadMax + 1e-9) o.poorten.push("spread te breed");
      }
      o.geschikt = o.aRegels && o.poorten.length === 0;
      o.voordeel = (p !== null && b.ja !== null) ? b.ja - p : null;
      if (o.geschikt) {
        uit.geschikt.push({ i: i, label: b.label, ja: b.ja, kans: p, voordeel: o.voordeel,
                            spread: o.spread, spreiding: o.spreiding });
      } else if (o.aRegels) {
        uit.gestrand.push({ i: i, label: b.label, ja: b.ja, voordeel: o.voordeel,
                            poorten: o.poorten, spread: o.spread, spreiding: o.spreiding });
      }
      uit.vakken.push(o);
    });
    uit.geschikt.sort(function (a, b) { return b.voordeel - a.voordeel; });
    return uit;
  }


  /* ── Scan over alle steden ──
     Voor de melding op de tegel moet de app van elke stad weten of er een vakje
     is dat aan strategie A voldoet, zonder dat je elk paneel opent. Dat kost een
     verzoek per stad, dus er wordt zo min mogelijk gescand:

       - alleen de dag waarvan de markt binnen het koopvenster van 36 tot 12 uur
         sluit; buiten dat venster doet de strategie toch niets
       - alleen de reeks op de hoogste temperatuur
       - hoogstens vier verzoeken tegelijk, en de uitkomst wordt bewaard

     Markten sluiten om 12:00 UTC op hun eigen datum; daarmee is vooraf te
     bepalen welke dag in het venster valt. */
  function scanDag(dagen, stad) {
    dagen = dagen || [];
    /* Een vaste dag gekozen in de balk: die nemen, ook als hij buiten het
       koopvenster valt. Het paneel zegt er dan bij dat je buiten het venster
       zit, dus je ziet het verschil. */
    if (STRAT_A.dagKeuze !== "auto") {
      var i2 = STRAT_A.dagKeuze;
      var d2 = dagen[i2] && dagen[i2].datum;
      if (!d2) return null;
      return { i: i2, datum: d2, uren: (Date.parse(d2 + "T12:00:00Z") - Date.now()) / 3600000 };
    }
    var beste = null;
    for (var i = 0; i < Math.min(3, dagen.length); i++) {
      var datum = dagen[i] && dagen[i].datum;
      if (!datum) continue;
      var uren = (Date.parse(datum + "T12:00:00Z") - Date.now()) / 3600000;
      if (uren <= STRAT_A.uurVroeg && uren >= STRAT_A.uurLaat) {
        if (!beste || uren < beste.uren) beste = { i: i, datum: datum, uren: uren };
      }
    }
    return beste;
  }

  /* lijst: [{stad, dagen}] -> belooft {stadssleutel: {aantal, beste, datum, uren}} */
  function scan(lijst, klaarPer) {
    var werk = [];
    lijst.forEach(function (o) {
      if (!o.stad || !SLUG[o.stad.key]) return;
      var dag = scanDag(o.dagen, o.stad);
      if (!dag) return;
      werk.push({ stad: o.stad, dagen: o.dagen, dag: dag });
    });
    var uit = {};
    if (!werk.length) return Promise.resolve(uit);

    var volgende = 0, actief = 0;
    return new Promise(function (klaar) {
      function start() {
        while (volgende < werk.length && actief < 4) {
          (function (w) {
            actief++;
            var slug = slugVan(w.stad.key, w.dag.datum, "max");
            haal(slug).then(function (d) {
              if (!d || !d.vakken.length) return;
              var eigen = w.dagen[w.dag.i];
              var marktEenheid = d.eenheid || w.stad.eenheid;
              var onze = eigen ? onzeKansen(d.vakken, eigen, marktEenheid, w.stad.eenheid) : null;
              var A = beoordeelA(d, onze, eigen, marktEenheid, w.stad.eenheid);
              uit[w.stad.key] = {
                aantal: A.geschikt.length,
                bijna: A.gestrand.length,
                bijnaBeste: A.gestrand.length ? A.gestrand[0] : null,
                beste: A.geschikt.length ? A.geschikt[0] : null,
                datum: w.dag.datum, dagIndex: w.dag.i,
                uren: Math.round(A.uren), venster: A.venster,
                volume: d.volume
              };
              if (klaarPer) klaarPer(w.stad.key, uit[w.stad.key]);
            }).catch(function () { /* een stad zonder markt is geen fout */ })
              .then(function () {
                actief--;
                if (volgende >= werk.length && actief === 0) klaar(uit);
                else start();
              });
          })(werk[volgende++]);
        }
      }
      start();
    });
  }

  /* Verwachte temperatuur volgens de markt: het midden van elk vak, gewogen met
     de genormaliseerde Ja-kansen. De open vakken aan de randen krijgen hun grens
     plus/min een halve vakbreedte, anders zou het gemiddelde niet bestaan. */
  function marktGemiddelde(vakken) {
    var W = 0, S = 0;
    var breed = 1;
    vakken.forEach(function (b) {
      if (b.lo !== null && b.hi !== null) breed = Math.max(breed, b.hi - b.lo + 1);
    });
    vakken.forEach(function (b) {
      if (b.ja === null) return;
      var mid;
      if (b.lo === null) mid = b.hi - breed / 2;
      else if (b.hi === null) mid = b.lo + breed / 2;
      else mid = (b.lo + b.hi) / 2;
      W += b.ja; S += b.ja * mid;
    });
    return W > 0.2 ? S / W : null;
  }

  /* ── Uitklappaneel per stad ──
     De cijfers staan in de kaart zelf, onder "polymarket", naast het paneel
     "model en controles". Per stad wordt onthouden welke reeks (hoogste of
     laagste) en welke dag je aan het bekijken was. */

  var toestand = {};                // stadssleutel -> {soort, dagIndex}

  function toestandVan(key) {
    if (!toestand[key]) toestand[key] = { soort: "max", dagIndex: 0, alles: false };
    return toestand[key];
  }

  /* Het paneel vooraf op een dag en reeks zetten, zodat de melding op de tegel
     opent op de markt waar hij over gaat en niet op die van vandaag. */
  function richt(key, soort, dagIndex) {
    var st = toestandVan(key);
    if (soort) st.soort = soort;
    if (dagIndex !== null && dagIndex !== undefined) st.dagIndex = dagIndex;
  }

  function datumVan(stad, dagen, i) {
    var d = dagen && dagen[i];
    if (d && d.datum) return d.datum;
    var basis = new Date(Date.now() + i * 86400000);
    try {
      return new Intl.DateTimeFormat("en-CA", { timeZone: stad.tz, year: "numeric",
        month: "2-digit", day: "2-digit" }).format(basis);
    } catch (e) { return basis.toISOString().slice(0, 10); }
  }

  function dagLabel(dagen, i) {
    var namen = ["vandaag", "morgen", "overmorgen"];
    var d = dagen && dagen[i];
    if (!d || !d.datum) return namen[i] || ("dag " + i);
    return (namen[i] || "") + " " +
           parseInt(d.datum.slice(8, 10), 10) + "/" + parseInt(d.datum.slice(5, 7), 10);
  }

  /* Vult `el` met de marktcijfers van deze stad en bindt de knoppen erin. */
  function vul(el, stad, dagen) {
    if (!el || !stad || !SLUG[stad.key]) return;
    var t = toestandVan(stad.key);
    var datum = datumVan(stad, dagen, t.dagIndex);
    var slug = slugVan(stad.key, datum, t.soort);

    if (!el.getAttribute("data-markt-gebonden")) {
      el.setAttribute("data-markt-gebonden", "1");
      el.addEventListener("click", function (e) {
        var k = e.target;
        var a = function (n) { return k && k.getAttribute ? k.getAttribute(n) : null; };
        var soort = a("data-markt-soort");
        var dag = a("data-markt-dag");
        var ververs = a("data-markt-ververs");
        var alles = a("data-markt-alles");
        if (!soort && dag === null && ververs === null && alles === null) return;
        e.preventDefault(); e.stopPropagation();
        var st = toestandVan(stad.key);
        if (soort) st.soort = soort;
        if (alles !== null) st.alles = alles === "1";
        if (dag !== null) st.dagIndex = parseInt(dag, 10);
        if (ververs !== null) delete bak[slugVan(stad.key, datumVan(stad, dagen, st.dagIndex), st.soort)];
        vul(el, stad, dagen);
      });
    }

    el.innerHTML = kopHtml(stad, dagen, t, datum) +
                   '<div class="markt-melding">Polymarket ophalen…</div>';
    el.setAttribute("data-markt-slug", slug);

    haal(slug).then(function (d) {
      if (el.getAttribute("data-markt-slug") !== slug) return;   // er is intussen geklikt
      el.innerHTML = kopHtml(stad, dagen, t, datum) +
                     (d ? lijfHtml(stad, dagen, t, d, slug) : geenHtml(t));
    }).catch(function (e) {
      if (el.getAttribute("data-markt-slug") !== slug) return;
      el.innerHTML = kopHtml(stad, dagen, t, datum) +
        '<div class="markt-melding">Polymarket antwoordde niet (' + veilig(e.message) +
        '). <span class="markt-link" data-markt-ververs="1">opnieuw proberen</span></div>';
    });
  }

  function kopHtml(stad, dagen, t, datum) {
    var knop = function (attr, waarde, tekst, aan) {
      return '<button class="chip-knop' + (aan ? " aan" : "") + '" ' + attr + '="' +
             waarde + '">' + tekst + "</button>";
    };
    var dagen3 = Math.max(1, Math.min(3, (dagen && dagen.length) || 3));
    var dagKnoppen = "";
    for (var i = 0; i < dagen3; i++) {
      dagKnoppen += knop("data-markt-dag", i, dagLabel(dagen, i), t.dagIndex === i);
    }
    return '<div class="markt-tabs">' +
             knop("data-markt-soort", "max", "hoogste", t.soort === "max") +
             knop("data-markt-soort", "min", "laagste", t.soort === "min") +
             '<span class="balk-scheiding"></span>' + dagKnoppen +
           "</div>" +
           '<div class="markt-sub">afrekenstation ' + veilig(stad.station) +
             " · lokale datum " + veilig(datum) + bronLabel(stad) + "</div>";
  }

  /* Alleen melden als het wél uitmaakt. 48 van de 49 steden controleren op de
     METAR-reeks van precies het station waar de markt op afrekent; daar is een
     regel "bronconsistentie: station" ruis onder elke tabel. Hongkong is de
     uitzondering: de controle loopt via het observatorium (HKO), dus daar kan
     onze gemeten waarde van de afrekening afwijken. */
  function bronLabel(stad) {
    var bron = stad.bron || "iem";
    if (bron === "iem") return "";
    return " · let op: onze meting komt van " + veilig(bron) +
           ", niet van het afrekenstation";
  }

  function geenHtml(t) {
    return '<div class="markt-melding">' +
      (t.soort === "min"
        ? "Polymarket noteert voor deze stad en dag geen markt op de laagste temperatuur. Die reeks loopt maar voor een handvol steden."
        : "Polymarket noteert voor deze stad en dag geen markt op de hoogste temperatuur. Markten voor morgen en overmorgen gaan meestal pas een dag van tevoren open.") +
      ' <span class="markt-link" data-markt-ververs="1">opnieuw proberen</span></div>';
  }

  function lijfHtml(stad, dagen, t, d, slug) {
    var dag = dagen && dagen[t.dagIndex];
    var eigen = dag ? (t.soort === "min" ? dag.mn : dag) : null;
    var appEenheid = stad.eenheid;
    var marktEenheid = d.eenheid || appEenheid;
    var onze = eigen ? onzeKansen(d.vakken, eigen, marktEenheid, appEenheid) : null;
    var onsMax = onze ? Math.max.apply(null, onze) : null;
    var A = beoordeelA(d, onze, eigen, marktEenheid, appEenheid);

    var cijfers =
      cijfer("totaal verhandeld", geld(d.volume), "over de hele reeks") +
      cijfer("laatste 24 uur", geld(d.volume24), "verhandeld volume") +
      cijfer("open interest", geld(d.openInterest), "uitstaande posities") +
      cijfer("temperatuurvakken", String(d.vakken.length),
             d.gesloten ? "markt gesloten" : "markt open");

    var schaal = 0.08;
    d.vakken.forEach(function (b) { if (b.ja !== null && b.ja > schaal) schaal = b.ja; });
    if (onsMax !== null && onsMax > schaal) schaal = onsMax;

    /* Standaard alleen de vakken waarin gehandeld wordt. Een vak waar de markt
       niets meer in ziet maar wij wel, blijft staan: juist daar zit het verschil
       waar dit paneel voor bedoeld is. */
    var toonAlles = !!t.alles;
    var zichtbaar = [], verborgen = 0;
    d.vakken.forEach(function (b, i) {
      var p = onze ? onze[i] : null;
      var a = A.vakken[i];
      if (toonAlles || b.verhandeld || (p !== null && p >= 0.01) || (a && a.geschikt)) {
        zichtbaar.push({ b: b, p: p, a: a });
      } else verborgen++;
    });
    if (!zichtbaar.length) {                       // niets over: dan toch maar alles
      d.vakken.forEach(function (b, i) {
        zichtbaar.push({ b: b, p: onze ? onze[i] : null, a: A.vakken[i] });
      });
      verborgen = 0;
    }

    var rijen = zichtbaar.map(function (r, n) {
      var b = r.b, p = r.p, a = r.a;
      var edge = (p !== null && b.ja !== null) ? p - b.ja : null;
      var klas = ["vak"];
      if (!n) klas.push("eerste");
      if (b.ja !== null && b.ja >= 0.5) klas.push("piek");
      if (p !== null && p === onsMax) klas.push("onsx");
      if (a && a.geschikt) klas.push("koopno");
      var merk = (a && a.geschikt)
        ? ' <span class="no-merk" title="strategie A: NO kopen, ' +
          Math.round(a.voordeel * 100) + ' punt ondergewaardeerd">NO</span>' : "";
      return '<tr class="' + klas.join(" ") + '">' +
        "<td>" + veilig(b.label) + merk + "</td>" +
        "<td>" + procent(b.ja) + "</td>" +
        "<td>" + (p === null ? "\u00B7" : procent(p)) + "</td>" +
        edgeHtml(edge) +
        "<td>" + geld(b.volume) + "</td>" +
      "</tr>" +
      /* De staaf staat op een eigen regel over de volle breedte. Als kolom paste
         hij niet op een telefoonscherm en verdween hij daar helemaal. */
      '<tr class="balken"><td colspan="5"><span class="markt-balk">' +
        '<i class="markt" style="width:' + Math.round(Math.min(1, (b.ja || 0) / schaal) * 100) + '%"></i>' +
        (p === null ? "" : '<i class="ons" style="width:' +
          Math.round(Math.min(1, p / schaal) * 100) + '%"></i>') +
      "</span></td></tr>";
    }).join("");

    var verborgenRegel = "";
    if (verborgen || toonAlles) {
      verborgenRegel = '<div class="markt-verborgen">' +
        (toonAlles
          ? "alle " + d.vakken.length + " vakken \u00B7 " +
            '<span data-markt-alles="0">alleen waar in gehandeld wordt</span>'
          : verborgen + " vak" + (verborgen === 1 ? "" : "ken") +
            " zonder handel verborgen \u00B7 " + '<span data-markt-alles="1">toon alles</span>') +
        "</div>";
    }

    var mGem = marktGemiddelde(d.vakken);
    var onsGem = eigen ? naarEenheid(eigen.verwachting, appEenheid, marktEenheid) : null;
    var vergelijk = "";
    if (mGem !== null || onsGem !== null) {
      vergelijk = '<div class="markt-legenda" style="margin-top:8px">' +
        (mGem !== null ? "markt verwacht <b>" + nlGetal(mGem) + marktEenheid + "</b>" : "") +
        (mGem !== null && onsGem !== null ? " · " : "") +
        (onsGem !== null ? "wij <b>" + nlGetal(onsGem) + marktEenheid + "</b>" +
          (eigen && eigen.p10 !== undefined
            ? " (80% " + nlGetal(naarEenheid(eigen.p10, appEenheid, marktEenheid)) + " tot " +
              nlGetal(naarEenheid(eigen.p90, appEenheid, marktEenheid)) + ")" : "") : "") +
        (mGem !== null && onsGem !== null
          ? " · verschil " + nlGetal(onsGem - mGem) + marktEenheid : "") +
        "</div>";
    }

    var onsNoot = onze
      ? (eigen && eigen.gekalibreerd === false
          ? "onze kans komt uit de kale ledenspreiding, die is nog niet geijkt"
          : "onze kans komt uit de verwachting en de gekalibreerde 80%-band")
      : "voor deze dag heeft de app geen voorspelling, dus alleen de marktkansen staan er";

    return '<div class="markt-sub" style="margin-top:8px">' + veilig(d.titel) +
             (d.gesloten ? " · gesloten" : (d.einde ? " · sluit " + tijdKort(d.einde) : "")) +
           "</div>" +
           '<div class="markt-cijfers">' + cijfers + "</div>" +
           strategieHtml(A, marktEenheid) +
           '<div class="markt-kop2">Ja-kans per temperatuur</div>' +
           '<div class="markt-tabelwrap"><table class="markt-tabel"><thead><tr>' +
             "<th>vak</th><th>ja</th><th>ons</th><th>edge</th><th>volume</th>" +
           "</tr></thead><tbody>" + rijen + "</tbody></table></div>" + verborgenRegel +
           '<div class="markt-legenda">' +
             '<span class="lm">▬</span> markt · <span class="lo">▬</span> onze kans · ' +
             onsNoot + ".<br>De elf vakken sluiten elkaar uit; de Ja-prijzen tellen nu op tot " +
             procent(d.somJa) + " (boven 100% zit het verschil tussen bied en laat).</div>" +
           vergelijk +
           '<div class="markt-voet">Bron: publieke Gamma-API van Polymarket, opgehaald ' +
             tijdKort(d.opgehaald) +
             ' · <span class="markt-link" data-markt-ververs="1">vernieuwen</span>' +
             ' · <a class="markt-link" href="' + SITE + veilig(slug) +
             '" target="_blank" rel="noopener">markt openen</a><br>' +
             "Alleen ter vergelijking met de eigen voorspelling; de app handelt niet en verstuurt niets." +
           "</div>";
  }

  /* Samenvatting van strategie A boven de tabel: welke vakjes voldoen, hoeveel
     ze ondergewaardeerd zijn, en of je binnen het koopvenster zit. */
  function strategieHtml(A, eenheid) {
    var venster;
    if (A.uren === null) venster = "sluittijd onbekend";
    else if (A.uren < 0) venster = "markt is dicht";
    else if (A.uren > STRAT_A.uurVroeg) venster = "nog " + Math.round(A.uren) + " u tot afloop, " +
      "het koopvenster begint bij " + STRAT_A.uurVroeg + " u";
    else if (A.uren < STRAT_A.uurLaat) venster = "nog " + Math.round(A.uren) + " u tot afloop, " +
      "het koopvenster sloot bij " + STRAT_A.uurLaat + " u";
    else venster = "nog " + Math.round(A.uren) + " u tot afloop, binnen het koopvenster";

    var kop = '<div class="markt-kop2">Strategie A \u00B7 de staart faden</div>';
    if (!A.vakken.length) {
      return kop + '<div class="markt-melding">geen voorspelling voor deze dag, dus geen oordeel</div>';
    }
    var poortNoot = "liquiditeit " + geld(A.liquiditeit) +
      (A.grens > 0 ? " (grens " + geld(A.grens) + ")" : " (poort uit)") +
      (A.liquideGenoeg ? "" : " \u00B7 te dun") +
      " \u00B7 spread max " + (STRAT_A.spreadMax * 100).toFixed(0) + "\u00A2";
    if (!A.geschikt.length) {
      var bijna = A.gestrand.map(function (g) {
        return "<br>" + veilig(g.label) + " haalt de regels van A maar valt af op " +
               veilig(g.poorten.join(" en ")) +
               (g.spread ? " (spread " + (g.spread * 100).toFixed(1).replace(".", ",") + "\u00A2)" : "");
      }).join("");
      return kop + '<div class="strat-leeg">geen vakje voldoet aan alle regels \u00B7 ' +
             veilig(venster) + " \u00B7 " + poortNoot + bijna + "</div>";
    }
    var lijst = A.geschikt.map(function (g) {
      return '<div class="strat-rij"><span class="no-merk">NO</span> ' +
        '<span class="strat-vak">' + veilig(g.label) + "</span>" +
        '<span class="strat-cijfers">ja ' + Math.round(g.ja * 100) + "\u00A2 \u00B7 ons " +
        procent(g.kans) + " \u00B7 <b>" + Math.round(g.voordeel * 100) +
        " punt ondergewaardeerd</b></span></div>";
    }).join("");
    var extra = A.gestrand.length
      ? "<br>" + A.gestrand.length + " vak" + (A.gestrand.length === 1 ? "" : "ken") +
        " haalt de regels van A maar valt af op de poorten"
      : "";
    return kop +
      '<div class="strat-uit' + (A.venster ? " aan" : "") + '">' + lijst +
      '<div class="strat-noot">' + veilig(venster) +
      (A.venster ? "" : " \u00B7 buiten het venster van de strategie") +
      " \u00B7 " + veilig(poortNoot) + extra + "</div></div>";
  }

  function cijfer(label, waarde, noot) {
    return '<div class="markt-cijfer">' +
             '<div class="markt-cijfer-label">' + veilig(label) + "</div>" +
             '<div class="markt-cijfer-waarde">' + veilig(waarde) + "</div>" +
             '<div class="markt-cijfer-noot">' + veilig(noot) + "</div>" +
           "</div>";
  }

  function tijdKort(x) {
    var d = new Date(x);
    if (isNaN(d.getTime())) return "?";
    try {
      return new Intl.DateTimeFormat("nl-BE", { day: "numeric", month: "short",
        hour: "2-digit", minute: "2-digit" }).format(d);
    } catch (e) { return d.toISOString().slice(0, 16).replace("T", " "); }
  }

  window.WeerbotMarkt = {
    heeftMarkt: function (s) { return !!(s && !s.eigen && SLUG[s.key]); },
    slugVan: slugVan,
    vakUit: vakUit,
    onzeKansen: onzeKansen,
    marktGemiddelde: marktGemiddelde,
    beoordeelA: beoordeelA,
    strategie: STRAT_A,
    scan: scan,
    instellen: instellen,
    richt: richt,
    vul: vul
  };
})();
