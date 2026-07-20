/* Minimal offline shell for RavenTrade — network-first for API/SSE. */
const CACHE = "raventrade-shell-v63";
const SHELL = ["/", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // Never cache live streams / API
  if (
    url.pathname.startsWith("/live/") ||
    url.pathname.startsWith("/market/") ||
    url.pathname.startsWith("/sniper/") ||
    url.pathname.startsWith("/grok/") ||
    url.pathname.startsWith("/copy/") ||
    url.pathname.startsWith("/research/") ||
    url.pathname.startsWith("/news") ||
    url.pathname.startsWith("/alerts") ||
    url.pathname.startsWith("/portfolio")
  ) {
    return;
  }
  if (event.request.method !== "GET") return;
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(event.request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(event.request).then((r) => r || caches.match("/")))
  );
});
