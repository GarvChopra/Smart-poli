// "No internet" popup: shows when the phone goes offline (or the app is opened offline) and goes away by itself when it is back.
(function () {
  let pop = null;

  function build() {
    pop = document.createElement('div');
    pop.className = 'net-pop';
    pop.hidden = true;
    pop.setAttribute('role', 'alertdialog');
    pop.setAttribute('aria-modal', 'true');
    pop.innerHTML = `
      <div class="net-card">
        <div class="net-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 8.8a15 15 0 0 1 20 0"/><path d="M5 12.5a10 10 0 0 1 14 0"/><path d="M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19.5" r=".6" fill="currentColor"/><line x1="3" y1="3" x2="21" y2="21"/></svg>
        </div>
        <h3>No internet connection</h3>
        <p>Check your Wi-Fi or mobile data. SmartPoli will carry on as soon as you are back online.</p>
        <button type="button" id="netRetry">Try again</button>
      </div>`;
    document.body.appendChild(pop);
    pop.querySelector('#netRetry').addEventListener('click', retry);
  }

  function show() {
    if (!document.body) { document.addEventListener('DOMContentLoaded', show, { once: true }); return; }
    if (!pop) build();
    pop.hidden = false;
  }
  function hide() { if (pop) pop.hidden = true; }

  async function retry() {
    const btn = pop && pop.querySelector('#netRetry');
    if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }
    let ok = false;
    try { const r = await fetch('/offline', { cache: 'no-store' }); ok = r.ok; } catch (e) { ok = false; }
    if (btn) { btn.disabled = false; btn.textContent = 'Try again'; }
    if (ok) { hide(); location.reload(); }
  }

  window.addEventListener('offline', show);
  window.addEventListener('online', hide);
  if (!navigator.onLine) show();

  // A request that fails while the browser says we are offline brings the popup up too.
  const realFetch = window.fetch.bind(window);
  window.fetch = function () {
    return realFetch.apply(null, arguments).catch((e) => {
      if (!navigator.onLine) show();
      throw e;
    });
  };
})();
