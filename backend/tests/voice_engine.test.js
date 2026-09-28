// node --test backend/tests/voice_engine.test.js — the continuous voice loop
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const { VoiceEngine, splitSentences, isEcho } = require(path.join(__dirname, '..', 'static', 'voice-engine.js'));

// ---- fakes for the browser APIs -------------------------------------------------
class FakeRecognition {
  constructor() { this.started = 0; this.stopped = 0; this.lang = ''; }
  start() { this.started++; this.running = true; this.onstart && this.onstart(); }
  stop() { this.stopped++; this.running = false; this.onend && this.onend(); }
  abort() { this.stop(); }
  // helpers for tests
  say(text, isFinal) {
    const alt = [{ transcript: text }]; alt.isFinal = isFinal;
    this.onresult({ resultIndex: 0, results: [Object.assign(alt, { 0: { transcript: text }, isFinal })] });
  }
  end() { this.running = false; this.onend && this.onend(); }
}
class FakeSynth {
  constructor() { this.spoken = []; this.cancelled = 0; this.current = null; }
  get speaking() { return !!this.current; }
  speak(u) { this.spoken.push(u.text); this.current = u; u.onstart && u.onstart(); }
  cancel() { this.cancelled++; const u = this.current; this.current = null; u && u.onend && u.onend(); }
  finish() { const u = this.current; this.current = null; u && u.onend && u.onend(); }
}
class FakeUtterance { constructor(text) { this.text = text; } }

function makeEngine(opts = {}) {
  const rec = new FakeRecognition();
  const synth = new FakeSynth();
  const sent = [];
  const states = [];
  let reply = opts.reply || (async (text) => ({ reply: 'Theek hai. Aap kab se pareshaan hain?', lang: 'hi', actions: [] }));
  const timers = [];
  const engine = new VoiceEngine({
    recognition: rec, synth, Utterance: FakeUtterance,
    send: async (text) => { sent.push(text); return reply(text); },
    onState: (s) => states.push(s),
    endOfSpeechMs: 800,
    setTimer: (fn, ms) => { const t = { fn, ms, cleared: false }; timers.push(t); return t; },
    clearTimer: (t) => { if (t) t.cleared = true; },
  });
  const flushTimers = () => { for (const t of timers.splice(0)) if (!t.cleared) t.fn(); };
  return { engine, rec, synth, sent, states, flushTimers, setReply: (r) => { reply = r; } };
}
const tick = () => new Promise((r) => setImmediate(r));

// ---- pure helpers ----------------------------------------------------------------
test('replies are split into sentences so speech starts right away', () => {
  assert.deepStrictEqual(splitSentences('Theek hai. Aap kab se pareshaan hain? Main dekhta hoon!'),
    ['Theek hai.', 'Aap kab se pareshaan hain?', 'Main dekhta hoon!']);
  assert.deepStrictEqual(splitSentences('एक वाक्य। दूसरा वाक्य।'), ['एक वाक्य।', 'दूसरा वाक्य।']);
  assert.deepStrictEqual(splitSentences(''), []);
});

test('its own voice coming back through the mic is recognised as echo', () => {
  const speaking = 'Aapko kab se sir dard ho raha hai';
  assert.strictEqual(isEcho('kab se sir dard ho raha', speaking), true);
  assert.strictEqual(isEcho('nahi nahi woh nahi', speaking), false);
  assert.strictEqual(isEcho('subah se', speaking), false);
  assert.strictEqual(isEcho('', speaking), true);
});

// ---- the loop ------------------------------------------------------------------
test('starting begins listening straight away', () => {
  const { engine, rec, states } = makeEngine();
  engine.start();
  assert.strictEqual(rec.started, 1);
  assert.deepStrictEqual(states, ['listening']);
});

test('a whole statement across pauses is sent once, after the speaker stops', async () => {
  const { engine, rec, sent, flushTimers } = makeEngine();
  engine.start();
  rec.say('mujhe aaj subah se sir dard ho raha hai', true);
  rec.say('aur thoda chakkar bhi aa raha hai', true);
  assert.deepStrictEqual(sent, []);           // still waiting for the end of speech
  flushTimers();
  await tick();
  assert.deepStrictEqual(sent, ['mujhe aaj subah se sir dard ho raha hai aur thoda chakkar bhi aa raha hai']);
});

test('listen → think → speak → listen, with no tap', async () => {
  const { engine, rec, synth, states, flushTimers } = makeEngine();
  engine.start();
  rec.say('mujhe sir dard hai', true);
  flushTimers();
  await tick(); await tick();
  assert.ok(states.includes('thinking'));
  assert.strictEqual(states[states.length - 1], 'speaking');
  assert.deepStrictEqual(synth.spoken, ['Theek hai.']);   // first sentence first
  synth.finish();
  assert.deepStrictEqual(synth.spoken, ['Theek hai.', 'Aap kab se pareshaan hain?']);
  synth.finish();
  assert.strictEqual(states[states.length - 1], 'listening');
  assert.ok(rec.running, 'mic keeps listening');
});

test('the recogniser stopping on its own is restarted while the conversation is on', () => {
  const { engine, rec } = makeEngine();
  engine.start();
  rec.end();                                 // Chrome ends recognition after silence
  assert.strictEqual(rec.started, 2);
  engine.stop();
  rec.end();
  assert.strictEqual(rec.started, 2, 'not restarted after the patient stops the conversation');
});

test('barge-in: the patient talking over SmartPoli stops the speech and is heard', async () => {
  const { engine, rec, synth, sent, states, flushTimers } = makeEngine();
  engine.start();
  rec.say('mujhe sir dard hai', true);
  flushTimers(); await tick(); await tick();
  assert.strictEqual(states[states.length - 1], 'speaking');
  rec.say('nahi nahi woh nahi', false);      // interim words, not an echo of the reply
  assert.strictEqual(synth.cancelled, 1);
  assert.strictEqual(states[states.length - 1], 'listening');
  rec.say('nahi nahi woh nahi, pet mein dard hai', true);
  flushTimers(); await tick();
  assert.strictEqual(sent[sent.length - 1], 'nahi nahi woh nahi, pet mein dard hai');
});

test('SmartPoli hearing its own voice does not interrupt itself', async () => {
  const { engine, rec, synth, sent, flushTimers } = makeEngine();
  engine.start();
  rec.say('mujhe sir dard hai', true);
  flushTimers(); await tick(); await tick();
  rec.say('theek hai', false);               // echo of "Theek hai."
  rec.say('theek hai', true);
  flushTimers(); await tick();
  assert.strictEqual(synth.cancelled, 0);
  assert.strictEqual(sent.length, 1);
});

test('speech while SmartPoli is thinking is kept for the next turn, not lost', async () => {
  let release;
  const { engine, rec, sent, flushTimers, setReply } = makeEngine();
  setReply(() => new Promise((r) => { release = () => r({ reply: 'Achha.', lang: 'hi', actions: [] }); }));
  engine.start();
  rec.say('pehli baat', true);
  flushTimers(); await tick();
  rec.say('aur ek baat', true);
  flushTimers(); await tick();
  assert.deepStrictEqual(sent, ['pehli baat']);
  release(); await tick(); await tick();
  assert.deepStrictEqual(sent, ['pehli baat', 'aur ek baat']);
});

test('an emergency reply pauses listening so the help card has full attention', async () => {
  const { engine, rec, synth, states, flushTimers, setReply } = makeEngine();
  setReply(async () => ({ reply: 'Abhi doctor ki madad lijiye.', lang: 'hi', actions: [{ type: 'emergency' }] }));
  engine.start();
  rec.say('papa behosh ho gaye', true);
  flushTimers(); await tick(); await tick();
  synth.finish();
  assert.strictEqual(states[states.length - 1], 'paused');
  assert.strictEqual(rec.running, false);
});

test('pause and resume', () => {
  const { engine, rec, states } = makeEngine();
  engine.start();
  engine.pause();
  assert.strictEqual(states[states.length - 1], 'paused');
  assert.strictEqual(rec.running, false);
  engine.resume();
  assert.strictEqual(states[states.length - 1], 'listening');
  assert.ok(rec.running);
});

test('SmartPoli can speak on its own (a recheck) and then keeps listening', () => {
  const { engine, synth, states } = makeEngine();
  engine.start();
  engine.announce('Ab kaisa lag raha hai?', 'hi');
  assert.deepStrictEqual(synth.spoken, ['Ab kaisa lag raha hai?']);
  assert.strictEqual(states[states.length - 1], 'speaking');
  synth.finish();
  assert.strictEqual(states[states.length - 1], 'listening');
});

test('onDoneSpeaking fires after the reply has been heard', async () => {
  const done = [];
  const rec = new FakeRecognition(); const synth = new FakeSynth();
  const engine = new VoiceEngine({ recognition: rec, synth, Utterance: FakeUtterance,
    send: async () => ({ reply: 'Khol raha hoon.', lang: 'hi', actions: [{ type: 'navigate', screen: 'timeline' }] }),
    onDoneSpeaking: (res) => done.push(res.actions[0].screen), setTimer: (fn) => { fn(); return 1; }, clearTimer: () => {} });
  engine.start();
  await engine.say('timeline dikhao');
  assert.deepStrictEqual(done, []);
  synth.finish();
  assert.deepStrictEqual(done, ['timeline']);
});
