// SmartPoli Voice — hands-free commands for the whole app.
//
// One tap to begin (browsers require it for the mic), then it keeps listening:
// say a command, get one short answer, and it listens again. Commands are
// understood in the browser by fixed phrase tables (voice-commands.js) — no AI
// service and no API key — and answered from the dashboard this page already
// loaded, so a reply is instant. "Open …" goes straight to that screen.

const VX = {
  patientId: null,
  lang: 'en',
  muted: false,
  contactPhone: null,
  engine: null,
  dash: null,       // the latest /dashboard response — every answer comes from it
};

const STORE = { lang: 'smartpoli_voice_lang', muted: 'smartpoli_voice_muted' };
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode — fine */ } },
};

const TEXT = {
  en: {
    greeting: 'Talk to SmartPoli', subline: 'Say a command — Hindi, English or Hinglish.',
    start: 'Start talking', startHint: 'Tap once to turn on your microphone.',
    idle: 'Tap to start', listening: 'Listening…', thinking: 'Working…', speaking: 'Speaking — tap to interrupt',
    paused: 'Paused — tap to continue',
    noSpeech: "This browser can't listen. Open SmartPoli in Chrome to talk to it.",
    micBlocked: 'Microphone is blocked. Allow it in your browser settings, then tap to start.',
    trySaying: 'Try saying', tomorrow: 'Tomorrow', noneLeft: 'None left', doseWord: 'dose', dosesWord: 'doses', takenWord: 'taken', missedWord: 'missed',
    tNext: 'Next dose', tLeft: 'Left today', tAdh: 'Adherence',
    error: "I couldn't reach SmartPoli. Check your connection and try again.",
    taken: 'Taken', undo: 'Undo', undone: 'Undone — the dose is back to pending.', which: 'Which one did you take?',
    noPatient: 'Create your patient profile in the SmartPoli app first.', openApp: 'Open SmartPoli',
    emTitle: 'Get medical help now', emText: 'Call 112, or ask someone near you to call — the button below does it for you.',
    emCall: 'Call 112', emContact: 'Call my emergency contact', emSymptom: 'Not urgent — check my symptoms', emResume: "I'm okay — keep listening",
  },
  hi: {
    greeting: 'SmartPoli se baat kijiye', subline: 'Command boliye — Hindi, English ya Hinglish.',
    start: 'Baat shuru karein', startHint: 'Microphone chalu karne ke liye ek baar dabaiye.',
    idle: 'Shuru karne ke liye dabaiye', listening: 'Sun raha hoon…', thinking: 'Kar raha hoon…', speaking: 'Bol raha hoon — rokne ke liye dabaiye',
    paused: 'Ruka hua — jaari rakhne ke liye dabaiye',
    noSpeech: 'Yeh browser sun nahi sakta. Baat karne ke liye SmartPoli ko Chrome mein kholiye.',
    micBlocked: 'Microphone band hai. Browser settings mein allow kijiye, phir dabaiye.',
    trySaying: 'Aise boliye', tomorrow: 'Kal', noneLeft: 'Koi nahi', doseWord: 'dawai', dosesWord: 'dawaiyan', takenWord: 'li', missedWord: 'chhooti',
    tNext: 'Agli dawai', tLeft: 'Aaj baaki', tAdh: 'Niyamitata',
    error: 'SmartPoli tak nahi pahunch paaye. Internet check karke dobara boliye.',
    taken: 'Le li', undo: 'Wapas lein', undone: 'Wapas le liya — dawai phir se pending hai.', which: 'Aapne kaun si li?',
    noPatient: 'Pehle SmartPoli app mein apni patient profile banaiye.', openApp: 'SmartPoli kholiye',
    emTitle: 'Abhi doctor ki madad lijiye', emText: '112 par call kijiye, ya paas kisi se call karwaiye — neeche ka button call kar dega.',
    emCall: '112 par call karein', emContact: 'Emergency contact ko call karein', emSymptom: 'Itna gambhir nahi — lakshan jaanchiye', emResume: 'Main theek hoon — sunte rahiye',
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

// ---------------------------------------------------------------- language / controls

function applyLanguage() {
  document.documentElement.lang = VX.lang === 'hi' ? 'hi' : 'en';
  $('greeting').textContent = tx('greeting');
  $('subline').textContent = tx('subline');
  $('startBtn').textContent = tx('start');
  $('startHint').textContent = tx('startHint');
  $('tNextLabel').textContent = tx('tNext');
  $('tLeftLabel').textContent = tx('tLeft');
  $('tAdhLabel').textContent = tx('tAdh');
  tryIndex = 0;
  if ($('tryLine').textContent) rotateTry();
  document.querySelectorAll('.vx-lang button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.lang === VX.lang)));
  if (VX.engine) {
    VX.engine.rec.lang = VX.lang === 'hi' ? 'hi-IN' : 'en-IN';
    showState(VX.engine.state);
  }
  if (VX.dash) renderToday(VX.dash);
}

function setLanguage(lang) {
  VX.lang = lang;
  store.set(STORE.lang, lang);
  applyLanguage();
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

// ---------------------------------------------------------------- one command

/** The patient's words → an answer, right here in the page. The only
 * requests are the ones a command genuinely needs (marking a dose taken). */
async function runCommand(text) {
  showHeard(text);
  if (!VX.dash) await loadToday();
  const intent = VoiceCommands.understand(text);
  const res = VoiceCommands.answer(intent, VX.dash, new Date(), VX.lang);

  const take = res.actions.find(a => a.type === 'take');
  if (take) {
    try {
      const done = await Promise.all(take.doses.map(d => apiFetch('POST', `/doses/${d.dose_id}/take`)));
      take.doses.forEach((d, i) => { d.taken_at = done[i].acted_at; });
      loadToday();
    } catch {
      return { reply: tx('error'), lang: VX.lang, actions: [] };
    }
  }
  const nav = res.actions.find(a => a.type === 'navigate');
  if (nav) {
    // Going straight there is the answer — no waiting for speech to finish.
    res.display = res.reply;
    res.reply = '';
  }
  return res;
}

function onReply(res) {
  document.querySelector('.vx-app').classList.add('is-talking');
  showReply(res.display || res.reply);
  renderActions(res.actions || []);
}

function onDoneSpeaking(res) {
  const nav = (res.actions || []).find(a => a.type === 'navigate');
  if (nav) {
    window.location.href = `/#tab=${encodeURIComponent(nav.screen)}${nav.edit ? '&edit=1' : ''}`;
  }
  if ((res.actions || []).some(a => a.type === 'stop')) VX.engine?.pause();
}

function renderActions(actions) {
  for (const a of actions) {
    if (a.type === 'take') a.doses.forEach(doseTakenCard);
    if (a.type === 'choose_dose') chooseDoseCard(a);
    if (a.type === 'emergency') helpCard();
    if (a.type === 'language') setLanguage(a.lang);
  }
}

// ---------------------------------------------------------------- today at a glance + what to say

const TRY = {
  en: ['“Open dashboard”', '“When is my next medicine?”', '“I took my medicine”', '“Update my details”',
       '“How many doses are left today?”', '“Show my report”', '“Manage caregivers”', '“What is my adherence?”'],
  hi: ['“Dashboard kholo”', '“Agli dawai kab hai?”', '“Maine dawai le li”', '“Meri details badlo”',
       '“Aaj kitni dawai baaki hai?”', '“Report dikhao”', '“Caregiver settings kholo”', '“Kitni dawai chhooti?”'],
};
let tryIndex = 0;

function rotateTry() {
  const el = $('tryLine');
  const list = TRY[VX.lang] || TRY.en;
  el.classList.add('is-fading');
  setTimeout(() => {
    el.innerHTML = `${esc(tx('trySaying'))} <b>${esc(list[tryIndex % list.length])}</b>`;
    el.classList.remove('is-fading');
    tryIndex++;
  }, 350);
}

/** Loads the dashboard once — it's what every spoken answer is read from. */
async function loadToday() {
  if (!VX.patientId) return;
  try {
    VX.dash = await apiFetch('GET', `/patients/${VX.patientId}/dashboard`);
    renderToday(VX.dash);
  } catch { /* the glance is a nice-to-have; commands that need data say they couldn't reach it */ }
}

function renderToday(d) {
  const now = new Date();
  const today = now.toDateString();
  const open = (d.recent_doses || []).filter(x => ['pending', 'snoozed'].includes(x.state) && new Date(x.scheduled_at) >= now);
  const next = open[0] || (d.upcoming_doses || []).find(x => new Date(x.scheduled_at) >= now);
  const left = open.filter(x => new Date(x.scheduled_at).toDateString() === today).length;
  const pct = d.adherence?.adherence_percent;
  $('tNext').textContent = next ? next.medicine_name : tx('noneLeft');
  if (next) {
    const at = new Date(next.scheduled_at);
    const time = at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const tomorrow = new Date(Date.now() + 86400000).toDateString();
    const day = at.toDateString() === today ? '' : at.toDateString() === tomorrow ? `${tx('tomorrow')} `
      : `${at.toLocaleDateString([], { weekday: 'short' })} `;
    $('tNextTime').textContent = day + time;
  } else {
    $('tNextTime').textContent = '';
  }
  $('tLeft').textContent = String(left);
  $('tLeftSub').textContent = tx(left === 1 ? 'doseWord' : 'dosesWord');
  $('tAdh').textContent = pct == null ? '—' : `${Math.round(pct)}%`;
  $('tAdhSub').textContent = `${d.adherence?.taken ?? 0} ${tx('takenWord')} · ${d.adherence?.missed ?? 0} ${tx('missedWord')}`;
  $('today').hidden = false;
}

// ---------------------------------------------------------------- cards

function doseTakenCard(a) {
  const time = a.taken_at ? new Date(a.taken_at + (/Z|[+-]\d\d:\d\d$/.test(a.taken_at) ? '' : 'Z'))
    .toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  const card = addCard(`
    <div class="vx-card-row"><span class="vx-tick">${ICON.tick}</span>
      <div><h3>${esc(a.medicine)}</h3><p>${tx('taken')} ${esc(time)}</p></div>
      <button type="button" class="vx-btn">${tx('undo')}</button></div>`);
  const btn = card.querySelector('button');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      await apiFetch('POST', `/doses/${a.dose_id}/undo`);
      card.querySelector('p').textContent = tx('undone');
      btn.remove();
      loadToday();
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
      loadToday();
    } catch {
      card.querySelectorAll('button').forEach(b => { b.disabled = false; });
    }
  }));
}

/** Call 112 in one tap. Listening pauses until the patient is ready. */
function helpCard() {
  const contact = VX.contactPhone ? `<a class="vx-btn" href="tel:${esc(VX.contactPhone)}">${tx('emContact')}</a>` : '';
  const card = addCard(`
    <h3>${tx('emTitle')}</h3>
    <p>${tx('emText')}</p>
    <a class="vx-help-call" href="tel:112">${tx('emCall')}</a>
    <div class="vx-card-actions">${contact}<a class="vx-btn" href="/#tab=triage">${tx('emSymptom')}</a></div>
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
  // Android Chrome's continuous mode re-sends the whole sentence as every result;
  // one phrase per session is clean there, and the engine restarts listening by itself.
  rec.continuous = !/Android/i.test(navigator.userAgent);
  rec.interimResults = true;
  // Muted: the engine still runs the loop, the synth just stays silent.
  const synth = VX.muted ? silentSynth() : window.speechSynthesis;
  const engine = new VoiceEngine({
    recognition: rec, synth, Utterance: window.SpeechSynthesisUtterance,
    send: runCommand, onReply, onDoneSpeaking,
    onState: showState,
    onHeard: (text) => showHeard(text),
    voiceFor: pickVoice,
    // half duplex: the mic is off while SmartPoli speaks, so a phone speaker can't feed its voice back
    duplex: 'half',
    // Commands are short: a brief pause is enough to know they've finished.
    endOfSpeechMs: 600,
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
  if (e.state === 'speaking') { e.interrupt(); return; }  // tap = "stop talking, I'll speak"
  e.pause();
}

// ---------------------------------------------------------------- boot

async function boot() {
  const auth = getAuth();
  if (!auth || !auth.token || !auth.user) {
    window.location.href = '/login?next=' + encodeURIComponent('/voice');
    return;
  }
  if (auth.user.role !== 'patient') { window.location.href = landingPageFor(auth.user.role); return; }

  VX.lang = store.get(STORE.lang) || ((navigator.language || '').startsWith('hi') ? 'hi' : 'en');
  VX.muted = store.get(STORE.muted) === '1';
  $('openAppLink').innerHTML = ICON.grid;
  applyLanguage();
  applyMute();
  showState('idle');
  rotateTry();
  setInterval(rotateTry, 4500);

  document.querySelector('.vx-lang').addEventListener('click', (e) => {
    const b = e.target.closest('[data-lang]');
    if (b) setLanguage(b.dataset.lang);
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
      showNote(`${esc(tx('noPatient'))} <a class="vx-btn" href="/">${esc(tx('openApp'))}</a>`);
      $('startScreen').hidden = true;
      return;
    }
    VX.patientId = patients[0].id;
    const phone = (patients[0].emergency_contact || '').match(/\+?\d[\d\s-]{6,}\d/);
    VX.contactPhone = phone ? phone[0].replace(/[\s-]/g, '') : null;
    loadToday();
    setInterval(loadToday, 5 * 60 * 1000);
  } catch {
    showNote(esc(tx('error')));
  }

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw-voice.js', { scope: '/voice' }).catch(() => {});
  }
}

boot();
