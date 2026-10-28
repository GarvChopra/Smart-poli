"""Notification wording (clear, different each day, never one notification per medicine) and the Remind now button that
sits on the missed-dose notification itself."""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import auth  # noqa: E402
import care_push  # noqa: E402
import notify_copy  # noqa: E402
import push_actions  # noqa: E402
import reminders  # noqa: E402
from db import Dose, PushSubscription, SessionLocal, init_db  # noqa: E402
from main import app  # noqa: E402
from test_care_notes_api import link_caregiver  # noqa: E402
from timing_helpers import add_medicine, new_patient  # noqa: E402

AT = datetime(2026, 10, 28, 8, 0)


def item(name="Metformin", dose="500mg", food="any", at=AT):
    return {"name": name, "dose": dose, "food": food, "at": at}


# ---------------------------------------------------------------- the words

@pytest.mark.parametrize("kind", ["lead", "soon", "due", "followup"])
def test_every_kind_has_a_clear_short_message_for_one_medicine_and_for_several(kind):
    for items in ([item()], [item(), item("Telmisartan", "40mg")]):
        m = notify_copy.self_reminder(kind, items, 20, seed=3)
        assert m["title"] and m["body"] and len(m["title"]) <= 60 and len(m["body"]) <= 170, m
        assert "Metformin" in (m["title"] + m["body"])
    several = notify_copy.self_reminder(kind, [item(), item("Telmisartan", "40mg")], 20, seed=3)
    assert "Telmisartan" in several["body"] or "2" in several["title"]


def test_the_wording_changes_from_day_to_day_but_not_within_a_day():
    days = [notify_copy.self_reminder("due", [item()], 0, seed=d) for d in range(40000, 40012)]
    assert len({(m["title"], m["body"]) for m in days}) >= 6                      # a different message most days
    same_day = [notify_copy.self_reminder("due", [item()], 0, seed=40007) for _ in range(5)]
    assert len({(m["title"], m["body"]) for m in same_day}) == 1                  # but a day's reminders agree


def test_the_food_hint_is_said_only_when_there_is_one_and_never_mixed():
    assert "after food" in notify_copy.self_reminder("due", [item(food="after")], 0, 1)["body"]
    assert "before food" in notify_copy.self_reminder("due", [item(food="before")], 0, 1)["body"]
    assert "food" not in notify_copy.self_reminder("due", [item(food="any")], 0, 1)["body"].replace("Steady", "")
    mixed = notify_copy.self_reminder("due", [item(food="after"), item("B", "", "before")], 0, 1)["body"]
    assert "food" not in mixed


def test_no_message_gives_advice_makes_a_claim_or_uses_a_gendered_word():
    banned = (" he ", " she ", " his ", " her ", "should", "must", "cure", "treat", "dangerous", "overdose", "double")
    for line in notify_copy.DAILY_LINES:
        assert not any(b in f" {line.lower()} " for b in banned), line
    for kind in ("lead", "soon", "due", "followup"):
        for seed in range(12):
            m = notify_copy.self_reminder(kind, [item()], 12, seed)
            assert not any(b in f" {(m['title'] + ' ' + m['body']).lower()} " for b in banned), m
    for m in (notify_copy.caregiver_missed("Mum", [item()]), notify_copy.nudge_to_patient("Gita", [item()])):
        assert not any(b in f" {(m['title'] + ' ' + m['body']).lower()} " for b in banned), m


def test_caregiver_text_names_the_person_the_time_and_the_one_thing_to_do():
    one = notify_copy.caregiver_missed("Mum", [item(at=datetime(2026, 10, 28, 9, 42))])
    assert one["title"] == "Mum missed the 9:42 AM dose"
    assert "Metformin 500mg" in one["body"] and "Remind now" in one["body"]
    many = notify_copy.caregiver_missed("Dad", [item(at=AT), item("B", "", at=AT), item("C", "", at=AT), item("D", "", at=AT)])
    assert many["title"] == "Dad missed 4 doses" and "+1 more" in many["body"]


# ---------------------------------------------------------------- one notification per moment

@pytest.fixture()
def phone():
    init_db()
    c = TestClient(app)
    pid, _ = new_patient(c, "Anita")
    c.post(f"/patients/{pid}/push/subscribe", json={"endpoint": f"https://push.example/anita-{pid}", "keys": {"p256dh": "k" * 20, "auth": "a" * 12}})
    return c, pid


def test_two_medicines_due_together_are_one_notification_and_never_repeat(phone):
    c, pid = phone
    now = datetime.utcnow().replace(second=0, microsecond=0)
    _, d1 = add_medicine(pid, "Metformin", [now - timedelta(minutes=1)])
    _, d2 = add_medicine(pid, "Telmisartan", [now - timedelta(minutes=1)])
    mine = set(d1 + d2)
    sent = []
    fake = lambda ep, p, a, payload: sent.append(payload) or "sent"  # noqa: E731
    ours = lambda: [p for p in sent if mine & set(p.get("dose_ids", []))]  # noqa: E731    (other tests leave doses due around now too)
    db = SessionLocal()
    try:
        reminders.run_reminder_sweep(db, now, fake, lambda *a: True)
        due = [p for p in ours() if p["kind"] == "due"]
        assert len(due) == 1 and set(due[0]["dose_ids"]) == mine
        assert "Metformin" in due[0]["body"] and "Telmisartan" in due[0]["body"]
        before = len(ours())
        reminders.run_reminder_sweep(db, now, fake, lambda *a: True)                       # each dose still told about once
        assert len(ours()) == before
    finally:
        db.close()


def test_a_failed_send_releases_every_dose_so_the_next_minute_retries_them_together(phone):
    c, pid = phone
    now = datetime.utcnow().replace(second=0, microsecond=0)
    _, d1 = add_medicine(pid, "Metformin", [now - timedelta(minutes=1)])
    _, d2 = add_medicine(pid, "Telmisartan", [now - timedelta(minutes=1)])
    mine = set(d1 + d2)
    db = SessionLocal()
    try:
        reminders.run_reminder_sweep(db, now, lambda *a: "error", lambda *a: True)         # nothing is delivered ...
        ok = []
        reminders.run_reminder_sweep(db, now, lambda ep, p, a, payload: ok.append(payload) or "sent", lambda *a: True)
        retried = [p for p in ok if mine & set(p.get("dose_ids", [])) and p["kind"] == "due"]
        assert len(retried) == 1 and set(retried[0]["dose_ids"]) == mine                   # ... so both go out together next time
    finally:
        db.close()


def test_doses_at_different_times_stay_separate_notifications(phone):
    c, pid = phone
    now = datetime.utcnow().replace(second=0, microsecond=0)
    _, d1 = add_medicine(pid, "Metformin", [now - timedelta(minutes=1)])
    _, d2 = add_medicine(pid, "Telmisartan", [now - timedelta(minutes=2)])
    mine = set(d1 + d2)
    sent = []
    db = SessionLocal()
    try:
        reminders.run_reminder_sweep(db, now, lambda ep, p, a, payload: sent.append(payload) or "sent", lambda *a: True)
    finally:
        db.close()
    assert len([p for p in sent if p["kind"] == "due" and mine & set(p.get("dose_ids", []))]) == 2


# ---------------------------------------------------------------- the missed-dose alert with Remind now

@pytest.fixture()
def family():
    init_db()
    mum = TestClient(app)
    pid, _ = new_patient(mum, "Sunita")
    past = datetime.utcnow() - timedelta(hours=5)
    med1, d1 = add_medicine(pid, "Metformin", [past], states=["missed"])
    med2, d2 = add_medicine(pid, "Telmisartan", [past], states=["missed"])
    gita, gita_user = link_caregiver(mum, pid, "Gita")
    gita.patch(f"/caregiver/patients/{pid}/nickname", json={"nickname": "Mum"})
    gita.post("/caregiver/push/subscribe", json={"endpoint": f"https://push.example/gita-{pid}", "keys": {"p256dh": "k" * 12, "auth": "a" * 12}})
    return SimpleNamespace(mum=mum, gita=gita, gita_user=gita_user, pid=pid, doses=[d1[0], d2[0]])


def _missed(family, push):
    db = SessionLocal()
    try:
        doses = db.query(Dose).filter(Dose.id.in_(family.doses)).all()
        return reminders.notify_missed(db, doses, push_fn=push)
    finally:
        db.close()


def test_two_missed_doses_are_one_alert_in_the_name_the_caregiver_gave_with_a_remind_now_button(family):
    sent = []
    _missed(family, lambda ep, p, a, payload: sent.append((ep, payload)) or "sent")
    alerts = [p for ep, p in sent if "gita-" in ep]
    assert len(alerts) == 1
    a = alerts[0]
    assert a["title"] == "Mum missed 2 doses" and "Metformin" in a["body"] and "Telmisartan" in a["body"]
    assert a["actions"] == [{"action": "remind", "title": "Remind now"}, {"action": "open", "title": "Open"}]
    assert a["kind"] == "missed" and a["url"] == f"/caregiver#/p/{family.pid}/today" and a["action_url"] == "/push/action/nudge"
    claims = push_actions.read_nudge_token(a["action_token"])
    assert claims["u"] == family.gita_user["id"] and claims["p"] == family.pid and sorted(claims["d"]) == sorted(family.doses)
    again = []
    _missed(family, lambda ep, p, a2, payload: again.append(payload) or "sent")
    assert [x for x in again if x.get("kind") == "missed" and "Mum" in x["title"]] == []          # never alerted twice


def _button(family, monkeypatch):
    """The patient's phone is reachable; the caregiver's notification carries a token. Returns (patient pushes, token)."""
    got = []
    monkeypatch.setattr(care_push.webpush_service, "send", lambda ep, p, a, payload: got.append(payload) or "sent")
    monkeypatch.setattr(care_push.webpush_service, "is_configured", lambda: True)
    d = SessionLocal()
    d.add(PushSubscription(patient_id=family.pid, endpoint=f"https://push.example/mum-phone-{family.pid}", p256dh="k", auth="a"))
    d.commit()
    d.close()
    sent = []
    _missed(family, lambda ep, p, a, payload: sent.append((ep, payload)) or "sent")
    return got, [p for ep, p in sent if "gita-" in ep][0]["action_token"]


def test_pressing_remind_now_on_the_notification_reminds_the_patient_naming_what_was_missed(family, monkeypatch):
    got, token = _button(family, monkeypatch)
    r = TestClient(app).post("/push/action/nudge", json={"token": token})                     # no login: the service worker has none
    assert r.status_code == 200 and r.json()["sent"] == 1 and r.json()["label"] == "Mum"
    assert got[0]["title"] == "Gita sent you a reminder" and "Metformin" in got[0]["body"]
    again = TestClient(app).post("/push/action/nudge", json={"token": token})
    assert again.status_code == 429 and "minute" in again.json()["detail"]                    # the same cooldown as the in-app button
    assert len(got) == 1


def test_the_token_cannot_be_forged_reused_as_a_session_or_outlive_a_removed_caregiver(family, monkeypatch):
    got, token = _button(family, monkeypatch)
    anon = TestClient(app)
    assert anon.post("/push/action/nudge", json={"token": "nonsense"}).status_code == 401
    # signed with another key, or changed to point at another patient
    forged = jwt.encode({"typ": "nudge", "u": family.gita_user["id"], "p": family.pid, "exp": datetime.utcnow() + timedelta(hours=1)}, "wrong-key", algorithm="HS256")
    assert anon.post("/push/action/nudge", json={"token": forged}).status_code == 401
    # a login session token is not a nudge token ...
    assert anon.post("/push/action/nudge", json={"token": family.gita.headers["Authorization"].split()[1]}).status_code == 401
    # ... and a nudge token is not a login session
    assert anon.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    # expired
    old = jwt.encode({"typ": "nudge", "u": family.gita_user["id"], "p": family.pid, "exp": datetime.utcnow() - timedelta(minutes=1)}, auth.JWT_SECRET, algorithm="HS256")
    assert anon.post("/push/action/nudge", json={"token": old}).status_code == 401
    # a token for a patient the caregiver is NOT linked to does nothing, even though it is validly signed
    stranger = TestClient(app)
    other_pid, _ = new_patient(stranger, "Someone Else")
    mine_for_other = push_actions.make_nudge_token(family.gita_user["id"], other_pid, [])
    assert anon.post("/push/action/nudge", json={"token": mine_for_other}).status_code == 403
    # removing the caregiver cancels the buttons already on their phone
    link = [l for l in family.mum.get(f"/patients/{family.pid}/caregiver-links").json() if l["status"] == "active"][0]
    assert family.mum.post(f"/caregiver-links/{link['id']}/revoke").status_code == 200
    assert anon.post("/push/action/nudge", json={"token": token}).status_code == 403
    assert got == []                                                                          # nothing was ever sent through any of these


def test_a_token_naming_another_patients_doses_never_puts_them_in_the_message(family, monkeypatch):
    got, _ = _button(family, monkeypatch)
    stranger = TestClient(app)
    other_pid, _ = new_patient(stranger, "Someone Else")
    _, secret_doses = add_medicine(other_pid, "SecretMedicine", [datetime.utcnow()])
    tok = push_actions.make_nudge_token(family.gita_user["id"], family.pid, secret_doses)     # signed, linked, but the doses are not Mum's
    assert TestClient(app).post("/push/action/nudge", json={"token": tok}).status_code == 200
    assert "SecretMedicine" not in got[-1]["body"] and "SecretMedicine" not in got[-1]["title"]
