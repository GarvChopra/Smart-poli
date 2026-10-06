// SmartPoli — feedback, shared by the patient app and the caregiver portal.
//
//   SmartFeedback.renderFeedbackPage(root)   the full Feedback page (animated header, stars with a reacting face, topic tiles,
//                                             message, contact switch, thank-you with confetti, your earlier feedback)
//   SmartFeedback.init({ goFeedback })        counts app opens and, only sometimes, shows the "Enjoying SmartPoli?" popup
//
// The popup rules are pure functions (recordOpen / shouldPrompt / after*) so they are tested in Node
// (backend/tests/feedback_logic.test.js). Everything the server sends back is escaped before it is drawn.

(function (root) {
  const DAY = 86400000;
  const KEY = 'smartpoli_rate_v1';
  const CATEGORIES = [['problem', 'Problem', '🛠️'], ['idea', 'Idea', '💡'], ['praise', 'Praise', '💚'], ['other', 'Other', '💬']];
  const STAR_WORDS = ['Tap a star', 'Poor', 'Fair', 'Good', 'Very good', 'Excellent'];
  const FACES = ['💬', '😞', '😕', '🙂', '😊', '🤩'];

  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ---------------------------------------------------------------- when may the popup appear? (pure)

  const dayKey = (ms) => new Date(ms).toISOString().slice(0, 10);

  /** Note that the app was opened (one entry per calendar day, newest 30 kept). */
  function recordOpen(st, now) {
    const s = Object.assign({ first: now, days: [], snoozeUntil: 0, notNow: 0, doneAt: 0, never: false }, st || {});
    const d = dayKey(now);
    if (!s.days.includes(d)) s.days = s.days.concat(d).slice(-30);
    return s;
  }

  /** Only sometimes: 7+ days since first use, 3+ different days of use, not snoozed, not asked three times already,
   * not within 90 days of sending feedback, and never after "don't ask again". */
  function shouldPrompt(st, now) {
    if (!st || st.never) return false;
    if (st.doneAt && now - st.doneAt < 90 * DAY) return false;
    if (st.snoozeUntil > now) return false;
    if (st.notNow >= 3) return false;
    if (now - st.first < 7 * DAY) return false;
    return st.days.length >= 3;
  }

  const afterNotNow = (st, now) => Object.assign({}, st, { notNow: (st.notNow || 0) + 1, snoozeUntil: now + 14 * DAY });
  const afterSent = (st, now) => Object.assign({}, st, { doneAt: now });
  const afterNever = (st) => Object.assign({}, st, { never: true });

  const promptQuestion = (rating) => (rating >= 4 ? 'What do you like about SmartPoli?' : 'Sorry about that. What went wrong?');
  const categoryForRating = (rating) => (rating >= 4 ? 'praise' : 'problem');

  // ---------------------------------------------------------------- stored state (per device)

  function load() { try { return JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) { return null; } }
  function save(st) { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) { /* storage blocked: the popup just may not remember */ } }

  // ---------------------------------------------------------------- pieces

  /** The animated header picture: a speech bubble with a heart, drifting stars and sparkles. */
  const HERO_SVG = `
    <svg class="fb-art" viewBox="0 0 220 150" aria-hidden="true">
      <circle class="fb-blob b1" cx="60" cy="80" r="46"/><circle class="fb-blob b2" cx="170" cy="60" r="34"/>
      <g class="fb-bubble"><path d="M48 34h96a16 16 0 0 1 16 16v44a16 16 0 0 1-16 16H98l-24 22v-22H48a16 16 0 0 1-16-16V50a16 16 0 0 1 16-16Z" fill="#fff" stroke="#12876F" stroke-width="3"/>
        <path class="fb-heart" d="M96 88c-20-13-24-26-14-33 6-4 12-1 14 4 2-5 8-8 14-4 10 7 6 20-14 33Z" fill="#E8604C"/></g>
      <g class="fb-star s1"><path d="M178 20l4 9 10 1-7 7 2 10-9-5-9 5 2-10-7-7 10-1Z" fill="#E8A317"/></g>
      <g class="fb-star s2"><path d="M26 22l3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1Z" fill="#E8A317"/></g>
      <g class="fb-star s3"><path d="M190 100l3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1Z" fill="#7FD1BC"/></g>
      <g class="fb-plus p1"><path d="M10 100v12M4 106h12" stroke="#12876F" stroke-width="3" stroke-linecap="round"/></g>
      <g class="fb-plus p2"><path d="M200 52v10M195 57h10" stroke="#12876F" stroke-width="3" stroke-linecap="round"/></g>
    </svg>`;

  function starsHtml(value) {
    return `<div class="fb-stars" role="radiogroup" aria-label="Rating">${[1, 2, 3, 4, 5].map((n) =>
      `<button type="button" class="fb-star-btn${n <= value ? ' on' : ''}" data-star="${n}" role="radio" aria-checked="${n === value}" aria-label="${n} star${n > 1 ? 's' : ''}">★</button>`).join('')}</div>`;
  }

  function wireStars(box, onPick) {
    box.querySelectorAll('[data-star]').forEach((b) => b.addEventListener('click', () => {
      const n = Number(b.dataset.star);
      box.querySelectorAll('[data-star]').forEach((x) => { x.classList.toggle('on', Number(x.dataset.star) <= n); x.setAttribute('aria-checked', String(Number(x.dataset.star) === n)); });
      onPick(n);
    }));
  }

  /** The big face above the stars: changes with the rating and pops. */
  function setFace(el, rating) {
    if (!el) return;
    el.textContent = FACES[rating] || FACES[0];
    el.classList.remove('pop');
    void el.offsetWidth;                                   // restart the animation
    el.classList.add('pop');
  }

  function confettiHtml() {
    const colors = ['#12876F', '#E8A317', '#E8604C', '#7FD1BC', '#5B8DEF'];
    return `<div class="fb-confetti" aria-hidden="true">${Array.from({ length: 26 }, (_, i) => {
      const a = (i / 26) * Math.PI * 2, d = 70 + (i % 5) * 18;
      return `<i style="--x:${Math.round(Math.cos(a) * d)}px;--y:${Math.round(Math.sin(a) * d - 20)}px;--r:${(i * 47) % 360}deg;--c:${colors[i % colors.length]};--t:${(i % 6) * 0.04}s"></i>`;
    }).join('')}</div>`;
  }

  const thanksHtml = (extra) => `<div class="fb-thanks">${confettiHtml()}
    <svg class="fb-check-svg" viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="23" fill="none" stroke="#12876F" stroke-width="3"/><path d="M15 27l8 8 14-16" fill="none" stroke="#12876F" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>
    <h3>Thank you!</h3><p class="reg-meta">We read every message.</p>${extra || ''}</div>`;

  // ---------------------------------------------------------------- the full page

  function renderFeedbackPage(rootEl) {
    let rating = 0, category = 'other';
    rootEl.innerHTML = `
      <div class="fb-hero">
        <div class="fb-hero-text"><h2>We’d love to hear from you</h2>
          <p>Tell us what is working and what is not. We read every message and it makes SmartPoli better for everyone.</p></div>
        ${HERO_SVG}
      </div>
      <div class="card fb-card" id="fbCard">
        <div class="fb-label">How would you rate SmartPoli?</div>
        <div class="fb-face" id="fbFace" aria-hidden="true">${FACES[0]}</div>
        ${starsHtml(0)}
        <div class="fb-word" id="fbWord" aria-live="polite">${STAR_WORDS[0]}</div>

        <div class="fb-label">This is about</div>
        <div class="fb-tiles" id="fbCats">${CATEGORIES.map(([v, l, icon]) =>
          `<button type="button" class="fb-tile${v === 'other' ? ' on' : ''}" data-cat="${v}"><span class="fb-tile-icon">${icon}</span>${l}</button>`).join('')}</div>

        <div class="fb-label">Your message</div>
        <textarea id="fbMsg" maxlength="1000" rows="5" placeholder="What happened, or what would you like us to add?"></textarea>
        <div class="fb-row"><span class="fb-count"><span id="fbCount">0</span>/1000</span></div>

        <label class="fb-switch"><input type="checkbox" id="fbContact"><span class="fb-switch-track"><i></i></span><span>You can contact me about this</span></label>
        <div class="fb-err" id="fbErr" role="alert"></div>
        <button class="primary fb-send" id="fbSend"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7Z"/></svg>Send feedback</button>
      </div>
      <div id="fbMine"></div>`;
    const card = rootEl.querySelector('#fbCard');
    wireStars(card, (n) => { rating = n; card.querySelector('#fbWord').textContent = STAR_WORDS[n]; setFace(card.querySelector('#fbFace'), n); });
    card.querySelectorAll('[data-cat]').forEach((b) => b.addEventListener('click', () => {
      category = b.dataset.cat;
      card.querySelectorAll('[data-cat]').forEach((x) => x.classList.toggle('on', x === b));
    }));
    const msg = card.querySelector('#fbMsg');
    msg.addEventListener('input', () => { card.querySelector('#fbCount').textContent = msg.value.length; });
    card.querySelector('#fbSend').addEventListener('click', async () => {
      const err = card.querySelector('#fbErr');
      err.textContent = '';
      if (!rating) { err.textContent = 'Please choose a star rating first.'; return; }
      const btn = card.querySelector('#fbSend');
      btn.disabled = true;
      try {
        await apiFetch('POST', '/feedback', { rating, category, message: msg.value, contact_ok: card.querySelector('#fbContact').checked, source: 'page' });
        save(afterSent(recordOpen(load(), Date.now()), Date.now()));
        card.innerHTML = thanksHtml('<button class="ghost" id="fbAgain">Send more feedback</button>');
        card.querySelector('#fbAgain').addEventListener('click', () => renderFeedbackPage(rootEl));
        loadMine(rootEl);
      } catch (e) {
        err.textContent = e.message || 'Could not send. Please try again.';
        btn.disabled = false;
      }
    });
    loadMine(rootEl);
  }

  async function loadMine(rootEl) {
    const box = rootEl.querySelector('#fbMine');
    if (!box) return;
    let items = [];
    try { items = (await apiFetch('GET', '/feedback/mine')).items || []; } catch (e) { return; }
    if (!items.length) { box.innerHTML = ''; return; }
    const label = Object.fromEntries(CATEGORIES.map(([v, l, icon]) => [v, `${icon} ${l}`]));
    box.innerHTML = `<h3 class="fb-earlier">Your earlier feedback</h3>` + items.map((f) => `
      <div class="card fb-item">
        <div class="fb-item-head"><span class="fb-stars-ro" aria-label="${esc(f.rating)} out of 5">${'★'.repeat(f.rating)}${'☆'.repeat(5 - f.rating)}</span>
          <span class="mi-chip">${esc(label[f.category] || f.category)}</span>
          <span class="reg-meta">${esc(new Date(f.created_at).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' }))}</span></div>
        ${f.message ? `<div class="fb-item-body">${esc(f.message)}</div>` : ''}
      </div>`).join('');
  }

  // ---------------------------------------------------------------- the occasional popup

  function showRatePrompt(goFeedback) {
    const overlay = document.createElement('div');
    overlay.className = 'safety-modal-overlay fb-modal';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.innerHTML = `<div class="safety-modal" id="fbBox"></div>`;
    document.body.appendChild(overlay);
    const box = overlay.querySelector('#fbBox');
    const close = () => overlay.remove();

    const footer = () => `<div class="fb-foot"><button type="button" class="fb-link" data-act="later">Not now</button><span>·</span>
      <button type="button" class="fb-link" data-act="never">Don’t ask again</button></div>`;
    const wireFooter = () => {
      box.querySelector('[data-act="later"]').addEventListener('click', () => { save(afterNotNow(load() || recordOpen(null, Date.now()), Date.now())); close(); });
      box.querySelector('[data-act="never"]').addEventListener('click', () => { save(afterNever(load() || recordOpen(null, Date.now()))); close(); });
    };

    box.innerHTML = `<div class="fb-pop-art">${HERO_SVG}</div><h3>Enjoying SmartPoli?</h3><p class="reg-meta" style="margin:0 0 6px;">Tap a star to rate us.</p>${starsHtml(0)}${footer()}`;
    wireFooter();
    wireStars(box, (rating) => {
      box.innerHTML = `<div class="fb-face big" id="fbPopFace">${FACES[rating]}</div><h3>${esc(promptQuestion(rating))}</h3>${starsHtml(rating)}
        <textarea id="fbPopMsg" maxlength="300" rows="3" placeholder="A few words are enough (optional)"></textarea>
        <div class="fb-err" id="fbPopErr" role="alert"></div>
        <div class="sm-actions"><button class="primary" id="fbPopSend">Send</button></div>
        <div class="fb-foot"><button type="button" class="fb-link" id="fbLonger">Write a longer review</button></div>${footer()}`;
      setFace(box.querySelector('#fbPopFace'), rating);
      wireFooter();
      box.querySelector('#fbLonger').addEventListener('click', () => { close(); if (goFeedback) goFeedback(); });
      wireStars(box, (n) => { rating = n; box.querySelector('h3').textContent = promptQuestion(n); setFace(box.querySelector('#fbPopFace'), n); });
      box.querySelector('#fbPopSend').addEventListener('click', async () => {
        const btn = box.querySelector('#fbPopSend');
        btn.disabled = true;
        try {
          await apiFetch('POST', '/feedback', { rating, category: categoryForRating(rating), message: box.querySelector('#fbPopMsg').value, contact_ok: false, source: 'popup' });
          save(afterSent(load() || recordOpen(null, Date.now()), Date.now()));
          box.innerHTML = thanksHtml('');
          setTimeout(close, 1800);
        } catch (e) {
          box.querySelector('#fbPopErr').textContent = e.message || 'Could not send. Please try again.';
          btn.disabled = false;
        }
      });
    });
  }

  /** Call once when the app opens. Counts the visit and, only if the rules allow, shows the popup after a pause and only
   * when nothing else is on screen. */
  function init(opts) {
    const now = Date.now();
    const st = recordOpen(load(), now);
    save(st);
    if (!shouldPrompt(st, now)) return;
    setTimeout(() => {
      if (document.querySelector('.safety-modal-overlay, .fp-overlay')) return;     // never on top of another popup
      if (!shouldPrompt(load(), Date.now())) return;
      showRatePrompt(opts && opts.goFeedback);
    }, 20000);
  }

  const api = { recordOpen, shouldPrompt, afterNotNow, afterSent, afterNever, promptQuestion, categoryForRating, renderFeedbackPage, showRatePrompt, init };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SmartFeedback = api;
})(typeof window !== 'undefined' ? window : globalThis);
