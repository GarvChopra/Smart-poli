"""A linked caregiver can add a medicine for the patient (labelled with who added it, the patient is told), and can take
back only what they added. The doctor role is switched off: no sign-up, no login, no /doctor page or API, no access."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from conftest import register_and_login  # noqa: E402
from db import Medicine, PushSubscription, SessionLocal, init_db  # noqa: E402
from main import app  # noqa: E402
from test_care_notes_api import link_caregiver, link_doctor, revoke_caregiver  # noqa: E402
from timing_helpers import new_patient  # noqa: E402

MED = {"name": "Dolo 650", "strength": "650mg", "form": "tab", "as_needed": False, "times": ["08:00", "20:00"],
       "weekdays": None, "food": "after", "duration_days": 5}


@pytest.fixture()
def world(monkeypatch):
    init_db()
    pushes = []
    monkeypatch.setattr(main.webpush_service, "send", lambda ep, p, a, payload: pushes.append(payload) or "sent")
    patient = TestClient(app)
    pid, _ = new_patient(patient, "Anita")
    patient.post(f"/patients/{pid}/push/subscribe",
                 json={"endpoint": f"https://push.example.test/anita-{pid}", "keys": {"p256dh": "k" * 20, "auth": "a" * 12}})
    cg, cg_user = link_caregiver(patient, pid, "Sita")
    return pid, patient, cg, cg_user, pushes


def test_a_linked_caregiver_adds_a_medicine_and_the_patient_sees_who_added_it_and_is_told(world):
    pid, patient, cg, cg_user, pushes = world
    r = cg.post("/medicines/manual", json={**MED, "patient_id": pid})
    assert r.status_code == 200, r.text
    assert r.json()["doses_scheduled"] > 0 and r.json()["medicine"]["added_by"] == {"user_id": cg_user["id"], "name": "Sita"}
    assert any(p["kind"] == "medicine_added" and p["title"] == "Sita added Dolo 650 for you" for p in pushes)
    mine = [m for m in patient.get(f"/patients/{pid}/safety-center").json()["medicines"] if m["name"] == "Dolo 650"]
    assert mine and mine[0]["added_by"]["name"] == "Sita"
    per_med = cg.get(f"/caregiver/patients/{pid}/overview").json()["per_medicine"]
    assert [m["added_by_me"] for m in per_med if m["name"] == "Dolo 650"] == [True]


def test_what_the_patient_adds_is_not_labelled_and_the_caregiver_cannot_remove_it(world):
    pid, patient, cg, _, _ = world
    own = patient.post("/medicines/manual", json={**MED, "name": "Telma 40", "patient_id": pid}).json()["medicine"]
    assert own["added_by"] is None
    assert cg.delete(f"/medicines/{own['id']}").status_code == 403
    added = cg.post("/medicines/manual", json={**MED, "patient_id": pid}).json()["medicine"]
    assert cg.delete(f"/medicines/{added['id']}").status_code == 200        # takes back only its own
    assert patient.delete(f"/medicines/{own['id']}").status_code == 200     # the patient can remove anything


def test_the_patient_can_remove_what_the_caregiver_added(world):
    pid, patient, cg, _, _ = world
    added = cg.post("/medicines/manual", json={**MED, "patient_id": pid}).json()["medicine"]
    assert patient.delete(f"/medicines/{added['id']}").status_code == 200
    per_med = cg.get(f"/caregiver/patients/{pid}/overview").json()["per_medicine"]
    assert "Dolo 650" not in [m["name"] for m in per_med]                       # gone from the caregiver's list too


def test_an_unlinked_or_revoked_caregiver_cannot_add(world):
    pid, patient, cg, _, _ = world
    stranger = TestClient(app)
    register_and_login(stranger, role="caregiver", name="Stranger")
    assert stranger.post("/medicines/manual", json={**MED, "patient_id": pid}).status_code == 403
    assert stranger.post("/medicines/scan", data={"patient_id": pid},
                         files={"file": ("x.jpg", b"not really", "image/jpeg")}).status_code == 403
    revoke_caregiver(patient, pid)
    assert cg.post("/medicines/manual", json={**MED, "patient_id": pid}).status_code == 403


def test_a_caregiver_still_cannot_change_anything_else(world):
    pid, _, cg, _, _ = world
    assert cg.put(f"/patients/{pid}/settings", json={"timezone": "Asia/Kolkata"}).status_code == 403
    assert cg.post("/prescriptions", json={"patient_id": pid, "lines": ["Tab Dolo 650 1-0-1"]}).status_code == 403


def test_the_caregiver_page_offers_scan_and_manual_with_the_shared_popups():
    static = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    page = open(os.path.join(static, "caregiver.html"), encoding="utf-8").read()
    js = open(os.path.join(static, "caregiver-pages.js"), encoding="utf-8").read()
    assert page.index("/static/med-wizard.js") < page.index("/static/caregiver-pages.js")
    assert 'data-add-med="scan"' in js and 'data-add-med="manual"' in js and "Added by you" in js


# ---------------------------------------------------------------- the doctor role is switched off

@pytest.fixture()
def doctor_off(monkeypatch):
    init_db()
    patient = TestClient(app)
    pid, _ = new_patient(patient, "Anita")
    dr = link_doctor(patient, pid)                       # made while the role was on (tests turn it on in conftest)
    monkeypatch.delenv("SMARTPOLI_DOCTOR_ROLE", raising=False)
    return pid, patient, dr


def test_with_the_doctor_role_off_there_is_no_doctor_anything(doctor_off):
    pid, patient, dr = doctor_off
    r = TestClient(app).post("/auth/register", json={"email": "dr.off@example.com", "password": "password123",
                                                    "name": "D", "role": "doctor"})
    assert r.status_code == 400
    assert dr.get(f"/patients/{pid}/dashboard").status_code == 403           # an old doctor link no longer opens the record
    assert dr.get("/doctor/patients").status_code == 404
    assert patient.post(f"/patients/{pid}/doctor-links").status_code == 404
    page = TestClient(app).get("/doctor", follow_redirects=False)
    assert page.status_code == 307 and page.headers["location"] == "/login"
    js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "app.js"), encoding="utf-8").read()
    assert "Link a doctor" not in js and "/doctor-links" not in js
