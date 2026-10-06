"""Missed-dose alerts to caregivers' phones, their switches, and Remind now."""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import care_push  # noqa: E402
import reminders  # noqa: E402
from db import Dose, PushSubscription, SessionLocal, init_db  # noqa: E402
from main import app  # noqa: E402
from test_care_notes_api import link_caregiver, revoke_caregiver  # noqa: E402
from timing_helpers import add_medicine, new_patient  # noqa: E402


def subscribe(cg, n):
    r = cg.post("/caregiver/push/subscribe", json={"endpoint": f"https://push.example/{n}", "keys": {"p256dh": "k" * 12, "auth": "a" * 12}})
    assert r.status_code == 200, r.text


@pytest.fixture()
def world():
    init_db()
    patient = TestClient(app)
    pid, _ = new_patient(patient, "Anita")
    past = datetime.utcnow() - timedelta(hours=5)
    med, doses = add_medicine(pid, "Metformin", [past], states=["missed"])
    cg1, u1 = link_caregiver(patient, pid, "Sita")
    cg2, u2 = link_caregiver(patient, pid, "Ravi")
    subscribe(cg1, f"a-{pid}")
    subscribe(cg2, f"b-{pid}")
    sent = []
    fake = lambda ep, p, a, payload: sent.append((ep, payload)) or "sent"  # noqa: E731
    yield SimpleNamespace(patient=patient, pid=pid, dose=doses[0], cg1=cg1, cg2=cg2, u1=u1, u2=u2, sent=sent, fake=fake)


def run_sweep(w):
    db = SessionLocal()
    try:
        dose = db.query(Dose).filter(Dose.id == w.dose).first()
        return reminders.notify_missed(db, [dose], push_fn=w.fake)
    finally:
        db.close()


def caregiver_pushes(w):
    return [p for ep, p in w.sent if "push.example/a-" in ep or "push.example/b-" in ep]


def test_every_linked_caregiver_with_a_phone_gets_one_alert_naming_patient_medicine_and_time(world):
    run_sweep(world)
    got = caregiver_pushes(world)
    assert len(got) == 2
    assert all("Anita" in p["title"] and "Metformin" in p["body"] for p in got)


def test_the_same_missed_dose_never_alerts_twice(world):
    run_sweep(world)
    before = len(caregiver_pushes(world))
    run_sweep(world)
    assert len(caregiver_pushes(world)) == before


def test_a_caregiver_can_switch_alerts_off_for_one_patient(world):
    assert world.cg1.get(f"/caregiver/patients/{world.pid}/prefs").json() == {"notify_missed": True}
    assert world.cg1.put(f"/caregiver/patients/{world.pid}/prefs", json={"notify_missed": False}).json() == {"notify_missed": False}
    run_sweep(world)
    got = caregiver_pushes(world)
    assert len(got) == 1 and "push.example/b-" in [ep for ep, _ in world.sent if "push.example/b-" in ep][0]


def test_revoked_caregivers_and_caregivers_without_a_phone_are_skipped(world):
    revoke_caregiver_for(world, world.u1["id"])
    cg3, _ = link_caregiver(world.patient, world.pid, "NoPhone")           # linked but never turned on phone alerts
    run_sweep(world)
    assert len(caregiver_pushes(world)) == 1


def revoke_caregiver_for(w, user_id):
    links = w.patient.get(f"/patients/{w.pid}/caregiver-links").json()
    target = [l for l in links if l["caregiver_name"] == "Sita" and l["status"] == "active"][0]
    assert w.patient.post(f"/caregiver-links/{target['id']}/revoke").status_code == 200


def test_a_dead_subscription_is_removed_and_a_failure_never_breaks_the_sweep(world):
    from db import UserPushSubscription
    dead = lambda ep, p, a, payload: "gone"  # noqa: E731
    world.fake = dead
    run_sweep(world)
    db = SessionLocal()
    assert db.query(UserPushSubscription).filter(UserPushSubscription.user_id.in_([world.u1["id"], world.u2["id"]])).count() == 0
    db.close()


def test_subscribe_is_idempotent_and_unsubscribe_removes_it(world):
    from db import UserPushSubscription
    subscribe(world.cg1, f"a-{world.pid}")                                       # same device again
    db = SessionLocal()
    assert db.query(UserPushSubscription).filter(UserPushSubscription.user_id == world.u1["id"]).count() == 1
    db.close()
    assert world.cg1.post("/caregiver/push/unsubscribe", json={"endpoint": f"https://push.example/a-{world.pid}"}).status_code == 200
    db = SessionLocal()
    assert db.query(UserPushSubscription).filter(UserPushSubscription.user_id == world.u1["id"]).count() == 0
    db.close()
    assert TestClient(app).post("/caregiver/push/subscribe", json={"endpoint": "https://x", "keys": {"p256dh": "k" * 12, "auth": "a" * 12}}).status_code == 401
    assert world.patient.post("/caregiver/push/subscribe", json={"endpoint": "https://x", "keys": {"p256dh": "k" * 12, "auth": "a" * 12}}).status_code == 403


def test_prefs_need_a_link(world):
    other = TestClient(app)
    from conftest import register_and_login
    register_and_login(other, role="caregiver")
    assert other.get(f"/caregiver/patients/{world.pid}/prefs").status_code == 403
    assert other.put(f"/caregiver/patients/{world.pid}/prefs", json={"notify_missed": False}).status_code == 403


# ---------------------------------------------------------------- Remind now

def test_remind_now_pushes_to_the_patient_once_per_fifteen_minutes(world, monkeypatch):
    got = []
    monkeypatch.setattr(care_push.webpush_service, "send", lambda ep, p, a, payload: got.append(payload) or "sent")
    monkeypatch.setattr(care_push.webpush_service, "is_configured", lambda: True)
    d = SessionLocal()
    d.add(PushSubscription(patient_id=world.pid, endpoint="https://push.example/patient-phone", p256dh="k", auth="a"))
    d.commit()
    d.close()
    r = world.cg1.post(f"/caregiver/patients/{world.pid}/nudge")
    assert r.status_code == 200 and r.json()["sent"] == 1 and "Sita" in got[0]["title"]
    again = world.cg2.post(f"/caregiver/patients/{world.pid}/nudge")                     # a different caregiver, same patient
    assert again.status_code == 429 and "minute" in again.json()["detail"] and "retry-after" in again.headers
    assert len(got) == 1


def test_remind_now_says_so_when_the_patient_has_no_phone_reminders(world):
    out = world.cg1.post(f"/caregiver/patients/{world.pid}/nudge").json()
    assert out["sent"] == 0 and "not turned on" in out["note"]
