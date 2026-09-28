// node --test backend/tests/voice_stt.test.js — Whisper recorder's noise handling
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const { WhisperRecognizer, encodeWav } = require(path.join(__dirname, '..', 'static', 'voice-stt.js'));

const SAMPLE_RATE = 16000;
const FRAME_MS = 100;
const FRAME_SIZE = (SAMPLE_RATE * FRAME_MS) / 1000;   // 1600 samples

function loudFrame(amp = 0.5) { return new Float32Array(FRAME_SIZE).fill(amp); }
function quietFrame() { return new Float32Array(FRAME_SIZE); }
const tick = () => new Promise((r) => setImmediate(r));

/** A real mic always has some ambient floor before speech starts; the adaptive
 * threshold needs that contrast to recognise loud audio as speech at all —
 * feeding loud frames with no baseline (as if speech were the very first
 * sound ever heard) doesn't exercise the real detection path. */
function primeQuiet(rec, n = 5) {
  for (let i = 0; i < n; i++) rec._onAudio(quietFrame());
}

/** A recognizer with the real getUserMedia/AudioContext bypassed — `_onAudio`
 * is fed synthetic frames directly, as the real audio pipeline would. */
function makeRecognizer(opts = {}) {
  const uploads = [];
  const rec = new WhisperRecognizer({
    upload: async (blob, lang) => { uploads.push({ blob, lang }); return opts.uploadText ?? 'transcribed text'; },
    lang: opts.lang || 'hi', minSpeechMs: opts.minSpeechMs,
  });
  rec.ctx = { sampleRate: SAMPLE_RATE, state: 'running', resume() {} };
  rec.running = true;   // as if start() had already completed
  const events = [];
  rec.onresult = (e) => events.push(['result', e]);
  rec.onend = () => events.push(['end']);
  rec.onerror = (e) => events.push(['error', e]);
  return { rec, uploads, events };
}

test('a brief click or cough (after a quiet room) is discarded — never uploaded, listening continues', () => {
  const { rec, uploads, events } = makeRecognizer();
  primeQuiet(rec);
  rec._onAudio(loudFrame());          // 100ms
  rec._onAudio(loudFrame());          // 200ms — crosses the enter-speech threshold
  for (let i = 0; i < 12; i++) rec._onAudio(quietFrame());   // back to quiet — too short to be real speech
  assert.deepStrictEqual(uploads, [], 'a blip must never be uploaded');
  assert.deepStrictEqual(events, [], 'no result, no end — recognizer just keeps listening');
  assert.strictEqual(rec.running, true);
});

test('a real utterance (after a quiet room) is uploaded once, after the pause', async () => {
  const { rec, uploads, events } = makeRecognizer({ uploadText: 'mujhe sir dard hai' });
  primeQuiet(rec);
  rec._onAudio(loudFrame());
  rec._onAudio(loudFrame());          // enters speech
  for (let i = 0; i < 5; i++) rec._onAudio(loudFrame());     // 500ms of genuine speech
  for (let i = 0; i < 10; i++) rec._onAudio(quietFrame());   // trailing pause ends the utterance
  await tick(); await tick();
  assert.strictEqual(uploads.length, 1);
  const [kind, e] = events.find(([k]) => k === 'result');
  assert.strictEqual(kind, 'result');
  assert.strictEqual(e.results[0][0].transcript, 'mujhe sir dard hai');
  assert.ok(events.some(([k]) => k === 'end'));
});

test('stop() before speech is even detected discards silently', () => {
  const { rec, uploads, events } = makeRecognizer();
  primeQuiet(rec);
  rec._onAudio(loudFrame());   // only 100ms — hasn't crossed the enter-speech threshold yet
  rec.stop();
  assert.deepStrictEqual(uploads, []);
  assert.deepStrictEqual(events, [['end']]);
  assert.strictEqual(rec.running, false);
});

test('stop() right as speech is detected (no confirmed duration yet) discards, not a fragment upload', () => {
  const { rec, uploads, events } = makeRecognizer();
  primeQuiet(rec);
  rec._onAudio(loudFrame());
  rec._onAudio(loudFrame());   // enters speech this frame
  rec.stop();
  assert.deepStrictEqual(uploads, []);
  assert.deepStrictEqual(events, [['end']]);
});

test('stop() mid-utterance still uploads what was genuinely captured', async () => {
  const { rec, uploads } = makeRecognizer();
  primeQuiet(rec);
  rec._onAudio(loudFrame());
  rec._onAudio(loudFrame());
  for (let i = 0; i < 5; i++) rec._onAudio(loudFrame());   // 500ms of real speech, still talking
  rec.stop();
  await tick(); await tick();
  assert.strictEqual(uploads.length, 1);
});

test('the language hint is passed through to the upload', async () => {
  const { rec, uploads } = makeRecognizer({ lang: 'en' });
  primeQuiet(rec);
  rec._onAudio(loudFrame());
  rec._onAudio(loudFrame());
  for (let i = 0; i < 5; i++) rec._onAudio(loudFrame());
  for (let i = 0; i < 10; i++) rec._onAudio(quietFrame());
  await tick(); await tick();
  assert.strictEqual(uploads[0].lang, 'en');
});

test('abort discards everything immediately, no upload', () => {
  const { rec, uploads, events } = makeRecognizer();
  primeQuiet(rec);
  rec._onAudio(loudFrame());
  rec._onAudio(loudFrame());
  for (let i = 0; i < 5; i++) rec._onAudio(loudFrame());
  rec.abort();
  assert.deepStrictEqual(uploads, []);
  assert.deepStrictEqual(events, [['end']]);
  assert.strictEqual(rec.running, false);
});

// ---- WAV encoding -----------------------------------------------------------------
function readWavHeader(blob) {
  // jsdom/node Blob doesn't sync-expose bytes; encodeWav is given plain arrays in these
  // tests via a minimal Blob shim check instead — see below.
  return blob;
}

test('encodeWav produces a valid 16kHz mono 16-bit PCM header', async () => {
  const chunks = [new Float32Array(320).fill(0.25)];   // 20ms at 16kHz input, no resampling needed
  const blob = encodeWav(chunks, 16000);
  const buf = Buffer.from(await blob.arrayBuffer());
  assert.strictEqual(buf.toString('ascii', 0, 4), 'RIFF');
  assert.strictEqual(buf.toString('ascii', 8, 12), 'WAVE');
  assert.strictEqual(buf.readUInt32LE(24), 16000, 'sample rate');
  assert.strictEqual(buf.readUInt16LE(22), 1, 'mono');
  assert.strictEqual(buf.readUInt16LE(34), 16, 'bits per sample');
  assert.strictEqual(buf.length, 44 + 320 * 2);
});

test('encodeWav downsamples 48kHz input to 16kHz (Whisper\'s expected rate)', async () => {
  const chunks = [new Float32Array(480 * 3).fill(1)];   // 30ms at 48kHz, full-scale
  const blob = encodeWav(chunks, 48000);
  const buf = Buffer.from(await blob.arrayBuffer());
  assert.strictEqual(buf.readUInt32LE(24), 16000);
  const pcmSamples = (buf.length - 44) / 2;
  assert.strictEqual(pcmSamples, 480);   // 30ms of 16kHz audio = 480 samples
  assert.strictEqual(buf.readInt16LE(44), 0x7fff, 'a constant full-scale input downsamples to full scale, not silence');
});
