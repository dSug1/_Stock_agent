/* Curator service worker (PWA, D26).
 *
 * Tier C (local/client) offline + instant paint:
 *   - App shell (template, icon, manifest): cache-first.
 *   - GET /api/board: network-first with cache fallback (fresh online, last
 *     board offline) — the SWR spirit on the client.
 *   - Everything else / POSTs: pass through to the network untouched.
 * Bump CACHE_VERSION to invalidate the shell after a template restyle.
 */
"use strict";

const CACHE_VERSION = "curator-v1";
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

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // never intercept POST /api/*

  const url = new URL(req.url);

  if (url.pathname === "/api/board") {
    // Network-first: serve fresh when online, fall back to the last cached board.
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE_VERSION).then((c) => c.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // App shell + static: cache-first, fall back to network.
  event.respondWith(
    caches.match(req).then((cached) => cached || fetch(req))
  );
});
