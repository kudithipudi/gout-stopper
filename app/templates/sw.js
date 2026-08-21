const CACHE_VERSION = "goutstopper-v1";
const PREFIX = "{{ prefix }}";
const OFFLINE_URL = PREFIX + "/offline";

const PRECACHE_URLS = [
  PREFIX + "/",
  PREFIX + "/about",
  OFFLINE_URL,
  PREFIX + "/static/css/app.css",
  PREFIX + "/static/js/alpine.min.js",
  PREFIX + "/static/icons/icon-192.png",
  PREFIX + "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_VERSION)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Page navigations: network-first so content stays fresh, falling back to
  // whatever's cached and finally to the offline page when nothing matches.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(
        () => caches.match(request).then((cached) => cached || caches.match(OFFLINE_URL))
      )
    );
    return;
  }

  // Static assets: cache-first, populated on first fetch. Everything else
  // (scan submissions, /admin, /uploads/*) is left untouched and hits the
  // network directly.
  if (url.pathname.startsWith(PREFIX + "/static/")) {
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            const copy = response.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(request, copy));
            return response;
          })
      )
    );
  }
});
