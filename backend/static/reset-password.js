(() => {
  const params = new URLSearchParams(location.hash.slice(1));
  const token = params.get('token');
  if (location.hash) history.replaceState(null, '', location.pathname);

  const requestForm = document.getElementById('requestReset');
  const confirmForm = document.getElementById('confirmReset');
  const status = document.getElementById('resetStatus');
  requestForm.hidden = Boolean(token);
  confirmForm.hidden = !token;

  function handle(form, action, data, onSuccess) {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const button = form.querySelector('button');
      if (button.disabled) return;
      const label = button.textContent;
      status.textContent = '';
      status.classList.remove('is-error');
      try {
        const payload = data();
        button.disabled = true;
        button.textContent = button.dataset.pending;
        const response = await fetch(`/auth/password-recovery/${action}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const body = await response.json();
        if (!response.ok) {
          throw new Error(typeof body.detail === 'string' ? body.detail : 'Please check your details and try again.');
        }
        form.reset();
        form.hidden = true;
        status.textContent = body.message;
        if (onSuccess) onSuccess();
        status.focus();
      } catch (error) {
        status.classList.add('is-error');
        status.textContent = error.message || 'Something went wrong. Try again.';
      } finally {
        button.textContent = label;
        button.disabled = false;
      }
    });
  }

  if (token) {
    const link = document.getElementById('newResetLink');
    link.hidden = false;
    handle(confirmForm, 'confirm', () => {
      const password = document.getElementById('newPassword').value;
      if (password !== document.getElementById('repeatPassword').value) {
        throw new Error('The two passwords don’t match.');
      }
      return { token, password };
    }, () => { clearAuth(); link.hidden = true; });
  } else {
    handle(requestForm, 'request', () => ({ email: document.getElementById('resetEmail').value.trim() }));
  }
})();
