"""
The demo accounts must look like someone mid-course (doses today, a next dose soon) - not "history, then nothing
for two days" ("Next dose in 47 h"). fix_demo_timeline re-times only demo accounts' pending doses, never deletes.
"""

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Dose, Medicine, Patient, Prescription, SessionLocal, User, init_db  # noqa: E402
from seed import fix_demo_timeline  # noqa: E402

NOW = datetime(2026, 10, 5, 14, 0)


def make(db, email, name):
    user = User(email=email, password_hash="x", role="patient", name=name)
    db.add(user)
    db.commit()
    patient = Patient(user_id=user.id, name=name)
    db.add(patient)
    db.commit()
    pres = Prescription(patient_id=patient.id, status="confirmed", source="manual")
    db.add(pres)
    db.commit()
    med = Medicine(prescription_id=pres.id, raw_text="Tab Metformin 500mg 1-0-1", name="Metformin", status="verified",
                   confidence=1.0, times=json.dumps(["08:30", "20:30"]))
    db.add(med)
    db.commit()
    # history in the past (taken), then the rest of the course starting THREE days from today - the seed's gap
    rows = [Dose(medicine_id=med.id, scheduled_at=NOW - timedelta(days=3) + timedelta(hours=8 * i), state="taken",
                 acted_at=NOW - timedelta(days=3) + timedelta(hours=8 * i, minutes=5)) for i in range(3)]
    base = NOW.replace(hour=8, minute=30) + timedelta(days=3)
    rows += [Dose(medicine_id=med.id, scheduled_at=base + timedelta(hours=12 * i), state="pending") for i in range(6)]
    db.add_all(rows)
    db.commit()
    return med.id


def pending_times(db, med_id):
    return [d.scheduled_at for d in db.query(Dose).filter_by(medicine_id=med_id, state="pending").order_by(Dose.scheduled_at)]


def test_remaining_doses_start_today_and_the_ones_already_behind_us_are_settled():
    init_db()
    db = SessionLocal()
    try:
        demo = make(db, "realign-demo@smartpoli.demo", "Demo Patient")
        fix_demo_timeline(db, now=NOW)
        pend = pending_times(db, demo)
        assert len(pend) == 5                                                        # 6 shifted; today's 08:30 is behind 14:00 -> settled
        assert pend[0] == NOW.replace(hour=20, minute=30)                            # the next dose is this evening
        assert all(p > NOW for p in pend)                                            # nothing left "pending in the past"
        todays = [d for d in db.query(Dose).filter_by(medicine_id=demo) if d.scheduled_at.date() == NOW.date()]
        assert sorted(d.state for d in todays) == ["pending", "taken"]
        assert (pend[0] - NOW) < timedelta(hours=24)
    finally:
        db.close()


def test_it_never_touches_real_accounts():
    init_db()
    db = SessionLocal()
    try:
        real = make(db, "realign-real@example.com", "Real Patient")
        before = [(d.id, d.scheduled_at, d.state) for d in db.query(Dose).filter_by(medicine_id=real)]
        fix_demo_timeline(db, now=NOW)
        after = [(d.id, d.scheduled_at, d.state) for d in db.query(Dose).filter_by(medicine_id=real)]
        assert before == after
    finally:
        db.close()


def test_running_it_twice_changes_nothing_more_and_deletes_nothing():
    init_db()
    db = SessionLocal()
    try:
        demo = make(db, "realign-twice@smartpoli.demo", "Demo Twice")
        count = db.query(Dose).filter_by(medicine_id=demo).count()
        fix_demo_timeline(db, now=NOW)
        snapshot = sorted((d.id, d.scheduled_at, d.state) for d in db.query(Dose).filter_by(medicine_id=demo))
        fix_demo_timeline(db, now=NOW)
        assert sorted((d.id, d.scheduled_at, d.state) for d in db.query(Dose).filter_by(medicine_id=demo)) == snapshot
        assert db.query(Dose).filter_by(medicine_id=demo).count() == count
    finally:
        db.close()


def test_a_first_dose_tomorrow_morning_is_left_alone():
    init_db()
    db = SessionLocal()
    try:
        med = make(db, "realign-tomorrow@smartpoli.demo", "Tomorrow")
        for d in db.query(Dose).filter_by(medicine_id=med, state="pending"):          # pull the block to start tomorrow 08:30
            d.scheduled_at = d.scheduled_at - timedelta(days=2)
        db.commit()
        before = pending_times(db, med)
        assert before[0] == NOW.replace(hour=8, minute=30) + timedelta(days=1)
        fix_demo_timeline(db, now=NOW)
        assert pending_times(db, med) == before                                       # a normal state: not shifted
    finally:
        db.close()
