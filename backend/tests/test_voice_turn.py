import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from db import SessionLocal, Dose, Medicine, Prescription, SymptomCheck  # noqa: E402
from conftest import register_and_login  # noqa: E402
import voice_assistant  # noqa: E402


# ---------------------------------------------------------------- fake Groq

def _call(name, args, call_id="c1"):
    return SimpleNamespace(id=call_id, type="function",
                           function=SimpleNamespace(name=name, arguments=args if isinstance(args, str) else json.dumps(args)))


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def install_fake_groq(monkeypatch, script, calls=None):
    """`script` is a list of responses, or callables(messages) -> response."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    steps = list(script)

    class FakeCompletions:
        def create(self, **kwargs):
            if calls is not None:
                calls.append(kwargs)
            step = steps.pop(0)
            if isinstance(step, Exception):
                raise step
            return step(kwargs["messages"]) if callable(step) else step

    class FakeClient:
        def __init__(self, api_key=None, **_):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("groq.Groq", FakeClient)


def _setup(client, due_now=1):
    register_and_login(client)
    pid = client.post("/patients", json={"name": "Ramesh Kumar", "emergency_contact": "Anita, +91 98765 43210"}).json()["id"]
    pres = client.post("/prescriptions", json={"patient_id": pid, "lines": ["Tab Metformin 500mg 1-0-1 x5d",
                                                                           "Tab Telma 40mg 1-0-0 x5d"]}).json()
    client.post(f"/prescriptions/{pres['prescription_id']}/confirm")
    db = SessionLocal()
    try:
        doses = (db.query(Dose).join(Medicine).join(Prescription).filter(Prescription.patient_id == pid)
                 .order_by(Dose.id).all())
        now = datetime.now()
        for i, d in enumerate(doses):
            d.scheduled_at = now - timedelta(minutes=20) if i < due_now else now + timedelta(hours=5 + i)
            d.state = "pending"
        db.commit()
        due_ids = [d.id for d in doses[:due_now]]
    finally:
        db.close()
    return pid, due_ids


def _turn(client, pid, text, **extra):
    body = {"text": text, "lang": "en", "client_time": datetime.now().astimezone().isoformat(),
            "history": [], "state": {}, **extra}
    return client.post(f"/patients/{pid}/voice/turn", json=body)


def _dose_state(dose_id):
    db = SessionLocal()
    try:
        return db.query(Dose).get(dose_id).state
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    voice_assistant.reset_rate_limits()


# ---------------------------------------------------------------- tests

def test_red_flag_is_emergency_without_calling_groq(monkeypatch):
    calls = []
    install_fake_groq(monkeypatch, [], calls)
    with TestClient(app) as client:
        pid, _ = _setup(client)
        r = _turn(client, pid, "mujhe saans nahi aa rahi")
        assert r.status_code == 200, r.text
        body = r.json()
    assert calls == []
    assert body["actions"][0]["type"] == "emergency"
    assert "112" in body["reply"]
    db = SessionLocal()
    try:
        assert db.query(SymptomCheck).filter(SymptomCheck.patient_id == pid,
                                             SymptomCheck.severity == "EMERGENCY").count() == 1
    finally:
        db.close()


def test_groq_marks_dose_through_router(monkeypatch):
    with TestClient(app) as client:
        pid, due = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("get_today_schedule", {})]),
            _msg(tool_calls=[_call("mark_dose_taken", {"dose_id": due[0]}, "c2")]),
            _msg(content="Done. Morning dose recorded."),
        ])
        body = _turn(client, pid, "Maine subah wali le li").json()
    assert body["reply"] == "Done. Morning dose recorded."
    assert body["actions"][0]["type"] == "dose_taken"
    assert _dose_state(due[0]) == "taken"


def test_model_cannot_downgrade_rule_engine_emergency(monkeypatch):
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("update_symptom_check",
                                   {"symptom_ids": ["chest_pain"], "answers": {"difficulty_breathing": True}})]),
            _msg(content="This is nothing serious, just rest."),
        ])
        body = _turn(client, pid, "seene mein dard hai, thoda saans lene mein dikkat").json()
    assert body["actions"][-1]["type"] == "emergency"
    assert "nothing serious" not in body["reply"]
    assert "112" in body["reply"]


def test_symptom_state_round_trips_between_turns(monkeypatch):
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("update_symptom_check", {"symptom_ids": ["headache"], "symptom_labels": ["sir dard"]})]),
            _msg(content="Ye kab se ho raha hai?"),
        ])
        body = _turn(client, pid, "Mere sir mein bahut dard ho raha hai").json()
    assert body["reply"] == "Ye kab se ho raha hai?"
    assert body["state"]["symptom"]["ids"] == ["headache"]


def test_bad_tool_calls_do_not_break_the_turn(monkeypatch):
    with TestClient(app) as client:
        pid, due = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("drop_database", {}), _call("mark_dose_taken", "{not json", "c2")]),
            _msg(content="Sorry, could you say that again?"),
        ])
        r = _turn(client, pid, "hmm")
    assert r.status_code == 200
    assert r.json()["reply"] == "Sorry, could you say that again?"
    assert _dose_state(due[0]) == "pending"


def test_groq_failure_falls_back(monkeypatch):
    with TestClient(app) as client:
        pid, due = _setup(client)
        install_fake_groq(monkeypatch, [RuntimeError("groq down")])
        body = _turn(client, pid, "maine dawai le li").json()
    assert body["mode"] == "fallback"
    assert _dose_state(due[0]) == "taken"


def test_fallback_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with TestClient(app) as client:
        pid, due = _setup(client)
        body = _turn(client, pid, "maine dawai le li").json()
        assert body["mode"] == "fallback"
        assert body["actions"][0]["type"] == "dose_taken"
        assert _dose_state(due[0]) == "taken"

        body = _turn(client, pid, "meri agli medicine kab hai").json()
        assert "Telma" in body["reply"] or "Metformin" in body["reply"]

        body = _turn(client, pid, "timeline dikhao").json()
        assert {"type": "navigate", "screen": "timeline"} in body["actions"]

        body = _turn(client, pid, "mujhe chakkar aa rahe hain").json()
        assert {"type": "navigate", "screen": "triage"} in body["actions"]


def test_fallback_asks_when_two_doses_are_due(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with TestClient(app) as client:
        pid, due = _setup(client, due_now=2)
        body = _turn(client, pid, "I took my medicine").json()
    assert body["actions"][0]["type"] == "choose_dose"
    assert {o["dose_id"] for o in body["actions"][0]["options"]} == set(due)
    assert all(_dose_state(d) == "pending" for d in due)


def test_validation_and_permissions(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with TestClient(app) as client:
        pid, _ = _setup(client)
        assert _turn(client, pid, "").status_code == 422
        assert _turn(client, pid, "   ").status_code == 422
        assert _turn(client, pid, "x" * 1001).status_code == 422
        assert _turn(client, pid, "hi", lang="fr").status_code == 422
        register_and_login(client)
        assert _turn(client, pid, "hi").status_code == 403


def test_rate_limit(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(voice_assistant, "RATE_LIMIT_PER_MINUTE", 2)
    with TestClient(app) as client:
        pid, _ = _setup(client)
        assert _turn(client, pid, "hello").status_code == 200
        assert _turn(client, pid, "hello").status_code == 200
        assert _turn(client, pid, "hello").status_code == 429


def test_voice_available(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with TestClient(app) as client:
        register_and_login(client)
        assert client.get("/voice/available").json() == {"available": False}


def test_short_voice_link_redirects():
    with TestClient(app) as client:
        r = client.get("/voice", follow_redirects=False)
        assert r.status_code in (302, 307)
        assert r.headers["location"] == "/static/voice.html"
        assert client.get("/static/voice.html").status_code == 200
        assert client.get("/static/manifest.webmanifest").status_code == 200


def test_empty_model_reply_gets_one_forced_text_answer(monkeypatch):
    calls = []
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("get_next_dose", {})]),
            _msg(content=""),
            _msg(content="Aapki agli dawai 8:30 baje hai."),
        ], calls)
        body = _turn(client, pid, "agli dawai kab hai").json()
    assert body["reply"] == "Aapki agli dawai 8:30 baje hai."
    assert calls[-1]["tool_choice"] == "none"


def test_symptom_labels_are_not_duplicated(monkeypatch):
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("update_symptom_check", {"symptom_ids": ["headache"], "symptom_labels": ["sir dard"]})]),
            _msg(content="Kab se?"),
        ])
        body = _turn(client, pid, "sir dard", state={"symptom": {"ids": ["headache"], "labels": ["sir dard"], "answers": {}}}).json()
    assert body["state"]["symptom"]["labels"] == ["sir dard"]
