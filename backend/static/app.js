// SmartPoli — vanilla JS SPA. No build step: this file is served as-is by FastAPI.
//
// Auth: this page only renders once auth.js's requireRole('patient') has
// confirmed a real, server-issued token for a patient account. There is no
// client-side role switch any more — the backend (auth.py) is the only
// thing that ever decides who someone is; this file just displays what it
// already proved (see boot() at the bottom).

const currentUser = requireRole('patient');

const state = {
  patients: [],
  patientId: null,
  lang: 'en',            // 'en' | 'hi' — optional Feature G
  activeTab: 'dashboard',
  symptoms: [],
  selectedSymptoms: [],
  triageQueue: [],       // [{symptom_id, question}]
  triageAnswers: {},
  triageSeverity: 'LOW',
  triageDone: false,
};

// apiFetch is defined in auth.js — attaches the bearer token, handles 401.
// Any write may change what the dashboard shows, so it drops the cached one.
function api(method, path, body) {
  if (method !== 'GET') dashboardCache = null;
  return apiFetch(method, path, body);
}

// The dashboard view and the side glance panel both need /dashboard, and every
// tab switch redraws the glance. Each fetch is several database round trips,
// so one response is shared for a few seconds instead of fetched again.
const DASHBOARD_TTL_MS = 15000;
let dashboardCache = null; // { patientId, at, promise }

function getDashboard() {
  const now = Date.now();
  if (dashboardCache && dashboardCache.patientId === state.patientId && now - dashboardCache.at < DASHBOARD_TTL_MS) {
    return dashboardCache.promise;
  }
  const promise = apiFetch('GET', `/patients/${state.patientId}/dashboard`);
  dashboardCache = { patientId: state.patientId, at: now, promise };
  promise.catch(() => { dashboardCache = null; });
  return promise;
}

function el(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function badgeClass(status) { return status.toLowerCase(); }
// ICONS / iconBadge() are defined in icons.js, loaded before this file.

function renderInteractionRows(interactions) {
  if (!interactions.length) {
    return alertCardHtml({ tone: 'clear', title: 'No known interactions among your medicines', problem: '', action: '' });
  }
  return interactions.map(i => {
    const critical = String(i.severity).toUpperCase() === 'CRITICAL';
    return alertCardHtml({
      tone: critical ? 'action' : 'care',
      icon: 'warning',
      title: `${capName(i.drug_a)} + ${capName(i.drug_b)}: ${critical ? 'talk to your doctor before the next dose' : 'use with care'}`,
      problem: i.description,
      action: critical
        ? 'Ask your doctor or pharmacist before taking these together again. Don’t stop either medicine on your own.'
        : 'Keep taking your medicines as prescribed and mention this to your doctor or pharmacist at your next chance.',
    });
  }).join('');
}
function renderFoodWarningRows(warnings) {
  if (!warnings.length) {
    return alertCardHtml({ tone: 'clear', title: 'No food or drink warnings for your medicines', problem: '', action: '' });
  }
  return warnings.map(w => alertCardHtml({
    tone: 'info',
    icon: 'utensils',
    title: `${capName(w.drug)}: avoid ${w.avoid}`,
    problem: w.description,
    action: `While you’re on ${escHtml(capName(w.drug))}, avoid <strong>${escHtml(w.avoid)}</strong>.`,
  })).join('');
}

// ---------------------------------------------------------------- shell

async function loadPatients() {
  state.patients = await api('GET', '/patients');
  const select = document.getElementById('patientSelect');
  select.innerHTML = state.patients.map(p => `<option value="${p.id}">${p.name}</option>`).join('');
  if (!state.patientId && state.patients.length) state.patientId = state.patients[0].id;
  if (state.patientId) select.value = state.patientId;
  // Only people who manage more than one patient (e.g. a parent) ever see a patient switcher.
  document.getElementById('patientGroup').style.display = state.patients.length > 1 ? '' : 'none';
}

document.getElementById('patientSelect').addEventListener('change', (e) => {
  state.patientId = Number(e.target.value);
  renderActiveTab();
});

const UI_STRINGS = {
  en: {
    navPrescriptions: 'Prescription', navDashboard: 'Dashboard', navTriage: 'Symptom check',
    navReport: 'Care report', navTimeline: 'Timeline', navEmergency: 'Emergency card',
    dashboardHeading: "Today's dashboard", prescriptionHeading: 'Decode a prescription',
    triageHeading: 'Symptom check', emergencyHeading: 'Emergency Card', timelineHeading: 'Treatment timeline',
  },
  hi: {
    navPrescriptions: 'पर्ची', navDashboard: 'डैशबोर्ड', navTriage: 'लक्षण जांच',
    navReport: 'केयर रिपोर्ट', navTimeline: 'टाइमलाइन', navEmergency: 'इमरजेंसी कार्ड',
    dashboardHeading: 'आज का डैशबोर्ड', prescriptionHeading: 'पर्ची समझें',
    triageHeading: 'लक्षण जांच', emergencyHeading: 'इमरजेंसी कार्ड', timelineHeading: 'उपचार टाइमलाइन',
  },
};
function t(key) { return (UI_STRINGS[state.lang] || UI_STRINGS.en)[key] || UI_STRINGS.en[key] || key; }

function applyNavTranslation() {
  const setLabel = (tab, text) => {
    document.querySelector(`[data-tab="${tab}"] .nav-label`).textContent = text;
  };
  setLabel('prescriptions', t('navPrescriptions'));
  setLabel('dashboard', t('navDashboard'));
  setLabel('triage', t('navTriage'));
  setLabel('report', t('navReport'));
  setLabel('timeline', t('navTimeline'));
  setLabel('emergency', t('navEmergency'));
}

/** Injects each nav button's icon (from the shared icons.js set) ahead of
 * its label, from the button's own data-icon attribute — one injection
 * point instead of hand-writing the same SVG markup into every HTML page
 * that has a nav bar. */
function injectNavIcons() {
  document.querySelectorAll('nav.pill-nav button[data-icon]').forEach((btn) => {
    if (btn.querySelector('.nav-icon')) return; // already injected
    const icon = ICONS[btn.dataset.icon];
    if (!icon) return;
    btn.insertAdjacentHTML('afterbegin', `<span class="nav-icon">${icon}</span>`);
  });
}

document.getElementById('langSelect').addEventListener('change', (e) => {
  state.lang = e.target.value;
  applyNavTranslation();
  renderActiveTab();
});

function renderSessionChip() {
  const chip = document.getElementById('sessionChip');
  if (!chip || !currentUser) return;
  chip.innerHTML = `
    <span class="who">${currentUser.name}</span>
    <button class="ghost small" id="logoutBtn">Log out</button>
  `;
  document.getElementById('logoutBtn').addEventListener('click', logout);
}

function applyA11yMode() {
  const on = localStorage.getItem('smartpoli_a11y') === '1';
  document.documentElement.dataset.a11y = on ? 'large' : '';
  const btn = document.getElementById('a11yToggleBtn');
  if (btn) btn.setAttribute('aria-pressed', String(on));
}

document.getElementById('a11yToggleBtn').addEventListener('click', () => {
  const on = localStorage.getItem('smartpoli_a11y') === '1';
  localStorage.setItem('smartpoli_a11y', on ? '0' : '1');
  applyA11yMode();
});


document.querySelectorAll('nav.pill-nav button[data-tab]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('nav.pill-nav button[data-tab]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.activeTab = btn.dataset.tab;
    document.querySelectorAll('main.content > section').forEach(s => s.style.display = 'none');
    document.getElementById(`view-${state.activeTab}`).style.display = 'block';
    renderActiveTab();
    closeDrawer();
  });
});

// Header is deliberately minimal — brand + hamburger only. The nav lives in
// an off-canvas drawer (same buttons/classes as the old pill-nav, just
// repositioned by CSS) so every screen size gets one nav pattern, not a
// crowded header row of controls duplicating what's now in Settings.
const hamburgerBtn = document.getElementById('hamburgerBtn');
const navDrawer = document.getElementById('navDrawer');
const navOverlay = document.getElementById('navOverlay');
const closeDrawerBtn = document.getElementById('closeDrawerBtn');

function openDrawer() {
  navDrawer.classList.add('open');
  navOverlay.classList.add('open');
  hamburgerBtn.setAttribute('aria-expanded', 'true');
}
function closeDrawer() {
  navDrawer.classList.remove('open');
  navOverlay.classList.remove('open');
  hamburgerBtn.setAttribute('aria-expanded', 'false');
}
hamburgerBtn.addEventListener('click', openDrawer);
closeDrawerBtn.addEventListener('click', closeDrawer);
navOverlay.addEventListener('click', closeDrawer);

function renderNoPatientState() {
  const view = document.getElementById(`view-${state.activeTab}`);
  if (view && state.activeTab !== 'settings') view.innerHTML = '<div class="empty">One moment…</div>';
  askFirstProfile();
}

/** First login: a short popup (name + age) instead of a "create patient" page. It cannot be dismissed, because
 * everything in the app belongs to a profile. The name is filled in from the account. */
function askFirstProfile() {
  if (document.getElementById('firstProfileOverlay')) return;
  const overlay = el(`
    <div class="safety-modal-overlay" id="firstProfileOverlay" role="dialog" aria-modal="true">
      <form class="safety-modal" id="firstProfileForm" autocomplete="off">
        <h3>Welcome to SmartPoli</h3>
        <p style="color:var(--ink-soft);margin:4px 0 14px;">Tell us a little about you.</p>
        <label for="fpName">Your name</label>
        <input id="fpName" type="text" required maxlength="80" value="${escHtml((currentUser && currentUser.name) || '')}">
        <div class="fp-row">
          <div><label for="fpAge">Age</label>
            <input id="fpAge" type="number" inputmode="numeric" required min="0" max="120" placeholder="e.g. 54"></div>
          <div><label for="fpSex">Sex</label>
            <select id="fpSex" required><option value="">Select</option><option value="F">Female</option><option value="M">Male</option><option value="Other">Other</option></select></div>
        </div>
        <label for="fpBlood" style="margin-top:10px;display:block;">Blood group <span class="reg-meta">(optional)</span></label>
        <select id="fpBlood"><option value="">Not sure</option>${['A+','A-','B+','B-','AB+','AB-','O+','O-'].map((g) => `<option>${g}</option>`).join('')}</select>
        <label for="fpAllergy" style="margin-top:10px;display:block;">Allergies <span class="reg-meta">(optional)</span></label>
        <input id="fpAllergy" type="text" maxlength="200" placeholder="e.g. Penicillin">
        <label for="fpContact" style="margin-top:10px;display:block;">Emergency contact <span class="reg-meta">(optional)</span></label>
        <input id="fpContact" type="text" maxlength="120" placeholder="Name, phone number">
        <div id="fpMsg" style="color:#8E2018;font-size:13px;margin-top:8px;"></div>
        <div class="sm-actions" style="margin-top:14px;"><button class="primary" type="submit" id="fpSave">Continue</button></div>
      </form>
    </div>`);
  document.body.appendChild(overlay);
  overlay.querySelector('#fpName').focus();
  overlay.querySelector('#firstProfileForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = overlay.querySelector('#fpName').value.trim();
    const age = Number(overlay.querySelector('#fpAge').value);
    const msg = overlay.querySelector('#fpMsg');
    if (!name) { msg.textContent = 'Please enter your name.'; return; }
    if (!Number.isFinite(age) || age < 0 || age > 120) { msg.textContent = 'Please enter a valid age.'; return; }
    const sex = overlay.querySelector('#fpSex').value;
    if (!sex) { msg.textContent = 'Please choose your sex.'; return; }
    const val = (id) => overlay.querySelector(id).value.trim() || null;
    const btn = overlay.querySelector('#fpSave');
    btn.disabled = true;
    try {
      const patient = await api('POST', '/patients', {
        name, age: Math.round(age), sex, blood_group: val('#fpBlood'), allergies: val('#fpAllergy'), emergency_contact: val('#fpContact'),
      });
      overlay.remove();
      await loadPatients();
      state.patientId = patient.id;
      document.getElementById('patientSelect').value = patient.id;
      renderActiveTab();
      renderGlance();
    } catch (err) {
      btn.disabled = false;
      msg.textContent = err.message || 'Could not save. Please try again.';
    }
  });
}

function renderActiveTab() {
  // No profile yet (first login): the name-and-age popup opens; nothing else can render without one.
  if (!state.patientId) { renderNoPatientState(); return; }
  syncPatientTimezone(state.patientId);
  if (state.activeTab === 'prescriptions') renderPrescriptions();
  if (state.activeTab === 'dashboard') renderDashboard();
  if (state.activeTab === 'safety') renderSafetyCenter();
  if (state.activeTab === 'triage') renderTriage();
  if (state.activeTab === 'report') renderReport();
  if (state.activeTab === 'timeline') renderTimeline();
  if (state.activeTab === 'emergency') renderEmergencyCard();
  if (state.activeTab === 'feedback') SmartFeedback.renderFeedbackPage(document.getElementById('view-feedback'));
  if (state.activeTab === 'settings') {
    renderSettingsCareTeam(); renderSettingsWhatsApp();
    renderNotificationsCard(document.getElementById('settingsNotifications'), state.patientId);
    renderRoutineCard(document.getElementById('settingsRoutine'), state.patientId);
    renderSettingsPersonal();
  }
  renderGlance();
}

// ---------------------------------------------------------------- safety center (Part 3)

async function renderSafetyCenter() {
  const view = document.getElementById('view-safety');
  view.innerHTML = `<div class="empty">Loading...</div>`;
  const s = await api('GET', `/patients/${state.patientId}/safety-center`);

  const medsHtml = s.medicines.map(m => `
    <div class="safety-med-row">
      <div style="flex:1;min-width:0;">
        <strong>${escHtml(m.name)}</strong>
        ${m.generic_name ? `<div class="generic">generic: ${escHtml(m.generic_name)}</div>` : ''}
        <div data-med-info="${escHtml(m.name)}"></div>
      </div>
      <button class="ghost small" data-remove-med="${m.medicine_id}" data-name="${escHtml(m.name)}">Remove</button>
    </div>
  `).join('') || '<div class="empty">No active medicines yet.</div>';

  const dosageHtml = s.dosage_warnings.map(w => alertCardHtml({
    tone: w.severity === 'REVIEW' ? 'action' : 'care',
    icon: 'alertCircle',
    title: `${capName(w.medicine)}: please check this line`,
    problem: w.message,
    action: 'Compare this line with your original prescription. If it is different, correct it on the Prescription tab.',
  })).join('');

  view.innerHTML = `
    <h2>Medication safety center</h2>
    <p style="color:var(--ink-soft);">One place for interaction checks, food warnings and basic dosage sanity checks — all rule-based, none of it a clinical review.</p>

    <div class="card">
      <div class="card-head">${iconBadge('teal', 'shield')}<h3>Your medicines</h3></div>
      ${medsHtml}
    </div>
    ${dosageHtml ? `<div class="card"><div class="card-head">${iconBadge('amber', 'alertCircle')}<h3>Dosage checks</h3></div>${dosageHtml}</div>` : ''}
    <div class="card">
      <div class="card-head">${iconBadge('amber', 'clock')}<h3>Medicine timing &amp; spacing</h3></div>
      <div id="safetyConflicts" class="empty">Checking your schedule…</div>
    </div>
    <div class="card">
      <div class="card-head">${iconBadge('amber', 'warning')}<h3>Medicines that shouldn’t be taken together</h3></div>
      <div id="safetyCombos" class="empty">Checking…</div>
    </div>
    <div class="card">
      <div class="card-head">${iconBadge('teal', 'shield')}<h3>Check sources</h3></div>
      <p style="color:var(--ink-soft);font-size:13px;margin-top:0;">Checks your medicines against official lists (India CDSCO, US FDA).</p>
      <div id="safetyRegulatory"><button class="primary small" id="regCheckBtn">Check now</button></div>
    </div>
    <div class="two-col">
      <div class="card">
        <div class="card-head">${iconBadge('amber', 'warning')}<h3>Drug interactions</h3></div>
        ${renderInteractionRows(s.interactions)}
      </div>
      <div class="card">
        <div class="card-head">${iconBadge('amber', 'utensils')}<h3>Food &amp; substance warnings</h3></div>
        ${renderFoodWarningRows(s.food_warnings)}
      </div>
    </div>
    <footer class="disclaimer">${s.disclaimer}</footer>
  `;
  loadConflictsInto(document.getElementById('safetyConflicts'));
  wireRemoveButtons(view, () => renderSafetyCenter());
  loadMedicineInfoInto(view);
  loadCombinationCheckInto(document.getElementById('safetyCombos'));
  document.getElementById('regCheckBtn').addEventListener('click', async () => {
    const mount = document.getElementById('safetyRegulatory');
    mount.innerHTML = '<div class="empty">Checking…</div>';
    let data = null;
    try { data = await api('GET', `/patients/${state.patientId}/regulatory`); } catch (e) { data = null; }
    mount.innerHTML = regulatoryHtml(data);
  });
}

// ---------------------------------------------------------------- at-a-glance rail
// Fills the wide-viewport side column so it never reads as empty/broken —
// always shows next dose, today's adherence and anything needing attention,
// independent of whichever tab is open.

async function renderGlance() {
  const panel = document.getElementById('glancePanel');
  if (!panel || !state.patientId) return;
  const patient = state.patients.find(p => p.id === state.patientId);
  const dash = await getDashboard();
  const pct = dash.adherence.adherence_percent;
  const next = dash.upcoming_doses[0];

  const criticalInteractions = dash.interactions.filter(i => i.severity === 'CRITICAL');
  const alertsHtml = [
    criticalInteractions.length
      ? `<div class="glance-alert">${criticalInteractions.length} critical drug interaction(s)</div>` : '',
    dash.prn_medicines.length
      ? `<div class="glance-empty">${dash.prn_medicines.length} as-needed medicine(s) on file</div>` : '',
  ].join('') || '<div class="glance-empty">Nothing needs attention.</div>';

  panel.innerHTML = `
    <div class="glance-card">
      <div class="glance-card-head"><h4>At a glance — ${patient ? patient.name : ''}</h4></div>
      <div class="glance-block glance-ring">
        <span class="ring" data-pct="${pct === null ? '—' : pct + '%'}" style="--ring-pct:${pct === null ? 0 : pct}"></span>
        <div>
          <div style="font-weight:700;font-size:13.5px;">Medication adherence</div>
          <div class="glance-empty">${pct === null ? 'No doses acted on yet' : pct >= 90 ? "You're doing great!" : 'Keep it up for better outcomes'}</div>
        </div>
      </div>
      <div class="glance-block" style="display:flex;align-items:center;gap:10px;">
        ${iconBadge('teal', 'pill')}
        <div style="flex:1;">
          <h4 style="margin-bottom:2px;">Next dose</h4>
          ${next
            ? `<div class="glance-next-dose">${next.medicine_name}<span class="when">${new Date(next.scheduled_at).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}</span></div>`
            : '<div class="glance-empty">Nothing scheduled.</div>'}
        </div>
      </div>
      <div class="glance-block" style="display:flex;align-items:flex-start;gap:10px;margin-bottom:0;">
        ${iconBadge('amber', 'alertCircle')}
        <div style="flex:1;">
          <h4 style="margin-bottom:2px;">Needs attention</h4>
          ${alertsHtml}
        </div>
      </div>
    </div>
    <div class="glance-tip">${iconBadge('teal', 'leaf')}<div><strong>Small steps.</strong><br>Healthier tomorrows.</div></div>
  `;
}

// ---------------------------------------------------------------- prescriptions (Feature 1)

function renderPrescriptions(lastResult) {
  const view = document.getElementById('view-prescriptions');
  view.innerHTML = `
    <h2>${t('prescriptionHeading')}</h2>
    <p style="color:var(--ink-soft)">Add a medicine in one tap, or type it in.</p>

    <div class="card">
      <button class="primary add-big add-full" id="manualBtn"><span class="icon">${ICONS.pill}</span>Enter manually</button>
      <div style="text-align:center;margin-top:12px;">
        <button class="ghost small" id="chooseFileBtn"><span class="icon">${ICONS.fileText}</span>Choose file</button>
        <span class="reg-meta">&nbsp;a photo of a whole prescription</span>
      </div>
      <input type="file" id="rxImageInput" accept="image/*" hidden>
      <div id="uploadStatus"></div>
    </div>

    <div class="card">
      <div class="card-head">${iconBadge('teal', 'pill')}<h3>Or type it in</h3></div>
      <label for="rxInput">Enter the prescription exactly as written</label>
      <textarea id="rxInput" placeholder="Tab Dolo 650mg 1-0-1 PC x5d
Cap Amoxicillin 500mg TDS AC 7 days
Syrup Crocin 5ml SOS"></textarea>
      <div style="margin-top:12px;display:flex;gap:10px;flex-wrap:wrap;">
        <button class="primary" id="decodeBtn"><span class="icon">${ICONS.shield}</span>Decode</button>
        <button class="ghost" id="fillExampleBtn">Fill example</button>
      </div>
    </div>
    <div id="rxResult"></div>
  `;

  document.getElementById('manualBtn').addEventListener('click', () => medicineWizard({ step: 'details' }));

  document.getElementById('fillExampleBtn').addEventListener('click', () => {
    document.getElementById('rxInput').value =
      'Tab Dolo 650mg 1-0-1 PC x5d\nCap Amoxicillin 500mg TDS AC 7 days\nSyrup Crocin 5ml SOS\nTab X 1-?-1';
  });

  document.getElementById('decodeBtn').addEventListener('click', async () => {
    const lines = document.getElementById('rxInput').value.split('\n').map(l => l.trim()).filter(Boolean);
    if (!lines.length) return;
    const result = await api('POST', '/prescriptions', { patient_id: state.patientId, lines });
    renderRxResult(result);
  });

  const rxInput = document.getElementById('rxImageInput');
  const chooseBtn = document.getElementById('chooseFileBtn');
  chooseBtn.addEventListener('click', () => rxInput.click());                              // opens the phone's files
  rxInput.addEventListener('change', async () => {
    if (!rxInput.files.length) return;
    const status = document.getElementById('uploadStatus');
    const setStatus = (kind, text) => {
      status.className = kind ? `upload-status-banner ${kind}` : '';
      status.innerHTML = kind === 'loading' ? `<span class="upload-spinner"></span><span>${text}</span>` : text;
    };
    chooseBtn.disabled = true;
    setStatus('loading', 'Reading photo — a full-size phone photo can take up to a minute…');
    const form = new FormData();
    form.append('patient_id', state.patientId);
    form.append('file', rxInput.files[0]);
    try {
      const auth = getAuth();
      dashboardCache = null;  // the photo becomes a draft prescription
      const res = await fetch('/prescriptions/from-image', {
        method: 'POST', body: form,
        headers: auth && auth.token ? { Authorization: `Bearer ${auth.token}` } : {},
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        setStatus('error', escHtml(detail.detail || `Could not read that photo (${res.status}).`));
        return;
      }
      const result = await res.json();
      setStatus(null, '');
      showQuickPopup('Photo read successfully', null, `${result.ocr_lines_found} medicine line(s) found`);
      renderRxResult(result);
    } catch (e) {
      setStatus('error', 'Could not reach the server. Use manual entry instead.');
    } finally {
      chooseBtn.disabled = false;
      rxInput.value = '';
    }
  });

  if (lastResult) renderRxResult(lastResult);

}

// ---------------------------------------------------------------- add a medicine: name, then "how do you take it"
//
//   Enter manually -> name, strength, form -> times a day, at what time, repeat days, with food, for how long.
// Nothing is added until the person answers the last step; the line then goes through the normal prescription
// review gate, so SmartPoli never invents a dose or schedule.

// ---------------------------------------------------------------- after adding: what it is for, and any clash with the other medicines

const waitMs = (ms) => new Promise((r) => setTimeout(r, ms));

function medInfoBlock(info) {
  const chips = (info.conditions || []).map((c) => `<span class="mi-chip">${escHtml(c)}</span>`).join('');
  const list = (title, items) => items && items.length ? `<div class="mi-sub">${title}</div><ul class="mi-list">${items.map((i) => `<li>${escHtml(i)}</li>`).join('')}</ul>` : '';
  return `
    <div class="mi-for">For: <strong>${escHtml(info.what_for)}</strong></div>
    ${chips ? `<div class="mi-chips">${chips}</div>` : ''}
    <details class="rp-more"><summary>Side effects &amp; care</summary>
      ${list('Common side effects', info.side_effects)}${list('Take care', info.take_care)}
      <div class="reg-meta" style="margin-top:6px;">${escHtml(info.note || '')}</div></details>`;
}

/** Fetch what each medicine is for (cached on the server; missing ones are looked up in the background) and fill every
 * `[data-med-info]` placeholder inside root. Tries again once for anything still being looked up. */
async function loadMedicineInfoInto(root, onData) {
  for (let attempt = 0; attempt < 3; attempt++) {
    if (!document.body.contains(root)) return;
    const data = await api('GET', `/patients/${state.patientId}/medicine-info`).catch(() => null);
    if (!data) return;
    const byName = new Map(data.medicines.filter((m) => m.known).map((m) => [m.name.toLowerCase(), m]));
    root.querySelectorAll('[data-med-info]').forEach((slot) => {
      const info = byName.get(slot.dataset.medInfo.toLowerCase());
      if (info && !slot.dataset.filled) { slot.innerHTML = medInfoBlock(info); slot.dataset.filled = '1'; }
    });
    if (onData) onData(data);
    if (!data.pending.length || !data.enabled) return;
    await waitMs(attempt === 0 ? 6000 : 9000);
  }
}

function pairWarningHtml(w) {
  const [a, b] = w.medicines;
  const sug = w.suggestion;
  const timesText = sug ? sug.new_times.map(fmtClock).join(', ') : '';
  return alertCardHtml({
    tone: 'action',
    title: `Don’t take ${a} and ${b} together`,
    problem: w.message,
    action: sug
      ? `Take <strong>${escHtml(a)}</strong> at <strong>${escHtml(timesText)}</strong> instead. Nothing changes unless you tap the button.`
      : 'Ask your pharmacist how far apart to take them. Until then, don’t change your doses on your own.',
    buttons: sug ? `<button class="primary small" data-shift="${sug.medicine_id}" data-minutes="${sug.shift_minutes}" data-times="${escHtml(timesText)}" style="margin-top:8px;">Move ${escHtml(a)} to ${escHtml(timesText)}</button>` : '',
    details: `<div class="reg-meta" style="margin-top:6px;">${escHtml(w.source_label || '')}${w.quote ? ` “${escHtml(w.quote)}”` : ''}</div>`,
  });
}

function interactionWarningHtml(i) {
  return alertCardHtml({
    tone: 'action',
    title: `${capName(i.drug_a)} and ${capName(i.drug_b)} can interact`,
    problem: i.description,
    action: 'Ask your pharmacist before taking them together.',
  });
}

async function afterMedicineAdded(med, out, when, sos) {
  const m = showSafetyModal(`
    <h3>Added ✓</h3>
    <div class="wz-card"><div class="wz-name">${escHtml(med.name)}</div><div class="wz-meta">${escHtml(when)}</div>
      <div data-med-info="${escHtml(med.name)}" class="wz-for"></div></div>
    <div class="reg-meta wz-check" id="wzCheck" style="margin-top:10px;text-align:center;">
      ${sos ? 'You can log a dose from the dashboard whenever you need it.' : '<span class="upload-spinner"></span> Checking it against your other medicines…'}</div>
    <div class="sm-actions"><button class="primary small" id="wzDone">Done</button><button class="ghost small" id="wzAnother">Add another</button></div>`);
  const finish = () => { m.remove(); renderPrescriptions(); renderGlance(); };
  m.querySelector('#wzDone').addEventListener('click', finish);
  m.querySelector('#wzAnother').addEventListener('click', () => { m.remove(); medicineWizard({ step: 'details' }); });
  loadMedicineInfoInto(m);                                           // "Used for: ..." appears when the lookup finishes
  if (sos) return;

  let pair = [];
  try { pair = (await api('GET', `/medicines/${out.medicine.id}/pair-check`)).warnings || []; } catch (e) { pair = []; }
  const curated = out.conflicts || [], inter = out.interactions || [];
  if (!document.body.contains(m)) return;
  if (!curated.length && !inter.length && !pair.length) {
    m.querySelector('#wzCheck').textContent = '✓ No known clash with your other medicines.';
    return;
  }
  const warn = showSafetyModal(`
    <h3>⚠ Don’t take these together</h3>
    ${curated.map((c) => compactClashHtml(c.medicines, c.medicine_ids)).join('')}
    ${pair.map((w) => compactClashHtml(w.medicines, w.medicine_ids, w.suggestion)).join('')}
    ${inter.map((i) => compactClashHtml([capName(i.drug_a), capName(i.drug_b)], i.medicine_ids)).join('')}
    <div class="sm-actions"><button class="primary small" id="wwOk">OK</button></div>`);
  warn.classList.add('cw-modal');
  warn.querySelector('#wwOk').addEventListener('click', () => { warn.remove(); renderPrescriptions(); renderGlance(); });
  wireConflictActions(warn);
  wireShiftButtons(warn);
  wireRemoveButtons(warn, () => { warn.remove(); renderPrescriptions(); renderGlance(); });
}

/** "Move X to <time>" buttons on keep-apart warnings: shift every upcoming dose of that medicine, then say so. */
function wireShiftButtons(root, onMoved) {
  root.querySelectorAll('[data-shift]').forEach((btn) => btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      await api('POST', `/medicines/${btn.dataset.shift}/shift`, { minutes: Number(btn.dataset.minutes) });
      dashboardCache = null;
      btn.outerHTML = `<div class="wz-moved">Moved to ${escHtml(btn.dataset.times)} ✓</div>`;
      if (onMoved) onMoved();
    } catch (e) { btn.disabled = false; showNotice('Could not move it', e.message); }
  }));
}

/** After a typed / photographed prescription is confirmed: the same "please check this" popup, only if something clashes. */
async function checkPrescriptionCombinations(prescriptionId) {
  let warnings = [];
  try { warnings = (await api('GET', `/prescriptions/${prescriptionId}/pair-check`)).warnings || []; } catch (e) { return; }
  if (!warnings.length) return;
  const warn = showSafetyModal(`
    <h3>⚠ Don’t take these together</h3>
    ${warnings.map((w) => compactClashHtml(w.medicines, w.medicine_ids, w.suggestion)).join('')}
    <div class="sm-actions"><button class="primary small" id="wwOk">OK</button></div>`);
  warn.classList.add('cw-modal');
  warn.querySelector('#wwOk').addEventListener('click', () => { warn.remove(); renderActiveTab(); });
  wireShiftButtons(warn);
  wireRemoveButtons(warn, () => { warn.remove(); renderActiveTab(); });
}

/** Safety center card: every pair among the person's medicines that should be kept apart. */
async function loadCombinationCheckInto(mount) {
  for (let attempt = 0; attempt < 3; attempt++) {
    if (!document.body.contains(mount)) return;
    const data = await api('GET', `/patients/${state.patientId}/pair-check`).catch(() => null);
    if (!data) { mount.innerHTML = '<div class="empty">Could not check right now.</div>'; return; }
    const warn = data.warnings.length ? data.warnings.map((w) => `<div class="ow-item">${pairWarningHtml(w)}${removeButtonsHtml(w.medicines, w.medicine_ids)}</div>`).join('')
      : '<div class="mi-ok">✓ No known clash between your medicines.</div>';
    mount.innerHTML = `${warn}${data.pending ? '<div class="reg-meta"><span class="upload-spinner"></span> Still checking some combinations…</div>' : ''}
      <div class="reg-meta" style="margin-top:6px;">${escHtml(data.note)}</div>`;
    wireShiftButtons(mount, () => loadCombinationCheckInto(mount));
    wireRemoveButtons(mount, () => renderSafetyCenter());
    if (!data.pending) return;
    await waitMs(attempt === 0 ? 7000 : 10000);
  }
}


/** "Remove <medicine>": asks first, stops its reminders and hides it everywhere; past history stays. */
async function removeMedicine(id, name) {
  const yes = await confirmDialog(`Remove ${name}?`, 'Its reminders will stop and it will disappear from your lists. Your past history stays.', 'Remove', 'Keep it', true);
  if (!yes) return false;
  try {
    await api('DELETE', `/medicines/${id}`);
    dashboardCache = null;
    return true;
  } catch (e) { showNotice('Could not remove it', e.message); return false; }
}

function wireRemoveButtons(root, onRemoved) {
  root.querySelectorAll('[data-remove-med]').forEach((btn) => btn.addEventListener('click', async () => {
    if (await removeMedicine(btn.dataset.removeMed, btn.dataset.name)) onRemoved();
  }));
}

/** One short row per problem: the two medicines, one line, and a Remove button for each (plus "Move ... to <time>" when a safer time exists). */
function compactClashHtml(names, ids, suggestion) {
  const [a, b] = names;
  const btns = names.map((n, i) => ids && ids[i]
    ? `<button class="ghost small rm-btn" data-remove-med="${ids[i]}" data-name="${escHtml(n)}">Remove ${escHtml(n)}</button>` : '').join('');
  const move = suggestion
    ? `<button class="primary small" data-shift="${suggestion.medicine_id}" data-minutes="${suggestion.shift_minutes}" data-times="${escHtml(suggestion.new_times.map(fmtClock).join(', '))}">Move ${escHtml(suggestion.medicine)} to ${escHtml(suggestion.new_times.map(fmtClock).join(', '))}</button>` : '';
  return `<div class="cw-item"><div class="cw-names">${escHtml(a)} + ${escHtml(b)}</div>
    <div class="cw-note">Can cause problems when taken together.</div>
    <div class="cw-actions">${btns}${move}</div></div>`;
}

const removeButtonsHtml = (names, ids) => {
  const rows = names.map((n, i) => ids && ids[i]
    ? `<div class="rm-row"><span>${escHtml(n)}</span><button class="ghost small rm-btn" data-remove-med="${ids[i]}" data-name="${escHtml(n)}">Remove</button></div>` : '').join('');
  return rows ? `<div class="rm-box"><div class="rm-title">Remove one of them?</div>${rows}</div>` : '';
};

// ---------------------------------------------------------------- Settings: personal information

/** The same details asked at first login, editable here. Saving updates the profile and refreshes the names shown around the app. */
async function renderSettingsPersonal() {
  const mount = document.getElementById('settingsPersonal');
  if (!mount || !state.patientId) return;
  const p = await api('GET', `/patients/${state.patientId}`).catch(() => null);
  if (!p) { mount.innerHTML = '<div class="empty">Could not load your details.</div>'; return; }
  const sexOption = (v, l) => `<option value="${v}" ${p.sex === v ? 'selected' : ''}>${l}</option>`;
  mount.innerHTML = `
    <form class="sp-form" id="spForm" autocomplete="off">
      <label for="spName">Name</label>
      <input id="spName" type="text" required maxlength="80" value="${escHtml(p.name || '')}">
      <div class="fp-row">
        <div><label for="spAge">Age</label>
          <input id="spAge" type="number" inputmode="numeric" required min="0" max="120" value="${escHtml(p.age == null ? '' : p.age)}"></div>
        <div><label for="spSex">Sex</label>
          <select id="spSex" required><option value="">Select</option>${sexOption('F', 'Female')}${sexOption('M', 'Male')}${sexOption('Other', 'Other')}</select></div>
      </div>
      <label for="spBlood" style="margin-top:10px;display:block;">Blood group <span class="reg-meta">(optional)</span></label>
      <select id="spBlood"><option value="">Not sure</option>${['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'].map((g) => `<option ${p.blood_group === g ? 'selected' : ''}>${g}</option>`).join('')}</select>
      <label for="spAllergy" style="margin-top:10px;display:block;">Allergies <span class="reg-meta">(optional)</span></label>
      <input id="spAllergy" type="text" maxlength="200" placeholder="e.g. Penicillin" value="${escHtml(p.allergies || '')}">
      <label for="spContact" style="margin-top:10px;display:block;">Emergency contact <span class="reg-meta">(optional)</span></label>
      <input id="spContact" type="text" maxlength="120" placeholder="Name, phone number" value="${escHtml(p.emergency_contact || '')}">
      <div class="sp-msg" id="spMsg" role="status"></div>
      <button class="primary" type="submit" id="spSave" style="margin-top:10px;">Save</button>
    </form>`;
  const form = mount.querySelector('#spForm');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const msg = form.querySelector('#spMsg');
    msg.className = 'sp-msg';
    const name = form.querySelector('#spName').value.trim();
    const age = Number(form.querySelector('#spAge').value);
    const sex = form.querySelector('#spSex').value;
    if (!name) { msg.textContent = 'Please enter your name.'; return; }
    if (!Number.isFinite(age) || age < 0 || age > 120) { msg.textContent = 'Please enter a valid age.'; return; }
    if (!sex) { msg.textContent = 'Please choose your sex.'; return; }
    const btn = form.querySelector('#spSave');
    btn.disabled = true;
    try {
      await api('PATCH', `/patients/${state.patientId}`, {
        name, age: Math.round(age), sex,
        blood_group: form.querySelector('#spBlood').value,
        allergies: form.querySelector('#spAllergy').value.trim(),
        emergency_contact: form.querySelector('#spContact').value.trim(),
      });
      await loadPatients();
      renderGlance();
      msg.className = 'sp-msg ok';
      msg.textContent = 'Saved ✓';
    } catch (err) {
      msg.textContent = err.message || 'Could not save. Please try again.';
    }
    btn.disabled = false;
  });
}

// ---------------------------------------------------------------- when the app is opened: medicines that shouldn't be taken together

const OPEN_WARN_KEY = 'smartpoli_open_warn';

/** A popup, once each time the app is opened, if the person's medicines have a REAL problem: they interact, a source says
 * they must be kept apart while they are scheduled together, or a verified timing rule is broken. Nothing minor or
 * uncertain ever appears here, and it stays silent when everything is fine. Any medicine can be removed from it. */
async function showOpenWarnings() {
  if (!state.patientId || document.getElementById('openWarnOverlay')) return;
  let data = null;
  for (let attempt = 0; attempt < 3; attempt++) {                    // combinations still being looked up show up a few seconds later
    data = await api('GET', `/patients/${state.patientId}/open-warnings`).catch(() => null);
    if (!data || data.has_problems || !data.pending) break;
    await waitMs(attempt === 0 ? 7000 : 10000);
  }
  if (!data || !data.has_problems) return;
  try { if (sessionStorage.getItem(OPEN_WARN_KEY) === `${state.patientId}:${data.key}`) return; } catch (e) { /* ignore */ }
  const overlay = showSafetyModal(`
    <h3>⚠ Don’t take these together</h3>
    ${data.interactions.map((i) => compactClashHtml([capName(i.drug_a), capName(i.drug_b)], i.medicine_ids)).join('')}
    ${data.pair_warnings.map((w) => compactClashHtml(w.medicines, w.medicine_ids, w.suggestion)).join('')}
    ${data.timing.map((c) => compactClashHtml(c.medicines, c.medicine_ids)).join('')}
    <div class="sm-actions"><button class="primary small" id="owOk">OK</button></div>`);
  overlay.classList.add('cw-modal');
  overlay.id = 'openWarnOverlay';
  const close = () => {
    overlay.remove();
    try { sessionStorage.setItem(OPEN_WARN_KEY, `${state.patientId}:${data.key}`); } catch (e) { /* ignore */ }
  };
  overlay.querySelector('#owOk').addEventListener('click', close);
  wireConflictActions(overlay);
  wireShiftButtons(overlay);
  wireRemoveButtons(overlay, () => { overlay.remove(); renderActiveTab(); renderGlance(); showOpenWarnings(); });
}

function fmtClock(hhmm) {
  const [h, mi] = hhmm.split(':').map(Number);
  return `${((h + 11) % 12) + 1}:${String(mi).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
}

const MED_FORMS = [['tab', 'Tablet'], ['cap', 'Capsule'], ['syrup', 'Syrup'], ['inj', 'Injection']];
const FREQUENCIES = [
  ['once', 'Once a day'], ['twice', 'Twice a day'], ['thrice', 'Three times a day'], ['four', 'Four times a day'], ['sos', 'Only when needed'],
];
// which of the person's daily-routine times each frequency starts from (they can change every time, like an alarm)
const FREQ_SLOTS = { once: ['morning'], twice: ['morning', 'night'], thrice: ['morning', 'afternoon', 'night'], four: ['morning', 'afternoon', 'evening', 'night'] };
const DEFAULT_TIMES = { morning: '08:00', afternoon: '14:00', evening: '18:00', night: '20:30', bedtime: '22:00' };
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function medicineWizard(ctx) {
  const med = ctx.med || {};
  if (ctx.step === 'details') {
    const m = showSafetyModal(`
      <h3>Add a medicine</h3>
      <label for="wzName">Medicine name</label>
      <input id="wzName" type="text" maxlength="80" placeholder="e.g. Dolo 650" value="${escHtml(med.name || '')}">
      <div class="fp-row">
        <div><label for="wzStrength">Strength <span class="reg-meta">(optional)</span></label>
          <input id="wzStrength" type="text" maxlength="30" placeholder="e.g. 500mg" value="${escHtml(med.strength || '')}"></div>
        <div><label for="wzForm">Form</label>
          <select id="wzForm">${MED_FORMS.map(([v, l]) => `<option value="${v}" ${(med.form || 'tab') === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
      </div>
      <div id="wzMsg" class="wz-msg"></div>
      <div class="sm-actions">
        <button class="primary small" id="wzNext">Next</button>
        <button class="ghost small" id="wzCancel">Cancel</button>
      </div>`);
    m.querySelector('#wzName').focus();
    m.querySelector('#wzCancel').addEventListener('click', () => m.remove());
    m.querySelector('#wzNext').addEventListener('click', () => {
      const name = m.querySelector('#wzName').value.trim();
      if (name.length < 2) { m.querySelector('#wzMsg').textContent = 'Please enter the medicine name.'; return; }
      m.remove();
      medicineWizard({ step: 'schedule', med: { name, strength: m.querySelector('#wzStrength').value.trim(), form: m.querySelector('#wzForm').value } });
    });
    return;
  }

  // step: schedule - set it like an alarm: how many times, at what time, which days, for how long
  const m = showSafetyModal(`
    <h3>Set reminders for ${escHtml(med.name)}</h3>
    <div class="wz-label">How many times a day?</div>
    <div class="wz-chips" id="wzFreq">${FREQUENCIES.map(([v, l]) => `<button type="button" class="wz-chip" data-v="${v}">${l}</button>`).join('')}</div>
    <div id="wzTimesBox" hidden>
      <div class="wz-label">At what time?</div>
      <div id="wzTimes" class="wz-times"></div>
    </div>
    <div id="wzRepeatBox" hidden>
      <div class="wz-label">Repeat</div>
      <div class="wz-chips" id="wzRepeat">
        <button type="button" class="wz-chip on" data-v="daily">Every day</button>
        <button type="button" class="wz-chip" data-v="days">Choose days</button>
      </div>
      <div class="wz-chips wz-days is-locked" id="wzDays">${WEEKDAYS.map((d, i) => `<button type="button" class="wz-chip wz-day on" data-d="${i}">${d}</button>`).join('')}</div>
    </div>
    <div id="wzFoodBox" hidden>
      <div class="wz-label">With food?</div>
      <div class="wz-chips" id="wzFood">
        <button type="button" class="wz-chip" data-v="before">Before food</button>
        <button type="button" class="wz-chip on" data-v="after">After food</button>
        <button type="button" class="wz-chip" data-v="any">Doesn’t matter</button>
      </div>
    </div>
    <div id="wzLenBox" hidden>
      <div class="wz-label">For how long?</div>
      <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
        <input id="wzLen" type="number" min="1" max="365" placeholder="days" style="width:100px;">
        <label style="display:flex;gap:6px;align-items:center;"><input type="checkbox" id="wzOngoing"> Ongoing</label>
      </div>
    </div>
    <div id="wzMsg" class="wz-msg"></div>
    <div class="sm-actions">
      <button class="primary small" id="wzAdd">Add medicine</button>
      <button class="ghost small" id="wzBack">Back</button>
    </div>`);

  let routineTimes = DEFAULT_TIMES;
  api('GET', `/patients/${state.patientId}/routine`).then((r) => { if (r && r.times) routineTimes = { ...DEFAULT_TIMES, ...r.times }; }).catch(() => {});

  const chosen = (id) => (m.querySelector(`#${id} .wz-chip.on`) || {}).dataset || {};
  const drawTimes = (freq) => {
    const slots = FREQ_SLOTS[freq] || [];
    m.querySelector('#wzTimes').innerHTML = slots.map((slot, i) => `
      <label class="wz-time"><span>${slots.length > 1 ? `Dose ${i + 1}` : 'Time'}</span>
        <input type="time" value="${escHtml(routineTimes[slot] || DEFAULT_TIMES[slot])}" data-time></label>`).join('');
  };
  const pickOne = (id, after) => m.querySelectorAll(`#${id} .wz-chip`).forEach((b) => b.addEventListener('click', () => {
    m.querySelectorAll(`#${id} .wz-chip`).forEach((x) => x.classList.remove('on'));
    b.classList.add('on');
    if (after) after(b.dataset.v);
  }));
  pickOne('wzFreq', (v) => {
    const sos = v === 'sos';
    m.querySelector('#wzTimesBox').hidden = sos;
    m.querySelector('#wzRepeatBox').hidden = sos;
    m.querySelector('#wzFoodBox').hidden = sos;
    m.querySelector('#wzLenBox').hidden = sos;
    if (!sos) drawTimes(v);
  });
  const daysRow = m.querySelector('#wzDays');
  pickOne('wzRepeat', (v) => {
    const every = v !== 'days';
    daysRow.classList.toggle('is-locked', every);                       // "Every day": all seven shown selected, not tappable
    m.querySelectorAll('.wz-day').forEach((b) => b.classList.toggle('on', every));   // "Choose days": start with none selected
  });
  pickOne('wzFood');
  m.querySelectorAll('.wz-day').forEach((b) => b.addEventListener('click', () => {
    if (!daysRow.classList.contains('is-locked')) b.classList.toggle('on');
  }));
  m.querySelector('#wzBack').addEventListener('click', () => { m.remove(); medicineWizard({ step: 'details', med }); });

  m.querySelector('#wzAdd').addEventListener('click', async () => {
    const msg = m.querySelector('#wzMsg');
    const freq = chosen('wzFreq').v;
    if (!freq) { msg.textContent = 'Choose how many times a day.'; return; }
    const sos = freq === 'sos';
    const times = sos ? [] : [...m.querySelectorAll('[data-time]')].map((i) => i.value).filter(Boolean);
    if (!sos && times.length !== (FREQ_SLOTS[freq] || []).length) { msg.textContent = 'Set a time for every dose.'; return; }
    if (!sos && new Set(times).size !== times.length) { msg.textContent = 'Each dose needs a different time.'; return; }
    let weekdays = null;
    if (!sos && chosen('wzRepeat').v === 'days') {
      weekdays = [...m.querySelectorAll('.wz-day.on')].map((b) => Number(b.dataset.d));
      if (!weekdays.length) { msg.textContent = 'Choose at least one day.'; return; }
    }
    const len = Number(m.querySelector('#wzLen').value);
    const ongoing = m.querySelector('#wzOngoing').checked;
    if (!sos && !ongoing && !(len >= 1)) { msg.textContent = 'Enter the number of days, or tick Ongoing.'; return; }
    const btn = m.querySelector('#wzAdd'); btn.disabled = true;
    try {
      const out = await api('POST', '/medicines/manual', {
        patient_id: state.patientId, name: med.name, strength: med.strength || null, form: med.form || 'tab',
        as_needed: sos, times, weekdays, food: sos ? 'any' : chosen('wzFood').v || 'any',
        duration_days: sos || ongoing ? null : Math.round(len),
      });
      m.remove();
      dashboardCache = null;
      const when = sos ? 'Only when needed' : times.map(fmtClock).join(', ') + (weekdays && weekdays.length < 7 ? ' · ' + weekdays.map((d) => WEEKDAYS[d]).join(' ') : ' · every day');
      afterMedicineAdded(med, out, when, sos);
    } catch (e) { btn.disabled = false; msg.textContent = e.message || 'Could not add it.'; }
  });
}

const VERIF_LABELS = {
  transcription: { confirmed_transcription: 'Confirmed by you', needs_patient_review: 'Needs your review', unreadable_needs_patient_input: 'Could not be read' },
  catalogue_match: { exact: 'Matches a known name', approximate: 'Approximate name match', unresolved: 'Name not matched' },
  schedule: { ready: 'Ready', requires_clarification: 'Needs clarification' },
  regulatory: { verification_pending: 'See Safety center' },
};
const VERIF_OVERALL = {
  ready_for_reminder_activation: 'Ready for reminders',
  needs_patient_review: 'Needs your review',
  medicine_match_unresolved: 'Name not matched',
  schedule_requires_clarification: 'Schedule needs clarification',
};

/** Four separate checks. One passing says nothing about the others. */
function verificationHtml(v) {
  if (!v) return '';
  const row = (label, group) => `<span class="reg-chip">${label}: ${escHtml((VERIF_LABELS[group] || {})[v[group]] || v[group])}</span>`;
  return `<div class="verif-row">
    ${row('Transcription', 'transcription')}${row('Name', 'catalogue_match')}${row('Schedule', 'schedule')}${row('Official status', 'regulatory')}
    <div class="reg-meta">${escHtml(VERIF_OVERALL[v.overall] || v.overall)} · ${escHtml(v.note)}</div>
  </div>`;
}

function renderRxResult(result) {
  const box = document.getElementById('rxResult');
  const medsHtml = result.medicines.map(m => `
    <div class="medicine-line ${m.status === 'needs_confirmation' ? 'needs_confirmation' : ''}" data-med-id="${m.id}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px;">
        <div>
          <strong>${m.name || '(name not read)'}</strong> ${m.dose_amount ? m.dose_amount + (m.dose_unit || '') : ''}
          <div class="raw">"${m.raw_text}"</div>
        </div>
        <span class="badge ${badgeClass(m.status)}">${m.status.replace('_', ' ')}</span>
      </div>
      <div class="plain">${state.lang === 'hi' && m.plain_language_hi ? m.plain_language_hi : (m.plain_language || '')}</div>
      ${verificationHtml(m.verification)}
      <div style="margin-top:8px;">
        ${Object.entries(m.field_confidence).filter(([k]) => k !== 'source').map(([k, v]) => `
          <div class="confidence-row">
            <span class="field-name">${k}</span>
            <span class="confidence-track"><span class="confidence-fill ${badgeClass(m.status)}" style="width:${Math.round(v * 100)}%"></span></span>
            <span class="confidence-pct">${v >= 0.85 ? 'High' : v >= 0.70 ? 'Medium' : 'Low'}</span>
          </div>
        `).join('')}
      </div>
      ${m.status === 'needs_confirmation' ? `
        <div style="margin-top:12px;border-top:1px dashed var(--line);padding-top:12px;">
          <label>This couldn't be read confidently — confirm the schedule shorthand (e.g. 1-0-1, BD, TDS, SOS):</label>
          <div style="display:flex;gap:8px;flex-wrap:wrap;">
            <input type="text" id="fix-${m.id}" placeholder="1-0-1" style="flex:1 1 140px;min-width:0;">
            <button class="ghost small" data-fix-id="${m.id}">Confirm</button>
          </div>
        </div>
      ` : ''}
    </div>
  `).join('');

  box.innerHTML = `
    <div class="card">
      <div class="card-head">${iconBadge('teal', 'shield')}<h3>Parsed medicines</h3></div>
      <p style="color:var(--ink-soft);font-size:13px;margin-top:-8px;">Review the extracted medicines and confirm the details before creating your schedule.</p>
      ${medsHtml || '<div class="empty">Nothing parsed yet.</div>'}
      <button class="primary" id="confirmRxBtn" style="margin-top:8px;">Confirm &amp; schedule</button>
      <div id="confirmSummary"></div>
    </div>
  `;

  box.querySelectorAll('[data-fix-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.fixId;
      const schedule_code = document.getElementById(`fix-${id}`).value.trim();
      if (!schedule_code) return;
      await api('PATCH', `/medicines/${id}`, { schedule_code });
      // Re-render just this line as verified by re-fetching the whole prescription state via a soft reload.
      const line = box.querySelector(`[data-med-id="${id}"]`);
      line.classList.remove('needs_confirmation');
      line.querySelector('.badge').className = 'badge verified';
      line.querySelector('.badge').textContent = 'verified';
      line.querySelector('div[style*="dashed"]')?.remove();
      // keep the medicine object in result in sync so confirm works with fresh status
      const med = result.medicines.find(m => m.id === Number(id));
      if (med) med.status = 'verified';
    });
  });

  document.getElementById('confirmRxBtn').addEventListener('click', async () => {
    const summary = await api('POST', `/prescriptions/${result.prescription_id}/confirm`);
    const s = document.getElementById('confirmSummary');
    s.innerHTML = `
      <div style="margin-top:14px;padding:12px;background:var(--green-soft);border-radius:10px;">
        Scheduled ${summary.scheduled.length} medicine(s).
        ${summary.prn.length ? `${summary.prn.length} marked as-needed (no fixed schedule).` : ''}
        ${summary.blocked_needs_confirmation.length ? `<br><strong>${summary.blocked_needs_confirmation.length} still need confirmation before they can be scheduled.</strong>` : ''}
      </div>`;
    if (summary.scheduled.length) checkPrescriptionCombinations(result.prescription_id);
  });
}

// ---------------------------------------------------------------- dashboard (Feature 2)

/** Groups upcoming doses by calendar day (in the browser's local time, same
 * as every other date shown on this dashboard) so they render as day-by-day
 * agenda blocks instead of one flat list — a lightweight calendar look
 * without pulling in a full calendar-grid dependency. */
function groupDosesByDay(doses) {
  const groups = [];
  const byKey = new Map();
  doses.forEach(d => {
    const dt = new Date(d.scheduled_at);
    const key = dt.toDateString();
    if (!byKey.has(key)) {
      const group = { date: dt, doses: [] };
      byKey.set(key, group);
      groups.push(group);
    }
    byKey.get(key).doses.push(d);
  });
  return groups;
}

function dayLabel(date) {
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  if (date.toDateString() === today.toDateString()) return 'Today';
  if (date.toDateString() === tomorrow.toDateString()) return 'Tomorrow';
  return date.toLocaleDateString([], { weekday: 'short' });
}

// The dashboard shows TODAY only (the patient's own day). The days after are generated in the
// background and simply become "today" when their day comes - nobody needs to scroll a 30-day list.
function renderTodaySchedule(dash) {
  const doses = dash.today_doses || [];
  const next = (dash.upcoming_doses || [])[0];
  if (!doses.length) {
    return `<div class="empty">No medicines scheduled for today.${next
      ? ` Next: <strong>${escHtml(next.medicine_name)}</strong>, ${escHtml(shortTime(next.scheduled_at))}.` : ''}</div>`;
  }
  const hhmm = (iso) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const now = Date.now();
  const rows = doses.map((d) => {
    const open = d.state === 'pending' || d.state === 'snoozed';
    const minsAway = (new Date(d.scheduled_at) - now) / 60000;
    let status = '';
    let actions = '';
    if (d.state === 'taken') status = `<span class="today-pill ok">✓ Taken${d.acted_at ? ' ' + escHtml(hhmm(d.acted_at)) : ''}</span>`;
    else if (d.state === 'skipped') status = '<span class="today-pill muted">Skipped</span>';
    else if (d.state === 'missed') {
      status = '<span class="today-pill care">Missed</span>';
      actions = `<button class="ghost small" data-missed-help="${d.id}">What should I do?</button>`;
    } else if (minsAway < 0) status = '<span class="today-pill action">Due now</span>';
    else if (minsAway <= 60) status = `<span class="today-pill info">In ${Math.max(1, Math.round(minsAway))} min</span>`;
    if (open && isTakeableNow(d.scheduled_at)) {
      actions = `<button class="ghost small" data-act="take">Take</button>
                 <button class="ghost small" data-act="snooze">Snooze</button>
                 <button class="ghost small" data-act="skip">Skip</button>`;
    } else if (open && minsAway > 0) {
      status = `<span class="today-pill muted">Later today</span>`;
      actions = `<button class="ghost small is-early" data-act="take">Take</button>`;   // tapping explains why not yet
    }
    const slotName = { morning: 'Morning', afternoon: 'Afternoon', evening: 'Evening', night: 'Night', bedtime: 'Bedtime' }[d.slot] || '';
    const foodText = d.food === 'before' ? 'take before food' : d.food === 'after' ? 'take after food' : '';
    const hintText = [slotName, foodText].filter(Boolean).join(' · ');
    const hint = open && hintText ? `<div class="reg-meta">${escHtml(hintText)}</div>` : '';
    return `
      <div class="dose-calendar-slot ${open ? '' : 'is-done'}" data-dose-id="${d.id}" data-med-name="${escHtml(d.medicine_name)}" data-scheduled-at="${escHtml(d.scheduled_at)}">
        <div class="dose-calendar-time">${escHtml(hhmm(d.scheduled_at))}</div>
        <div class="dose-calendar-dot"></div>
        <div class="dose-calendar-info">
          <div class="dose-calendar-med">${escHtml(d.medicine_name)} ${status}</div>
          ${hint}
          ${actions ? `<div class="dose-calendar-actions">${actions}</div>` : ''}
        </div>
      </div>`;
  }).join('');
  const anyMissed = doses.some((d) => d.state === 'missed');
  const done = dash.left_today === 0
    ? `<div class="today-done${anyMissed ? ' has-missed' : ''}">${anyMissed ? 'Nothing more scheduled today.' : 'All done for today ✓'}${next ? ` Next: <strong>${escHtml(next.medicine_name)}</strong>, ${escHtml(shortTime(next.scheduled_at))}.` : ''}</div>` : '';
  return `<div class="dose-calendar"><div class="dose-calendar-day"><div class="dose-calendar-day-body">${rows}</div></div></div>${done}`;
}

function renderDoseCalendar(doses) {
  if (!doses.length) return '<div class="empty">Nothing scheduled yet — decode and confirm a prescription first.</div>';
  const groups = groupDosesByDay(doses);
  return `<div class="dose-calendar">${groups.map(g => `
    <div class="dose-calendar-day">
      <div class="dose-calendar-day-head">
        <span>${dayLabel(g.date)}</span>
        <span class="dose-calendar-day-date">${g.date.toLocaleDateString([], { month: 'short', day: 'numeric' })}</span>
      </div>
      <div class="dose-calendar-day-body">
        ${g.doses.map(d => `
          <div class="dose-calendar-slot" data-dose-id="${d.id}" data-med-name="${d.medicine_name}" data-scheduled-at="${d.scheduled_at}">
            <div class="dose-calendar-time">${new Date(d.scheduled_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
            <div class="dose-calendar-dot"></div>
            <div class="dose-calendar-info">
              <div class="dose-calendar-med">${d.medicine_name}</div>
              <div class="dose-calendar-actions">
                <button class="ghost small" data-act="take">Take</button>
                <button class="ghost small" data-act="snooze">Snooze</button>
                <button class="ghost small" data-act="skip">Skip</button>
              </div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>
  `).join('')}</div>`;
}

/** A brief on-screen confirmation popup — a big check icon plus a short
 * headline/detail/label, so an action is visibly acknowledged beyond just
 * the page quietly re-rendering. Closes on click or after 2.5s. */
function showQuickPopup(title, detail, label) {
  document.querySelectorAll('.taken-popup-overlay').forEach(el => el.remove());
  const overlay = el(`
    <div class="taken-popup-overlay">
      <div class="taken-popup">
        <span class="taken-popup-icon">${ICONS.checkCircle}</span>
        <div class="taken-popup-med">${title}</div>
        ${detail ? `<div class="taken-popup-when">${detail}</div>` : ''}
        <div class="taken-popup-label">${label}</div>
      </div>
    </div>
  `);
  document.body.appendChild(overlay);
  const dismiss = () => overlay.remove();
  overlay.addEventListener('click', dismiss);
  setTimeout(dismiss, 2500);
}

function showTakenPopup(medName, scheduledAt) {
  const when = new Date(scheduledAt);
  showQuickPopup(medName,
    `${when.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })} · ${when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`,
    'Marked as taken');
}


// ---------------------------------------------------------------- small shared helpers

/** A plain "OK" popup for things the patient must notice (a refused tap, a failed download). */
function showNotice(title, message) {
  const overlay = showSafetyModal(`
    <h3>${escHtml(title)}</h3>
    <div class="sm-headline" style="font-weight:500;">${escHtml(message)}</div>
    <div class="sm-actions"><button class="primary small" id="noticeOk">OK</button></div>`);
  overlay.querySelector('#noticeOk').addEventListener('click', () => overlay.remove());
}

// Same window the server enforces (scheduler.TAKE_EARLY_WINDOW / TAKE_LATE_WINDOW). The server is the
// real guard; this only decides which buttons to show.
const TAKE_EARLY_MS = 2 * 3600000;
const TAKE_LATE_MS = 12 * 3600000;
function isTakeableNow(iso) {
  const diff = new Date(iso) - Date.now();
  return diff <= TAKE_EARLY_MS && diff >= -TAKE_LATE_MS;
}

/** Yes/No popup. Resolves true only if the patient chooses the confirm button. */
function confirmDialog(title, message, yesLabel, noLabel, safeIsNo = false) {
  // safeIsNo: the "No / wait" answer is the safe one, so it gets the prominent button.
  return new Promise((resolve) => {
    const overlay = showSafetyModal(`
      <h3>${escHtml(title)}</h3>
      <div class="sm-headline" style="font-weight:500;">${escHtml(message)}</div>
      <div class="sm-actions">
        <button class="${safeIsNo ? 'ghost' : 'primary'} small" id="cdYes">${escHtml(yesLabel)}</button>
        <button class="${safeIsNo ? 'primary' : 'ghost'} small" id="cdNo">${escHtml(noLabel)}</button>
      </div>`);
    const done = (v) => { overlay.remove(); resolve(v); };
    overlay.querySelector('#cdYes').addEventListener('click', () => done(true));
    overlay.querySelector('#cdNo').addEventListener('click', () => done(false));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) done(false); });
  });
}

function earlyLabel(mins) {
  const m = Math.round(mins);
  return m >= 60 ? `${Math.floor(m / 60)} h${m % 60 ? ' ' + (m % 60) + ' min' : ''}` : `${m} min`;
}

/**
 * Mark a dose taken, with layers of protection against taking the wrong dose:
 *   - more than ~30 min early (but inside the 2 h window): ask first;
 *   - a dose that is not due yet, or too soon after the last dose of the same medicine: the server refuses
 *     and we show why, as a popup;
 *   - two medicines that must be kept apart (FDA label rule, or an AI estimate): we say when it is okay, and the
 *     patient can answer "I already took it" - which is recorded as an override.
 * The button is always visible so tapping it always explains itself instead of doing nothing.
 */
async function takeDoseWithGuard(doseId, medName, scheduledAt, override = false) {
  const minsEarly = (new Date(scheduledAt) - Date.now()) / 60000;
  if (!override && minsEarly > 30 && minsEarly <= TAKE_EARLY_MS / 60000) {
    const at = new Date(scheduledAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const yes = await confirmDialog('Taking this early?',
      `${medName} is scheduled for ${at}, which is ${earlyLabel(minsEarly)} from now. Did you take it already?`,
      'Yes, I took it', 'No, not yet');
    if (!yes) return false;
  }
  try {
    await api('POST', `/doses/${doseId}/take`, override ? { override: true } : undefined);
    showTakenPopup(medName, scheduledAt);
    return true;
  } catch (e) {
    const info = e.info || {};
    if (e.status === 409 && info.can_override) {
      // Cross-medicine spacing or an AI-estimated gap: explain, and let the patient say they already took it.
      const tookIt = await confirmDialog(
        info.code === 'spacing' ? 'These two medicines need a gap' : 'A little too soon after your last dose',
        e.message, 'I already took it', 'OK, I’ll wait', true);
      return tookIt ? takeDoseWithGuard(doseId, medName, scheduledAt, true) : false;
    }
    showNotice(info.code === 'too_soon' ? 'Too soon after your last dose' : 'Not yet — don’t take this now',
      e.message || 'This dose is not due yet.');
    return false;
  }
}

/** Download an authenticated file. A plain <a href> sends no login token, which is why
 * "Export calendar" showed an error page. */
async function downloadAuthed(path, filename) {
  const auth = getAuth();
  const res = await fetch(path, { headers: auth && auth.token ? { Authorization: `Bearer ${auth.token}` } : {} });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || `Download failed (${res.status})`);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 15000);
}

/** WhatsApp is a popup, not a permanent dashboard card. */
async function showWhatsAppPopup() {
  let info = null;
  try { info = await api('GET', '/whatsapp/available'); } catch (e) { info = null; }
  const ok = info && info.available && info.link;
  const overlay = showSafetyModal(`
    <h3>Reminders on WhatsApp</h3>
    <div class="sm-sub">Get dose reminders, mark doses taken, or send a prescription photo from WhatsApp.</div>
    ${ok ? '<div class="reg-meta">Tap send once on the pre-filled “join” message to connect.</div>'
         : '<div class="sm-never">WhatsApp reminders are not set up on this server yet.</div>'}
    <div class="sm-actions">
      ${ok ? '<button class="primary small" id="waOpen">Open WhatsApp</button>' : ''}
      <button class="ghost small" id="waClose">Close</button>
    </div>`);
  overlay.querySelector('#waClose').addEventListener('click', () => overlay.remove());
  const open = overlay.querySelector('#waOpen');
  if (open) open.addEventListener('click', () => { window.open(info.link, '_blank', 'noopener'); overlay.remove(); });
}

/** One quiet line on the dashboard if there is a medicine-timing issue; the detail lives in Safety center. */
async function loadTimingChip(mount) {
  if (!mount) return;
  try {
    const data = await api('GET', `/patients/${state.patientId}/schedule-conflicts`);
    const n = data.conflicts.length;
    if (!n) return;
    mount.innerHTML = `<button class="chip-link" id="timingChipBtn">⏱ ${n} medicine timing issue${n === 1 ? '' : 's'} to check — review</button>`;
    mount.querySelector('#timingChipBtn').addEventListener('click', () => document.querySelector('[data-tab=safety]').click());
  } catch (e) { /* optional hint */ }
}

function treatmentProgressHtml(dash) {
  const rows = ((dash && dash.per_medicine) || []).filter(p => p.progress && p.progress.current_day !== null).map(p => `
    <div style="margin-bottom:10px;">
      <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px;font-size:13px;">
        <span>${escHtml(p.name)}</span>
        <span style="color:var(--ink-soft);">${p.progress.ongoing ? `Day ${p.progress.current_day}` : `Day ${p.progress.current_day} / ${p.progress.total_days}`}</span>
      </div>
      <div class="day-progress-track"><div class="day-progress-fill" style="width:${p.progress.ongoing ? 100 : Math.round(p.progress.current_day / p.progress.total_days * 100)}%"></div></div>
    </div>`).join('');
  return rows ? `<div class="report-section"><div class="card-head">${iconBadge('teal', 'chartBar')}<h3>Treatment progress</h3></div>${rows}</div>` : '';
}

// ---------------------------------------------------------------- timing safety UI
// Everything below renders answers computed by the server's rule engine
// (safety_engine.py, from quoted FDA label sentences). The browser never
// works out an interval or a catch-up time itself.

function escHtml(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function fmtLocalTime(iso) {
  return new Date(iso).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
}

// ---------------------------------------------------------------- alerts (one calm, readable design)
// Tones - red is deliberately NOT used here; it is kept for real emergencies (triage):
//   action  amber  = something to do or ask about
//   care    soft   = use with care / good to know
//   info    blue   = for your information
//   clear   green  = all fine
const ALERT_ICON = { action: 'clock', care: 'warning', info: 'shield', clear: 'checkCircle' };

function alertCardHtml({ tone = 'care', icon, title, problem, action, visual = '', details = '', buttons = '' }) {
  return `
    <div class="alert-card tone-${tone}">
      <span class="alert-icon">${ICONS[icon || ALERT_ICON[tone]] || ''}</span>
      <div class="alert-body">
        <div class="alert-title">${escHtml(title)}</div>
        ${problem ? `<div class="alert-label">What's the problem</div><div class="alert-text">${escHtml(problem)}</div>` : ''}
        ${visual}
        ${action ? `<div class="alert-do"><div class="alert-label" style="margin-top:0;">What to do</div><div class="alert-text">${action}</div>${buttons}</div>` : ''}
        ${details}
      </div>
    </div>`;
}

function capName(v) {
  return String(v == null ? '' : v).replace(/(^|[\s+/-])([a-z])/g, (m, pre, ch) => pre + ch.toUpperCase());
}

function alertLegendHtml() {
  return `<div class="alert-legend" aria-label="What the colours mean">
    <span><i style="background:var(--amber)"></i>Needs your action</span>
    <span><i style="background:#C9A227"></i>Use with care</span>
    <span><i style="background:var(--blue)"></i>Good to know</span>
    <span><i style="background:var(--teal)"></i>All fine</span></div>`;
}

function shortTime(iso) {
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  const t = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return sameDay ? t : `${d.toLocaleDateString([], { weekday: 'short' })} ${t}`;
}

function hoursLabel(h) {
  if (h == null) return '';
  if (h < 1) return `${Math.round(h * 60)} min`;
  return Number.isInteger(h) ? `${h} h` : `${h.toFixed(1)} h`;
}

/** "Now 0 min apart - needs 4 h": a bar that fills as the gap approaches what the label asks for. */
function gapVisualHtml(c) {
  const [a, b] = c.medicines;
  const [ta, tb] = c.dose_times || [];
  const pct = Math.max(4, Math.min(100, Math.round((c.actual_hours / c.required_hours) * 100)));
  return `
    <div class="gap-vis">
      <div class="gap-ends">
        <div><div class="gap-time">${escHtml(shortTime(ta))}</div><div class="gap-name">${escHtml(a)}</div></div>
        <div style="text-align:right;"><div class="gap-time">${escHtml(shortTime(tb))}</div><div class="gap-name">${escHtml(b)}</div></div>
      </div>
      <div class="gap-bar"><div class="gap-fill" style="width:${pct}%"></div></div>
      <div class="gap-caption"><span>Now: <strong>${escHtml(hoursLabel(c.actual_hours) || 'same time')}</strong> apart</span>
        <span>Needed: <strong>${escHtml(hoursLabel(c.required_hours))}</strong></span></div>
    </div>`;
}

function whyDetailsHtml(c) {
  if (!c.source) return '';
  return `<details class="alert-why"><summary>Why am I seeing this?</summary>
    <div class="sm-quote">“${escHtml(c.source.quote)}”<br>
      <a href="${escHtml(c.source.url)}" target="_blank" rel="noopener noreferrer">${escHtml(c.source.title)}</a>
      · label effective ${escHtml(c.source.effective)}</div>
    <div class="sm-unreviewed">Taken from the US FDA label. Not reviewed by a clinician — check with your pharmacist or doctor.</div>
  </details>`;
}

function timingAlertHtml(c) {
  const names = c.medicines.map(escHtml).join(' and ');
  if (c.kind === 'timing') {
    const proposal = c.proposal;
    return alertCardHtml({
      tone: 'action',
      title: `Don't take ${c.medicines[0]} and ${c.medicines[1]} too close together`,
      problem: c.message.split(' Keep at least')[0],
      visual: gapVisualHtml(c),
      action: proposal
        ? `Take <strong>${escHtml(proposal.medicine)}</strong> at <strong>${escHtml(shortTime(proposal.to))}</strong> instead of ${escHtml(shortTime(proposal.from))}. Nothing changes unless you tap the button.`
        : 'SmartPoli can’t safely move these by itself. Ask your pharmacist or doctor how far apart to take them. Until then, don’t change your doses on your own.',
      buttons: proposal ? `<button class="primary small" data-reschedule="${proposal.dose_id}" data-to="${escHtml(proposal.to)}" style="margin-top:8px;">Move ${escHtml(proposal.medicine)} to ${escHtml(shortTime(proposal.to))}</button>` : '',
      details: whyDetailsHtml(c),
    });
  }
  if (c.kind === 'interval_unspecified') {
    return alertCardHtml({
      tone: 'action',
      title: `${c.medicines[0]} and ${c.medicines[1]} should be taken apart`,
      problem: c.message,
      action: 'Ask your pharmacist or doctor: “How many hours apart should I take these two?” Don’t guess.',
      details: whyDetailsHtml(c),
    });
  }
  if (c.kind === 'duplicate_dose') {
    return alertCardHtml({
      tone: 'care', icon: 'alertCircle',
      title: `${c.medicines[0]} appears twice within an hour`,
      problem: 'Two doses of the same medicine are scheduled very close together.',
      action: 'Check your prescription to see if this is meant. If you’re not sure, ask your pharmacist.',
    });
  }
  return alertCardHtml({
    tone: 'care', icon: 'alertCircle',
    title: `${names} contain the same ingredient`,
    problem: 'Taking both could mean taking more of that ingredient than intended.',
    action: 'Ask your pharmacist or doctor whether you should take both.',
  });
}

function showSafetyModal(innerHtml) {
  document.querySelectorAll('.safety-modal-overlay').forEach((n) => n.remove());
  const overlay = el(`<div class="safety-modal-overlay" role="dialog" aria-modal="true"><div class="safety-modal">${innerHtml}</div></div>`);
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  return overlay;
}

const MISSED_SEEN_KEY = 'smartpoli_missed_seen';
function missedSeen() {
  try { return new Set(JSON.parse(localStorage.getItem(MISSED_SEEN_KEY) || '[]')); } catch (e) { return new Set(); }
}
function markMissedSeen(id) {
  try { const s = missedSeen(); s.add(id); localStorage.setItem(MISSED_SEEN_KEY, JSON.stringify([...s].slice(-200))); } catch (e) { /* ignore */ }
}

/** Calm "what now?" popup for a missed dose: facts, the label's own words, and
 * only the actions the rule engine supports. Never suggests doubling up. */
async function showMissedGuidance(doseId) {
  markMissedSeen(doseId);
  let g = null;
  try { g = await api('GET', `/doses/${doseId}/missed-guidance`); } catch (e) { g = null; }
  const name = g ? escHtml(g.medicine) : 'this medicine';
  const headline = g ? escHtml(g.headline)
    : 'SmartPoli could not load guidance just now. Do not take a double dose, and ask your pharmacist or doctor what to do.';
  const quote = g && g.label_quote && g.source ? `
    <div class="sm-quote">“${escHtml(g.label_quote)}”<br>
      <a href="${escHtml(g.source.url)}" target="_blank" rel="noopener noreferrer">${escHtml(g.source.title)}</a>
      · label effective ${escHtml(g.source.effective)}
      ${g.applies_to_note ? `<br>${escHtml(g.applies_to_note)}` : ''}</div>` : '';
  const notes = g && g.spacing_notes && g.spacing_notes.length
    ? `<ul style="margin:6px 0 0 18px;font-size:13px;">${g.spacing_notes.map((n) => `<li>${escHtml(n)}</li>`).join('')}</ul>` : '';
  const overlay = showSafetyModal(`
    <h3>Missed dose: ${name}</h3>
    ${g ? `<div class="sm-sub">Was due ${escHtml(fmtLocalTime(g.scheduled_at))}${g.next_dose_at ? ` · next dose ${escHtml(fmtLocalTime(g.next_dose_at))}` : ''}</div>` : ''}
    <div class="sm-never">Never take a double dose to catch up.</div>
    <div class="sm-headline">${headline}</div>
    ${notes}${quote}
    ${g ? `<div class="sm-unreviewed">From the US FDA label. Not reviewed by a clinician — confirm with your pharmacist or doctor.</div>` : ''}
    <div class="sm-actions">
      <button class="primary small" id="smTook">I took it</button>
      <button class="ghost small" id="smSkip">Skip it</button>
      <button class="ghost small" id="smClose">Close</button>
    </div>`);
  const done = () => { overlay.remove(); dashboardCache = null; renderActiveTab(); };
  overlay.querySelector('#smClose').addEventListener('click', () => overlay.remove());
  overlay.querySelector('#smTook').addEventListener('click', async () => { await api('POST', `/doses/${doseId}/take`); done(); });
  overlay.querySelector('#smSkip').addEventListener('click', async () => {
    await api('POST', `/doses/${doseId}/skip`, { reason: 'Skipped after missed-dose guidance' }); done();
  });
}

function conflictsHtml(data) {
  if (!data) return '<div class="empty">Could not check the schedule just now.</div>';
  const note = `<div class="reg-meta" style="margin-top:6px;">${escHtml(data.not_verified_note)}</div>`;
  if (!data.conflicts.length) {
    return alertCardHtml({
      tone: 'clear',
      title: 'No timing problems found',
      problem: '',
      action: '',
    }) + note;
  }
  const n = data.conflicts.length;
  const fixable = data.conflicts.filter((c) => c.proposal).length;
  const applyAll = fixable > 1
    ? `<button class="primary small" data-apply-all="1" style="margin:0 0 10px 8px;">Apply all ${fixable} suggestions</button>` : '';
  return `<div class="alert-summary">${n} thing${n === 1 ? '' : 's'} to check</div>${applyAll}${alertLegendHtml()}`
    + data.conflicts.map(timingAlertHtml).join('') + note;
}

/** Apply every suggestion, one at a time: after each move the server re-checks, because moving one
 * dose can change what is suggested for the next. Stops when nothing movable is left. */
async function applyAllSuggestions() {
  for (let i = 0; i < 20; i++) {
    const data = await api('GET', `/patients/${state.patientId}/schedule-conflicts`);
    const next = data.conflicts.find((c) => c.proposal);
    if (!next) return;
    await api('POST', `/doses/${next.proposal.dose_id}/reschedule`, { to: next.proposal.to });
  }
}

function wireConflictActions(root) {
  root.querySelectorAll('[data-apply-all]').forEach((btn) => btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = 'Applying…';
    try { await applyAllSuggestions(); } catch (e) { showNotice('Could not apply', e.message); }
    dashboardCache = null; renderActiveTab();
  }));
  root.querySelectorAll('[data-reschedule]').forEach((btn) => btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      await api('POST', `/doses/${btn.dataset.reschedule}/reschedule`, { to: btn.dataset.to });
      dashboardCache = null; renderActiveTab();
    } catch (e) { btn.disabled = false; btn.textContent = (e && e.message) || 'Could not move it'; }
  }));
}

async function loadConflictsInto(mount) {
  if (!mount) return;
  let data = null;
  try { data = await api('GET', `/patients/${state.patientId}/schedule-conflicts`); } catch (e) { data = null; }
  mount.innerHTML = conflictsHtml(data);
  wireConflictActions(mount);
}

const REG_STATUS_LABEL = {
  verified_record_found: 'Verified record found',
  potential_match: 'Potential match — exact product not confirmed',
  regulatory_alert_found: 'Regulatory alert found',
  restricted_status_identified: 'Restricted status identified',
  no_matching_record: 'No matching record found',
  source_unavailable: 'Source unavailable',
  verification_pending: 'Verification pending',
};
const REG_CHECK_LABEL = {
  cdsco_prohibited_fdc: 'CDSCO prohibited / restricted combinations',
  india_approval: 'India approval (Drugs@CDSCO)',
  us_fda_approval: 'US FDA approval records',
  us_fda_recalls: 'US FDA recalls',
};

// One calm verdict per medicine; the full official wording stays one tap away under "More".
function regVerdict(m) {
  const checks = Object.values(m.checks);
  const flagged = checks.filter((c) => c.status === 'restricted_status_identified' || c.status === 'regulatory_alert_found');
  if (flagged.length) return { tone: 'warn', label: 'Needs a look', note: flagged[0].message };
  if (checks.every((c) => c.status === 'source_unavailable' || c.status === 'verification_pending')) {
    return { tone: 'muted', label: "Couldn't check", note: 'Official lists were not reachable. Try again later.' };
  }
  return { tone: 'ok', label: 'No problem found', note: '' };
}

function regulatoryHtml(data) {
  if (!data) return '<div class="empty">Could not reach the lookup just now.</div>';
  if (!data.medicines.length) return '<div class="empty">No confirmed medicines to check.</div>';
  const verdicts = data.medicines.map(regVerdict);
  const flaggedCount = verdicts.filter((v) => v.tone === 'warn').length;
  const summary = flaggedCount
    ? `${flaggedCount} medicine${flaggedCount > 1 ? 's need' : ' needs'} a look — ask your pharmacist.`
    : 'Nothing worrying found in the official lists.';
  const rows = data.medicines.map((m, i) => {
    const v = verdicts[i];
    const more = Object.values(m.checks).map((c) => `
      <div class="reg-check">
        <div><strong>${escHtml(REG_CHECK_LABEL[c.check] || c.check)}</strong> — ${escHtml(REG_STATUS_LABEL[c.status] || c.status)}</div>
        <div>${escHtml(c.message)}</div>
        ${(c.matches || []).map((x) => `<div class="reg-meta">${escHtml(x.combination)} — ${escHtml(x.notification)}</div>`).join('')}
        <div class="reg-meta">${c.source ? `Source: <a href="${escHtml(c.source.url)}" target="_blank" rel="noopener noreferrer">${escHtml(c.source.title)}</a>` : ''}${c.from_stale_cache ? ' · older saved result' : ''}</div>
      </div>`).join('');
    return `
      <div class="reg-med">
        <div class="reg-row"><strong>${escHtml(m.medicine)}</strong><span class="reg-verdict ${v.tone}">${v.label}</span></div>
        ${v.note ? `<div class="reg-meta">${escHtml(v.note)}</div>` : ''}
        <details class="reg-more"><summary>More</summary>${more}</details>
      </div>`;
  }).join('');
  return `<div class="reg-summary ${flaggedCount ? 'warn' : 'ok'}">${summary}</div>${rows}
    <div class="reg-meta" style="margin-top:10px;">Not medical advice. Missing from a list does not mean unsafe.</div>`;
}

async function renderDashboard() {
  const view = document.getElementById('view-dashboard');
  view.innerHTML = `<div class="empty">Loading...</div>`;
  const dash = await getDashboard();

  const next = dash.upcoming_doses[0];
  const heroHtml = next ? (() => {
    const diffMs = new Date(next.scheduled_at) - new Date();
    const overdue = diffMs < 0;
    const abs = Math.abs(diffMs);
    const hrs = Math.floor(abs / 3600000);
    const mins = Math.floor((abs % 3600000) / 60000);
    const label = hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`;
    const takeable = isTakeableNow(next.scheduled_at);
    const takeFrom = new Date(new Date(next.scheduled_at) - TAKE_EARLY_MS).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return `
      <div class="card hero-next-dose">
        <div style="display:flex;align-items:center;gap:14px;">
          ${iconBadge('teal', 'pill')}
          <div>
            <div style="font-size:12px;color:var(--ink-soft);">Next dose</div>
            <div class="med-name">${next.medicine_name}</div>
            <div style="font-size:13px;color:var(--ink-soft);">${new Date(next.scheduled_at).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}</div>
          </div>
        </div>
        <div style="text-align:right;">
          <div class="countdown ${overdue ? 'overdue' : ''}">${overdue ? 'Overdue by ' : 'in '}${label}</div>
          <button class="${takeable ? 'primary' : 'ghost is-early'} small" data-hero-take="${next.id}" style="margin-top:8px;">Mark taken</button>
          ${takeable ? '' : `<div class="reg-meta" style="margin-top:6px;">Not due yet · can be taken from ${takeFrom}</div>`}
        </div>
      </div>
    `;
  })() : '';

  const prnHtml = dash.prn_medicines.map(m => `
    <div class="dose-row">
      <div><strong>${m.name || m.raw_text}</strong> <span style="color:var(--ink-soft);font-size:12px;">as-needed</span></div>
      <button class="ghost small" data-prn-id="${m.id}">Log a dose</button>
    </div>
  `).join('');

  const todayHtml = renderTodaySchedule(dash);

  // Missed doses are kept apart from the upcoming ones and only the last
  // 24h are surfaced (recent_doses spans +-36h): an old miss is not an
  // action item. No catch-up time is suggested — see the note below.
  const dayAgo = Date.now() - 24 * 3600000;
  const recentMissed = (dash.recent_doses || []).filter(d => d.state === 'missed' && new Date(d.scheduled_at) >= dayAgo);
  // Today's own misses already show in "Today's medicines"; this card is only for misses from earlier (still within 24 h).
  const earlierMissed = recentMissed.filter(d => String(d.scheduled_at).slice(0, 10) !== dash.today);
  const missedHtml = earlierMissed.length ? `
    <div class="card missed-card">
      <div class="card-head">${iconBadge('amber', 'alertCircle')}<h3>Missed earlier</h3></div>
      ${earlierMissed.map(d => `
        <div class="missed-row">
          <span><strong>${escHtml(d.medicine_name)}</strong> · ${new Date(d.scheduled_at).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}</span>
          <button class="ghost small missed-help" data-missed-help="${d.id}">What should I do?</button>
        </div>`).join('')}
      <div class="missed-note">Don't take a double dose to catch up. If you're unsure whether to take a missed dose now, check the medicine's leaflet or ask your pharmacist or doctor. Your next dose stays as scheduled above.</div>
    </div>` : '';

  view.innerHTML = `
    <div class="dash-head">
      <h2>${t('dashboardHeading')}</h2>
      <button class="ghost small" id="dashWhatsAppBtn"><span class="icon">${ICONS.messageCircle}</span>WhatsApp</button>
    </div>
    <div id="dashPushPrompt"></div>
    <div id="dashTimingChip"></div>
    ${heroHtml}
    <div class="card" id="todayCard">
      <div class="card-head">${iconBadge('teal', 'calendar')}<h3>Today's medicines</h3>
        <span class="card-art" aria-hidden="true">
          <svg viewBox="0 0 84 48" width="84" height="48" fill="none" stroke-linecap="round" stroke-linejoin="round">
            <path class="art-heart" d="M42 40 C18 24 22 8 33 8 C38 8 41 11 42 14 C43 11 46 8 51 8 C62 8 66 24 42 40Z" fill="#E8F5F0" stroke="#0F8A70" stroke-width="2"/>
            <path class="art-ecg" d="M2 26 H26 L31 16 L38 36 L44 10 L50 30 L54 26 H82" stroke="#0F8A70" stroke-width="2.4"/>
          </svg>
        </span>
      </div>
      ${todayHtml}
    </div>
    ${missedHtml}
    ${dash.prn_medicines.length ? `
    <div class="card">
      <div class="card-head">${iconBadge('teal', 'pill')}<h3>As-needed</h3></div>
      ${prnHtml}
    </div>` : ''}
  `;

  document.getElementById('dashWhatsAppBtn').addEventListener('click', showWhatsAppPopup);
  loadTimingChip(document.getElementById('dashTimingChip'));
  renderPushPrompt(document.getElementById('dashPushPrompt'), state.patientId);
  view.querySelectorAll('[data-missed-help]').forEach((b) => b.addEventListener('click', () => showMissedGuidance(Number(b.dataset.missedHelp))));
  // A dose that was just missed gets the calm guidance popup once.
  const unseen = recentMissed.find((d) => !missedSeen().has(d.id));
  if (unseen) showMissedGuidance(unseen.id);

  const heroTakeBtn = view.querySelector('[data-hero-take]');
  if (heroTakeBtn) {
    heroTakeBtn.addEventListener('click', async () => {
      await takeDoseWithGuard(heroTakeBtn.dataset.heroTake, next.medicine_name, next.scheduled_at);
      renderDashboard();
      renderGlance();
    });
  }

  view.querySelectorAll('[data-prn-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      await api('POST', `/medicines/${btn.dataset.prnId}/log-prn`);
      renderDashboard();
      renderGlance();
    });
  });

  view.querySelectorAll('.dose-calendar-slot[data-dose-id]').forEach(row => {
    const doseId = row.dataset.doseId;
    row.querySelectorAll('[data-act]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const act = btn.dataset.act;
        try {
          if (act === 'take') {
            await takeDoseWithGuard(doseId, row.dataset.medName, row.dataset.scheduledAt);
          } else if (act === 'snooze') {
            await api('POST', `/doses/${doseId}/snooze`);
          } else if (act === 'skip') {
            const reason = prompt('Reason for skipping this dose?');
            if (!reason) return;
            await api('POST', `/doses/${doseId}/skip`, { reason });
          }
        } catch (e) {
          showNotice('Not possible right now', e.message);
        }
        renderDashboard();
        renderGlance();
      });
    });
  });
}

// ---------------------------------------------------------------- triage (Feature 3)

async function renderTriage() {
  state.symptoms = await api('GET', `/triage/symptoms?lang=${state.lang}`);
  if (state.llmAvailable === undefined) {
    state.llmAvailable = (await api('GET', '/triage/llm-available')).available;
  }
  resetTriage();
  renderTriagePicker();
}

function resetTriage() {
  state.selectedSymptoms = [];
  state.triageQueue = [];
  state.triageAnswers = {};
  state.suggestedAnswers = {};   // from free-text interpretation — pre-highlighted, never auto-submitted
  state.triageSeverity = 'LOW';
  state.triageDone = false;
}

function renderTriagePicker() {
  const view = document.getElementById('view-triage');
  view.innerHTML = `
    <h2>${t('triageHeading')}</h2>
    <p style="color:var(--ink-soft)">Rule-based only — this grades urgency, it never gives a diagnosis.</p>

    <div id="triageInputCards">
      ${state.llmAvailable ? `
      <div class="card">
        <div class="card-head">${iconBadge('teal', 'fileText')}<h3>Or describe it in your own words</h3></div>
        <label for="freeTextInput" style="font-weight:400;color:var(--ink-soft);">Tell us what you're experiencing, e.g. "I have chest pain and it's hard to breathe"</label>
        <input type="text" id="freeTextInput" placeholder="e.g. I have chest pain and it's hard to breathe">
        <div style="margin-top:10px;">
          <button class="ghost" id="interpretBtn"><span class="icon">${ICONS.chevronRight}</span>Interpret</button>
        </div>
        <div id="interpretStatus" style="margin-top:8px;font-size:13px;color:var(--ink-soft);"></div>
        <div style="font-size:11px;color:var(--ink-soft);margin-top:6px;">
          This only pre-selects symptoms and highlights suggested answers below — you still review
          and confirm every answer, and the severity grade always comes from the fixed rules, never from this.
        </div>
      </div>` : ''}

      <div class="card">
        <div class="card-head">${iconBadge('teal', 'stethoscope')}<h3>What are you experiencing? (select one or more)</h3></div>
        <div class="symptom-grid" id="symptomGrid">
          ${state.symptoms.map(s => `<button data-sid="${s.id}">${s.label}</button>`).join('')}
        </div>
        <button class="primary" id="startTriageBtn" style="margin-top:16px;" disabled>Continue</button>
      </div>
    </div>
    <div id="triageFlow"></div>
  `;

  const startBtn = document.getElementById('startTriageBtn');
  const symptomBtn = sid => view.querySelector(`#symptomGrid button[data-sid="${sid}"]`);

  view.querySelectorAll('#symptomGrid button').forEach(btn => {
    btn.addEventListener('click', () => {
      const sid = btn.dataset.sid;
      btn.classList.toggle('selected');
      if (state.selectedSymptoms.includes(sid)) {
        state.selectedSymptoms = state.selectedSymptoms.filter(x => x !== sid);
      } else {
        state.selectedSymptoms.push(sid);
      }
      startBtn.disabled = state.selectedSymptoms.length === 0;
    });
  });

  const interpretBtn = document.getElementById('interpretBtn');
  if (interpretBtn) {
    interpretBtn.addEventListener('click', async () => {
      const text = document.getElementById('freeTextInput').value.trim();
      const status = document.getElementById('interpretStatus');
      if (!text) return;
      status.textContent = 'Interpreting...';
      try {
        const suggestion = await api('POST', '/triage/interpret-free-text', { text });
        state.suggestedAnswers = suggestion.answers || {};
        for (const sid of suggestion.symptom_ids || []) {
          if (!state.selectedSymptoms.includes(sid)) {
            state.selectedSymptoms.push(sid);
            symptomBtn(sid)?.classList.add('selected');
          }
        }
        startBtn.disabled = state.selectedSymptoms.length === 0;
        status.textContent = suggestion.symptom_ids?.length
          ? `Suggested: ${suggestion.symptom_ids.join(', ')} — review the highlighted chips, then continue.`
          : "Couldn't map that to a listed symptom — pick from the list below instead.";
      } catch (e) {
        status.textContent = 'Free-text interpretation is unavailable right now — use the symptom picker.';
      }
    });
  }

  startBtn.addEventListener('click', async () => {
    // Lock the picker/free-text controls for the rest of this check — once
    // a symptom set is committed, re-touching them must never silently mix
    // stale answers from this run into a later one (only "Start a new
    // check", which fully resets state, unlocks them again).
    document.getElementById('triageInputCards').style.display = 'none';
    await buildTriageQueue();
    // Seed the real base severity (e.g. chest_pain starts at MODERATE, not
    // LOW) before the first question is even asked — rule 3: the result
    // before any follow-up is already real.
    const preview = await api('POST', '/triage/preview', {
      patient_id: state.patientId, symptom_ids: state.selectedSymptoms, answers: {},
    });
    state.triageSeverity = preview.severity;
    askNextTriageQuestion();
  });
}

async function buildTriageQueue() {
  state.triageQueue = [];
  for (const sid of state.selectedSymptoms) {
    const questions = await api('GET', `/triage/symptoms/${sid}/questions?lang=${state.lang}`);
    for (const q of questions) state.triageQueue.push({ symptom_id: sid, question: q });
  }
}

async function askNextTriageQuestion() {
  const flow = document.getElementById('triageFlow');

  if (state.triageSeverity === 'EMERGENCY' || state.triageQueue.length === 0) {
    return submitTriage();
  }

  const next = state.triageQueue.shift();
  const suggested = state.suggestedAnswers[next.question.id];
  flow.innerHTML = `
    <div class="card">
      <div class="badge ${badgeClass(state.triageSeverity)}" style="margin-bottom:10px;">so far: ${state.triageSeverity}</div>
      <label style="font-size:16px;">${next.question.text}</label>
      ${suggested !== undefined ? `<div style="font-size:12px;color:var(--ink-soft);margin-top:4px;">Suggested from your description: ${suggested ? 'Yes' : 'No'} — confirm or change it.</div>` : ''}
      <div style="display:flex;gap:10px;margin-top:12px;">
        <button class="${suggested === true ? 'primary' : 'ghost'}" data-ans="true">Yes</button>
        <button class="${suggested === false ? 'primary' : 'ghost'}" data-ans="false">No</button>
      </div>
    </div>
  `;
  flow.querySelectorAll('[data-ans]').forEach(btn => {
    btn.addEventListener('click', async () => {
      state.triageAnswers[next.question.id] = btn.dataset.ans === 'true';
      const preview = await api('POST', '/triage/preview', {
        patient_id: state.patientId,
        symptom_ids: state.selectedSymptoms,
        answers: state.triageAnswers,
      });
      state.triageSeverity = preview.severity;
      askNextTriageQuestion();
    });
  });
}

async function submitTriage() {
  state.triageDone = true;
  const flow = document.getElementById('triageFlow');
  const result = await api('POST', `/triage/check?lang=${state.lang}`, {
    patient_id: state.patientId,
    symptom_ids: state.selectedSymptoms,
    answers: state.triageAnswers,
  });

  const routeCta = result.route === 'emergency'
    ? `<a class="cta" href="tel:112"><button class="danger">Call 112</button></a>`
    : result.route === 'book_doctor'
    ? `<a class="cta" href="#" onclick="alert('Doctor booking link would go here.'); return false;"><button class="primary">Book a doctor</button></a>`
    : '';

  const sourcesHtml = (result.sources || []).length ? `
    <div style="margin-top:10px;font-size:12px;color:var(--ink-soft);">
      Based on: ${result.sources.map(s => `<a href="${s.url}" target="_blank" rel="noopener">${s.title}</a>`).join(' · ')}
    </div>
  ` : '';

  const fillPct = { LOW: 33, MODERATE: 66, EMERGENCY: 100 }[result.severity] || 33;
  flow.innerHTML = `
    <div class="severity-banner ${badgeClass(result.severity)}">
      <div class="severity-capsule" aria-hidden="true"><span class="fill" style="height:${fillPct}%"></span></div>
      <div>
        <h3>${result.severity}</h3>
        ${result.reasons.length ? `<div>Why:</div><ul>${result.reasons.map(r => `<li>${r}</li>`).join('')}</ul>` : '<div>No red-flag answers were given.</div>'}
        <div><strong>Action:</strong> ${result.action}</div>
        ${routeCta}
        ${sourcesHtml}
      </div>
    </div>
    <button class="ghost" id="newCheckBtn">Start a new check</button>
  `;
  document.getElementById('newCheckBtn').addEventListener('click', renderTriage);
}

// ---------------------------------------------------------------- report (Feature 4)

async function renderReport() {
  const view = document.getElementById('view-report');
  view.innerHTML = `<div class="empty">Loading...</div>`;
  const r = await api('GET', `/patients/${state.patientId}/report`);

  const meds = r.prescriptions.flatMap(p => p.medicines);
  const seen = new Set();
  const medRows = meds.filter((m) => {            // the same medicine entered twice shows once
    const k = `${(m.name || m.raw_text || '').toLowerCase()}|${m.dose_amount}|${m.dose_unit}|${m.when}`;
    if (seen.has(k)) return false;
    seen.add(k); return true;
  }).map(m => `
    <div class="rp-med">
      <div class="rp-med-name">${escHtml(m.name || m.raw_text)} <span class="rp-dose">${escHtml(`${m.dose_amount || ''}${m.dose_unit || ''}`)}</span></div>
      ${m.when ? `<div class="rp-when">${escHtml(m.when)}</div>` : ''}
    </div>`).join('') || '<div class="empty">No medicines yet.</div>';

  const a = r.adherence;
  const pct = a.adherence_percent;
  const day = (iso) => new Date(iso).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

  const missedList = r.missed_doses.map(d => `<div class="rp-line"><span>${escHtml(d.medicine_name)}</span><span class="rp-sub">${escHtml(day(d.scheduled_at))}</span></div>`).join('');
  const symptomList = r.symptom_history.slice(-5).reverse().map(c => `
    <div class="rp-line"><span>${escHtml(c.symptoms.join(', '))}</span>
      <span class="rp-sub">${escHtml(new Date(c.created_at).toLocaleDateString())} · <span class="badge ${badgeClass(c.severity)}">${escHtml(c.severity)}</span></span></div>`).join('');
  const hasNotes = r.doctor_caregiver_notes && r.doctor_caregiver_notes.length;
  const more = (title, count, body) => `<details class="rp-more"><summary>${title}${count ? ` <span class="rp-count">${count}</span>` : ''}</summary>${body}</details>`;

  view.innerHTML = `
    <div class="card report-simple">
      <div class="rp-head">
        <h2>Care report</h2>
        <div class="rp-who">${escHtml(r.patient.name)}${r.patient.age ? ', ' + escHtml(r.patient.age) : ''}${r.patient.sex ? ', ' + escHtml(r.patient.sex) : ''}</div>
        <div class="rp-sub">Prepared ${escHtml(new Date(r.generated_at + 'Z').toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' }))}</div>
      </div>

      ${r.patient.allergies ? `<div class="rp-allergy"><strong>Allergies:</strong> ${escHtml(r.patient.allergies)}</div>` : ''}
      ${r.alerts.length ? `<div class="rp-alerts">${r.alerts.map(x => `<div>${escHtml(x)}</div>`).join('')}</div>` : ''}

      <h3 class="rp-title">Medicines I take</h3>
      ${medRows}

      <h3 class="rp-title">How my doses went</h3>
      <div class="rp-stats">
        <div><div class="rp-num">${a.taken}</div><div class="rp-sub">Taken</div></div>
        <div><div class="rp-num">${a.missed}</div><div class="rp-sub">Missed</div></div>
        <div><div class="rp-num">${pct === null ? '—' : pct + '%'}</div><div class="rp-sub">On time</div></div>
      </div>

      ${r.missed_doses.length ? more('Missed doses', r.missed_doses.length, missedList) : ''}
      ${r.symptom_history.length ? more('Symptom checks', r.symptom_history.length, symptomList) : ''}
      ${hasNotes ? more('Doctor notes', r.doctor_caregiver_notes.length, `<div id="notesList">${renderNotesList(r.doctor_caregiver_notes)}</div>`) : ''}

      <div class="no-print rp-actions">
        <button class="primary" onclick="downloadAuthed('/patients/${state.patientId}/report/pdf', 'smartpoli-report.pdf').catch(e => showNotice('Download failed', e.message))">Download PDF</button>
        <button class="ghost" onclick="window.print()">Print</button>
        <button class="ghost" onclick="downloadAuthed('/patients/${state.patientId}/calendar.ics', 'smartpoli-medicines.ics').catch(e => showNotice('Export failed', e.message))">Add to calendar</button>
      </div>
      <footer class="disclaimer">${escHtml(r.disclaimer)}</footer>
    </div>
  `;
}

function formatNoteActor(actor) {
  // Real actor strings now look like "doctor:<user_id>:<name>" (auth.py-
  // derived, never client-supplied — see doctor_router.py's note endpoint).
  const parts = (actor || '').split(':');
  if (parts[0] === 'doctor' && parts[2]) return `Dr. ${parts.slice(2).join(':')}`;
  if (parts[0] === 'doctor') return 'Doctor';
  return actor || 'Unknown';
}

function renderNotesList(notes) {
  if (!notes.length) return '<div class="empty">No notes yet.</div>';
  return notes.map(n => `
    <div style="padding:6px 0;border-bottom:1px solid var(--line);font-size:13px;">
      <strong>${formatNoteActor(n.actor)}</strong>
      <span style="color:var(--ink-soft);"> · ${new Date(n.at).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})}</span>
      <div>${n.note}</div>
    </div>
  `).join('');
}

// ---------------------------------------------------------------- unified timeline
//
// One chronological read of the same AuditLog every other feature already
// writes to — no separate history table, no separate feature. This is
// what turns "prescription parser + scheduler + triage" into a single
// visible treatment journey.

async function renderTimeline() {
  const view = document.getElementById('view-timeline');
  view.innerHTML = `<div class="empty">Loading...</div>`;
  const events = await api('GET', `/patients/${state.patientId}/timeline`);

  if (!events.length) {
    view.innerHTML = `<h2>${t('timelineHeading')}</h2><div class="empty">Nothing recorded yet.</div>`;
    return;
  }

  const LABEL = { taken: 'Taken', missed: 'Not taken', skipped: 'Skipped' };
  let lastDateKey = null;
  const rows = events.map(e => {
    const d = new Date(e.at);
    const dateKey = d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    const dayHeader = dateKey !== lastDateKey ? `<h3 style="margin-top:18px;">${dateKey}</h3>` : '';
    lastDateKey = dateKey;
    return `
      ${dayHeader}
      <div class="dose-row">
        <div class="time">${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
        <div style="flex:1;padding:0 10px;"><strong>${escHtml(e.summary)}</strong></div>
        <span class="tl-status ${escHtml(e.status)}">${LABEL[e.status] || escHtml(e.status)}</span>
      </div>
    `;
  }).join('');

  view.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">
      ${iconBadge('teal', 'history')}<h2 style="margin:0;">${t('timelineHeading')}</h2>
    </div>
    <div class="card">${rows}</div>
  `;
}

// ---------------------------------------------------------------- emergency card (Feature J, optional)

async function renderEmergencyCard() {
  // The whole screen is the 3D health card (health-card.js).
  await mountHealthCard(document.getElementById('view-emergency'), state.patientId);
  if (state.openCardEditor) {
    state.openCardEditor = false;
    document.querySelector('#view-emergency [data-hc="edit"]')?.click();
  }
}

// Care-team linking lives in Settings (moved off the emergency card screen).
async function renderSettingsCareTeam() {
  const mount = document.getElementById('settingsCareTeam');
  if (!state.patientId) { mount.innerHTML = ''; return; }
  mount.innerHTML = `
    <div class="card" id="careTeamCard">
      <div class="card-head">${iconBadge('teal', 'shield')}<h3>Care team</h3></div>
      <p style="color:var(--ink-soft);font-size:13px;">Link a caregiver or doctor so they can see this record —
        nothing is shared until you generate a code and they redeem it.</p>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;">
        <button class="ghost small" id="genCaregiverCodeBtn">Link a caregiver</button>
        <button class="ghost small" id="genDoctorCodeBtn">Link a doctor</button>
      </div>
      <div id="careTeamGenerated"></div>
      <div id="caregiverLinksList"></div>
      <div id="doctorLinksList"></div>
    </div>
  `;
  document.getElementById('genCaregiverCodeBtn').addEventListener('click', () => generateLinkCode('caregiver'));
  document.getElementById('genDoctorCodeBtn').addEventListener('click', () => generateLinkCode('doctor'));
  await renderCareTeamLists();
}

async function renderSettingsWhatsApp() {
  const mount = document.getElementById('settingsWhatsApp');
  const { available, link } = await api('GET', '/whatsapp/available');
  if (!available || !link) { mount.innerHTML = ''; return; }
  mount.innerHTML = `
    <div class="card">
      <div class="card-head">${iconBadge('teal', 'messageCircle')}<h3>Chat on WhatsApp</h3></div>
      <p style="color:var(--ink-soft);font-size:13px;">Send a prescription photo, mark doses taken, or get your
        emergency card link — all from WhatsApp, no app needed.</p>
      <button class="primary small" id="openWhatsAppBtn">Open WhatsApp</button>
      <p style="color:var(--ink-faint);font-size:12px;margin-top:8px;">This opens WhatsApp with a one-time
        "join" message pre-filled — just tap send once to connect.</p>
    </div>
  `;
  document.getElementById('openWhatsAppBtn').addEventListener('click', () => window.open(link, '_blank', 'noopener'));
}

async function generateLinkCode(kind) {
  const path = kind === 'caregiver' ? `/patients/${state.patientId}/caregiver-links` : `/patients/${state.patientId}/doctor-links`;
  const result = await api('POST', path);
  document.getElementById('careTeamGenerated').innerHTML = `
    <div style="margin-bottom:14px;">
      Share this code with the ${kind} — it can be redeemed once:
      <div class="link-code-display" style="margin-top:6px;">${result.code}</div>
    </div>
  `;
  await renderCareTeamLists();
}

async function renderCareTeamLists() {
  const [caregiverLinks, doctorLinks] = await Promise.all([
    api('GET', `/patients/${state.patientId}/caregiver-links`),
    api('GET', `/patients/${state.patientId}/doctor-links`),
  ]);

  const listHtml = (links, label) => links.length ? links.map(l => `
    <div class="dose-row">
      <div style="flex:1;">
        ${l.caregiver_name || l.doctor_name || `${label} (code ${l.code}, not yet redeemed)`}
        <span class="badge ${l.status === 'active' ? 'verified' : l.status === 'revoked' ? 'needs_confirmation' : 'review'}">${l.status}</span>
      </div>
      ${l.status !== 'revoked' ? `<button class="ghost small" data-revoke-link="${l.id}" data-revoke-kind="${label}">Revoke</button>` : ''}
    </div>
  `).join('') : `<div class="empty">No ${label}s linked yet.</div>`;

  document.getElementById('caregiverLinksList').innerHTML = `<h4 style="margin-top:14px;">Caregivers</h4>${listHtml(caregiverLinks, 'caregiver')}`;
  document.getElementById('doctorLinksList').innerHTML = `<h4 style="margin-top:14px;">Doctors</h4>${listHtml(doctorLinks, 'doctor')}`;

  document.querySelectorAll('[data-revoke-link]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const kind = btn.dataset.revokeKind;
      const path = kind === 'caregiver' ? `/caregiver-links/${btn.dataset.revokeLink}/revoke` : `/doctor-links/${btn.dataset.revokeLink}/revoke`;
      await api('POST', path);
      await renderCareTeamLists();
    });
  });
}

// ---------------------------------------------------------------- voice
//
// The mic button opens the voice command page (voice.html). It links back
// here with #tab=<name>, plus &edit=1 to open the card editor ("update my details").

document.getElementById('voiceBtn').addEventListener('click', () => {
  window.location.href = '/voice';
});

function openTabFromHash() {
  const m = window.location.hash.match(/^#tab=([a-z]+)(&edit=1)?$/);
  const btn = m && document.querySelector(`nav.pill-nav button[data-tab="${m[1]}"]`);
  state.openCardEditor = !!(m && m[2]);
  if (btn) btn.click();
  return !!btn;
}

// ---------------------------------------------------------------- boot

(async function boot() {
  if (!currentUser) return; // requireRole() already redirected to /login
  renderSessionChip();
  applyA11yMode();
  injectNavIcons();
  await loadPatients();
  applyNavTranslation();
  // Straight to the tab in the URL (#tab=… from the voice page); rendering the
  // dashboard first and then switching would load both.
  if (!openTabFromHash()) renderActiveTab();
  setTimeout(showOpenWarnings, 3500);
  SmartFeedback.init({ goFeedback: () => document.querySelector('nav.pill-nav button[data-tab="feedback"]').click() });            // after the screen is up: the check makes many database calls
})();
