// SmartPoli — doctor dashboard (Parts 1 and 7 of the brief).
//
// Same pattern as caregiver.js: every request carries the doctor's real
// bearer token, and doctor_router.py is what actually enforces that this
// account only ever reaches a patient it holds an ACTIVE DoctorLink for.
// A correction here is never silent — see submitCorrection() below, which
// always shows the server's before/after + who + why back to the doctor
// immediately after saving it.
//
// The per-patient detail is split across tabs (Overview / Prescriptions /
// Adherence / Triage history / Notes) reached through the hamburger drawer,
// instead of one long page — the full report is fetched once per patient
// selection (state.currentReport) and each tab just renders its own slice
// of it, so switching tabs never re-fetches.

const currentUser = requireRole('doctor');
const api = apiFetch;

const state = {
  patients: [], patientId: null, templates: [], activeTab: 'patients', currentReport: null,
  selectedMedicineId: null, selectedDoseDate: null, prescriptionDoseHistory: {}, prescriptionHistoryRequest: 0,
};

const CORRECTABLE_FIELDS = ['name', 'dose_amount', 'dose_unit', 'schedule_code', 'food', 'duration_days'];

function badgeClass(status) { return (status || '').toLowerCase(); }

function doctorEscapeHtml(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]));
}

const PRIORITY_LABELS = { emergency: 'Emergency', high: 'High', medium: 'Medium', routine: 'Routine' };

function renderPriorityBadge(priority) {
  if (!priority) return '';
  return `<span class="badge ${priority.level}">${PRIORITY_LABELS[priority.level] || priority.level}</span>`;
}

function renderPriorityReasons(priority) {
  if (!priority || !priority.reasons.length) return '';
  return `<ul class="priority-reasons">${priority.reasons.map(r => `<li>${r}</li>`).join('')}</ul>`;
}

function priorityFor(patientId) {
  const p = state.patients.find(p => p.id === patientId);
  return p ? p.priority : null;
}

function renderSessionChip() {
  const chip = document.getElementById('sessionChip');
  if (!chip || !currentUser) return;
  chip.innerHTML = `
    <span class="who">${currentUser.name}</span>
    <span class="role-tag">Doctor</span>
    <button class="ghost small" id="logoutBtn">Log out</button>
  `;
  document.getElementById('logoutBtn').addEventListener('click', logout);
}

function applyA11yMode() {
  const on = localStorage.getItem('smartpoli_a11y') === '1';
  document.documentElement.dataset.a11y = on ? 'large' : '';
  document.getElementById('a11yToggleBtn')?.setAttribute('aria-pressed', String(on));
}
document.getElementById('a11yToggleBtn').addEventListener('click', () => {
  const on = localStorage.getItem('smartpoli_a11y') === '1';
  localStorage.setItem('smartpoli_a11y', on ? '0' : '1');
  applyA11yMode();
});

/** Injects each nav button's icon from icons.js, same helper as app.js —
 * one injection point instead of hand-writing SVG markup per page. */
function injectNavIcons() {
  document.querySelectorAll('nav.pill-nav button[data-icon]').forEach((btn) => {
    if (btn.querySelector('.nav-icon')) return;
    const icon = ICONS[btn.dataset.icon];
    if (!icon) return;
    btn.insertAdjacentHTML('afterbegin', `<span class="nav-icon">${icon}</span>`);
  });
}

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

/** Switches the visible section + nav highlight only — never renders stale
 * data itself. Used both for a plain tab click (caller re-renders from the
 * already-fetched report right after) and from selectPatient() (which lands
 * on Overview then lets loadPatientDetail()'s own fetch/render take over, so
 * the previous patient's data never flashes first). */
function setActiveNavAndSection(tabName) {
  document.querySelectorAll('nav.pill-nav button[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tabName));
  state.activeTab = tabName;
  document.querySelectorAll('main.content > section').forEach(s => s.style.display = 'none');
  document.getElementById(`view-${tabName}`).style.display = ''; // let doctor.css pick block/grid per breakpoint
  const recordTabs = document.getElementById('recordTabs');
  if (recordTabs) recordTabs.style.display = ['overview', 'prescriptions', 'triage'].includes(tabName) ? 'flex' : 'none';
  recordTabs?.querySelectorAll('[data-record-tab]').forEach(b => b.classList.toggle('active', b.dataset.recordTab === tabName));
  renderCurrentPatientBar();
}

/** The picker (search + link-a-patient) lives only on the Patients tab now;
 * every other patient-specific tab shows this compact bar instead, so the
 * doctor always knows whose record they're viewing and can jump back. */
function renderCurrentPatientBar() {
  const bar = document.getElementById('currentPatientBar');
  if (!bar) return;
  if (state.activeTab === 'patients' || state.activeTab === 'settings' || !state.patientId) {
    bar.style.display = 'none';
    return;
  }
  const p = state.patients.find(p => p.id === state.patientId);
  bar.style.display = 'flex';
  bar.innerHTML = `
    <span><strong>${p ? p.name : 'Patient'}</strong>${p && p.age ? ' · ' + p.age + ' yrs' : ''}${p && p.sex ? ', ' + p.sex : ''}</span>
    <button type="button" class="ghost small" id="switchPatientBtn">Switch patient</button>
  `;
  document.getElementById('switchPatientBtn').addEventListener('click', () => setActiveNavAndSection('patients'));
}

document.querySelectorAll('nav.pill-nav button[data-tab]').forEach(btn => {
  btn.addEventListener('click', () => {
    setActiveNavAndSection(btn.dataset.tab);
    renderActiveDoctorTab();
    closeDrawer();
  });
});

document.querySelectorAll('[data-record-tab]').forEach(btn => btn.addEventListener('click', () => {
  setActiveNavAndSection(btn.dataset.recordTab);
  renderActiveDoctorTab();
}));

document.getElementById('linkPatientBtn').addEventListener('click', () => {
  document.getElementById('linkForm').style.display = 'block';
});
document.getElementById('cancelLinkBtn').addEventListener('click', () => {
  document.getElementById('linkForm').style.display = 'none';
});
document.getElementById('redeemLinkBtn').addEventListener('click', async () => {
  const code = document.getElementById('linkCodeInput').value.trim();
  const status = document.getElementById('linkStatus');
  if (!code) return;
  try {
    const result = await api('POST', '/doctor/link/redeem', { code });
    status.style.color = 'var(--teal-dark)';
    status.textContent = `Linked to ${result.patient.name}.`;
    document.getElementById('linkCodeInput').value = '';
    await loadPatients();
    selectPatient(result.patient.id, { switchToOverview: true });
  } catch (e) {
    status.style.color = 'var(--alarm)';
    status.textContent = e.message;
  }
});

async function loadPatients() {
  state.patients = await api('GET', '/doctor/patients');
  if (!state.patients.length) {
    document.getElementById('patientPicker').innerHTML = '<div class="empty">No linked patients yet — use "+ Link a patient" above.</div>';
    return;
  }
  renderPatientPicker(state.patients);
  if (!state.patientId) selectPatient(state.patients[0].id);
}

function renderPatientPicker(patients) {
  const picker = document.getElementById('patientPicker');
  picker.innerHTML = patients.map(p => `
    <button type="button" class="patient-picker-card ${p.id === state.patientId ? 'active' : ''}" data-pid="${p.id}">
      <div class="name">${doctorEscapeHtml(p.name)}</div>
      <div class="meta">${p.age ? p.age + ' yrs' : ''}${p.sex ? ', ' + p.sex : ''}</div>
      <div class="priority-row">
        ${renderPriorityBadge(p.priority)}
        ${renderPriorityReasons(p.priority)}
      </div>
    </button>
  `).join('') || '<div class="empty">No patients match that search.</div>';
  picker.querySelectorAll('[data-pid]').forEach(btn => {
    btn.addEventListener('click', () => selectPatient(Number(btn.dataset.pid), { switchToOverview: true }));
  });
}

document.getElementById('patientSearchInput').addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  const filtered = q ? state.patients.filter(p => p.name.toLowerCase().includes(q)) : state.patients;
  renderPatientPicker(filtered);
});

function selectPatient(id, { switchToOverview = false } = {}) {
  if (state.patientId !== id) {
    state.currentReport = null;
    state.selectedMedicineId = null;
    state.selectedDoseDate = null;
    state.prescriptionDoseHistory = {};
    state.prescriptionHistoryRequest += 1;
  }
  state.patientId = id;
  document.querySelectorAll('#patientPicker [data-pid]').forEach(btn => {
    btn.classList.toggle('active', Number(btn.dataset.pid) === id);
  });
  if (switchToOverview) setActiveNavAndSection('overview');
  else renderCurrentPatientBar();
  loadPatientDetail();
}

function isLaptopWidth() { return window.matchMedia('(min-width: 1024px)').matches; }

const PATIENT_DRIVEN_TABS = ['overview', 'prescriptions', 'triage'];
let patientDetailRequest = 0;

async function loadPatientDetail() {
  if (state.activeTab === 'settings') return; // settings is patient-independent, nothing to fetch
  const patientId = state.patientId;
  const request = ++patientDetailRequest;
  if (PATIENT_DRIVEN_TABS.includes(state.activeTab)) {
    const view = document.getElementById(`view-${state.activeTab}`);
    if (view) view.innerHTML = '<div class="empty">Loading...</div>';
  }
  const isCurrent = () => state.patientId === patientId && request === patientDetailRequest;
  try {
    const report = await api('GET', `/doctor/patients/${patientId}`);
    if (!isCurrent()) return;
    state.currentReport = report;
    renderActiveDoctorTab();
  } catch (error) {
    if (!isCurrent() || !PATIENT_DRIVEN_TABS.includes(state.activeTab)) return;
    state.currentReport = null;
    document.getElementById(`view-${state.activeTab}`).innerHTML =
      `<div class="empty" role="status">${doctorEscapeHtml(error.message)}</div>`;
  }
}

function renderActiveDoctorTab() {
  if (state.activeTab === 'settings') return; // static content, already in the page
  if (!state.patientId || !state.currentReport) return;
  if (state.activeTab === 'patients') return;
  else if (state.activeTab === 'overview') renderOverviewTab();
  else if (state.activeTab === 'prescriptions') renderPrescriptionsTab();
  else if (state.activeTab === 'triage') renderVisitsTab();
}

function renderPatientPreview() {
  const preview = document.getElementById('patientPreview');
  renderOverviewTab(preview);
  preview.querySelector('h2').insertAdjacentHTML('beforeend',
    '<button type="button" class="ghost small" id="openFullRecordBtn">Open full record</button>');
  document.getElementById('openFullRecordBtn').addEventListener('click', () => {
    setActiveNavAndSection('overview');
    renderActiveDoctorTab();
  });
}

function renderOverviewTab(view = document.getElementById('view-overview')) {
  const r = state.currentReport;
  const b = r.brief || {};
  const medicineNames = b.current_medicine_names || [];
  const visibleMedicines = medicineNames.slice(0, 3);
  const medicineCount = Number.isFinite(b.current_medicine_count) ? b.current_medicine_count : null;
  const moreMedicines = Math.max(0, (medicineCount || medicineNames.length) - visibleMedicines.length);
  const latestNote = (r.doctor_caregiver_notes || [])[0];
  const since = r.since_last_visit;
  const sharedAllergies = b.allergies == null ? 'Not shared' : b.allergies;
  const lastVisit = since
    ? `${since.days_since === 0 ? 'Today' : `${since.days_since} day${since.days_since === 1 ? '' : 's'} ago`}`
    : 'First shared visit';
  const initials = r.patient.name.split(/\s+/).map(part => part[0]).slice(0, 2).join('').toUpperCase();
  const alertHtml = (r.alerts || []).length
    ? `<div class="doctor-summary-alerts">${r.alerts.map(a => `<div class="alert-item">${doctorEscapeHtml(a)}</div>`).join('')}</div>`
    : '';

  view.innerHTML = `
    <section class="doctor-summary">
      <header class="doctor-summary-head">
        <div class="doctor-summary-avatar" aria-hidden="true">${doctorEscapeHtml(initials)}</div>
        <div>
          <h2>${doctorEscapeHtml(r.patient.name)}</h2>
          <p>Patient-shared care record</p>
        </div>
        <div class="doctor-summary-priority">${renderPriorityBadge(priorityFor(state.patientId))}</div>
      </header>

      <div class="doctor-summary-card">
        <section class="doctor-summary-cell">
          <span class="doctor-summary-label">Allergy</span>
          <strong>${doctorEscapeHtml(sharedAllergies)}</strong>
        </section>
        <section class="doctor-summary-cell">
          <span class="doctor-summary-label">Last visit</span>
          <strong>${doctorEscapeHtml(lastVisit)}</strong>
        </section>
        <section class="doctor-summary-cell doctor-summary-medicines">
          <span class="doctor-summary-label">Current medicines</span>
          ${medicineCount === null ? '<p>Not shared</p>' : visibleMedicines.length
            ? `<div class="doctor-summary-med-list">${visibleMedicines.map(name => `<span>${doctorEscapeHtml(name)}</span>`).join('')}</div>${moreMedicines ? `<p>+${moreMedicines} more</p>` : ''}`
            : '<p>None recorded</p>'}
        </section>
        <section class="doctor-summary-cell doctor-summary-note">
          <span class="doctor-summary-label">Recent note</span>
          <p>${latestNote ? doctorEscapeHtml(latestNote.note) : 'No visit note shared yet.'}</p>
        </section>
      </div>
      ${alertHtml}
    </section>
  `;
}

function renderPrescriptionsTab() {
  const r = state.currentReport;
  const view = document.getElementById('view-prescriptions');
  const medicines = (r.prescriptions || []).flatMap(p => (p.medicines || []).map(m => ({ ...m, prescription: p })));
  const selected = medicines.find(m => Number(m.id) === Number(state.selectedMedicineId)) || medicines[0] || null;
  state.selectedMedicineId = selected ? selected.id : null;
  const medLabel = m => doctorEscapeHtml(m.name || m.raw_text || '(name not read)');
  const medDose = m => [m.dose_amount ? `${m.dose_amount}${m.dose_unit || ''}` : '', m.when || m.schedule_code || 'Schedule not recorded']
    .filter(Boolean).map(doctorEscapeHtml).join(' · ');
  const statusLabel = m => doctorEscapeHtml(String(m.status || 'recorded').replace(/_/g, ' '));
  const medicinesHtml = medicines.map(m => `
    <button type="button" class="doctor-rx-select" data-select-medicine="${Number(m.id)}"
      aria-pressed="${selected && Number(selected.id) === Number(m.id)}">
      <span class="doctor-rx-select-copy"><strong>${medLabel(m)}</strong><small>${medDose(m)}</small></span>
      <span class="badge ${doctorEscapeHtml(badgeClass(m.status))}">${statusLabel(m)}</span>
    </button>
  `).join('') || '<div class="empty">No prescriptions on file.</div>';

  const renderSelectedMedicine = m => {
    if (!m) return '<div class="empty">Choose a medicine to review its prescription and dose history.</div>';
    const rx = m.prescription || {};
    const confidence = Object.entries(m.field_confidence || {}).filter(([key]) => key !== 'source').map(([key, value]) => {
      const pct = Math.max(0, Math.min(100, Math.round(Number(value) * 100) || 0));
      return `<div class="confidence-row"><span class="field-name">${doctorEscapeHtml(key.replace(/_/g, ' '))}</span><span class="confidence-track"><span class="confidence-fill ${doctorEscapeHtml(badgeClass(m.status))}" style="width:${pct}%"></span></span><span class="confidence-pct">${pct}%</span></div>`;
    }).join('');
    return `
      <div class="doctor-rx-detail-head"><div><small>Prescription · ${doctorEscapeHtml(rx.issued_date || 'Date not recorded')}</small><h3>${medLabel(m)}</h3></div><span class="badge ${doctorEscapeHtml(badgeClass(m.status))}">${statusLabel(m)}</span></div>
      <p class="doctor-rx-instructions">${medDose(m)}</p>
      <div class="doctor-rx-meta">Prescribed by <strong>${doctorEscapeHtml(rx.doctor_name || 'Patient record')}</strong></div>
      <section class="doctor-dose-history" aria-label="Daily medicine schedule"><h4>Daily schedule</h4><div id="doctor-dose-history" aria-live="polite"><div class="empty">Loading day…</div></div></section>
      <details class="doctor-rx-review"><summary>Prescription review</summary><div class="doctor-rx-review-body">
        <div class="raw">${doctorEscapeHtml(m.raw_text || 'No original prescription text recorded.')}</div>
        ${confidence ? `<div class="doctor-confidence-list">${confidence}</div>` : ''}
        <button type="button" class="ghost small" data-correct-id="${Number(m.id)}">Add a correction</button>
        <div class="correction-form" id="correction-${Number(m.id)}" style="display:none;">
          <label for="correctField-${Number(m.id)}">Field to correct</label><select id="correctField-${Number(m.id)}">${CORRECTABLE_FIELDS.map(f => `<option value="${f}">${doctorEscapeHtml(f.replace(/_/g, ' '))}</option>`).join('')}</select>
          <label for="correctValue-${Number(m.id)}">Correct value</label><input type="text" id="correctValue-${Number(m.id)}" placeholder="Corrected value">
          <label for="correctReason-${Number(m.id)}">Why is it being corrected?</label><input type="text" id="correctReason-${Number(m.id)}" placeholder="Reason (required; saved in the audit trail)">
          <button type="button" class="primary small" data-submit-correction="${Number(m.id)}">Save correction</button><div id="correctStatus-${Number(m.id)}" role="status" style="font-size:12px;margin-top:6px;"></div>
        </div>
        <div id="correctionHistory-${Number(m.id)}" class="doctor-correction-history"></div>
      </div></details>`;
  };

  view.innerHTML = `
    <h2 class="doctor-prescriptions-title">Prescriptions</h2>
    <div class="card doctor-prescriptions-card">
      <div class="card-head"><h3>Patient medicines</h3><span class="doctor-rx-count">${medicines.length}</span></div>
      <div class="doctor-rx-layout">
        <div class="doctor-rx-list ${medicines.length > 5 ? 'is-scrollable' : ''}" role="group" aria-label="Patient medicines">${medicinesHtml}</div>
        <section class="doctor-rx-detail" id="doctorMedicineDetail" aria-label="Selected prescription">${renderSelectedMedicine(selected)}</section>
      </div>
    </div>
    <details class="card doctor-write-card"><summary class="card-head"><h3>Write a prescription</h3><span class="doctor-write-toggle">Add</span></summary>
      <p class="doctor-write-note">The patient reviews and confirms it before SmartPoli schedules doses.</p>
      <div class="field-inline doctor-template-field"><label for="templateSelect">Use a saved template (optional)</label><select id="templateSelect"><option value="">— none —</option>${state.templates.map(t => `<option value="${Number(t.id)}">${doctorEscapeHtml(t.label)}</option>`).join('')}</select></div>
      <textarea id="newPrescriptionLines" rows="4" placeholder="Tab Dolo 650mg 1-0-1 PC x5d"></textarea>
      <div class="doctor-write-actions"><button class="primary small" id="sendPrescriptionBtn">Send to patient</button><button class="ghost small" id="saveTemplateBtn">Save as template</button></div>
      <div id="prescriptionFormStatus" role="status" class="doctor-form-status"></div>
    </details>
  `;

  const detailView = view.querySelector('#doctorMedicineDetail');
  const selectMedicine = medicine => {
    state.selectedMedicineId = medicine.id;
    state.selectedDoseDate = null;
    view.querySelectorAll('[data-select-medicine]').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.selectMedicine) === Number(medicine.id))));
    detailView.innerHTML = renderSelectedMedicine(medicine);
    bindSelectedMedicine(medicine);
  };
  const bindSelectedMedicine = medicine => {
    const detail = view.querySelector('#doctorMedicineDetail');
    if (!detail || !medicine) return;
    detail.querySelector('[data-correct-id]')?.addEventListener('click', event => {
      const form = detail.querySelector(`#correction-${Number(event.currentTarget.dataset.correctId)}`);
      if (form) form.style.display = form.style.display === 'none' ? 'grid' : 'none';
    });
    detail.querySelector('[data-submit-correction]')?.addEventListener('click', event => submitCorrection(event.currentTarget.dataset.submitCorrection));
    loadSelectedDoseHistory(medicine, detail.querySelector('#doctor-dose-history'));
    loadCorrectionHistory(medicine);
  };
  view.querySelectorAll('[data-select-medicine]').forEach(button => {
    button.addEventListener('click', () => {
      const medicine = medicines.find(m => Number(m.id) === Number(button.dataset.selectMedicine));
      if (medicine) selectMedicine(medicine);
    });
  });
  if (selected) bindSelectedMedicine(selected);

  function loadSelectedDoseHistory(_medicine, target) {
    if (!target) return;
    target.innerHTML = '<div class="empty">Loading day…</div>';
    DayTimeline.mount(target, state.patientId, {
      fetchDay: day => api('GET', `/doctor/patients/${state.patientId}/doses${day ? `?date=${encodeURIComponent(day)}` : ''}`),
    });
  }
  async function loadCorrectionHistory(medicine) {
    try {
      const corrections = await api('GET', `/doctor/medicines/${medicine.id}/corrections`);
      const target = view.querySelector(`#correctionHistory-${Number(medicine.id)}`);
      if (!target || !corrections.length) return;
      target.textContent = `Corrections: ${corrections.map(c => `${c.field} “${c.original_value}” → “${c.corrected_value}”`).join('; ')}`;
    } catch (error) { /* correction history is secondary; keep the prescription usable */ }
  }

  document.getElementById('templateSelect').addEventListener('change', (e) => {
    const template = state.templates.find(t => String(t.id) === e.target.value);
    document.getElementById('newPrescriptionLines').value = template ? template.lines.join('\n') : '';
  });

  document.getElementById('sendPrescriptionBtn').addEventListener('click', async () => {
    const status = document.getElementById('prescriptionFormStatus');
    const lines = document.getElementById('newPrescriptionLines').value.split('\n').map(l => l.trim()).filter(Boolean);
    if (!lines.length) {
      status.style.color = 'var(--alarm)';
      status.textContent = 'Enter at least one medicine line.';
      return;
    }
    try {
      await api('POST', `/doctor/patients/${state.patientId}/prescriptions`, { lines });
      status.style.color = 'var(--teal-dark)';
      status.textContent = 'Sent — the patient will see this as a draft to confirm.';
      await loadPatientDetail();
    } catch (e) {
      status.style.color = 'var(--alarm)';
      status.textContent = e.message;
    }
  });

  document.getElementById('saveTemplateBtn').addEventListener('click', async () => {
    const status = document.getElementById('prescriptionFormStatus');
    const lines = document.getElementById('newPrescriptionLines').value.split('\n').map(l => l.trim()).filter(Boolean);
    if (!lines.length) {
      status.style.color = 'var(--alarm)';
      status.textContent = 'Nothing to save — type at least one line first.';
      return;
    }
    const label = prompt('Template name?');
    if (!label) return;
    await api('POST', '/doctor/templates', { label, lines });
    await loadTemplates();
    status.style.color = 'var(--teal-dark)';
    status.textContent = `Saved as "${label}".`;
    const select = document.getElementById('templateSelect');
    select.innerHTML = `<option value="">— none —</option>` +
      state.templates.map(t => `<option value="${t.id}">${t.label}</option>`).join('');
  });

}

function renderAdherenceTab() {
  const r = state.currentReport;
  const view = document.getElementById('view-adherence');
  const missedHtml = r.missed_doses.map(d => `
    <tr><td data-label="When">${new Date(d.scheduled_at).toLocaleString()}</td><td data-label="Medicine">${d.medicine_name}</td></tr>
  `).join('') || '<tr><td colspan="2" class="empty">None</td></tr>';

  view.innerHTML = `
    <h2 style="margin-top:26px;">Adherence</h2>

    <div class="stat-row" style="margin-bottom:16px;">
      <div class="stat">${iconBadge('teal', 'chartBar')}<div><div class="num">${r.adherence.adherence_percent ?? '—'}${r.adherence.adherence_percent !== null ? '%' : ''}</div><div class="label">Adherence</div></div></div>
      <div class="stat">${iconBadge('blue', 'pill')}<div><div class="num">${r.adherence.taken}</div><div class="label">Taken</div></div></div>
      <div class="stat">${iconBadge('alarm', 'alertCircle')}<div><div class="num">${r.adherence.missed}</div><div class="label">Missed</div></div></div>
    </div>

    <div class="card">
      <div class="card-head">${iconBadge('alarm', 'clock')}<h3>Missed doses</h3></div>
      <div class="table-scroll"><table class="responsive-table"><thead><tr><th>When</th><th>Medicine</th></tr></thead><tbody>${missedHtml}</tbody></table></div>
    </div>
  `;
}

function renderVisitsTab() {
  const r = state.currentReport;
  const view = document.getElementById('view-triage');
  const triageHtml = r.symptom_history.map(c => `
    <tr>
      <td data-label="Date">${new Date(c.created_at).toLocaleDateString()}</td>
      <td data-label="Symptoms">${c.symptoms.join(', ')}</td>
      <td data-label="Severity"><span class="badge ${badgeClass(c.severity)}">${c.severity}</span></td>
      <td data-label="Action">${c.action}</td>
    </tr>
  `).join('') || '<tr><td colspan="4" class="empty">None</td></tr>';
  const notesHtml = r.doctor_caregiver_notes.map(n => `
    <div class="change-row"><span class="label">${new Date(n.at).toLocaleDateString()}</span><span class="value">${doctorEscapeHtml(n.note)}</span></div>
  `).join('') || '<div class="empty">No visit notes recorded.</div>';

  view.innerHTML = `
    <h2>Visits</h2>
    <div class="card"><div class="card-head">${iconBadge('teal', 'calendar')}<h3>Visit requests</h3></div>
      <p class="doctor-write-note">Requests from connected patients. Confirm a time, then update check-in and call status as they arrive.</p>
      <div id="doctorAppointments" aria-live="polite"><div class="empty">Loading requests…</div></div>
    </div>
    <div class="card">
      <div class="card-head">${iconBadge('teal', 'stethoscope')}<h3>Symptom history</h3></div>
      <div class="table-scroll"><table class="responsive-table"><thead><tr><th>Date</th><th>Symptoms</th><th>Severity</th><th>Action</th></tr></thead><tbody>${triageHtml}</tbody></table></div>
    </div>
    <div class="card"><div class="card-head">${iconBadge('teal', 'clipboard')}<h3>Visit notes</h3></div>${notesHtml}</div>
  `;
  loadDoctorAppointments();
}

async function loadDoctorAppointments() {
  const target = document.getElementById('doctorAppointments');
  if (!target) return;
  try {
    const visits = await api('GET', '/doctor/appointments');
    target.innerHTML = visits.length ? visits.map(visit => {
      const nextAction = ({ requested: 'confirm', confirmed: 'check_in', checked_in: 'call', called: 'complete' })[visit.status];
      const actionLabel = ({ confirm: 'Confirm time', check_in: 'Check in', call: 'Call patient', complete: 'Complete visit' })[nextAction];
      return `<div class="doctor-appointment-row"><div class="doctor-appointment-main">
        <strong>${doctorEscapeHtml(visit.patient_name)}</strong>
        <span>${new Date(`${visit.visit_date}T12:00:00`).toLocaleDateString([], {weekday:'short',month:'short',day:'numeric'})} · ${visit.scheduled_at ? new Date(visit.scheduled_at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}) : `${doctorEscapeHtml(visit.time_preference)} requested`}</span>
        ${visit.token_number ? `<span class="doctor-token">Token ${visit.token_number}</span>` : ''}
        <span class="badge ${visit.status === 'confirmed' || visit.status === 'checked_in' || visit.status === 'called' ? 'verified' : 'review'}">${doctorEscapeHtml(visit.status.replace(/_/g, ' '))}</span>
      </div>${visit.status === 'requested' ? `<label class="doctor-confirm-time">Confirm time<input type="datetime-local" id="visitSlot-${visit.id}" value="${visit.visit_date}T09:00"></label>` : ''}
        ${nextAction ? `<button class="primary small" data-visit-id="${Number(visit.id)}" data-visit-action="${nextAction}">${actionLabel}</button>` : ''}
        ${visit.status !== 'completed' ? `<button class="ghost small" data-visit-id="${Number(visit.id)}" data-visit-action="cancel">Cancel</button>` : ''}
      </div>`;
    }).join('') : '<div class="empty">No visit requests yet.</div>';
    target.querySelectorAll('[data-visit-action]').forEach(button => button.addEventListener('click', async () => {
      const body = { action: button.dataset.visitAction };
      if (body.action === 'confirm') body.scheduled_at = target.querySelector(`#visitSlot-${Number(button.dataset.visitId)}`).value;
      button.disabled = true;
      try { await api('PATCH', `/doctor/appointments/${Number(button.dataset.visitId)}`, body); await loadDoctorAppointments(); }
      catch (error) { button.disabled = false; alert(error.message || 'Could not update this visit.'); }
    }));
  } catch (error) {
    target.innerHTML = `<div class="empty">${doctorEscapeHtml(error.message || 'Visit requests are unavailable.')}</div>`;
  }
}

function renderNotesTab() {
  const r = state.currentReport;
  const view = document.getElementById('view-notes');
  const notesHtml = r.doctor_caregiver_notes.map(n => `
    <div style="padding:6px 0;border-bottom:1px solid var(--line);font-size:13px;">
      <span style="color:var(--ink-soft);">${new Date(n.at).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})}</span>
      <div>${n.note}</div>
    </div>
  `).join('') || '<div class="empty">No notes yet.</div>';

  view.innerHTML = `
    <h2 style="margin-top:26px;">Notes</h2>
    <div class="card">
      <div class="card-head">${iconBadge('teal', 'clipboard')}<h3>Clinical notes</h3></div>
      ${notesHtml}
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;">
        <input type="text" id="noteText" placeholder="Add a consultation / follow-up note..." style="flex:1 1 200px;min-width:0;">
        <button class="primary small" id="addNoteBtn">Add</button>
      </div>
    </div>
  `;

  document.getElementById('addNoteBtn').addEventListener('click', async () => {
    const note = document.getElementById('noteText').value.trim();
    if (!note) return;
    await api('POST', `/patients/${state.patientId}/notes`, { note });
    await loadPatientDetail();
  });
}

async function submitCorrection(medicineId) {
  const field = document.getElementById(`correctField-${medicineId}`).value;
  const corrected_value = document.getElementById(`correctValue-${medicineId}`).value.trim();
  const reason = document.getElementById(`correctReason-${medicineId}`).value.trim();
  const status = document.getElementById(`correctStatus-${medicineId}`);
  if (!corrected_value || !reason) {
    status.style.color = 'var(--alarm)';
    status.textContent = 'Both a corrected value and a reason are required — corrections are never silent.';
    return;
  }
  try {
    const result = await api('POST', `/doctor/medicines/${medicineId}/correction`, { field, corrected_value, reason });
    status.style.color = 'var(--teal-dark)';
    status.textContent = `Saved. ${result.warning || ''}`;
    for (const key of Object.keys(state.prescriptionDoseHistory)) {
      if (key.endsWith(`:${medicineId}`)) delete state.prescriptionDoseHistory[key];
    }
    setTimeout(loadPatientDetail, 800);
  } catch (e) {
    status.style.color = 'var(--alarm)';
    status.textContent = e.message;
  }
}

async function loadTemplates() {
  state.templates = await api('GET', '/doctor/templates');
}

(async function boot() {
  if (!currentUser) return;
  renderSessionChip();
  applyA11yMode();
  injectNavIcons();
  await loadTemplates();
  await loadPatients();
})();
