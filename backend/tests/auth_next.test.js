// node --test backend/tests/auth_next.test.js — login ?next= safety
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const ctx = { window: { location: { search: '', pathname: '/static/login.html' }, history: { replaceState() {} } }, document: { title: '' }, localStorage: { getItem: () => null, setItem() {}, removeItem() {} }, URLSearchParams, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'auth.js'), 'utf8'), ctx);

test('patient goes back to the voice page', () => {
  assert.strictEqual(ctx.landingAfterLogin('patient', '?next=/static/voice.html'), '/static/voice.html');
});
test('external or odd targets are ignored', () => {
  for (const bad of ['https://evil.com', '//evil.com/x.html', '/static/../main.py', '/static/voice.html\njs', 'javascript:alert(1)', '/auth/login']) {
    assert.strictEqual(ctx.landingAfterLogin('patient', '?next=' + encodeURIComponent(bad)), '/static/index.html', bad);
  }
});
test('doctors and caregivers keep their own landing page', () => {
  assert.strictEqual(ctx.landingAfterLogin('doctor', '?next=/static/voice.html'), '/static/doctor.html');
});
