// SmartPoli — doctor dashboard (Parts 1 and 7 of the brief).
//
// Same pattern as caregiver.js: every request carries the doctor's real
// bearer token, and doctor_router.py is what actually enforces that this
// account only ever reaches a patient it holds an ACTIVE DoctorLink for.
// A correction here is never silent — see submitCorrection() below, which
// always shows the server's before/after + who + why back to the doctor
// immediately after saving it.

const currentUser = requireRole('doctor');
const api = apiFetch;

const state = { patients: [], patientId: null };

const CORRECTABLE_FIELDS = ['name', 'dose_amount', 'dose_unit', 'schedule_code', 'food', 'duration_days'];

function badgeClass(status) { return (status || '').toLowerCase(); }

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
    selectPatient(result.patient.id);
  } catch (e) {
    status.style.color = 'var(--alarm)';
    status.textContent = e.message;
  }
});

async function loadPatients() {
  state.patients = await api('GET', '/doctor/patients');
  const picker = document.getElementById('patientPicker');
  if (!state.patients.length) {
    picker.innerHTML = '<div class="empty">No linked patients yet — use "+ Link a patient" above.</div>';
    document.getElementById('detail').innerHTML = '';
    return;
  }
  picker.innerHTML = state.patients.map(p => `
    <button type="button" class="patient-picker-card ${p.id === state.patientId ? 'active' : ''}" data-pid="${p.id}">
      <div class="name">${p.name}</div>
      <div class="meta">${p.age ? p.age + ' yrs' : ''}${p.sex ? ', ' + p.sex : ''}</div>
    </button>
  `).join('');
  picker.querySelectorAll('[data-pid]').forEach(btn => {
    btn.addEventListener('click', () => selectPatient(Number(btn.dataset.pid)));
  });
  if (!state.patientId) selectPatient(state.patients[0].id);
}

function selectPatient(id) {
  state.patientId = id;
  document.querySelectorAll('#patientPicker [data-pid]').forEach(btn => {
    btn.classList.toggle('active', Number(btn.dataset.pid) === id);
  });
  renderDetail();
}

async function renderDetail() {
  const view = document.getElementById('detail');
  view.innerHTML = '<div class="empty">Loading...</div>';
  const r = await api('GET', `/doctor/patients/${state.patientId}`);

  const medsHtml = r.prescriptions.flatMap(p => p.medicines).map(m => `
    <div class="medicine-line ${m.status === 'needs_confirmation' ? 'needs_confirmation' : ''}" data-med-id="${m.id}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px;">
        <div>
          <strong>${m.name || '(name not read)'}</strong> ${m.dose_amount ? m.dose_amount + (m.dose_unit || '') : ''}
          ${m.schedule_code ? ' — ' + m.schedule_code : ''}
          <div class="raw">"${m.raw_text}"</div>
        </div>
        <span class="badge ${badgeClass(m.status)}">${m.status.replace('_', ' ')}</span>
      </div>
      <div style="margin-top:6px;">
        ${Object.entries(m.field_confidence).filter(([k]) => k !== 'source').map(([k, v]) => `
          <div class="confidence-row">
            <span class="field-name">${k}</span>
            <span class="confidence-track"><span class="confidence-fill ${badgeClass(m.status)}" style="width:${Math.round(v * 100)}%"></span></span>
            <span class="confidence-pct">${Math.round(v * 100)}%</span>
          </div>
        `).join('')}
      </div>
      <button class="ghost small" data-correct-id="${m.id}" style="margin-top:8px;">Add a correction</button>
      <div class="correction-form" id="correction-${m.id}" style="display:none;">
        <select id="correctField-${m.id}">
          ${CORRECTABLE_FIELDS.map(f => `<option value="${f}">${f.replace('_', ' ')}</option>`).join('')}
        </select>
        <input type="text" id="correctValue-${m.id}" placeholder="Corrected value">
        <input type="text" id="correctReason-${m.id}" placeholder="Reason (required — kept in the audit trail)">
        <button class="primary small" data-submit-correction="${m.id}">Save correction</button>
        <div id="correctStatus-${m.id}" style="font-size:12px;margin-top:6px;"></div>
      </div>
      <div id="correctionHistory-${m.id}" style="font-size:12px;color:var(--ink-soft);margin-top:6px;"></div>
    </div>
  `).join('') || '<div class="empty">No prescriptions on file.</div>';

  const missedHtml = r.missed_doses.map(d => `
    <tr><td data-label="When">${new Date(d.scheduled_at).toLocaleString()}</td><td data-label="Medicine">${d.medicine_name}</td></tr>
  `).join('') || '<tr><td colspan="2" class="empty">None</td></tr>';

  const triageHtml = r.symptom_history.map(c => `
    <tr>
      <td data-label="Date">${new Date(c.created_at).toLocaleDateString()}</td>
      <td data-label="Symptoms">${c.symptoms.join(', ')}</td>
      <td data-label="Severity"><span class="badge ${badgeClass(c.severity)}">${c.severity}</span></td>
      <td data-label="Action">${c.action}</td>
    </tr>
  `).join('') || '<tr><td colspan="4" class="empty">None</td></tr>';

  const alertsHtml = r.alerts.map(a => `<div class="alert-item">${a}</div>`).join('') || '<div class="empty">No alerts.</div>';

  const notesHtml = r.doctor_caregiver_notes.map(n => `
    <div style="padding:6px 0;border-bottom:1px solid var(--line);font-size:13px;">
      <span style="color:var(--ink-soft);">${new Date(n.at).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})}</span>
      <div>${n.note}</div>
    </div>
  `).join('') || '<div class="empty">No notes yet.</div>';

  view.innerHTML = `
    <h2 style="margin-top:26px;">${r.patient.name}</h2>
    <div style="color:var(--ink-soft);font-size:13px;margin-bottom:14px;">
      ${r.patient.age ? r.patient.age + ' yrs' : ''}${r.patient.sex ? ', ' + r.patient.sex : ''}
    </div>

    <div class="card">
      <div class="card-head">${iconBadge('amber', 'alertCircle')}<h3>Alerts</h3></div>
      ${alertsHtml}
    </div>

    <div class="card">
      <div class="card-head">${iconBadge('teal', 'fileText')}<h3>Prescription &amp; extraction review</h3></div>
      ${medsHtml}
    </div>

    <div class="stat-row" style="margin-bottom:16px;">
      <div class="stat">${iconBadge('teal', 'chartBar')}<div><div class="num">${r.adherence.adherence_percent ?? '—'}${r.adherence.adherence_percent !== null ? '%' : ''}</div><div class="label">Adherence</div></div></div>
      <div class="stat">${iconBadge('blue', 'pill')}<div><div class="num">${r.adherence.taken}</div><div class="label">Taken</div></div></div>
      <div class="stat">${iconBadge('alarm', 'alertCircle')}<div><div class="num">${r.adherence.missed}</div><div class="label">Missed</div></div></div>
    </div>

    <div class="card">
      <div class="card-head">${iconBadge('alarm', 'clock')}<h3>Missed doses</h3></div>
      <div class="table-scroll"><table class="responsive-table"><thead><tr><th>When</th><th>Medicine</th></tr></thead><tbody>${missedHtml}</tbody></table></div>
    </div>

    <div class="card">
      <div class="card-head">${iconBadge('teal', 'stethoscope')}<h3>Symptoms &amp; triage history</h3></div>
      <div class="table-scroll"><table class="responsive-table"><thead><tr><th>Date</th><th>Symptoms</th><th>Severity</th><th>Action</th></tr></thead><tbody>${triageHtml}</tbody></table></div>
    </div>

    <div class="card">
      <div class="card-head">${iconBadge('teal', 'fileText')}<h3>Clinical notes</h3></div>
      ${notesHtml}
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;">
        <input type="text" id="noteText" placeholder="Add a consultation / follow-up note..." style="flex:1 1 200px;min-width:0;">
        <button class="primary small" id="addNoteBtn">Add</button>
      </div>
    </div>
  `;

  view.querySelectorAll('[data-correct-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.correctId;
      const form = document.getElementById(`correction-${id}`);
      form.style.display = form.style.display === 'none' ? 'block' : 'none';
    });
  });

  view.querySelectorAll('[data-submit-correction]').forEach(btn => {
    btn.addEventListener('click', () => submitCorrection(btn.dataset.submitCorrection));
  });

  document.getElementById('addNoteBtn').addEventListener('click', async () => {
    const note = document.getElementById('noteText').value.trim();
    if (!note) return;
    await api('POST', `/patients/${state.patientId}/notes`, { note });
    renderDetail();
  });

  // Correction history is fetched lazily per medicine, only if any exist,
  // so a fresh prescription with no corrections doesn't cost extra calls.
  r.prescriptions.flatMap(p => p.medicines).forEach(async (m) => {
    try {
      const corrections = await api('GET', `/doctor/medicines/${m.id}/corrections`);
      if (!corrections.length) return;
      const el = document.getElementById(`correctionHistory-${m.id}`);
      if (el) {
        el.innerHTML = 'Corrections: ' + corrections.map(c =>
          `${c.field} "${c.original_value}" → "${c.corrected_value}"`
        ).join('; ');
      }
    } catch (e) { /* non-critical history fetch */ }
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
    setTimeout(renderDetail, 800);
  } catch (e) {
    status.style.color = 'var(--alarm)';
    status.textContent = e.message;
  }
}

(async function boot() {
  if (!currentUser) return;
  renderSessionChip();
  applyA11yMode();
  await loadPatients();
})();
