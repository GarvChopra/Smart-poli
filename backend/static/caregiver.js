// SmartPoli — caregiver dashboard (Part 2 of the brief).
//
// Every request here goes through auth.js's apiFetch, which attaches the
// caregiver's real bearer token. The backend (caregiver_router.py) is what
// actually enforces that this caregiver only ever sees patients they hold
// an ACTIVE CaregiverLink for — this file has no access logic of its own,
// it just renders what the server was willing to return.

const currentUser = requireRole('caregiver');
const api = apiFetch;

const state = { patients: [], patientId: null };

function renderSessionChip() {
  const chip = document.getElementById('sessionChip');
  if (!chip || !currentUser) return;
  chip.innerHTML = `
    <span class="who">${currentUser.name}</span>
    <span class="role-tag">Caregiver</span>
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
    const result = await api('POST', '/caregiver/link/redeem', { code });
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
  state.patients = await api('GET', '/caregiver/patients');
  const picker = document.getElementById('patientPicker');
  if (!state.patients.length) {
    picker.innerHTML = '<div class="empty">No linked patients yet — use "+ Link a patient" above.</div>';
    document.getElementById('overview').innerHTML = '';
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
  renderOverview();
}

async function renderOverview() {
  const view = document.getElementById('overview');
  view.innerHTML = '<div class="empty">Loading...</div>';
  const o = await api('GET', `/caregiver/patients/${state.patientId}/overview`);

  const nudgesHtml = (o.nudges || []).map(n => `<div class="nudge-banner ${n.level}">${n.text}</div>`).join('');
  const alertsHtml = o.alerts.map(a => `<div class="alert-item">${a}</div>`).join('') || '<div class="empty">Nothing needs attention.</div>';

  const upcomingHtml = o.upcoming_doses.map(d => `
    <div class="dose-row">
      <div class="time">${new Date(d.scheduled_at).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})}</div>
      <div style="flex:1;padding:0 10px;">${d.medicine_name}</div>
      <span class="badge ${d.state}">${d.state}</span>
    </div>
  `).join('') || '<div class="empty">Nothing scheduled.</div>';

  const perMedHtml = o.per_medicine.map(m => `
    <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px;font-size:13px;padding:6px 0;border-top:1px solid var(--line);">
      <span>${m.name}</span>
      <span style="color:var(--ink-soft);">${m.adherence_percent === null ? '—' : m.adherence_percent + '%'} · ${m.taken} taken / ${m.missed} missed</span>
    </div>
  `).join('') || '<div class="empty">No medicines yet.</div>';

  const symptomsHtml = o.recent_symptom_checks.map(c => `
    <div class="dose-row">
      <div class="time">${new Date(c.created_at).toLocaleDateString()}</div>
      <div style="flex:1;padding:0 10px;">${c.action}</div>
      <span class="badge ${c.severity.toLowerCase()}">${c.severity}</span>
    </div>
  `).join('') || '<div class="empty">No symptom checks recorded.</div>';

  const ec = o.emergency_card;
  const emergencyUrl = `${window.location.origin}/emergency/${state.patientId}`;

  view.innerHTML = `
    <h2 style="margin-top:26px;">${o.patient.name}'s overview</h2>
    ${nudgesHtml}

    <div class="card">
      <div class="card-head">${iconBadge('amber', 'alertCircle')}<h3>Alerts</h3></div>
      ${alertsHtml}
    </div>

    <div class="stat-row" style="margin-bottom:16px;">
      <div class="stat">${iconBadge('teal', 'chartBar')}<div><div class="num">${o.adherence.adherence_percent ?? '—'}${o.adherence.adherence_percent !== null ? '%' : ''}</div><div class="label">Adherence</div></div></div>
      <div class="stat">${iconBadge('blue', 'pill')}<div><div class="num">${o.adherence.taken}</div><div class="label">Taken</div></div></div>
      <div class="stat">${iconBadge('alarm', 'alertCircle')}<div><div class="num">${o.adherence.missed}</div><div class="label">Missed</div></div></div>
      <div class="stat">${iconBadge('blue', 'clock')}<div><div class="num">${o.adherence.skipped}</div><div class="label">Skipped</div></div></div>
    </div>

    <div class="card">
      <div class="card-head">${iconBadge('teal', 'chartBar')}<h3>Per-medicine adherence</h3></div>
      ${perMedHtml}
    </div>

    <div class="card">
      <div class="card-head">${iconBadge('blue', 'clock')}<h3>Upcoming doses</h3></div>
      ${upcomingHtml}
    </div>

    <div class="card">
      <div class="card-head">${iconBadge('teal', 'stethoscope')}<h3>Recent symptom checks</h3></div>
      ${symptomsHtml}
    </div>

    <div class="card">
      <div class="card-head">${iconBadge('alarm', 'siren')}<h3>Emergency information</h3></div>
      ${ec.has_emergency_triage_history ? '<div class="alert-item">Has a history of an EMERGENCY-graded symptom check.</div>' : ''}
      <div><strong>Allergies:</strong> ${ec.patient.allergies || 'none recorded'}</div>
      <div><strong>Emergency contact:</strong> ${ec.patient.emergency_contact || 'none recorded'}</div>
      <div style="margin-top:10px;"><a href="${emergencyUrl}" target="_blank">Open full emergency card →</a></div>
    </div>
  `;
}

(async function boot() {
  if (!currentUser) return;
  renderSessionChip();
  applyA11yMode();
  await loadPatients();
})();
