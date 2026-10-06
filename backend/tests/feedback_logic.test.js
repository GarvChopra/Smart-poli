// The rules for WHEN the "rate the app" popup may appear (pure logic, no browser).
const test = require('node:test');
const assert = require('node:assert');
const F = require('../static/feedback.js');

const DAY = 86400000;
const T0 = Date.UTC(2026, 9, 6, 10, 0, 0);

function used(days, firstAt = T0) {                         // a person who opened the app on each of `days` (day offsets from firstAt)
  let st = null;
  for (const d of days) st = F.recordOpen(st, firstAt + d * DAY);
  return st;
}

test('never on day one: needs 7 days since first use AND 3 different days of use', () => {
  assert.strictEqual(F.shouldPrompt(used([0]), T0), false);
  assert.strictEqual(F.shouldPrompt(used([0, 1, 2]), T0 + 3 * DAY), false);            // 3 days of use but only 3 days old
  assert.strictEqual(F.shouldPrompt(used([0, 8]), T0 + 8 * DAY), false);               // old enough but only 2 days of use
  assert.strictEqual(F.shouldPrompt(used([0, 3, 8]), T0 + 8 * DAY), true);
});

test('opening the app several times on the same day counts as one day', () => {
  let st = null;
  for (let i = 0; i < 5; i++) st = F.recordOpen(st, T0 + i * 3600000);
  assert.strictEqual(st.days.length, 1);
});

test('"Not now" waits 14 days, and after three of them it stops', () => {
  let st = used([0, 3, 8]);
  const now = T0 + 8 * DAY;
  st = F.afterNotNow(st, now);
  assert.strictEqual(F.shouldPrompt(st, now + 13 * DAY), false);
  assert.strictEqual(F.shouldPrompt(st, now + 15 * DAY), true);
  st = F.afterNotNow(F.afterNotNow(st, now + 15 * DAY), now + 30 * DAY);
  assert.strictEqual(st.notNow, 3);
  assert.strictEqual(F.shouldPrompt(st, now + 400 * DAY), false);
});

test('after sending feedback it waits 90 days; "don\'t ask again" is forever', () => {
  const now = T0 + 8 * DAY;
  let st = F.afterSent(used([0, 3, 8]), now);
  assert.strictEqual(F.shouldPrompt(st, now + 89 * DAY), false);
  assert.strictEqual(F.shouldPrompt(F.recordOpen(st, now + 91 * DAY), now + 91 * DAY), true);
  assert.strictEqual(F.shouldPrompt(F.afterNever(used([0, 3, 8])), now + 999 * DAY), false);
});

test('the stored list of days never grows without bound', () => {
  let st = null;
  for (let d = 0; d < 100; d++) st = F.recordOpen(st, T0 + d * DAY);
  assert.ok(st.days.length <= 30);
});

test('the questions follow the rating and the category follows too', () => {
  assert.match(F.promptQuestion(5), /like/i);
  assert.match(F.promptQuestion(2), /wrong/i);
  assert.strictEqual(F.categoryForRating(5), 'praise');
  assert.strictEqual(F.categoryForRating(4), 'praise');
  assert.strictEqual(F.categoryForRating(3), 'problem');
  assert.strictEqual(F.categoryForRating(1), 'problem');
});
