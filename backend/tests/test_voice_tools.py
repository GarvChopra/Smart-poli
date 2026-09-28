import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app, RULESET  # noqa: E402
from db import SessionLocal, Dose, Medicine, Prescription, User, SymptomCheck, AuditLog  # noqa: E402
from conftest import register_and_login  # noqa: E402
from triage import evaluate_check  # noqa: E402
import voice_tools  # noqa: E402
from voice_tools import ToolContext, run_tool  # noqa: E402


def _setup(client, lines=("Tab Metformin 500mg 1-0-1 x5d",), prn=False):
    user = register_and_login(client)
    pid = client.post("/patients", json={"name": "Voice Patient", "allergies": "Penicillin",
                                         "emergency_contact": "Anita, +91 98765 43210"}).json()["id"]
    all_lines = list(lines) + (["Tab Dolo 650mg SOS"] if prn else [])
    pres = client.post("/prescriptions", json={"patient_id": pid, "lines": all_lines}).json()
    client.post(f"/prescriptions/{pres['prescription_id']}/confirm")
    return user["id"], pid


def _ctx(db, pid, user_id, now=None, state=None):
    return ToolContext(db=db, patient_id=pid, user=db.query(User).get(user_id),
                       now=now or datetime.utcnow(), lang="en", state=state or {})


def _doses(db, pid):
    return (db.query(Dose).join(Medicine).join(Prescription)
            .filter(Prescription.patient_id == pid).order_by(Dose.scheduled_at, Dose.id).all())


def _set_dose_time(db, dose, when):
    dose.scheduled_at = when
    dose.state = "pending"
    db.commit()


def test_schedule_and_next_dose():
    with TestClient(app) as client:
        uid, pid = _setup(client)
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        doses = _doses(db, pid)
        _set_dose_time(db, doses[0], now - timedelta(minutes=30))
        _set_dose_time(db, doses[1], now + timedelta(hours=3))
        ctx = _ctx(db, pid, uid, now)
        sched = run_tool(ctx, "get_today_schedule", {})
        assert any(d["dose_id"] == doses[0].id and d["can_mark_taken"] for d in sched["doses"])
        nxt = run_tool(ctx, "get_next_dose", {})
        assert nxt["next_dose"]["dose_id"] == doses[1].id
        assert "Metformin" in nxt["next_dose"]["medicine"]
    finally:
        db.close()


def test_mark_dose_taken_writes_same_state_and_audit_as_button():
    with TestClient(app) as client:
        uid, pid = _setup(client)
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        doses = _doses(db, pid)
        _set_dose_time(db, doses[0], now - timedelta(minutes=20))
        ctx = _ctx(db, pid, uid, now)
        result = run_tool(ctx, "mark_dose_taken", {"dose_id": doses[0].id})
        assert result["ok"] is True
        db.refresh(doses[0])
        assert doses[0].state == "taken"
        assert ctx.actions[0]["type"] == "dose_taken"
        assert ctx.actions[0]["dose_id"] == doses[0].id
        assert "next_dose" in result
        assert db.query(AuditLog).filter(AuditLog.patient_id == pid,
                                         AuditLog.action == "dose_taken_via_voice").count() == 1
    finally:
        db.close()


def test_mark_dose_taken_corrects_auto_missed_dose_in_window():
    with TestClient(app) as client:
        uid, pid = _setup(client)
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        dose = _doses(db, pid)[0]
        dose.scheduled_at = now - timedelta(hours=4)
        dose.state = "missed"
        db.commit()
        assert run_tool(_ctx(db, pid, uid, now), "mark_dose_taken", {"dose_id": dose.id})["ok"] is True
    finally:
        db.close()


def test_mark_dose_taken_refusals():
    with TestClient(app) as client:
        uid, pid = _setup(client)
        other_uid, other_pid = _setup(client)
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        far = _doses(db, pid)[-1]
        _set_dose_time(db, far, now + timedelta(days=2))
        ctx = _ctx(db, pid, uid, now)
        assert run_tool(ctx, "mark_dose_taken", {"dose_id": far.id})["ok"] is False

        others = _doses(db, other_pid)[0]
        _set_dose_time(db, others, now)
        r = run_tool(ctx, "mark_dose_taken", {"dose_id": others.id})
        assert r["ok"] is False
        db.refresh(others)
        assert others.state == "pending"

        mine = _doses(db, pid)[0]
        _set_dose_time(db, mine, now)
        run_tool(ctx, "mark_dose_taken", {"dose_id": mine.id})
        assert run_tool(ctx, "mark_dose_taken", {"dose_id": mine.id})["ok"] is False
        assert run_tool(ctx, "mark_dose_taken", {"dose_id": "abc"})["ok"] is False
    finally:
        db.close()


def test_prn_logging_and_medicines_and_adherence():
    with TestClient(app) as client:
        uid, pid = _setup(client, prn=True)
    db = SessionLocal()
    try:
        ctx = _ctx(db, pid, uid)
        meds = run_tool(ctx, "get_medicines", {})["medicines"]
        prn = next(m for m in meds if m["as_needed"])
        assert run_tool(ctx, "log_prn_taken", {"medicine_id": prn["medicine_id"]})["ok"] is True
        assert ctx.actions[-1]["type"] == "prn_logged"
        regular = next(m for m in meds if not m["as_needed"])
        assert run_tool(ctx, "log_prn_taken", {"medicine_id": regular["medicine_id"]})["ok"] is False
        adh = run_tool(ctx, "get_adherence", {})
        assert set(adh["overall"]) >= {"taken", "missed", "skipped"}
    finally:
        db.close()


def test_emergency_card_history_and_open_screen():
    with TestClient(app) as client:
        uid, pid = _setup(client)
    db = SessionLocal()
    try:
        ctx = _ctx(db, pid, uid)
        card = run_tool(ctx, "get_emergency_card", {})
        assert card["allergies"] == "Penicillin"
        assert ctx.actions[-1]["type"] == "open_card"
        hist = run_tool(ctx, "get_treatment_history", {})
        assert hist["prescriptions"][0]["medicines"]
        assert run_tool(ctx, "open_screen", {"screen": "timeline"})["ok"] is True
        assert ctx.actions[-1] == {"type": "navigate", "screen": "timeline"}
        assert run_tool(ctx, "open_screen", {"screen": "../../admin"})["ok"] is False
    finally:
        db.close()


def test_prescription_decode_then_confirm_only_in_a_later_turn():
    with TestClient(app) as client:
        uid, pid = _setup(client)
    db = SessionLocal()
    try:
        ctx = _ctx(db, pid, uid)
        draft = run_tool(ctx, "decode_prescription", {"text": "Tab Telma 40mg 1-0-0 x30d\nTab Pan 40 1-0-0 AC x10d"})
        pres_id = draft["prescription_id"]
        assert len(draft["medicines"]) == 2
        # same turn: refused (patient hasn't been asked yet)
        assert run_tool(ctx, "confirm_prescription", {"prescription_id": pres_id})["ok"] is False
        # a fabricated id: refused
        ctx2 = _ctx(db, pid, uid, state=ctx.state)
        assert run_tool(ctx2, "confirm_prescription", {"prescription_id": pres_id + 999})["ok"] is False
        # next turn, same id: allowed
        ctx3 = _ctx(db, pid, uid, state=ctx.state)
        assert run_tool(ctx3, "confirm_prescription", {"prescription_id": pres_id})["ok"] is True
        assert ctx3.actions[-1]["type"] == "prescription_confirmed"
        assert "pending_prescription_id" not in ctx3.state
    finally:
        db.close()


def test_symptom_check_asks_rule_questions_then_rule_engine_decides():
    with TestClient(app) as client:
        uid, pid = _setup(client)
    db = SessionLocal()
    try:
        ctx = _ctx(db, pid, uid)
        r = run_tool(ctx, "update_symptom_check", {"symptom_ids": ["headache"], "symptom_labels": ["sir dard"],
                                                  "answers": {}})
        pending = {q["id"] for q in r["questions_to_ask"]}
        rule_qids = {q["id"] for q in RULESET["symptoms"]["headache"]["questions"]}
        assert pending == rule_qids
        assert all(q["text_hi"] for q in r["questions_to_ask"])

        # can't finish with questions open
        assert run_tool(ctx, "finish_symptom_check", {})["ok"] is False

        answers = {qid: False for qid in rule_qids}
        run_tool(ctx, "update_symptom_check", {"answers": answers})
        res = run_tool(ctx, "finish_symptom_check", {})
        expected = evaluate_check(RULESET, ["headache"], answers)
        assert res["severity"] == expected["severity"]
        assert ctx.actions[-1]["type"] == "triage_result"
        assert ctx.actions[-1]["severity"] == expected["severity"]
        check = db.query(SymptomCheck).filter(SymptomCheck.patient_id == pid).one()
        assert json.loads(check.symptoms) == ["headache"]
        assert check.severity == expected["severity"]
        assert "symptom" not in ctx.state
    finally:
        db.close()


def test_rule_emergency_finishes_immediately_with_emergency_action():
    with TestClient(app) as client:
        uid, pid = _setup(client)
    db = SessionLocal()
    try:
        ctx = _ctx(db, pid, uid)
        r = run_tool(ctx, "update_symptom_check", {"symptom_ids": ["chest_pain"],
                                                  "answers": {"difficulty_breathing": True}})
        assert r["severity"] == "EMERGENCY"
        assert ctx.actions[-1]["type"] == "emergency"
        assert db.query(SymptomCheck).filter(SymptomCheck.patient_id == pid,
                                             SymptomCheck.severity == "EMERGENCY").count() == 1
    finally:
        db.close()


def test_unmapped_symptom_is_not_assessed_and_not_saved():
    with TestClient(app) as client:
        uid, pid = _setup(client)
    db = SessionLocal()
    try:
        ctx = _ctx(db, pid, uid)
        run_tool(ctx, "update_symptom_check", {"symptom_ids": ["cough", "made_up"], "symptom_labels": ["khansi"]})
        res = run_tool(ctx, "finish_symptom_check", {})
        assert res["severity"] == "NOT_ASSESSED"
        assert ctx.actions[-1]["severity"] == "NOT_ASSESSED"
        assert db.query(SymptomCheck).filter(SymptomCheck.patient_id == pid).count() == 0
    finally:
        db.close()


def test_tampered_state_answers_are_revalidated():
    with TestClient(app) as client:
        uid, pid = _setup(client)
    db = SessionLocal()
    try:
        state = {"symptom": {"ids": ["headache", "not_a_symptom"], "labels": [],
                             "answers": {"bogus_q": True, "worst_ever_sudden": "yes"}}}
        ctx = _ctx(db, pid, uid, state=state)
        r = run_tool(ctx, "update_symptom_check", {})
        assert ctx.state["symptom"]["ids"] == ["headache"]
        assert "bogus_q" not in ctx.state["symptom"]["answers"]
        assert "worst_ever_sudden" not in ctx.state["symptom"]["answers"]
        assert r["ok"] is True
    finally:
        db.close()


def test_unknown_tool_is_refused():
    with TestClient(app) as client:
        uid, pid = _setup(client)
    db = SessionLocal()
    try:
        assert run_tool(_ctx(db, pid, uid), "drop_tables", {})["ok"] is False
    finally:
        db.close()


def test_tool_schemas_cover_every_tool():
    names = {t["function"]["name"] for t in voice_tools.TOOLS}
    assert names == set(voice_tools.HANDLERS)
