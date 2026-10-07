// SmartPoli install page: one button that installs the app straight away with the browser's own install prompt.
(function () {
  const $ = (id) => document.getElementById(id);
  const btn = $('installBtn'), label = $('installLabel'), note = $('installNote');

  const standalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const ua = navigator.userAgent || '';
  const isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  
  if (standalone) {                       // already running as the installed app
    label.textContent = 'Open SmartPoli';
    note.textContent = 'SmartPoli is installed on this device.';
  }

  // The install offer only appears once the browser has a working service worker for the site: register it now.
  if ('serviceWorker' in navigator && !standalone) {
    navigator.serviceWorker.register('/sw-push.js', { scope: '/' }).catch(function () { /* the button falls back below */ });
  }

  window.addEventListener('appinstalled', () => {
    note.textContent = 'Installed! Opening SmartPoli…';
    setTimeout(() => { window.location.href = '/'; }, 1200);
  });

  /** Resolves with Chrome's install event, or null if it does not arrive within `ms`. */
  function installOffer(ms) {
    return new Promise((resolve) => {
      if (window.__bip) return resolve(window.__bip);
      const done = () => { clearTimeout(t); resolve(window.__bip); };
      const t = setTimeout(() => { window.removeEventListener('bip-ready', done); resolve(null); }, ms);
      window.addEventListener('bip-ready', done, { once: true });
    });
  }


  // ---- iPhone / iPad: Safari has no install button to press, so show the two taps it needs, with a moving arrow.
  const inAppBrowser = /FBAN|FBAV|Instagram|Line\/|MicroMessenger|Snapchat|TikTok|WhatsApp|GSA\//.test(ua);   // these cannot add to the home screen
  function showIosSheet() {
    let sheet = document.getElementById('iosSheet');
    if (sheet) { sheet.hidden = false; return; }
    sheet = document.createElement('div');
    sheet.id = 'iosSheet';
    sheet.className = 'sheet';
    const shareIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="#0A84FF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15V3M8 7l4-4 4 4"/><path d="M6 11H5a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-8a1 1 0 0 0-1-1h-1"/></svg>';
    const plusIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="#222" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M12 8v8M8 12h8"/></svg>';
    sheet.innerHTML = inAppBrowser
      ? `<div class="sheet-card ios-card"><h2>Open in Safari</h2>
           <p class="ios-p">This app cannot be added from here. Open this page in Safari, then tap Install again.</p>
           <button class="install small" type="button" id="iosCopy">Copy link</button>
           <button class="ios-close" type="button" id="iosClose">Close</button></div>`
      : `<div class="sheet-card ios-card"><h2>Add SmartPoli to your Home Screen</h2>
           <div class="ios-step"><span class="ios-ic">${shareIcon}</span><span><b>1.</b> Tap the <b>Share</b> button</span></div>
           <div class="ios-step"><span class="ios-ic">${plusIcon}</span><span><b>2.</b> Tap <b>Add to Home Screen</b>, then <b>Add</b></span></div>
           <div class="ios-arrow" aria-hidden="true">&#8595;</div>
           <button class="ios-close" type="button" id="iosClose">Got it</button></div>`;
    document.body.appendChild(sheet);
    sheet.addEventListener('click', (e) => { if (e.target === sheet) sheet.hidden = true; });
    sheet.querySelector('#iosClose').addEventListener('click', () => { sheet.hidden = true; });
    const copy = sheet.querySelector('#iosCopy');
    if (copy) copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(location.href); copy.textContent = 'Link copied ✓'; } catch (e) { copy.textContent = 'Press and hold the address to copy'; }
    });
  }

  // On an iPhone the sheet opens by itself, so nobody has to hunt for how to install.
  if (isIOS && !standalone) setTimeout(showIosSheet, 1000);

  btn.addEventListener('click', async () => {
    if (standalone) { window.location.href = '/'; return; }
    if (isIOS) { showIosSheet(); return; }
    let offer = window.__bip;                                    // usually already here: prompt at once, inside the tap
    if (!offer && !isIOS) {
      btn.disabled = true;
      const original = label.textContent;
      label.textContent = 'Getting ready…';
      offer = await installOffer(3000);                          // short wait (a tap's permission to prompt lasts a few seconds)
      label.textContent = original;
      btn.disabled = false;
    }
    if (offer) {
      window.__bip = null;                                       // an offer can be used once
      offer.prompt();
      const choice = await offer.userChoice.catch(() => null);
      note.textContent = choice && choice.outcome === 'accepted' ? 'Installing SmartPoli…' : 'No problem — you can install it any time.';
      return;
    }
    // No install offer from the browser: say so in one line (never a how-to). This is the case on iPhone Safari, or when
    // the app is already installed on this phone.
    note.textContent = 'SmartPoli may already be installed. Open it from your home screen, or open this page in Chrome.';
  });
})();
