"""
SmartPoli — speech-to-text for the voice page, via Groq Whisper.

The browser's built-in speech recognition is weak for Hinglish and, on
phones, keeps re-sending the sentence so far. The voice page instead records
one utterance (with the phone's echo cancellation and noise suppression on),
detects when the patient stops talking, and posts the audio here; Whisper
transcribes it. If Whisper isn't available the page falls back to the
browser's recogniser.
"""

import os

MAX_AUDIO_BYTES = 5 * 1024 * 1024   # ~2.5 minutes of 16 kHz mono WAV — far more than one utterance
ALLOWED_TYPES = ("audio/wav", "audio/x-wav", "audio/wave", "audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4")

# Whisper follows the style of its prompt: this nudges it toward how patients
# actually speak to SmartPoli (Hindi, English and mixed, medicine names).
STT_PROMPT = ("Doctor-patient style conversation in Hindi, English or Hinglish about medicines and symptoms: "
              "dawai, dose, saans, sir dard, chakkar, bukhar, khansi, pet dard, Metformin, Paracetamol, BP, sugar.")


class TranscriptionUnavailable(Exception):
    pass


def is_available() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def transcribe(audio: bytes, filename: str, lang_hint: str = "") -> str:
    if not is_available():
        raise TranscriptionUnavailable("GROQ_API_KEY is not set.")
    from groq import Groq
    # max_retries=0: on a 429 the SDK's own retry can wait out Groq's full
    # Retry-After (seen up to 30s x 2 attempts) before raising — the patient
    # would sit on "transcribing…" for a minute for nothing. Fail fast instead;
    # voice.js shows "couldn't hear that" and the mic reopens for another try.
    client = Groq(api_key=os.getenv("GROQ_API_KEY"), max_retries=0)
    kwargs = {"file": (filename, audio), "model": os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
              "prompt": STT_PROMPT, "response_format": "json", "temperature": 0}
    if lang_hint == "en":
        kwargs["language"] = "en"   # Hindi / mixed speech is left to auto-detection
    result = client.audio.transcriptions.create(**kwargs)
    return (getattr(result, "text", "") or "").strip()
