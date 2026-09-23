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
  activeTab: 'prescriptions',
  symptoms: [],
  selectedSymptoms: [],
  triageQueue: [],       // [{symptom_id, question}]
  triageAnswers: {},
  triageSeverity: 'LOW',
  triageDone: false,
};

const api = apiFetch; // defined in auth.js — attaches the bearer token, handles 401

function el(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function badgeClass(status) { return status.toLowerCase(); }
// ICONS / iconBadge() are defined in icons.js, loaded before this file.

function renderInteractionRows(interactions) {
  if (!interactions.length) return '<div class="empty">No known interactions among your active medicines.</div>';
  return interactions.map(i => `
    <div class="interaction-row">
      <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px;">
        <span class="interaction-pair">${i.drug_a} + ${i.drug_b}</span>
        <span class="badge ${badgeClass(i.severity)}">${i.severity.toLowerCase()}</span>
      </div>
      <div style="font-size:13px;color:var(--ink-soft);margin-top:4px;">${i.description}</div>
    </div>
  `).join('');
}

function renderFoodWarningRows(warnings) {
  if (!warnings.length) return '<div class="empty">No specific food/substance warnings for your active medicines.</div>';
  return warnings.map(w => `
    <div class="interaction-row">
      <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px;">
        <span class="interaction-pair">${w.drug}</span>
        <span class="badge ${badgeClass(w.severity)}">${w.severity.toLowerCase()}</span>
      </div>
      <div style="font-size:13px;margin-top:4px;">Avoid: <strong>${w.avoid}</strong></div>
      <div style="font-size:13px;color:var(--ink-soft);margin-top:2px;">${w.description}</div>
    </div>
  `).join('');
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
    triageHeading: 'Symptom check', emergencyHeading: 'Emergency card', timelineHeading: 'Treatment timeline',
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

document.getElementById('newPatientBtn').addEventListener('click', async () => {
  const name = prompt('Patient name?');
  if (!name) return;
  const age = prompt('Age? (optional)');
  const sex = prompt('Sex? (M/F/Other, optional)');
  const patient = await api('POST', '/patients', { name, age: age ? Number(age) : null, sex: sex || null });
  await loadPatients();
  state.patientId = patient.id;
  document.getElementById('patientSelect').value = patient.id;
  renderActiveTab();
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

function renderActiveTab() {
  if (!state.patientId) return;
  if (state.activeTab === 'prescriptions') renderPrescriptions();
  if (state.activeTab === 'dashboard') renderDashboard();
  if (state.activeTab === 'safety') renderSafetyCenter();
  if (state.activeTab === 'triage') renderTriage();
  if (state.activeTab === 'report') renderReport();
  if (state.activeTab === 'timeline') renderTimeline();
  if (state.activeTab === 'emergency') renderEmergencyCard();
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

  const dosageHtml = s.dosage_warnings.map(w => `
    <div class="alert-item" style="border-color:${w.severity === 'REVIEW' ? 'var(--alarm)' : 'var(--amber)'};">
      <strong>${w.medicine}:</strong> ${w.message}
    </div>
  `).join('');

  view.innerHTML = `
    <h2>Medication safety center</h2>
    <p style="color:var(--ink-soft);">One place for interaction checks, food warnings and basic dosage sanity checks — all rule-based, none of it a clinical review.</p>

    <div class="card">
      <div class="card-head">${iconBadge('teal', 'shield')}<h3>Your medicines</h3></div>
      ${medsHtml}
    </div>
    ${dosageHtml ? `<div class="card"><div class="card-head">${iconBadge('amber', 'alertCircle')}<h3>Dosage checks</h3></div>${dosageHtml}</div>` : ''}
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
}

// ---------------------------------------------------------------- at-a-glance rail
// Fills the wide-viewport side column so it never reads as empty/broken —
// always shows next dose, today's adherence and anything needing attention,
// independent of whichever tab is open.

async function renderGlance() {
  const panel = document.getElementById('glancePanel');
  if (!panel || !state.patientId) return;
  const patient = state.patients.find(p => p.id === state.patientId);
  const dash = await api('GET', `/patients/${state.patientId}/dashboard`);
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
      <div id="uploadStatus" style="margin-top:10px;font-size:13px;color:var(--ink-soft);"></div>
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
    if (!fileInput.files.length) {
      status.textContent = 'Choose a photo first.';
      return;
    }
    status.textContent = 'Reading photo — this can take a little while the first time (loading the OCR model)...';

    const form = new FormData();
    form.append('patient_id', state.patientId);
    form.append('file', fileInput.files[0]);

    try {
      const auth = getAuth();
      const res = await fetch('/prescriptions/from-image', {
        method: 'POST', body: form,
        headers: auth && auth.token ? { Authorization: `Bearer ${auth.token}` } : {},
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        status.textContent = detail.detail || `Could not read that photo (${res.status}).`;
        return;
      }
      const result = await res.json();
      status.textContent = `Found ${result.ocr_lines_found} line(s) in the photo.`;
      renderRxResult(result);
    } catch (e) {
      status.textContent = 'Could not reach the server. Use manual entry instead.';
    }
  });

  if (lastResult) renderRxResult(lastResult);
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

async function renderDashboard() {
  const view = document.getElementById('view-dashboard');
  view.innerHTML = `<div class="empty">Loading...</div>`;
  const dash = await api('GET', `/patients/${state.patientId}/dashboard`);

  const a = dash.adherence;
  const pct = a.adherence_percent === null ? '—' : `${a.adherence_percent}%`;

  const unconfirmedHtml = dash.unconfirmed_medicines.length ? `
    <div class="alert-item">${dash.unconfirmed_medicines.length} medicine(s) need confirmation before they're scheduled — fix them on the Prescription tab.</div>
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

  const upcomingHtml = dash.upcoming_doses.map(d => `
    <div class="dose-row" data-dose-id="${d.id}">
      <div class="time">${new Date(d.scheduled_at).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})}</div>
      <div style="flex:1;padding:0 10px;">${d.medicine_name}</div>
      <div class="actions">
        <button class="ghost small" data-act="take">Take</button>
        <button class="ghost small" data-act="snooze">Snooze</button>
        <button class="ghost small" data-act="skip">Skip</button>
      </div>
    </div>
  `).join('') || '<div class="empty">Nothing scheduled yet — decode and confirm a prescription first.</div>';

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
    <div class="stat-row" style="margin-bottom:16px;">
      <div class="stat">${iconBadge('teal', 'chartBar')}<div><div class="num">${pct}</div><div class="label">Adherence</div></div></div>
      <div class="stat">${iconBadge('blue', 'pill')}<div><div class="num">${a.taken}</div><div class="label">Taken</div></div></div>
      <div class="stat">${iconBadge('alarm', 'alertCircle')}<div><div class="num">${a.missed}</div><div class="label">Missed</div></div></div>
      <div class="stat">${iconBadge('blue', 'clock')}<div><div class="num">${a.pending}</div><div class="label">Upcoming</div></div></div>
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
      <div class="card">
        <div class="card-head">${iconBadge('blue', 'clock')}<h3>Upcoming doses</h3></div>
        ${upcomingHtml}
      </div>
    </div>
  `;

  const heroTakeBtn = view.querySelector('[data-hero-take]');
  if (heroTakeBtn) {
    heroTakeBtn.addEventListener('click', async () => {
      await api('POST', `/doses/${heroTakeBtn.dataset.heroTake}/take`);
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

  view.querySelectorAll('.dose-row[data-dose-id]').forEach(row => {
    const doseId = row.dataset.doseId;
    row.querySelectorAll('[data-act]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const act = btn.dataset.act;
        if (act === 'take') await api('POST', `/doses/${doseId}/take`);
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

  const fillPct = { LOW: 33, MODERATE: 66, EMERGENCY: 100 }[result.severity] || 33;
  flow.innerHTML = `
    <div class="severity-banner ${badgeClass(result.severity)}">
      <div class="severity-capsule" aria-hidden="true"><span class="fill" style="height:${fillPct}%"></span></div>
      <div>
        <h3>${result.severity}</h3>
        ${result.reasons.length ? `<div>Why:</div><ul>${result.reasons.map(r => `<li>${r}</li>`).join('')}</ul>` : '<div>No red-flag answers were given.</div>'}
        <div><strong>Action:</strong> ${result.action}</div>
        ${routeCta}
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
  const view = document.getElementById('view-emergency');
  view.innerHTML = `<div class="empty">Loading...</div>`;

  const patient = state.patients.find(p => p.id === state.patientId);
  const data = await api('GET', `/patients/${state.patientId}/emergency-card`);
  const cardUrl = `${window.location.origin}/emergency/${state.patientId}`;

  const scheduledRows = data.scheduled_medicines.map(m => `
    <li><strong>${m.name}</strong>${m.dose_amount ? ' ' + m.dose_amount + (m.dose_unit || '') : ''}${m.schedule_code ? ' — ' + m.schedule_code : ''}</li>
  `).join('') || '<li class="empty">None on file.</li>';

  const prnRows = data.as_needed_medicines.map(m => `<li><strong>${m.name}</strong></li>`).join('')
    || '<li class="empty">None on file.</li>';

  view.innerHTML = `
    <h2>${t('emergencyHeading')}</h2>
    <p style="color:var(--ink-soft);">Scan the QR to open a plain, no-login page with just what a first responder needs — nothing else from the record.</p>

    <div class="card" style="display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start;">
      <img id="qrImg" width="160" height="160" alt="QR code linking to this patient's emergency card" style="border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--paper);">
      <div style="flex:1;min-width:220px;">
        <div class="card-head" style="margin-bottom:10px;">${iconBadge('alarm', 'siren')}<h3 style="margin:0;">${patient ? patient.name : ''}</h3></div>
        ${data.has_emergency_triage_history ? '<div class="alert-item">Has a history of an EMERGENCY-graded symptom check.</div>' : ''}
        <div style="margin-bottom:10px;">
          <strong>Allergies:</strong> ${data.patient.allergies || '<span class="empty" style="padding:0;">none recorded</span>'}
          <button class="ghost small" id="editAllergiesBtn" style="margin-left:8px;">Edit</button>
        </div>
        <div>
          <strong>Emergency contact:</strong> ${data.patient.emergency_contact || '<span class="empty" style="padding:0;">none recorded</span>'}
          <button class="ghost small" id="editContactBtn" style="margin-left:8px;">Edit</button>
        </div>
        <div style="margin-top:14px;"><a href="${cardUrl}" target="_blank">${cardUrl}</a></div>
      </div>
    </div>

    <div class="two-col">
      <div class="card">
        <div class="card-head">${iconBadge('teal', 'pill')}<h3>Scheduled medicines</h3></div>
        <ul>${scheduledRows}</ul>
      </div>
      <div class="card">
        <div class="card-head">${iconBadge('teal', 'pill')}<h3>As-needed medicines</h3></div>
        <ul>${prnRows}</ul>
      </div>
    </div>

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

  document.getElementById('editAllergiesBtn').addEventListener('click', async () => {
    const value = prompt('Allergies (comma-separated, or leave blank for none):', data.patient.allergies || '');
    if (value === null) return;
    await api('PATCH', `/patients/${state.patientId}`, { allergies: value });
    renderEmergencyCard();
  });
  document.getElementById('editContactBtn').addEventListener('click', async () => {
    const value = prompt('Emergency contact (name, phone):', data.patient.emergency_contact || '');
    if (value === null) return;
    await api('PATCH', `/patients/${state.patientId}`, { emergency_contact: value });
    renderEmergencyCard();
  });

  document.getElementById('genCaregiverCodeBtn').addEventListener('click', () => generateLinkCode('caregiver'));
  document.getElementById('genDoctorCodeBtn').addEventListener('click', () => generateLinkCode('doctor'));
  await renderCareTeamLists();

  // <img src> can't carry an Authorization header, so the QR PNG (an
  // authenticated endpoint) is fetched with the bearer token and rendered
  // as a blob URL instead of pointed at directly.
  const auth = getAuth();
  fetch(`/patients/${state.patientId}/emergency-card/qr.png`, {
    headers: auth && auth.token ? { Authorization: `Bearer ${auth.token}` } : {},
  })
    .then(res => (res.ok ? res.blob() : Promise.reject(res)))
    .then(blob => { document.getElementById('qrImg').src = URL.createObjectURL(blob); })
    .catch(() => {});
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

// ---------------------------------------------------------------- voice (Feature F, optional)
//
// Web Speech API only — no server call, no API key, no new dependency.
// Routing is plain keyword matching (deterministic, same ethos as the
// rest of the app): voice only navigates and reads back data that's
// already on screen, and for a symptom description it hands off to the
// SAME free-text triage flow a typed description would use — it never
// itself decides a severity or takes an action on the patient's record.

function speak(text) {
  try {
    if (!window.speechSynthesis) return;
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = state.lang === 'hi' ? 'hi-IN' : 'en-IN';
    window.speechSynthesis.speak(utter);
  } catch (e) { /* speech synthesis is a nice-to-have, never block on it */ }
}

async function handleVoiceCommand(transcript) {
  const text = transcript.toLowerCase();
  const voiceStatusEl = document.getElementById('voiceBtn');

  const remindMatch = text.match(/remind me in (\d+)\s*(minute|min)/);
  if (remindMatch) {
    const minutes = Number(remindMatch[1]);
    speak(`Okay, I will remind you in ${minutes} minutes.`);
    setTimeout(async () => {
      speak('This is your reminder.');
      if (window.Notification && Notification.permission === 'granted') {
        new Notification('SmartPoli reminder', { body: 'This is your reminder to take your medicine.' });
      } else {
        alert('SmartPoli reminder: time to take your medicine.');
      }
    }, minutes * 60000);
    if (window.Notification && Notification.permission === 'default') Notification.requestPermission();
    return;
  }

  // "I took my evening medicine" / "I missed my morning medicine" — Part 6
  // of the brief. Converts straight into the SAME take/miss endpoints the
  // dashboard buttons call; no separate voice-only medication engine.
  const takeMatch = text.match(/\bi took my (morning|afternoon|evening|night)?\s*medic/);
  if (takeMatch) return voiceActOnDose('take', takeMatch[1]);

  const missMatch = text.match(/\bi missed my (morning|afternoon|evening|night)?\s*medic/);
  if (missMatch) return voiceActOnDose('miss', missMatch[1]);

  if (/schedule|medic|dose|today/.test(text)) {
    document.querySelector('[data-tab="dashboard"]').click();
    await new Promise(r => setTimeout(r, 300));
    const dash = await api('GET', `/patients/${state.patientId}/dashboard`);
    const next = dash.upcoming_doses[0];
    speak(next
      ? `Your next dose is ${next.medicine_name} at ${new Date(next.scheduled_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}. Adherence is ${dash.adherence.adherence_percent ?? 'not yet available'} percent.`
      : 'You have nothing scheduled right now.');
    return;
  }

  if (/taken|missed/.test(text)) {
    const dash = await api('GET', `/patients/${state.patientId}/dashboard`);
    speak(`You have taken ${dash.adherence.taken} doses and missed ${dash.adherence.missed} so far.`);
    return;
  }

  // Anything else — treat as a symptom description, same as typing it in.
  document.querySelector('[data-tab="triage"]').click();
  await new Promise(r => setTimeout(r, 300));
  const input = document.getElementById('freeTextInput');
  if (input) {
    input.value = transcript;
    speak("I've put that in the symptom check for you to review.");
    document.getElementById('interpretBtn')?.click();
  } else {
    speak('I heard: ' + transcript + '. Please use the symptom picker — free-text interpretation needs an API key that is not set up.');
  }
}

async function voiceActOnDose(kind, slotWord) {
  const dash = await api('GET', `/patients/${state.patientId}/dashboard`);
  const candidates = dash.upcoming_doses.filter((d) => {
    if (!slotWord) return true;
    const hour = new Date(d.scheduled_at).getHours();
    const slot = hour < 11 ? 'morning' : hour < 16 ? 'afternoon' : hour < 19 ? 'evening' : 'night';
    return slot === slotWord;
  });
  const dose = candidates[0];
  if (!dose) {
    speak(`I couldn't find a matching ${slotWord ? slotWord + ' ' : ''}dose to mark. Please use the dashboard instead.`);
    return;
  }
  await api('POST', `/doses/${dose.id}/${kind}`);
  speak(kind === 'take' ? `Marked ${dose.medicine_name} as taken.` : `Marked ${dose.medicine_name} as missed.`);
  document.querySelector('[data-tab="dashboard"]').click();
  await new Promise((r) => setTimeout(r, 200));
  renderDashboard();
  renderGlance();
}

document.getElementById('voiceBtn').addEventListener('click', () => {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    alert('Voice input is not supported in this browser. Try Chrome.');
    return;
  }
  const recognition = new Recognition();
  recognition.lang = state.lang === 'hi' ? 'hi-IN' : 'en-IN';
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  const fab = document.getElementById('voiceBtn');
  const label = document.getElementById('voiceBtnLabel');
  fab.classList.add('listening');
  label.textContent = 'Listening...';
  recognition.start();

  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    fab.classList.remove('listening');
    label.textContent = 'Voice';
    handleVoiceCommand(transcript);
  };
  recognition.onerror = () => { fab.classList.remove('listening'); label.textContent = 'Voice'; };
  recognition.onend = () => { fab.classList.remove('listening'); label.textContent = 'Voice'; };
});

// ---------------------------------------------------------------- boot

(async function boot() {
  if (!currentUser) return; // requireRole() already redirected to /static/login.html
  renderSessionChip();
  applyA11yMode();
  injectNavIcons();
  await loadPatients();
  applyNavTranslation();
  renderActiveTab();
})();
