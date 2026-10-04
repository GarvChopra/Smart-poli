"""
Repair of doses recorded taken far too early by the old Mark-taken bug. Dry run by default; never deletes; audited;
never touches doses taken on time or late; refuses --apply against an unconfirmed database.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import AuditLog, Dose, Medicine, Patient, Prescription, SessionLocal, User, init_db  # noqa: E402
from tools.repair_early_takes import find_early_takes, revert  # noqa: E402

BASE = datetime(2026, 10, 4, 20, 0)


def make(db, email):
    user = User(email=email, password_hash="x", role="patient", name="Repair Test")
    db.add(user)
    db.commit()
    patient = Patient(user_id=user.id, name="Repair Test")
    db.add(patient)
    db.commit()
    pres = Prescription(patient_id=patient.id, status="confirmed", source="manual")
    db.add(pres)
    db.commit()
    med = Medicine(prescription_id=pres.id, raw_text="Tab X", name="X", status="verified", confidence=1.0)
    db.add(med)
    db.commit()
    return patient.id, med.id


def dose(db, med_id, scheduled, acted, state="taken"):
    d = Dose(medicine_id=med_id, scheduled_at=scheduled, state=state, acted_at=acted)
    db.add(d)
    db.commit()
    return d.id


def test_only_doses_taken_more_than_two_hours_early_are_found_and_reverted_with_an_audit_trail():
    init_db()
    db = SessionLocal()
    try:
        pid, med = make(db, "repair-a@smartpoli.demo")
        tomorrow_8am = BASE + timedelta(hours=12.5)
        bogus = dose(db, med, tomorrow_8am, BASE)                                           # a day early: the old bug
        edge_ok = dose(db, med, BASE + timedelta(hours=2), BASE)                            # exactly 2 h early: allowed
        on_time = dose(db, med, BASE, BASE + timedelta(minutes=5))
        late = dose(db, med, BASE - timedelta(hours=5), BASE)
        pending = dose(db, med, BASE + timedelta(days=1), None, state="pending")
        found = find_early_takes(db, include_past=True)
        ids = {d.id for d, p in found}
        assert bogus in ids and not ({edge_ok, on_time, late, pending} & ids)

        assert revert(db, [(d, p) for d, p in found if d.id == bogus]) == 1
        fixed = db.query(Dose).get(bogus)
        assert fixed.state == "pending" and fixed.acted_at is None                          # back to pending; nothing deleted
        entry = db.query(AuditLog).filter_by(patient_id=pid, action="dose_taken_reverted").one()
        assert json.loads(entry.detail)["was_acted_at"] == BASE.isoformat() and entry.actor == "system:repair"
        assert db.query(Dose).get(on_time).state == "taken"
        assert bogus not in {d.id for d, p in find_early_takes(db, include_past=True)}   # idempotent
    finally:
        db.close()


def test_the_only_demo_filter_leaves_real_accounts_alone():
    init_db()
    db = SessionLocal()
    try:
        _, demo_med = make(db, "repair-demo@smartpoli.demo")
        _, real_med = make(db, "repair-real@example.com")
        demo_bad = dose(db, demo_med, BASE + timedelta(days=1), BASE)
        real_bad = dose(db, real_med, BASE + timedelta(days=1), BASE)
        ids = {d.id for d, p in find_early_takes(db, only_demo=True, include_past=True)}
        assert demo_bad in ids and real_bad not in ids
        assert real_bad in {d.id for d, p in find_early_takes(db, include_past=True)}
    finally:
        db.close()


def run_cli(*args, db_url):
    env = dict(os.environ, SMARTPOLI_DATABASE_URL=db_url, SMARTPOLI_AI_GAPS="0")
    return subprocess.run([sys.executable, os.path.join("tools", "repair_early_takes.py"), *args], capture_output=True, text=True,
                          cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), env=env)


def test_cli_is_a_dry_run_by_default_and_apply_needs_the_matching_host(tmp_path):
    url = f"sqlite:///{tmp_path / 'r.db'}"
    env_run = lambda *a: run_cli(*a, db_url=url)  # noqa: E731
    # prepare a database with one bogus take
    subprocess.run([sys.executable, "-c",
                    "import db, datetime as d\n"
                    "db.init_db(); s = db.SessionLocal()\n"
                    "u = db.User(email='c@smartpoli.demo', password_hash='x', role='patient', name='C'); s.add(u); s.commit()\n"
                    "p = db.Patient(user_id=u.id, name='C'); s.add(p); s.commit()\n"
                    "r = db.Prescription(patient_id=p.id, status='confirmed', source='manual'); s.add(r); s.commit()\n"
                    "m = db.Medicine(prescription_id=r.id, raw_text='x', name='X', status='verified', confidence=1.0); s.add(m); s.commit()\n"
                    "s.add(db.Dose(medicine_id=m.id, scheduled_at=d.datetime.utcnow()+d.timedelta(days=2), state='taken', acted_at=d.datetime.utcnow())); s.commit()\n"],
                   cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), env=dict(os.environ, SMARTPOLI_DATABASE_URL=url),
                   check=True)
    dry = env_run()
    assert dry.returncode == 0 and "Dry run: nothing changed" in dry.stdout and "1 dose(s)" in dry.stdout
    refuse = env_run("--apply")
    assert refuse.returncode == 2 and "Refusing to apply" in refuse.stdout
    wrong = env_run("--apply", "--confirm-host", "some-other-host")
    assert wrong.returncode == 2
    ok = env_run("--apply", "--confirm-host", "r.db")
    assert ok.returncode == 0 and "Reverted 1 dose(s)" in ok.stdout
    again = env_run()
    assert "0 dose(s)" in again.stdout


def test_old_doses_are_left_alone_unless_asked_for():
    from clock import patient_now
    init_db()
    db = SessionLocal()
    try:
        pid, med = make(db, "repair-past@smartpoli.demo")
        now = patient_now(db, pid)
        future_bad = dose(db, med, now + timedelta(days=1), now - timedelta(hours=1))
        past_bad = dose(db, med, now - timedelta(days=5), now - timedelta(days=5, hours=6))     # marked 6 h before its time, long ago
        default_ids = {d.id for d, p in find_early_takes(db, only_demo=True)}
        assert future_bad in default_ids and past_bad not in default_ids
        assert past_bad in {d.id for d, p in find_early_takes(db, only_demo=True, include_past=True)}
    finally:
        db.close()
