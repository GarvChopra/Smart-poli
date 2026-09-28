import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from conftest import register_and_login  # noqa: E402
import voice_assistant  # noqa: E402

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 60


@pytest.fixture(autouse=True)
def _fresh_limits():
    voice_assistant.reset_rate_limits()


def _fake_whisper(monkeypatch, text="mujhe saans lene mein dikkat ho rahi hai", calls=None):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    class FakeTranscriptions:
        def create(self, **kwargs):
            if calls is not None:
                calls.append(kwargs)
            return SimpleNamespace(text=text)

    class FakeClient:
        def __init__(self, api_key=None, **_):
            self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())

    monkeypatch.setattr("groq.Groq", FakeClient)


def _patient(client):
    register_and_login(client)
    return client.post("/patients", json={"name": "STT Patient"}).json()["id"]


def test_audio_is_transcribed_by_whisper(monkeypatch):
    calls = []
    _fake_whisper(monkeypatch, calls=calls)
    with TestClient(app) as client:
        pid = _patient(client)
        r = client.post(f"/patients/{pid}/voice/transcribe", files={"audio": ("u.wav", WAV, "audio/wav")},
                        data={"lang": "hi"})
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "mujhe saans lene mein dikkat ho rahi hai"}
    assert calls[0]["model"].startswith("whisper")
    assert "language" not in calls[0]          # Hindi / Hinglish is auto-detected
    assert "Hinglish" in calls[0]["prompt"]


def test_non_audio_and_oversized_uploads_are_refused(monkeypatch):
    _fake_whisper(monkeypatch)
    with TestClient(app) as client:
        pid = _patient(client)
        r = client.post(f"/patients/{pid}/voice/transcribe", files={"audio": ("x.html", b"<script>", "text/html")})
        assert r.status_code == 415
        big = b"RIFF" + b"\x00" * (5 * 1024 * 1024 + 10)
        r = client.post(f"/patients/{pid}/voice/transcribe", files={"audio": ("u.wav", big, "audio/wav")})
        assert r.status_code == 413


def test_only_the_patient_can_transcribe_into_their_record(monkeypatch):
    _fake_whisper(monkeypatch)
    with TestClient(app) as client:
        pid = _patient(client)
        register_and_login(client)
        r = client.post(f"/patients/{pid}/voice/transcribe", files={"audio": ("u.wav", WAV, "audio/wav")})
        assert r.status_code == 403


def test_without_a_key_the_page_is_told_to_use_the_browser(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with TestClient(app) as client:
        pid = _patient(client)
        r = client.post(f"/patients/{pid}/voice/transcribe", files={"audio": ("u.wav", WAV, "audio/wav")})
        assert r.status_code == 503
        assert client.get("/voice/available").json()["stt"] is False
