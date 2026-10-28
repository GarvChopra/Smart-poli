// SmartPoli — shared auth client for all three apps (patient/caregiver/doctor).
// No build step: loaded as a plain <script> before each app's own app.js.
//
// The backend is the only thing that ever decides a user's role (auth.py) —
// this file just carries the token it issues and attaches it to every
// request. There is no client-side "pretend to be a doctor" switch any
// more; the page you land on is a consequence of what /auth/login returned,
// not something the page itself claims.

const AUTH_KEY = 'smartpoli_auth'; // { token, user: {id, email, name, role, patient_ids} }

function getAuth() {
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function setAuth(token, user) {
  try {
    localStorage.setItem(AUTH_KEY, JSON.stringify({ token, user }));
  } catch (e) { /* private browsing / storage blocked — session still works for this page load */ }
}

function clearAuth() {
  try { localStorage.removeItem(AUTH_KEY); } catch (e) { /* ignore */ }
}

// A person's account holds their own medicines ("You") and the family they look after, so patient, caregiver and the
// newer "both" all use the same pages. Doctors keep their own. What anyone may SEE is decided by the server per record.
const PERSON_ROLES = ['patient', 'caregiver', 'both'];

function landingPageFor(role) {
  if (role === 'doctor') return '/doctor';
  return '/caregiver';          // the Family home: You first, then the people you look after
}

/** `?next=` after login (e.g. the voice page) — only the app's own
 * pages, never another origin, so the login page can't be an open redirect. */
function safeNextPath(raw) {
  return typeof raw === 'string' && /^\/(?:voice|doctor|caregiver|join\/[A-Za-z0-9-]{4,16})?(?:[?#][^\s]*)?$/.test(raw) ? raw : null;
}

function landingAfterLogin(role, search) {
  const next = safeNextPath(new URLSearchParams(search || '').get('next'));
  return (PERSON_ROLES.includes(role) && next) || landingPageFor(role);
}

/**
 * WhatsApp magic link support (whatsapp_bot.py's web_dashboard_link()): a
 * WhatsApp-only patient never sets a password, so instead of a login form
 * they get a link like /?token=<jwt>. This consumes that
 * token into normal localStorage auth before requireRole() ever runs, then
 * strips it from the URL so it doesn't linger in browser history.
 *
 * A synchronous XHR is used deliberately: requireRole() runs synchronously
 * at the very top of every page's own script (`const currentUser =
 * requireRole(...)`), before any async code would have a chance to
 * complete — this is the one, rare, one-time-per-magic-link request where
 * blocking the main thread briefly is the simplest correct option, rather
 * than restructuring every page's boot sequence around an async check that
 * runs on every single page load.
 */
(function consumeMagicLinkToken() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token');
  if (!token) return;

  try {
    const xhr = new XMLHttpRequest();
    xhr.open('GET', '/auth/me', false); // false = synchronous, see docstring above
    xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.send(null);
    if (xhr.status === 200) {
      setAuth(token, JSON.parse(xhr.responseText));
    }
  } catch (e) { /* malformed/expired token — fall through, requireRole() will redirect to login */ }

  params.delete('token');
  const cleanUrl = window.location.pathname + (params.toString() ? `?${params}` : '');
  window.history.replaceState({}, document.title, cleanUrl);
})();

/** Call at the top of every app page. Redirects to login if not authenticated
 * as the expected role, and returns the current user otherwise. */
/** A session that is a third used up is swapped for a fresh one of the same kind (Remember me or not), quietly, so someone
 * who keeps using SmartPoli is never signed out. Best effort: if it fails the current session simply runs its course. */
function refreshSessionIfOld(auth) {
  try {
    const part = auth.token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const claims = JSON.parse(atob(part));
    const life = claims.exp - claims.iat;
    if (!(life > 0) || Date.now() / 1000 - claims.iat <= life / 3) return;
    fetch('/auth/refresh', { method: 'POST', headers: { Authorization: `Bearer ${auth.token}` } })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j && j.token && j.user) setAuth(j.token, j.user); })
      .catch(() => null);
  } catch (e) { /* unreadable token: nothing to refresh */ }
}

/** "Set a password" (an account made with Google has none) or "Change password": the same account, so the email + password
 * form and Continue with Google both open it. */
function openSetPassword() {
  const auth = getAuth();
  const has = !!(auth && auth.user && auth.user.has_password);
  const m = showSafetyModal(`
    <h3>${has ? 'Change password' : 'Set a password'}</h3>
    <p class="reg-meta">${has ? '' : 'After this you can log in with your email and this password, or with Google. It is the same account.'}</p>
    ${has ? '<label for="pwOld">Current password</label><input id="pwOld" type="password" autocomplete="current-password">' : ''}
    <label for="pwNew">New password</label>
    <input id="pwNew" type="password" autocomplete="new-password">
    <div class="reg-meta">At least 8 characters.</div>
    <div id="pwMsg" class="auth-error" role="alert"></div>
    <div class="sm-actions"><button class="primary small" id="pwGo" type="button">Save</button><button class="ghost small" id="pwNo" type="button">Cancel</button></div>`);
  m.querySelector('#pwNo').addEventListener('click', () => m.remove());
  m.querySelector('#pwGo').addEventListener('click', async (e) => {
    const next = m.querySelector('#pwNew').value;
    if (next.length < 8) { m.querySelector('#pwMsg').textContent = 'Use at least 8 characters.'; return; }
    e.target.disabled = true;
    try {
      const res = await fetch('/auth/set-password', {
        method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth.token}` },
        body: JSON.stringify({ new_password: next, current_password: has ? m.querySelector('#pwOld').value : null }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not save the password.');
      auth.user.has_password = true; setAuth(auth.token, auth.user);
      m.querySelector('.safety-modal').innerHTML = '<h3>Password saved</h3><p class="reg-meta">You can now log in with your email and password, or with Google.</p><div class="sm-actions"><button class="primary small" id="pwOk" type="button">Done</button></div>';
      m.querySelector('#pwOk').addEventListener('click', () => { m.remove(); if (typeof refreshAccountLabels === 'function') refreshAccountLabels(); });
    } catch (err) { m.querySelector('#pwMsg').textContent = err.message; e.target.disabled = false; }
  });
}

/** "Delete my account": says what goes, asks for the password (or the email, for a Google account), then deletes. */
function openDeleteAccount() {
  const auth = getAuth();
  const withPassword = !auth || !auth.user || auth.user.has_password !== false;
  const esc2 = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const m = showSafetyModal(`
    <h3>Delete my account</h3>
    <p class="reg-meta">This removes your account and everything in it: your medicines, doses, reminders, notes and emergency card. Anyone who looks after you loses access. <b>It cannot be undone.</b></p>
    <p class="reg-meta">People you look after are not deleted. Only your link to them goes.</p>
    <label for="delProof">${withPassword ? 'Enter your password to confirm' : 'Type your email address to confirm'}</label>
    <input id="delProof" type="${withPassword ? 'password' : 'text'}" autocomplete="${withPassword ? 'current-password' : 'off'}" placeholder="${withPassword ? '' : esc2((auth && auth.user && auth.user.email) || '')}">
    <div id="delMsg" class="auth-error" role="alert"></div>
    <div class="sm-actions"><button class="primary small danger" id="delGo" type="button">Delete everything</button><button class="ghost small" id="delNo" type="button">Cancel</button></div>`);
  m.querySelector('#delNo').addEventListener('click', () => m.remove());
  m.querySelector('#delGo').addEventListener('click', async (e) => {
    const proof = m.querySelector('#delProof').value;
    if (!proof.trim()) { m.querySelector('#delMsg').textContent = withPassword ? 'Enter your password.' : 'Type your email address.'; return; }
    e.target.disabled = true;
    try {
      const res = await fetch('/auth/delete-account', {
        method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth.token}` },
        body: JSON.stringify(withPassword ? { password: proof } : { confirm_email: proof }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not delete the account. Please try again.');
      clearAuth();
      window.location.href = '/login?deleted=1';
    } catch (err) {
      m.querySelector('#delMsg').textContent = err.message;
      e.target.disabled = false;
    }
  });
}

function requireRole(expectedRole) {
  const auth = getAuth();
  if (!auth || !auth.token || !auth.user) {
    window.location.href = '/login';
    return null;
  }
  const same = auth.user.role === expectedRole || (PERSON_ROLES.includes(expectedRole) && PERSON_ROLES.includes(auth.user.role));
  if (!same) {
    // Logged in, just on the wrong app — send them to the page that's theirs.
    window.location.href = landingPageFor(auth.user.role);
    return null;
  }
  refreshSessionIfOld(auth);
  return auth.user;
}

async function logout() {
  const auth = getAuth();
  try {
    if (auth && auth.token) {
      // A shared phone must stop showing this person's dose reminders, and the
      // session token must stop working server-side (a JWT is otherwise valid
      // until it expires). Both are best effort - leaving the page must not hang on them.
      if (typeof disablePushReminders === 'function' && auth.user && auth.user.patient_ids) {
        for (const pid of auth.user.patient_ids) await disablePushReminders(pid);
      }
      await fetch('/auth/logout', { method: 'POST', headers: { Authorization: `Bearer ${auth.token}` }, keepalive: true });
    }
  } catch (e) { /* offline or already expired: still log out locally */ }
  clearAuth();
  window.location.href = '/login';
}

/** Same shape as the old local api() helper each page used to define, but
 * attaches the bearer token and handles a 401 by bouncing to login instead
 * of leaving the page stuck on a silently-failed fetch. */
async function apiFetch(method, path, body) {
  const auth = getAuth();
  const opts = { method, headers: {} };
  if (auth && auth.token) opts.headers['Authorization'] = `Bearer ${auth.token}`;
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    clearAuth();
    window.location.href = '/login';
    throw new Error('Session expired — please log in again.');
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const err = new Error((typeof detail.detail === 'string' && detail.detail) || `${method} ${path} failed (${res.status})`);
    err.status = res.status;
    err.info = detail;          // structured reason (code, earliest, source ...) when the server gives one
    throw err;
  }
  return res.status === 204 ? null : res.json();
}
