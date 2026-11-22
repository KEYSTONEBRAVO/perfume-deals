// Offline support: the app shell is cached; data is fetched fresh when online
// and the last copy is used when offline.
const CACHE = "perfume-deals-v4";
const SHELL = ["./", "index.html", "manifest.json", "icon-192.png", "icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.origin !== location.origin) {           // product photos: cache as you browse
    if (e.request.destination === "image") {
      e.respondWith(caches.open(CACHE + "-img").then(async c => {
        const hit = await c.match(e.request);
        if (hit) return hit;
        try { const r = await fetch(e.request); if (r.ok || r.type === "opaque") c.put(e.request, r.clone()); return r; }
        catch { return hit || Response.error(); }
      }));
    }
    return;
  }
  // same origin: network first (fresh prices), cache fallback (offline)
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
    return r;
  }).catch(() => caches.match(e.request, { ignoreSearch: true }).then(r => r || caches.match("index.html"))));
});
