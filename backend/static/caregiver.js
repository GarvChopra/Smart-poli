// SmartPoli — caregiver portal shell: session, menu, larger text, and the page router.
// The pages themselves are in caregiver-pages.js (loaded first). Access is enforced by the server on every request
// (caregiver_router.py); this file has no access logic of its own.

const currentUser = requireRole('caregiver');

function renderSessionChip() {
  const chip = document.getElementById('sessionChip');
  if (!chip || !currentUser) return;
  chip.innerHTML = `
    <span class="who">${esc(currentUser.name)}</span>
    <span class="role-tag">Caregiver</span>
    <button class="ghost small" id="logoutBtn">Log out</button>`;
  document.getElementById('logoutBtn').addEventListener('click', logout);
}

function applyA11yMode() {
  const on = localStorage.getItem('smartpoli_a11y') === '1';
  document.documentElement.dataset.a11y = on ? 'large' : '';
  document.getElementById('a11yToggleBtn')?.setAttribute('aria-pressed', String(on));
}
document.getElementById('a11yToggleBtn').addEventListener('click', () => {
  const on = localStorage.getItem('smartpoli_a11y') === '1';
  localStorage.setItem('smartpoli_a11y', on ? '0' : '1');
  applyA11yMode();
});

// Header is brand + hamburger only; the menu holds the page links, text size and the session.
const hamburgerBtn = document.getElementById('hamburgerBtn');
const navDrawer = document.getElementById('navDrawer');
const navOverlay = document.getElementById('navOverlay');
function openDrawer() { navDrawer.classList.add('open'); navOverlay.classList.add('open'); hamburgerBtn.setAttribute('aria-expanded', 'true'); }
function closeDrawer() { navDrawer.classList.remove('open'); navOverlay.classList.remove('open'); hamburgerBtn.setAttribute('aria-expanded', 'false'); }
hamburgerBtn.addEventListener('click', openDrawer);
document.getElementById('closeDrawerBtn').addEventListener('click', closeDrawer);
navOverlay.addEventListener('click', closeDrawer);
navDrawer.addEventListener('click', (e) => { if (e.target.closest('a')) closeDrawer(); });

// ---------------------------------------------------------------- router: #/  #/settings  #/p/:id/(today|medicines|notes|history|emergency)

const MENU_PAGES = [['today', 'Today'], ['medicines', 'Medicines'], ['notes', 'Notes'], ['history', 'History'], ['emergency', 'Emergency']];

/** The left menu: your patients, then (inside a patient) that patient's pages, then settings. */
function renderDrawerNav(path) {
  const nav = document.getElementById('drawerNav');
  if (!nav) return;
  const m = path.match(/^\/p\/(\d+)\/(\w+)$/);
  const link = (href, label, on) => `<a class="cg-menu-link${on ? ' active' : ''}" href="${href}">${esc(label)}</a>`;
  let html = link('#/', 'Your patients', path === '/');
  if (m) {
    const pid = m[1];
    const name = overviewCache.pid === Number(pid) && overviewCache.data ? overviewCache.data.patient.name : 'This patient';
    html += `<div class="cg-menu-head">${esc(name)}</div>` + MENU_PAGES.map(([key, label]) => link(`#/p/${pid}/${key}`, label, m[2] === key)).join('');
  }
  html += link('#/settings', 'Settings', path === '/settings');
  html += link('#/feedback', 'Feedback', path === '/feedback');
  nav.innerHTML = html;
}

const PAGE_FOR = { today: pageToday, medicines: pageMedicines, notes: pageNotes, history: pageHistory, emergency: pageEmergency };

async function route() {
  const root = document.getElementById('page');
  const path = (window.location.hash || '#/').replace(/^#/, '') || '/';
  try {
    let m;
    if (path === '/') await pagePatients(root);
    else if (path === '/settings') await pageSettings(root);
    else if (path === '/feedback') pageFeedback(root);
    else if ((m = path.match(/^\/p\/(\d+)\/(today|medicines|notes|history|emergency)$/))) await PAGE_FOR[m[2]](root, Number(m[1]));
    else window.location.hash = '#/';
    renderDrawerNav(path);
    window.scrollTo(0, 0);
  } catch (e) {
    const gone = /not linked|403/i.test(e.message || '') || e.status === 403;
    root.innerHTML = gone
      ? `<div class="card"><h3>You're no longer linked to this patient</h3><p class="reg-meta">The patient may have removed your access.</p><a class="primary" href="#/">Back to your patients</a></div>`
      : `<div class="card"><h3>Something went wrong</h3><p class="reg-meta">${esc(e.message || 'Please try again.')}</p><a href="#/">Back to your patients</a></div>`;
  }
}
window.addEventListener('hashchange', route);

(function boot() {
  if (!currentUser) return;
  renderSessionChip();
  applyA11yMode();
  route();
  SmartFeedback.init({ goFeedback: () => { window.location.hash = '#/feedback'; } });
})();
