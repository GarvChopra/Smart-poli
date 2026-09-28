// node --test backend/tests/voice_commands.test.js — voice commands, no AI service
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const { understand, answer } = require(path.join(__dirname, '..', 'static', 'voice-commands.js'));

const type = (text) => understand(text).type;
const screen = (text) => understand(text).screen;

test('opening screens, in English, Hinglish and Devanagari', () => {
  assert.strictEqual(screen('open dashboard'), 'dashboard');
  assert.strictEqual(screen('Dashboard kholo'), 'dashboard');
  assert.strictEqual(screen('go home'), 'dashboard');
  assert.strictEqual(screen('show my report'), 'report');
  assert.strictEqual(screen('timeline dikhao'), 'timeline');
  assert.strictEqual(screen('open my prescriptions'), 'prescriptions');
  assert.strictEqual(screen('upload a prescription'), 'prescriptions');
  assert.strictEqual(screen('show my emergency card'), 'emergency');
  assert.strictEqual(screen('manage caregivers'), 'settings');
  assert.strictEqual(screen('open settings'), 'settings');
  assert.strictEqual(screen('what should I avoid with my medicines'), 'safety');
  assert.strictEqual(screen('रिपोर्ट दिखाओ'), 'report');
});

test('"update my details" opens the card editor', () => {
  assert.deepStrictEqual(understand('update my details'), { type: 'navigate', screen: 'emergency', edit: true });
  assert.strictEqual(understand('change my blood group').edit, true);
  assert.strictEqual(understand('meri details badlo').edit, true);
});

test('medicine questions get a quick answer, not a screen', () => {
  assert.strictEqual(type('when is my next medicine'), 'next');
  assert.strictEqual(type('agli dawai kab hai'), 'next');
  assert.strictEqual(type('how many doses are left today'), 'today');
  assert.strictEqual(type('aaj kitni dawai baaki hai'), 'today');
  assert.strictEqual(type('what is my adherence'), 'adherence');
  assert.strictEqual(type('how many doses did I miss'), 'adherence');
});

test('taking medicine: reports, questions and negations', () => {
  assert.strictEqual(type('I took my medicine'), 'took');
  assert.strictEqual(type('maine dawai le li'), 'took');
  assert.strictEqual(understand('I took my morning medicine').slot, 'morning');
  assert.strictEqual(type('did I take my medicine?'), 'did_i_take');
  assert.notStrictEqual(type("I didn't take my medicine"), 'took');
  assert.notStrictEqual(type('maine dawai nahi li'), 'took');
  assert.notStrictEqual(type('I took a walk'), 'took');
});

test('danger words go straight to the call-112 card; other symptoms open the symptom check', () => {
  for (const t of ['I have chest pain', 'he is unconscious', 'I can\'t breathe', 'seene mein dard ho raha hai', 'call ambulance']) {
    assert.strictEqual(type(t), 'emergency', t);
  }
  assert.deepStrictEqual(understand('I have a headache'), { type: 'navigate', screen: 'triage', symptom: true });
  assert.strictEqual(screen('mujhe bukhar hai'), 'triage');
  assert.strictEqual(type('open emergency card'), 'navigate');   // a screen, not an emergency
});

test('control words', () => {
  assert.strictEqual(type('stop'), 'stop');
  assert.strictEqual(type('band karo'), 'stop');
  assert.deepStrictEqual(understand('Hindi mein bolo'), { type: 'language', lang: 'hi' });
  assert.deepStrictEqual(understand('speak in English'), { type: 'language', lang: 'en' });
  assert.strictEqual(type('what can you do'), 'help');
  assert.strictEqual(type('banana'), 'unknown');
});

// ---- answers from the dashboard data ---------------------------------------------

const NOW = new Date(2026, 8, 29, 8, 30);   // 08:30 local
const iso = (h, m = 0, day = 29) => {
  const d = new Date(2026, 8, day, h, m);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:00`;
};
const DASH = {
  adherence: { adherence_percent: 87.5, taken: 7, missed: 1 },
  upcoming_doses: [],
  recent_doses: [
    { id: 1, medicine_name: 'Metformin', scheduled_at: iso(8), state: 'pending' },
    { id: 2, medicine_name: 'Amlodipine', scheduled_at: iso(8), state: 'pending' },
    { id: 3, medicine_name: 'Metformin', scheduled_at: iso(20), state: 'pending' },
    { id: 4, medicine_name: 'Atorvastatin', scheduled_at: iso(21), state: 'pending' },
    { id: 5, medicine_name: 'Metformin', scheduled_at: iso(8, 0, 30), state: 'pending' },
  ],
};
const say = (text, lang = 'en') => answer(understand(text), DASH, NOW, lang);

test('"I took my medicine" marks every dose due at the time closest to now', () => {
  const res = say('I took my medicine');
  const take = res.actions.find((a) => a.type === 'take');
  assert.deepStrictEqual(take.doses.map((d) => d.dose_id), [1, 2]);
  assert.match(res.reply, /Metformin, Amlodipine/);
});

test('naming the medicine marks just that one', () => {
  const take = say('I took my amlodipine tablet').actions.find((a) => a.type === 'take');
  assert.deepStrictEqual(take.doses.map((d) => d.dose_id), [2]);
});

test('nothing due now → says so, marks nothing', () => {
  const res = answer(understand('I took my medicine'), DASH, new Date(2026, 8, 29, 14, 0), 'en');
  assert.ok(!res.actions.some((a) => a.type === 'take'));
});

test('quick answers read the dashboard', () => {
  assert.strictEqual(say('when is my next medicine').reply, 'Your next medicine is Metformin, today at 20:00.');
  assert.strictEqual(say('how many doses are left today').reply, '2 left today: Metformin 20:00, Atorvastatin 21:00.');
  assert.strictEqual(say('what is my adherence').reply, 'Your adherence is 88 percent. 7 taken, 1 missed.');
  assert.match(say('did I take my medicine?').reply, /^Not yet/);
  assert.match(say('agli dawai kab hai', 'hi').reply, /^Aapki agli dawai Metformin hai/);
});

test('navigation and emergencies carry their action', () => {
  assert.deepStrictEqual(say('open dashboard').actions, [{ type: 'navigate', screen: 'dashboard', edit: false }]);
  assert.deepStrictEqual(say('I have chest pain').actions, [{ type: 'emergency' }]);
});
