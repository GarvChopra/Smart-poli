"""Ongoing medicines (no end date) keep getting doses: they used to stop a month after being added, and so did their
reminders. Also: the app re-sends this phone's push subscription on every open."""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import scheduler  # noqa: E402
from db import Dose, Medicine, SessionLocal  # noqa: E402
from main import app  # noqa: E402
from timing_helpers import add_medicine, new_patient  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "static"
UTC_NOW = datetime(2026, 10, 13, 6, 30)          # 12:00 in Asia/Kolkata


@pytest.fixture
def pid():
    from db import init_db
    init_db()
    client = TestClient(app)
    p, _ = new_patient(client)
    assert client.put(f"/patients/{p}/settings", json={"timezone": "Asia/Kolkata"}).status_code == 200
    return p


def _doses(med_id):
    db = SessionLocal()
    try:
        return sorted((d.scheduled_at, d.state) for d in db.query(Dose).filter(Dose.medicine_id == med_id).all())
    finally:
        db.close()


def test_ongoing_medicine_is_kept_thirty_days_ahead(pid):
    last = datetime(2026, 10, 15, 8, 0)
    med_id, _ = add_medicine(pid, "Topup Amlodipine", [datetime(2026, 10, 14, 8, 0), last], times=["08:00", "20:00"])
    db = SessionLocal()
    try:
        added = scheduler.top_up_ongoing_doses(db, utc_now=UTC_NOW)
    finally:
        db.close()
    doses = _doses(med_id)
    assert added > 0
    after = [t for t, _ in doses if t > last]
    assert after[0] == datetime(2026, 10, 16, 8, 0)                  # day after the last one, never back-filled
    assert max(after).date() == datetime(2026, 11, 12).date()        # today + 30 (patient's own clock)
    assert all(t.strftime("%H:%M") in ("08:00", "20:00") for t in after)
    assert len(set(doses)) == len(doses)

    db = SessionLocal()
    try:
        assert scheduler.top_up_ongoing_doses(db, utc_now=UTC_NOW) == 0  # already topped up: nothing new
    finally:
        db.close()


def test_courses_removed_and_unscheduled_medicines_are_left_alone(pid):
    course, _ = add_medicine(pid, "Topup Course", [datetime(2026, 10, 14, 8, 0)], times=["08:00"], duration_days=5)
    prn, _ = add_medicine(pid, "Topup Prn", [], times=["08:00"], is_prn=True)
    draft, _ = add_medicine(pid, "Topup Draft", [], times=["08:00"])
    removed, _ = add_medicine(pid, "Topup Removed", [datetime(2026, 10, 14, 8, 0)], times=["08:00"])
    db = SessionLocal()
    try:
        m = db.query(Medicine).get(removed)
        m.status, m.field_confidence = "needs_confirmation", '{"removed": true}'
        db.commit()
        scheduler.top_up_ongoing_doses(db, utc_now=UTC_NOW)
    finally:
        db.close()
    assert len(_doses(course)) == 1
    assert _doses(prn) == [] and _doses(draft) == []
    assert len(_doses(removed)) == 1


def test_gap_is_not_back_filled_with_past_doses(pid):
    med_id, _ = add_medicine(pid, "Topup Old", [datetime(2026, 9, 1, 8, 0)], times=["08:00", "20:00"])
    db = SessionLocal()
    try:
        scheduler.top_up_ongoing_doses(db, utc_now=UTC_NOW)
    finally:
        db.close()
    new = [t for t, _ in _doses(med_id) if t > datetime(2026, 9, 1, 8, 0)]
    assert new[0] == datetime(2026, 10, 13, 20, 0)                   # 08:00 today is already past at 12:00 local


def test_app_resends_push_subscription_on_every_open():
    push = (STATIC / "push.js").read_text(encoding="utf-8")
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "async function resyncPushSubscription(patientId)" in push
    assert "Notification.permission !== 'granted'" in push            # never asks for permission by itself
    assert "resyncPushSubscription(state.patientId)" in app_js
