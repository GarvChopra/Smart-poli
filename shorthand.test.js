const test = require('node:test');
const assert = require('node:assert');
const { decodeSchedule } = require('./shorthand');

// ------------------------------------------------------------ slot notation

test('1-0-1 gives morning and night only', () => {
  const r = decodeSchedule('Tab Paracetamol 650mg 1-0-1 PC x5d');
  assert.strictEqual(r.dosesPerDay, 2);
  assert.deepStrictEqual(r.slots, ['morning', 'night']);
  assert.deepStrictEqual(r.times, ['08:30', '20:30']);
  assert.strictEqual(r.food, 'after');
  assert.strictEqual(r.durationDays, 5);
  assert.strictEqual(r.confidence, 1);
});

test('1-0-1 plain language lists every slot including the zero', () => {
  const r = decodeSchedule('1-0-1 after meals');
  assert.match(r.plainLanguage, /Morning — 1 tablet/);
  assert.match(r.plainLanguage, /Afternoon — 0 tablets/);
  assert.match(r.plainLanguage, /Night — 1 tablet/);
  assert.match(r.plainLanguage, /Take after meals\./);
});

test('1-1-1 gives three doses', () => {
  const r = decodeSchedule('Tab Azithral 500 1-1-1 x3d');
  assert.strictEqual(r.dosesPerDay, 3);
  assert.deepStrictEqual(r.slots, ['morning', 'afternoon', 'night']);
});

test('0-1-0 gives afternoon only', () => {
  const r = decodeSchedule('0-1-0');
  assert.strictEqual(r.dosesPerDay, 1);
  assert.deepStrictEqual(r.slots, ['afternoon']);
});

test('2-0-2 counts units separately from doses', () => {
  const r = decodeSchedule('Tab Shelcal 2-0-2');
  assert.strictEqual(r.dosesPerDay, 2);
  assert.strictEqual(r.unitsPerDay, 4);
});

test('four-part code maps to morning/afternoon/evening/night', () => {
  const r = decodeSchedule('1-0-1-1');
  assert.deepStrictEqual(r.slots, ['morning', 'evening', 'night']);
  assert.strictEqual(r.dosesPerDay, 3);
});

test('half tablets parse from glyph and from fraction', () => {
  const glyph = decodeSchedule('½-0-½');
  assert.strictEqual(glyph.unitsPerDay, 1);
  const frac = decodeSchedule('1/2-0-1/2');
  assert.strictEqual(frac.unitsPerDay, 1);
});

// ------------------------------------------------------------ abbreviations

test('TDS before food gives three doses shifted 30 min earlier', () => {
  const r = decodeSchedule('Cap Amoxicillin 500mg TDS AC 7 days');
  assert.strictEqual(r.dosesPerDay, 3);
  assert.strictEqual(r.food, 'before');
  assert.deepStrictEqual(r.times, ['08:00', '13:30', '20:00']);
  assert.strictEqual(r.durationDays, 7);
});

test('OD before breakfast', () => {
  const r = decodeSchedule('Pantoprazole 40mg OD before breakfast');
  assert.strictEqual(r.dosesPerDay, 1);
  assert.strictEqual(r.food, 'before');
});

test('HS schedules at bedtime', () => {
  const r = decodeSchedule('Tab Alprazolam 0.25mg HS 10 days');
  assert.deepStrictEqual(r.slots, ['bedtime']);
  assert.deepStrictEqual(r.times, ['22:00']);
  assert.strictEqual(r.durationDays, 10);
});

test('BD and BID are equivalent', () => {
  assert.strictEqual(decodeSchedule('BD').dosesPerDay, decodeSchedule('BID').dosesPerDay);
});

test('Q8H uses fixed clock times, not named slots', () => {
  const r = decodeSchedule('Inj Ceftriaxone Q8H x3d');
  assert.deepStrictEqual(r.times, ['06:00', '14:00', '22:00']);
  assert.strictEqual(r.slots.length, 0);
  assert.strictEqual(r.dosesPerDay, 3);
});

test('dotted abbreviations normalise', () => {
  const r = decodeSchedule('Tab X b.d. p.c.');
  assert.strictEqual(r.dosesPerDay, 2);
  assert.strictEqual(r.food, 'after');
});

// ------------------------------------------------------------ safety cases

test('SOS generates ZERO scheduled doses', () => {
  const r = decodeSchedule('Syrup Crocin 5ml SOS');
  assert.strictEqual(r.prn, true);
  assert.strictEqual(r.doses.length, 0);
  assert.strictEqual(r.times.length, 0);
  assert.strictEqual(r.dosesPerDay, 0);
  assert.match(r.plainLanguage, /only when needed/i);
});

test('PRN generates ZERO scheduled doses', () => {
  const r = decodeSchedule('Tab Dolo 650 PRN');
  assert.strictEqual(r.prn, true);
  assert.strictEqual(r.doses.length, 0);
});

test('malformed slot code is flagged, never guessed', () => {
  const r = decodeSchedule('Tab Unknown 1-?-1');
  assert.ok(r.unparsed.includes('1-?-1'));
  assert.ok(r.confidence < 0.7, `expected low confidence, got ${r.confidence}`);
  assert.strictEqual(r.dosesPerDay, 0);
});

test('no frequency at all gives zero confidence and no doses', () => {
  const r = decodeSchedule('Tab Paracetamol 650mg');
  assert.strictEqual(r.confidence, 0);
  assert.strictEqual(r.doses.length, 0);
  assert.match(r.plainLanguage, /could not be read/i);
});

test('conflicting frequencies drop confidence and are reported', () => {
  const r = decodeSchedule('Tab X 1-0-1 TDS');
  assert.ok(r.confidence < 0.7, `expected low confidence, got ${r.confidence}`);
  assert.ok(r.notes.some((n) => /conflict/i.test(n)));
});

test('empty input is handled without throwing', () => {
  const r = decodeSchedule('');
  assert.strictEqual(r.confidence, 0);
  assert.strictEqual(r.doses.length, 0);
});

test('0-0-0 is treated as no doses and low confidence', () => {
  const r = decodeSchedule('Tab X 0-0-0 x5d');
  assert.strictEqual(r.dosesPerDay, 0);
  assert.ok(r.confidence < 0.7);
});

// ------------------------------------------------------------ duration

test('ongoing treatment has no end date', () => {
  const r = decodeSchedule('Tab Metformin 500mg 1-0-1 continue');
  assert.strictEqual(r.ongoing, true);
  assert.strictEqual(r.durationDays, null);
  assert.match(r.plainLanguage, /until your doctor/i);
});

test('UK shorthand 5/7 means five days', () => {
  assert.strictEqual(decodeSchedule('BD 5/7').durationDays, 5);
});

test('UK shorthand 2/52 means two weeks', () => {
  assert.strictEqual(decodeSchedule('OD 2/52').durationDays, 14);
});

test('weeks convert to days', () => {
  assert.strictEqual(decodeSchedule('BD 2 weeks').durationDays, 14);
});

test('total tablet count derives duration and flags uneven division', () => {
  const r = decodeSchedule('Tab Y 1-0-1 10 tabs');
  assert.strictEqual(r.durationDays, 5);
  const uneven = decodeSchedule('Tab Y 1-1-1 10 tabs');
  assert.strictEqual(uneven.durationDays, 3);
  assert.ok(uneven.notes.some((n) => /confirm/i.test(n)));
});

// ------------------------------------------------------------ config

test('custom slot times are honoured', () => {
  const r = decodeSchedule('1-0-1', { slotTimes: { morning: '07:00', night: '21:00' } });
  assert.deepStrictEqual(r.times, ['07:00', '21:00']);
});
