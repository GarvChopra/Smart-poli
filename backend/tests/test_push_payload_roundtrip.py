"""What actually crosses the wire: SmartPoli's real notifications, encrypted with real VAPID keys for a real browser-style
subscription, then decrypted the way the phone does. Title, body, buttons and the Remind now token must arrive intact, and
every message must fit the push services' payload limit (4096 bytes)."""
import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import http_ece  # noqa: E402
import pytest  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import push_actions  # noqa: E402
import reminders  # noqa: E402
import webpush_service  # noqa: E402
from db import Dose, SessionLocal, init_db  # noqa: E402
from main import app  # noqa: E402
from test_care_notes_api import link_caregiver  # noqa: E402
from test_webpush_service import B64, patch_post, vapid_env  # noqa: E402,F401
from timing_helpers import add_medicine, new_patient  # noqa: E402

LIMIT = 4096


class Phone:
    """A browser's push subscription, with the private half so we can decrypt like the browser does."""
    def __init__(self, endpoint):
        self.priv = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        pub = self.priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.endpoint, self.p256dh = endpoint, B64(pub)
        self.keys = {"p256dh": self.p256dh, "auth": B64(self.auth)}

    def open(self, request) -> dict:
        assert request["headers"]["content-encoding"] == "aes128gcm"
        assert len(request["data"]) <= LIMIT, f"{len(request['data'])} bytes is over the push limit"
        plain = http_ece.decrypt(request["data"], private_key=self.priv, auth_secret=self.auth, version="aes128gcm")
        return json.loads(plain.decode("utf-8"))


def _send_for_real(ep, p256dh, auth, payload):
    return webpush_service.send(ep, p256dh, auth, payload)


@pytest.fixture()
def family(vapid_env, monkeypatch):
    init_db()
    seen = []
    patch_post(monkeypatch, 201, seen)
    mum = TestClient(app)
    pid, _ = new_patient(mum, "Sunita")
    gita, gita_user = link_caregiver(mum, pid, "Gita")
    gita.patch(f"/caregiver/patients/{pid}/nickname", json={"nickname": "Mum"})
    phone_g = Phone(f"https://push.example.test/gita-{pid}")
    assert gita.post("/caregiver/push/subscribe", json={"endpoint": phone_g.endpoint, "keys": phone_g.keys}).status_code == 200
    phone_m = Phone(f"https://push.example.test/mum-{pid}")
    assert mum.post(f"/patients/{pid}/push/subscribe", json={"endpoint": phone_m.endpoint, "keys": phone_m.keys}).status_code == 200
    return SimpleNamespace(mum=mum, gita=gita, gita_user=gita_user, pid=pid, seen=seen, phone_g=phone_g, phone_m=phone_m)


def _to(seen, phone):
    return [r for r in seen if r["url"] == phone.endpoint]


def test_the_caregivers_missed_alert_arrives_intact_with_its_buttons_and_a_working_token(family):
    past = datetime.utcnow() - timedelta(hours=5)
    _, d1 = add_medicine(family.pid, "Metformin", [past], states=["missed"])
    _, d2 = add_medicine(family.pid, "Telmisartan", [past], states=["missed"])
    db = SessionLocal()
    try:
        reminders.notify_missed(db, db.query(Dose).filter(Dose.id.in_(d1 + d2)).all(), push_fn=_send_for_real)
    finally:
        db.close()
    got = _to(family.seen, family.phone_g)
    assert len(got) == 1
    msg = family.phone_g.open(got[0])
    assert msg["title"] == "Mum missed 2 doses" and "Metformin" in msg["body"] and "Telmisartan" in msg["body"]
    assert msg["actions"] == [{"action": "remind", "title": "Remind now"}, {"action": "open", "title": "Open"}]
    assert msg["kind"] == "missed" and msg["url"] == f"/caregiver#/p/{family.pid}/today" and msg["action_url"] == "/push/action/nudge"
    claims = push_actions.read_nudge_token(msg["action_token"])                  # the token that came out of the phone works
    assert claims["u"] == family.gita_user["id"] and claims["p"] == family.pid
    # the patient's own missed notice is a separate message to their own phone, with the safety advice
    mine = [family.phone_m.open(r) for r in _to(family.seen, family.phone_m)]
    assert mine and all("double" in m["body"].lower() for m in mine)


def test_even_the_largest_alert_fits_the_push_limit(family):
    past = datetime.utcnow() - timedelta(hours=5)
    ids = []
    for i in range(12):                                                         # a dozen missed doses with long medicine names
        _, d = add_medicine(family.pid, f"Extremely-Long-Medicine-Name-Number-{i:02d} Forte Plus", [past + timedelta(minutes=i)], states=["missed"])
        ids += d
    db = SessionLocal()
    try:
        reminders.notify_missed(db, db.query(Dose).filter(Dose.id.in_(ids)).all(), push_fn=_send_for_real)
    finally:
        db.close()
    msg = family.phone_g.open(_to(family.seen, family.phone_g)[0])             # open() also asserts the size limit
    assert msg["title"] == "Mum missed 12 doses" and "+9 more" in msg["body"]
    assert len(push_actions.read_nudge_token(msg["action_token"])["d"]) <= 10   # the token never grows without bound


def test_two_medicines_due_together_arrive_as_one_readable_reminder(family):
    now = datetime.utcnow().replace(second=0, microsecond=0)
    _, a = add_medicine(family.pid, "Metformin", [now - timedelta(minutes=1)])
    _, b = add_medicine(family.pid, "Telmisartan", [now - timedelta(minutes=1)])
    db = SessionLocal()
    try:
        reminders.run_reminder_sweep(db, now, _send_for_real, lambda *x: True)
    finally:
        db.close()
    msgs = [family.phone_m.open(r) for r in _to(family.seen, family.phone_m)]
    ours = [m for m in msgs if set(m.get("dose_ids", [])) & set(a + b)]
    assert len(ours) == 1 and ours[0]["kind"] == "due"
    assert "Metformin" in ours[0]["body"] and "Telmisartan" in ours[0]["body"] and ours[0]["tag"]
    assert "actions" not in ours[0] and ours[0]["url"] == "/"                   # an ordinary reminder: no buttons, opens the app


def test_unicode_names_survive_encryption(family):
    past = datetime.utcnow() - timedelta(hours=5)
    _, d = add_medicine(family.pid, "पैरासिटामोल 650", [past], states=["missed"])
    db = SessionLocal()
    try:
        reminders.notify_missed(db, db.query(Dose).filter(Dose.id.in_(d)).all(), push_fn=_send_for_real)
    finally:
        db.close()
    assert "पैरासिटामोल" in family.phone_g.open(_to(family.seen, family.phone_g)[0])["body"]
