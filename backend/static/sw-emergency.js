// SmartPoli — offline emergency fallback service worker (Part 16 of the brief).
//
// Scope: /emergency/ only, registered from that page itself
// (main.py's emergency_card_page). Caches the emergency card the first
// time it's opened successfully online, so re-opening the same link with
// no signal still shows something instead of a browser error. Nothing
// else in SmartPoli is made offline-capable — see CLAUDE.md section 11
// ("Only make essential emergency information available offline").

const CACHE_NAME = "smartpoli-emergency-v1";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || !url.pathname.startsWith("/emergency/")) return;

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
