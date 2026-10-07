// SmartPoli - service worker for dose-reminder notifications (Web Push).
// Besides notifications, the only thing kept on the phone is the plain "No internet connection" page, shown when the app is
// opened with no connection. Nothing else is cached or intercepted, so it can never serve stale medical data.

const OFFLINE_URL = '/offline';

// A fetch handler is what makes Chrome treat the site as an installable app. Page loads go straight to the network; only if
// the network is unreachable do we answer with the offline page.
self.addEventListener('fetch', (event) => {
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request).catch(async () => (await caches.match(OFFLINE_URL)) || Response.error()));
});

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open('smartpoli-offline-v1').then((c) => c.add(OFFLINE_URL)).catch(() => null).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; }
  catch (e) { data = { title: 'SmartPoli', body: event.data ? event.data.text() : '' }; }
  const title = data.title || 'SmartPoli';
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    tag: data.tag || 'smartpoli',          // same tag replaces, so a repeat never stacks
    renotify: true,
    icon: '/static/app-icon-192.png',
    badge: '/static/app-icon-192.png',
    vibrate: [200, 100, 200],
    requireInteraction: data.kind === 'due' || data.kind === 'missed',
    data: { url: data.url || '/' },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of all) {
      if ('focus' in c) { await c.focus(); return; }
    }
    if (self.clients.openWindow) await self.clients.openWindow(target);
  })());
});
