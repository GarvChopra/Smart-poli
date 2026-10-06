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

  btn.addEventListener('click', async () => {
    if (standalone) { window.location.href = '/'; return; }
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
    note.textContent = isIOS ? 'Open this page in Safari, then use Share → Add to Home Screen.' : 'SmartPoli may already be installed. Open it from your home screen, or open this page in Chrome.';
  });
})();
