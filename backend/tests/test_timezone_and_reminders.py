"""
Patient-local time, missed-dose sweeping and the multi-step reminder engine.

All clocks are injected (utc_now / explicit datetimes); nothing sleeps and
nothing talks to Twilio or a push service - senders are recording stubs.
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import clock  # noqa: E402
import reminders  # noqa: E402
import scheduler  # noqa: E402
from db import (  # noqa: E402
    AuditLog, Dose, Medicine, PatientSettings, PushSubscription, ReminderLog, SessionLocal, WhatsAppSession,
)
from main import app  # noqa: E402
from timing_helpers import add_medicine, new_patient  # noqa: E402

DAY = datetime(2026, 10, 5)
IST = "Asia/Kolkata"


@pytest.fixture(autouse=True)
def _clean_timing_tables():
    """The sweeps are global by design, so start every test from a clean slate of
    doses / reminder logs / subscriptions (other modules' leftovers would
    otherwise be swept too)."""
    from db import init_db
    init_db()
    db = SessionLocal()
    try:
        for model in (ReminderLog, Dose, PushSubscription, WhatsAppSession):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()
    yield


def local(h, m=0):
    return DAY.replace(hour=h, minute=m)


def ist_to_utc(dt):
    return dt - timedelta(hours=5, minutes=30)


def set_tz(client, pid, tz):
    r = client.put(f"/patients/{pid}/settings", json={"timezone": tz})
    assert r.status_code == 200, r.text


class Recorder:
    """Stand-ins for the Web Push and WhatsApp senders."""
    def __init__(self, push_status="sent", wa_ok=True):
        self.push, self.wa = [], []
        self.push_status, self.wa_ok = push_status, wa_ok

    def push_fn(self, endpoint, p256dh, auth, payload):
        self.push.append(payload)
        return self.push_status

    def wa_fn(self, phone, body):
        self.wa.append(body)
        return self.wa_ok

    def kinds(self):
        return [p["kind"] for p in self.push]


def subscribe(client, pid, endpoint="https://push.example.test/abc123"):
    r = client.post(f"/patients/{pid}/push/subscribe",
                    json={"endpoint": endpoint, "keys": {"p256dh": "k" * 20, "auth": "a" * 12}})
    assert r.status_code == 200, r.text


def opt_in_whatsapp(pid, phone="+15550009999"):
    db = SessionLocal()
    try:
        db.add(WhatsAppSession(phone=phone, patient_id=pid, state="ready", notifications_opt_in=True))
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------- the clock

def test_local_now_applies_the_patients_utc_offset():
    utc = datetime(2026, 10, 5, 2, 30)
    assert clock.local_now(IST, utc) == datetime(2026, 10, 5, 8, 0)
    assert clock.local_now("UTC", utc) == utc


def test_daylight_saving_transition_is_followed():
    # US clocks jump 02:00 -> 03:00 on 2026-03-08 (07:00 UTC).
    assert clock.local_now("America/New_York", datetime(2026, 3, 8, 6, 59)) == datetime(2026, 3, 8, 1, 59)
    assert clock.local_now("America/New_York", datetime(2026, 3, 8, 7, 1)) == datetime(2026, 3, 8, 3, 1)


def test_date_boundary_is_judged_in_the_patients_zone():
    # 20:00 UTC on the 4th is already 01:30 on the 5th in India.
    assert clock.local_now(IST, datetime(2026, 10, 4, 20, 0)).date() == datetime(2026, 10, 5).date()


def test_unknown_timezone_falls_back_to_the_default_instead_of_crashing():
    assert clock.resolve_timezone("Not/AZone") == clock.resolve_timezone(None)
    assert not clock.is_valid_timezone("Not/AZone") and clock.is_valid_timezone(IST)
    assert clock.local_now("Not/AZone", datetime(2026, 10, 5, 2, 30)) == datetime(2026, 10, 5, 2, 30)  # tests pin UTC


# ---------------------------------------------------------------- settings API

def test_settings_default_update_validate_and_audit():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        assert client.get(f"/patients/{pid}/settings").json() == {
            "timezone": "UTC", "timezone_is_set": False, "reminder_lead_minutes": 30}
        r = client.put(f"/patients/{pid}/settings", json={"timezone": IST, "reminder_lead_minutes": 15})
        assert r.json()["timezone"] == IST and r.json()["timezone_is_set"] is True and r.json()["reminder_lead_minutes"] == 15
        assert client.put(f"/patients/{pid}/settings", json={"timezone": "Mars/Olympus"}).status_code == 422
        assert client.put(f"/patients/{pid}/settings", json={"reminder_lead_minutes": 999}).status_code == 422
        db = SessionLocal()
        try:
            assert db.query(AuditLog).filter_by(patient_id=pid, action="settings_updated").count() == 1
        finally:
            db.close()


def test_only_the_patient_can_change_their_settings():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        other = TestClient(app)
        from conftest import register_and_login
        register_and_login(other)
        assert other.put(f"/patients/{pid}/settings", json={"timezone": IST}).status_code == 403
        assert other.get(f"/patients/{pid}/settings").status_code == 403


# ---------------------------------------------------------------- missed sweep on the patient's clock

def test_missed_is_judged_on_the_patients_clock_not_the_servers():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        _, (dose_id,) = add_medicine(pid, "Telmisartan", [local(8)])
        db = SessionLocal()
        try:
            # 09:30 IST: only 1.5h late -> not missed
            assert scheduler.sweep_missed_doses(db, utc_now=ist_to_utc(local(9, 30))) == []
            # 10:01 IST: >2h late -> missed, once
            done = scheduler.sweep_missed_doses(db, utc_now=ist_to_utc(local(10, 1)))
            assert [d.id for d in done] == [dose_id] and done[0].state == "missed"
            assert done[0].acted_at == local(10, 1)           # stamped in patient-local time
            # idempotent: a second sweep changes nothing
            assert scheduler.sweep_missed_doses(db, utc_now=ist_to_utc(local(10, 5))) == []
            assert db.query(AuditLog).filter_by(patient_id=pid, action="dose_auto_missed").count() == 1
        finally:
            db.close()


def test_the_same_server_instant_is_missed_for_one_patient_and_not_another():
    with TestClient(app) as client:
        pid_ist, _ = new_patient(client, "In India")
        set_tz(client, pid_ist, IST)
        _, (d1,) = add_medicine(pid_ist, "Amlodipine", [local(8)])
        pid_utc, _ = new_patient(client, "In UTC")
        _, (d2,) = add_medicine(pid_utc, "Amlodipine", [local(8)])
        db = SessionLocal()
        try:
            missed = scheduler.sweep_missed_doses(db, utc_now=ist_to_utc(local(10, 1)))  # 04:31 UTC
            ids = {d.id for d in missed}
            assert d1 in ids and d2 not in ids          # for the UTC patient it is only 04:31, before 08:00
        finally:
            db.close()


def test_a_snoozed_dose_that_is_never_acted_on_eventually_becomes_missed():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _, (dose_id,) = add_medicine(pid, "Metformin", [local(8)], states=["snoozed"])
        db = SessionLocal()
        try:
            done = scheduler.sweep_missed_doses(db, now=local(11))
            assert [d.id for d in done] == [dose_id]
        finally:
            db.close()


def test_legacy_sweep_with_explicit_now_still_returns_a_count():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        add_medicine(pid, "Metformin", [local(8)])
        db = SessionLocal()
        try:
            assert scheduler.sweep_missed(db, now=local(11)) == 1
        finally:
            db.close()


# ---------------------------------------------------------------- dose generation

def _medicine(times, days=2):
    import json
    return Medicine(id=1, prescription_id=1, raw_text="x", name="X", status="verified", is_prn=False,
                    schedule_code="BD", times=json.dumps(times), duration_days=days)


def test_slots_already_past_today_are_not_created_when_asked():
    m = _medicine(["08:00", "20:00"], days=2)
    all_slots = scheduler.generate_doses(m, start_at=local(15))
    assert len(all_slots) == 4                                   # legacy behaviour unchanged
    future = scheduler.generate_doses(m, start_at=local(15), skip_past=True)
    assert [d.scheduled_at for d in future] == [local(20), local(8) + timedelta(days=1), local(20) + timedelta(days=1)]


def test_confirming_mid_day_does_not_create_an_instantly_missed_dose(monkeypatch):
    import prescription_service
    monkeypatch.setattr(prescription_service, "patient_now", lambda db, pid, utc_now=None: local(15))
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        pres = client.post("/prescriptions", json={"patient_id": pid, "lines": ["Tab Dolo 650mg 1-0-1 PC x2d"]}).json()
        assert client.post(f"/prescriptions/{pres['prescription_id']}/confirm").status_code == 200
        db = SessionLocal()
        try:
            doses = db.query(Dose).join(Medicine).join(Medicine.prescription.property.mapper.class_).filter(
                Medicine.prescription_id == pres["prescription_id"]).all()
            assert doses and all(d.scheduled_at >= local(15) for d in doses)
        finally:
            db.close()


# ---------------------------------------------------------------- the reminder lifecycle

def test_every_dose_gets_two_heads_ups_a_due_and_a_follow_up_each_exactly_once():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        subscribe(client, pid)
        opt_in_whatsapp(pid)
        add_medicine(pid, "Telmisartan", [local(8)], food="after")
        rec = Recorder()
        db = SessionLocal()
        try:
            run = lambda h, m: reminders.run_reminder_sweep(db, ist_to_utc(local(h, m)), rec.push_fn, rec.wa_fn)  # noqa: E731
            assert run(7, 20) == 0                                # 40 min away: too early
            assert run(7, 31) == 2                                # early heads-up (30 min) on push + WhatsApp
            assert run(7, 35) == 0 and run(7, 45) == 0            # the 1-minute sweep never repeats it
            assert run(7, 51) == 2                                # last heads-up (10 min)
            assert run(7, 55) == 0
            assert run(8, 0) == 2                                 # due
            assert run(8, 5) == 0
            assert run(8, 16) == 2                                # still not taken -> follow-up
            assert run(8, 30) == 0 and run(9, 0) == 0
            assert rec.kinds() == ["lead", "soon", "due", "followup"]
            assert len(rec.wa) == 4
            assert "after food" in rec.push[0]["body"].lower() and "(in 29 min)" in rec.push[0]["body"]
            assert len(rec.push) >= 2                             # the "at least two notifications" requirement
        finally:
            db.close()


def test_a_short_lead_gives_one_heads_up_a_long_lead_gives_two():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        subscribe(client, pid)
        add_medicine(pid, "Telmisartan", [local(8)])
        client.put(f"/patients/{pid}/settings", json={"reminder_lead_minutes": 10})
        rec = Recorder()
        db = SessionLocal()
        try:
            for h, m in ((7, 40), (7, 51), (7, 55)):
                reminders.run_reminder_sweep(db, ist_to_utc(local(h, m)), rec.push_fn, rec.wa_fn)
            assert rec.kinds() == ["lead"]                        # 10-min lead: a single heads-up
            # a longer lead applies to a dose that has not had its early reminder yet (already-sent ones are not repeated)
            client.put(f"/patients/{pid}/settings", json={"reminder_lead_minutes": 90})
            add_medicine(pid, "Amlodipine", [local(9)])
            reminders.run_reminder_sweep(db, ist_to_utc(local(7, 45)), rec.push_fn, rec.wa_fn)
            assert rec.kinds()[-1] == "lead" and "Amlodipine" in rec.push[-1]["body"] and "(in 75 min)" in rec.push[-1]["body"]
            assert rec.kinds() == ["lead", "lead"]                   # nothing was re-sent for the Telmisartan dose
            add_medicine(pid, "Metformin", [local(10)])
            reminders.run_reminder_sweep(db, ist_to_utc(local(8, 30)), rec.push_fn, rec.wa_fn)
            assert "(in 1 h 30 min)" in rec.push[-1]["body"]
        finally:
            db.close()


def test_nothing_more_is_sent_once_the_dose_is_taken():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        subscribe(client, pid)
        _, (dose_id,) = add_medicine(pid, "Telmisartan", [local(8)])
        rec = Recorder()
        db = SessionLocal()
        try:
            reminders.run_reminder_sweep(db, ist_to_utc(local(7, 51)), rec.push_fn, rec.wa_fn)   # 9 min away -> "soon"
            db.query(Dose).filter_by(id=dose_id).update({"state": "taken"})
            db.commit()
            assert reminders.run_reminder_sweep(db, ist_to_utc(local(8, 0)), rec.push_fn, rec.wa_fn) == 0
            assert reminders.run_reminder_sweep(db, ist_to_utc(local(8, 16)), rec.push_fn, rec.wa_fn) == 0
            assert rec.kinds() == ["soon"]
        finally:
            db.close()


def test_reminders_follow_the_patients_timezone():
    with TestClient(app) as client:
        pid, _ = new_patient(client)                 # stays on the UTC default
        subscribe(client, pid)
        add_medicine(pid, "Telmisartan", [local(8)])
        rec = Recorder()
        db = SessionLocal()
        try:
            # 08:00 *Indian* time is 02:30 UTC; for a UTC patient nothing is due yet.
            assert reminders.run_reminder_sweep(db, datetime(2026, 10, 5, 2, 30), rec.push_fn, rec.wa_fn) == 0
            assert reminders.run_reminder_sweep(db, datetime(2026, 10, 5, 8, 0), rec.push_fn, rec.wa_fn) == 1
        finally:
            db.close()


def test_a_reminder_the_server_was_too_late_for_skips_the_heads_up_but_still_sends_due():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        subscribe(client, pid)
        add_medicine(pid, "Telmisartan", [local(8)])
        rec = Recorder()
        db = SessionLocal()
        try:
            reminders.run_reminder_sweep(db, ist_to_utc(local(8, 5)), rec.push_fn, rec.wa_fn)
            assert rec.kinds() == ["due"]
        finally:
            db.close()


def test_heads_up_can_be_switched_off_or_changed_per_patient():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        subscribe(client, pid)
        add_medicine(pid, "Telmisartan", [local(8)])
        client.put(f"/patients/{pid}/settings", json={"reminder_lead_minutes": 0})
        rec = Recorder()
        db = SessionLocal()
        try:
            reminders.run_reminder_sweep(db, ist_to_utc(local(7, 55)), rec.push_fn, rec.wa_fn)
            assert rec.kinds() == []
            client.put(f"/patients/{pid}/settings", json={"reminder_lead_minutes": 30})
            reminders.run_reminder_sweep(db, ist_to_utc(local(7, 40)), rec.push_fn, rec.wa_fn)
            assert rec.kinds() == ["lead"]
        finally:
            db.close()


def test_snoozing_re_arms_the_due_and_follow_up_reminders_for_the_new_time(monkeypatch):
    import main
    monkeypatch.setattr(main, "patient_now", lambda db, patient_id, utc_now=None: local(8, 0))
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        subscribe(client, pid)
        _, (dose_id,) = add_medicine(pid, "Telmisartan", [local(8)])
        rec = Recorder()
        db = SessionLocal()
        try:
            reminders.run_reminder_sweep(db, ist_to_utc(local(8, 0)), rec.push_fn, rec.wa_fn)
            assert rec.kinds() == ["due"]
            assert client.post(f"/doses/{dose_id}/snooze").status_code == 200   # now 08:15
            db.expire_all()
            assert db.query(ReminderLog).filter_by(dose_id=dose_id, kind="due").count() == 0
            reminders.run_reminder_sweep(db, ist_to_utc(local(8, 15)), rec.push_fn, rec.wa_fn)
            assert rec.kinds() == ["due", "due"]
        finally:
            db.close()


def test_a_failed_send_is_retried_and_an_expired_subscription_is_removed():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        subscribe(client, pid)
        add_medicine(pid, "Telmisartan", [local(8)])
        db = SessionLocal()
        try:
            flaky = Recorder(push_status="error")
            assert reminders.run_reminder_sweep(db, ist_to_utc(local(8, 0)), flaky.push_fn, flaky.wa_fn) == 0
            assert db.query(ReminderLog).count() == db.query(ReminderLog).filter(ReminderLog.kind != "due").count()
            ok = Recorder()
            assert reminders.run_reminder_sweep(db, ist_to_utc(local(8, 1)), ok.push_fn, ok.wa_fn) == 1   # retried

            gone = Recorder(push_status="gone")
            with TestClient(app) as c2:
                pid2, _ = new_patient(c2)
                set_tz(c2, pid2, IST)
                subscribe(c2, pid2, "https://push.example.test/dead")
                add_medicine(pid2, "Telmisartan", [local(8)])
                reminders.run_reminder_sweep(db, ist_to_utc(local(8, 0)), gone.push_fn, gone.wa_fn)
                assert db.query(PushSubscription).filter_by(patient_id=pid2).count() == 0
        finally:
            db.close()


def test_no_channel_means_no_send_and_no_log_rows():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        _, (dose_id,) = add_medicine(pid, "Telmisartan", [local(8)])
        rec = Recorder()
        db = SessionLocal()
        try:
            assert reminders.run_reminder_sweep(db, ist_to_utc(local(8, 0)), rec.push_fn, rec.wa_fn) == 0
            assert db.query(ReminderLog).filter_by(dose_id=dose_id).count() == 0
        finally:
            db.close()


def test_claiming_the_same_reminder_twice_is_refused_even_across_workers():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _, (dose_id,) = add_medicine(pid, "Telmisartan", [local(8)])
        db1, db2 = SessionLocal(), SessionLocal()
        try:
            assert reminders._claim(db1, dose_id, "due", "push") is True
            assert reminders._claim(db2, dose_id, "due", "push") is False
            assert reminders._claim(db2, dose_id, "due", "whatsapp") is True
        finally:
            db1.close()
            db2.close()


# ---------------------------------------------------------------- the one-off "missed" notification

def test_a_missed_dose_sends_one_message_with_never_double_and_the_next_dose_time():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        subscribe(client, pid)
        add_medicine(pid, "Metformin", [local(8), local(20)])
        rec = Recorder()
        db = SessionLocal()
        try:
            missed = scheduler.sweep_missed_doses(db, utc_now=ist_to_utc(local(10, 30)))
            assert len(missed) == 1
            assert reminders.notify_missed(db, missed, ist_to_utc(local(10, 30)), rec.push_fn, rec.wa_fn) == 1
            body = rec.push[0]["body"]
            assert rec.kinds() == ["missed"]
            assert "Don't take a double dose" in body and "20:00" in body
            # sending it again (e.g. a retried job) does not duplicate it
            assert reminders.notify_missed(db, missed, ist_to_utc(local(10, 31)), rec.push_fn, rec.wa_fn) == 0
            assert db.query(AuditLog).filter_by(patient_id=pid, action="missed_dose_guidance").count() >= 1
        finally:
            db.close()


def test_missed_notice_for_a_high_risk_medicine_never_offers_a_catch_up_time():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        subscribe(client, pid)
        add_medicine(pid, "Warfarin", [local(8), local(20)])
        rec = Recorder()
        db = SessionLocal()
        try:
            missed = scheduler.sweep_missed_doses(db, utc_now=ist_to_utc(local(10, 30)))
            reminders.notify_missed(db, missed, ist_to_utc(local(10, 30)), rec.push_fn, rec.wa_fn)
            body = rec.push[0]["body"]
            assert "doctor or pharmacist" in body and "wait until" not in body.lower()
        finally:
            db.close()


def test_a_guidance_failure_still_sends_the_safe_default_notice(monkeypatch):
    import safety_service
    monkeypatch.setattr(safety_service, "missed_guidance_for_dose", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        subscribe(client, pid)
        add_medicine(pid, "Metformin", [local(8)])
        rec = Recorder()
        db = SessionLocal()
        try:
            missed = scheduler.sweep_missed_doses(db, utc_now=ist_to_utc(local(10, 30)))
            assert reminders.notify_missed(db, missed, ist_to_utc(local(10, 30)), rec.push_fn, rec.wa_fn) == 1
            assert "double dose" in rec.push[0]["body"]
        finally:
            db.close()


# ---------------------------------------------------------------- other major notices

def test_course_end_notice_text_and_dedupe():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        subscribe(client, pid)
        tomorrow = timedelta(days=1)
        # today's doses are already taken; the only pending one is tomorrow's last dose
        add_medicine(pid, "Amoxicillin", [local(8), local(8) + tomorrow], states=["taken", "pending"], duration_days=2)
        add_medicine(pid, "Telmisartan", [local(8) + tomorrow], duration_days=None)      # ongoing -> never announced
        add_medicine(pid, "Dolo", [local(8) + tomorrow], is_prn=True, duration_days=3)    # as-needed -> never announced
        rec = Recorder()
        db = SessionLocal()
        try:
            assert reminders.notify_course_ending(db, ist_to_utc(local(8, 30)), rec.push_fn, rec.wa_fn) == 0    # too early in the day
            assert reminders.notify_course_ending(db, ist_to_utc(local(10, 0)), rec.push_fn, rec.wa_fn) == 1
            assert reminders.notify_course_ending(db, ist_to_utc(local(11, 0)), rec.push_fn, rec.wa_fn) == 0    # once only
            assert rec.push[0]["title"] == "Last dose of Amoxicillin tomorrow"
            assert "ask your doctor" in rec.push[0]["body"] and rec.kinds() == ["course_end"]
            # a later pending dose means it is not the last one
            add_medicine(pid, "Cefixime", [local(8) + tomorrow, local(8) + 2 * tomorrow], duration_days=3)
            assert reminders.notify_course_ending(db, ist_to_utc(local(12, 0)), rec.push_fn, rec.wa_fn) == 0
        finally:
            db.close()


def test_conflict_notice_is_sent_once_per_pair_and_keeps_clinical_detail_off_the_lock_screen():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        set_tz(client, pid, IST)
        subscribe(client, pid)
        add_medicine(pid, "Levothyroxine", [local(8)])
        add_medicine(pid, "Calcium carbonate", [local(8), local(20)])
        rec = Recorder()
        db = SessionLocal()
        try:
            assert reminders.notify_new_conflicts(db, pid, ist_to_utc(local(6)), rec.push_fn, rec.wa_fn) == 1
            body = rec.push[0]["body"]
            assert "Levothyroxine" in body and "Calcium carbonate" in body and "Open SmartPoli" in body
            assert "4 hours" not in body and "FDA" not in body             # detail lives in the app, with its source
            assert reminders.notify_new_conflicts(db, pid, ist_to_utc(local(6, 5)), rec.push_fn, rec.wa_fn) == 0
        finally:
            db.close()


def test_a_notification_problem_never_fails_a_prescription_confirmation(monkeypatch):
    import reminders as r
    monkeypatch.setattr(r, "notify_new_conflicts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("push down")))
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        pres = client.post("/prescriptions", json={"patient_id": pid, "lines": ["Tab Dolo 650mg 1-0-1 PC x2d"]}).json()
        assert client.post(f"/prescriptions/{pres['prescription_id']}/confirm").status_code == 200
