// SmartPoli — device timezone sync + dose-reminder notifications (Web Push).
// Loaded after auth.js (uses apiFetch) and before app.js.
//
// What this can and cannot promise: the SERVER sends reminders through the
// browser's push service and sw-push.js displays them, so they can arrive
// with the app closed. Whether Android shows them promptly still depends on
// the phone (battery optimisation, "notifications" permission for the
// installed app) — the Settings card below has a "Send a test" button so
// that can be checked on the real device instead of assumed.

const _syncedTimezones = new Set();

/** Tell the server which timezone this device is in, once per patient per
 * page load. Dose times are patient-local wall-clock; reminders and the
 * "missed" judgement are only correct if the server knows the patient's zone. */
async function syncPatientTimezone(patientId) {
  if (!patientId || _syncedTimezones.has(patientId)) return;
  _syncedTimezones.add(patientId);
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (!tz) return;
    const s = await apiFetch('GET', `/patients/${patientId}/settings`);
    if (s.timezone !== tz || !s.timezone_is_set) {
      await apiFetch('PUT', `/patients/${patientId}/settings`, { timezone: tz });
    }
  } catch (e) { /* best effort: the server falls back to its default zone */ }
}

function _urlBase64ToUint8Array(b64) {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

function pushSupported() {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
}

async function _pushRegistration() {
  return navigator.serviceWorker.register('/sw-push.js', { scope: '/' });
}

async function currentPushSubscription() {
  if (!pushSupported()) return null;
  try {
    const reg = await navigator.serviceWorker.getRegistration('/');
    return reg ? await reg.pushManager.getSubscription() : null;
  } catch (e) { return null; }
}

async function enablePushReminders(patientId) {
  if (!pushSupported()) throw new Error('This browser does not support push notifications.');
  const key = await apiFetch('GET', '/push/public-key');
  if (!key.configured || !key.public_key) throw new Error('Reminders are not switched on for this server yet.');
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') throw new Error('Notifications are blocked. Allow them in the app/browser settings.');
  const reg = await _pushRegistration();
  await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: _urlBase64ToUint8Array(key.public_key),
    });
  }
  const json = sub.toJSON();
  await apiFetch('POST', `/patients/${patientId}/push/subscribe`, { endpoint: json.endpoint, keys: json.keys });
  return sub;
}

async function disablePushReminders(patientId) {
  const sub = await currentPushSubscription();
  if (!sub) return;
  try { await apiFetch('POST', `/patients/${patientId}/push/unsubscribe`, { endpoint: sub.endpoint }); } catch (e) { /* ignore */ }
  try { await sub.unsubscribe(); } catch (e) { /* ignore */ }
}

/** Settings card: turn reminders on/off, pick the heads-up time, send a test. */
async function renderNotificationsCard(mount, patientId) {
  if (!mount) return;
  const settings = await apiFetch('GET', `/patients/${patientId}/settings`).catch(() => null);
  const serverKey = await apiFetch('GET', '/push/public-key').catch(() => ({ configured: false }));
  const supported = pushSupported();
  const sub = supported ? await currentPushSubscription() : null;
  const perm = supported ? Notification.permission : 'unsupported';
  const lead = settings ? settings.reminder_lead_minutes : 10;

  let statusText;
  if (!serverKey.configured) statusText = 'Reminders are not switched on for this server yet.';
  else if (!supported) statusText = 'This browser cannot show reminder notifications. Use WhatsApp reminders below.';
  else if (perm === 'denied') statusText = 'Notifications are blocked. Allow them in your phone or browser settings, then come back.';
  else if (sub) statusText = 'Reminders are on for this device.';
  else statusText = 'Reminders are off for this device.';

  mount.innerHTML = `
    <div class="card" id="notifCard">
      <div class="card-head">${iconBadge('teal', 'clock')}<h3>Dose reminders</h3></div>
      <p style="color:var(--ink-soft);font-size:13px;">You get a heads-up before each dose, a reminder at the dose time,
        and a nudge if it isn't marked taken. If a dose is missed you get one message about what to do next.</p>
      <p id="notifStatus" style="font-weight:600;font-size:13.5px;">${statusText}</p>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
        ${serverKey.configured && supported && perm !== 'denied' ? (sub
          ? '<button class="ghost small" id="notifOff">Turn off on this device</button><button class="ghost small" id="notifTest">Send a test</button>'
          : '<button class="primary small" id="notifOn">Turn on reminders</button>') : ''}
        <label style="font-size:13px;color:var(--ink-soft);">Heads-up
          <select id="notifLead" style="margin-left:4px;">
            ${[0, 5, 10, 15, 30].map((m) => `<option value="${m}" ${m === lead ? 'selected' : ''}>${m === 0 ? 'none' : m + ' min before'}</option>`).join('')}
          </select>
        </label>
      </div>
      <p style="color:var(--ink-faint);font-size:12px;margin-top:8px;">Timezone used for reminders: ${settings ? settings.timezone : '—'}.</p>
    </div>`;

  const status = mount.querySelector('#notifStatus');
  const on = mount.querySelector('#notifOn');
  if (on) on.addEventListener('click', async () => {
    try { await enablePushReminders(patientId); } catch (e) { status.textContent = e.message; return; }
    renderNotificationsCard(mount, patientId);
  });
  const off = mount.querySelector('#notifOff');
  if (off) off.addEventListener('click', async () => { await disablePushReminders(patientId); renderNotificationsCard(mount, patientId); });
  const test = mount.querySelector('#notifTest');
  if (test) test.addEventListener('click', async () => {
    status.textContent = 'Sending…';
    try {
      const r = await apiFetch('POST', `/patients/${patientId}/push/test`);
      status.textContent = r.sent ? 'Test sent — it should appear in a few seconds.' : 'The test could not be delivered to any device.';
    } catch (e) { status.textContent = e.message || 'The test failed.'; }
  });
  mount.querySelector('#notifLead').addEventListener('change', async (e) => {
    try { await apiFetch('PUT', `/patients/${patientId}/settings`, { reminder_lead_minutes: Number(e.target.value) }); }
    catch (err) { status.textContent = 'Could not save that.'; }
  });
}


/** Settings card: when the patient's day actually happens, and which optional reminders they want. */
async function renderRoutineCard(mount, patientId) {
  if (!mount) return;
  let r;
  try { r = await apiFetch('GET', `/patients/${patientId}/routine`); } catch (e) { mount.innerHTML = ''; return; }
  const slots = ['morning', 'afternoon', 'evening', 'night', 'bedtime'];
  const rows = slots.map((slot) => `
    <label class="routine-row"><span>${escHtml(r.labels[slot])}</span>
      <input type="time" data-slot="${slot}" value="${escHtml(r.times[slot])}"></label>`).join('');
  mount.innerHTML = `
    <div class="card" id="routineCard">
      <div class="card-head">${iconBadge('teal', 'clock')}<h3>My daily routine &amp; reminders</h3></div>
      <p style="color:var(--ink-soft);font-size:13px;margin-top:0;">Your medicines are timed around <em>your</em> day, not a fixed clock.
        Medicines to take <strong>before food</strong> are placed 30 minutes earlier. Medicines written with an exact time or
        “every 8 hours” keep that time.</p>
      ${rows}
      <div style="margin-top:12px;font-weight:600;font-size:13.5px;">Reminders</div>
      <label class="check-row"><input type="checkbox" id="rtSoon" ${r.notify_soon ? 'checked' : ''}> A last reminder 10 minutes before each dose</label>
      <label class="check-row"><input type="checkbox" id="rtFollow" ${r.notify_followup ? 'checked' : ''}> Ask me if I haven’t marked a dose 15 minutes after</label>
      <div class="reg-meta" style="margin-top:6px;">The early heads-up time is set in “Dose reminders” above. The reminder at the dose time is always sent.</div>
      <div id="rtMsg" style="font-size:13px;margin-top:8px;"></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;">
        <button class="primary small" id="rtSave">Save</button>
        <button class="ghost small" id="rtApply">Save &amp; move my current medicines</button>
      </div>
      ${r.is_saved ? '' : '<div class="reg-meta" style="margin-top:8px;">You haven’t saved a routine yet, so standard times are being used.</div>'}
    </div>`;

  const msg = mount.querySelector('#rtMsg');
  const collect = () => {
    const body = { notify_soon: mount.querySelector('#rtSoon').checked, notify_followup: mount.querySelector('#rtFollow').checked };
    mount.querySelectorAll('input[data-slot]').forEach((i) => { body[i.dataset.slot] = i.value; });
    return body;
  };
  const save = async () => apiFetch('PUT', `/patients/${patientId}/routine`, collect());
  mount.querySelector('#rtSave').addEventListener('click', async () => {
    try { await save(); msg.style.color = 'var(--teal-dark)'; msg.textContent = 'Saved. New medicines will follow this routine.'; }
    catch (e) { msg.style.color = 'var(--amber)'; msg.textContent = e.message; }
  });
  mount.querySelector('#rtApply').addEventListener('click', async () => {
    try {
      await save();
      const res = await apiFetch('POST', `/patients/${patientId}/routine/apply`);
      msg.style.color = 'var(--teal-dark)';
      msg.textContent = res.doses_moved
        ? `Saved. Moved ${res.doses_moved} upcoming dose${res.doses_moved === 1 ? '' : 's'} (${res.medicines_changed} medicine${res.medicines_changed === 1 ? '' : 's'}) to your routine.`
        : 'Saved. Your current medicines already match this routine.';
    } catch (e) { msg.style.color = 'var(--amber)'; msg.textContent = e.message; }
  });
}


/** Dashboard nudge: shown only while reminders are possible but not yet on for this device. One tap asks Android for
 * permission and subscribes. Disappears once on, or when notifications are blocked (Settings explains how to unblock). */
async function renderPushPrompt(mount, patientId) {
  if (!mount || !patientId || !pushSupported()) return;
  try {
    const key = await apiFetch('GET', '/push/public-key');
    if (!key.configured || Notification.permission === 'denied') return;
    if (await currentPushSubscription()) return;
    mount.innerHTML = `
      <div class="push-prompt"><div><strong>Get dose reminders</strong><div class="reg-meta">So you never miss a medicine.</div></div>
        <button class="primary small" id="pushPromptBtn">Turn on</button></div>`;
    // Like any normal app: the first time the person taps anything, Android's own "Allow notifications?" dialog
    // appears. Browsers only allow that dialog right after a tap, so we wait for one. Asked at most once a day, so
    // someone who dismisses it is not nagged (the card above stays as a quiet way to turn it on later).
    const askedKey = 'smartpoli_push_asked';
    let last = 0;
    try { last = Number(localStorage.getItem(askedKey) || 0); } catch (e) { /* ignore */ }
    if (Notification.permission === 'default' && Date.now() - last > 24 * 3600 * 1000) {
      document.addEventListener('pointerup', async () => {
        try { localStorage.setItem(askedKey, String(Date.now())); } catch (e) { /* ignore */ }
        try { await enablePushReminders(patientId); mount.innerHTML = ''; } catch (e) { /* card stays */ }
      }, { once: true });
    }
    mount.querySelector('#pushPromptBtn').addEventListener('click', async () => {
      try { await enablePushReminders(patientId); mount.innerHTML = ''; }
      catch (e) { mount.querySelector('.reg-meta').textContent = e.message; }
    });
  } catch (e) { /* best effort: Settings still has the full control */ }
}
