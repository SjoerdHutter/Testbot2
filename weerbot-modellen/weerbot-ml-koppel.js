/* Koppelstuk tussen de Weerbot-app en de ML-modellen. Schaduwfase: rekent per
 * stad per dag de ML-voorspelling uit en logt die naast de bestaande, zonder
 * iets aan de getoonde cijfers te veranderen. Na 60 dagen: WeerbotKoppel.rapport()
 * in de console, en pas dan eventueel ACTIEF op true zetten. */
(function () {
  "use strict";
  var ACTIEF = false;   /* WEERBOT_ML_ACTIEF: pas omzetten na de schaduwfase */
  var KEYMAP = { NYC:"nyc", CHI:"chicago", MIA:"miami", LAX:"losangeles",
    SFO:"sanfrancisco", SEA:"seattle", DEN:"denver", DAL:"dallas", HOU:"houston",
    AUS:"austin", ATL:"atlanta", LON:"londen", PAR:"parijs", AMS:"amsterdam",
    MAD:"madrid", MIL:"milaan", MUC:"munchen", WAW:"warschau", HEL:"helsinki",
    ANK:"ankara", IST:"istanbul", MOW:"moskou", TYO:"tokio", SEL:"seoul",
    PUS:"busan", TPE:"taipei", PEK:"peking", SHA:"shanghai", CAN:"guangzhou",
    SZX:"shenzhen", CTU:"chengdu", CKG:"chongqing", WUH:"wuhan", TAO:"qingdao",
    TNA:"jinan", CGO:"zhengzhou", HKG:"hongkong", MNL:"manila",
    KUL:"kualalumpur", SIN:"singapore", KHI:"karachi", LKO:"lucknow",
    JED:"jeddah", TLV:"telaviv", TOR:"toronto", MEX:"mexicostad",
    PTY:"panamastad", BUE:"buenosaires", SAO:"saopaulo", CPT:"kaapstad",
    WLG:"wellington" };
  var ENSMAP = { ecmwf_ifs025:"ifs", ecmwf_aifs025:"aifs", ncep_gefs025:"gfs",
                 icon_seamless:"icon", gem_global:"gem" };
  var AUXV = "relative_humidity_2m_mean,cloud_cover_mean,wind_speed_10m_max," +
             "shortwave_radiation_sum,precipitation_sum";
  var AUX = {};        /* mlKey -> datum -> {rh,bewolking,wind,instraling,neerslag} */
  var klaar = null;

  function naarC(v, e) { return e === "\u00B0F" ? (v - 32) * 5 / 9 : v; }
  function uitC(v, e)  { return e === "\u00B0F" ? v * 9 / 5 + 32 : v; }
  function deltaNaarC(v, e) { return e === "\u00B0F" ? v * 5 / 9 : v; }

  function haalAux() {
    try {
      var c = JSON.parse(localStorage.getItem("weerbot-ml-aux-v1") || "null");
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
      try { localStorage.setItem("weerbot-ml-aux-v1",
        JSON.stringify({ t: Date.now(), d: AUX })); } catch (e) {}
    });
  }

  function schaduwStad(s, uitkomst) {
    if (!klaar || !uitkomst || !uitkomst.dagen) return;
    klaar.then(function () {
      var mk = KEYMAP[s.key]; if (!mk) return;
      uitkomst.dagen.forEach(function (d) {
        if (!d || !d.mlx || !d.mlx.m) return;
        var p1 = {};
        for (var em in d.mlx.m) {
          var kort = ENSMAP[em];
          if (kort && typeof d.mlx.m[em] === "number") p1[kort] = naarC(d.mlx.m[em], s.eenheid);
        }
        var inv = { p1: p1,
                    spreiding: (typeof d.mlx.s === "number") ? deltaNaarC(d.mlx.s, s.eenheid) : null,
                    run2run: null,
                    lagFout: (typeof d.mlx.lag === "number") ? deltaNaarC(d.mlx.lag, s.eenheid) : null,
                    aux: (AUX[mk] && AUX[mk][d.datum]) || {} };
        var ml = WeerbotML.voorspel(mk, d.datum, inv);
        if (!ml) return;
        var muMarkt = Math.round(uitC(ml.mu, s.eenheid) * 10) / 10;
        WeerbotML.schaduw(s.key, d.datum, d.verwachting, muMarkt);
        d.ml = { mu: muMarkt, variant: ml.variant };
        if (ACTIEF && WeerbotML.label(mk) !== "LINEAIR") {
          var delta = muMarkt - d.verwachting;
          d.verwachting = muMarkt; d.p10 += delta; d.p90 += delta;
        }
      });
    });
  }

  function rapport() {
    var actuals = {};
    try {
      var V = JSON.parse(localStorage.getItem("weerbot-verificatie-v1") || "{}");
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
      return haalAux();
    }).catch(function (e) {
      if (typeof console !== "undefined") console.warn("WeerbotML niet geladen:", e);
      klaar = null;
    });
  }
  var wortel = (typeof globalThis !== "undefined") ? globalThis : self;
  wortel.WeerbotKoppel = { schaduwStad: schaduwStad, rapport: rapport };
  if (typeof module !== "undefined" && module.exports) module.exports = wortel.WeerbotKoppel;
})();
