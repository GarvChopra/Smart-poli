// SmartPoli Voice — a continuous voice interface to SmartPoli, not a chatbot.
//
// One tap to begin (browsers require it for the mic and speech), then the
// conversation runs by itself: listening → thinking → speaking → listening,
// with barge-in (voice-engine.js). Groq holds the conversation on the server;
// SmartPoli's own services do every action and the triage rules decide every
// severity — this page only shows what matters right now: the live words, the
// latest reply, and the one or two cards that need the patient's eyes.

const VX = {
  patientId: null,
  lang: 'en',
  muted: false,
  history: [],       // [{role, content}] — recent turns, sent back for context
  convState: {},     // server-owned state (symptom check in progress, recheck…)
  contactPhone: null,
  engine: null,
  pendingNav: null,
  recheckTimer: null,
};

const STORE = { lang: 'smartpoli_voice_lang', muted: 'smartpoli_voice_muted', noted: 'smartpoli_voice_privacy_seen' };
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode — fine */ } },
};

const TEXT = {
  en: {
    greeting: 'Talk to SmartPoli', subline: 'Speak naturally — Hindi, English or Hinglish.',
    start: 'Start talking', startHint: 'Tap once to turn on your microphone.',
    idle: 'Tap to start', listening: 'Listening…', thinking: 'Understanding…', speaking: 'Speaking — just talk to interrupt',
    paused: 'Paused — tap to continue',
    noSpeech: "This browser can't listen. Open SmartPoli in Chrome to talk to it.",
    micBlocked: 'Microphone is blocked. Allow it in your browser settings, then tap to start.',
    basic: 'Basic mode: I can help with your medicines. Full conversation needs the AI service switched on.',
    privacy: 'To understand you, your words are sent to our AI service (Groq). Only your own SmartPoli data is used.',
    ok: 'Got it', basedOn: 'Based on:', error: "I couldn't reach SmartPoli. Check your connection — I'm still listening.",
    slow: 'Too many requests — give me a moment.',
    taken: 'Taken', undo: 'Undo', undone: 'Undone — the dose is back to pending.', which: 'Which one did you take?',
    open: 'Open', openCard: 'Open my emergency card', resultFrom: "Result from SmartPoli's clinical rules",
    sev: { LOW: 'Self-care & monitor', MODERATE: 'Try first, then a doctor', EMERGENCY: 'Get medical help now', NOT_ASSESSED: 'Keep an eye on it' },
    sevText: { LOW: 'This can usually be looked after at home. Keep an eye on how it goes.',
      MODERATE: "Try the steps below first. If it isn't better when I check back, please see a doctor — within a day or two either way." },
    fromPrescription: 'From your prescription', mayHelp: 'What may help',
    prescriptionNote: 'Only if your doctor gave this for this problem — take it the way they told you.',
    recheckAt: "I'll check with you at {time}", recheckHow: 'Or tell me any time how it feels:',
    better: 'Better', same: 'Same', worse: 'Worse', howNow: 'How are you feeling now?',
    notAssessed: "SmartPoli's rules don't cover this symptom. If it is severe, getting worse, or worrying you, contact your doctor.",
    scheduled: 'Prescription scheduled', draft: 'Prescription read — say yes to schedule it',
    noPatient: 'Create your patient profile in the SmartPoli app first.', openApp: 'Open SmartPoli',
    screens: { dashboard: 'Dashboard', prescriptions: 'Prescriptions', safety: 'Safety center', triage: 'Symptom check', report: 'Care report', timeline: 'Timeline', emergency: 'Emergency card', settings: 'Settings' },
    emTitle: 'Please get medical help now', emText: "Stay calm. Call 112, or ask someone near you to call — the button below does it for you.",
    emCall: 'Call 112', emContact: 'Call my emergency contact', emCard: 'Show my emergency card', emResume: "I'm okay — keep talking",
  },
  hi: {
    greeting: 'SmartPoli se baat kijiye', subline: 'Aaram se boliye — Hindi, English ya Hinglish.',
    start: 'Baat shuru karein', startHint: 'Microphone chalu karne ke liye ek baar dabaiye.',
    idle: 'Shuru karne ke liye dabaiye', listening: 'Sun raha hoon…', thinking: 'Samajh raha hoon…', speaking: 'Bol raha hoon — beech mein bol sakte hain',
    paused: 'Ruka hua — jaari rakhne ke liye dabaiye',
    noSpeech: 'Yeh browser sun nahi sakta. Baat karne ke liye SmartPoli ko Chrome mein kholiye.',
    micBlocked: 'Microphone band hai. Browser settings mein allow kijiye, phir dabaiye.',
    basic: 'Basic mode: main dawaiyon mein madad kar sakta hoon. Poori baatcheet ke liye AI service chahiye.',
    privacy: 'Aapki baat samajhne ke liye aapke shabd hamari AI service (Groq) ko bheje jaate hain. Sirf aapka SmartPoli data use hota hai.',
    ok: 'Theek hai', basedOn: 'Jaankari ka srot:', error: 'SmartPoli tak nahi pahunch paaye. Internet check kijiye — main sun raha hoon.',
    slow: 'Bahut saari requests — thoda rukiye.',
    taken: 'Le li', undo: 'Wapas lein', undone: 'Wapas le liya — dawai phir se pending hai.', which: 'Aapne kaun si li?',
    open: 'Kholiye', openCard: 'Mera emergency card kholiye', resultFrom: 'SmartPoli ke clinical rules ka nateeja',
    sev: { LOW: 'Ghar par dhyan rakhiye', MODERATE: 'Pehle upay, phir doctor', EMERGENCY: 'Abhi doctor ki madad lijiye', NOT_ASSESSED: 'Nazar rakhiye' },
    sevText: { LOW: 'Iska dhyan aam taur par ghar par rakha ja sakta hai. Dekhte rahiye kaisa lag raha hai.',
      MODERATE: 'Pehle neeche diye upay try kijiye. Agar main dobara poochun tab tak farak na pade, to doctor ko dikhaiye — waise bhi ek-do din mein dikha lijiye.' },
    fromPrescription: 'Aapke prescription se', mayHelp: 'Isse madad mil sakti hai',
    prescriptionNote: 'Sirf tab, jab doctor ne ise isi problem ke liye diya ho — unke bataye tareeke se lijiye.',
    recheckAt: 'Main {time} baje aapse poochunga', recheckHow: 'Ya kabhi bhi batayein kaisa lag raha hai:',
    better: 'Behtar', same: 'Waisa hi', worse: 'Zyada kharab', howNow: 'Ab kaisa lag raha hai?',
    notAssessed: 'SmartPoli ke rules is lakshan ko cover nahi karte. Agar yeh tez hai, badh raha hai ya chinta ho rahi hai, to doctor se sampark kijiye.',
    scheduled: 'Prescription schedule ho gaya', draft: 'Prescription padh liya — schedule karne ke liye haan boliye',
    noPatient: 'Pehle SmartPoli app mein apni patient profile banaiye.', openApp: 'SmartPoli kholiye',
    screens: { dashboard: 'Dashboard', prescriptions: 'Prescription', safety: 'Safety center', triage: 'Lakshan jaanch', report: 'Care report', timeline: 'Timeline', emergency: 'Emergency card', settings: 'Settings' },
    emTitle: 'Abhi doctor ki madad lijiye', emText: 'Ghabraiye nahi. 112 par call kijiye, ya paas kisi se call karwaiye — neeche ka button call kar dega.',
    emCall: '112 par call karein', emContact: 'Emergency contact ko call karein', emCard: 'Mera emergency card dikhaiye', emResume: 'Main theek hoon — baat jaari rakhein',
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

// ---------------------------------------------------------------- language / controls

function applyLanguage() {
  document.documentElement.lang = VX.lang === 'hi' ? 'hi' : 'en';
  $('greeting').textContent = tx('greeting');
  $('subline').textContent = tx('subline');
  $('startBtn').textContent = tx('start');
  $('startHint').textContent = tx('startHint');
  document.querySelectorAll('.vx-lang button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.lang === VX.lang)));
  if (VX.engine) {
    VX.engine.rec.lang = VX.lang === 'hi' ? 'hi-IN' : 'en-IN';
    showState(VX.engine.state);
  }
}

function applyMute() {
  $('muteBtn').innerHTML = VX.muted ? ICON.muted : ICON.speaker;
  $('muteBtn').setAttribute('aria-pressed', String(VX.muted));
  if (VX.muted) window.speechSynthesis?.cancel();
}

function showState(state) {
  document.querySelector('.vx-app').dataset.state = state;
  $('status').textContent = tx(state === 'idle' ? 'idle' : state);
  $('orb').setAttribute('aria-label', tx(state === 'idle' ? 'idle' : state));
}

function showNote(html) {
  const note = $('note');
  note.innerHTML = html;
  note.hidden = false;
}

// ---------------------------------------------------------------- words on screen (not a chat log)

function showHeard(text) { $('heard').textContent = text; }

function showReply(text) {
  $('reply').textContent = text || '';
  $('reply').hidden = !text;
}

/** Only the latest one or two cards stay on screen — this isn't a chat history. */
function addCard(html, cls) {
  const box = $('cards');
  const card = document.createElement('div');
  card.className = `vx-card ${cls || ''}`;
  card.innerHTML = html;
  box.prepend(card);
  while (box.children.length > 2) box.lastElementChild.remove();
  return card;
}

// ---------------------------------------------------------------- server turn

async function sendTurn(text, extra = {}) {
  showHeard(text);
  let res;
  try {
    res = await apiFetch('POST', `/patients/${VX.patientId}/voice/turn`, {
      text, lang: VX.lang, client_time: localIsoNow(), history: VX.history.slice(-20), state: VX.convState, ...extra,
    });
  } catch (err) {
    const msg = /429|Too many/.test(err.message) ? tx('slow') : tx('error');
    return { reply: msg, lang: VX.lang, actions: [] };
  }
  VX.history.push({ role: 'user', content: text }, { role: 'assistant', content: res.reply });
  VX.convState = res.state || {};
  saveConvState();
  return res;
}

function onReply(res) {
  showReply(res.reply);
  showSources([]);
  renderActions(res.actions || []);
}

/** Subtle "Based on …" line — where the medical information came from. */
function showSources(items) {
  const el = $('sources');
  el.hidden = !items.length;
  el.innerHTML = items.length ? `${tx('basedOn')} ${items.map(s =>
    `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" title="${esc(s.document)}">${esc(s.publisher.split(' (')[0])}</a>`).join(' · ')}` : '';
}

function onDoneSpeaking(res) {
  const nav = (res.actions || []).find(a => a.type === 'navigate');
  if (nav) window.location.href = `/static/index.html#tab=${encodeURIComponent(nav.screen)}`;
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

function renderActions(actions) {
  for (const a of actions) {
    if (a.type === 'dose_taken') doseTakenCard(a);
    if (a.type === 'prn_logged') addCard(`<div class="vx-card-row"><span class="vx-tick">${ICON.tick}</span><div><h3>${esc(a.medicine)}</h3><p>${tx('taken')}</p></div></div>`);
    if (a.type === 'choose_dose') chooseDoseCard(a);
    if (a.type === 'triage_result') triageCard(a);
    if (a.type === 'emergency') helpCard(a);
    if (a.type === 'recheck') recheckCard(a);
    if (a.type === 'sources') showSources(a.items || []);
    if (a.type === 'open_card') linkCard(tx('openCard'), '/static/index.html#tab=emergency');
    if (a.type === 'prescription_draft') {
      addCard(`<h3>${tx('draft')}</h3><ul>${a.medicines.map(m => `<li>${esc([m.name || m.line, m.dose, m.schedule].filter(Boolean).join(' · '))}</li>`).join('')}</ul>`);
    }
    if (a.type === 'prescription_confirmed') addCard(`<div class="vx-card-row"><span class="vx-tick">${ICON.tick}</span><div><h3>${tx('scheduled')}</h3></div></div>`);
    if (a.type === 'navigate') linkCard(`${tx('open')}: ${(tx('screens') || {})[a.screen] || a.screen}`, `/static/index.html#tab=${encodeURIComponent(a.screen)}`);
  }
}

function doseTakenCard(a) {
  const card = addCard(`
    <div class="vx-card-row"><span class="vx-tick">${ICON.tick}</span>
      <div><h3>${esc(a.medicine)}</h3><p>${tx('taken')} ${esc(fmtTime(a.taken_at))}</p></div>
      <button type="button" class="vx-btn">${tx('undo')}</button></div>`);
  const btn = card.querySelector('button');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      await apiFetch('POST', `/doses/${a.dose_id}/undo`);
      card.querySelector('p').textContent = tx('undone');
      btn.remove();
    } catch (err) {
      btn.disabled = false;
      card.querySelector('p').textContent = err.message;
    }
  });
}

function chooseDoseCard(a) {
  const card = addCard(`<h3>${tx('which')}</h3><div class="vx-card-actions">${a.options.map(o =>
    `<button type="button" class="vx-btn" data-dose="${o.dose_id}">${esc(o.medicine)} · ${esc(o.time)}</button>`).join('')}</div>`);
  card.querySelectorAll('[data-dose]').forEach(btn => btn.addEventListener('click', async () => {
    card.querySelectorAll('button').forEach(b => { b.disabled = true; });
    try {
      const dose = await apiFetch('POST', `/doses/${btn.dataset.dose}/take`);
      const opt = a.options.find(o => String(o.dose_id) === btn.dataset.dose);
      card.remove();
      doseTakenCard({ dose_id: dose.id, medicine: opt.medicine, taken_at: dose.acted_at });
    } catch (err) {
      card.querySelectorAll('button').forEach(b => { b.disabled = false; });
    }
  }));
}

/** Self-care / try-first / not rated — guidance only from the patient's own
 * prescription and the sourced list the server returns. */
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
  addCard(`<span class="vx-sev ${esc(sev)}">${esc(tx('sev')[sev] || sev)}</span><p>${esc(body)}</p>
    ${prescribed}${general}
    ${sev !== 'NOT_ASSESSED' ? `<div class="vx-source">${tx('resultFrom')}</div>` : ''}`, `vx-level-${esc(sev)}`);
}

/** "I'll check back at 10:45" — Better / Same / Worse (or just say it), and a gentle ask when it's time. */
function recheckCard(a) {
  const due = new Date(a.due_at);
  const time = due.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const card = addCard(`<h3>${esc(tx('recheckAt').replace('{time}', time))}</h3><p>${tx('recheckHow')}</p>
    <div class="vx-card-actions">
      <button type="button" class="vx-btn" data-recheck="better">${tx('better')}</button>
      <button type="button" class="vx-btn" data-recheck="same">${tx('same')}</button>
      <button type="button" class="vx-btn" data-recheck="worse">${tx('worse')}</button>
    </div>`, 'vx-recheck');
  card.querySelectorAll('[data-recheck]').forEach(btn => btn.addEventListener('click', () => {
    card.querySelectorAll('button').forEach(b => { b.disabled = true; });
    VX.engine?.say(btn.textContent, { recheck: btn.dataset.recheck });
  }));
  clearTimeout(VX.recheckTimer);
  VX.recheckTimer = setTimeout(() => {
    if (!VX.convState.recheck) return;
    card.classList.add('is-due');
    showReply(tx('howNow'));
    VX.engine?.announce(tx('howNow'), VX.lang);
  }, Math.max(due.getTime() - Date.now(), 0));
}

function linkCard(label, url) {
  addCard(`<a class="vx-btn is-primary" href="${esc(url)}">${esc(label)}</a>`);
}

/** Calm, in-place help — no full-screen alarm. Listening pauses until the patient is ready. */
function helpCard(a) {
  const contact = VX.contactPhone ? `<a class="vx-btn" href="tel:${esc(VX.contactPhone)}">${tx('emContact')}</a>` : '';
  const card = addCard(`
    <h3>${tx('emTitle')}</h3>
    <p>${tx('emText')}</p>
    <a class="vx-help-call" href="tel:112">${tx('emCall')}</a>
    <div class="vx-card-actions">${contact}<a class="vx-btn" href="/static/index.html#tab=emergency">${tx('emCard')}</a></div>
    ${(a.reasons || []).length ? `<p class="vx-small">${a.reasons.map(esc).join(' · ')}</p>` : ''}
    <button type="button" class="vx-link" data-resume>${tx('emResume')}</button>`, 'vx-help');
  card.querySelector('[data-resume]').addEventListener('click', () => VX.engine?.resume());
  card.querySelector('.vx-help-call').focus({ preventScroll: true });
}

// ---------------------------------------------------------------- engine

// Indian voices first, by name, on every platform (Android/Chrome, Windows, Apple).
const INDIAN_VOICE_NAMES = /(Google \u0939\u093F\u0928\u094D\u0926\u0940|Google Hindi|Google English India|Heera|Neerja|Swara|Kalpana|Ravi|Hemant|Prabhat|Madhur|Lekha|Rishi|Veena|Aditi|Raveena|Kajal)/i;

function pickVoice(lang, text) {
  const hindi = lang === 'hi' || /[\u0900-\u097F]/.test(text);
  const want = hindi ? 'hi-IN' : 'en-IN';
  const voices = window.speechSynthesis?.getVoices() || [];
  const norm = (v) => (v.lang || '').replace('_', '-');
  const indian = voices.filter(v => /-IN$/i.test(norm(v)) || INDIAN_VOICE_NAMES.test(v.name));
  const voice =
    indian.find(v => norm(v) === want && INDIAN_VOICE_NAMES.test(v.name)) ||
    indian.find(v => norm(v) === want) ||
    // no Indian-English voice on this device: an Indian (Hindi) voice reads English with an Indian accent
    (!hindi && indian.find(v => norm(v) === 'hi-IN')) ||
    indian[0] || null;
  return { lang: voice ? norm(voice) : want, voice };
}

function createEngine() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) return null;
  const rec = new Recognition();
  rec.lang = VX.lang === 'hi' ? 'hi-IN' : 'en-IN';
  rec.continuous = true;
  rec.interimResults = true;
  // Muted: the engine still runs the loop, the synth just stays silent.
  const synth = VX.muted ? silentSynth() : window.speechSynthesis;
  const engine = new VoiceEngine({
    recognition: rec, synth, Utterance: window.SpeechSynthesisUtterance,
    send: sendTurn, onReply, onDoneSpeaking,
    onState: showState,
    onHeard: (text) => showHeard(text),
    voiceFor: pickVoice,
    endOfSpeechMs: 1100,
  });
  rec.onerror = (e) => {
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
      engine.stop();
      showNote(esc(tx('micBlocked')));
      $('startScreen').hidden = false;
    }
  };
  return engine;
}

/** A synth that "speaks" instantly — used when muted so the loop keeps flowing. */
function silentSynth() {
  return {
    speaking: false,
    speak(u) { setTimeout(() => u.onend && u.onend(), 0); },
    cancel() {},
  };
}

function startConversation() {
  $('startScreen').hidden = true;
  if (!VX.engine) VX.engine = createEngine();
  if (!VX.engine) { showNote(esc(tx('noSpeech'))); return; }
  // speaking once inside the tap unlocks speech on mobile browsers
  try { window.speechSynthesis?.speak(new SpeechSynthesisUtterance('')); } catch { /* ignore */ }
  VX.engine.start();
}

function onOrbTap() {
  const e = VX.engine;
  if (!e || e.state === 'idle') return startConversation();
  if (e.state === 'paused') return e.resume();
  if (e.state === 'speaking') { e._bargeIn(); return; }  // tap = "stop talking, I'll speak"
  e.pause();
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
  showState('idle');

  document.querySelector('.vx-lang').addEventListener('click', (e) => {
    const b = e.target.closest('[data-lang]');
    if (!b) return;
    VX.lang = b.dataset.lang;
    store.set(STORE.lang, VX.lang);
    applyLanguage();
  });
  $('muteBtn').addEventListener('click', () => {
    VX.muted = !VX.muted;
    store.set(STORE.muted, VX.muted ? '1' : '0');
    applyMute();
    if (VX.engine) VX.engine.synth = VX.muted ? silentSynth() : window.speechSynthesis;
  });
  $('orb').addEventListener('click', onOrbTap);
  $('startBtn').addEventListener('click', startConversation);

  if (!(window.SpeechRecognition || window.webkitSpeechRecognition)) {
    $('startScreen').hidden = true;
    showNote(esc(tx('noSpeech')));
  }

  try {
    const patients = await apiFetch('GET', '/patients');
    if (!patients.length) {
      showNote(`${esc(tx('noPatient'))} <a class="vx-btn" href="/static/index.html">${esc(tx('openApp'))}</a>`);
      $('startScreen').hidden = true;
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
