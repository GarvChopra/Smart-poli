// SmartPoli Voice — caches only the voice page's own shell so it opens fast
// and installs as an app. Every other request (API, the main app) is left
// to the network untouched.
const CACHE = 'smartpoli-voice-v4';
const SHELL = ['/voice', '/static/voice.css', '/static/voice.js', '/static/voice-engine.js', '/static/voice-commands.js', '/static/auth.js',
  '/static/manifest.webmanifest', '/static/voice-icon.svg', '/static/voice-icon-192.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((k) => k.startsWith('smartpoli-voice-') && k !== CACHE).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || !SHELL.includes(url.pathname)) return;
  // network first, so a new deploy shows up; the cache is only the offline fallback
  e.respondWith(fetch(e.request).then((res) => {
    const copy = res.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copy));
    return res;
  }).catch(() => caches.match(e.request)));
});
