const CACHE_NAME = "sahara-pwa-v4";

const APP_SHELL = [
  "/",
  "/static/index.html",
  "/static/manifest.webmanifest",
  "/static/icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== CACHE_NAME)
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

function isAppHtmlRequest(request, url) {
  if (request.mode === "navigate") return true;
  if (request.destination === "document") return true;
  if (url.pathname === "/" || url.pathname === "/static/index.html") return true;
  return false;
}

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  if (isAppHtmlRequest(event.request, url)) {
    event.respondWith(
      fetch(event.request)
        .then((resp) => {
          if (resp && resp.status === 200) {
            const copy = resp.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return resp;
        })
        .catch(() =>
          caches
            .match(event.request)
            .then((hit) => hit || caches.match("/") || caches.match("/static/index.html"))
        )
    );
    return;
  }

  // Do not intercept other requests. Caching /me, /wallet/summary, etc. breaks auth
  // and shows the wrong onboarding state or kicks users into stale sessions.
});
