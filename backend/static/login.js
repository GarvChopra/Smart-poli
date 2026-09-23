// SmartPoli — login/register page logic.

(function redirectIfAlreadyLoggedIn() {
  const auth = getAuth();
  if (auth && auth.token && auth.user) window.location.href = landingPageFor(auth.user.role);
})();

let mode = 'login';

document.querySelectorAll('.auth-tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    mode = tab.dataset.mode;
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
      const role = document.getElementById('role').value;
      if (!name) throw new Error('Please enter your name.');
      const res = await fetch('/auth/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, name, role }),
      });
      body = await res.json();
      if (!res.ok) throw new Error(body.detail || 'Could not create that account.');
    } else {
      const res = await fetch('/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      body = await res.json();
      if (!res.ok) throw new Error(body.detail || 'Incorrect email or password.');
    }
    setAuth(body.token, body.user);
    window.location.href = landingPageFor(body.user.role);
  } catch (err) {
    errorEl.textContent = err.message || 'Something went wrong. Try again.';
  } finally {
    submitBtn.disabled = false;
  }
});
