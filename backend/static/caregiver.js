// SmartPoli — caregiver portal shell: session, menu, larger text, and the page router.
// The pages themselves are in caregiver-pages.js (loaded first). Access is enforced by the server on every request
// (caregiver_router.py); this file has no access logic of its own.

const currentUser = requireRole('caregiver');

function renderSessionChip() {
  const chip = document.getElementById('sessionChip');
  if (!chip || !currentUser) return;
  chip.innerHTML = `
    <span class="who">${esc(currentUser.name)}</span>
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
const closeDrawerBtn = document.getElementById('closeDrawerBtn');
function openDrawer() {
  navDrawer.inert = false;
  navDrawer.classList.add('open');
  navOverlay.classList.add('open');
  hamburgerBtn.setAttribute('aria-expanded', 'true');
  closeDrawerBtn.focus();
}
function closeDrawer() {
  navDrawer.inert = true;
  navDrawer.classList.remove('open');
  navOverlay.classList.remove('open');
  hamburgerBtn.setAttribute('aria-expanded', 'false');
  hamburgerBtn.focus();
}
hamburgerBtn.addEventListener('click', openDrawer);
closeDrawerBtn.addEventListener('click', closeDrawer);
navOverlay.addEventListener('click', closeDrawer);
navDrawer.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });
let famOpen = true;                 // the Family list in the menu: open on the Family page, rolled up once a person is chosen
let navFamily = null;               // who is in that list (from /caregiver/home)
let drawerPath = '/';
navDrawer.addEventListener('click', (e) => {
  if (e.target.closest('.fam-toggle')) { famOpen = !famOpen; renderDrawerNav(drawerPath); navDrawer.querySelector('.fam-toggle').focus(); return; }
  if (e.target.closest('a')) closeDrawer();
});

// ---------------------------------------------------------------- router: #/  #/settings  #/p/:id/(today|history|report|notes|emergency)

const MENU_PAGES = [['today', 'Today'], ['history', 'History'], ['report', 'Care report'], ['notes', 'Notes'], ['emergency', 'Emergency']];

/** The one menu. Family first: tap it to unroll the list of people (it scrolls by itself when long); the person you are on
 * has a border. Rolled up, their name sits small above their pages. Then Settings and Feedback. */
function renderDrawerNav(path) {
  const nav = document.getElementById('drawerNav');
  if (!nav) return;
  drawerPath = path;
  const m = path.match(/^\/p\/(\d+)\/(\w+)$/);
  const open = m ? Number(m[1]) : null;
  const link = (href, label, className) => {
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.className = className;
    anchor.textContent = label;
    return anchor;
  };
  const menuLink = (href, label, active) => link(href, label, active ? 'cg-menu-link active' : 'cg-menu-link');
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'cg-menu-link fam-toggle';
  toggle.setAttribute('aria-expanded', String(famOpen));
  toggle.textContent = 'Family';
  const chevron = document.createElement('span');
  chevron.className = 'dd-chev';
  chevron.setAttribute('aria-hidden', 'true');
  chevron.textContent = famOpen ? '▾' : '▸';
  toggle.append(chevron);
  const entries = [toggle];
  if (famOpen) {
    toggle.setAttribute('aria-controls', 'famList');
    const list = document.createElement('div');
    list.className = 'drop';
    list.id = 'famList';
    list.append(link('#/', 'All family', path === '/' ? 'm open' : 'm'));
    for (const member of navFamily || []) {
      if (!Number.isSafeInteger(member.id) || member.id < 1) continue;
      list.append(link(`#/p/${member.id}/today`, member.label, open === member.id ? 'm open' : 'm'));
    }
    list.append(link('/', 'You', 'm you'));
    entries.push(list);
  }
  if (m) {
    const name = document.createElement('div');
    name.className = 'who';
    name.textContent = overviewLabel.pid === open ? overviewLabel.name : 'This person';
    entries.push(name, ...MENU_PAGES.map(([key, label]) => menuLink(`#/p/${m[1]}/${key}`, label, m[2] === key)));
  }
  entries.push(menuLink('#/settings', 'Settings', path === '/settings'), menuLink('#/feedback', 'Feedback', path === '/feedback'));
  nav.replaceChildren(...entries);
}

const PAGE_FOR = { today: pageToday, history: pageHistory, report: pageReport, notes: pageNotes, emergency: pageEmergency };

function routePath() {
  return (window.location.hash || '#/').replace(/^#/, '') || '/';
}

async function route() {
  const page = document.getElementById('page');
  const path = routePath();
  // Each route owns its own mount. A late response can only write into the
  // previous, detached mount, including callbacks started by that page.
  const root = document.createElement('div');
  page.replaceChildren(root);
  const isCurrent = () => root.parentNode === page && routePath() === path;
  try {
    let m;
    famOpen = path === '/';
    if (path === '/') await pagePatients(root);
    else if (path === '/settings') await pageSettings(root);
    else if (path === '/feedback') pageFeedback(root);
    else if ((m = path.match(/^\/p\/(\d+)\/medicines$/))) { window.location.hash = `#/p/${m[1]}/history`; return; }   // the old Medicines page: History took its place
    else if ((m = path.match(/^\/p\/(\d+)\/(today|history|report|notes|emergency)$/))) await PAGE_FOR[m[2]](root, Number(m[1]));
    else { window.location.hash = '#/'; return; }
    if (!isCurrent()) return;
    renderDrawerNav(path);
    api('GET', '/caregiver/home').then((h) => {
      if (!isCurrent()) return;
      navFamily = h.family;
      renderDrawerNav(drawerPath);
    }).catch(() => {});
    window.scrollTo(0, 0);
  } catch (e) {
    if (!isCurrent()) return;
    const gone = /not linked|403/i.test(e.message || '') || e.status === 403;
    const pid = Number(path.match(/^\/p\/(\d+)\//)?.[1]);
    if (gone && pid) {
      invalidateOverview(pid);
      if (navFamily) navFamily = navFamily.filter((member) => member.id !== pid);
      renderDrawerNav('/');
    }
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
