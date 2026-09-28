// SmartPoli — Whisper speech-to-text for the voice page.
//
// A drop-in for the browser's SpeechRecognition (same start/stop/abort and
// onstart/onresult/onend/onerror), so voice-engine.js runs unchanged:
//   - records the mic with the phone's echo cancellation + noise suppression on,
//   - detects speech start/end from the audio level (adapting to room noise),
//     keeping ~0.5 s before speech so the first word isn't clipped,
//   - a brief click/cough (less than minSpeechMs of genuine loud audio) is
//     discarded silently — never uploaded, never reported — so background
//     noise can't be mistaken for a spoken turn,
//   - sends ONE utterance as 16 kHz WAV to /patients/{id}/voice/transcribe
//     (Groq Whisper), which is far better at Hindi / Hinglish than the
//     browser's recogniser, and never re-sends the same words.

(function (root) {
  const TARGET_RATE = 16000;

  function encodeWav(chunks, inRate) {
    let length = 0;
    for (const c of chunks) length += c.length;
    const joined = new Float32Array(length);
    let off = 0;
    for (const c of chunks) { joined.set(c, off); off += c.length; }
    // downsample by averaging to 16 kHz
    const ratio = inRate / TARGET_RATE;
    const outLen = Math.floor(joined.length / ratio);
    const pcm = new Int16Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const start = Math.floor(i * ratio);
      const end = Math.min(Math.floor((i + 1) * ratio), joined.length);
      let sum = 0;
      for (let j = start; j < end; j++) sum += joined[j];
      const v = Math.max(-1, Math.min(1, sum / Math.max(end - start, 1)));
      pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    const buf = new ArrayBuffer(44 + pcm.length * 2);
    const view = new DataView(buf);
    const str = (o, s) => { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)); };
    str(0, 'RIFF'); view.setUint32(4, 36 + pcm.length * 2, true); str(8, 'WAVE');
    str(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, TARGET_RATE, true); view.setUint32(28, TARGET_RATE * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    str(36, 'data'); view.setUint32(40, pcm.length * 2, true);
    new Int16Array(buf, 44).set(pcm);
    return new Blob([buf], { type: 'audio/wav' });
  }

  class WhisperRecognizer {
    /** upload(blob, lang) → Promise<string> */
    constructor({ upload, lang, minSpeechMs }) {
      this.upload = upload;
      this.lang = lang || '';
      this.minSpeechMs = minSpeechMs ?? 300;   // below this, a blip is discarded, not uploaded
      this.running = false;
      this.uploading = false;
      this.stream = null;
      this.noise = null;   // null = not yet estimated (0 is a real, valid noise floor in a silent room)
      this._reset();
    }

    async _ensureAudio() {
      if (this.stream) return;
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new Ctx();
      const source = this.ctx.createMediaStreamSource(this.stream);
      this.proc = this.ctx.createScriptProcessor(4096, 1, 1);
      source.connect(this.proc);
      this.proc.connect(this.ctx.destination);   // Chrome only runs the processor when connected; output stays silent
      this.proc.onaudioprocess = (e) => this._onAudio(e.inputBuffer.getChannelData(0));
    }

    start() {
      if (this.running) throw new Error('already started');
      this.running = true;
      this._reset();
      this._ensureAudio()
        .then(() => {
          if (this.ctx.state === 'suspended') this.ctx.resume();
          if (this.running && this.onstart) this.onstart();
        })
        .catch(() => {
          this.running = false;
          if (this.onerror) this.onerror({ error: 'not-allowed' });
          if (this.onend) this.onend();
        });
    }

    /** Stop listening; if the patient was mid-sentence with enough real speech
     * captured, it's still sent — otherwise (e.g. a click right as we stopped)
     * it's discarded the same way a too-short blip is. */
    stop() {
      if (!this.running) return;
      this.running = false;
      if (this.inSpeech && this._speechDuration() >= this.minSpeechMs) this._finish();
      else { this._reset(); if (this.onend) this.onend(); }
    }

    abort() {
      this.running = false;
      this._reset();
      if (this.onend) this.onend();
    }

    _reset() {
      this.pre = [];
      this.preMs = 0;
      this.chunks = [];
      this.inSpeech = false;
      this.loudMs = 0;
      this.quietMs = 0;
      this.speechMs = 0;
    }

    _onAudio(data) {
      if (!this.running || this.uploading) return;
      const frame = new Float32Array(data);
      const ms = (frame.length / this.ctx.sampleRate) * 1000;
      let sum = 0;
      for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
      const rms = Math.sqrt(sum / frame.length);
      if (!this.inSpeech) this.noise = this.noise === null ? rms : this.noise * 0.95 + rms * 0.05;
      const threshold = Math.max(0.012, this.noise * 2.5);

      if (!this.inSpeech) {
        this.pre.push(frame);
        this.preMs += ms;
        while (this.preMs > 500 && this.pre.length > 1) this.preMs -= (this.pre.shift().length / this.ctx.sampleRate) * 1000;
        if (rms > threshold) {
          this.loudMs += ms;
          if (this.loudMs >= 120) {
            this.inSpeech = true;
            this.chunks = this.pre.slice();
            this.pre = [];
            if (this.onspeechstart) this.onspeechstart();
          }
        } else {
          this.loudMs = 0;
        }
        return;
      }
      this.chunks.push(frame);
      this.speechMs += ms;
      this.quietMs = rms > threshold * 0.8 ? 0 : this.quietMs + ms;
      if (this.speechMs >= 20000) return this._finish();
      if (this.quietMs >= 900) {
        // speechMs includes the trailing quiet time itself, so the genuine
        // loud-speech duration is what's left once that's subtracted out.
        if (this._speechDuration() < this.minSpeechMs) this._reset();   // a click/cough — keep listening
        else this._finish();
      }
    }

    /** How much of the time since speech was first detected was actually
     * loud, i.e. excluding the trailing quiet stretch counted in speechMs. */
    _speechDuration() {
      return this.speechMs - this.quietMs;
    }

    async _finish() {
      const chunks = this.chunks;
      const rate = this.ctx.sampleRate;
      this._reset();
      this.running = false;
      this.uploading = true;
      if (this.onspeechend) this.onspeechend();
      try {
        const text = await this.upload(encodeWav(chunks, rate), this.lang);
        if (text && text.trim() && this.onresult) {
          const result = Object.assign([{ transcript: text.trim() }], { isFinal: true });
          this.onresult({ resultIndex: 0, results: [result] });
        }
      } catch (err) {
        if (this.onerror) this.onerror({ error: 'network', message: err && err.message });
      } finally {
        this.uploading = false;
        if (this.onend) this.onend();
      }
    }
  }

  const api = { WhisperRecognizer, encodeWav };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof window !== 'undefined' ? window : globalThis);
