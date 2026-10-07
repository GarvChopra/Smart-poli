// Takes the opening animation away once the app has loaded (at least 1.2 s so it reads as an animation, at most 3 s).
// Shown once per app launch: moving between SmartPoli's own pages in the same session does not replay it.
(function () {
  const el = document.getElementById('splash');
  if (!el) return;
  const started = Date.now();
  let gone = false;
  function hide() {
    if (gone) return;
    gone = true;
    try { sessionStorage.setItem('sp_splash', '1'); } catch (e) { /* ignore */ }
    el.classList.add('sp-out');
    setTimeout(() => el.remove(), 600);
  }
  function whenReady() { setTimeout(hide, Math.max(0, 1200 - (Date.now() - started))); }
  if (document.readyState === 'complete') whenReady(); else window.addEventListener('load', whenReady, { once: true });
  setTimeout(hide, 3000);
})();
