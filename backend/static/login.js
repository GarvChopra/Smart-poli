// SmartPoli — login/register page logic.

(function redirectIfAlreadyLoggedIn() {
  const auth = getAuth();
  const intendedRole = new URLSearchParams(window.location.search).get('role');
  if (auth && auth.token && auth.user && (!intendedRole || auth.user.role === intendedRole)) {
    window.location.href = landingAfterLogin(auth.user.role, window.location.search);
  }
})();

let mode = 'login';
let selectedRole = new URLSearchParams(window.location.search).get('role') === 'doctor' ? 'doctor' : 'patient';
let doctorEnabled = false;
let recoveryEnabled = false;

const forgotPassword = document.getElementById('forgotPassword');

async function loadRecoveryOption() {
  try {
    const response = await fetch('/auth/password-recovery/config');
    recoveryEnabled = response.ok && (await response.json()).enabled === true;
    forgotPassword.hidden = !recoveryEnabled || mode !== 'login';
  } catch (_) {
    // Login still works if recovery configuration is unavailable.
  }
}
loadRecoveryOption();

function updateAudienceUI() {
  const audience = document.getElementById('authAudience');
  if (!audience) return;
  audience.hidden = !doctorEnabled;
  audience.querySelectorAll('[data-role]').forEach(button => {
    const active = button.dataset.role === selectedRole;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  document.getElementById('authAudienceHint').textContent = selectedRole === 'doctor'
    ? 'Open your clinician workspace and connected patient records.'
    : 'Manage your medicines and care journey.';
}

(async function loadAccountOptions() {
  try { doctorEnabled = !!(await (await fetch('/auth/google/config')).json()).doctor_enabled; }
  catch (_) { doctorEnabled = false; }
  updateAudienceUI();
})();

document.querySelectorAll('.auth-audience-option').forEach(button => button.addEventListener('click', () => {
  selectedRole = button.dataset.role;
  updateAudienceUI();
  document.getElementById('authError').textContent = '';
}));

if (new URLSearchParams(window.location.search).get('deleted') === '1') {
  const note = document.getElementById('authError');
  if (note) { note.style.color = 'var(--teal-dark)'; note.textContent = 'Your account has been deleted. Thank you for using SmartPoli.'; }
}

document.querySelectorAll('.auth-tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    mode = tab.dataset.mode;
    forgotPassword.hidden = !recoveryEnabled || mode !== 'login';
    document.querySelectorAll('.auth-tab').forEach((t) => t.classList.toggle('active', t === tab));
    document.getElementById('registerFields').style.display = mode === 'register' ? 'block' : 'none';
    document.getElementById('passwordHint').style.display = mode === 'register' ? 'block' : 'none';
    document.getElementById('submitBtn').textContent = mode === 'register' ? 'Create account' : 'Log in';
    document.getElementById('authError').textContent = '';
  });
});

document.getElementById('authForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('authError');
  errorEl.textContent = '';
  const email = document.getElementById('email').value.trim();
  const password = document.getElementById('password').value;
  const submitBtn = document.getElementById('submitBtn');
  submitBtn.disabled = true;

  try {
    let body;
    if (mode === 'register') {
      const name = document.getElementById('name').value.trim();
      if (!name) throw new Error('Please enter your name.');
      const res = await fetch('/auth/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, name, role: selectedRole === 'doctor' ? 'doctor' : 'both', remember: document.getElementById('remember').checked }),
      });
      body = await res.json();
      if (!res.ok) throw new Error(body.detail || 'Could not create that account.');
    } else {
      const res = await fetch('/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, remember: document.getElementById('remember').checked }),
      });
      body = await res.json();
      if (!res.ok) throw new Error(body.detail || 'Incorrect email or password.');
    }
    if ((selectedRole === 'doctor') !== (body.user.role === 'doctor')) {
      throw new Error(selectedRole === 'doctor'
        ? 'This is not a clinician account. Switch to Patient or use your clinician account.'
        : 'This is a clinician account. Switch to Clinician to continue.');
    }
    setAuth(body.token, body.user);
    window.location.href = landingAfterLogin(body.user.role, window.location.search);
  } catch (err) {
    errorEl.textContent = err.message || 'Something went wrong. Try again.';
  } finally {
    submitBtn.disabled = false;
  }
});

// ---------------------------------------------------------------- Continue with Google
// Google's own button (Google Identity Services) hands back an ID token; the server verifies it. Shown only when the
// server has a Google client id configured. Someone new gets a regular account straight away (their own medicines, and family if they add any).
(async function setUpGoogle() {
  let cfg;
  try { cfg = await (await fetch('/auth/google/config')).json(); } catch (e) { return; }
  if (!cfg || !cfg.enabled || !cfg.client_id) return;
  const box = document.getElementById('googleBox');
  const errorEl = document.getElementById('authError');

  async function send(credential) {
    const res = await fetch('/auth/google', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ credential, role: selectedRole === 'doctor' ? 'doctor' : 'both', remember: true }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || 'Google sign-in failed. Try again.');
    return body;
  }

  async function onCredential(resp) {
    errorEl.textContent = '';
    try {
      const body = await send(resp.credential);
      if ((selectedRole === 'doctor') !== (body.user.role === 'doctor')) {
        throw new Error(selectedRole === 'doctor'
          ? 'This Google account is not a clinician account. Use your clinician account.'
          : 'This is a clinician account. Switch to Clinician to continue.');
      }
      setAuth(body.token, body.user);
      window.location.href = landingAfterLogin(body.user.role, window.location.search);
    } catch (err) {
      errorEl.textContent = err.message;
    }
  }

  const s = document.createElement('script');
  s.src = 'https://accounts.google.com/gsi/client';
  s.async = true;
  s.onload = () => {
    if (!window.google || !google.accounts || !google.accounts.id) return;
    google.accounts.id.initialize({ client_id: cfg.client_id, callback: onCredential, ux_mode: 'popup' });
    const holder = document.getElementById('googleBtn');
    google.accounts.id.renderButton(holder, {
      theme: 'outline', size: 'large', shape: 'pill', text: 'continue_with', logo_alignment: 'center',
      width: Math.min(360, Math.max(220, holder.clientWidth || box.clientWidth || 300)),
    });
    box.hidden = false;
  };
  document.head.appendChild(s);
  box.hidden = false;
})();
