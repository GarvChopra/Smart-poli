// SmartPoli install page: one big button that really installs the PWA.
(function () {
  const $ = (id) => document.getElementById(id);
  const btn = $('installBtn'), label = $('installLabel'), note = $('installNote');
  const sheet = $('howSheet'), steps = $('howSteps');
  let deferred = null;

  const standalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const ua = navigator.userAgent || '';
  const isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isAndroid = /Android/i.test(ua);

  if (standalone) {                       // already running as the installed app
    label.textContent = 'Open SmartPoli';
    note.textContent = 'SmartPoli is installed on this device.';
  }

  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); deferred = e; });
  window.addEventListener('appinstalled', () => {
    note.textContent = 'Installed! Opening SmartPoli…';
    setTimeout(() => { window.location.href = '/'; }, 1200);
  });

  function showHowTo() {
    let list;
    if (isIOS) {
      list = ['Tap the <b>Share</b> button at the bottom of Safari.', 'Scroll and tap <b>Add to Home Screen</b>.', 'Tap <b>Add</b>. SmartPoli appears on your home screen.'];
    } else if (isAndroid) {
      list = ['Tap the <b>⋮</b> menu at the top right of Chrome.', 'Tap <b>Install app</b> (or <b>Add to Home screen</b>).', 'Tap <b>Install</b>. SmartPoli appears on your home screen.'];
    } else {
      list = ['Look for the <b>install icon</b> at the right end of the address bar.', 'Click it, then click <b>Install</b>.', 'Open SmartPoli from your apps or desktop.'];
    }
    steps.innerHTML = list.map((s) => `<li>${s}</li>`).join('');
    sheet.hidden = false;
  }

  btn.addEventListener('click', async () => {
    if (standalone) { window.location.href = '/'; return; }
    if (deferred) {
      deferred.prompt();
      const choice = await deferred.userChoice.catch(() => null);
      deferred = null;
      note.textContent = choice && choice.outcome === 'accepted' ? 'Installing SmartPoli…' : 'No problem — you can install it any time.';
      return;
    }
    showHowTo();
  });
  $('howClose').addEventListener('click', () => { sheet.hidden = true; });
  sheet.addEventListener('click', (e) => { if (e.target === sheet) sheet.hidden = true; });
})();
