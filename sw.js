/* CacheStorage geldt per herkomst, niet per pad: op github.io deelt Weerbot 2
   zijn cachelijst met de eerste Weerbot. Daarom draagt elke cache hier het
   voorvoegsel weerbot2- en ruimt de activate-stap alleen die op. Zonder die
   filter gooit elke app bij het activeren de schil van de ander weg. */
const VOORVOEGSEL = "weerbot2-";
const VERSIE = VOORVOEGSEL + "v1";
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

self.addEventListener("fetch", function (e) {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;
  e.respondWith(
    fetch(e.request).then(function (antwoord) {
      const kopie = antwoord.clone();
      caches.open(VERSIE).then(function (c) { c.put(e.request, kopie); });
      return antwoord;
    }).catch(function () {
      /* uit de eigen cache, niet uit die van een andere app op deze herkomst */
      return caches.open(VERSIE).then(function (c) {
        return c.match(e.request).then(function (m) {
          return m || c.match("./index.html");
        });
      });
    })
  );
});
