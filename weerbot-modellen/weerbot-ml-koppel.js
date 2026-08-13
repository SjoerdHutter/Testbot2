/* Koppelstuk tussen de Weerbot-app en de ML-modellen. Schaduwfase: rekent per
 * stad per dag per horizon de ML-voorspelling uit en logt die naast de
 * bestaande, zonder iets aan de getoonde cijfers te veranderen.
 *
 * De invoer komt uit dezelfde bron als de training. Dat was hiervoor niet zo en
 * het is de reden dat dit bestand grondig is herzien; de meting staat in
 * REVIEW.md. Kort: de modellen zijn getraind op de deterministische
 * previous-runs (p1_ifs tot en met p1_gem), maar kregen live het gemiddelde van
 * de ensembleleden uit ensemble-api voorgeschoteld. Voor GEM en GFS zijn dat
 * niet alleen andere getallen maar zelfs andere modelvarianten: gem_seamless
 * tegen gem_global, gfs_seamless tegen ncep_gefs025. Gemeten over 262
 * stad-dagen verschoof de ML-voorspelling daardoor gemiddeld 0,565 °C, met
 * uitschieters van 1,7 °C. De winst die het model moet opleveren is 0,06 °C.
 *
 * Daarom haalt haalPrev() nu dezelfde reeks op als de training, en wordt er
 * niets voorspeld als die reeks er niet is: een schaduwcijfer op de verkeerde
 * invoer is misleidender dan geen cijfer.
 *
 * Activeren gaat per stad en horizon via weerbot-modellen/ml_activatie.json,
 * niet met één schakelaar voor alles. Zie magActief() onderaan. */
(function () {
  "use strict";
  /* Weerbot 2 deelt de herkomst met de eerste versie; SLEUTEL geeft elke
   * opslagnaam een eigen voorvoegsel zodat de twee apps elkaar niet overschrijven. */
  function SLEUTEL(naam) {
    return (window.WEERBOT2_OPSLAG ? window.WEERBOT2_OPSLAG(naam) : naam);
  }
  var KEYMAP = { NYC:"nyc", CHI:"chicago", MIA:"miami", LAX:"losangeles",
    SFO:"sanfrancisco", SEA:"seattle", DEN:"denver", DAL:"dallas", HOU:"houston",
    AUS:"austin", ATL:"atlanta", LON:"londen", PAR:"parijs", AMS:"amsterdam",
    MAD:"madrid", MIL:"milaan", MUC:"munchen", WAW:"warschau", HEL:"helsinki",
    ANK:"ankara", IST:"istanbul", MOW:"moskou", TYO:"tokio", SEL:"seoul",
    PUS:"busan", TPE:"taipei", PEK:"peking", SHA:"shanghai", CAN:"guangzhou",
    SZX:"shenzhen", CTU:"chengdu", CKG:"chongqing", WUH:"wuhan", TAO:"qingdao",
    HKG:"hongkong", MNL:"manila",
    KUL:"kualalumpur", SIN:"singapore", KHI:"karachi", LKO:"lucknow",
    JED:"jeddah", TLV:"telaviv", TOR:"toronto", MEX:"mexicostad",
    PTY:"panamastad", BUE:"buenosaires", SAO:"saopaulo", CPT:"kaapstad",
    WLG:"wellington" };
  /* De modellen zoals deel9_wekelijks.py ze opvraagt (PREV daar). Deze namen
     horen bij de featurekolommen p1_* en p2_* waar de modellen op zijn gefit;
     ze wijken bewust af van ENS_MODELLEN in index.html, want dat is de
     ensemble-API voor de eigen rekenkern van de app. */
  var PREVMOD = "ecmwf_ifs025,ecmwf_aifs025_single,gfs_seamless,icon_seamless," +
                "gem_seamless";
  /* Open-Meteo hernoemde AIFS onderweg; beide namen komen langs. */
  var PREVMAP = { ecmwf_ifs025:"ifs", ecmwf_aifs025_single:"aifs",
                  ecmwf_aifs025:"aifs", gfs_seamless:"gfs",
                  icon_seamless:"icon", gem_seamless:"gem" };
  var KORT = ["ifs", "aifs", "gfs", "icon", "gem"];
  var AUXV = "relative_humidity_2m_mean,cloud_cover_mean,wind_speed_10m_max," +
             "shortwave_radiation_sum,precipitation_sum";
  var AUX = {};        /* mlKey -> datum -> {rh,bewolking,wind,instraling,neerslag} */
  var PREV = {};       /* mlKey -> datum -> {p1:{kort:°C}, p2:{kort:°C}} */
  var ACTIVATIE = null;
  var klaar = null;

  function uitC(v, e)  { return e === "\u00B0F" ? v * 9 / 5 + 32 : v; }
  function deltaNaarC(v, e) { return e === "\u00B0F" ? v * 5 / 9 : v; }
  /* Een verschil, geen niveau: de 32 hoort er niet bij. */
  function uitDelta(v, e) { return e === "\u00B0F" ? v * 9 / 5 : v; }

  function haalAux() {
    try {
      var c = JSON.parse(localStorage.getItem(SLEUTEL("weerbot-ml-aux-v1")) || "null");
      if (c && Date.now() - c.t < 6 * 3600 * 1000) { AUX = c.d; return Promise.resolve(); }
    } catch (e) {}
    var steden = (typeof CONFIG !== "undefined" && CONFIG.steden) || [];
    var werk = [];
    for (var i = 0; i < steden.length; i += 17) werk.push(steden.slice(i, i + 17));
    return Promise.all(werk.map(function (groep) {
      var url = "https://api.open-meteo.com/v1/forecast?latitude=" +
        groep.map(function (s) { return s.lat; }).join(",") + "&longitude=" +
        groep.map(function (s) { return s.lon; }).join(",") +
        "&daily=" + AUXV + "&forecast_days=4&timezone=auto";
      return fetch(url).then(function (r) { return r.json(); }).then(function (d) {
        var lijst = Array.isArray(d) ? d : [d];
        groep.forEach(function (s, gi) {
          var res = lijst[gi]; if (!res || !res.daily) return;
          var mk = KEYMAP[s.key]; if (!mk) return;
          var per = AUX[mk] = AUX[mk] || {};
          (res.daily.time || []).forEach(function (t, ti) {
            per[t] = { rh: res.daily.relative_humidity_2m_mean[ti],
                       bewolking: res.daily.cloud_cover_mean[ti],
                       wind: res.daily.wind_speed_10m_max[ti],
                       instraling: res.daily.shortwave_radiation_sum[ti],
                       neerslag: res.daily.precipitation_sum[ti] };
          });
        });
      }).catch(function () {});
    })).then(function () {
      try { localStorage.setItem(SLEUTEL("weerbot-ml-aux-v1"),
        JSON.stringify({ t: Date.now(), d: AUX })); } catch (e) {}
    });
  }

  /* Dagmaximum uit een uurreeks, met dezelfde ondergrens als dagmax() in
     deel9_wekelijks.py: minder dan twaalf uurwaarden is geen dag. */
  function dagmax(tijden, waarden) {
    var per = {};
    for (var i = 0; i < tijden.length; i++) {
      var v = waarden[i];
      if (v === null || v === undefined) continue;
      (per[tijden[i].slice(0, 10)] = per[tijden[i].slice(0, 10)] || []).push(v);
    }
    var uit = {};
    for (var dag in per) if (per[dag].length >= 12) uit[dag] = Math.max.apply(null, per[dag]);
    return uit;
  }

  /* De p1- en p2-reeksen waar de modellen op zijn getraind, in °C en gebundeld
     in groepen van zeventien steden. Draait na het eerste beeld, net als
     haalAux, dus hij kost de bezoeker geen wachttijd. */
  function haalPrev() {
    try {
      var c = JSON.parse(localStorage.getItem(SLEUTEL("weerbot-ml-prev-v1")) || "null");
      if (c && Date.now() - c.t < 6 * 3600 * 1000) { PREV = c.d; return Promise.resolve(); }
    } catch (e) {}
    var steden = (typeof CONFIG !== "undefined" && CONFIG.steden) || [];
    var werk = [];
    for (var i = 0; i < steden.length; i += 17) werk.push(steden.slice(i, i + 17));
    return Promise.all(werk.map(function (groep) {
      var url = "https://previous-runs-api.open-meteo.com/v1/forecast?latitude=" +
        groep.map(function (s) { return s.lat; }).join(",") + "&longitude=" +
        groep.map(function (s) { return s.lon; }).join(",") +
        "&hourly=temperature_2m_previous_day1,temperature_2m_previous_day2" +
        "&models=" + PREVMOD + "&forecast_days=4" +
        "&temperature_unit=celsius&timezone=auto";
      return fetch(url).then(function (r) { return r.json(); }).then(function (d) {
        var lijst = Array.isArray(d) ? d : [d];
        groep.forEach(function (s, gi) {
          var res = lijst[gi]; if (!res || !res.hourly) return;
          var mk = KEYMAP[s.key]; if (!mk) return;
          var H = res.hourly, tijden = H.time || [];
          var per = PREV[mk] = PREV[mk] || {};
          for (var sleutel in H) {
            var lead = sleutel.indexOf("temperature_2m_previous_day1_") === 0 ? "p1" :
                       sleutel.indexOf("temperature_2m_previous_day2_") === 0 ? "p2" : null;
            if (!lead) continue;
            var kort = PREVMAP[sleutel.slice(sleutel.indexOf("_day") + 6)];
            if (!kort) continue;
            var mx = dagmax(tijden, H[sleutel]);
            for (var dag in mx) {
              var r2 = per[dag] = per[dag] || { p1: {}, p2: {} };
              r2[lead][kort] = mx[dag];
            }
          }
        });
      }).catch(function () {});
    })).then(function () {
      try { localStorage.setItem(SLEUTEL("weerbot-ml-prev-v1"),
        JSON.stringify({ t: Date.now(), d: PREV })); } catch (e) {}
    });
  }

  function gemiddelde(o) {
    var v = [];
    for (var i = 0; i < KORT.length; i++) if (typeof o[KORT[i]] === "number") v.push(o[KORT[i]]);
    if (v.length < 4) return null;                  // zelfde drempel als de training
    return v.reduce(function (a, b) { return a + b; }, 0) / v.length;
  }

  /* Spreiding over de p1's, met ddof=1 zoals np.std(p1v, ddof=1) in de training.
     De app rekent zijn eigen spreiding met ddof=0; dat hoort bij de rekenkern en
     blijft daar ongemoeid, maar het is niet de grootheid die deze modellen
     kennen. */
  function spreidingVan(p1) {
    var v = [];
    for (var i = 0; i < KORT.length; i++) if (typeof p1[KORT[i]] === "number") v.push(p1[KORT[i]]);
    if (v.length < 2) return null;
    var gem = v.reduce(function (a, b) { return a + b; }, 0) / v.length, q = 0;
    v.forEach(function (x) { q += (x - gem) * (x - gem); });
    return Math.sqrt(q / (v.length - 1));
  }

  /* Welke reeks bij welke horizon hoort, en waarom horizon 0 niets krijgt.
   *
   * kalibratie.py voedt zijn eigen rekenkern per horizon uit een andere bron:
   * horizon 0 uit de historical-forecast (de run van de dag zelf), horizon 1
   * uit previous_day1 en horizon 2 uit previous_day2. De ML-modellen zijn
   * uitsluitend op previous_day1 getraind, oftewel op horizon 1.
   *
   * Horizon 1 is daarmee de enige waar het model krijgt waar het op is gefit.
   * Horizon 2 krijgt previous_day2: dezelfde grootheid, een dag verder weg —
   * buiten de trainingsafstand, maar wel meetbaar, en schaduw_backtest.py
   * meet hem ook zo (--lead 2).
   *
   * Horizon 0 krijgt niets. Daar heeft de rekenkern de run van vandaag en zou
   * het ML-model het met die van gisteren moeten doen; dan vervang je een
   * verse voorspelling door een oudere. Dat is geen afweging maar een
   * verslechtering, dus er wordt niet eens gerekend. */
  function reeksVoor(r, horizon) {
    if (horizon === 1) return r.p1;
    if (horizon === 2) return r.p2;
    return null;
  }

  function invoerVoor(mk, datum, s, d, horizon) {
    var r = PREV[mk] && PREV[mk][datum];
    if (!r) return null;                            // geen trainingsinvoer: niet voorspellen
    var reeks = reeksVoor(r, horizon);
    if (!reeks) return null;
    var mm1 = gemiddelde(reeks);
    if (mm1 === null) return null;
    /* run2run is de laatste ronde tegenover de vorige. Op horizon 1 is dat p1
       tegen p2; op horizon 2 zou je p3 nodig hebben en die is er niet. In de
       training telt een ontbrekende run2run als nul, en zo komt hij hier ook
       binnen. */
    var mm2 = (horizon === 1) ? gemiddelde(r.p2) : null;
    return { p1: reeks,
             spreiding: spreidingVan(reeks),
             run2run: (mm2 === null) ? null : mm1 - mm2,
             /* lag2_err van de training: de fout van het kale modelgemiddelde
                op t-2 of t-3. index.html levert hem in mlx.lag2. Hiervoor ging
                hier mlx.lag heen, de EWMA-restfout van de eigen rekenkern; die
                heeft een spreiding van 0,56 °C waar de training 1,18 °C zag,
                dus de lagterm telde structureel half mee. */
             lagFout: (d && typeof d.mlx.lag2 === "number")
                      ? deltaNaarC(d.mlx.lag2, s.eenheid) : null,
             aux: (AUX[mk] && AUX[mk][datum]) || {} };
  }

  function schaduwStad(s, uitkomst) {
    if (!klaar || !uitkomst || !uitkomst.dagen) return;
    klaar.then(function () {
      var mk = KEYMAP[s.key]; if (!mk) return;
      uitkomst.dagen.forEach(function (d, horizon) {
        if (!d || !d.mlx) return;
        var inv = invoerVoor(mk, d.datum, s, d, horizon);
        if (!inv) return;
        var ml = WeerbotML.voorspel(mk, d.datum, inv);
        if (!ml) return;
        var muMarkt = Math.round(uitC(ml.mu, s.eenheid) * 10) / 10;
        var sigMarkt = (typeof ml.sigma === "number") ? uitDelta(ml.sigma, s.eenheid) : null;
        WeerbotML.schaduw(s.key, d.datum, horizon, d.verwachting, muMarkt,
                          sigMarkt, s.eenheid);
        d.ml = { mu: muMarkt, sigma: sigMarkt, variant: ml.variant };
        if (magActief(mk, horizon)) {
          var delta = muMarkt - d.verwachting;
          d.verwachting = muMarkt; d.p10 += delta; d.p90 += delta;
        }
      });
    });
  }

  /* Activeren gebeurt per stad en horizon, niet met één schakelaar.
   *
   * Twee redenen. De modellen zijn getraind op p1, de run van de vorige dag,
   * en dat is de horizon "morgen". Op vandaag en overmorgen worden ze buiten
   * hun trainingsafstand gebruikt, en dat hoeft niet in dezelfde richting uit
   * te pakken. En de 14 steden met label LINEAIR hebben geen eigen ML-model;
   * daar is de bestaande kern het bewezen betere antwoord.
   *
   * Het bestand ml_activatie.json staat in de schil van de servicewerker.
   * Wijzigt het, dan dwingt controleer_schil.py een nieuw versienummer af en
   * halen bezoekers het vers op. Terugdraaien is daarmee hetzelfde als het
   * bestand op false zetten en het nummer ophogen. */
  function laadActivatie() {
    return fetch("weerbot-modellen/ml_activatie.json")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (c) { ACTIVATIE = c; })
      .catch(function () { ACTIVATIE = null; });
  }

  function magActief(mk, horizon) {
    if (!ACTIVATIE || !ACTIVATIE.aan) return false;
    if ((ACTIVATIE.nooit_horizons || []).indexOf(String(horizon)) >= 0) return false;
    if ((ACTIVATIE.nooit_labels || []).indexOf(WeerbotML.label(mk)) >= 0) return false;
    var a = ACTIVATIE.aan[mk];
    return !!(a && a[String(horizon)] === true);
  }

  function rapport() {
    var actuals = {};
    try {
      var V = JSON.parse(localStorage.getItem(SLEUTEL("weerbot-verificatie-v1")) || "{}");
      for (var k in V) {
        (V[k].rijen || []).forEach(function (r) {
          var dtm = r.datum || r.dag;
          var echt = (typeof r.echt === "number") ? r.echt :
                     (typeof r.werkelijk === "number") ? r.werkelijk : null;
          if (dtm && echt !== null) (actuals[k] = actuals[k] || {})[dtm] = echt;
        });
      }
    } catch (e) {}
    return WeerbotML.schaduwRapport(actuals);
  }

  if (typeof WeerbotML !== "undefined") {
    klaar = WeerbotML.init({ basis: "weerbot-modellen/" }).then(function (st) {
      if (typeof console !== "undefined") console.log("WeerbotML geladen:", st);
      return Promise.all([haalAux(), haalPrev(), laadActivatie()]);
    }).catch(function (e) {
      if (typeof console !== "undefined") console.warn("WeerbotML niet geladen:", e);
      klaar = null;
    });
  }
  var wortel = (typeof globalThis !== "undefined") ? globalThis : self;
  wortel.WeerbotKoppel = { schaduwStad: schaduwStad, rapport: rapport,
    /* Voor bot/test_ml.py: de rekenstappen los toetsbaar, zonder netwerk. */
    _intern: { dagmax: dagmax, gemiddelde: gemiddelde, spreidingVan: spreidingVan,
               invoerVoor: invoerVoor, magActief: magActief, uitDelta: uitDelta,
               KEYMAP: KEYMAP, PREVMAP: PREVMAP, KORT: KORT,
               zet: function (p, a) { PREV = p; ACTIVATIE = a; } } };
  if (typeof module !== "undefined" && module.exports) module.exports = wortel.WeerbotKoppel;
})();
