// SmartPoli guided tour. Each "Next" moves to the real screen and spotlights the thing being explained, so a first-time user
// learns where everything is: add medicines, the dashboard, reminders, missed doses, medicines that clash, the safety centre.
// Shown once after the first-login popup, and any time from the menu ("How SmartPoli works").
(function () {
  const DONE_KEY = 'smartpoli_tour_done';

  const $ = (sel) => document.querySelector(sel);
  const byText = (sel, text) => Array.from(document.querySelectorAll(sel)).find((n) => n.textContent.trim().startsWith(text));

  // tab: the screen to open first. target: what to spotlight there (a function, so it is looked up after the screen draws).
  // sample: a picture of something that only appears when it is needed (a missed dose, a clash), shown instead of a spotlight.
  const STEPS = [
    { title: 'Welcome to SmartPoli', text: 'A quick look at how SmartPoli keeps your medicines safe and on time.', icon: 'pill' },
    { tab: 'prescriptions', target: () => $('#manualBtn'), title: 'Add your medicines here',
      text: 'Tap Enter manually, type the medicine and set the time, like an alarm.' },
    { tab: 'dashboard', target: () => $('#view-dashboard .card'), title: 'Your day at a glance',
      text: 'The doses due today are listed here. Tap Take or Skip. If a dose is overdue, we ask when you open the app.' },
    { tab: 'settings', target: () => byText('#view-settings summary', 'Notifications'), title: 'Reminders on your phone',
      text: 'Turn on notifications and we remind you on time, even when the app is closed.' },
    { tab: 'dashboard', sample: 'missed', title: 'We notice missed doses',
      text: 'We tell you what to do, never to double up. Your caregiver can be alerted too.' },
    { tab: 'dashboard', sample: 'clash', title: 'We catch medicines that clash',
      text: 'We check your medicines against official labels and suggest a safe gap, or let you remove one.' },
    { tab: 'safety', target: () => $('#view-safety .card'), title: 'Safety centre',
      text: 'Timing, interaction and food checks in one place. AI help is marked as an estimate.' },
    { tab: 'dashboard', title: 'You are all set', text: 'Add your first medicine to begin. Reopen this tour any time from the menu.', icon: 'checkCircle', last: true },
  ];

  const SAMPLES = {
    missed: `<div class="tour-sample"><div class="tour-sample-icon amber">!</div><strong>You missed Creatine</strong>
      <span>Was due 6:30 PM</span><div class="tour-sample-note">Never take a double dose to catch up.</div>
      <div class="tour-sample-btns"><span class="b p">I took it</span><span class="b">Skip it</span></div></div>`,
    clash: `<div class="tour-sample"><div class="tour-sample-icon rose">⚠</div><strong>Don’t take these together</strong>
      <span>Ibuprofen + Aspirin</span><div class="tour-sample-note rose">Keep them at least 4 hours apart.</div>
      <div class="tour-sample-btns"><span class="b p">Shift one</span><span class="b">Remove one</span></div></div>`,
  };

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function done() { try { localStorage.setItem(DONE_KEY, '1'); } catch (e) { /* ignore */ } }
  function wasShown() { try { return localStorage.getItem(DONE_KEY) === '1'; } catch (e) { return false; } }
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  async function findTarget(step) {
    if (!step.target) return null;
    for (let i = 0; i < 20; i++) {                                  // the screen may still be loading its data
      const el = step.target();
      if (el && el.getBoundingClientRect().height > 0) return el;
      await wait(150);
    }
    return null;
  }

  function show() {
    document.querySelectorAll('.safety-modal-overlay').forEach((n) => { if (n.id !== 'firstProfileOverlay') n.remove(); });
    const root = document.createElement('div');
    root.className = 'tour-root';
    root.innerHTML = '<div class="tour-block"></div><div class="tour-spot" hidden></div><div class="tour-tip" role="dialog" aria-modal="true" aria-live="polite"></div>';
    document.body.appendChild(root);
    const spot = root.querySelector('.tour-spot');
    const tip = root.querySelector('.tour-tip');
    let i = 0;
    let token = 0;

    function close() { done(); window.removeEventListener('resize', place); root.remove(); }
    let current = null;

    function place() {
      if (!current) { spot.hidden = true; tip.classList.add('center'); tip.style.cssText = ''; return; }
      const r = current.getBoundingClientRect();
      const pad = 8, gap = 12, vh = window.innerHeight;
      spot.hidden = false;
      // the spotlight never grows past the screen: a tall section is lit down to the edge of the card instead
      spot.style.cssText = `top:${r.top - pad}px;left:${r.left - pad}px;width:${r.width + pad * 2}px;height:${Math.min(r.height + pad * 2, vh - r.top + pad)}px;`;
      tip.classList.remove('center');
      const tipH = tip.offsetHeight || 220;
      const spaceBelow = vh - (r.bottom + pad + gap), spaceAbove = r.top - pad - gap;
      let top;
      if (spaceBelow >= tipH) top = r.bottom + pad + gap;                 // under the highlighted thing
      else if (spaceAbove >= tipH) top = r.top - pad - gap - tipH;        // above it
      else top = vh - tipH - gap;                                         // a tall section on a small phone: card sits at the bottom
      tip.style.cssText = `top:${Math.max(gap, top)}px;`;
    }

    async function go(n) {
      i = n;
      const my = ++token;
      const s = STEPS[i];
      if (s.tab) { const b = $(`nav.pill-nav button[data-tab="${s.tab}"]`); if (b) b.click(); }
      current = null;
      draw(s, true);
      const el = await findTarget(s);
      if (my !== token) return;
      current = el;
      if (el) el.scrollIntoView({ block: el.getBoundingClientRect().height > window.innerHeight * 0.4 ? 'start' : 'center', behavior: 'smooth' });
      await wait(el ? 350 : 0);
      if (my !== token) return;
      draw(s, false);
    }

    function draw(s, loading) {
      const icon = s.icon && typeof ICONS !== 'undefined' ? ICONS[s.icon] : '';
      tip.innerHTML = `
        ${icon ? `<div class="tour-icon" aria-hidden="true">${icon}</div>` : ''}
        <div class="tour-step">${i + 1} of ${STEPS.length}</div>
        <h3>${esc(s.title)}</h3>
        <p>${esc(s.text)}</p>
        ${s.sample ? SAMPLES[s.sample] : ''}
        <div class="tour-bar"><span style="width:${Math.round(((i + 1) / STEPS.length) * 100)}%"></span></div>
        <div class="tour-actions">
          ${i === 0 ? '<button class="ghost" type="button" id="tourSkip">Skip tour</button>' : '<button class="ghost" type="button" id="tourBack">Back</button>'}
          <button class="primary" type="button" id="tourNext">${s.last ? 'Get started' : 'Next'}</button>
        </div>`;
      tip.querySelector('#tourNext').addEventListener('click', () => { if (s.last) close(); else go(i + 1); });
      const back = tip.querySelector('#tourBack');
      if (back) back.addEventListener('click', () => go(i - 1));
      const skip = tip.querySelector('#tourSkip');
      if (skip) skip.addEventListener('click', close);
      place();
      if (!loading) tip.querySelector('#tourNext').focus({ preventScroll: true });
    }

    window.addEventListener('resize', place);
    go(0);
  }

  /** Right after the first profile is saved: show the tour once. */
  function showFirstTime() { if (!wasShown()) show(); }

  window.SmartTour = { show, showFirstTime, wasShown, STEPS };
})();
