/* CacheStorage geldt per herkomst, niet per pad: op github.io deelt Weerbot 2
   zijn cachelijst met de eerste Weerbot. Daarom draagt elke cache hier het
   voorvoegsel weerbot2- en ruimt de activate-stap alleen die op. Zonder die
   filter gooit elke app bij het activeren de schil van de ander weg. */
const VOORVOEGSEL = "weerbot2-";
/* Ophogen bij een wijziging in de schil: activate gooit de oude versie weg en
   install haalt alles vers op, zodat niemand op oude bestanden blijft hangen.

   De staart achter het streepje is een vingerafdruk van de schilbestanden en
   wordt niet met de hand gezet. weerbot-modellen/controleer_schil.py rekent hem
   uit, draait mee in de zelftest en valt om zodra de schil wijzigt zonder nieuw
   nummer; --zet werkt hem bij. Onthouden werkte niet: tussen v9 en v10 ging
   portefeuille.html vier keer de deur uit terwijl het nummer bleef staan, en
   bezoekers hielden de oude pagina zonder dat daar iets aan te zien was. */
const VERSIE = VOORVOEGSEL + "v10-78a1baf2";
const SCHIL = ["./", "./index.html", "./portefeuille.html", "./manifest.webmanifest", "./app_params.js", "./weerbot-modellen/polymarkt.js", "./weerbot-modellen/weerbot-ml.js", "./weerbot-modellen/weerbot-ml-koppel.js", "./weerbot-modellen/modellen/modellen.json", "./icon-192.png", "./icon-512.png", "./apple-touch-icon.png"];
/* Gegevens, geen schil: hier hoort de verse versie te komen, niet de bewaarde.
   portfolio.json wordt vier keer per dag herschreven en is het enige dat het
   tabblad portefeuille leest; cache-first zou daar de stand van gisteren tonen
   terwijl het stoplicht juist over vandaag gaat. */
const ALTIJD_VERS = ["portfolio.json"];

/* Netwerk eerst, cache als terugval. portefeuille.html is klein (17 kB) en
   verandert vaker dan de rest van de schil; cache-first betekende daar dat een
   nieuwe versie pas de tweede keer openen zichtbaar werd, en dat gaat mis zodra
   het versienummer hierboven een keer niet is opgehoogd. Dat is precies wat er
   tussen v9 en v10 vier keer achter elkaar gebeurde.

   index.html blijft wél cache-first: dat bestand is 237 kB en die keuze staat
   met gemeten cijfers in README.md (eerste beeld van 4,8 naar 1,7 seconde).
   Offline blijft dit blad werken, want de terugval is de cache. */
const VERS_EERST = ["portefeuille.html"];

self.addEventListener("install", function (e) {
  e.waitUntil(
    caches.open(VERSIE)
      .then(function (c) { return c.addAll(SCHIL); })
      .catch(function () {})
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys().then(function (sleutels) {
      return Promise.all(sleutels.filter(function (k) {
        return k.indexOf(VOORVOEGSEL) === 0 && k !== VERSIE;   // alleen eigen oude versies
      }).map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

/* Eerst uit de cache, dan pas het netwerk.
 *
 * Hiervoor stond het andersom: elk bezoek wachtte op index.html (204 kB) en
 * app_params.js (78 kB) van github.io voordat er iets te zien was, ook al stond
 * alles al op het toestel. Dat kostte twee seconden bij elke start, terwijl de
 * cache alleen dienstdeed als er geen verbinding was.
 *
 * Nu wordt het bewaarde antwoord meteen teruggegeven en haalt de servicewerker
 * de verse versie op de achtergrond op. De schil is daarmee zo snel als lokale
 * bestanden. Prijs: na een nieuwe versie zie je die pas bij de volgende keer
 * openen. De weergegevens zelf komen niet uit deze cache maar rechtstreeks van
 * de weer-API's, dus die blijven altijd actueel.
 */
self.addEventListener("fetch", function (e) {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;
  if (ALTIJD_VERS.some(function (n) { return url.pathname.endsWith("/" + n); })) return;

  if (VERS_EERST.some(function (n) { return url.pathname.endsWith("/" + n); })) {
    e.respondWith(
      caches.open(VERSIE).then(function (c) {
        return fetch(e.request).then(function (antwoord) {
          if (antwoord && antwoord.ok) {
            c.put(e.request, antwoord.clone());
            return antwoord;
          }
          /* Wél een antwoord, maar een foutantwoord. Hiervoor ging dat
             onveranderd door naar het scherm, ook al stond er een bruikbare
             pagina in de cache. Toen Pages een keer uit stond gaf dit blad
             daardoor "There isn't a GitHub Pages site here", terwijl de kaart
             ernaast gewoon doordraaide: die is cache-first en kwam niet eens
             langs het netwerk. Een oude portefeuille met een datum erboven is
             beter dan de 404-pagina van GitHub. */
          return c.match(e.request).then(function (bewaard) {
            return bewaard || antwoord;       // niets bewaard: dan toch de fout
          });
        }).catch(function () {
          return c.match(e.request);          // offline: de bewaarde versie
        });
      })
    );
    return;
  }

  e.respondWith(
    caches.open(VERSIE).then(function (c) {
      return c.match(e.request).then(function (bewaard) {
        const vanNetwerk = fetch(e.request).then(function (antwoord) {
          if (antwoord && antwoord.ok) c.put(e.request, antwoord.clone());
          return antwoord;
        }).catch(function () {
          /* uit de eigen cache, niet uit die van een andere app op deze herkomst */
          return bewaard || c.match("./index.html");
        });
        if (!bewaard) return vanNetwerk;
        vanNetwerk.catch(function () {});   // op de achtergrond bijwerken
        return bewaard;
      });
    })
  );
});
