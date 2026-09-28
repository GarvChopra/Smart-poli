"""Phase 3 (patient context) and Phase 4 (memory across days) of the voice assistant."""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from db import SessionLocal, User, VoiceMessage, log_audit  # noqa: E402
from conftest import register_and_login  # noqa: E402
from triage_service import record_symptom_check  # noqa: E402
from voice_tools import ToolContext, run_tool, RULESET  # noqa: E402
import voice_assistant  # noqa: E402
from test_voice_turn import install_fake_groq, _msg, _call  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_limits():
    voice_assistant.reset_rate_limits()


def _patient(client):
    user = register_and_login(client)
    pid = client.post("/patients", json={"name": "Memory Patient", "allergies": "Sulfa",
                                         "emergency_contact": "Anita, +91 98765 43210"}).json()["id"]
    pres = client.post("/prescriptions", json={"patient_id": pid, "lines": [
        "Tab Metformin 500mg 1-0-1 x30d", "Tab Warfarin 5mg 0-0-1 x30d", "Asthalin inhaler 2 puffs SOS"]}).json()
    client.post(f"/prescriptions/{pres['prescription_id']}/confirm")
    return user["id"], pid


def _ctx(db, pid, uid, state=None):
    return ToolContext(db=db, patient_id=pid, user=db.query(User).get(uid), now=datetime.now(), lang="hi",
                       state=state or {})


def _turn(client, pid, text):
    return client.post(f"/patients/{pid}/voice/turn", json={
        "text": text, "lang": "hi", "client_time": datetime.now().astimezone().isoformat(),
        "history": [], "state": {}}).json()


# ---------------------------------------------------------------- Phase 3: targeted patient context

def test_doctor_notes_prn_history_and_safety_warnings_come_from_the_record():
    with TestClient(app) as client:
        uid, pid = _patient(client)
    db = SessionLocal()
    try:
        log_audit(db, pid, "doctor:9", "clinical_note", "If wheezy: 2 puffs of Asthalin, recheck in 10 minutes")
        log_audit(db, pid, "patient:1", "prn_taken", "medicine 3 (Asthalin) at 2026-09-27T21:00:00")
        ctx = _ctx(db, pid, uid)
        notes = run_tool(ctx, "get_doctor_notes", {})
        assert "Asthalin" in notes["notes"][0]["note"]
        prn = run_tool(ctx, "get_prn_history", {})
        assert prn["uses"] and "Asthalin" in prn["uses"][0]["detail"]
        warn = run_tool(ctx, "get_safety_warnings", {})
        assert warn["ok"] is True and "interactions" in warn and "food_warnings" in warn
        full = run_tool(ctx, "get_patient_context", {})
        assert full["emergency_contact"].startswith("Anita")
        assert "care_team" in full and "adherence" in full
    finally:
        db.close()


def test_every_turn_prompt_is_a_compact_profile_not_the_whole_record(monkeypatch):
    calls = []
    with TestClient(app) as client:
        uid, pid = _patient(client)
        db = SessionLocal()
        try:
            for i in range(6):
                log_audit(db, pid, "doctor:9", "clinical_note", f"NOTE-NUMBER-{i}")
        finally:
            db.close()
        install_fake_groq(monkeypatch, [_msg(content="Namaste")], calls)
        _turn(client, pid, "hello")
    system = calls[0]["messages"][0]["content"]
    assert "Sulfa" in system and "Metformin" in system and "Asthalin" in system
    assert sum(f"NOTE-NUMBER-{i}" in system for i in range(6)) <= 1   # notes come via get_doctor_notes


# ---------------------------------------------------------------- Phase 4: memory

def test_turns_are_stored_server_side_and_used_as_history(monkeypatch):
    calls = []
    with TestClient(app) as client:
        uid, pid = _patient(client)
        install_fake_groq(monkeypatch, [_msg(content="Kab se?"), _msg(content="Theek hai.")], calls)
        _turn(client, pid, "mujhe raat ko wheezing ho rahi thi")
        _turn(client, pid, "subah se")   # the client sends NO history
    second = [m["content"] for m in calls[1]["messages"] if m["role"] in ("user", "assistant")]
    assert "mujhe raat ko wheezing ho rahi thi" in second and "Kab se?" in second
    db = SessionLocal()
    try:
        assert db.query(VoiceMessage).filter(VoiceMessage.patient_id == pid).count() == 4
    finally:
        db.close()


def test_recall_finds_yesterdays_problem_with_its_outcome():
    with TestClient(app) as client:
        uid, pid = _patient(client)
    db = SessionLocal()
    try:
        yesterday = datetime.utcnow() - timedelta(days=1)
        db.add(VoiceMessage(patient_id=pid, role="user", content="mujhe raat ko wheezing ho rahi thi", created_at=yesterday))
        db.add(VoiceMessage(patient_id=pid, role="user", content="aaj khana achha tha", created_at=yesterday))
        db.commit()
        _, check = record_symptom_check(db, pid, "t", RULESET, ["breathlessness"], {"at_rest": True})
        check.created_at = yesterday
        db.commit()
        r = run_tool(_ctx(db, pid, uid), "recall_previous", {"query": "wheezing breathing"})
    finally:
        db.close()
    said = [m["text"] for m in r["earlier_conversations"]]
    assert "mujhe raat ko wheezing ho rahi thi" in said
    assert "aaj khana achha tha" not in said
    ep = r["earlier_symptom_checks"][0]
    assert ep["symptoms"] == ["breathlessness"] and ep["severity"] == "MODERATE"
    assert r["how_often"]["breathlessness"] >= 1
    assert "not" in r["use"].lower() and "cause" in r["use"].lower()


def test_memory_never_crosses_patients():
    with TestClient(app) as client:
        uid_a, pid_a = _patient(client)
        uid_b, pid_b = _patient(client)
    db = SessionLocal()
    try:
        db.add(VoiceMessage(patient_id=pid_a, role="user", content="SECRET-A wheezing",
                            created_at=datetime.utcnow() - timedelta(days=1)))
        db.commit()
        r = run_tool(_ctx(db, pid_b, uid_b), "recall_previous", {"query": "wheezing"})
    finally:
        db.close()
    assert all("SECRET-A" not in m["text"] for m in r["earlier_conversations"])


def test_prompt_carries_the_memory_rule_and_recent_episodes(monkeypatch):
    calls = []
    with TestClient(app) as client:
        uid, pid = _patient(client)
        db = SessionLocal()
        try:
            _, check = record_symptom_check(db, pid, "t", RULESET, ["headache"], {})
            check.created_at = datetime.utcnow() - timedelta(days=2)
            db.commit()
        finally:
            db.close()
        install_fake_groq(monkeypatch, [_msg(content="Haan?")], calls)
        _turn(client, pid, "aaj phir wahi problem ho rahi hai")
    system = calls[0]["messages"][0]["content"]
    assert "headache" in system
    assert "context, not a diagnosis" in system


def test_patient_can_delete_their_voice_history():
    with TestClient(app) as client:
        uid, pid = _patient(client)
        _turn(client, pid, "hello")
        assert client.delete(f"/patients/{pid}/voice/history").status_code == 200
        register_and_login(client)
        assert client.delete(f"/patients/{pid}/voice/history").status_code == 403
    db = SessionLocal()
    try:
        assert db.query(VoiceMessage).filter(VoiceMessage.patient_id == pid).count() == 0
    finally:
        db.close()
