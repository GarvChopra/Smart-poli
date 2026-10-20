// SmartPoli - add a medicine: scan the strip or type it in, then set it like an alarm (how often, what time, which days,
// with food, for how long). Shared by the patient app and the caregiver page, so a caregiver adding a medicine for the
// patient goes through exactly the same questions. The page sets who it is for and what happens after saving:
//
//   MedWizard.patientId = () => <patient id>;   MedWizard.onAdded = (med, out, when, sos) => { ... };
//
// Also the small helpers both pages use: el(), escHtml(), capName(), showSafetyModal(), fmtClock().

const MedWizard = { patientId: () => null, onAdded: () => {} };

function el(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function escHtml(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function capName(v) {
  return String(v == null ? '' : v).replace(/(^|[\s+/-])([a-z])/g, (m, pre, ch) => pre + ch.toUpperCase());
}

function showSafetyModal(innerHtml) {
  document.querySelectorAll('.safety-modal-overlay').forEach((n) => n.remove());
  const overlay = el(`<div class="safety-modal-overlay" role="dialog" aria-modal="true"><div class="safety-modal">${innerHtml}</div></div>`);
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  return overlay;
}

function fmtClock(hhmm) {
  const [h, mi] = hhmm.split(':').map(Number);
  return `${((h + 11) % 12) + 1}:${String(mi).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
}

// ---------------------------------------------------------------- scan a medicine: photo of the strip -> "is this it?" -> how you take it
//
//   camera -> "Reading..." -> "Is this your medicine?" (name, strength, expiry) -> the same timing questions as Enter manually.
// The pack only says WHAT the medicine is; how often, when, with food and for how long always come from the person.

function openMedicineCamera() {
  let input = document.getElementById('scanInput');
  if (!input) {
    input = el('<input type="file" id="scanInput" accept="image/*" capture="environment" hidden>');
    document.body.appendChild(input);
    input.addEventListener('change', async () => {
      if (!input.files.length) return;
      const file = input.files[0];
      input.value = '';
      await scanMedicinePhoto(file);
    });
  }
  input.click();
}

async function scanMedicinePhoto(file) {
  const loading = showSafetyModal(`
    <h3>Reading your medicine…</h3>
    <div class="upload-status-banner loading" style="margin-top:10px;"><span class="upload-spinner"></span><span>This takes a few seconds.</span></div>`);
  try {
    const form = new FormData();
    form.append('patient_id', MedWizard.patientId());
    form.append('file', file);
    const auth = getAuth();
    const res = await fetch('/medicines/scan', {
      method: 'POST', body: form,
      headers: auth && auth.token ? { Authorization: `Bearer ${auth.token}` } : {},
    });
    const body = await res.json().catch(() => ({}));
    loading.remove();
    const cand = res.ok && body.candidates && body.candidates[0];
    if (!cand) { scanFailedPopup(body.detail); return; }
    medicineWizard({ step: 'confirm', cand, others: body.candidates.slice(1), form: body.form_guess, expiry: body.expiry });
  } catch (e) {
    loading.remove();
    scanFailedPopup();
  }
}

function scanFailedPopup(detail) {
  const m = showSafetyModal(`
    <h3>Couldn’t read that medicine</h3>
    <div class="sm-headline" style="font-weight:500;">${escHtml(typeof detail === 'string' ? detail : 'We couldn’t read the medicine name from that photo.')}</div>
    <ul class="scan-tips">
      <li>Photograph the side with the name in big letters</li>
      <li>Tilt the strip so the shiny foil doesn’t reflect light</li>
      <li>Fill the frame and hold still</li>
    </ul>
    <div class="sm-actions">
      <button class="primary small" id="sfRetry">Try again</button>
      <button class="ghost small" id="sfManual">Enter manually</button>
    </div>`);
  m.querySelector('#sfManual').addEventListener('click', () => { m.remove(); medicineWizard({ step: 'details' }); });
  m.querySelector('#sfRetry').addEventListener('click', () => { m.remove(); openMedicineCamera(); });
}

function expiryHtml(expiry) {
  if (!expiry) return '';
  const now = new Date();
  const expired = expiry.year < now.getFullYear() || (expiry.year === now.getFullYear() && expiry.month < now.getMonth() + 1);
  return expired
    ? `<div class="scan-expired">⚠ This pack may have expired — we read the date as ${escHtml(expiry.label)}. Check the date printed on it; don’t take expired medicine.</div>`
    : `<div class="wz-meta">Expires ${escHtml(expiry.label)}</div>`;
}

let medicineNames = null;
async function medicineNameList() {
  if (!medicineNames) medicineNames = await apiFetch('GET', '/medicines/names').then((r) => r.names).catch(() => []);
  return medicineNames;
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
  if (ctx.step === 'confirm') {
    const c = ctx.cand;
    const formLabel = (MED_FORMS.find(([v]) => v === (c.form || ctx.form)) || [])[1] || '';
    const m = showSafetyModal(`
      <h3>Is this your medicine?</h3>
      <div class="wz-card">
        <div class="wz-name">${escHtml(capName(c.name))}</div>
        <div class="wz-meta">${[c.strength, formLabel, c.generic && capName(c.generic)].filter(Boolean).map(escHtml).join(' · ')}</div>
        ${expiryHtml(ctx.expiry)}
      </div>
      ${c.known ? '' : '<div class="wz-msg">We don’t recognise this name. Check the spelling against the strip, and tap Change if it’s wrong.</div>'}
      ${(ctx.others || []).length ? `<div class="wz-label">Or is it…</div>
        <div class="wz-chips">${ctx.others.map((o, i) => `<button type="button" class="wz-chip" data-other="${i}">${escHtml(capName(o.name))}${o.strength ? ' ' + escHtml(o.strength) : ''}</button>`).join('')}</div>` : ''}
      <div class="sm-actions">
        <button class="primary small" id="wzYes">Yes, this is it</button>
        <button class="ghost small" id="wzChange">Change</button>
        <button class="ghost small" id="wzRescan">Scan again</button>
      </div>`);
    const picked = { name: capName(c.name), strength: c.strength || '', form: c.form || ctx.form || 'tab' };
    m.querySelector('#wzYes').addEventListener('click', () => { m.remove(); medicineWizard({ step: 'schedule', med: picked }); });
    m.querySelector('#wzChange').addEventListener('click', () => { m.remove(); medicineWizard({ step: 'details', med: picked }); });
    m.querySelector('#wzRescan').addEventListener('click', () => { m.remove(); openMedicineCamera(); });
    m.querySelectorAll('[data-other]').forEach((b) => b.addEventListener('click', () => {
      const o = ctx.others[Number(b.dataset.other)];
      m.remove();
      medicineWizard({ ...ctx, cand: o, others: [c, ...ctx.others.filter((x) => x !== o)] });
    }));
    return;
  }

  if (ctx.step === 'details') {
    const m = showSafetyModal(`
      <h3>Add a medicine</h3>
      <label for="wzName">Medicine name</label>
      <input id="wzName" type="text" maxlength="80" placeholder="e.g. Dolo 650" list="wzNameList" autocomplete="off" value="${escHtml(med.name || '')}">
      <datalist id="wzNameList"></datalist>
      <div class="fp-row">
        <div><label for="wzStrength">Strength <span class="reg-meta">(optional)</span></label>
          <input id="wzStrength" type="text" maxlength="30" placeholder="e.g. 500mg" value="${escHtml(med.strength || '')}"></div>
        <div><label for="wzForm">Form</label>
          <select id="wzForm">${MED_FORMS.map(([v, l]) => `<option value="${v}" ${(med.form || 'tab') === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
      </div>
      <div id="wzMsg" class="wz-msg"></div>
      <div class="sm-actions">
        <button class="primary small" id="wzNext">Next</button>
        <button class="ghost small" id="wzScan">📷 Scan instead</button>
        <button class="ghost small" id="wzCancel">Cancel</button>
      </div>`);
    m.querySelector('#wzName').focus();
    medicineNameList().then((names) => {
      const list = m.querySelector('#wzNameList');
      if (list) list.innerHTML = names.map((n) => `<option value="${escHtml(n)}">`).join('');
    });
    m.querySelector('#wzScan').addEventListener('click', () => { m.remove(); openMedicineCamera(); });
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
  apiFetch('GET', `/patients/${MedWizard.patientId()}/routine`).then((r) => { if (r && r.times) routineTimes = { ...DEFAULT_TIMES, ...r.times }; }).catch(() => {});

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
      const out = await apiFetch('POST', '/medicines/manual', {
        patient_id: MedWizard.patientId(), name: med.name, strength: med.strength || null, form: med.form || 'tab',
        as_needed: sos, times, weekdays, food: sos ? 'any' : chosen('wzFood').v || 'any',
        duration_days: sos || ongoing ? null : Math.round(len),
      });
      m.remove();
      const when = sos ? 'Only when needed' : times.map(fmtClock).join(', ') + (weekdays && weekdays.length < 7 ? ' · ' + weekdays.map((d) => WEEKDAYS[d]).join(' ') : ' · every day');
      MedWizard.onAdded(med, out, when, sos);
    } catch (e) { btn.disabled = false; msg.textContent = e.message || 'Could not add it.'; }
  });
}
