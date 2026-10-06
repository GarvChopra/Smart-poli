"""Care notes API: who can read and write what (stranger / unlinked / revoked caregiver / patient / doctor)."""
import os
import sys
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import care_push  # noqa: E402
from conftest import register_and_login  # noqa: E402
from main import app  # noqa: E402
from timing_helpers import add_medicine, new_patient  # noqa: E402


def link_caregiver(patient_client, pid, name="Sita"):
    code = patient_client.post(f"/patients/{pid}/caregiver-links").json()["code"]
    cg = TestClient(app)
    user = register_and_login(cg, role="caregiver", name=name)
    r = cg.post("/caregiver/link/redeem", json={"code": code})
    assert r.status_code == 200, r.text
    return cg, user


def link_doctor(patient_client, pid):
    code = patient_client.post(f"/patients/{pid}/doctor-links").json()["code"]
    dr = TestClient(app)
    register_and_login(dr, role="doctor", name="Dr Rao")
    assert dr.post("/doctor/link/redeem", json={"code": code}).status_code == 200
    return dr


def revoke_caregiver(patient_client, pid):
    link = [l for l in patient_client.get(f"/patients/{pid}/caregiver-links").json() if l["status"] == "active"][0]
    assert patient_client.post(f"/caregiver-links/{link['id']}/revoke").status_code == 200


@pytest.fixture()
def world(monkeypatch):
    pushes = []
    monkeypatch.setattr(care_push.webpush_service, "send", lambda ep, p, a, payload: pushes.append((ep, payload)) or "sent")
    monkeypatch.setattr(care_push.webpush_service, "is_configured", lambda: True)
    from db import init_db
    init_db()                                   # these tests use plain TestClient(app) objects, so create the tables first
    patient = TestClient(app)
    pid, _ = new_patient(patient)
    med, _ = add_medicine(pid, "Telma", [datetime(2030, 1, 1, 8)])
    cg, cg_user = link_caregiver(patient, pid)
    yield SimpleNamespace(patient=patient, pid=pid, med=med, cg=cg, cg_user=cg_user, pushes=pushes)


def post(cg, pid, **body):
    return cg.post(f"/caregiver/patients/{pid}/notes", json=body)


def test_a_linked_caregiver_writes_and_reads_all_three_kinds(world):
    assert post(world.cg, world.pid, kind="message", body="Doctor visit tomorrow").status_code == 200
    assert post(world.cg, world.pid, kind="medicine", body="Take with milk", medicine_id=world.med).status_code == 200
    assert post(world.cg, world.pid, kind="handover", body="BP felt high at 5").status_code == 200
    kinds = {n["kind"] for n in world.cg.get(f"/caregiver/patients/{world.pid}/notes").json()["notes"]}
    assert kinds == {"message", "medicine", "handover"}
    only = world.cg.get(f"/caregiver/patients/{world.pid}/notes?kind=handover").json()["notes"]
    assert [n["body"] for n in only] == ["BP felt high at 5"]


def test_a_revoked_caregiver_loses_everything(world):
    n = post(world.cg, world.pid, kind="message", body="hello").json()
    revoke_caregiver(world.patient, world.pid)
    assert post(world.cg, world.pid, kind="message", body="still here?").status_code == 403
    assert world.cg.get(f"/caregiver/patients/{world.pid}/notes").status_code == 403
    assert world.cg.delete(f"/caregiver/notes/{n['id']}").status_code == 403           # even their own note
    assert world.cg.post(f"/caregiver/patients/{world.pid}/nudge").status_code == 403


def test_unlinked_caregivers_and_strangers_are_refused(world):
    other = TestClient(app)
    register_and_login(other, role="caregiver", name="Nobody")
    assert post(other, world.pid, kind="message", body="hi").status_code == 403
    assert other.get(f"/caregiver/patients/{world.pid}/notes").status_code == 403
    assert TestClient(app).get(f"/caregiver/patients/{world.pid}/notes").status_code == 401         # not logged in
    assert world.patient.post(f"/caregiver/patients/{world.pid}/notes", json={"kind": "message", "body": "x"}).status_code == 403   # patients are not caregivers


def test_bad_notes_are_refused_with_plain_messages(world):
    assert post(world.cg, world.pid, kind="message", body="   ").status_code == 422
    assert post(world.cg, world.pid, kind="message", body="a" * 501).status_code == 422
    assert post(world.cg, world.pid, kind="medicine", body="x note").status_code == 422             # no medicine named
    other_pid, _ = new_patient(TestClient(app), "Other")
    other_med, _ = add_medicine(other_pid, "Iron", [datetime(2030, 1, 1, 8)])
    r = post(world.cg, world.pid, kind="medicine", body="x note", medicine_id=other_med)            # another patient's medicine
    assert r.status_code == 422 and "this patient" in r.json()["detail"]
    assert post(world.cg, world.pid, kind="shout", body="hi").status_code == 422


def test_only_the_author_can_delete_and_the_patient_stops_seeing_it(world):
    n = post(world.cg, world.pid, kind="message", body="oops").json()
    cg2, _ = link_caregiver(world.patient, world.pid, "Ravi")
    assert cg2.delete(f"/caregiver/notes/{n['id']}").status_code == 404                  # another caregiver cannot delete it
    assert world.cg.delete(f"/caregiver/notes/{n['id']}").status_code == 200
    assert world.cg.get(f"/caregiver/patients/{world.pid}/notes").json()["notes"] == []


def test_two_caregivers_share_handover_notes_and_a_doctor_can_read_them(world):
    cg2, _ = link_caregiver(world.patient, world.pid, "Ravi")
    post(world.cg, world.pid, kind="handover", body="gave tea")
    assert [n["body"] for n in cg2.get(f"/caregiver/patients/{world.pid}/notes?kind=handover").json()["notes"]] == ["gave tea"]
    dr = link_doctor(world.patient, world.pid)
    notes = dr.get(f"/doctor/patients/{world.pid}/care-notes").json()["notes"]
    assert [n["body"] for n in notes] == ["gave tea"]
    other = TestClient(app)
    register_and_login(other, role="doctor")
    assert other.get(f"/doctor/patients/{world.pid}/care-notes").status_code == 403       # an unlinked doctor


def test_a_message_can_notify_the_patients_phone_and_script_text_stays_inert(world):
    from db import PushSubscription, SessionLocal
    d = SessionLocal()
    d.add(PushSubscription(patient_id=world.pid, endpoint="https://push.example/p1", p256dh="k", auth="a"))
    d.commit()
    d.close()
    r = post(world.cg, world.pid, kind="message", body="<b>Take</b> your tablet please, it is important " * 3, notify=True)
    assert r.status_code == 200 and r.json()["notified"] == 1
    assert len(world.pushes) == 1 and len(world.pushes[0][1]["body"]) <= 100
    r = post(world.cg, world.pid, kind="message", body="<script>alert(1)</script>")
    assert r.status_code == 200 and r.json()["body"] == "<script>alert(1)</script>"        # raw JSON; the page escapes it


def test_notes_are_rate_limited_per_author(world):
    codes = [post(world.cg, world.pid, kind="message", body=f"n{i}").status_code for i in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429
