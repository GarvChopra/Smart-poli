// SmartPoli — caregiver portal pages. One function per page; caregiver.js (the shell) routes to them.
//
//   #/                          Patients
//   #/p/:id/today               Today (alerts, next doses, Remind now, Call / WhatsApp)
//   #/p/:id/medicines           Medicines (adherence + a notes thread per medicine)
//   #/p/:id/notes               Notes (messages to the patient + care-team-only handover notes)
//   #/p/:id/history             History (adherence, missed doses, symptom checks)
//   #/p/:id/emergency           Emergency info
//   #/settings                  Phone alerts, link a patient
//
// Every request goes through auth.js's apiFetch (the caregiver's real token). The server decides who may see what; nothing
// here has access logic. Everything the server sends is passed through esc() before it is drawn.

const api = apiFetch;
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const PRIORITY_LABELS = { emergency: 'Emergency', high: 'High', medium: 'Medium', routine: 'Routine' };
const cgBadge = (p) => (p ? `<span class="badge ${esc(p.level)}">${esc(PRIORITY_LABELS[p.level] || p.level)}</span>` : '');
const fmtDay = (iso) => new Date(iso).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

// ---------------------------------------------------------------- shared: the overview of one patient (cached briefly)

const overviewCache = { pid: null, at: 0, data: null };

async function getOverview(pid, force = false) {
  if (!force && overviewCache.pid === pid && Date.now() - overviewCache.at < 20000) return overviewCache.data;
  const data = await api('GET', `/caregiver/patients/${pid}/overview`);
  Object.assign(overviewCache, { pid, at: Date.now(), data });
  return data;
}

const TABS = [['today', 'Today'], ['medicines', 'Medicines'], ['notes', 'Notes'], ['history', 'History'], ['emergency', 'Emergency']];

/** The frame every patient page shares: back link, name, the tab bar, and a body to fill. */
function patientFrame(root, o, active, bodyHtml) {
  const pid = o.patient.id;
  root.innerHTML = `
    <a class="cg-back" href="#/">← All patients</a>
    <div class="cg-title"><h2>${esc(o.patient.name)}</h2>${cgBadge(o.priority)}</div>
    <nav class="cg-tabs" aria-label="Patient pages">
      ${TABS.map(([key, label]) => `<a href="#/p/${pid}/${key}" class="${key === active ? 'active' : ''}">${label}</a>`).join('')}
    </nav>
    <div class="cg-body">${bodyHtml}</div>`;
}

function emptyBox(text) { return `<div class="empty">${esc(text)}</div>`; }

// ---------------------------------------------------------------- Patients

async function pagePatients(root) {
  root.innerHTML = '<div class="empty">Loading…</div>';
  const patients = await api('GET', '/caregiver/patients');
  const cards = (list) => list.map((p) => `
    <a class="patient-picker-card cg-patient" href="#/p/${p.id}/today">
      <div class="name">${esc(p.name)}</div>
      <div class="meta">${esc(p.age ? p.age + ' yrs' : '')}${esc(p.sex ? ', ' + p.sex : '')}</div>
      <div class="priority-row">${cgBadge(p.priority)}
        ${(p.priority && p.priority.reasons || []).length ? `<ul class="priority-reasons">${p.priority.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}</div>
    </a>`).join('') || emptyBox('No patients match that search.');
  root.innerHTML = `
    <h2>Your patients</h2>
    ${patients.length ? '<input type="text" id="cgSearch" placeholder="Search patients by name…" style="margin-bottom:12px;">' : ''}
    <div id="cgPatients" class="patient-picker-grid">${patients.length ? cards(patients) : emptyBox('No linked patients yet. Link one below.')}</div>
    ${linkFormHtml()}`;
  const search = root.querySelector('#cgSearch');
  if (search) search.addEventListener('input', () => {
    const q = search.value.trim().toLowerCase();
    root.querySelector('#cgPatients').innerHTML = cards(q ? patients.filter((p) => p.name.toLowerCase().includes(q)) : patients);
  });
  wireLinkForm(root);
}

function linkFormHtml() {
  return `
    <div class="card" style="margin-top:18px;">
      <h3>Link a patient</h3>
      <p style="color:var(--ink-soft);font-size:13px;">Ask the patient to create an invite code in their SmartPoli app (Settings → Care team → Link a caregiver), then enter it here.</p>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <input type="text" id="linkCodeInput" placeholder="8-character code" style="flex:1 1 160px;min-width:0;text-transform:uppercase;">
        <button class="primary" id="redeemLinkBtn">Link</button>
      </div>
      <div id="linkStatus" style="margin-top:8px;font-size:13px;color:var(--ink-soft);"></div>
    </div>`;
}

function wireLinkForm(root) {
  const btn = root.querySelector('#redeemLinkBtn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const code = root.querySelector('#linkCodeInput').value.trim();
    const status = root.querySelector('#linkStatus');
    if (!code) return;
    try {
      const result = await api('POST', '/caregiver/link/redeem', { code });
      status.style.color = 'var(--teal-dark)';
      status.textContent = `Linked to ${result.patient.name}.`;
      setTimeout(() => { window.location.hash = `#/p/${result.patient.id}/today`; }, 600);
    } catch (e) {
      status.style.color = 'var(--alarm)';
      status.textContent = e.message;
    }
  });
}

// ---------------------------------------------------------------- Today

const phoneDigits = (p) => String(p || '').replace(/[^\d]/g, '');

async function pageToday(root, pid) {
  root.innerHTML = '<div class="empty">Loading…</div>';
  const o = await getOverview(pid, true);
  const next = (o.upcoming_doses || []).find((d) => ['pending', 'snoozed'].includes(d.state));
  const nextHtml = next
    ? `<div class="cg-next"><div class="reg-meta">Next dose</div><div class="cg-next-name">${esc(next.medicine_name)}</div><div class="reg-meta">${esc(fmtDay(next.scheduled_at))}</div></div>`
    : emptyBox('Nothing scheduled.');
  const alerts = (o.alerts || []).map((a) => `<div class="alert-item">${esc(a)}</div>`).join('') || '<div class="empty">Nothing needs attention.</div>';
  const nudges = (o.nudges || []).map((n) => `<div class="nudge-banner ${esc(n.level)}">${esc(n.text)}</div>`).join('');
  const ph = o.phones || {};
  const own = ph.patient, ec = ph.emergency_contact;
  const contact = [
    own ? `<a class="ghost small cg-link" href="tel:${esc(own)}">Call ${esc(o.patient.name)}</a><a class="ghost small cg-link" href="https://wa.me/${esc(phoneDigits(own))}" target="_blank" rel="noopener">WhatsApp</a>` : '',
    ec ? `<a class="ghost small cg-link" href="tel:${esc(ec.phone)}">Call ${esc(ec.label || 'emergency contact')}</a>` : '',
  ].join('');
  patientFrame(root, o, 'today', `
    ${nudges}
    ${nextHtml}
    <div class="cg-actions">
      <button class="primary" id="cgRemind">Remind now</button>
      ${contact}
    </div>
    <div id="cgRemindMsg" class="reg-meta" role="status"></div>
    <div class="stat-row" style="margin:14px 0;">
      <div class="stat"><div><div class="num">${esc(o.adherence.adherence_percent ?? '—')}${o.adherence.adherence_percent !== null ? '%' : ''}</div><div class="label">Adherence</div></div></div>
      <div class="stat"><div><div class="num">${esc(o.adherence.taken)}</div><div class="label">Taken</div></div></div>
      <div class="stat"><div><div class="num">${esc(o.adherence.missed)}</div><div class="label">Missed</div></div></div>
    </div>
    <div class="card"><div class="card-head">${iconBadge('amber', 'alertCircle')}<h3>Alerts</h3></div>${alerts}</div>`);
  const btn = root.querySelector('#cgRemind');
  const msg = root.querySelector('#cgRemindMsg');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      const r = await api('POST', `/caregiver/patients/${pid}/nudge`);
      msg.textContent = r.sent ? `Reminder sent to ${r.patient}.` : (r.note || 'Nothing was delivered.');
    } catch (e) { msg.textContent = e.message; }
    setTimeout(() => { btn.disabled = false; }, 1500);
  });
}

// ---------------------------------------------------------------- notes: lists and forms shared by Medicines and Notes

function noteListHtml(notes, canDeleteId) {
  if (!notes.length) return emptyBox('No notes yet.');
  return notes.map((n) => `
    <div class="cg-note">
      <div class="cg-note-head"><strong>${esc(n.author_name)}</strong><span class="reg-meta">${esc(fmtDay(n.created_at))}${n.kind === 'message' ? (n.seen ? ' · seen' : ' · not seen yet') : ''}</span></div>
      <div class="cg-note-body">${esc(n.body)}</div>
      ${canDeleteId === n.author_user_id ? `<button class="ghost small" data-del-note="${esc(n.id)}">Delete</button>` : ''}
    </div>`).join('');
}

function noteFormHtml(id, placeholder, buttonLabel, extra = '') {
  return `
    <div class="cg-form" id="${id}">
      <textarea maxlength="500" rows="3" placeholder="${esc(placeholder)}"></textarea>
      ${extra}
      <div class="cg-form-row"><span class="reg-meta"><span data-count>0</span>/500</span><button class="primary small" data-send>${esc(buttonLabel)}</button></div>
      <div class="cg-err" data-err></div>
    </div>`;
}

/** Wire a note form: counts characters, posts, shows the server's plain message on failure, then calls onSaved. */
function wireNoteForm(form, makeBody, onSaved) {
  const ta = form.querySelector('textarea'), err = form.querySelector('[data-err]'), btn = form.querySelector('[data-send]');
  ta.addEventListener('input', () => { form.querySelector('[data-count]').textContent = ta.value.length; });
  btn.addEventListener('click', async () => {
    err.textContent = '';
    btn.disabled = true;
    try { await makeBody(ta.value); ta.value = ''; await onSaved(); } catch (e) { err.textContent = e.message; }
    btn.disabled = false;
  });
}

function wireDeleteButtons(root, onDone) {
  root.querySelectorAll('[data-del-note]').forEach((b) => b.addEventListener('click', async () => {
    if (!window.confirm('Delete this note?')) return;
    try { await api('DELETE', `/caregiver/notes/${b.dataset.delNote}`); await onDone(); } catch (e) { window.alert(e.message); }
  }));
}

// ---------------------------------------------------------------- Medicines

async function pageMedicines(root, pid) {
  root.innerHTML = '<div class="empty">Loading…</div>';
  const o = await getOverview(pid);
  const notes = (await api('GET', `/caregiver/patients/${pid}/notes?kind=medicine`)).notes;
  const meds = o.per_medicine || [];
  const cards = meds.map((m) => `
    <div class="card cg-med" data-med="${esc(m.medicine_id)}">
      <div class="cg-med-head"><strong>${esc(m.name)}</strong>
        <span class="reg-meta">${m.adherence_percent === null ? '—' : esc(m.adherence_percent) + '%'} · ${esc(m.taken)} taken / ${esc(m.missed)} missed</span></div>
      <div class="cg-notes">${noteListHtml(notes.filter((n) => n.medicine_id === m.medicine_id), currentUser.id)}</div>
      ${noteFormHtml(`medForm${m.medicine_id}`, `Add a note about ${m.name} (for example: take with milk)`, 'Add note')}
    </div>`).join('') || emptyBox('No medicines yet.');
  patientFrame(root, o, 'medicines', `<p class="reg-meta">Notes here show on this medicine for the patient and the whole care team. They are not medical advice.</p>${cards}`);
  const reload = () => pageMedicines(root, pid);
  meds.forEach((m) => wireNoteForm(root.querySelector(`#medForm${m.medicine_id}`),
    (text) => api('POST', `/caregiver/patients/${pid}/notes`, { kind: 'medicine', body: text, medicine_id: m.medicine_id }), reload));
  wireDeleteButtons(root, reload);
}

// ---------------------------------------------------------------- Notes

async function pageNotes(root, pid) {
  root.innerHTML = '<div class="empty">Loading…</div>';
  const o = await getOverview(pid);
  const all = (await api('GET', `/caregiver/patients/${pid}/notes`)).notes;
  const messages = all.filter((n) => n.kind === 'message');
  const handover = all.filter((n) => n.kind === 'handover');
  patientFrame(root, o, 'notes', `
    <div class="card">
      <div class="card-head">${iconBadge('teal', 'messageCircle')}<h3>Messages to the patient</h3></div>
      <p class="reg-meta">${esc(o.patient.name)} sees these in their SmartPoli app. They are not medical advice.</p>
      ${noteFormHtml('msgForm', `Write a message to ${o.patient.name}…`, 'Send',
        '<label class="cg-check"><input type="checkbox" data-notify> Also notify their phone now</label>')}
      <div class="cg-notes">${noteListHtml(messages, currentUser.id)}</div>
    </div>
    <div class="card">
      <div class="card-head">${iconBadge('blue', 'fileText')}<h3>Handover notes</h3></div>
      <p class="reg-meta">For the care team only (other caregivers and the doctor). ${esc(o.patient.name)} does not see these.</p>
      ${noteFormHtml('handForm', 'What should the next caregiver know?', 'Add note')}
      <div class="cg-notes">${noteListHtml(handover, currentUser.id)}</div>
    </div>`);
  const reload = () => pageNotes(root, pid);
  const msgForm = root.querySelector('#msgForm');
  wireNoteForm(msgForm, (text) => api('POST', `/caregiver/patients/${pid}/notes`,
    { kind: 'message', body: text, notify: msgForm.querySelector('[data-notify]').checked }), reload);
  wireNoteForm(root.querySelector('#handForm'), (text) => api('POST', `/caregiver/patients/${pid}/notes`, { kind: 'handover', body: text }), reload);
  wireDeleteButtons(root, reload);
}

// ---------------------------------------------------------------- History

async function pageHistory(root, pid) {
  root.innerHTML = '<div class="empty">Loading…</div>';
  const o = await getOverview(pid);
  const missed = (o.recent_missed || []).map((d) => `
    <div class="dose-row"><div class="time">${esc(fmtDay(d.scheduled_at))}</div><div style="flex:1;padding:0 10px;">${esc(d.medicine_name)}</div><span class="badge missed">missed</span></div>`).join('')
    || emptyBox('No missed doses in the last two weeks.');
  const symptoms = (o.recent_symptom_checks || []).map((c) => `
    <div class="dose-row"><div class="time">${esc(new Date(c.created_at).toLocaleDateString())}</div><div style="flex:1;padding:0 10px;">${esc(c.action)}</div><span class="badge ${esc(c.severity.toLowerCase())}">${esc(c.severity)}</span></div>`).join('')
    || emptyBox('No symptom checks recorded.');
  const perMed = (o.per_medicine || []).map((m) => `
    <div class="cg-permed"><span>${esc(m.name)}</span><span class="reg-meta">${m.adherence_percent === null ? '—' : esc(m.adherence_percent) + '%'} · ${esc(m.taken)} taken / ${esc(m.missed)} missed</span></div>`).join('')
    || emptyBox('No medicines yet.');
  patientFrame(root, o, 'history', `
    <div class="card"><div class="card-head">${iconBadge('teal', 'chartBar')}<h3>Per-medicine adherence</h3></div>${perMed}</div>
    <div class="card"><div class="card-head">${iconBadge('alarm', 'clock')}<h3>Missed doses (last 2 weeks)</h3></div>${missed}</div>
    <div class="card"><div class="card-head">${iconBadge('teal', 'stethoscope')}<h3>Recent symptom checks</h3></div>${symptoms}</div>`);
}

// ---------------------------------------------------------------- Emergency

async function pageEmergency(root, pid) {
  root.innerHTML = '<div class="empty">Loading…</div>';
  const o = await getOverview(pid);
  const ec = o.emergency_card;
  patientFrame(root, o, 'emergency', `
    <div class="card">
      <div class="card-head">${iconBadge('alarm', 'siren')}<h3>Emergency information</h3></div>
      ${ec.has_emergency_triage_history ? '<div class="alert-item">Has a history of an EMERGENCY-graded symptom check.</div>' : ''}
      <div><strong>Allergies:</strong> ${esc(ec.patient.allergies || 'none recorded')}</div>
      <div><strong>Blood group:</strong> ${esc(ec.patient.blood_group || 'not recorded')}</div>
      <div><strong>Emergency contact:</strong> ${esc(ec.patient.emergency_contact || 'none recorded')}</div>
      <div style="margin-top:10px;"><a href="${esc(window.location.origin + ec.card_path)}" target="_blank" rel="noopener">Open full emergency card →</a></div>
    </div>`);
}

// ---------------------------------------------------------------- Settings

function _b64ToBytes(b64) {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

const pushOk = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

async function caregiverPhoneSubscription() {
  if (!pushOk()) return null;
  try { const reg = await navigator.serviceWorker.getRegistration('/'); return reg ? await reg.pushManager.getSubscription() : null; } catch (e) { return null; }
}

/** Turn on missed-dose alerts on THIS phone: permission, subscribe, tell the server. */
async function enableCaregiverAlerts() {
  if (!pushOk()) throw new Error('This browser cannot show phone alerts.');
  const key = await api('GET', '/push/public-key');
  if (!key.configured) throw new Error('Phone alerts are not switched on for this server yet.');
  if ((await Notification.requestPermission()) !== 'granted') throw new Error('Notifications are blocked. Allow them in your phone settings and try again.');
  const reg = await navigator.serviceWorker.register('/sw-push.js', { scope: '/' });
  await navigator.serviceWorker.ready;
  const sub = (await reg.pushManager.getSubscription()) || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: _b64ToBytes(key.public_key) });
  const json = sub.toJSON();
  await api('POST', '/caregiver/push/subscribe', { endpoint: json.endpoint, keys: json.keys });
}

async function pageSettings(root) {
  root.innerHTML = '<div class="empty">Loading…</div>';
  const patients = await api('GET', '/caregiver/patients');
  const prefs = await Promise.all(patients.map((p) => api('GET', `/caregiver/patients/${p.id}/prefs`).catch(() => ({ notify_missed: true }))));
  const sub = await caregiverPhoneSubscription();
  root.innerHTML = `
    <h2>Settings</h2>
    <div class="card">
      <div class="card-head">${iconBadge('amber', 'alertCircle')}<h3>Phone alerts</h3></div>
      <p class="reg-meta">Get a notification on this phone when a patient misses a dose.</p>
      <div id="cgAlertState" style="font-weight:600;">${sub ? 'Alerts are on for this phone.' : 'Alerts are off for this phone.'}</div>
      <div class="cg-actions">${sub ? '<button class="ghost small" id="cgAlertsOff">Turn off</button>' : '<button class="primary small" id="cgAlertsOn">Turn on alerts</button>'}</div>
      <div id="cgAlertMsg" class="cg-err"></div>
      ${patients.length ? `<div class="wz-label" style="margin-top:12px;">Alert me about</div>` + patients.map((p, i) => `
        <label class="cg-check"><input type="checkbox" data-pref="${esc(p.id)}" ${prefs[i].notify_missed ? 'checked' : ''}> ${esc(p.name)}</label>`).join('') : ''}
    </div>
    ${linkFormHtml()}`;
  const msg = root.querySelector('#cgAlertMsg');
  const on = root.querySelector('#cgAlertsOn');
  if (on) on.addEventListener('click', async () => { try { await enableCaregiverAlerts(); pageSettings(root); } catch (e) { msg.textContent = e.message; } });
  const off = root.querySelector('#cgAlertsOff');
  if (off) off.addEventListener('click', async () => {
    const s = await caregiverPhoneSubscription();
    if (s) { try { await api('POST', '/caregiver/push/unsubscribe', { endpoint: s.endpoint }); } catch (e) { /* ignore */ } try { await s.unsubscribe(); } catch (e) { /* ignore */ } }
    pageSettings(root);
  });
  root.querySelectorAll('[data-pref]').forEach((c) => c.addEventListener('change', async () => {
    try { await api('PUT', `/caregiver/patients/${c.dataset.pref}/prefs`, { notify_missed: c.checked }); } catch (e) { msg.textContent = e.message; }
  }));
  wireLinkForm(root);
}
