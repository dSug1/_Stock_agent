/* Curator service worker (PWA, D26).
 *
 * Tier C (local/client) offline + instant paint. Strategy (v2):
 *   - HTML shell (navigations, "/", "/list_results.html") and GET /api/board:
 *     NETWORK-FIRST with cache fallback. The page/template and board are always
 *     the freshest the server has when online, and only fall back to cache
 *     offline. (v1 used cache-first for the shell, which pinned the browser to a
 *     STALE template after edits — the cause of like/heart weirdness. Fixed.)
 *   - Static assets (icon, manifest): cache-first (they rarely change).
 *   - POSTs (e.g. /api/interact): never intercepted — straight to the network.
 * Bump CACHE_VERSION whenever the shell strategy changes to purge old caches.
 */
"use strict";

const CACHE_VERSION = "curator-v2";
const SHELL = ["/", "/list_results.html", "/icon.svg", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

function networkFirst(req) {
  return fetch(req)
    .then((res) => {
      const copy = res.clone();
      caches.open(CACHE_VERSION).then((c) => c.put(req, copy));
      return res;
    })
    .catch(() => caches.match(req).then((c) => c || caches.match("/")));
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // never intercept POST /api/interact etc.

  const url = new URL(req.url);
  const isHTML =
    req.mode === "navigate" ||
    url.pathname === "/" ||
    url.pathname === "/list_results.html";

  if (isHTML || url.pathname === "/api/board") {
    event.respondWith(networkFirst(req)); // always fresh when online
    return;
  }

  // Static assets: cache-first, fall back to network.
  event.respondWith(caches.match(req).then((cached) => cached || fetch(req)));
});
