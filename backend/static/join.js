// SmartPoli - the page a family invite link opens (/join/<code>).
//
// Opening the link grants nothing. Not signed in: log in or create an account first, then come back here. Signed in:
// the person is told who is asking and what that person will be able to see, and nothing connects until they tap Yes.
// In an ordinary browser tab (not the installed app) they are first offered the app, and can always carry on in the browser.

(function () {
  const card = document.getElementById('joinCard');
  const rawCode = decodeURIComponent((location.pathname.match(/^\/join\/([^/?#]+)/) || [])[1] || '');
  const code = rawCode.replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 12);
  const pretty = code.length === 8 ? `${code.slice(0, 4)}-${code.slice(4)}` : code;
  const standalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const safeGet = (k) => { try { return sessionStorage.getItem(k); } catch (e) { return null; } };
  const safeSet = (k, v) => { try { sessionStorage.setItem(k, v); } catch (e) { /* private mode: the page still works */ } };

  function show(html) { card.innerHTML = html; }

  if (!code) {
    show(`<h2>That link isn't complete</h2><p class="reg-meta">Ask for a new invite, or open SmartPoli and choose <b>Have a code?</b></p>
      <a class="primary join-btn" href="/caregiver">Open SmartPoli</a>`);
    return;
  }

  // Keep the code so it is not lost on the way through login or the install page.
  safeSet('sp_join_code', code);

  const auth = getAuth();
  const here = `/join/${code}`;

  function offerApp() {
    show(`
      <h2>You've been invited to a family on SmartPoli</h2>
      <p class="reg-meta">SmartPoli keeps everyone's medicines in one place and reminds them on time. It works best as an app on your phone.</p>
      <a class="primary join-btn" href="/install?join=${encodeURIComponent(code)}">Get the app</a>
      <button class="ghost join-btn" id="joinBrowser" type="button">Continue in the browser</button>
      <p class="reg-meta" style="margin-top:12px;">Your invite code: <b>${esc(pretty)}</b></p>`);
    document.getElementById('joinBrowser').addEventListener('click', () => { safeSet('sp_join_browser', '1'); start(); });
  }

  function needLogin() {
    show(`
      <h2>You've been invited to a family on SmartPoli</h2>
      <p class="reg-meta">Log in or create an account first. You'll come straight back here to say yes or no.</p>
      <a class="primary join-btn" href="/login?next=${encodeURIComponent(here)}">Log in or create an account</a>
      <p class="reg-meta" style="margin-top:12px;">Your invite code: <b>${esc(pretty)}</b></p>`);
  }

  function problem(title, text) {
    show(`<h2>${esc(title)}</h2><p class="reg-meta">${esc(text)}</p><a class="primary join-btn" href="/caregiver">Open SmartPoli</a>`);
  }

  async function start() {
    if (!auth || !auth.token) { needLogin(); return; }
    let preview;
    try {
      const res = await fetch(`/family/join/${code}`, { headers: { Authorization: `Bearer ${auth.token}` } });
      if (res.status === 401) { clearAuth(); needLogin(); return; }
      preview = await res.json();
      if (res.status === 404) { await sharedCode(); return; }           // not an invite made for you: maybe a code someone made to share THEIR medicines
      if (!res.ok) { problem("This invite can't be used", preview.detail || 'Ask for a new one.'); return; }
    } catch (e) {
      problem("Couldn't check this invite", 'Check your connection and open the link again.');
      return;
    }
    if (preview.own) {
      problem('This is your own invite', 'Send this link or code to the person you are adding. They open it and say yes.');
      return;
    }
    const who = esc(preview.inviter_name);
    show(`
      <h2>${who} wants to look after your medicines</h2>
      <p class="reg-meta">If you say yes, ${who} will be able to:</p>
      <ul class="join-list">
        <li>see your medicines and today's doses</li>
        <li>see which doses you took or missed</li>
        <li>see your notes and emergency card</li>
        <li>add a medicine for you (you'll see who added it)</li>
        <li>send you a reminder</li>
      </ul>
      <p class="reg-meta">${who} can't take doses for you or change your records. You can remove ${who} at any time.</p>
      <button class="primary join-btn" id="joinYes" type="button">Yes, connect</button>
      <button class="ghost join-btn" id="joinNo" type="button">No, not now</button>
      <div id="joinMsg" class="auth-error" role="alert"></div>`);
    document.getElementById('joinNo').addEventListener('click', () => { location.href = '/caregiver'; });
    document.getElementById('joinYes').addEventListener('click', async (e) => {
      e.target.disabled = true;
      try {
        const res = await fetch(`/family/join/${code}`, { method: 'POST', headers: { Authorization: `Bearer ${auth.token}` } });
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || 'Could not connect. Try again.');
        try { sessionStorage.removeItem('sp_join_code'); } catch (er) { /* ignore */ }
        show(`<h2>You're connected</h2><p class="reg-meta">${who} can now look after your medicines with you.</p>
          <a class="primary join-btn" href="/caregiver">Open SmartPoli</a>`);
        setTimeout(() => { location.href = '/caregiver'; }, 1800);
      } catch (err) {
        document.getElementById('joinMsg').textContent = err.message;
        e.target.disabled = false;
      }
    });
  }

  /** A code a person made to share their own medicines with you ("Share my medicines"): you become their caregiver. */
  async function sharedCode() {
    let info;
    try {
      const res = await fetch(`/caregiver/link/${code}`, { headers: { Authorization: `Bearer ${auth.token}` } });
      info = await res.json();
      if (!res.ok) { problem("This code can't be used", res.status === 404 ? "That code doesn't match an invite. Check it and try again." : (info.detail || 'Ask for a new one.')); return; }
    } catch (e) {
      problem("Couldn't check this code", 'Check your connection and try again.');
      return;
    }
    if (info.own) { problem('This is your own code', 'Send it to the person who will look after you.'); return; }
    const who = esc(info.patient_name);
    show(`
      <h2>${who} wants to share their medicines with you</h2>
      <p class="reg-meta">If you connect, you'll be able to:</p>
      <ul class="join-list">
        <li>see ${who}'s medicines and today's doses</li>
        <li>see which doses were taken or missed</li>
        <li>add a medicine for ${who}, and send a reminder</li>
        <li>get an alert when a dose is missed</li>
      </ul>
      <label for="joinNick">What do you call ${who}?</label>
      <input type="text" id="joinNick" maxlength="30" placeholder="e.g. Mum, Dad" autocomplete="off">
      <button class="primary join-btn" id="joinYes" type="button">Yes, connect</button>
      <button class="ghost join-btn" id="joinNo" type="button">No, not now</button>
      <div id="joinMsg" class="auth-error" role="alert"></div>`);
    document.getElementById('joinNo').addEventListener('click', () => { location.href = '/caregiver'; });
    document.getElementById('joinYes').addEventListener('click', async (e) => {
      e.target.disabled = true;
      try {
        const res = await fetch('/caregiver/link/redeem', {
          method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth.token}` },
          body: JSON.stringify({ code, nickname: document.getElementById('joinNick').value.trim() || null }),
        });
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || 'Could not connect. Try again.');
        try { sessionStorage.removeItem('sp_join_code'); } catch (er) { /* ignore */ }
        show(`<h2>You're connected</h2><p class="reg-meta">You can now look after ${who}'s medicines.</p><a class="primary join-btn" href="/caregiver">Open SmartPoli</a>`);
        setTimeout(() => { location.href = '/caregiver'; }, 1800);
      } catch (err) {
        document.getElementById('joinMsg').textContent = err.message;
        e.target.disabled = false;
      }
    });
  }

  if (!standalone && !safeGet('sp_join_browser') && !(auth && auth.token)) offerApp();
  else start();
})();
