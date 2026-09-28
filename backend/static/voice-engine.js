// SmartPoli — continuous voice engine.
//
// One loop, no tapping:   listening → thinking → speaking → listening …
//
// - Speech-to-text via a pluggable `recognition` object (the browser's own
//   SpeechRecognition); a short pause after the patient stops
//   talking ends their turn, so "sir dard hai … aur chakkar bhi" arrives as
//   ONE message, never repeated.
// - Replies are spoken sentence by sentence, so speech starts immediately.
// - Half duplex by default: the mic is held OFF for the WHOLE time SmartPoli
//   is thinking (waiting for the reply) AND speaking — not just while
//   speaking — so it can never record its own voice, and a stray noise
//   picked up while thinking can't resolve later and falsely interrupt the
//   reply. Tap the orb to interrupt; it listens again immediately.
//   (Full duplex — listen while speaking, barge in by voice — stays available
//   for recognisers where that's safe, but the page defaults to half.)
// - The recogniser stopping on its own (Chrome does after silence) is restarted
//   while the conversation is on.
// - An emergency reply pauses listening so the help card has full attention.
//
// Browser APIs are injected so the loop is testable in Node
// (backend/tests/voice_engine.test.js).

(function (root) {
  const SENTENCE_END = /(?<=[.!?।])\s+/;

  function splitSentences(text) {
    return (text || '').split(SENTENCE_END).map((s) => s.trim()).filter(Boolean);
  }

  function words(text) {
    return (text || '').toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, ' ').split(/\s+/).filter(Boolean);
  }

  /** Join what was heard before with a new result WITHOUT repeating words.
   * Android Chrome re-sends the whole sentence so far as each new result
   * ("hello" → "hello to" → "hello to mujhe"), so appending would snowball. */
  function mergeTranscript(prev, next) {
    const p = (prev || '').trim();
    const n = (next || '').trim();
    if (!p) return n;
    if (!n) return p;
    const pl = p.toLowerCase();
    const nl = n.toLowerCase();
    if (nl.includes(pl)) return n;   // the new result already contains everything so far
    if (pl.includes(nl)) return p;   // a repeat of something already heard
    const pw = p.split(/\s+/);
    const nw = n.split(/\s+/);
    for (let k = Math.min(pw.length, nw.length); k > 0; k--) {   // overlapping edge
      if (pw.slice(-k).join(' ').toLowerCase() === nw.slice(0, k).join(' ').toLowerCase()) {
        return pw.concat(nw.slice(k)).join(' ');
      }
    }
    return `${p} ${n}`;
  }

  /** Is `heard` just SmartPoli's own `spoken` words coming back through the mic? */
  function isEcho(heard, spoken) {
    const h = words(heard);
    if (!h.length) return true;
    const s = new Set(words(spoken));
    const overlap = h.filter((w) => s.has(w)).length / h.length;
    return overlap >= 0.6;
  }

  class VoiceEngine {
    constructor(opts) {
      this.rec = opts.recognition;
      this.synth = opts.synth;
      this.Utterance = opts.Utterance;
      this.send = opts.send;
      this.onState = opts.onState || (() => {});
      this.onHeard = opts.onHeard || (() => {});
      this.onReply = opts.onReply || (() => {});
      this.onDoneSpeaking = opts.onDoneSpeaking || (() => {});
      this.voiceFor = opts.voiceFor || (() => null);
      this.endOfSpeechMs = opts.endOfSpeechMs || 900;
      this.echoGraceMs = opts.echoGraceMs || 1500;
      this.setTimer = opts.setTimer || ((fn, ms) => setTimeout(fn, ms));
      this.clearTimer = opts.clearTimer || ((t) => clearTimeout(t));
      this.now = opts.now || (() => Date.now());
      // 'half' (default): the mic is off while SmartPoli speaks, so it never hears itself
      // through the speaker; tap to interrupt. 'full': listen while speaking (barge-in by voice).
      this.duplex = opts.duplex || 'half';
      this.resumeDelayMs = opts.resumeDelayMs ?? 350;
      this.micHeld = false;

      this.state = 'idle';
      this.active = false;      // the conversation is on
      this.paused = false;
      this.running = false;     // the recogniser is running
      this.busy = false;        // waiting for SmartPoli's reply
      this.buffer = '';         // the patient's words this turn (final results)
      this.pending = [];        // said while SmartPoli was thinking
      this.queue = [];          // sentences still to speak
      this.speakingText = '';   // the whole reply being spoken (for echo checks)
      this.lastSpokenAt = 0;
      this.endTimer = null;
      this.pauseAfterSpeech = false;

      this.rec.onstart = () => { this.running = true; };
      this.rec.onend = () => {
        this.running = false;
        if (this.active && !this.paused && !this.micHeld) this._listen();   // keep the mic open
      };
      this.rec.onerror = () => {};
      this.rec.onresult = (e) => this._onResult(e);
    }

    // ---- controls
    start() {
      this.active = true;
      this.paused = false;
      this._setState('listening');
      this._listen();
    }

    stop() {
      this.active = false;
      this._stopSpeaking();
      this._stopListening();
      this._setState('idle');
    }

    pause() {
      this.paused = true;
      this._stopSpeaking();
      this._stopListening();
      this._setState('paused');
    }

    resume() {
      this.paused = false;
      this.active = true;
      this.micHeld = false;
      this._setState('listening');
      this._listen();
    }

    /** Send something the patient tapped (a button) as if they had said it. */
    say(text, extra) { return this._turn(text, extra); }

    /** SmartPoli speaks first (e.g. the recheck "Ab kaisa lag raha hai?"), then listens. */
    announce(text, lang) {
      if (!this.active || this.paused || this.busy) return;
      this.lastReply = null;
      this._speak(text, lang);
    }

    // ---- listening
    _listen() {
      if (this.running || this.paused || !this.active || this.micHeld) return;
      try { this.rec.start(); } catch (e) { /* already starting */ }
    }

    _stopListening() {
      this.clearTimer(this.endTimer);
      if (this.running) { try { this.rec.stop(); } catch (e) { /* ignore */ } }
    }

    _onResult(e) {
      let finalText = '';
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalText += r[0].transcript + ' ';
        else interim += r[0].transcript + ' ';
      }
      const heard = (finalText + interim).trim();
      if (!heard) return;

      if (this.state === 'speaking') {
        // Its own voice coming back through the mic is ignored; real speech interrupts.
        if (isEcho(heard, this.speakingText) || words(heard).length < 2) return;
        this._bargeIn();
      } else if (this.now() - this.lastSpokenAt < this.echoGraceMs && isEcho(heard, this.speakingText)) {
        return;  // a late echo of the reply that just finished
      }

      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) this.buffer = mergeTranscript(this.buffer, e.results[i][0].transcript);
      }
      this.onHeard(mergeTranscript(this.buffer, interim), !interim.trim());
      this.clearTimer(this.endTimer);
      this.endTimer = this.setTimer(() => this._commit(), this.endOfSpeechMs);
    }

    _commit() {
      const text = this.buffer.trim();
      this.buffer = '';
      if (!text) return;
      if (this.busy) { this.pending.push(text); return; }
      this._turn(text);
    }

    // ---- a turn
    async _turn(text, extra) {
      this.busy = true;
      this._setState('thinking');
      if (this.duplex === 'half') {
        // Held from here (not just once speech starts) so nothing recorded
        // while waiting for the reply can resolve later and be mistaken for
        // the patient interrupting SmartPoli's answer.
        this.micHeld = true;
        this._stopListening();
      }
      let res;
      try {
        res = await this.send(text, extra);
      } catch (err) {
        res = { reply: '', error: err, actions: [] };
      }
      this.busy = false;
      this.onReply(res, text);
      if (this.pending.length) {
        // The patient already said more — answer that instead of talking over them.
        const next = this.pending.join(' ');
        this.pending = [];
        return this._turn(next);
      }
      this.pauseAfterSpeech = (res.actions || []).some((a) => a.type === 'emergency');
      this.lastReply = res;
      this._speak(res.reply || '', res.lang);
    }

    // ---- speaking
    _speak(text, lang) {
      this.queue = splitSentences(text);
      this.speakingText = text;
      this.lang = lang;
      if (!this.queue.length) return this._doneSpeaking();
      this._setState('speaking');
      if (this.duplex === 'half') {
        // Phones' speakers feed straight back into the mic: don't listen while speaking.
        this.micHeld = true;
        this._stopListening();
      } else {
        this._listen();
      }
      this._speakNext();
    }

    _speakNext() {
      const sentence = this.queue.shift();
      if (sentence === undefined) return this._doneSpeaking();
      const u = new this.Utterance(sentence);
      const voice = this.voiceFor(this.lang, sentence);
      if (voice) { u.voice = voice.voice || null; u.lang = voice.lang; }
      u.onend = () => { if (this.state === 'speaking' && this.current === u) this._speakNext(); };
      u.onerror = u.onend;
      this.current = u;
      this.synth.speak(u);
    }

    _doneSpeaking() {
      this.current = null;
      this.lastSpokenAt = this.now();
      if (this.lastReply) { const res = this.lastReply; this.lastReply = null; this.onDoneSpeaking(res); }
      if (this.pauseAfterSpeech) { this.pauseAfterSpeech = false; return this.pause(); }
      if (this.active && !this.paused) {
        this._setState('listening');
        if (this.duplex === 'half') {
          // a short gap so the last word from the speaker isn't caught
          this.setTimer(() => { this.micHeld = false; this._listen(); }, this.resumeDelayMs);
        } else {
          this._listen();
        }
      }
    }

    _stopSpeaking() {
      this.queue = [];
      this.current = null;
      if (this.synth.speaking) this.synth.cancel();
    }

    /** Stop talking and listen now (speech over SmartPoli in full duplex, or a tap on the orb). */
    _bargeIn() {
      this._stopSpeaking();
      this.lastSpokenAt = this.now();
      this.micHeld = false;
      this._setState('listening');
      this._listen();
    }

    interrupt() { if (this.state === 'speaking') this._bargeIn(); }

    _setState(s) {
      if (this.state === s) return;
      this.state = s;
      this.onState(s);
    }
  }

  const api = { VoiceEngine, splitSentences, isEcho, mergeTranscript };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof window !== 'undefined' ? window : globalThis);
