// ---------------------------------------------------------------- 3D digital health card
//
// The patient's emergency card as a physical-feeling ID card (layout follows
// the approved reference, image.png): front = identity, blood group,
// allergy/emergency indicators, QR, teal wave; back = contacts, care team,
// conditions, medicines, instructions, last updated, verified. Front shows
// by default; tap, drag/swipe or Enter flips it. Actions under it:
// Share Card · Download (wallet PDF) · Print · Edit · Revoke QR.
// Everything patient-entered is escaped before it touches innerHTML.

const HC_ICONS = {
  share: '<circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="m8.2 10.8 7.6-4.4M8.2 13.2l7.6 4.4"/>',
  download: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 19h16"/>',
  print: '<path d="M7 9V3h10v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M7 14h10v7H7z"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="m13.5 6.5 4 4"/>',
  trash: '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/><path d="M10 11v6M14 11v6"/>',
  shield: '<path d="M12 2.5 4.5 5.5v6c0 5 3.3 8.4 7.5 10 4.2-1.6 7.5-5 7.5-10v-6L12 2.5z" fill="currentColor" stroke="none"/><path d="m8.8 12 2.2 2.2 4.3-4.4" stroke="#fff"/>',
  eyeOff: '<path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c5 0 9 4.5 10 7-.4 1-1.2 2.3-2.4 3.5M6.2 6.2C4.2 7.6 2.7 9.7 2 12c1 2.5 5 7 10 7 1.6 0 3-.4 4.3-1"/>',
  phone: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.5-3.6 3.2-5.5 6.5-5.5s6 1.9 6.5 5.5"/><path d="M15.5 4.8a3.5 3.5 0 0 1 0 6.4M18 14.8c2 .7 3.2 2.5 3.5 5.2"/>',
  doctor: '<circle cx="12" cy="7" r="3.8"/><path d="M4.5 21c.4-4 3.4-6.5 7.5-6.5s7.1 2.5 7.5 6.5"/><path d="M9 15v3.5a1.5 1.5 0 0 0 3 0"/><circle cx="15.5" cy="18.5" r="1.3"/>',
  clipboard: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V2.8h6V4"/><path d="M12 9v6M9 12h6"/>',
  pill: '<rect x="2.8" y="8.5" width="18.4" height="7" rx="3.5" transform="rotate(-45 12 12)"/><path d="m8.5 8.5 7 7"/>',
  fileText: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/>',
  refresh: '<path d="M4 12a8 8 0 0 1 13.7-5.6L20 8.5"/><path d="M20 4v4.5h-4.5"/><path d="M20 12a8 8 0 0 1-13.7 5.6L4 15.5"/><path d="M4 20v-4.5h4.5"/>',
  lock: '<rect x="5.5" y="10.5" width="13" height="10" rx="2" fill="currentColor" stroke="none"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
  drop: '<path d="M12 3.5C9 8 6.5 10.8 6.5 14a5.5 5.5 0 0 0 11 0c0-3.2-2.5-6-5.5-10.5z" fill="currentColor" stroke="none"/>',
  alert: '<path d="M12 3.5 2.5 20h19L12 3.5z" fill="currentColor" stroke="none"/><path d="M12 10v4.2" stroke="#fff" stroke-width="2.2"/><circle cx="12" cy="17.1" r="1.2" fill="#fff" stroke="none"/>',
  heartPulse: '<path d="M12 20.5S3.5 15.3 3.5 9.2A4.7 4.7 0 0 1 12 6.4a4.7 4.7 0 0 1 8.5 2.8c0 6.1-8.5 11.3-8.5 11.3z"/><path d="M5 12h3.5l1.5-2.5 2.5 5 1.8-3.5H19"/>',
};
const hcIcon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${HC_ICONS[name]}</svg>`;

const hcEsc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const HC_SHARE_LABELS = {
  photo: 'Photo', blood_group: 'Blood group', allergies: 'Allergies', conditions: 'Medical conditions',
  medicines: 'Current medications', emergency_contact: 'Emergency contact', caregiver: 'Caregiver',
  doctor: 'Primary doctor', instructions: 'Emergency instructions',
};

const HC_BLOOD_GROUPS = ['', 'A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'];

// Bottom-of-front teal wave, as in the reference.
const HC_WAVE = `<svg class="hc-wave" viewBox="0 0 1000 420" preserveAspectRatio="none" aria-hidden="true">
  <defs>
    <linearGradient id="hcWaveMain" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#0E7C66"/><stop offset="0.5" stop-color="#23A286"/><stop offset="1" stop-color="#8FD9C6" stop-opacity="0.85"/>
    </linearGradient>
    <linearGradient id="hcWaveSoft" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#9ADBC9"/><stop offset="1" stop-color="#E4F6F0" stop-opacity="0.6"/>
    </linearGradient>
  </defs>
  <path d="M0 40 C150 50 260 160 440 205 C620 250 800 262 1000 322 V420 H0 Z" fill="url(#hcWaveSoft)" opacity="0.75"/>
  <path d="M0 118 C160 130 260 228 460 262 C640 294 820 302 1000 362 V420 H0 Z" fill="url(#hcWaveMain)"/>
  <path d="M0 116 C160 128 260 226 460 260 C640 292 820 300 1000 360" fill="none" stroke="#fff" stroke-opacity="0.45" stroke-width="3"/>
</svg>`;

// Thin rule with an ECG blip under the back's wordmark.
const HC_RULE = `<svg class="hc-rule" viewBox="0 0 1000 40" preserveAspectRatio="none" aria-hidden="true">
  <path d="M0 30 H560 l10 -6 l8 16 l10 -38 l10 34 l8 -6 H1000"/></svg>`;

async function hcAuthedBlobUrl(path) {
  const auth = getAuth();
  const res = await fetch(path, { headers: auth && auth.token ? { Authorization: `Bearer ${auth.token}` } : {} });
  if (!res.ok) throw new Error(`${res.status}`);
  return URL.createObjectURL(await res.blob());
}

function hcInitials(name) {
  return (name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join('');
}

function hcFormatDate(iso) {
  const d = new Date(iso + 'Z');
  const date = d.toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' });
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return `${date} • ${time}`;
}

// "Anita Kumar (daughter), +91 98765 43210" -> name line + phone line
function hcSplitContact(text) {
  if (!text) return { name: '', phone: '' };
  const m = text.match(/(\+?\d[\d\s-]{6,}\d)\s*$/);
  if (!m) return { name: text, phone: '' };
  return { name: text.slice(0, m.index).replace(/[,\s–-]+$/, ''), phone: m[1] };
}

// free text -> short lines ("Hypertension, Type 2 diabetes" -> two lines)
function hcLines(text, max = 3) {
  return (text || '').split(/\n|,|;/).map(s => s.trim()).filter(Boolean).slice(0, max);
}

function hcFrontHtml(data) {
  const p = data.patient;
  const sex = p.sex === 'M' ? 'Male' : p.sex === 'F' ? 'Female' : p.sex;
  const details = [p.age ? `${p.age} years` : '', sex].filter(Boolean);
  return `
    ${HC_WAVE}
    <div class="hc-f-brand"><span class="hc-f-mark">+</span>SmartPoli</div>
    <div class="hc-f-kind">Emergency health ID</div>
    <div class="hc-f-avatar" data-hc-avatar>${hcEsc(hcInitials(p.name))}</div>
    <div class="hc-f-who">
      <div class="hc-f-name">${hcEsc(p.name)}</div>
      <div class="hc-f-details">${details.map(hcEsc).join('<span class="hc-f-sep">•</span>')}</div>
      <div class="hc-f-chips">
        ${p.blood_group ? `<span class="hc-chip">${hcIcon('drop')}${hcEsc(p.blood_group)}</span>` : ''}
        ${p.allergies ? `<span class="hc-chip">${hcIcon('alert')}Allergic to ${hcEsc(p.allergies)}</span>` : ''}
      </div>
      ${data.has_emergency_triage_history ? `<span class="hc-f-emergency">${hcIcon('alert')}Emergency history</span>` : ''}
    </div>
    <div class="hc-f-qr">
      <div class="hc-f-qr-frame"><img data-hc-qr alt="QR code for your public emergency page"></div>
      <div class="hc-f-qr-caption">Scan in an emergency</div>
    </div>
    <div class="hc-f-tagline">${hcIcon('heartPulse')}<span>My Health.<br>Always With Me.</span></div>`;
}

function hcBackHtml(data) {
  const p = data.patient;
  const prof = data.profile;
  const share = prof.share;
  const contact = hcSplitContact(p.emergency_contact);
  const meds = [...data.scheduled_medicines, ...data.as_needed_medicines]
    .map(m => [m.name, m.dose_amount ? `${m.dose_amount} ${m.dose_unit || ''}`.trim() : ''].filter(Boolean).join(' '));
  const medLines = meds.length > 3 ? meds.slice(0, 2).concat([`+${meds.length - 2} more`]) : meds;
  const hidden = (key) => share[key] ? '' : `<span class="hc-hidden" title="Not shown on the QR page">${hcIcon('eyeOff')}</span>`;
  const row = (key, icon, label, lines, strongFirst) => `
    <div class="hc-b-row">
      <span class="hc-b-icon">${hcIcon(icon)}</span>
      <div class="hc-b-text">
        <div class="hc-b-label">${label}${hidden(key)}</div>
        ${lines.length
          ? lines.map((l, i) => `<div class="${strongFirst && i === 0 ? 'hc-b-strong' : 'hc-b-line'}">${hcEsc(l)}</div>`).join('')
          : '<div class="hc-b-line hc-b-empty">Not added</div>'}
      </div>
    </div>`;
  return `
    <div class="hc-b-head"><span>SmartPoli</span>${HC_RULE}</div>
    <div class="hc-b-body">
      <div class="hc-b-col">
        ${row('emergency_contact', 'phone', 'Emergency Contact', [contact.name, contact.phone].filter(Boolean), true)}
        ${row('caregiver', 'users', 'Caregiver', data.care_team.caregivers.slice(0, 2), true)}
        ${row('doctor', 'doctor', 'Primary Doctor', data.care_team.doctors.slice(0, 2), true)}
      </div>
      <div class="hc-b-col">
        ${row('conditions', 'clipboard', 'Medical Conditions', hcLines(prof.conditions), false)}
        ${row('medicines', 'pill', 'Current Medications', medLines, false)}
        ${row('instructions', 'fileText', 'Emergency Instructions', hcLines(prof.instructions, 2), false)}
      </div>
    </div>
    <div class="hc-b-foot">
      <div class="hc-b-updated">${hcIcon('refresh')}<div><div class="hc-b-label">Last updated</div><div class="hc-b-date">${hcEsc(hcFormatDate(data.last_updated))}</div></div></div>
      <div class="hc-b-verified">${hcIcon('shield')}SmartPoli Verified</div>
      <span class="hc-b-lock" title="Only what you choose is shared">${hcIcon('lock')}</span>
    </div>`;
}

async function mountHealthCard(container, patientId) {
  container.innerHTML = `<div class="empty">Loading your card…</div>`;
  const data = await api('GET', `/patients/${patientId}/emergency-card`);
  const cardUrl = `${window.location.origin}${data.card_path}`;

  container.innerHTML = `
    <div class="hc-page">
      <header class="hc-head">
        <div>
          <div class="hc-eyebrow">SmartPoli</div>
          <h2 class="hc-title">${t('emergencyHeading')}</h2>
          <p class="hc-sub">Carry your emergency information as a card. Anyone who scans the QR sees only what you choose to share, and nothing else from your account.</p>
        </div>
        <div class="hc-active">${hcIcon('shield')}Your card is active</div>
      </header>

      <div class="hc-stage">
        <div class="hc-card" tabindex="0" role="button" aria-pressed="false"
             aria-label="Your emergency health card. Showing the front. Press Enter to flip.">
          <div class="hc-edge" style="--z:-1.5px"></div>
          <div class="hc-edge" style="--z:-0.5px"></div>
          <div class="hc-edge" style="--z:0.5px"></div>
          <div class="hc-face hc-front">${hcFrontHtml(data)}<div class="hc-sheen"></div></div>
          <div class="hc-face hc-back">${hcBackHtml(data)}<div class="hc-sheen"></div></div>
        </div>
      </div>

      <div class="hc-pager" role="tablist" aria-label="Card side">
        <button class="hc-pager-btn is-on" data-side="front" role="tab" aria-selected="true"><span></span>Front</button>
        <i aria-hidden="true"></i>
        <button class="hc-pager-btn" data-side="back" role="tab" aria-selected="false"><span></span>Back</button>
      </div>

      <div class="hc-actions" role="toolbar" aria-label="Card actions">
        <button class="hc-action" data-hc="share">${hcIcon('share')}<span>Share Card</span></button>
        <button class="hc-action" data-hc="download">${hcIcon('download')}<span>Download</span></button>
        <button class="hc-action" data-hc="print">${hcIcon('print')}<span>Print</span></button>
        <button class="hc-action" data-hc="edit">${hcIcon('edit')}<span>Edit</span></button>
        <button class="hc-action is-danger" data-hc="revoke">${hcIcon('trash')}<span>Revoke QR</span></button>
      </div>
      <div class="hc-panel" id="hcPanel" aria-live="polite"></div>

      <div class="hc-print-sheet" aria-hidden="true">
        <div class="hc-print-face hc-front">${hcFrontHtml(data)}</div>
        <div class="hc-print-face hc-back">${hcBackHtml(data)}</div>
      </div>
    </div>`;

  hcLoadImages(container, patientId, data);
  const card = container.querySelector('.hc-card');
  const showSide = hcWireRotation(card, (side) => {
    container.querySelectorAll('.hc-pager-btn').forEach(b => {
      const on = b.dataset.side === side;
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-selected', String(on));
    });
  });
  container.querySelector('.hc-pager').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-side]');
    if (btn) showSide(btn.dataset.side);
  });

  const panel = container.querySelector('#hcPanel');
  const rerender = () => mountHealthCard(container, patientId);
  container.querySelector('.hc-actions').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-hc]');
    if (!btn) return;
    const action = btn.dataset.hc;
    if (action === 'share') hcShare(panel, cardUrl, data.patient.name);
    if (action === 'download') hcDownload(panel, btn, patientId);
    if (action === 'print') hcPrint();
    if (action === 'edit') hcOpenEditor(panel, patientId, data, rerender);
    if (action === 'revoke') hcOpenRevoke(panel, patientId, rerender);
  });
}

function hcLoadImages(container, patientId, data) {
  hcAuthedBlobUrl(`/patients/${patientId}/emergency-card/qr.png`)
    .then(url => container.querySelectorAll('[data-hc-qr]').forEach(img => { img.src = url; }))
    .catch(() => {});
  if (data.profile.has_photo) {
    hcAuthedBlobUrl(`/patients/${patientId}/emergency-card/photo`)
      .then(url => container.querySelectorAll('[data-hc-avatar]').forEach(el => {
        el.innerHTML = `<img src="${url}" alt="">`;
      }))
      .catch(() => {});
  }
}

// ---- rotation: drag/swipe turns the card; release settles on the nearest
// face. Returns showSide('front' | 'back') for the pager.
function hcWireRotation(card, onSide) {
  const REST_X = 4, REST_Y = -6;  // the reference's slight resting perspective
  let base = 0;
  let angle = 0, tilt = REST_X;
  let drag = null;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const apply = () => {
    const rest = drag ? 0 : REST_Y;
    card.style.transform = `rotateX(${tilt}deg) rotateY(${angle + rest}deg)`;
    const turn = ((angle % 360) + 360) % 360;
    card.style.setProperty('--sheen', `${50 + Math.sin(turn * Math.PI / 180) * 45}%`);
    card.style.setProperty('--lean', `${Math.sin(turn * Math.PI / 180) * -14}px`);
  };
  const sideOf = (a) => ((((a % 360) + 360) % 360) === 180 ? 'back' : 'front');
  const settle = (to) => {
    base = to; angle = to; tilt = REST_X;
    card.classList.toggle('is-settling', !reduced);
    apply();
    const side = sideOf(to);
    card.setAttribute('aria-pressed', String(side === 'back'));
    card.setAttribute('aria-label', `Your emergency health card. Showing the ${side}. Press Enter to flip.`);
    onSide(side);
  };
  const flip = (dir = 1) => settle(base + 180 * dir);

  card.addEventListener('pointerdown', (e) => {
    if (e.button !== undefined && e.button !== 0) return;
    drag = { x: e.clientX, y: e.clientY, moved: false };
    card.classList.remove('is-settling');
    card.setPointerCapture(e.pointerId);
  });
  card.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) > 6 || Math.abs(dy) > 6) drag.moved = true;
    angle = base + dx * 0.6;
    tilt = Math.max(-12, Math.min(12, REST_X - dy * 0.12));
    apply();
  });
  const end = () => {
    if (!drag) return;
    const wasTap = !drag.moved;
    drag = null;
    if (wasTap) return flip(1);
    settle(Math.round(angle / 180) * 180);
  };
  card.addEventListener('pointerup', end);
  card.addEventListener('pointercancel', end);
  card.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowRight') { e.preventDefault(); flip(1); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); flip(-1); }
  });
  settle(0);
  return (side) => { if (sideOf(base) !== side) flip(1); };
}

// ---- actions
function hcNotice(panel, text, tone = '') {
  panel.innerHTML = `<div class="hc-notice ${tone}">${hcEsc(text)}</div>`;
}

async function hcShare(panel, cardUrl, name) {
  const shareData = { title: 'SmartPoli emergency card', text: `Emergency health card for ${name}`, url: cardUrl };
  if (navigator.share) {
    try { await navigator.share(shareData); return; } catch (err) { if (err.name === 'AbortError') return; }
  }
  try {
    await navigator.clipboard.writeText(cardUrl);
    hcNotice(panel, 'Card link copied. Anyone with the link sees only what you chose to share.');
  } catch {
    panel.innerHTML = `<div class="hc-notice">Copy your card link: <input class="hc-link-input" readonly value="${hcEsc(cardUrl)}"></div>`;
    panel.querySelector('input').select();
  }
}

async function hcDownload(panel, btn, patientId) {
  btn.disabled = true;
  try {
    const url = await hcAuthedBlobUrl(`/patients/${patientId}/emergency-card/card.pdf`);
    const a = document.createElement('a');
    a.href = url; a.download = 'smartpoli-health-card.pdf';
    document.body.appendChild(a); a.click(); a.remove();
    hcNotice(panel, 'Card downloaded as a PDF. Print it at 100% scale to get wallet size.');
  } catch {
    hcNotice(panel, 'Could not download the card. Check your connection and try again.', 'is-error');
  } finally {
    btn.disabled = false;
  }
}

function hcPrint() {
  document.body.classList.add('print-health-card');
  const done = () => { document.body.classList.remove('print-health-card'); window.removeEventListener('afterprint', done); };
  window.addEventListener('afterprint', done);
  window.print();
}

function hcOpenRevoke(panel, patientId, rerender) {
  panel.innerHTML = `
    <div class="hc-sheet">
      <h3>Revoke this QR code?</h3>
      <p>The current QR, your shared link and any printed card stop working straight away. You get a new QR to use instead.</p>
      <div class="hc-sheet-actions">
        <button class="hc-danger-btn" id="hcRevokeYes">Revoke and make a new QR</button>
        <button class="ghost" id="hcRevokeNo">Keep current QR</button>
      </div>
    </div>`;
  panel.querySelector('#hcRevokeNo').addEventListener('click', () => { panel.innerHTML = ''; });
  panel.querySelector('#hcRevokeYes').addEventListener('click', async (e) => {
    e.target.disabled = true;  // a double-click must not fire two revokes
    try {
      await api('POST', `/patients/${patientId}/emergency-card/revoke`);
      await rerender();
      hcNotice(document.getElementById('hcPanel'), 'Old QR revoked. Your card now has a new QR code.');
    } catch {
      e.target.disabled = false;
      panel.querySelector('.hc-sheet').insertAdjacentHTML('beforeend',
        '<div class="hc-notice is-error">Could not revoke, so the old QR still works. Check your connection and try again.</div>');
    }
  });
}

function hcResizePhoto(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const size = 256;
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = size;
      const s = Math.min(img.width, img.height);
      canvas.getContext('2d').drawImage(img, (img.width - s) / 2, (img.height - s) / 2, s, s, 0, 0, size, size);
      URL.revokeObjectURL(img.src);
      resolve(canvas.toDataURL('image/jpeg', 0.85));
    };
    img.onerror = () => reject(new Error('Not an image'));
    img.src = URL.createObjectURL(file);
  });
}

function hcOpenEditor(panel, patientId, data, rerender) {
  const p = data.patient, prof = data.profile;
  let photo;  // undefined = unchanged, '' = remove, data URL = new
  panel.innerHTML = `
    <form class="hc-sheet hc-editor" novalidate>
      <h3>Edit your card</h3>
      <div class="hc-form-grid">
        <label>Allergies<input name="allergies" maxlength="200" value="${hcEsc(p.allergies)}" placeholder="e.g. Penicillin"></label>
        <label>Blood group<select name="blood_group">${HC_BLOOD_GROUPS.map(g =>
          `<option value="${g}" ${g === (p.blood_group || '') ? 'selected' : ''}>${g || 'Not known'}</option>`).join('')}</select></label>
        <label class="is-wide">Emergency contact<input name="emergency_contact" maxlength="200" value="${hcEsc(p.emergency_contact)}" placeholder="Name, phone number"></label>
        <label class="is-wide">Medical conditions<textarea name="conditions" maxlength="500" rows="2" placeholder="e.g. Hypertension, Type 2 diabetes">${hcEsc(prof.conditions)}</textarea></label>
        <label class="is-wide">Emergency instructions<textarea name="instructions" maxlength="500" rows="2" placeholder="e.g. Contact emergency contact, share full history with doctor">${hcEsc(prof.instructions)}</textarea></label>
        <div class="is-wide hc-photo-row">
          <span>Photo</span>
          <label class="ghost small hc-file">Choose photo<input type="file" accept="image/*" name="photo_file"></label>
          ${prof.has_photo ? '<button type="button" class="ghost small" id="hcRemovePhoto">Remove photo</button>' : ''}
          <span class="hc-photo-status" id="hcPhotoStatus"></span>
        </div>
      </div>
      <fieldset class="hc-share">
        <legend>Show on the QR page</legend>
        <p>Your name is always shown. Turn off anything you'd rather keep private.</p>
        ${Object.entries(HC_SHARE_LABELS).map(([key, label]) => `
          <label class="hc-toggle"><input type="checkbox" name="share_${key}" ${prof.share[key] ? 'checked' : ''}><span>${label}</span></label>`).join('')}
      </fieldset>
      <div class="hc-sheet-actions">
        <button type="submit" id="hcSave">Save card</button>
        <button type="button" class="ghost" id="hcCancel">Cancel</button>
      </div>
      <div id="hcEditError"></div>
    </form>`;

  const form = panel.querySelector('form');
  const status = panel.querySelector('#hcPhotoStatus');
  form.photo_file.addEventListener('change', async () => {
    const file = form.photo_file.files[0];
    if (!file) return;
    try { photo = await hcResizePhoto(file); status.textContent = 'New photo ready. Save to use it.'; }
    catch { status.textContent = 'That file isn\'t an image. Choose a JPG or PNG.'; }
  });
  const removeBtn = panel.querySelector('#hcRemovePhoto');
  if (removeBtn) removeBtn.addEventListener('click', () => { photo = ''; status.textContent = 'Photo will be removed when you save.'; });
  panel.querySelector('#hcCancel').addEventListener('click', () => { panel.innerHTML = ''; });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const save = panel.querySelector('#hcSave');
    save.disabled = true;
    const share = Object.fromEntries(Object.keys(HC_SHARE_LABELS).map(k => [k, form[`share_${k}`].checked]));
    const profileBody = { conditions: form.conditions.value.trim(), instructions: form.instructions.value.trim(), share };
    if (photo !== undefined) profileBody.photo = photo;
    try {
      await api('PATCH', `/patients/${patientId}`, {
        allergies: form.allergies.value.trim(),
        blood_group: form.blood_group.value,
        emergency_contact: form.emergency_contact.value.trim(),
      });
      await api('PUT', `/patients/${patientId}/emergency-card/profile`, profileBody);
      await rerender();
      hcNotice(document.getElementById('hcPanel'), 'Card saved. Your QR page shows the new details now.');
    } catch {
      save.disabled = false;
      panel.querySelector('#hcEditError').innerHTML =
        '<div class="hc-notice is-error">Could not save your card. Check your connection and try again.</div>';
    }
  });
}
