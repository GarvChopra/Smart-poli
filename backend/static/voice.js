// SmartPoli Voice — the voice-first page.
//
// Mic → browser speech-to-text → POST /patients/{id}/voice/turn → reply text
// (spoken with speechSynthesis) + actions (popups, result cards, emergency
// panel, links into the app). Groq holds the conversation on the server;
// SmartPoli's own services do every action and the triage rules decide every
// severity — this page only renders what comes back.

const VX = {
  patientId: null,
  lang: 'en',
  muted: false,
  history: [],       // [{role, content}] — last turns, sent back for context
  convState: {},     // server-owned state (symptom check in progress etc.)
  busy: false,
  listening: false,
  recognition: null,
  contactPhone: null,
};

const STORE = { lang: 'smartpoli_voice_lang', muted: 'smartpoli_voice_muted', noted: 'smartpoli_voice_privacy_seen' };
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode — fine */ } },
};

const TEXT = {
  en: {
    greeting: 'How can I help you today?', subline: 'Speak in Hindi, English or Hinglish.',
    tap: 'Tap to speak', listening: 'Listening…', thinking: 'Thinking…', speaking: 'Speaking… tap to stop',
    typeHere: 'Or type here…', noSpeech: "This browser can't listen. Type your message below — or open SmartPoli in Chrome.",
    basic: 'Basic mode: I can mark medicines and tell you your next dose. Full conversation needs the AI service switched on.',
    privacy: 'To understand you, your words are sent to our AI service (Groq). Only your own SmartPoli data is used.',
    ok: 'Got it', error: "I couldn't reach SmartPoli. Check your connection and try again.", slow: 'Too many requests — wait a moment.',
    chips: ['Aaj kaun si medicine hai?', 'Maine dawai le li', 'Meri agli medicine kab hai?', 'Mujhe theek nahi lag raha'],
    taken: 'Taken', undo: 'Undo', undone: 'Undone — the dose is back to pending.', which: 'Which one did you take?',
    open: 'Open', openCard: 'Open my emergency card', resultFrom: "Result from SmartPoli's clinical rules",
    sev: { LOW: 'Self-care & monitor', MODERATE: 'See a doctor', EMERGENCY: 'Get medical help now', NOT_ASSESSED: 'Keep an eye on it' },
    sevText: { LOW: 'This can usually be looked after at home. Keep an eye on how it goes.',
      MODERATE: 'Please see a doctor within the next day or two. You can keep monitoring meanwhile.' },
    fromPrescription: 'From your prescription', mayHelp: 'What may help',
    prescriptionNote: 'Only if your doctor gave this for this problem — take it the way they told you.',
    calmNote: "Take your time answering. If it ever feels much worse, help is one tap away.", call112: 'Call 112',
    recheckAt: "I'll check with you at {time}", recheckHow: 'Or tell me any time how it feels:',
    better: 'Better', same: 'Same', worse: 'Worse', howNow: 'How are you feeling now?',
    notAssessed: "SmartPoli's rules don't cover this symptom. If it is severe, getting worse, or worrying you, contact your doctor.",
    scheduled: 'Prescription scheduled', draft: 'Prescription read — say yes to schedule it',
    noPatient: 'Create your patient profile in the SmartPoli app first.', openApp: 'Open SmartPoli',
    screens: { dashboard: 'Dashboard', prescriptions: 'Prescriptions', safety: 'Safety center', triage: 'Symptom check', report: 'Care report', timeline: 'Timeline', emergency: 'Emergency card', settings: 'Settings' },
    emTitle: 'Please get medical help now', emText: "Stay calm. Call 112, or ask someone near you to call — the button below does it for you.",
    emCall: 'Call 112', emContact: 'Call my emergency contact', emCard: 'Show my emergency card',
  },
  hi: {
    greeting: 'Aaj main aapki kya madad karoon?', subline: 'Hindi, English ya Hinglish mein boliye.',
    tap: 'Bolne ke liye dabaiye', listening: 'Sun raha hoon…', thinking: 'Soch raha hoon…', speaking: 'Bol raha hoon… rokne ke liye dabaiye',
    typeHere: 'Ya yahan likhiye…', noSpeech: 'Yeh browser sun nahi sakta. Neeche likhiye — ya SmartPoli ko Chrome mein kholiye.',
    basic: 'Basic mode: main dawai mark kar sakta hoon aur agli dawai bata sakta hoon. Poori baatcheet ke liye AI service chahiye.',
    privacy: 'Aapki baat samajhne ke liye aapke shabd hamari AI service (Groq) ko bheje jaate hain. Sirf aapka SmartPoli data use hota hai.',
    ok: 'Theek hai', error: 'SmartPoli tak nahi pahunch paaye. Internet check karke dobara koshish kijiye.', slow: 'Bahut saari requests — thoda rukiye.',
    chips: ['Aaj kaun si medicine hai?', 'Maine dawai le li', 'Meri agli medicine kab hai?', 'Mujhe theek nahi lag raha'],
    taken: 'Le li', undo: 'Wapas lein', undone: 'Wapas le liya — dawai phir se pending hai.', which: 'Aapne kaun si li?',
    open: 'Kholiye', openCard: 'Mera emergency card kholiye', resultFrom: 'SmartPoli ke clinical rules ka nateeja',
    sev: { LOW: 'Ghar par dhyan rakhiye', MODERATE: 'Doctor ko dikhaiye', EMERGENCY: 'Abhi doctor ki madad lijiye', NOT_ASSESSED: 'Nazar rakhiye' },
    sevText: { LOW: 'Iska dhyan aam taur par ghar par rakha ja sakta hai. Dekhte rahiye kaisa lag raha hai.',
      MODERATE: 'Agle ek-do din mein doctor ko dikha lijiye. Tab tak dhyan rakhte rahiye.' },
    fromPrescription: 'Aapke prescription se', mayHelp: 'Isse madad mil sakti hai',
    prescriptionNote: 'Sirf tab, jab doctor ne ise isi problem ke liye diya ho — unke bataye tareeke se lijiye.',
    calmNote: 'Aaram se jawab dijiye. Agar kabhi bhi bahut zyada takleef lage, madad bas ek button door hai.', call112: '112 par call',
    recheckAt: 'Main {time} baje aapse poochunga', recheckHow: 'Ya kabhi bhi batayein kaisa lag raha hai:',
    better: 'Behtar', same: 'Waisa hi', worse: 'Zyada kharab', howNow: 'Ab kaisa lag raha hai?',
    notAssessed: 'SmartPoli ke rules is lakshan ko cover nahi karte. Agar yeh tez hai, badh raha hai ya chinta ho rahi hai, to doctor se sampark kijiye.',
    scheduled: 'Prescription schedule ho gaya', draft: 'Prescription padh liya — schedule karne ke liye haan boliye',
    noPatient: 'Pehle SmartPoli app mein apni patient profile banaiye.', openApp: 'SmartPoli kholiye',
    screens: { dashboard: 'Dashboard', prescriptions: 'Prescription', safety: 'Safety center', triage: 'Lakshan jaanch', report: 'Care report', timeline: 'Timeline', emergency: 'Emergency card', settings: 'Settings' },
    emTitle: 'Abhi doctor ki madad lijiye', emText: 'Ghabraiye nahi. 112 par call kijiye, ya paas kisi se call karwaiye — neeche ka button call kar dega.',
    emCall: '112 par call karein', emContact: 'Emergency contact ko call karein', emCard: 'Mera emergency card dikhaiye',
  },
};
const tx = (k) => (TEXT[VX.lang] || TEXT.en)[k];

const ICON = {
  speaker: '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/></svg>',
  muted: '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="m17 9 5 6M22 9l-5 6"/></svg>',
  grid: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/></svg>',
  tick: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
};

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtTime = (iso) => new Date(iso + (iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? '' : 'Z'))
  .toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

// ---------------------------------------------------------------- UI text + controls

function applyLanguage() {
  document.documentElement.lang = VX.lang === 'hi' ? 'hi' : 'en';
  $('greeting').textContent = tx('greeting');
  $('subline').textContent = tx('subline');
  $('typeInput').placeholder = tx('typeHere');
  document.querySelectorAll('.vx-lang button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.lang === VX.lang)));
  $('chips').innerHTML = tx('chips').map(c => `<button type="button">${esc(c)}</button>`).join('');
  setStatus(VX.listening ? 'listening' : VX.busy ? 'thinking' : 'tap');
  if (VX.recognition) VX.recognition.lang = VX.lang === 'hi' ? 'hi-IN' : 'en-IN';
}

function setStatus(key) {
  $('status').textContent = tx(key);
  $('micBtn').setAttribute('aria-label', tx(key));
}

function applyMute() {
  $('muteBtn').innerHTML = VX.muted ? ICON.muted : ICON.speaker;
  $('muteBtn').setAttribute('aria-pressed', String(VX.muted));
  if (VX.muted) window.speechSynthesis?.cancel();
}

function showNote(html) {
  const note = $('note');
  note.innerHTML = html;
  note.hidden = false;
}

// ---------------------------------------------------------------- conversation log

function addEntry(html, cls) {
  const li = document.createElement('li');
  li.className = cls;
  li.innerHTML = html;
  $('log').appendChild(li);
  document.querySelector('.vx-app').classList.add('is-talking');
  li.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  return li;
}

const addUser = (text) => addEntry(esc(text), 'vx-msg is-user');
const addBot = (text) => addEntry(esc(text), 'vx-msg is-bot');

// ---------------------------------------------------------------- speech out

function speak(text, onDone, lang) {
  const synth = window.speechSynthesis;
  if (VX.muted || !synth || !text) { onDone?.(); return; }
  synth.cancel();
  const u = new SpeechSynthesisUtterance(text);
  // Devanagari replies need a Hindi voice even when the toggle says EN.
  u.lang = (lang || VX.lang) === 'hi' || /[ऀ-ॿ]/.test(text) ? 'hi-IN' : 'en-IN';
  const voice = synth.getVoices().find(v => v.lang === u.lang) || synth.getVoices().find(v => v.lang.startsWith(u.lang.slice(0, 2)));
  if (voice) u.voice = voice;
  u.onstart = () => { setStatus('speaking'); $('micBtn').classList.add('is-speaking'); };
  u.onend = u.onerror = () => { $('micBtn').classList.remove('is-speaking'); setStatus('tap'); onDone?.(); };
  synth.speak(u);
}

// ---------------------------------------------------------------- speech in

function setupRecognition() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) return null;
  const r = new Recognition();
  r.lang = VX.lang === 'hi' ? 'hi-IN' : 'en-IN';
  r.interimResults = true;
  r.continuous = false;
  let finalText = '';
  r.onstart = () => { VX.listening = true; finalText = ''; $('micBtn').classList.add('is-listening'); setStatus('listening'); };
  r.onresult = (e) => {
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      if (e.results[i].isFinal) finalText += e.results[i][0].transcript;
      else interim += e.results[i][0].transcript;
    }
    $('interim').textContent = (finalText + ' ' + interim).trim();
  };
  r.onerror = () => {};
  r.onend = () => {
    VX.listening = false;
    $('micBtn').classList.remove('is-listening');
    $('interim').textContent = '';
    setStatus('tap');
    if (finalText.trim()) send(finalText.trim());
  };
  return r;
}

function onMic() {
  if (VX.busy) return;
  const synth = window.speechSynthesis;
  if (synth && synth.speaking) { synth.cancel(); $('micBtn').classList.remove('is-speaking'); setStatus('tap'); return; }  // tap to stop talking
  if (!VX.recognition) { $('typeInput').focus(); return; }
  if (VX.listening) { VX.recognition.stop(); return; }
  try { VX.recognition.start(); } catch { /* already starting */ }
}

// ---------------------------------------------------------------- server turn

async function send(text, extra = {}) {
  if (VX.busy || !text || !VX.patientId) return;
  VX.busy = true;
  $('micBtn').classList.add('is-busy');
  setStatus('thinking');
  addUser(text);
  const typing = addEntry('<span class="vx-typing"><i></i><i></i><i></i></span><span class="vx-sr">' + esc(tx('thinking')) + '</span>', 'vx-msg is-bot is-typing');
  try {
    const res = await apiFetch('POST', `/patients/${VX.patientId}/voice/turn`, {
      text, lang: VX.lang, client_time: localIsoNow(), history: VX.history.slice(-20), state: VX.convState, ...extra,
    });
    VX.history.push({ role: 'user', content: text }, { role: 'assistant', content: res.reply });
    VX.convState = res.state || {};
    saveConvState();
    const emergency = (res.actions || []).find(a => a.type === 'emergency');
    typing.remove();
    addBot(res.reply);
    const afterSpeech = renderActions(res.actions || []);
    if (emergency) showEmergency(emergency);
    speak(res.reply, afterSpeech, res.lang);
  } catch (err) {
    typing.remove();
    addEntry(esc(/429|Too many/.test(err.message) ? tx('slow') : tx('error')), 'vx-msg is-error');
  } finally {
    VX.busy = false;
    $('micBtn').classList.remove('is-busy');
    if (!window.speechSynthesis?.speaking) setStatus('tap');
  }
}

// A symptom check or a pending recheck survives a reload of the page.
function saveConvState() {
  store.set(`smartpoli_voice_state_${VX.patientId}`, JSON.stringify(VX.convState || {}));
}

function loadConvState() {
  try { VX.convState = JSON.parse(store.get(`smartpoli_voice_state_${VX.patientId}`) || '{}') || {}; }
  catch { VX.convState = {}; }
}

function localIsoNow() {
  const d = new Date();
  const off = -d.getTimezoneOffset();
  const pad = (n) => String(Math.floor(Math.abs(n))).padStart(2, '0');
  const local = new Date(d.getTime() + off * 60000).toISOString().slice(0, 19);
  return `${local}${off >= 0 ? '+' : '-'}${pad(off / 60)}:${pad(off % 60)}`;
}

// ---------------------------------------------------------------- actions → cards

/** Renders each action; returns a callback to run after the reply is spoken
 * (navigation waits so the patient hears the answer first). */
function renderActions(actions) {
  let after = null;
  for (const a of actions) {
    if (a.type === 'dose_taken') doseTakenCard(a);
    if (a.type === 'prn_logged') addEntry(`<div class="vx-card-row"><span class="vx-tick">${ICON.tick}</span><div><h3>${esc(a.medicine)}</h3><p>${tx('taken')}</p></div></div>`, 'vx-card');
    if (a.type === 'choose_dose') chooseDoseCard(a);
    if (a.type === 'triage_result') triageCard(a);
    if (a.type === 'calm_check') calmCheckCard();
    if (a.type === 'recheck') recheckCard(a);
    if (a.type === 'open_card') linkCard(tx('openCard'), '/static/index.html#tab=emergency');
    if (a.type === 'prescription_draft') {
      addEntry(`<h3>${tx('draft')}</h3><ul>${a.medicines.map(m => `<li>${esc([m.name || m.line, m.dose, m.schedule].filter(Boolean).join(' · '))}</li>`).join('')}</ul>`, 'vx-card');
    }
    if (a.type === 'prescription_confirmed') addEntry(`<div class="vx-card-row"><span class="vx-tick">${ICON.tick}</span><div><h3>${tx('scheduled')}</h3></div></div>`, 'vx-card');
    if (a.type === 'navigate') {
      const url = `/static/index.html#tab=${encodeURIComponent(a.screen)}`;
      linkCard(`${tx('open')}: ${(tx('screens') || {})[a.screen] || a.screen}`, url);
      after = () => { window.location.href = url; };
    }
  }
  return after;
}

function doseTakenCard(a) {
  const li = addEntry(`
    <div class="vx-card-row"><span class="vx-tick">${ICON.tick}</span>
      <div><h3>${esc(a.medicine)}</h3><p>${tx('taken')} ${esc(fmtTime(a.taken_at))}</p></div>
      <button type="button" class="vx-btn">${tx('undo')}</button></div>`, 'vx-card');
  const btn = li.querySelector('button');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      await apiFetch('POST', `/doses/${a.dose_id}/undo`);
      li.querySelector('p').textContent = tx('undone');
      btn.remove();
    } catch (err) {
      btn.disabled = false;
      li.querySelector('p').textContent = err.message;
    }
  });
}

function chooseDoseCard(a) {
  const li = addEntry(`<h3>${tx('which')}</h3><div class="vx-card-actions">${a.options.map(o =>
    `<button type="button" class="vx-btn" data-dose="${o.dose_id}">${esc(o.medicine)} · ${esc(o.time)}</button>`).join('')}</div>`, 'vx-card');
  li.querySelectorAll('[data-dose]').forEach(btn => btn.addEventListener('click', async () => {
    li.querySelectorAll('button').forEach(b => { b.disabled = true; });
    try {
      const dose = await apiFetch('POST', `/doses/${btn.dataset.dose}/take`);
      const opt = a.options.find(o => String(o.dose_id) === btn.dataset.dose);
      li.remove();
      doseTakenCard({ dose_id: dose.id, medicine: opt.medicine, taken_at: dose.acted_at });
    } catch (err) {
      li.querySelectorAll('button').forEach(b => { b.disabled = false; });
    }
  }));
}

/** Self-care / see a doctor / not rated — with the guidance SmartPoli is
 * allowed to give: the patient's own prescription first, then sourced steps. */
function triageCard(a) {
  const sev = a.severity || 'NOT_ASSESSED';
  const body = sev === 'NOT_ASSESSED' ? tx('notAssessed') : (tx('sevText')[sev] || a.action);
  const g = a.guidance || {};
  const prescribed = (g.prescribed || []).length ? `
    <div class="vx-guide"><h4>${tx('fromPrescription')}</h4><ul>${g.prescribed.map(m =>
      `<li><strong>${esc(m.name)}</strong> — ${esc(m.instruction)}</li>`).join('')}</ul>
      <p class="vx-small">${tx('prescriptionNote')}</p></div>` : '';
  const general = (g.general || []).length ? `
    <div class="vx-guide"><h4>${tx('mayHelp')}</h4><ul>${g.general.map(i =>
      `<li>${esc(i.text)} <a class="vx-src" href="${esc(i.source_url)}" target="_blank" rel="noopener noreferrer">${esc(i.source_title.split(' - ')[0])}</a></li>`).join('')}</ul></div>` : '';
  addEntry(`<span class="vx-sev ${esc(sev)}">${esc(tx('sev')[sev] || sev)}</span><p>${esc(body)}</p>
    ${prescribed}${general}
    ${sev !== 'NOT_ASSESSED' ? `<div class="vx-source">${tx('resultFrom')}</div>` : ''}`, `vx-card vx-level-${esc(sev)}`);
}

/** Worrying words: one calm question is being asked; help stays one tap away. */
function calmCheckCard() {
  addEntry(`<p>${tx('calmNote')}</p><div class="vx-card-actions"><a class="vx-btn is-quiet" href="tel:112">${tx('call112')}</a></div>`,
    'vx-card vx-calm');
}

/** "I'll check back at 10:45" — Better / Same / Worse, and a gentle ask when it's time. */
function recheckCard(a) {
  const due = new Date(a.due_at);
  const time = due.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const li = addEntry(`<h3>${esc(tx('recheckAt').replace('{time}', time))}</h3><p>${tx('recheckHow')}</p>
    <div class="vx-card-actions">
      <button type="button" class="vx-btn" data-recheck="better">${tx('better')}</button>
      <button type="button" class="vx-btn" data-recheck="same">${tx('same')}</button>
      <button type="button" class="vx-btn" data-recheck="worse">${tx('worse')}</button>
    </div>`, 'vx-card vx-recheck');
  li.querySelectorAll('[data-recheck]').forEach(btn => btn.addEventListener('click', () => {
    li.querySelectorAll('button').forEach(b => { b.disabled = true; });
    send(btn.textContent, { recheck: btn.dataset.recheck });
  }));
  scheduleRecheckPrompt(due);
}

function scheduleRecheckPrompt(due) {
  clearTimeout(VX.recheckTimer);
  const wait = due.getTime() - Date.now();
  VX.recheckTimer = setTimeout(() => {
    if (!VX.convState.recheck) return;
    addBot(tx('howNow'));
    speak(tx('howNow'));
    document.querySelector('.vx-recheck:last-of-type')?.classList.add('is-due');
  }, Math.max(wait, 0));
}

function linkCard(label, url) {
  addEntry(`<a class="vx-btn is-primary" href="${esc(url)}">${esc(label)}</a>`, 'vx-card');
}

/** Calm, in-conversation help — no full-screen alarm, no flashing. */
function showEmergency(a) {
  VX.recognition?.abort?.();
  const contact = VX.contactPhone ? `<a class="vx-btn" href="tel:${esc(VX.contactPhone)}">${tx('emContact')}</a>` : '';
  const li = addEntry(`
    <h3>${tx('emTitle')}</h3>
    <p>${tx('emText')}</p>
    <a class="vx-help-call" href="tel:112">${tx('emCall')}</a>
    <div class="vx-card-actions">${contact}<a class="vx-btn" href="/static/index.html#tab=emergency">${tx('emCard')}</a></div>
    ${(a.reasons || []).length ? `<p class="vx-small">${a.reasons.map(esc).join(' · ')}</p>` : ''}`, 'vx-card vx-help');
  li.querySelector('.vx-help-call').focus({ preventScroll: true });
}

// ---------------------------------------------------------------- boot

async function boot() {
  const auth = getAuth();
  if (!auth || !auth.token || !auth.user) {
    window.location.href = '/static/login.html?next=' + encodeURIComponent('/static/voice.html');
    return;
  }
  if (auth.user.role !== 'patient') { window.location.href = landingPageFor(auth.user.role); return; }

  VX.lang = store.get(STORE.lang) || ((navigator.language || '').startsWith('hi') ? 'hi' : 'en');
  VX.muted = store.get(STORE.muted) === '1';
  $('openAppLink').innerHTML = ICON.grid;
  applyLanguage();
  applyMute();

  VX.recognition = setupRecognition();
  if (!VX.recognition) showNote(esc(tx('noSpeech')));

  document.querySelector('.vx-lang').addEventListener('click', (e) => {
    const b = e.target.closest('[data-lang]');
    if (!b) return;
    VX.lang = b.dataset.lang;
    store.set(STORE.lang, VX.lang);
    applyLanguage();
  });
  $('muteBtn').addEventListener('click', () => { VX.muted = !VX.muted; store.set(STORE.muted, VX.muted ? '1' : '0'); applyMute(); });
  $('micBtn').addEventListener('click', onMic);
  $('chips').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) send(b.textContent); });
  $('typeForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const text = $('typeInput').value.trim();
    if (!text) return;
    $('typeInput').value = '';
    send(text);
  });

  try {
    const patients = await apiFetch('GET', '/patients');
    if (!patients.length) {
      showNote(`${esc(tx('noPatient'))} <a class="vx-btn" href="/static/index.html">${esc(tx('openApp'))}</a>`);
      $('micBtn').disabled = true;
      return;
    }
    VX.patientId = patients[0].id;
    loadConvState();
    if (VX.convState.recheck?.due_at) recheckCard({ due_at: VX.convState.recheck.due_at });
    const phone = (patients[0].emergency_contact || '').match(/\+?\d[\d\s-]{6,}\d/);
    VX.contactPhone = phone ? phone[0].replace(/[\s-]/g, '') : null;
    const { available } = await apiFetch('GET', '/voice/available');
    if (!available) showNote(esc(tx('basic')));
    else if (store.get(STORE.noted) !== '1') {
      showNote(`<span>${esc(tx('privacy'))}</span><button type="button" id="noteOk">${esc(tx('ok'))}</button>`);
      $('noteOk').addEventListener('click', () => { store.set(STORE.noted, '1'); $('note').hidden = true; });
    }
  } catch {
    showNote(esc(tx('error')));
  }

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw-voice.js', { scope: '/static/' }).catch(() => {});
  }
}

boot();
