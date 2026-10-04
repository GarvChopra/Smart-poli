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
    <span class="role-tag">Patient</span>
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

async function createNewPatient() {
  const name = prompt('Patient name?');
  if (!name) return;
  const age = prompt('Age? (optional)');
  const sex = prompt('Sex? (M/F/Other, optional)');
  const patient = await api('POST', '/patients', { name, age: age ? Number(age) : null, sex: sex || null });
  await loadPatients();
  state.patientId = patient.id;
  document.getElementById('patientSelect').value = patient.id;
  renderActiveTab();
}
document.getElementById('newPatientBtn').addEventListener('click', createNewPatient);

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
  if (!view) return;
  view.innerHTML = `
    <div class="card" style="text-align:center;padding:40px 24px;margin-top:24px;">
      <h3>Let's set up your first patient profile</h3>
      <p style="color:var(--ink-soft);font-size:14px;margin:10px 0 20px;">
        Prescriptions, schedules, symptom checks and reports are all tracked
        per patient. Create one to get started — it only takes a few seconds.
      </p>
      <button class="primary" id="createFirstPatientBtn">+ Create patient profile</button>
    </div>
  `;
  document.getElementById('createFirstPatientBtn').addEventListener('click', createNewPatient);
}

function renderActiveTab() {
  // Settings is static (patient/language/account) and is how you create
  // your first patient in the first place, so it must never be replaced
  // by the no-patient empty state — everything else needs a patient.
  if (!state.patientId && state.activeTab !== 'settings') { renderNoPatientState(); return; }
  if (!state.patientId) return;
  syncPatientTimezone(state.patientId);
  if (state.activeTab === 'prescriptions') renderPrescriptions();
  if (state.activeTab === 'dashboard') renderDashboard();
  if (state.activeTab === 'safety') renderSafetyCenter();
  if (state.activeTab === 'triage') renderTriage();
  if (state.activeTab === 'report') renderReport();
  if (state.activeTab === 'timeline') renderTimeline();
  if (state.activeTab === 'emergency') renderEmergencyCard();
  if (state.activeTab === 'settings') {
    renderSettingsCareTeam(); renderSettingsWhatsApp();
    renderNotificationsCard(document.getElementById('settingsNotifications'), state.patientId);
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
      <div>
        <strong>${m.name}</strong>
        ${m.generic_name ? `<div class="generic">generic: ${m.generic_name}</div>` : ''}
      </div>
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
      <div class="card-head">${iconBadge('teal', 'shield')}<h3>Official status of your medicines</h3></div>
      <p style="color:var(--ink-soft);font-size:13px;margin-top:0;">What can be checked in official sources (CDSCO, US FDA), and where. A missing record is not proof a medicine is unsafe or illegal.</p>
      <div id="safetyRegulatory"><button class="primary small" id="regCheckBtn">Check official sources</button></div>
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
  document.getElementById('regCheckBtn').addEventListener('click', async () => {
    const mount = document.getElementById('safetyRegulatory');
    mount.innerHTML = '<div class="empty">Checking official sources…</div>';
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
    dash.unconfirmed_medicines.length
      ? `<div class="glance-alert">${dash.unconfirmed_medicines.length} medicine(s) need confirmation</div>` : '',
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
    <p style="color:var(--ink-soft)">Type each medicine on its own line, exactly as written — shorthand and all. Or upload a photo — either way, the same confidence check applies.</p>

    <div class="card">
      <div class="card-head">${iconBadge('teal', 'pill')}<h3>Scan a medicine</h3></div>
      <p style="color:var(--ink-soft);font-size:13px;margin-top:0;">Point your camera at the strip or box so the printed name is clear.
        SmartPoli suggests what it reads — you confirm it and enter how your doctor told you to take it.</p>
      <input type="file" id="scanInput" accept="image/*" capture="environment">
      <div style="margin-top:10px;"><button class="primary" id="scanBtn">Scan medicine</button></div>
      <div id="scanResult"></div>
    </div>

    <div class="card">
      <div class="card-head">${iconBadge('teal', 'fileText')}<h3>Upload a photo</h3></div>
      <div class="upload-zone">
        <label for="rxImageInput" style="cursor:pointer;display:block;">
          ${iconBadge('teal', 'fileText')}
          <div style="margin-top:8px;font-weight:600;">Drag &amp; drop an image of your prescription</div>
          <div style="font-size:12.5px;color:var(--ink-soft);margin-top:2px;">Supports JPG, PNG, PDF (max 10MB)</div>
        </label>
        <input type="file" id="rxImageInput" accept="image/*" style="margin-top:12px;">
      </div>
      <div style="margin-top:12px;">
        <button class="primary" id="uploadBtn">Read prescription</button>
      </div>
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

  document.getElementById('scanBtn').addEventListener('click', async () => {
    const input = document.getElementById('scanInput');
    const out = document.getElementById('scanResult');
    if (!input.files.length) {
      out.innerHTML = '<div class="upload-status-banner error">Take or choose a photo of the medicine first.</div>';
      return;
    }
    out.innerHTML = '<div class="upload-status-banner loading"><span class="upload-spinner"></span><span>Reading the packaging…</span></div>';
    const form = new FormData();
    form.append('patient_id', state.patientId);
    form.append('file', input.files[0]);
    try {
      const auth = getAuth();
      const res = await fetch('/medicines/scan', {
        method: 'POST', body: form,
        headers: auth && auth.token ? { Authorization: `Bearer ${auth.token}` } : {},
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `Could not read that photo (${res.status}).`);
      renderScanResult(body);
    } catch (e) {
      out.innerHTML = `<div class="upload-status-banner error">${escHtml(e.message || 'Could not reach the server. Type the medicine in instead.')}</div>`;
    }
  });

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

  document.getElementById('uploadBtn').addEventListener('click', async () => {
    const fileInput = document.getElementById('rxImageInput');
    const status = document.getElementById('uploadStatus');
    const btn = document.getElementById('uploadBtn');
    const setStatus = (kind, text) => {
      status.className = kind ? `upload-status-banner ${kind}` : '';
      status.innerHTML = kind === 'loading' ? `<span class="upload-spinner"></span><span>${text}</span>` : text;
    };
    if (!fileInput.files.length) {
      setStatus('error', 'Choose a photo first.');
      return;
    }
    btn.disabled = true;
    btn.textContent = 'Reading…';
    setStatus('loading', 'Reading photo — a full-size phone photo can take up to a minute…');

    const form = new FormData();
    form.append('patient_id', state.patientId);
    form.append('file', fileInput.files[0]);

    try {
      const auth = getAuth();
      dashboardCache = null;  // the photo becomes a draft prescription
      const res = await fetch('/prescriptions/from-image', {
        method: 'POST', body: form,
        headers: auth && auth.token ? { Authorization: `Bearer ${auth.token}` } : {},
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        setStatus('error', detail.detail || `Could not read that photo (${res.status}).`);
        return;
      }
      const result = await res.json();
      setStatus(null, '');
      showQuickPopup('Photo read successfully', null, `${result.ocr_lines_found} medicine line(s) found`);
      renderRxResult(result);
    } catch (e) {
      setStatus('error', 'Could not reach the server. Use manual entry instead.');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Read prescription';
    }
  });

  if (lastResult) renderRxResult(lastResult);
}

// Candidate names read from a medicine box/strip. Nothing is added until the
// patient confirms name + strength and types how they were told to take it;
// the line then goes through the normal prescription review gate.
const SCAN_SCHEDULES = [
  ['1-0-0', 'Once a day (morning)'], ['0-0-1', 'Once a day (night)'], ['1-0-1', 'Twice a day'],
  ['1-1-1', 'Three times a day'], ['SOS', 'Only when needed (SOS)'],
];

function renderScanResult(res) {
  const out = document.getElementById('scanResult');
  const cands = res.candidates || [];
  const first = cands[0] || {};
  const pick = cands.length ? cands.map((c, i) => `
    <label class="scan-option">
      <input type="radio" name="scanPick" value="${i}" ${i === 0 ? 'checked' : ''}>
      <span><strong>${escHtml(c.name)}</strong> ${c.strength ? escHtml(c.strength) : ''}
        <span class="reg-meta">${c.match === 'exact' ? 'matches a known name' : 'approximate match — check carefully'}
        · read from “${escHtml(c.read_from)}”</span></span>
    </label>`).join('')
    : `<div class="empty" style="padding:6px 0;">We couldn't match a known medicine name. Type it below.
        <div class="reg-meta">Text we read: ${escHtml((res.lines_read || []).slice(0, 6).join(' · ') || 'nothing')}</div></div>`;
  out.innerHTML = `
    <div class="scan-result">
      <div style="font-weight:600;margin:10px 0 4px;">Is this your medicine?</div>
      ${pick}
      <div class="reg-meta" style="margin:6px 0;">${escHtml(res.note || '')}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;">
        <label>Medicine name<input type="text" id="scanName" value="${escHtml(first.name || '')}"></label>
        <label>Strength<input type="text" id="scanStrength" placeholder="e.g. 500mg" value="${escHtml(first.strength || '')}"></label>
        <label>Form<select id="scanForm">
          ${[['tab', 'Tablet'], ['cap', 'Capsule'], ['syrup', 'Syrup'], ['inj', 'Injection']]
            .map(([v, l]) => `<option value="${v}" ${(res.form_guess || 'tab') === v ? 'selected' : ''}>${l}</option>`).join('')}
        </select></label>
        <label>How to take it<select id="scanSchedule">
          <option value="">Choose…</option>
          ${SCAN_SCHEDULES.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}
        </select></label>
        <label>For how many days<input type="number" id="scanDays" min="1" max="365" placeholder="e.g. 5"></label>
        <label style="display:flex;align-items:flex-end;gap:6px;"><input type="checkbox" id="scanOngoing"> Ongoing</label>
      </div>
      <div class="reg-meta" style="margin-top:6px;">Enter this exactly as your doctor or the label told you. SmartPoli does not choose a dose or schedule.</div>
      <div id="scanError" style="color:var(--alarm);font-size:13px;margin-top:6px;"></div>
      <div style="margin-top:10px;"><button class="primary" id="scanAddBtn">Add medicine</button></div>
    </div>`;

  out.querySelectorAll('input[name="scanPick"]').forEach((r) => r.addEventListener('change', () => {
    const c = cands[Number(r.value)];
    document.getElementById('scanName').value = c.name;
    document.getElementById('scanStrength').value = c.strength || '';
  }));
  document.getElementById('scanAddBtn').addEventListener('click', async () => {
    const err = document.getElementById('scanError');
    const name = document.getElementById('scanName').value.trim();
    const schedule = document.getElementById('scanSchedule').value;
    const days = Number(document.getElementById('scanDays').value);
    const ongoing = document.getElementById('scanOngoing').checked;
    if (!name) { err.textContent = 'Enter the medicine name.'; return; }
    if (!schedule) { err.textContent = 'Choose how often it is taken — SmartPoli will not guess.'; return; }
    if (schedule !== 'SOS' && !ongoing && !(days >= 1)) { err.textContent = 'Enter the number of days, or tick Ongoing.'; return; }
    const strength = document.getElementById('scanStrength').value.trim();
    const form = document.getElementById('scanForm').value;
    const tail = schedule === 'SOS' ? '' : (ongoing ? ' continue' : ` x${days}d`);
    const num = (strength.match(/^\s*(\d+(?:\.\d+)?)/) || [])[1];
    const strengthPart = strength && !(num && new RegExp(`\\b${num.replace('.', '\\.')}\\s*$`).test(name)) ? ' ' + strength : '';
    const line = `${form.charAt(0).toUpperCase() + form.slice(1)} ${name}${strengthPart} ${schedule}${tail}`;
    try {
      const result = await api('POST', '/prescriptions', { patient_id: state.patientId, lines: [line] });
      out.innerHTML = '<div class="upload-status-banner loading" style="background:var(--teal-soft);color:var(--teal-dark);">Added for review — check it below, then confirm to start reminders.</div>';
      renderRxResult(result);
      document.getElementById('rxResult').scrollIntoView({ behavior: 'smooth' });
    } catch (e) { err.textContent = e.message || 'Could not add it.'; }
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
    if (open) {
      actions = `<button class="ghost small" data-act="take">Take</button>
                 <button class="ghost small" data-act="snooze">Snooze</button>
                 <button class="ghost small" data-act="skip">Skip</button>`;
    }
    const hint = open && d.food && d.food !== 'any' ? `<div class="reg-meta">Take ${escHtml(d.food)} food</div>` : '';
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
  const done = dash.left_today === 0
    ? `<div class="today-done">All done for today ✓${next ? ` Next: <strong>${escHtml(next.medicine_name)}</strong>, ${escHtml(shortTime(next.scheduled_at))}.` : ''}</div>` : '';
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
  return `<div class="alert-summary">${n} thing${n === 1 ? '' : 's'} to check</div>${alertLegendHtml()}`
    + data.conflicts.map(timingAlertHtml).join('') + note;
}

function wireConflictActions(root) {
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

function regulatoryHtml(data) {
  if (!data) return '<div class="empty">Could not reach the lookup just now.</div>';
  if (!data.medicines.length) return '<div class="empty">No confirmed medicines to check.</div>';
  const meds = data.medicines.map((m) => `
    <div class="reg-med">
      <strong>${escHtml(m.medicine)}</strong>${m.generic && m.generic.toLowerCase() !== String(m.medicine).toLowerCase() ? ` <span class="reg-meta">(ingredient: ${escHtml(m.generic)})</span>` : ''}
      ${Object.values(m.checks).map((c) => `
        <div class="reg-check">
          <span class="reg-chip ${escHtml(c.jurisdiction)}">${escHtml(c.jurisdiction)}</span>
          <span>${escHtml(REG_CHECK_LABEL[c.check] || c.check)}:</span>
          <span class="reg-status ${escHtml(c.status)}">${escHtml(REG_STATUS_LABEL[c.status] || c.status)}</span>
          <div>${escHtml(c.message)}</div>
          ${(c.matches || []).map((x) => `<div class="reg-meta">${escHtml(x.combination)} — ${escHtml(x.notification)} (<a href="${escHtml(x.source_url)}" target="_blank" rel="noopener noreferrer">${escHtml(x.list_title)}</a>)</div>`).join('')}
          ${(c.alerts || []).slice(0, 2).map((x) => `<div class="reg-meta">${escHtml(x.classification)} · ${escHtml(x.reason)} · ${escHtml(x.firm)} · ${escHtml(x.report_date)}</div>`).join('')}
          <div class="reg-meta">${c.source ? `Source: <a href="${escHtml(c.source.url)}" target="_blank" rel="noopener noreferrer">${escHtml(c.source.title)}</a>` : ''}
            ${c.checked_at ? ` · checked ${escHtml(new Date(c.checked_at + 'Z').toLocaleString())}` : ''}
            ${c.source && c.source.data_as_of ? ` · list loaded ${escHtml(c.source.data_as_of)}` : ''}
            ${c.from_stale_cache ? ' · showing an older saved result' : ''}</div>
        </div>`).join('')}
    </div>`).join('');
  return meds + `<div class="reg-meta" style="margin-top:8px;">${escHtml(data.disclaimer)}</div>`;
}

async function renderDashboard() {
  const view = document.getElementById('view-dashboard');
  view.innerHTML = `<div class="empty">Loading...</div>`;
  const dash = await getDashboard();

  const a = dash.adherence;
  const pct = a.adherence_percent === null ? '—' : `${a.adherence_percent}%`;

  const unconfirmedHtml = dash.unconfirmed_medicines.length ? `
    ${alertCardHtml({
      tone: 'action',
      icon: 'alertCircle',
      title: `${dash.unconfirmed_medicines.length} medicine${dash.unconfirmed_medicines.length === 1 ? '' : 's'} need${dash.unconfirmed_medicines.length === 1 ? 's' : ''} your confirmation`,
      problem: 'SmartPoli could not read the schedule with enough confidence, so no reminders are set for it yet.',
      action: 'Open the <strong>Prescription</strong> tab, check the line against your prescription and confirm it.',
    })}
  ` : '';

  const interactionsHtml = renderInteractionRows(dash.interactions);
  const foodWarningsHtml = renderFoodWarningRows(dash.food_warnings);

  const next = dash.upcoming_doses[0];
  const heroHtml = next ? (() => {
    const diffMs = new Date(next.scheduled_at) - new Date();
    const overdue = diffMs < 0;
    const abs = Math.abs(diffMs);
    const hrs = Math.floor(abs / 3600000);
    const mins = Math.floor((abs % 3600000) / 60000);
    const label = hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`;
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
          <button class="primary small" data-hero-take="${next.id}" style="margin-top:8px;">Mark taken</button>
        </div>
      </div>
    `;
  })() : '';

  const progressHtml = dash.per_medicine.filter(p => p.progress && p.progress.current_day !== null).map(p => `
    <div style="margin-bottom:10px;">
      <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px;font-size:13px;">
        <span>${p.name}</span>
        <span style="color:var(--ink-soft);">${p.progress.ongoing ? `Day ${p.progress.current_day}` : `Day ${p.progress.current_day} / ${p.progress.total_days}`}</span>
      </div>
      <div class="day-progress-track"><div class="day-progress-fill" style="width:${p.progress.ongoing ? 100 : Math.round(p.progress.current_day / p.progress.total_days * 100)}%"></div></div>
    </div>
  `).join('');

  const prnHtml = dash.prn_medicines.map(m => `
    <div class="dose-row">
      <div><strong>${m.name || m.raw_text}</strong> <span style="color:var(--ink-soft);font-size:12px;">as-needed</span></div>
      <button class="ghost small" data-prn-id="${m.id}">Log a dose</button>
    </div>
  `).join('') || '<div class="empty">No as-needed medicines.</div>';

  const todayHtml = renderTodaySchedule(dash);

  // Missed doses are kept apart from the upcoming ones and only the last
  // 24h are surfaced (recent_doses spans +-36h): an old miss is not an
  // action item. No catch-up time is suggested — see the note below.
  const dayAgo = Date.now() - 24 * 3600000;
  const recentMissed = (dash.recent_doses || []).filter(d => d.state === 'missed' && new Date(d.scheduled_at) >= dayAgo);
  const missedHtml = recentMissed.length ? `
    <div class="card missed-card">
      <div class="card-head">${iconBadge('amber', 'alertCircle')}<h3>Missed recently</h3></div>
      ${recentMissed.map(d => `
        <div class="missed-row">
          <span><strong>${escHtml(d.medicine_name)}</strong> · ${new Date(d.scheduled_at).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}</span>
          <button class="ghost small missed-help" data-missed-help="${d.id}">What should I do?</button>
        </div>`).join('')}
      <div class="missed-note">Don't take a double dose to catch up. If you're unsure whether to take a missed dose now, check the medicine's leaflet or ask your pharmacist or doctor. Your next dose stays as scheduled above.</div>
    </div>` : '';

  const nudgesHtml = (dash.nudges || []).map(n => `
    <div class="nudge-banner ${n.level}">${n.text}</div>
  `).join('');

  view.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px;margin-bottom:4px;">
      <div>
        <h2>${t('dashboardHeading')}</h2>
        <p style="color:var(--ink-soft);margin:0;">Stay on track with your medications and health goals.</p>
      </div>
      <a href="/patients/${state.patientId}/calendar.ics" class="no-print"><button class="ghost small"><span class="icon">${ICONS.calendar}</span>Export calendar (.ics)</button></a>
    </div>
    ${nudgesHtml}
    ${unconfirmedHtml}
    ${heroHtml}
    <div class="card" id="todayCard">
      <div class="card-head">${iconBadge('teal', 'calendar')}<h3>Today's medicines</h3></div>
      ${todayHtml}
    </div>
    <div id="dashWhatsApp"></div>
    <div class="stat-row" style="margin-bottom:16px;">
      <div class="stat">${iconBadge('teal', 'chartBar')}<div><div class="num">${pct}</div><div class="label">Adherence</div></div></div>
      <div class="stat">${iconBadge('blue', 'pill')}<div><div class="num">${a.taken}</div><div class="label">Taken</div></div></div>
      <div class="stat">${iconBadge('blue', 'clock')}<div><div class="num">${dash.left_today}</div><div class="label">Left today</div></div></div>
    </div>
    ${missedHtml}
    <div class="card" id="dashConflictsCard">
      <div class="card-head">${iconBadge('amber', 'clock')}<h3>Medicine timing</h3></div>
      <div id="dashConflicts" class="empty">Checking your schedule…</div>
    </div>
    ${progressHtml ? `
    <div class="card">
      <div class="card-head">${iconBadge('teal', 'chartBar')}<h3>Treatment progress</h3></div>
      ${progressHtml}
    </div>` : ''}
    <div class="two-col">
      ${dash.interactions.length ? `
      <div class="card">
        <div class="card-head">${iconBadge('amber', 'warning')}<h3>Drug interactions</h3></div>
        ${interactionsHtml}
      </div>` : ''}
      ${dash.food_warnings.length ? `
      <div class="card">
        <div class="card-head">${iconBadge('amber', 'utensils')}<h3>Food &amp; substance warnings</h3></div>
        ${foodWarningsHtml}
      </div>` : ''}
      <div class="card">
        <div class="card-head">${iconBadge('teal', 'pill')}<h3>As-needed (PRN / SOS)</h3></div>
        ${prnHtml}
      </div>
    </div>
  `;

  renderDashboardWhatsApp();
  loadConflictsInto(document.getElementById('dashConflicts'));
  view.querySelectorAll('[data-missed-help]').forEach((b) => b.addEventListener('click', () => showMissedGuidance(Number(b.dataset.missedHelp))));
  // A dose that was just missed gets the calm guidance popup once.
  const unseen = recentMissed.find((d) => !missedSeen().has(d.id));
  if (unseen) showMissedGuidance(unseen.id);

  const heroTakeBtn = view.querySelector('[data-hero-take]');
  if (heroTakeBtn) {
    heroTakeBtn.addEventListener('click', async () => {
      await api('POST', `/doses/${heroTakeBtn.dataset.heroTake}/take`);
      showTakenPopup(next.medicine_name, next.scheduled_at);
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
        if (act === 'take') {
          await api('POST', `/doses/${doseId}/take`);
          showTakenPopup(row.dataset.medName, row.dataset.scheduledAt);
        }
        else if (act === 'snooze') await api('POST', `/doses/${doseId}/snooze`);
        else if (act === 'skip') {
          const reason = prompt('Reason for skipping this dose?');
          if (!reason) return;
          await api('POST', `/doses/${doseId}/skip`, { reason });
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

  const medsRows = r.prescriptions.flatMap(p => p.medicines).map(m => `
    <tr>
      <td data-label="Medicine">${m.name || m.raw_text}</td>
      <td data-label="Dose">${m.dose_amount || ''} ${m.dose_unit || ''}</td>
      <td data-label="Schedule">${m.schedule_code || '—'}</td>
      <td data-label="Food">${m.food}</td>
      <td data-label="Duration (days)">${m.is_prn ? 'as needed' : (m.duration_days ?? 'ongoing')}</td>
      <td data-label="Status"><span class="badge ${badgeClass(m.status)}">${m.status.replace('_',' ')}</span></td>
    </tr>
  `).join('');

  const missedRows = r.missed_doses.map(d => `
    <tr><td data-label="When">${new Date(d.scheduled_at).toLocaleString()}</td><td data-label="Medicine">${d.medicine_name}</td></tr>
  `).join('') || '<tr><td colspan="2" class="empty">None</td></tr>';

  const triageRows = r.symptom_history.map(c => `
    <tr>
      <td data-label="Date">${new Date(c.created_at).toLocaleDateString()}</td>
      <td data-label="Symptoms">${c.symptoms.join(', ')}</td>
      <td data-label="Severity"><span class="badge ${badgeClass(c.severity)}">${c.severity}</span></td>
      <td data-label="Action">${c.action}</td>
    </tr>
  `).join('') || '<tr><td colspan="4" class="empty">None</td></tr>';

  const alertsHtml = r.alerts.map(a => `<div class="alert-item">${a}</div>`).join('') || '<div class="empty">No alerts.</div>';

  view.innerHTML = `
    <div class="card">
      <div class="report-header">
        <h2>SmartPoli Patient Care Report</h2>
        <div>${r.patient.name}${r.patient.age ? ', ' + r.patient.age : ''}${r.patient.sex ? ', ' + r.patient.sex : ''}</div>
        <div style="color:var(--ink-soft);font-size:13px;">Generated ${new Date(r.generated_at).toLocaleString()}</div>
      </div>

      <div class="report-section">
        <div class="card-head">${iconBadge('amber', 'alertCircle')}<h3>Alerts</h3></div>
        ${alertsHtml}
      </div>

      <div class="report-section">
        <div class="card-head">${iconBadge('teal', 'pill')}<h3>Prescription</h3></div>
        <div class="table-scroll"><table class="responsive-table">
          <thead><tr><th>Medicine</th><th>Dose</th><th>Schedule</th><th>Food</th><th>Duration (days)</th><th>Status</th></tr></thead>
          <tbody>${medsRows || '<tr><td colspan="6" class="empty">None</td></tr>'}</tbody>
        </table></div>
      </div>

      <div class="report-section">
        <div class="card-head">${iconBadge('teal', 'chartBar')}<h3>Adherence</h3></div>
        <div class="stat-row">
          <div class="stat">${iconBadge('teal', 'chartBar')}<div><div class="num">${r.adherence.adherence_percent ?? '—'}${r.adherence.adherence_percent !== null ? '%' : ''}</div><div class="label">Overall</div></div></div>
          <div class="stat">${iconBadge('blue', 'pill')}<div><div class="num">${r.adherence.taken}</div><div class="label">Taken</div></div></div>
          <div class="stat">${iconBadge('alarm', 'alertCircle')}<div><div class="num">${r.adherence.missed}</div><div class="label">Missed</div></div></div>
        </div>
      </div>

      <div class="report-section">
        <div class="card-head">${iconBadge('amber', 'warning')}<h3>Drug interactions</h3></div>
        ${renderInteractionRows(r.interactions)}
      </div>

      <div class="report-section">
        <div class="card-head">${iconBadge('amber', 'utensils')}<h3>Food &amp; substance warnings</h3></div>
        ${renderFoodWarningRows(r.food_warnings)}
      </div>

      <div class="report-section">
        <div class="card-head">${iconBadge('alarm', 'clock')}<h3>Missed doses</h3></div>
        <div class="table-scroll"><table class="responsive-table"><thead><tr><th>When</th><th>Medicine</th></tr></thead><tbody>${missedRows}</tbody></table></div>
      </div>

      <div class="report-section">
        <div class="card-head">${iconBadge('teal', 'stethoscope')}<h3>Symptom &amp; triage history</h3></div>
        <div class="table-scroll"><table class="responsive-table"><thead><tr><th>Date</th><th>Symptoms</th><th>Severity</th><th>Action</th></tr></thead><tbody>${triageRows}</tbody></table></div>
      </div>

      <div class="report-section">
        <div class="card-head">${iconBadge('teal', 'fileText')}<h3>Doctor notes</h3></div>
        <p style="color:var(--ink-soft);font-size:12.5px;">Added by a doctor you've linked, from their own dashboard —
          notes are read-only here (Part 8: only an authenticated, linked doctor can add one).</p>
        <div id="notesList">${renderNotesList(r.doctor_caregiver_notes)}</div>
      </div>

      <div class="no-print" style="display:flex;gap:10px;flex-wrap:wrap;margin-top:6px;">
        <button class="primary" onclick="window.print()">Print / Save as PDF (browser)</button>
        <a href="/patients/${state.patientId}/report/pdf"><button class="ghost">Download PDF report</button></a>
      </div>

      <footer class="disclaimer">${r.disclaimer}</footer>
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
        <div style="flex:1;padding:0 10px;">
          ${e.summary}
          ${e.detail ? `<div style="font-size:12px;color:var(--ink-soft);">${e.detail}</div>` : ''}
        </div>
        <div style="font-size:11px;color:var(--ink-soft);">${e.actor}</div>
      </div>
    `;
  }).join('');

  view.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">
      ${iconBadge('teal', 'history')}<h2 style="margin:0;">${t('timelineHeading')}</h2>
    </div>
    <p style="color:var(--ink-soft);">Every prescription, dose, symptom check and note, in one chronological view.</p>
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

// Compact dashboard entry point to the same WhatsApp bot the Settings card
// links to. Stays empty (never breaks the dashboard) if WhatsApp isn't
// configured or the lookup fails.
async function renderDashboardWhatsApp() {
  const mount = document.getElementById('dashWhatsApp');
  if (!mount) return;
  try {
    const { available, link } = await api('GET', '/whatsapp/available');
    if (!available || !link) return;
    mount.innerHTML = `
      <div class="card dash-whatsapp">
        <div style="display:flex;align-items:center;gap:12px;">
          ${iconBadge('teal', 'messageCircle')}
          <div style="flex:1;min-width:160px;">
            <strong>Get dose reminders on WhatsApp</strong>
            <div style="color:var(--ink-soft);font-size:12.5px;">Reminders, mark doses taken, send a prescription photo. Tap send once on the pre-filled "join" message to connect.</div>
          </div>
          <button class="primary small" id="dashWhatsAppBtn">Open WhatsApp</button>
        </div>
      </div>`;
    document.getElementById('dashWhatsAppBtn').addEventListener('click', () => window.open(link, '_blank', 'noopener'));
  } catch (e) { /* optional entry point — leave the slot empty */ }
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
  window.location.href = '/static/voice.html';
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
  if (!currentUser) return; // requireRole() already redirected to /static/login.html
  renderSessionChip();
  applyA11yMode();
  injectNavIcons();
  await loadPatients();
  applyNavTranslation();
  // Straight to the tab in the URL (#tab=… from the voice page); rendering the
  // dashboard first and then switching would load both.
  if (!openTabFromHash()) renderActiveTab();
})();
