// Runs the real Worker locally (wrangler dev + local D1) and attacks it. Run: npm test
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const wrangler = path.join(root, 'node_modules', 'wrangler', 'bin', 'wrangler.js');
const ORIGIN = 'http://site.test';
const state = path.join(root, '.wrangler-test');
const procs = [];

function start(port, extra = []) {
  const p = spawn(process.execPath, [wrangler, 'dev', '--local', '--port', String(port), '--persist-to', state,
    '--var', 'ENV:test', '--var', `ALLOWED_ORIGIN:${ORIGIN}`, '--var', `SITE_URL:${ORIGIN}`, ...extra], { cwd: root, stdio: 'ignore' });
  procs.push(p);
  return `http://127.0.0.1:${port}`;
}
async function ready(base) {
  for (let i = 0; i < 120; i++) {
    try { await fetch(base + '/nothing'); return; } catch { await new Promise((r) => setTimeout(r, 500)); }
  }
  throw new Error('worker did not start');
}
const post = (base, p, body, headers = {}) => fetch(base + p, { method: 'POST', headers: { 'content-type': 'application/json', origin: ORIGIN, ...headers }, body: typeof body === 'string' ? body : JSON.stringify(body) });
const join = async (base, email, extra = {}) => { const r = await post(base, '/waitlist', { email, ...extra }); return { r, j: await r.json() }; };
const confirm = async (base, token) => { const r = await post(base, '/waitlist/confirm', { token }); return { r, j: await r.json() }; };

let API, CLOSED, FLOOD;
before(async () => {
  rmSync(state, { recursive: true, force: true });
  execFileSync(process.execPath, [wrangler, 'd1', 'execute', 'smartpoli-waitlist', '--local', '--persist-to', state, '--file', 'schema.sql'], { cwd: root, stdio: 'ignore' });
  API = start(8791, ['--var', 'JOIN_PER_HOUR:1000', '--var', 'JOIN_ALL_PER_MIN:1000', '--var', 'ANY_PER_MIN:1000', '--var', 'CONFIRM_PER_HOUR:1000']);
  await ready(API);
  CLOSED = start(8792, ['--var', 'WAITLIST_OPEN:false']);
  await ready(CLOSED);
  FLOOD = start(8793);
  await ready(FLOOD);
});
after(() => { procs.forEach((p) => p.kill()); });

test('guessed paths and wrong methods return nothing useful', async () => {
  for (const p of ['/', '/admin', '/waitlist/all', '/waitlist/export', '/.env', '/api/users']) {
    const r = await fetch(API + p);
    assert.equal(r.status, 404, p);
    assert.deepEqual(await r.json(), { error: 'not found' });
  }
  const r = await fetch(API + '/waitlist');                  // GET on the join route must not list anything
  assert.equal(r.status, 405);
  assert.deepEqual(await r.json(), { error: 'method not allowed' });
  assert.equal((await fetch(API + '/waitlist', { method: 'DELETE' })).status, 405);
});

test('oversized bodies are refused before they are parsed', async () => {
  assert.equal((await post(API, '/waitlist', JSON.stringify({ email: 'a@b.co', pad: 'x'.repeat(3000) }))).status, 413);
  assert.equal((await post(API, '/waitlist', 'x'.repeat(1_000_000))).status, 413);        // the "million characters" case
  assert.equal((await post(API, '/waitlist', { email: 'a'.repeat(300) + '@example.com' })).status, 400);
});

test('wrong content type, junk json and bad emails are rejected', async () => {
  assert.equal((await post(API, '/waitlist', '{"email":"a@b.co"}', { 'content-type': 'text/plain' })).status, 415);
  assert.equal((await post(API, '/waitlist', '{not json')).status, 400);
  assert.equal((await post(API, '/waitlist', '[1,2]')).status, 400);
  for (const email of ['', 'nope', 'a@b', '<script>alert(1)</script>@x.io', 'a b@c.io', 'a@@b.io', 5, null, {}]) {
    assert.equal((await post(API, '/waitlist', { email })).status, 400, JSON.stringify(email));
  }
});

test('foreign origins cannot post', async () => {
  assert.equal((await post(API, '/waitlist', { email: 'x@y.io' }, { origin: 'http://evil.test' })).status, 403);
  const pre = await fetch(API + '/waitlist', { method: 'OPTIONS', headers: { origin: ORIGIN } });
  assert.equal(pre.status, 204);
  assert.equal(pre.headers.get('access-control-allow-origin'), ORIGIN);
  const bad = await fetch(API + '/waitlist', { method: 'OPTIONS', headers: { origin: 'http://evil.test' } });
  assert.equal(bad.headers.get('access-control-allow-origin'), null);
});

test('join -> confirm -> place; reused and fake tokens fail; replies never reveal who is on the list', async () => {
  const a = await join(API, 'Asha@Example.com');
  assert.equal(a.r.status, 200);
  assert.ok(a.j._token);                                    // only present because ENV=test
  const again = await join(API, 'asha@example.com');        // same address, inside the cooldown: same shape, no new mail
  assert.equal(again.r.status, 200);
  assert.deepEqual(Object.keys(again.j), ['ok']);
  assert.equal((await confirm(API, 'A'.repeat(43))).r.status, 400);
  assert.equal((await confirm(API, 'short')).r.status, 400);
  const c = await confirm(API, a.j._token);
  assert.equal(c.r.status, 200);
  assert.match(c.j.code, /^[A-HJKMNP-Z2-9]{10}$/);
  assert.equal((await confirm(API, a.j._token)).r.status, 400);   // single use
  const me = await fetch(API + '/waitlist/me', { headers: { 'x-code': c.j.code } });
  assert.deepEqual(await me.json(), { place: 1, referrals: 0, total: 1 });
  assert.equal((await join(API, 'asha@example.com')).j._token, undefined);   // confirmed already: no new token
});

test('/me only answers to a real code', async () => {
  for (const code of ['', 'AAAAAAAAAA', 'short', "' OR 1=1 --", 'A'.repeat(500)]) {
    const r = await fetch(API + '/waitlist/me', { headers: { 'x-code': code } });
    assert.equal(r.status, 404, code);
  }
  assert.equal((await fetch(API + '/waitlist/me')).status, 404);
});

test('honeypot looks successful but stores nothing', async () => {
  const { r, j } = await join(API, 'bot@example.com', { website: 'http://spam.test' });
  assert.equal(r.status, 200);
  assert.equal(j._token, undefined);
  assert.ok((await join(API, 'bot@example.com')).j._token);  // first real try still gets a token: nothing was stored
});

test('referrals move people up; unconfirmed referrers do not count', async () => {
  const host = await join(API, 'host@example.com');
  const { j: hc } = await confirm(API, host.j._token);
  const guests = [];
  for (let i = 0; i < 3; i++) {
    const g = await join(API, `guest${i}@example.com`, { ref: hc.code });
    guests.push((await confirm(API, g.j._token)).j.code);
  }
  const me = await (await fetch(API + '/waitlist/me', { headers: { 'x-code': hc.code } })).json();
  assert.equal(me.referrals, 3);
  assert.equal(me.place, 1);                                 // 3 referrals = 10 places up, never above 1
  const stranger = await join(API, 'late@example.com', { ref: 'ABCDEFGHJK' });   // unknown referrer is ignored, join still works
  assert.equal(stranger.r.status, 200);
});

test('board stays empty until the list is big enough', async () => {
  const r = await fetch(API + '/waitlist/board');
  assert.deepEqual(await r.json(), { board: [] });
});

test('kill switch closes signups', async () => {
  const r = await post(CLOSED, '/waitlist', { email: 'late@example.com' });
  assert.equal(r.status, 503);
});

test('a flood is cut off cheaply and answers stay tiny', async () => {
  let blocked = 0, bytes = 0;
  for (let i = 0; i < 90; i++) {
    const r = await fetch(FLOOD + '/waitlist/board');
    if (r.status === 429) { blocked++; bytes += (await r.text()).length; } else await r.text();
  }
  assert.ok(blocked > 20, `expected throttling, got ${blocked}`);
  assert.ok(bytes / blocked < 40);                           // error replies are a few bytes
  assert.equal((await post(FLOOD, '/waitlist', { email: 'x@y.io' })).status, 429);
});
