// SmartPoli waitlist API (Cloudflare Worker + D1). It holds waitlist emails only and shares nothing with the app or its database.
// Four routes, everything else is a tiny 404 that costs nothing:
//   POST /waitlist          join (always the same reply, so nobody can tell whether an email is already on the list)
//   POST /waitlist/confirm  confirm an email with the token from the email (reopening it within 24 hours answers the same)
//   GET  /waitlist/me       the caller's own place (X-Code header)
//   GET  /waitlist/board    top 5 opted-in initials, empty until the list is big enough

const MAX_BODY = 2048;                                  // nothing legitimate here is bigger than this
const CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'; // 31 symbols, no look-alikes
const CODE_RE = /^[A-HJKMNP-Z2-9]{10}$/;
const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;
const TOKEN_TTL = 24 * 3600;                            // seconds
const RESEND_COOLDOWN = 600;                            // one confirmation email per address per 10 minutes
const ROUTES = { '/waitlist': 'POST', '/waitlist/confirm': 'POST', '/waitlist/me': 'GET', '/waitlist/board': 'GET' };

const BASE = {
  'content-type': 'application/json',
  'cache-control': 'no-store',
  'x-content-type-options': 'nosniff',
  'content-security-policy': "default-src 'none'; frame-ancestors 'none'",
  'referrer-policy': 'no-referrer',
};
const reply = (body, status = 200, extra = {}) => new Response(JSON.stringify(body), { status, headers: { ...BASE, ...extra } });
const fail = (status, error, extra) => reply({ error }, status, extra);

// ---- per-isolate limiter: free, no database writes, so a flood cannot burn the D1 write quota. Cloudflare's edge rules are the real wall. ----
const hits = new Map();
function limited(key, max, windowMs) {
  const now = Date.now();
  if (hits.size > 5000) hits.clear();
  const h = hits.get(key);
  if (!h || now > h.reset) { hits.set(key, { n: 1, reset: now + windowMs }); return false; }
  return ++h.n > max;
}

// ---- small helpers ----
const sha256 = async (s) => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)))].map((b) => b.toString(16).padStart(2, '0')).join('');
function randomToken() {
  const b = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...b)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function randomCode() {
  let out = '';
  while (out.length < 10) for (const b of crypto.getRandomValues(new Uint8Array(16))) if (b < 248 && out.length < 10) out += CODE_ALPHABET[b % 31];
  return out;
}
function validEmail(v) {
  if (typeof v !== 'string') return null;
  const e = v.trim().toLowerCase();
  if (e.length < 5 || e.length > 254 || /\s/.test(e)) return null;
  const [local, domain, ...rest] = e.split('@');
  if (rest.length || !local || local.length > 64 || !domain || !/^[a-z0-9._%+-]+$/.test(local)) return null;   // plain email characters only, never markup
  return /^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(domain) ? e : null;
}
async function readCapped(req) {                         // stop reading the moment the body is too big
  const declared = Number(req.headers.get('content-length') || 0);
  if (declared > MAX_BODY) return null;
  if (!req.body) return '';
  const reader = req.body.getReader(), parts = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.length;
    if (size > MAX_BODY) { await reader.cancel(); return null; }
    parts.push(value);
  }
  return new TextDecoder().decode(await new Blob(parts).arrayBuffer());
}
async function readJson(req) {
  if (!(req.headers.get('content-type') || '').includes('application/json')) return { err: fail(415, 'json only') };
  const text = await readCapped(req);
  if (text === null) return { err: fail(413, 'too large') };
  try {
    const body = JSON.parse(text);
    return body && typeof body === 'object' && !Array.isArray(body) ? { body } : { err: fail(400, 'bad request') };
  } catch { return { err: fail(400, 'bad request') }; }
}
function corsHeaders(req, env) {
  const origin = req.headers.get('origin');
  return origin && origin === env.ALLOWED_ORIGIN
    ? { 'access-control-allow-origin': origin, 'vary': 'origin' } : { vary: 'origin' };
}

async function sendConfirmEmail(env, email, token) {
  if (!env.RESEND_API_KEY) return;                      // dev and tests: no mail
  const link = `${env.SITE_URL}/#c=${token}`;            // the token sits in the #fragment, which is never sent to any server
  await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, 'content-type': 'application/json' },
    body: JSON.stringify({
      from: env.MAIL_FROM, to: email, subject: 'Confirm your place on the SmartPoli waitlist',
      text: `Tap to confirm your email and hold your place (valid for 24 hours):\n${link}\n\nIf this wasn't you, ignore this email and nothing happens.\nSmartPoli is a reminder and information tool, not medical advice.`,
    }),
  }).then((r) => { if (!r.ok) console.log('resend', r.status); }).catch(() => console.log('resend unreachable'));   // shows in `wrangler tail`; the user can retry after the cooldown
}
async function turnstileOk(env, token, ip) {
  if (!env.TURNSTILE_SECRET) return true;
  if (typeof token !== 'string' || !token || token.length > 1500) return false;
  const res = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
    method: 'POST', body: new URLSearchParams({ secret: env.TURNSTILE_SECRET, response: token, remoteip: ip }),
  }).catch(() => null);
  return !!res && (await res.json().catch(() => ({}))).success === true;
}

// ---- handlers ----
async function join(req, env, ctx, ip) {
  if (env.WAITLIST_OPEN === 'false') return fail(503, 'closed');
  if (limited(`join:${ip}`, Number(env.JOIN_PER_HOUR || 5), 3600e3) || limited('join:all', Number(env.JOIN_ALL_PER_MIN || 30), 60e3)) return fail(429, 'busy', { 'retry-after': '60' });
  const { body, err } = await readJson(req);
  if (err) return err;
  if (body.website) return reply({ ok: true });         // honeypot: look successful, do nothing
  const email = validEmail(body.email);
  if (!email) return fail(400, 'invalid email');
  if (!(await turnstileOk(env, body.token, ip))) return fail(403, 'check failed');

  const now = Math.floor(Date.now() / 1000);
  const token = randomToken(), hash = await sha256(token);
  const ref = typeof body.ref === 'string' && CODE_RE.test(body.ref) ? body.ref : null;
  const initials = typeof body.initials === 'string' && /^[A-Z]{1,3}$/.test(body.initials) ? body.initials : null;
  const optIn = body.boardOptIn === true && initials ? 1 : 0;

  const ins = await env.DB.prepare(
    `INSERT INTO waitlist (email_lc, code, confirm_hash, last_email_at, created_at, board_opt_in, initials, referred_by)
     VALUES (?1, ?2, ?3, ?4, ?4, ?5, ?6, (SELECT id FROM waitlist WHERE code = ?7 AND confirmed_at IS NOT NULL))
     ON CONFLICT(email_lc) DO NOTHING`).bind(email, randomCode(), hash, now, optIn, optIn ? initials : null, ref).run();
  let mail = ins.meta.changes === 1;
  if (!mail) {                                          // already known: re-send only if unconfirmed and past the cooldown
    const upd = await env.DB.prepare(
      `UPDATE waitlist SET confirm_hash = ?2, last_email_at = ?3
       WHERE email_lc = ?1 AND confirmed_at IS NULL AND last_email_at <= ?3 - ?4`).bind(email, hash, now, RESEND_COOLDOWN).run();
    mail = upd.meta.changes === 1;
  }
  if (mail) ctx.waitUntil(sendConfirmEmail(env, email, token));
  return reply(env.ENV === 'test' && mail ? { ok: true, _token: token } : { ok: true });
}

async function confirm(req, env) {
  if (limited(`confirm:${req.headers.get('cf-connecting-ip') || 'local'}`, Number(env.CONFIRM_PER_HOUR || 20), 3600e3)) return fail(429, 'busy', { 'retry-after': '60' });
  const { body, err } = await readJson(req);
  if (err) return err;
  if (typeof body.token !== 'string' || !TOKEN_RE.test(body.token)) return fail(400, 'invalid link');
  const now = Math.floor(Date.now() / 1000), hash = await sha256(body.token);
  const row = await env.DB.prepare(
    `UPDATE waitlist SET confirmed_at = COALESCE(confirmed_at, ?2)   -- reopening the link inside its 24 hours answers the same, so a lost reply is harmless
     WHERE confirm_hash = ?1 AND last_email_at > ?2 - ?3 RETURNING code`).bind(hash, now, TOKEN_TTL).first();
  return row ? reply({ ok: true, code: row.code }) : fail(400, 'invalid link');
}

async function me(req, env) {
  const code = req.headers.get('x-code') || '';
  if (!CODE_RE.test(code)) return fail(404, 'not found');
  const row = await env.DB.prepare(`SELECT id, confirmed_at FROM waitlist WHERE code = ?1 AND confirmed_at IS NOT NULL`).bind(code).first();
  if (!row) return fail(404, 'not found');
  const [base, refs, total] = await env.DB.batch([
    env.DB.prepare(`SELECT COUNT(*) AS n FROM waitlist WHERE confirmed_at IS NOT NULL AND (confirmed_at < ?1 OR (confirmed_at = ?1 AND id < ?2))`).bind(row.confirmed_at, row.id),
    env.DB.prepare(`SELECT COUNT(*) AS n FROM waitlist WHERE referred_by = ?1 AND confirmed_at IS NOT NULL`).bind(row.id),
    env.DB.prepare(`SELECT COUNT(*) AS n FROM waitlist WHERE confirmed_at IS NOT NULL`),
  ]);
  const referrals = refs.results[0].n;
  const place = Math.max(1, base.results[0].n + 1 - 10 * Math.floor(referrals / 3));   // every 3 referrals move you up 10 places
  return reply({ place, referrals, total: total.results[0].n });
}

async function board(env) {
  const [totals] = (await env.DB.prepare(
    `SELECT COUNT(*) AS total, SUM(referred_by IS NOT NULL) AS refs FROM waitlist WHERE confirmed_at IS NOT NULL`).all()).results;
  const minTotal = Number(env.BOARD_MIN_SIGNUPS || 20), minRefs = Number(env.BOARD_MIN_REFERRALS || 5);
  if (totals.total < minTotal || (totals.refs || 0) < minRefs) return reply({ board: [] }, 200, { 'cache-control': 'public, max-age=10' });
  const { results } = await env.DB.prepare(
    `SELECT w.initials AS initials, COUNT(r.id) AS joined FROM waitlist w
     JOIN waitlist r ON r.referred_by = w.id AND r.confirmed_at IS NOT NULL
     WHERE w.board_opt_in = 1 AND w.initials IS NOT NULL GROUP BY w.id ORDER BY joined DESC, w.id LIMIT 5`).all();
  return reply({ board: results }, 200, { 'cache-control': 'public, max-age=10' });
}

export default {
  async fetch(req, env, ctx) {
    const { pathname } = new URL(req.url);
    const method = ROUTES[pathname];
    if (!method) return fail(404, 'not found');
    const cors = corsHeaders(req, env);
    if (req.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: { ...BASE, ...cors, 'access-control-allow-methods': `${method}, OPTIONS`, 'access-control-allow-headers': 'content-type, x-code', 'access-control-max-age': '600' } });
    }
    if (req.method !== method) return fail(405, 'method not allowed', { allow: method, ...cors });
    const ip = req.headers.get('cf-connecting-ip') || 'local';
    if (limited(`any:${ip}`, Number(env.ANY_PER_MIN || 60), 60e3)) return fail(429, 'busy', { 'retry-after': '60', ...cors });
    const origin = req.headers.get('origin');
    if (method === 'POST' && origin && origin !== env.ALLOWED_ORIGIN) return fail(403, 'forbidden');

    let res;
    try {
      if (pathname === '/waitlist') res = await join(req, env, ctx, ip);
      else if (pathname === '/waitlist/confirm') res = await confirm(req, env);
      else if (pathname === '/waitlist/me') res = await me(req, env);
      else res = await board(env);
    } catch {
      res = fail(500, 'error');                         // fixed body, no stack trace, and not logged line by line
    }
    for (const [k, v] of Object.entries(cors)) res.headers.set(k, v);
    return res;
  },
};
