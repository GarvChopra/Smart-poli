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
  const options = {
    body: data.body || '',
    tag: data.tag || 'smartpoli',          // same tag replaces, so a repeat never stacks
    renotify: true,
    icon: '/static/app-icon-192.png',
    badge: '/static/app-icon-192.png',
    vibrate: [200, 100, 200],
    requireInteraction: data.kind === 'due' || data.kind === 'missed',
    data: { url: data.url || '/', action_url: data.action_url || null, action_token: data.action_token || null, label: data.action_label || '' },
  };
  // Buttons on the notification itself (Android Chrome shows up to two; iPhone shows none, and a tap opens the page).
  if (Array.isArray(data.actions) && data.actions.length) options.actions = data.actions.slice(0, 2);
  event.waitUntil(self.registration.showNotification(title, options));
});

// The browser can replace the subscription on its own. Without this, reminders would keep going to the old one (which
// no longer exists) until the app happened to be opened. The old endpoint proves which account the phone belongs to.
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil((async () => {
    const old = event.oldSubscription;
    let sub = event.newSubscription;
    if (!sub && old && old.options) sub = await self.registration.pushManager.subscribe(old.options);
    if (!old || !sub) return;
    const json = sub.toJSON();
    await fetch('/push/rotate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_endpoint: old.endpoint, endpoint: json.endpoint, keys: json.keys }),
    });
  })().catch(() => null));
});

/** "Remind now" pressed on a missed-dose notification: send the reminder without opening the app, then say how it went. */
async function sendRemindFromNotification(d) {
  const result = (title, body, url) => self.registration.showNotification(title, {
    body, tag: 'remind-result', icon: '/static/app-icon-192.png', badge: '/static/app-icon-192.png', data: { url: url || d.url || '/' },
  });
  if (!d.action_url || !d.action_token) { await result('Open SmartPoli to send it', 'This reminder button is not available any more.'); return; }
  try {
    const res = await fetch(d.action_url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: d.action_token }),
    });
    const j = await res.json().catch(() => ({}));
    const who = j.label || d.label || 'them';
    if (res.ok && j.sent) { await result(`Reminder sent to ${who}`, 'It is on their phone now.'); return; }
    if (res.ok) { await result(`Couldn't reach ${who}`, j.note || 'Try calling instead.'); return; }
    // 429 = one was just sent; 401/403 = the button expired or you are no longer linked. The server's words are plain.
    await result(res.status === 429 ? 'Already reminded' : "Couldn't send the reminder", typeof j.detail === 'string' ? j.detail : 'Open SmartPoli to try again.');
  } catch (e) {
    await result("Couldn't send the reminder", 'Check your connection, or open SmartPoli to try again.');
  }
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const d = event.notification.data || {};
  if (event.action === 'remind') { event.waitUntil(sendRemindFromNotification(d)); return; }
  const target = d.url || '/';
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of all) {
      if ('focus' in c) {
        await c.focus();
        try { if ('navigate' in c && new URL(target, self.location.origin).origin === self.location.origin) await c.navigate(target); } catch (e) { /* stay where it is */ }
        return;
      }
    }
    if (self.clients.openWindow) await self.clients.openWindow(target);
  })());
});
