// SmartPoli waitlist page. Every piece of text that comes from the server or the visitor goes in with textContent, never as HTML.
const root = document.documentElement;
const API = (document.querySelector('meta[name=api-base]') || {}).content || '';
const TS_KEY = (document.querySelector('meta[name=turnstile-site-key]') || {}).content || '';
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
const $ = (id) => document.getElementById(id);
const CODE_RE = /^[A-HJKMNP-Z2-9]{10}$/, TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode: the page still works */ } },
  del: (k) => { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

// ---- the pill, carried down the page by scroll and the pointer ----
const pill = $('pill');
let px = 0, py = 0, tx = 0, ty = 0;
addEventListener('pointermove', (e) => { px = e.clientX / innerWidth - .5; py = e.clientY / innerHeight - .5; }, { passive: true });
function frame() {
  tx += (px - tx) * .08; ty += (py - ty) * .08;
  const max = Math.max(1, document.documentElement.scrollHeight - innerHeight), p = Math.min(1, scrollY / max);
  const w = pill.offsetWidth, narrow = innerWidth < 1000, swing = Math.cos(p * Math.PI * 3);
  const x = narrow ? (innerWidth - w) / 2 + swing * (innerWidth - w) * .35 : innerWidth * .62 + swing * innerWidth * .2 - w / 2;
  const y = narrow ? innerHeight * .8 - w * .25 + Math.sin(p * Math.PI * 3) * innerHeight * .08 : innerHeight * .5 - w * .25 + Math.sin(p * Math.PI * 3) * innerHeight * .14;
  pill.style.transform = `translate3d(${x + tx * 40}px,${y + ty * 30}px,0) rotate(${-18 + p * 320 + tx * 14}deg)`;
  if (!reduce) requestAnimationFrame(frame);
}
frame();
if (reduce) { addEventListener('scroll', frame, { passive: true }); addEventListener('resize', frame); }

if ('IntersectionObserver' in window) {
  const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), { threshold: .15 });
  document.querySelectorAll('.reveal').forEach((el) => io.observe(el));
} else document.querySelectorAll('.reveal').forEach((el) => el.classList.add('in'));

// ---- the film: plays muted while half of it is on screen, a click turns sound on, leaving the screen stops it and mutes it again ----
const vid = $('vid'), snd = $('sound'), pz = $('pz');
const label = () => { snd.textContent = vid.muted ? 'Tap for sound' : 'Sound on · tap to mute'; snd.setAttribute('aria-pressed', String(!vid.muted)); };
const toggleSound = () => { vid.muted = !vid.muted; if (!vid.muted && vid.paused) vid.play().catch(() => {}); label(); };
vid.addEventListener('play', () => { pz.textContent = 'Pause'; });
vid.addEventListener('pause', () => { pz.textContent = 'Play'; });
pz.addEventListener('click', () => (vid.paused ? vid.play().catch(() => {}) : vid.pause()));
vid.addEventListener('click', toggleSound);
snd.addEventListener('click', toggleSound);
if ('IntersectionObserver' in window) {
  new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) vid.play().catch(() => {}); else { vid.pause(); vid.muted = true; label(); } }), { threshold: .5 }).observe(vid);
}

// ---- joining ----
const emailOk = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v) && v.length <= 254;
let tsWidget = null, tsLoading = null;
function turnstileToken() {                           // invisible check; skipped when no site key is set (local development)
  if (!TS_KEY) return Promise.resolve('');
  tsLoading = tsLoading || new Promise((ok, no) => { const s = document.createElement('script'); s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'; s.onload = ok; s.onerror = no; document.head.appendChild(s); });
  return tsLoading.then(() => new Promise((ok, no) => {
    const bad = () => no(new Error('check'));
    if (tsWidget === null) { const box = document.createElement('div'); box.className = 'ts'; document.body.appendChild(box); tsWidget = turnstile.render(box, { sitekey: TS_KEY, execution: 'execute', appearance: 'interaction-only', callback: ok, 'error-callback': bad }); }
    else turnstile.reset(tsWidget);
    turnstile.execute(tsWidget);
  }));
}
function showJoined(pending) {
  root.classList.add('joined');
  root.classList.toggle('pending', pending);
  scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
}
async function api(path, opts) {
  const res = await fetch(API + path, opts);
  let data = {};
  try { data = await res.json(); } catch { /* non-json error */ }
  return { ok: res.ok, status: res.status, data };
}
function refCode() {
  try {
    const q = new URLSearchParams(location.search).get('r');
    if (q && CODE_RE.test(q)) sessionStorage.setItem('sp_ref', q);
    return sessionStorage.getItem('sp_ref');
  } catch { return null; }
}
const REF = refCode();

document.querySelectorAll('.form').forEach((f) => f.addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = f.querySelector('input[type=email]'), note = f.querySelector('.note'), btn = f.querySelector('.btn');
  if (f.querySelector('.hp').value) return;
  const email = input.value.trim();
  const problem = (msg) => { note.textContent = msg; note.classList.add('err'); };
  if (!emailOk(email)) { problem('Please enter a valid email.'); input.focus(); return; }
  btn.disabled = true; note.classList.remove('err');
  try {
    const token = await turnstileToken();
    const r = await api('/waitlist', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email, website: '', token, ref: REF || undefined }) });
    if (r.ok) { f.reset(); $('pos').textContent = 'One last step: confirm your email.'; showJoined(true); }
    else if (r.status === 429) problem('Lots of people are joining right now. Please try again in a minute.');
    else if (r.status === 503) problem('Signups are paused for a moment. Please come back soon.');
    else problem('That did not work. Please check the email and try again.');
  } catch { problem('Could not reach the server. Please try again.'); }
  btn.disabled = false;
}));

// ---- confirmed: show their own place and share link ----
async function showPlace(code) {
  const r = await api('/waitlist/me', { headers: { 'x-code': code } }).catch(() => null);
  if (!r || !r.ok) { if (r && r.status === 404) store.del('sp_code'); return false; }
  const n = r.data.referrals;
  $('place').textContent = String(r.data.place);
  $('pos').textContent = `You’re number ${r.data.place} · ${r.data.total} on the list`;
  $('confirmedcopy').textContent = 'Share your link: every 3 friends who confirm move you up 10 places.';
  $('linktxt').textContent = `${location.host}/?r=${code}`;
  $('mininote').textContent = `${n} of 3 friends joined. Refer 3 to move up 10 places.`;
  $('barfill').style.width = `${Math.max(6, (n % 3 || (n ? 3 : 0)) / 3 * 100)}%`;
  showJoined(false);
  return true;
}
(async () => {
  const m = /^#c=([A-Za-z0-9_-]+)$/.exec(location.hash);
  if (m) {
    history.replaceState(null, '', location.pathname + location.search);   // the token never stays in the address bar
    if (TOKEN_RE.test(m[1])) {
      const r = await api('/waitlist/confirm', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ token: m[1] }) }).catch(() => null);
      if (r && r.ok && CODE_RE.test(r.data.code)) { store.set('sp_code', r.data.code); await showPlace(r.data.code); return; }
    }
    root.classList.add('joined', 'pending');
    $('pos').textContent = 'That link has expired.';
    document.querySelector('.pending-copy').textContent = 'Join again below and we’ll send a fresh one.';
    return;
  }
  const saved = store.get('sp_code');
  if (saved && CODE_RE.test(saved)) showPlace(saved);
})();

$('copy').addEventListener('click', (e) => {
  const b = e.target;
  try { navigator.clipboard.writeText('https://' + $('linktxt').textContent); b.textContent = 'Copied'; } catch { b.textContent = 'Select and copy'; }
  setTimeout(() => { b.textContent = 'Copy'; }, 1600);
});
$('undo').addEventListener('click', () => {
  root.classList.remove('joined', 'pending');
  document.querySelectorAll('.form input[type=email]').forEach((i) => { i.value = ''; });
  document.querySelector('.hero .form input[type=email]').focus();
});
