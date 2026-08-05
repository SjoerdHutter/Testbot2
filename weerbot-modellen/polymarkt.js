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

  /* Stadssleutel van de app -> stadsdeel van de Polymarket-slug. Zhengzhou en
     Jinan staan er niet in: daar noteert Polymarket geen weermarkt. */
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
        bied: getal(m.bestBid), laat: getal(m.bestAsk)
      };
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

  /* ── Venster ── */

  function laag() { return document.getElementById("marktLaag"); }
  function venster() { return document.getElementById("marktVenster"); }

  function sluit() {
    var l = laag();
    if (l) l.classList.remove("zichtbaar");
    toestand = null;
  }

  function bindEens() {
    var l = laag();
    if (!l || l.getAttribute("data-gebonden")) return;
    l.setAttribute("data-gebonden", "1");
    l.addEventListener("click", function (e) {
      if (e.target === l) return sluit();
      var el = e.target;
      var a = function (n) { return el && el.getAttribute ? el.getAttribute(n) : null; };
      if (a("data-markt-sluit") !== null) return sluit();
      var soort = a("data-markt-soort");
      if (soort && toestand) { toestand.soort = soort; return tekenVenster(); }
      var dag = a("data-markt-dag");
      if (dag !== null && dag !== undefined && toestand) {
        toestand.dagIndex = parseInt(dag, 10);
        return tekenVenster();
      }
      if (a("data-markt-ververs") !== null && toestand) {
        delete bak[toestand.slug];
        return tekenVenster();
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") sluit();
    });
  }

  function open(stad, dagen, soort) {
    if (!SLUG[stad.key]) return;
    bindEens();
    toestand = { stad: stad, dagen: dagen || [], soort: soort === "min" ? "min" : "max", dagIndex: 0 };
    var l = laag();
    if (l) l.classList.add("zichtbaar");
    tekenVenster();
  }

  function datumVan(t) {
    var d = t.dagen && t.dagen[t.dagIndex];
    if (d && d.datum) return d.datum;
    var basis = new Date(Date.now() + t.dagIndex * 86400000);
    try {
      return new Intl.DateTimeFormat("en-CA", { timeZone: t.stad.tz, year: "numeric",
        month: "2-digit", day: "2-digit" }).format(basis);
    } catch (e) { return basis.toISOString().slice(0, 10); }
  }

  function dagLabel(t, i) {
    var namen = ["vandaag", "morgen", "overmorgen"];
    var d = t.dagen && t.dagen[i];
    if (!d || !d.datum) return namen[i] || ("dag " + i);
    var dm = parseInt(d.datum.slice(8, 10), 10) + "/" + parseInt(d.datum.slice(5, 7), 10);
    return (namen[i] || "") + " " + dm;
  }

  function tekenVenster() {
    var v = venster();
    if (!v || !toestand) return;
    var t = toestand;
    var datum = datumVan(t);
    var slug = slugVan(t.stad.key, datum, t.soort);
    t.slug = slug;

    v.innerHTML = kopHtml(t, datum) +
      '<div class="markt-melding">Polymarket ophalen…</div>';

    haal(slug).then(function (d) {
      if (!toestand || toestand.slug !== slug) return;    // er is intussen geklikt
      v.innerHTML = kopHtml(t, datum) + (d ? lijfHtml(t, d, slug) : geenHtml(t, slug));
    }).catch(function (e) {
      if (!toestand || toestand.slug !== slug) return;
      v.innerHTML = kopHtml(t, datum) +
        '<div class="markt-melding">Polymarket antwoordde niet (' + veilig(e.message) +
        '). <span class="markt-link" data-markt-ververs="1" style="cursor:pointer">opnieuw proberen</span></div>';
    });
  }

  function kopHtml(t, datum) {
    var soortKnop = function (k, tekst) {
      return '<button class="chip-knop' + (t.soort === k ? " aan" : "") +
             '" data-markt-soort="' + k + '">' + tekst + "</button>";
    };
    var dagKnop = function (i) {
      return '<button class="chip-knop' + (t.dagIndex === i ? " aan" : "") +
             '" data-markt-dag="' + i + '">' + dagLabel(t, i) + "</button>";
    };
    var aantalDagen = Math.max(1, Math.min(3, (t.dagen && t.dagen.length) || 3));
    var dagKnoppen = "";
    for (var i = 0; i < aantalDagen; i++) dagKnoppen += dagKnop(i);
    return '<div class="markt-kop">' +
             '<div class="markt-titel">Polymarket · ' + veilig(t.stad.naam) + "</div>" +
             '<button class="markt-sluit" data-markt-sluit="1" title="sluiten">×</button>' +
           "</div>" +
           '<div class="markt-sub">afrekenstation ' + veilig(t.stad.station) +
             " · lokale datum " + veilig(datum) + "</div>" +
           '<div class="markt-tabs">' + soortKnop("max", "hoogste temperatuur") +
             soortKnop("min", "laagste temperatuur") + "</div>" +
           '<div class="markt-tabs">' + dagKnoppen + "</div>";
  }

  function geenHtml(t, slug) {
    return '<div class="markt-melding">' +
      (t.soort === "min"
        ? "Polymarket noteert voor deze stad en dag geen markt op de laagste temperatuur. Die reeks loopt maar voor een handvol steden."
        : "Polymarket noteert voor deze stad en dag geen markt op de hoogste temperatuur. Markten voor morgen en overmorgen worden meestal pas een dag van tevoren geopend.") +
      '<div style="margin-top:8px"><span class="markt-link" data-markt-ververs="1" style="cursor:pointer">opnieuw proberen</span></div>' +
      "</div>";
  }

  function lijfHtml(t, d, slug) {
    var dag = t.dagen && t.dagen[t.dagIndex];
    var eigen = null;
    if (dag) eigen = t.soort === "min" ? dag.mn : dag;
    var appEenheid = t.stad.eenheid;
    var marktEenheid = d.eenheid || appEenheid;
    var onze = eigen ? onzeKansen(d.vakken, eigen, marktEenheid, appEenheid) : null;

    var cijfers =
      cijfer("totaal verhandeld", geld(d.volume), "over de hele reeks") +
      cijfer("laatste 24 uur", geld(d.volume24), "verhandeld volume") +
      cijfer("open interest", geld(d.openInterest), "uitstaande posities") +
      cijfer("temperatuurvakken", String(d.vakken.length),
             d.gesloten ? "markt gesloten" : "markt open");

    var maxJa = 0;
    d.vakken.forEach(function (b) { if (b.ja !== null && b.ja > maxJa) maxJa = b.ja; });
    if (onze) onze.forEach(function (p) { if (p > maxJa) maxJa = p; });
    var schaal = Math.max(0.08, maxJa);

    var rijen = d.vakken.map(function (b, i) {
      var p = onze ? onze[i] : null;
      var edge = (p !== null && b.ja !== null) ? p - b.ja : null;
      var klas = [];
      if (b.ja !== null && b.ja >= 0.5) klas.push("piek");
      if (p !== null && onze && p === Math.max.apply(null, onze)) klas.push("onsx");
      return "<tr" + (klas.length ? ' class="' + klas.join(" ") + '"' : "") + ">" +
        "<td>" + veilig(b.label) + "</td>" +
        "<td>" + procent(b.ja) + "</td>" +
        "<td>" + (p === null ? "·" : procent(p)) + "</td>" +
        edgeHtml(edge) +
        '<td class="markt-cel-balk"><span class="markt-balk">' +
          '<i class="markt" style="width:' + Math.round(Math.min(1, (b.ja || 0) / schaal) * 100) + '%"></i>' +
          (p === null ? "" : '<i class="ons" style="width:' +
            Math.round(Math.min(1, p / schaal) * 100) + '%"></i>') +
        "</span></td>" +
        "<td>" + geld(b.volume) + "</td>" +
      "</tr>";
    }).join("");

    var mGem = marktGemiddelde(d.vakken);
    var onsGem = eigen ? naarEenheid(eigen.verwachting, appEenheid, marktEenheid) : null;
    var vergelijk = "";
    if (mGem !== null || onsGem !== null) {
      vergelijk = '<div class="markt-legenda" style="margin-top:10px">' +
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
      ? (t.soort === "min" && eigen && eigen.gekalibreerd === false
          ? "onze kans komt uit de kale ledenspreiding, die is nog niet geijkt"
          : "onze kans komt uit de verwachting en de gekalibreerde 80%-band")
      : "voor deze dag heeft de app geen voorspelling, dus alleen de marktkansen staan er";

    return '<div class="markt-sub" style="margin-top:10px">' + veilig(d.titel) +
             (d.gesloten ? " · gesloten" : (d.einde ? " · sluit " + tijdKort(d.einde) : "")) +
           "</div>" +
           '<div class="markt-cijfers">' + cijfers + "</div>" +
           '<div class="markt-kop2">Ja-kans per temperatuur</div>' +
           '<table class="markt-tabel"><thead><tr>' +
             "<th>vak</th><th>ja</th><th>ons</th><th>edge</th>" +
             '<th class="markt-cel-balk"></th><th>volume</th>' +
           "</tr></thead><tbody>" + rijen + "</tbody></table>" +
           '<div class="markt-legenda">' +
             '<span class="lm">▬</span> markt · <span class="lo">▬</span> onze kans · ' +
             onsNoot + ".<br>" +
             "De elf vakken sluiten elkaar uit; de Ja-prijzen tellen nu op tot " +
             procent(d.somJa) + " (boven 100% zit het verschil in de spreiding tussen bied en laat).</div>" +
           vergelijk +
           '<div class="markt-voet">' +
             "Bron: publieke Gamma-API van Polymarket, opgehaald " + tijdKort(d.opgehaald) +
             ' · <span class="markt-link" data-markt-ververs="1" style="cursor:pointer">vernieuwen</span>' +
             ' · <a class="markt-link" href="' + SITE + veilig(slug) +
             '" target="_blank" rel="noopener">markt openen</a><br>' +
             "Alleen ter vergelijking met de eigen voorspelling; de app handelt niet en verstuurt niets." +
           "</div>";
  }

  function cijfer(label, waarde, noot) {
    return '<div class="markt-cijfer">' +
             '<div class="markt-cijfer-label">' + veilig(label) + "</div>" +
             '<div class="markt-cijfer-waarde">' + veilig(waarde) + "</div>" +
             '<div class="markt-cijfer-noot">' + veilig(noot) + "</div>" +
           "</div>";
  }

  function tijdKort(x) {
    var d = typeof x === "number" ? new Date(x) : new Date(x);
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
    open: open,
    sluit: sluit
  };
})();
