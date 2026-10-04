"""
The patient's daily routine: dose times follow THEIR day (morning / afternoon / evening / night / bedtime,
before-or-after food) instead of one fixed clock for everyone, and the optional reminders are theirs to choose.
"""

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import reminders  # noqa: E402
import routine  # noqa: E402
from conftest import register_and_login  # noqa: E402
from db import AuditLog, Dose, Medicine, PatientRoutine, Prescription, PushSubscription, ReminderLog, SessionLocal  # noqa: E402
from main import app  # noqa: E402
from shorthand import DEFAULT_SLOT_TIMES  # noqa: E402
from timing_helpers import new_patient  # noqa: E402

MINE = {"morning": "07:00", "afternoon": "13:00", "evening": "17:30", "night": "21:00", "bedtime": "23:00",
        "notify_soon": True, "notify_followup": True}


def confirm(client, pid, line):
    pres = client.post("/prescriptions", json={"patient_id": pid, "lines": [line]}).json()
    assert client.post(f"/prescriptions/{pres['prescription_id']}/confirm").status_code == 200
    return pres["prescription_id"]


def dose_rows(prescription_id):
    db = SessionLocal()
    try:
        meds = db.query(Medicine).filter_by(prescription_id=prescription_id).all()
        return meds[0].id, sorted((d.id, d.scheduled_at, d.state) for m in meds for d in m.doses)
    finally:
        db.close()


def hhmm(rows):
    return {r[1].strftime("%H:%M") for r in rows}


# ---------------------------------------------------------------- the settings

def test_default_routine_is_the_standard_times_with_all_reminders_on():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        r = client.get(f"/patients/{pid}/routine").json()
        assert r["times"] == DEFAULT_SLOT_TIMES and r["is_saved"] is False
        assert r["notify_soon"] is True and r["notify_followup"] is True
        assert r["labels"]["morning"] == "Morning (after breakfast)"


def test_saving_the_routine_is_validated_audited_and_owner_only():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        saved = client.put(f"/patients/{pid}/routine", json=MINE)
        assert saved.status_code == 200 and saved.json()["times"]["morning"] == "07:00" and saved.json()["is_saved"]
        assert client.get(f"/patients/{pid}/routine").json()["times"]["bedtime"] == "23:00"
        db = SessionLocal()
        try:
            assert db.query(AuditLog).filter_by(patient_id=pid, action="routine_updated").count() == 1
        finally:
            db.close()
        client.put(f"/patients/{pid}/routine", json=MINE)                       # unchanged -> no second audit entry
        db = SessionLocal()
        try:
            assert db.query(AuditLog).filter_by(patient_id=pid, action="routine_updated").count() == 1
        finally:
            db.close()

        bad_order = client.put(f"/patients/{pid}/routine", json={**MINE, "night": "12:00"})
        assert bad_order.status_code == 422 and "in order" in bad_order.json()["detail"]
        assert client.put(f"/patients/{pid}/routine", json={**MINE, "morning": "7am"}).status_code == 422
        assert client.put(f"/patients/{pid}/routine", json={**MINE, "morning": "25:00"}).status_code == 422

        stranger = TestClient(app)
        register_and_login(stranger)
        assert stranger.put(f"/patients/{pid}/routine", json=MINE).status_code == 403
        assert stranger.get(f"/patients/{pid}/routine").status_code == 403
        assert stranger.post(f"/patients/{pid}/routine/apply").status_code == 403


def test_apply_before_saving_a_routine_is_refused():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        assert client.post(f"/patients/{pid}/routine/apply").status_code == 409


# ---------------------------------------------------------------- new medicines follow the routine

def test_without_a_saved_routine_nothing_changes_standard_times_are_used():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _, rows = dose_rows(confirm(client, pid, "Tab Dolo 650mg 1-0-1 PC x3d"))
        assert rows and hhmm(rows) <= {"08:30", "20:30"}


def test_a_new_medicine_is_timed_by_the_routine_and_before_food_is_30_minutes_earlier():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        client.put(f"/patients/{pid}/routine", json=MINE)
        _, after_food = dose_rows(confirm(client, pid, "Tab Dolo 650mg 1-0-1 PC x3d"))
        assert after_food and hhmm(after_food) <= {"07:00", "21:00"}
        _, before_food = dose_rows(confirm(client, pid, "Tab Pantop 40mg 1-0-1 AC x3d"))
        assert before_food and hhmm(before_food) <= {"06:30", "20:30"}


def test_exact_time_and_every_n_hours_medicines_ignore_the_routine():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        client.put(f"/patients/{pid}/routine", json=MINE)
        _, rows = dose_rows(confirm(client, pid, "Tab Dolo 650mg Q8H x3d"))
        assert rows and hhmm(rows) <= {"06:00", "14:00", "22:00"}


def test_medicines_in_one_slot_follow_the_same_routine_time_for_that_slot():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        client.put(f"/patients/{pid}/routine", json=MINE)
        a = hhmm(dose_rows(confirm(client, pid, "Tab Dolo 650mg 1-0-0 PC x2d"))[1])
        b = hhmm(dose_rows(confirm(client, pid, "Tab Crocin 500mg 1-0-0 PC x2d"))[1])
        assert a == b == {"07:00"}
        # keeping real spacing between specific medicines is the job of the label-based conflict rules, not of the routine


# ---------------------------------------------------------------- moving the current medicines

def test_apply_moves_only_untouched_future_doses_and_is_idempotent():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        med_id, rows = dose_rows(confirm(client, pid, "Tab Dolo 650mg 1-0-1 PC x4d"))
        assert hhmm(rows) <= {"08:30", "20:30"} and len(rows) >= 6
        future = [r for r in rows if r[1] > datetime.utcnow() + timedelta(hours=3)]
        taken_id, moved_by_rule_id, normal_id = future[0][0], future[1][0], future[2][0]
        db = SessionLocal()
        try:
            db.query(Dose).filter_by(id=taken_id).update({"state": "taken", "acted_at": datetime.utcnow()})
            d = db.query(Dose).get(moved_by_rule_id)
            d.scheduled_at = d.scheduled_at.replace(hour=12, minute=15)                  # e.g. moved for a spacing rule
            db.add(ReminderLog(dose_id=normal_id, kind="lead", channel="push"))
            db.commit()
        finally:
            db.close()

        client.put(f"/patients/{pid}/routine", json=MINE)
        result = client.post(f"/patients/{pid}/routine/apply").json()
        assert result["medicines_changed"] == 1 and result["doses_moved"] >= 3

        _, after = dose_rows(rows and db_prescription(med_id))
        by_id = {r[0]: r for r in after}
        assert by_id[taken_id][1].strftime("%H:%M") in ("08:30", "20:30")                # taken: untouched
        assert by_id[moved_by_rule_id][1].strftime("%H:%M") == "12:15"                   # rule-moved: untouched
        assert by_id[normal_id][1].strftime("%H:%M") in ("07:00", "21:00")               # normal future dose: moved
        db = SessionLocal()
        try:
            assert db.query(ReminderLog).filter_by(dose_id=normal_id).count() == 0       # new time re-arms its reminders
            assert json.loads(db.query(Medicine).get(med_id).times) == ["07:00", "21:00"]
            assert db.query(AuditLog).filter_by(patient_id=pid, action="routine_applied").count() == 1
        finally:
            db.close()

        again = client.post(f"/patients/{pid}/routine/apply").json()
        assert again == {"medicines_changed": 0, "doses_moved": 0}                       # repeating changes nothing


def db_prescription(med_id):
    db = SessionLocal()
    try:
        return db.query(Medicine).get(med_id).prescription_id
    finally:
        db.close()


def test_apply_never_drags_a_dose_into_the_past():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        client.put(f"/patients/{pid}/routine", json={**MINE, "morning": "05:00"})
        day = datetime(2026, 10, 5)
        db = SessionLocal()
        try:
            pres = Prescription(patient_id=pid, status="confirmed", source="manual")
            db.add(pres)
            db.commit()
            med = Medicine(prescription_id=pres.id, raw_text="Tab X", name="X", status="verified", confidence=1.0,
                           slots=json.dumps(["morning"]), times=json.dumps(["08:30"]), food="any", schedule_code="OD")
            db.add(med)
            db.commit()
            today = Dose(medicine_id=med.id, scheduled_at=day.replace(hour=8, minute=30), state="pending")
            tomorrow = Dose(medicine_id=med.id, scheduled_at=(day + timedelta(days=1)).replace(hour=8, minute=30), state="pending")
            db.add_all([today, tomorrow])
            db.commit()
            now = day.replace(hour=6)                                       # 05:00 has already passed today
            result = routine.move_pending_doses(db, pid, now)
            db.refresh(today)
            db.refresh(tomorrow)
            assert today.scheduled_at.strftime("%H:%M") == "08:30"           # not dragged to 05:00, which is behind us
            assert tomorrow.scheduled_at.strftime("%H:%M") == "05:00"
            assert result == {"medicines_changed": 1, "doses_moved": 1}
        finally:
            db.close()


# ---------------------------------------------------------------- reminder options

def _ist(h, m=0):
    return datetime(2026, 10, 5, h, m)


def _utc(dt):
    return dt - timedelta(hours=5, minutes=30)


class Rec:
    def __init__(self):
        self.kinds = []

    def push(self, e, p, a, payload):
        self.kinds.append(payload["kind"])
        return "sent"

    def wa(self, phone, body):
        return True


def _setup(client, **toggles):
    pid, _ = new_patient(client)
    client.put(f"/patients/{pid}/settings", json={"timezone": "Asia/Kolkata"})
    client.put(f"/patients/{pid}/routine", json={**MINE, **toggles})
    client.post(f"/patients/{pid}/push/subscribe",
                json={"endpoint": f"https://push.example.test/{pid}", "keys": {"p256dh": "k" * 20, "auth": "a" * 12}})
    from timing_helpers import add_medicine
    add_medicine(pid, "Telmisartan", [_ist(8)])
    return pid


@pytest.fixture(autouse=True)
def _clean():
    from db import init_db
    init_db()
    db = SessionLocal()
    try:
        for model in (ReminderLog, Dose, PushSubscription):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()
    yield


def _run_day(db, rec):
    for h, m in ((7, 20), (7, 35), (7, 51), (8, 0), (8, 16)):
        reminders.run_reminder_sweep(db, _utc(_ist(h, m)), rec.push, rec.wa)


def test_all_reminders_on_sends_early_last_due_and_followup():
    with TestClient(app) as client:
        _setup(client)
        db, rec = SessionLocal(), Rec()
        try:
            _run_day(db, rec)
            assert rec.kinds == ["lead", "soon", "due", "followup"]
        finally:
            db.close()


def test_the_last_heads_up_and_the_followup_can_be_switched_off_but_the_due_reminder_never_is():
    with TestClient(app) as client:
        _setup(client, notify_soon=False, notify_followup=False)
        db, rec = SessionLocal(), Rec()
        try:
            _run_day(db, rec)
            assert rec.kinds == ["lead", "due"]
        finally:
            db.close()


def test_ui_has_a_routine_card_in_settings():
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    push = open(os.path.join(base, "push.js"), encoding="utf-8").read()
    html = open(os.path.join(base, "index.html"), encoding="utf-8").read()
    app_js = open(os.path.join(base, "app.js"), encoding="utf-8").read()
    assert "async function renderRoutineCard" in push and "Save &amp; move my current medicines" in push
    assert 'id="settingsRoutine"' in html and "renderRoutineCard(document.getElementById('settingsRoutine')" in app_js
