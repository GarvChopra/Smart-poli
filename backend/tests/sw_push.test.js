// Runs sw-push.js in a stand-in service-worker world and checks what a notification shows and what its buttons do.
// node tests/sw_push.test.js   (also run by tests/test_service_worker.py)
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

function load() {
  const handlers = {};
  const shown = [];
  const fetched = [];
  const state = { fetchReply: null, fetchThrows: false, clients: [], opened: [] };
  const self = {
    location: { origin: 'https://smartpoli.example' },
    addEventListener: (name, fn) => { handlers[name] = fn; },
    skipWaiting: () => {},
    registration: { showNotification: (title, opts) => { shown.push({ title, opts }); return Promise.resolve(); }, pushManager: {} },
    clients: {
      claim: () => Promise.resolve(),
      matchAll: async () => state.clients,
      openWindow: async (u) => { state.opened.push(u); },
    },
  };
  const ctx = {
    self, URL, JSON, Promise, Response: { error: () => ({}) }, console,
    caches: { open: async () => ({ add: async () => {} }), match: async () => null },
    fetch: async (url, opts) => {
      fetched.push({ url, opts });
      if (state.fetchThrows) throw new Error('offline');
      return state.fetchReply;
    },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'sw-push.js'), 'utf8'), ctx);
  return { handlers, shown, fetched, state };
}

const settle = async (p) => { await p; };
function waitUntilEvent(extra) {
  let p = Promise.resolve();
  return Object.assign({ waitUntil: (x) => { p = Promise.resolve(x); }, get done() { return p; } }, extra);
}
const reply = (status, json) => ({ ok: status >= 200 && status < 300, status, json: async () => json });

(async () => {
  // 1. a missed-dose push shows two buttons and keeps the token for them; it stays on screen until acted on
  let w = load();
  let ev = waitUntilEvent({ data: { json: () => ({
    title: 'Mum missed the 9:42 AM dose', body: 'Metformin 500mg wasn\'t marked as taken. Tap Remind now to nudge Mum.', kind: 'missed',
    tag: 'caregiver-missed-1-5', url: '/caregiver#/p/1/today', action_url: '/push/action/nudge', action_token: 'TOKEN', action_label: 'Mum',
    actions: [{ action: 'remind', title: 'Remind now' }, { action: 'open', title: 'Open' }, { action: 'extra', title: 'third' }],
  }) } });
  w.handlers.push(ev); await settle(ev.done);
  let n = w.shown[0];
  assert.strictEqual(n.title, 'Mum missed the 9:42 AM dose');
  assert.deepStrictEqual(n.opts.actions.map((a) => a.action), ['remind', 'open']);          // at most two buttons
  assert.strictEqual(n.opts.requireInteraction, true);
  assert.strictEqual(n.opts.data.action_token, 'TOKEN');
  assert.strictEqual(n.opts.data.url, '/caregiver#/p/1/today');

  // 2. an ordinary reminder has no buttons and does not demand attention beyond its kind
  w = load();
  ev = waitUntilEvent({ data: { json: () => ({ title: 'Time for Metformin', body: 'x', kind: 'lead', tag: 't' }) } });
  w.handlers.push(ev); await settle(ev.done);
  assert.strictEqual(w.shown[0].opts.actions, undefined);
  assert.strictEqual(w.shown[0].opts.requireInteraction, false);

  // 3. a broken payload still shows something rather than nothing
  w = load();
  ev = waitUntilEvent({ data: { json: () => { throw new Error('bad'); }, text: () => 'plain words' } });
  w.handlers.push(ev); await settle(ev.done);
  assert.strictEqual(w.shown[0].title, 'SmartPoli');
  assert.strictEqual(w.shown[0].opts.body, 'plain words');

  const press = async (w, action, data) => {
    const closed = { v: false };
    const e = waitUntilEvent({ action, notification: { close: () => { closed.v = true; }, data } });
    w.handlers.notificationclick(e); await settle(e.done);
    return closed.v;
  };
  const data = { url: '/caregiver#/p/1/today', action_url: '/push/action/nudge', action_token: 'TOKEN', label: 'Mum' };

  // 4. Remind now: posts the token (and nothing else, no cookies/credentials), then says it worked
  w = load();
  w.state.fetchReply = reply(200, { sent: 1, label: 'Mum' });
  assert.ok(await press(w, 'remind', data));
  assert.strictEqual(w.fetched.length, 1);
  assert.strictEqual(w.fetched[0].url, '/push/action/nudge');
  assert.deepStrictEqual(JSON.parse(w.fetched[0].opts.body), { token: 'TOKEN' });
  assert.strictEqual(w.shown.at(-1).title, 'Reminder sent to Mum');
  assert.strictEqual(w.state.opened.length, 0);                                                // the app was never opened

  // 5. every way it can fail says so plainly instead of silently doing nothing
  w = load(); w.state.fetchReply = reply(200, { sent: 0, label: 'Mum', note: 'Mum has not turned on phone reminders.' });
  await press(w, 'remind', data);
  assert.strictEqual(w.shown.at(-1).title, "Couldn't reach Mum");
  assert.ok(/not turned on/.test(w.shown.at(-1).opts.body));

  w = load(); w.state.fetchReply = reply(429, { detail: 'You just sent a reminder. You can send another in 14 minute(s).' });
  await press(w, 'remind', data);
  assert.strictEqual(w.shown.at(-1).title, 'Already reminded');
  assert.ok(/14 minute/.test(w.shown.at(-1).opts.body));

  w = load(); w.state.fetchReply = reply(403, { detail: 'You are not linked to this patient any more.' });
  await press(w, 'remind', data);
  assert.strictEqual(w.shown.at(-1).title, "Couldn't send the reminder");
  assert.ok(/not linked/.test(w.shown.at(-1).opts.body));

  w = load(); w.state.fetchThrows = true;
  await press(w, 'remind', data);
  assert.strictEqual(w.shown.at(-1).title, "Couldn't send the reminder");

  w = load();
  await press(w, 'remind', { url: '/' });                                                      // no token on the notification
  assert.strictEqual(w.fetched.length, 0);
  assert.strictEqual(w.shown.at(-1).title, 'Open SmartPoli to send it');

  // 6. tapping the notification (or Open) goes to that person's page: reuses an open window, else opens one
  w = load();
  const nav = [];
  w.state.clients = [{ focus: async () => {}, navigate: async (u) => { nav.push(u); } }];
  await press(w, '', data);
  assert.deepStrictEqual(nav, ['/caregiver#/p/1/today']);
  w = load();
  await press(w, 'open', data);
  assert.deepStrictEqual(w.state.opened, ['/caregiver#/p/1/today']);
  assert.strictEqual(w.fetched.length, 0);                                                     // Open never sends a reminder

  // 7. a notification can never steer the window to another website
  w = load();
  const nav2 = [];
  w.state.clients = [{ focus: async () => {}, navigate: async (u) => { nav2.push(u); } }];
  await press(w, '', { url: 'https://evil.example/phish' });
  assert.deepStrictEqual(nav2, []);

  console.log('sw_push: all checks passed');
})().catch((e) => { console.error(e); process.exit(1); });
