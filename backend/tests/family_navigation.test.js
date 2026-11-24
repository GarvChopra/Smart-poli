const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'caregiver.js'), 'utf8');

function drawerElement(tag) {
  return {
    tag,
    children: [],
    attributes: {},
    addEventListener() {},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    setAttribute(name, value) { this.attributes[name] = value; },
  };
}

function navigationFixture(family = []) {
  const shell = {
    child: null,
    replaceChildren(child) {
      if (this.child) this.child.parentNode = null;
      this.child = child;
      child.parentNode = this;
    },
  };
  const drawer = drawerElement('nav');
  const window = { location: { hash: '' }, addEventListener() {}, scrollTo() {} };
  const requests = new Map();
  const pageToday = (mount, patientId) => new Promise((resolve, reject) => {
    requests.set(patientId, {
      finish(name) {
        mount.innerHTML = name;
        resolve();
      },
      reject,
    });
  });
  const context = vm.createContext({
    window,
    document: {
      getElementById(id) {
        if (id === 'page') return shell;
        if (id === 'drawerNav') return drawer;
        return { addEventListener() {} };
      },
      createElement: drawerElement,
    },
    requireRole: () => null,
    esc: String,
    overviewLabel: {},
    api: async () => ({ family }),
    pagePatients() {},
    pageToday,
    pageHistory() {},
    pageReport() {},
    pageNotes() {},
    pageEmergency() {},
  });
  vm.runInContext(source, context);
  return {
    shell,
    drawer,
    requests,
    openFamily() {
      window.location.hash = '#/';
      return context.route();
    },
    go(patientId) {
      window.location.hash = `#/p/${patientId}/today`;
      return context.route();
    },
  };
}

test('a late Family page response cannot overwrite the active member or their menu', async () => {
  const { go, requests, shell, drawer } = navigationFixture();
  const mum = go(3);
  const dad = go(4);
  requests.get(4).finish('Dad');
  await dad;
  requests.get(3).finish('Mum');
  await mum;
  assert.equal(shell.child.innerHTML, 'Dad');
  const links = drawer.children.filter(child => child.tag === 'a').map(child => child.href);
  assert.ok(links.includes('#/p/4/today'));
  assert.ok(!links.includes('#/p/3/today'));
});

test('Family drawer creates internal links and puts member names in text nodes', async () => {
  const label = 'Mum & Dad <Family>';
  const { drawer, openFamily } = navigationFixture([{ id: 4, label }, { id: 'unknown', label: 'Invalid member' }]);
  await openFamily();
  const list = drawer.children.find(child => child.id === 'famList');
  assert.equal(list.children.length, 3);
  assert.equal(list.children[1].href, '#/p/4/today');
  assert.equal(list.children[1].textContent, label);
  assert.equal(list.children[1].children.length, 0);
});

test('a stale Family route error cannot replace the active member', async () => {
  const { go, requests, shell } = navigationFixture();
  const mum = go(3);
  const dad = go(4);
  requests.get(4).finish('Dad');
  await dad;
  requests.get(3).reject(new Error('403'));
  await mum;
  assert.equal(shell.child.innerHTML, 'Dad');
});

test('the owner dashboard ignores outdated profiles and their pending date requests', async () => {
  const app = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
  const start = app.indexOf('let dashboardRequest =');
  const end = app.indexOf('// ---------------------------------------------------------------- triage', start);
  const requests = new Map();
  const dateRequests = [];
  const state = { patientId: 1, activeTab: 'dashboard' };
  const dayButton = {
    dataset: { day: '2026-11-19' },
    classList: { toggle() {} },
    addEventListener(_, listener) { this.click = listener; },
  };
  const dayBox = { innerHTML: '', querySelectorAll: selector => selector === '[data-day]' ? [dayButton] : [] };
  const view = { innerHTML: '', querySelector: selector => selector === '#dashDay' ? dayBox : null, querySelectorAll: () => [] };
  const fixture = vm.createContext({
    state,
    document: { getElementById: id => id === 'view-dashboard' ? view : { addEventListener() {} }, querySelector: () => true },
    getDashboard: () => new Promise(resolve => requests.set(state.patientId, resolve)),
    api: (_method, url) => new Promise(resolve => dateRequests.push({ url, resolve })),
    DayTimeline: { stripHtml: () => '', listHtml: doses => doses.join(', ') },
    renderTodaySchedule: dash => dash.label,
    loadTimingChip() {}, renderPushPrompt() {}, showWhatsAppPopup() {}, openAddMedicineSheet() {},
    currentUser: null,
    ICONS: {},
    escHtml: String,
  });
  vm.runInContext(app.slice(start, end), fixture);
  const dashboard = label => ({ label, today: '2026-11-20', upcoming_doses: [], prn_medicines: [] });
  const first = fixture.renderDashboard();
  state.patientId = 2;
  const second = fixture.renderDashboard();
  requests.get(2)(dashboard('Profile 2'));
  await second;
  requests.get(1)(dashboard('Profile 1'));
  await first;
  assert.match(dayBox.innerHTML, /Profile 2/);

  const staleDate = dayButton.click;
  const pending = staleDate();
  assert.equal(dateRequests[0].url, '/patients/2/doses?date=2026-11-19');
  state.patientId = 3;
  const third = fixture.renderDashboard();
  requests.get(3)(dashboard('Profile 3'));
  await third;
  dateRequests[0].resolve({ doses: ['Outdated date result'] });
  await pending;
  await staleDate();
  assert.equal(dateRequests.length, 1);
  assert.match(dayBox.innerHTML, /Profile 3/);
  assert.doesNotMatch(dayBox.innerHTML, /Outdated date result/);
});
