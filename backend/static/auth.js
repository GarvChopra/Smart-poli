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

function landingPageFor(role) {
  if (role === 'doctor') return '/static/doctor.html';
  if (role === 'caregiver') return '/static/caregiver.html';
  return '/static/index.html';
}

/**
 * WhatsApp magic link support (whatsapp_bot.py's web_dashboard_link()): a
 * WhatsApp-only patient never sets a password, so instead of a login form
 * they get a link like /static/index.html?token=<jwt>. This consumes that
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
function requireRole(expectedRole) {
  const auth = getAuth();
  if (!auth || !auth.token || !auth.user) {
    window.location.href = '/static/login.html';
    return null;
  }
  if (auth.user.role !== expectedRole) {
    // Logged in, just on the wrong app — send them to the page that's theirs.
    window.location.href = landingPageFor(auth.user.role);
    return null;
  }
  return auth.user;
}

function logout() {
  clearAuth();
  window.location.href = '/static/login.html';
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
    window.location.href = '/static/login.html';
    throw new Error('Session expired — please log in again.');
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${method} ${path} failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}
