// SmartPoli - service worker for dose-reminder notifications (Web Push).
// Deliberately does nothing else: no caching, no fetch interception, so it
// can never serve stale medical data. The server decides what to send and
// when (reminders.py); this only displays it and opens the app on tap.

self.addEventListener('install', () => self.skipWaiting());
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
