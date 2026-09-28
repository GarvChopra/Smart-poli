"""CALM → HELP → RECHECK → ESCALATE: the voice symptom experience."""
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from db import SessionLocal, SymptomCheck, User, log_audit  # noqa: E402
from conftest import register_and_login  # noqa: E402
from voice_safety import scan_red_flags  # noqa: E402
from care_guidance import guidance_for  # noqa: E402
from triage_service import record_symptom_check  # noqa: E402
from voice_tools import ToolContext, run_tool, RULESET  # noqa: E402
import voice_assistant  # noqa: E402
from test_voice_turn import install_fake_groq, _msg, _call  # noqa: E402


@pytest.fixture(autouse=True)
def _no_key_and_fresh_limits(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    voice_assistant.reset_rate_limits()


def _patient(client, **extra):
    user = register_and_login(client)
    pid = client.post("/patients", json={"name": "Calm Patient", "allergies": "Penicillin",
                                         "emergency_contact": "Anita, +91 98765 43210", **extra}).json()["id"]
    return user["id"], pid


def _turn(client, pid, text, state=None, **extra):
    return client.post(f"/patients/{pid}/voice/turn", json={
        "text": text, "lang": "hi", "client_time": datetime.now().astimezone().isoformat(),
        "history": [], "state": state or {}, **extra}).json()


def _checks(pid):
    db = SessionLocal()
    try:
        return db.query(SymptomCheck).filter(SymptomCheck.patient_id == pid).order_by(SymptomCheck.id).all()
    finally:
        db.close()


# ---------------------------------------------------------------- red flags: two tiers

@pytest.mark.parametrize("text", [
    "papa behosh ho gaye", "he is having a seizure", "mujhe khudkushi karni hai",
    "maine zyada goliyan kha li", "her face is drooping", "he is gasping and choking",
    "saans ki wajah se bol nahi pa raha",
])
def test_unmistakable_immediate_risk(text):
    assert scan_red_flags(text)["immediate"], text


@pytest.mark.parametrize("text,symptom,first_q", [
    ("Mujhe saans lene mein thodi dikkat ho rahi hai", "breathlessness", "cannot_speak_full_sentence"),
    ("mujhe saans nahi aa rahi", "breathlessness", "cannot_speak_full_sentence"),
    ("I can't breathe properly", "breathlessness", "cannot_speak_full_sentence"),
    ("seene mein dard ho raha hai", "chest_pain", "difficulty_breathing"),
    ("bahut zyada khoon beh raha hai", "bleeding", "does_not_stop_with_pressure"),
])
def test_worrying_words_are_verified_not_escalated(text, symptom, first_q):
    flags = scan_red_flags(text)
    assert not flags["immediate"], text
    assert flags["verify"][0]["symptom"] == symptom
    assert flags["verify"][0]["questions"][0] == first_q


def test_past_events_are_not_immediate():
    assert not scan_red_flags("I fainted last year")["immediate"]
    assert not scan_red_flags("pichle saal mujhe daura pada tha")["immediate"]


# ---------------------------------------------------------------- calm verification turn

def test_breathing_worry_gets_one_calm_question_not_an_emergency():
    with TestClient(app) as client:
        _, pid = _patient(client)
        body = _turn(client, pid, "Mujhe saans lene mein thodi dikkat ho rahi hai")
    assert not any(a["type"] == "emergency" for a in body["actions"])
    assert "poora sentence" in body["reply"]
    assert body["actions"][0]["type"] == "calm_check"
    assert body["state"]["symptom"]["ids"] == ["breathlessness"]
    assert "cannot_speak_full_sentence" in body["state"]["symptom"]["asked"]
    assert _checks(pid) == []


def test_verification_answer_mild_continues_calmly():
    with TestClient(app) as client:
        _, pid = _patient(client)
        first = _turn(client, pid, "Mujhe saans lene mein thodi dikkat ho rahi hai")
        body = _turn(client, pid, "Nahi, halki hai, main bol pa raha hoon", state=first["state"])
    assert not any(a["type"] == "emergency" for a in body["actions"])
    assert body["state"]["symptom"]["answers"]["cannot_speak_full_sentence"] is False


def test_verification_answer_severe_escalates_through_rules():
    with TestClient(app) as client:
        _, pid = _patient(client)
        first = _turn(client, pid, "Mujhe saans lene mein thodi dikkat ho rahi hai")
        body = _turn(client, pid, "Haan, bahut zyada, sentence poora nahi bol pa raha", state=first["state"])
    assert body["actions"][-1]["type"] == "emergency"
    checks = _checks(pid)
    assert checks[-1].severity == "EMERGENCY"
    assert "Too breathless" in checks[-1].reasons or "breathless" in checks[-1].reasons.lower()


def test_immediate_risk_still_gets_help_straight_away():
    with TestClient(app) as client:
        _, pid = _patient(client)
        body = _turn(client, pid, "papa behosh ho gaye hain")
    assert body["actions"][0]["type"] == "emergency"


# ---------------------------------------------------------------- patient record as context

def test_patient_context_uses_existing_record():
    with TestClient(app) as client:
        uid, pid = _patient(client)
        client.put(f"/patients/{pid}/emergency-card/profile", json={"conditions": "Asthma"})
        pres = client.post("/prescriptions", json={"patient_id": pid, "lines": ["Asthalin inhaler 2 puffs SOS"]}).json()
        client.post(f"/prescriptions/{pres['prescription_id']}/confirm")
    db = SessionLocal()
    try:
        log_audit(db, pid, "doctor:1", "clinical_note", "If wheezy, 2 puffs of reliever and recheck in 10 min")
        record_symptom_check(db, pid, "patient", RULESET, ["breathlessness"], {"at_rest": True})
        ctx = ToolContext(db=db, patient_id=pid, user=db.query(User).get(uid), now=datetime.now(), lang="hi", state={})
        c = run_tool(ctx, "get_patient_context", {})
    finally:
        db.close()
    assert c["conditions"] == "Asthma"
    assert c["allergies"] == "Penicillin"
    assert any("Asthalin" in m["name"] for m in c["as_needed_medicines"])
    assert "reliever" in c["doctor_notes"][0]["note"]
    assert c["recent_symptom_checks"][0]["symptoms"] == ["breathlessness"]


def test_record_is_in_every_turns_prompt(monkeypatch):
    calls = []
    with TestClient(app) as client:
        _, pid = _patient(client)
        client.put(f"/patients/{pid}/emergency-card/profile", json={"conditions": "Asthma"})
        install_fake_groq(monkeypatch, [_msg(content="Kaise madad karoon?")], calls)
        _turn(client, pid, "hello")
    system = calls[0]["messages"][0]["content"]
    assert "Asthma" in system and "Penicillin" in system


# ---------------------------------------------------------------- guidance: sourced, prescribed first

def test_guidance_has_sources_and_prescribed_first():
    g = guidance_for(["headache"], "LOW", "hi", prescribed=[{"name": "Dolo 650", "instruction": "SOS"}])
    assert g["prescribed"][0]["name"] == "Dolo 650"
    assert g["general"] and all(item["source_url"].startswith("https://www.nhs.uk/") for item in g["general"])
    assert g["recheck_minutes"] == 15
    text = " ".join(i["text"] for i in g["general"]).lower()
    assert "paracetamol" not in text and "ibuprofen" not in text


@pytest.mark.parametrize("sid", list(RULESET["symptoms"]))
def test_every_symptom_gets_sourced_steps_to_try_first(sid):
    for severity in ("LOW", "MODERATE"):
        for lang in ("en", "hi"):
            g = guidance_for([sid], severity, lang, prescribed=[])
            assert g["general"], (sid, severity, lang)
            assert g["recheck_minutes"], sid
            assert all(i["source_url"].startswith("https://") and i["source_title"] for i in g["general"])


def test_steps_never_name_a_medicine_or_device():
    """Only the patient's own prescription may name a medicine."""
    for sid in RULESET["symptoms"]:
        for lang in ("en", "hi"):
            text = " ".join(i["text"] for i in guidance_for([sid], "MODERATE", lang, prescribed=[])["general"]).lower()
            for banned in ("nebuli", "inhaler", "paracetamol", "ibuprofen", "spray", "tablet", "dawai", "goli"):
                assert banned not in text, (sid, lang, banned)


def test_no_guidance_or_recheck_for_emergency():
    g = guidance_for(["headache"], "EMERGENCY", "en", prescribed=[{"name": "Dolo", "instruction": "SOS"}])
    assert g == {"prescribed": [], "general": [], "recheck_minutes": None}


def test_finished_low_check_returns_guidance_and_recheck(monkeypatch):
    with TestClient(app) as client:
        uid, pid = _patient(client)
    db = SessionLocal()
    try:
        ctx = ToolContext(db=db, patient_id=pid, user=db.query(User).get(uid), now=datetime.now(), lang="hi",
                          state={"symptom": {"ids": ["headache"], "answers": {}, "asked":
                                             ["worst_ever_sudden", "confusion", "fever_and_stiff_neck", "vision_changes"]}})
        run_tool(ctx, "update_symptom_check", {"answers": {q: False for q in ctx.asked_before}})
        res = run_tool(ctx, "finish_symptom_check", {})
    finally:
        db.close()
    assert res["severity"] == "LOW"
    assert res["guidance"]["general"]
    kinds = [a["type"] for a in ctx.actions]
    assert "triage_result" in kinds and "recheck" in kinds
    assert ctx.state["recheck"]["symptom_ids"] == ["headache"]


# ---------------------------------------------------------------- recheck

def _with_recheck(client, pid):
    return {"recheck": {"symptom_ids": ["headache"], "labels": ["sir dard"], "due_at": datetime.now().isoformat()}}


def test_recheck_better_keeps_monitoring():
    with TestClient(app) as client:
        _, pid = _patient(client)
        body = _turn(client, pid, "better", state=_with_recheck(client, pid), recheck="better")
    assert "recheck" not in body["state"]
    assert not any(a["type"] == "emergency" for a in body["actions"])
    assert _checks(pid) == []


def test_recheck_same_moves_to_medical_review():
    with TestClient(app) as client:
        _, pid = _patient(client)
        body = _turn(client, pid, "same", state=_with_recheck(client, pid), recheck="same")
    result = next(a for a in body["actions"] if a["type"] == "triage_result")
    assert result["severity"] == "MODERATE"
    assert _checks(pid)[-1].severity == "MODERATE"


def test_recheck_worse_reassesses_with_the_rules():
    with TestClient(app) as client:
        _, pid = _patient(client)
        body = _turn(client, pid, "worse", state=_with_recheck(client, pid), recheck="worse")
    sym = body["state"]["symptom"]
    assert sym["ids"] == ["headache"] and sym["answers"] == {}
    assert sym["floor"] == "MODERATE"
    assert not any(a["type"] == "emergency" for a in body["actions"])


def test_min_severity_only_raises():
    db = SessionLocal()
    try:
        with TestClient(app) as client:
            _, pid = _patient(client)
        low, _ = record_symptom_check(db, pid, "t", RULESET, ["headache"], {}, min_severity="MODERATE")
        assert low["severity"] == "MODERATE"
        emer, _ = record_symptom_check(db, pid, "t", RULESET, ["headache"], {"confusion": True}, min_severity="MODERATE")
        assert emer["severity"] == "EMERGENCY"
    finally:
        db.close()


def test_fixed_replies_follow_the_patients_language_not_the_toggle():
    with TestClient(app) as client:
        _, pid = _patient(client)
        r = client.post(f"/patients/{pid}/voice/turn", json={
            "text": "Mujhe saans lene mein thodi dikkat ho rahi hai", "lang": "en",
            "client_time": datetime.now().astimezone().isoformat(), "history": [], "state": {}}).json()
        assert "poora sentence" in r["reply"]
        r = client.post(f"/patients/{pid}/voice/turn", json={
            "text": "I have some trouble breathing", "lang": "hi",
            "client_time": datetime.now().astimezone().isoformat(), "history": [], "state": {}}).json()
        assert "full sentence" in r["reply"]


def test_reply_says_which_language_it_is_in():
    with TestClient(app) as client:
        _, pid = _patient(client)
        r = client.post(f"/patients/{pid}/voice/turn", json={
            "text": "meri agli dawai kab hai", "lang": "en",
            "client_time": datetime.now().astimezone().isoformat(), "history": [], "state": {}}).json()
    assert r["lang"] == "hi"
