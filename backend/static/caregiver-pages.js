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
  data.label = data.nickname || data.patient.name;                 // what this caregiver calls them: "Mum"
  Object.assign(overviewCache, { pid, at: Date.now(), data });
  return data;
}

const TABS = [['today', 'Today'], ['medicines', 'Medicines'], ['notes', 'Notes'], ['history', 'History'], ['emergency', 'Emergency']];

/** The frame every patient page shares: back link, name, the tab bar, and a body to fill. */
function patientFrame(root, o, active, bodyHtml) {
  root.innerHTML = `
    <a class="cg-back" href="#/">← Family</a>
    <div class="cg-title"><h2>${esc(o.nickname || o.patient.name)}</h2>${cgBadge(o.priority)}<button class="ghost small" id="cgRename" type="button">Rename</button><button class="ghost small danger-text" id="cgRemovePerson" type="button">Remove</button></div>
    <div class="cg-section">${esc((TABS.find(([key]) => key === active) || [])[1] || '')}</div>
    <div class="cg-body">${bodyHtml}</div>`;
  root.querySelector('#cgRemovePerson').addEventListener('click', () => {
    const who = o.label;
    const m = showSafetyModal(`
      <h3>Remove ${esc(who)}?</h3>
      <p class="reg-meta">You will stop seeing ${esc(who)}'s medicines and stop getting alerts about them. ${esc(who)} and their data are not deleted, and ${esc(who)} is told. You can be added again later with a new code.</p>
      <div id="cgRmPersonMsg" class="auth-error" role="alert"></div>
      <div class="sm-actions"><button class="primary small danger" id="cgRmPersonYes" type="button">Remove ${esc(who)}</button><button class="ghost small" id="cgRmPersonNo" type="button">Cancel</button></div>`);
    m.querySelector('#cgRmPersonNo').addEventListener('click', () => m.remove());
    m.querySelector('#cgRmPersonYes').addEventListener('click', async (e) => {
      e.target.disabled = true;
      try {
        await api('POST', `/caregiver/patients/${o.patient.id}/unlink`);
        overviewCache.pid = null;
        m.remove();
        window.location.hash = '#/';
      } catch (err) { m.querySelector('#cgRmPersonMsg').textContent = err.message; e.target.disabled = false; }
    });
  });
  root.querySelector('#cgRename').addEventListener('click', async () => {
    const next = window.prompt(`What do you call ${o.patient.name}? (e.g. Mum, Dad)`, o.nickname || '');
    if (next === null) return;
    await api('PATCH', `/caregiver/patients/${o.patient.id}/nickname`, { nickname: next.trim() || null });
    o.nickname = next.trim() || null;
    getOverview(o.patient.id, true).then(() => route());
  });
}

function emptyBox(text) { return `<div class="empty">${esc(text)}</div>`; }

// ---------------------------------------------------------------- Patients

const FAMILY_COLOURS = ['rose', 'blue', 'green', 'amber', 'violet'];
const DOSE_PILL = {
  taken: ['taken', 'Taken'], missed: ['missed', 'Missed'], skipped: ['pending', 'Skipped'],
  pending: ['pending', 'Pending'], snoozed: ['pending', 'Snoozed'],
};
const TODAY_PREVIEW = 4;
const CAPSULE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="8.5" width="18" height="7" rx="3.5" transform="rotate(-45 12 12)"/><path d="M9.2 9.2l5.6 5.6"/></svg>';
const clockOf = (iso) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const medCount = (n) => `${n} ${n === 1 ? 'medicine' : 'medicines'} today`;

/** The phone home: "You" first, then the people you look after. Today below it is YOUR doses only; each family
 * member's day is on their own screen (tap their row). */
async function pagePatients(root) {
  root.innerHTML = '<div class="empty">Loading…</div>';
  const home = await api('GET', '/caregiver/home');
  const you = home.you;
  // Someone looks after family but this phone is not set up to be told when a dose is missed: offer it right here.
  const alertsOff = home.family.length > 0 && pushOk() && !(await caregiverPhoneSubscription());
  const youRow = `
    <a class="fam-row fam-you" id="youRow" href="/">
      <span class="fam-avatar green" aria-hidden="true">${esc((currentUser.name || 'Y').trim().charAt(0).toUpperCase())}</span>
      <span class="fam-text"><b>You</b><small>${you.patient_id ? medCount(you.medicines_today) : 'Add your own medicines'}</small></span>
      <span class="fam-chev" aria-hidden="true">›</span>
    </a>`;
  const familyRows = home.family.map((f, i) => `
    <a class="fam-row" href="#/p/${f.id}/today">
      <span class="fam-avatar ${FAMILY_COLOURS[i % FAMILY_COLOURS.length]}" aria-hidden="true">${esc((f.label || '?').trim().charAt(0).toUpperCase())}</span>
      <span class="fam-text"><b>${esc(f.label)}</b>
        <small>${medCount(f.medicines_today)}${f.missed_today ? ` · <span class="fam-missed">${f.missed_today} missed</span>` : ''}</small></span>
      <span class="fam-chev" aria-hidden="true">›</span>
    </a>`).join('');
  const doseRow = (d) => {
    const [cls, label] = DOSE_PILL[d.state] || DOSE_PILL.pending;
    return `<div class="dose-row">
      <span class="dose-icon" aria-hidden="true">${CAPSULE}</span>
      <span class="fam-text"><b>${esc(d.medicine_name)}${d.strength ? ' ' + esc(d.strength) : ''}</b>
        <small>${esc(clockOf(d.scheduled_at))}</small></span>
      <span class="dose-pill ${cls}">${label}</span></div>`;
  };
  const doses = home.today;
  root.innerHTML = `
    <div class="cg-phone">
      <section>
        <div class="cg-row-head"><h2>Family</h2><button class="add-member" id="addMemberBtn" type="button">Add member</button></div>
        <div class="fam-list">${familyRows}${youRow}</div>
        ${alertsOff ? `<div class="fam-alerts"><span>Get an alert on this phone when a dose is missed.</span>
          <button class="add-member" id="alertsOnBtn" type="button">Turn on</button></div><div id="alertsMsg" class="cg-err"></div>` : ''}
        <p class="reg-meta fam-hint">${home.family.length ? '' : 'Add a family member to see their medicines and get an alert if they miss a dose. '}Want someone to look after <i>your</i> medicines? <button class="view-all" id="shareMineBtn" type="button">Share my medicines</button></p>
      </section>
      <section>
        <div class="cg-row-head"><h2>Today</h2>${doses.length > TODAY_PREVIEW ? '<button class="view-all" id="viewAllBtn" type="button">View all</button>' : ''}</div>
        <div class="fam-list" id="todayList">${doses.length ? doses.slice(0, TODAY_PREVIEW).map(doseRow).join('')
          : `<div class="empty">${you.patient_id ? 'Nothing to take today.' : 'Tap You to add your own medicines.'}</div>`}</div>
      </section>
    </div>`;

  // "You" opens the person's own app. The very first time there is no profile yet: make it, then go.
  root.querySelector('#youRow').addEventListener('click', async (e) => {
    if (you.patient_id) return;
    e.preventDefault();
    try {
      const made = await api('POST', '/patients', { name: currentUser.name || 'Me' });
      const auth = getAuth();
      if (auth && auth.user) { auth.user.patient_ids = [made.id]; setAuth(auth.token, auth.user); }
      window.location.href = '/';
    } catch (err) {
      root.querySelector('#youRow small').textContent = err.message || 'Could not open your profile. Please try again.';
    }
  });
  root.querySelector('#addMemberBtn').addEventListener('click', openAddMember);
  const alertsBtn = root.querySelector('#alertsOnBtn');
  if (alertsBtn) alertsBtn.addEventListener('click', async () => {
    try { await enableCaregiverAlerts(); pagePatients(root); }
    catch (e) { root.querySelector('#alertsMsg').textContent = e.message; }
  });
  root.querySelector('#shareMineBtn').addEventListener('click', () => openShareMine(you));
  const all = root.querySelector('#viewAllBtn');
  if (all) all.addEventListener('click', () => {
    const open = all.dataset.open !== '1';
    all.dataset.open = open ? '1' : '0';
    all.textContent = open ? 'Show less' : 'View all';
    root.querySelector('#todayList').innerHTML = (open ? doses : doses.slice(0, TODAY_PREVIEW)).map(doseRow).join('');
  });
}

// ---------------------------------------------------------------- Add member: a link / code to send, or a code you were sent

function inviteMessage(inv) {
  return `${currentUser.name} would like to help look after your medicines on SmartPoli. Tap to join: ${inv.url}\n(Or open SmartPoli and enter code ${inv.code})`;
}

function openAddMember() {
  const m = showSafetyModal(`
    <h3>Add a family member</h3>
    <p class="reg-meta">Ask them to open SmartPoli and tap <b>Share my medicines</b>. They'll get a code. Type it here.</p>
    <label for="amCode">Their code (or paste the link)</label>
    <input type="text" id="amCode" placeholder="e.g. K7Q3-XM2P" autocomplete="off" autocapitalize="characters">
    <div id="amMsg" class="auth-error" role="alert"></div>
    <div class="sm-actions"><button class="primary" id="amGo" type="button">Continue</button></div>
    <div class="am-or"><span>or</span></div>
    <p class="reg-meta" style="margin-top:0;">Can't get a code from them? Send them a link instead.</p>
    <label for="amNick">What do you call them?</label>
    <input type="text" id="amNick" maxlength="30" placeholder="e.g. Mum, Dad" autocomplete="off">
    <div class="sm-actions"><button class="ghost" id="amCreate" type="button">Create invite link</button></div>
    <div class="sm-actions"><button class="ghost small" id="amClose" type="button">Close</button></div>`);
  const input = m.querySelector('#amCode');
  // The next page works out whether it is their code or an invite, and asks for confirmation before anything is shared.
  const go = () => {
    const raw = input.value.trim();
    const last = raw.includes('/') ? raw.split('/').filter(Boolean).pop() : raw;           // a pasted link: take its code
    const code = last.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
    if (code.length < 6 || code.length > 12) { m.querySelector('#amMsg').textContent = 'That does not look like a full code. Check it and try again.'; return; }
    window.location.href = `/join/${encodeURIComponent(code)}`;
  };
  m.querySelector('#amGo').addEventListener('click', go);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  m.querySelector('#amClose').addEventListener('click', () => m.remove());
  m.querySelector('#amCreate').addEventListener('click', async (e) => {
    const btn = e.target;
    btn.disabled = true;
    try {
      const inv = await api('POST', '/family/invites', { nickname: m.querySelector('#amNick').value.trim() || null });
      showInvite(m, inv);
    } catch (err) {
      m.querySelector('#amMsg').textContent = err.message || 'Could not create the invite. Try again.';
      btn.disabled = false;
    }
  });
}

/** Share my medicines: make a one-time code for MY profile; whoever enters it becomes my caregiver (I stay in control). */
async function openShareMine(you) {
  const m = showSafetyModal(`<h3>Share my medicines</h3><p class="reg-meta">Making your code…</p>`);
  try {
    let pid = you.patient_id;
    if (!pid) {                                            // first time: make my own profile
      const made = await api('POST', '/patients', { name: currentUser.name || 'Me' });
      pid = made.id;
      const auth = getAuth();
      if (auth && auth.user) { auth.user.patient_ids = [pid]; setAuth(auth.token, auth.user); }
    }
    const link = await api('POST', `/patients/${pid}/caregiver-links`);
    const code = `${link.code.slice(0, 4)}-${link.code.slice(4)}`;
    const url = link.url;                                   // always the real site's address, from the server
    const text = `${currentUser.name} wants to share their medicines with you on SmartPoli. Tap to connect: ${url}\n(Or open SmartPoli, tap Add member and enter code ${code})`;
    m.querySelector('.safety-modal').innerHTML = `
      <h3>Send this to who will look after you</h3>
      <div class="am-code" aria-label="Your code">${esc(code)}</div>
      <p class="reg-meta" style="text-align:center;">Works once, for 7 days. They'll see your medicines only after they enter it. You can remove them any time in Settings.</p>
      <div class="am-share">
        <a class="primary" target="_blank" rel="noopener" href="https://wa.me/?text=${encodeURIComponent(text)}">Share on WhatsApp</a>
        ${navigator.share ? '<button class="ghost" id="smShare" type="button">Share…</button>' : ''}
        <button class="ghost" id="smCopy" type="button">Copy code</button>
      </div>
      <div id="smMsg" class="reg-meta" role="status" style="text-align:center;"></div>
      <div class="sm-actions"><button class="ghost small" id="smDone" type="button">Done</button></div>`;
    m.querySelector('#smCopy').addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(code); m.querySelector('#smMsg').textContent = 'Code copied.'; }
      catch (e) { m.querySelector('#smMsg').textContent = 'Copy did not work here. Press and hold the code to copy it.'; }
    });
    const sh = m.querySelector('#smShare');
    if (sh) sh.addEventListener('click', () => navigator.share({ text, url }).catch(() => null));
    m.querySelector('#smDone').addEventListener('click', () => { m.remove(); route(); });
  } catch (err) {
    m.querySelector('.safety-modal').innerHTML = `<h3>Couldn't make a code</h3><p class="reg-meta">${esc(err.message || 'Please try again.')}</p>
      <div class="sm-actions"><button class="ghost small" id="smClose" type="button">Close</button></div>`;
    m.querySelector('#smClose').addEventListener('click', () => m.remove());
  }
}

function showInvite(m, inv) {
  const text = inviteMessage(inv);
  m.querySelector('.safety-modal').innerHTML = `
    <h3>Send this to ${esc(inv.nickname || 'them')}</h3>
    <div class="am-code" aria-label="Invite code">${esc(inv.code)}</div>
    <p class="reg-meta" style="text-align:center;">Works once, for 7 days. ${esc(inv.nickname || 'They')} will be asked to confirm before you can see anything.</p>
    <div class="am-share">
      <a class="primary" id="amWa" target="_blank" rel="noopener" href="https://wa.me/?text=${encodeURIComponent(text)}">Share on WhatsApp</a>
      ${navigator.share ? '<button class="ghost" id="amShare" type="button">Share…</button>' : ''}
      <button class="ghost" id="amCopyLink" type="button">Copy link</button>
      <button class="ghost" id="amCopyCode" type="button">Copy code</button>
    </div>
    <div id="amMsg" class="reg-meta" role="status" style="text-align:center;"></div>
    <div class="sm-actions"><button class="ghost small" id="amDone" type="button">Done</button></div>`;
  const say = (t) => { m.querySelector('#amMsg').textContent = t; };
  const copy = async (value, done) => {
    try { await navigator.clipboard.writeText(value); say(done); }
    catch (e) { say('Copy did not work here. Press and hold the text to copy it.'); }
  };
  m.querySelector('#amCopyLink').addEventListener('click', () => copy(inv.url, 'Link copied.'));
  m.querySelector('#amCopyCode').addEventListener('click', () => copy(inv.code, 'Code copied.'));
  const share = m.querySelector('#amShare');
  if (share) share.addEventListener('click', () => navigator.share({ text, url: inv.url }).catch(() => null));
  m.querySelector('#amDone').addEventListener('click', () => m.remove());
}

function linkFormHtml() {
  return `
    <div class="card" style="margin-top:18px;">
      <h3>Link with a patient's code</h3>
      <p style="color:var(--ink-soft);font-size:13px;">Ask the patient to create an invite code in their SmartPoli app (Settings → Care team → Link a caregiver), then enter it here.</p>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <input type="text" id="linkCodeInput" placeholder="8-character code" style="flex:1 1 160px;min-width:0;text-transform:uppercase;">
        <input type="text" id="linkNickInput" placeholder="Nickname, e.g. Mum" maxlength="30" style="flex:1 1 160px;min-width:0;">
        <button class="primary" id="redeemLinkBtn">Add</button>
      </div>
      <div id="linkStatus" style="margin-top:8px;font-size:13px;color:var(--ink-soft);"></div>
    </div>`;
}

function wireLinkForm(root) {
  const btn = root.querySelector('#redeemLinkBtn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const code = root.querySelector('#linkCodeInput').value.trim();
    const nickname = root.querySelector('#linkNickInput').value.trim();
    const status = root.querySelector('#linkStatus');
    if (!code) return;
    try {
      const result = await api('POST', '/caregiver/link/redeem', { code, nickname: nickname || null });
      status.style.color = 'var(--teal-dark)';
      status.textContent = `Added ${nickname || result.patient.name}.`;
      setTimeout(() => { window.location.hash = `#/p/${result.patient.id}/today`; }, 600);
    } catch (e) {
      status.style.color = 'var(--alarm)';
      status.textContent = e.message;
    }
  });
}

// ---------------------------------------------------------------- adding a medicine for the patient
//
// The same popups as in the patient's own app (med-wizard.js): scan the strip or type it, then how often / what time /
// which days / food / how long. The server labels it "Added by <caregiver>" and tells the patient on their phone.

function startAddMedicine(pid, patientName, how, onDone) {
  MedWizard.patientId = () => pid;
  MedWizard.onAdded = (med, out, when) => {
    overviewCache.pid = null;
    const clashes = [...(out.conflicts || []).map((c) => c.medicines.join(' + ')),
      ...(out.interactions || []).map((i) => `${capName(i.drug_a)} + ${capName(i.drug_b)}`)];
    const m = showSafetyModal(`
      <h3>Added ✓</h3>
      <div class="wz-card"><div class="wz-name">${esc(med.name)}</div><div class="wz-meta">${esc(when)}</div></div>
      <div class="reg-meta" style="margin-top:10px;text-align:center;">${esc(patientName)} will get a notification, and their reminders are set.</div>
      ${clashes.length ? `<div class="scan-expired" style="margin-top:10px;">⚠ Check with a pharmacist: ${clashes.map(esc).join(', ')} may not go well together.</div>` : ''}
      <div class="sm-actions"><button class="primary small" id="cgAddDone">Done</button><button class="ghost small" id="cgAddMore">Add another</button></div>`);
    m.querySelector('#cgAddDone').addEventListener('click', () => { m.remove(); onDone(); });
    m.querySelector('#cgAddMore').addEventListener('click', () => { m.remove(); onDone(); medicineWizard({ step: 'details' }); });
  };
  if (how === 'scan') openMedicineCamera(); else if (how === 'pdf') openMedicinePdf(); else medicineWizard({ step: 'details' });
}

/** The popup behind "+ Add medicine" on a person's Today page: the three ways to add one, same as in their own app. */
function openAddMedicinePopup(pid, name, onDone) {
  const m = showSafetyModal(`
    <h3>Add a medicine for ${esc(name)}</h3>
    <div class="cg-add-options">
      <button class="primary" data-how="scan" type="button">📷 Scan a medicine</button>
      <button class="ghost" data-how="pdf" type="button">📄 Upload prescription PDF</button>
      <button class="ghost" data-how="manual" type="button">Enter manually</button>
    </div>
    <div class="reg-meta" style="margin-top:10px;">${esc(name)} sees it marked “Added by you” and can remove it.</div>
    <div class="sm-actions"><button class="ghost small" id="cgAddCancel" type="button">Cancel</button></div>`);
  m.querySelector('#cgAddCancel').addEventListener('click', () => m.remove());
  m.querySelectorAll('[data-how]').forEach((b) => b.addEventListener('click', () => { m.remove(); startAddMedicine(pid, name, b.dataset.how, onDone); }));
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
    own ? `<a class="ghost small cg-link" href="tel:${esc(own)}">Call ${esc(o.label)}</a><a class="ghost small cg-link" href="https://wa.me/${esc(phoneDigits(own))}" target="_blank" rel="noopener">WhatsApp</a>` : '',
    ec ? `<a class="ghost small cg-link" href="tel:${esc(ec.phone)}">Call ${esc(ec.label || 'emergency contact')}</a>` : '',
  ].join('');
  patientFrame(root, o, 'today', `
    ${nudges}
    ${nextHtml}
    <div class="cg-actions">
      <button class="primary" id="cgRemind">Remind now</button>
      <button class="ghost" id="cgAddMedBtn" type="button">+ Add medicine</button>
      ${contact}
    </div>
    <div id="cgRemindMsg" class="reg-meta" role="status"></div>
    <div class="stat-row" style="margin:14px 0;">
      <div class="stat"><div><div class="num">${esc(o.adherence.adherence_percent ?? '—')}${o.adherence.adherence_percent !== null ? '%' : ''}</div><div class="label">Adherence</div></div></div>
      <div class="stat"><div><div class="num">${esc(o.adherence.taken)}</div><div class="label">Taken</div></div></div>
      <div class="stat"><div><div class="num">${esc(o.adherence.missed)}</div><div class="label">Missed</div></div></div>
    </div>
    <div class="card"><div class="card-head">${iconBadge('amber', 'alertCircle')}<h3>Alerts</h3></div>${alerts}</div>`);
  root.querySelector('#cgAddMedBtn').addEventListener('click', () => openAddMedicinePopup(pid, o.label, () => { window.location.hash = `#/p/${pid}/medicines`; }));
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
  // Medicines: only what is being taken now. Adding one is "+ Add medicine" on the Today page; notes live on the Notes page.
  const meds = (o.per_medicine || []).filter((m) => m.taking !== false);
  const cards = meds.map((m) => `
    <div class="card cg-med" data-med="${esc(m.medicine_id)}">
      <div class="cg-med-head"><strong>${esc(m.name)}</strong>
        <span class="reg-meta">${m.adherence_percent === null ? '—' : esc(m.adherence_percent) + '%'} · ${esc(m.taken)} taken / ${esc(m.missed)} missed</span></div>
      ${m.added_by_me ? `<div class="cg-added-row"><span class="reg-chip">Added by you</span>
        <button class="ghost small" data-remove-med="${esc(m.medicine_id)}" data-name="${esc(m.name)}">Remove</button></div>` : ''}
    </div>`).join('') || emptyBox('No medicines are being taken right now.');
  patientFrame(root, o, 'medicines', cards);
  const reload = () => pageMedicines(root, pid);
  root.querySelectorAll('[data-remove-med]').forEach((b) => b.addEventListener('click', () => {
    const m = showSafetyModal(`
      <h3>Remove ${esc(b.dataset.name)}?</h3>
      <div class="sm-headline" style="font-weight:500;">Its reminders stop. What was already taken or missed stays in the history.</div>
      <div class="sm-actions"><button class="primary small" id="cgRmYes">Remove</button><button class="ghost small" id="cgRmNo">Cancel</button></div>`);
    m.querySelector('#cgRmNo').addEventListener('click', () => m.remove());
    m.querySelector('#cgRmYes').addEventListener('click', async () => {
      try { await api('DELETE', `/medicines/${b.dataset.removeMed}`); m.remove(); overviewCache.pid = null; reload(); }
      catch (e) { m.querySelector('.sm-headline').textContent = e.message; }
    });
  }));
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
      <p class="reg-meta">${esc(o.label)} sees these in their SmartPoli app. They are not medical advice.</p>
      ${noteFormHtml('msgForm', `Write a message to ${o.label}…`, 'Send',
        '<label class="cg-check"><input type="checkbox" data-notify> Also notify their phone now</label>')}
      <div class="cg-notes">${noteListHtml(messages, currentUser.id)}</div>
    </div>
    <div class="card">
      <div class="card-head">${iconBadge('blue', 'fileText')}<h3>Handover notes</h3></div>
      <p class="reg-meta">For the care team only (other caregivers). ${esc(o.label)} does not see these.</p>
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
    || emptyBox('No medicines are being taken right now.');
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
    ${ec.has_emergency_triage_history ? '<div class="alert-item">Has a history of an EMERGENCY-graded symptom check.</div>' : ''}
    <div id="cgHealthCard"></div>`);
  // The patient's own card (front and back, flip, Share / Download / Print), read-only for the caregiver.
  await mountHealthCard(root.querySelector('#cgHealthCard'), pid, { caregiver: true });
}

// ---------------------------------------------------------------- Feedback

function pageFeedback(root) { SmartFeedback.renderFeedbackPage(root); }

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
    ${linkFormHtml()}
    <div class="card" style="margin-top:18px;">
      <h3>Your account</h3>
      <div class="cg-actions"><button class="ghost" id="cgSetPassword" type="button">${(getAuth() && getAuth().user && getAuth().user.has_password) ? 'Change password' : 'Set a password'}</button></div>
      <p class="reg-meta">Delete your account and everything in it. This cannot be undone.</p>
      <button class="ghost danger-text" id="cgDeleteAccount" type="button">Delete my account</button>
    </div>`;
  root.querySelector('#cgDeleteAccount').addEventListener('click', openDeleteAccount);
  root.querySelector('#cgSetPassword').addEventListener('click', openSetPassword);
  window.refreshAccountLabels = () => pageSettings(root);
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
