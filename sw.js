/* CacheStorage geldt per herkomst, niet per pad: op github.io deelt Weerbot 2
   zijn cachelijst met de eerste Weerbot. Daarom draagt elke cache hier het
   voorvoegsel weerbot2- en ruimt de activate-stap alleen die op. Zonder die
   filter gooit elke app bij het activeren de schil van de ander weg. */
const VOORVOEGSEL = "weerbot2-";
/* Ophogen bij een wijziging in de schil: activate gooit de oude versie weg en
   install haalt alles vers op, zodat niemand op oude bestanden blijft hangen. */
const VERSIE = VOORVOEGSEL + "v2";
const SCHIL = ["./", "./index.html", "./manifest.webmanifest", "./app_params.js", "./weerbot-modellen/polymarkt.js", "./weerbot-modellen/weerbot-ml.js", "./weerbot-modellen/weerbot-ml-koppel.js", "./weerbot-modellen/modellen/modellen.json", "./icon-192.png", "./icon-512.png", "./apple-touch-icon.png"];

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
