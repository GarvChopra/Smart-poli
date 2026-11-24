"""
Tests for the doctor dashboard/correction workflow (doctor_router.py, Part 1
and Part 7 of the brief): a doctor only sees linked patients, and a
correction to a medicine is never silent — it must leave an audit trail
with the original value, the corrected value, who made it, and why.
"""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from conftest import register_and_login  # noqa: E402


def test_doctor_reads_only_selected_medicine_dose_history_for_linked_patient():
    from db import Dose, SessionLocal
    from clock import patient_now

    with TestClient(app) as client:
        register_and_login(client, role="patient", name="Dose History Patient")
        patient_auth = client.headers["Authorization"]
        patient_id = client.post("/patients", json={"name": "Dose History Patient"}).json()["id"]
        rx = client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"],
        }).json()
        medicine_id = rx["medicines"][0]["id"]
        client.patch(f"/medicines/{medicine_id}", json={"name": "Dolo", "schedule_code": "1-0-1"})
        client.post(f"/prescriptions/{rx['prescription_id']}/confirm")
        code = client.post(f"/patients/{patient_id}/doctor-links", json={"scopes": ["current_medicines", "dose_checkins"]}).json()["code"]
        register_and_login(client, role="doctor", name="Dr. History")
        assert client.post("/doctor/link/redeem", json={"code": code}).status_code == 200

        with SessionLocal() as db:
            now = patient_now(db, patient_id)
            db.add_all([
                Dose(medicine_id=medicine_id, scheduled_at=now.replace(hour=8, minute=0, second=0, microsecond=0),
                     state="taken", acted_at=now.replace(hour=8, minute=5, second=0, microsecond=0)),
                Dose(medicine_id=medicine_id, scheduled_at=now.replace(hour=13, minute=0, second=0, microsecond=0),
                     state="missed"),
                Dose(medicine_id=medicine_id, scheduled_at=now - timedelta(days=8), state="taken"),
            ])
            db.commit()

        response = client.get(f"/doctor/patients/{patient_id}/medicines/{medicine_id}/dose-history?days=7")
        assert response.status_code == 200, response.text
        history = response.json()
        assert len(history["days"]) == 7
        assert history["today"] == history["patient_now"][:10]
        states = {dose["state"] for dose in history["doses"]}
        assert {"taken", "missed", "pending"}.issubset(states)
        assert all(dose["scheduled_at"][:10] in history["days"] for dose in history["doses"])
        assert all(dose["medicine_name"] == "Dolo" for dose in history["doses"])
        assert client.get(f"/doctor/patients/{patient_id}/medicines/{medicine_id}/dose-history?days=31").status_code == 422

        client.headers["Authorization"] = patient_auth
        other_id = client.post("/patients", json={"name": "Unlinked Patient"}).json()["id"]
        register_and_login(client, role="doctor", name="Dr. Unlinked")
        assert client.get(f"/doctor/patients/{other_id}/medicines/{medicine_id}/dose-history").status_code == 403


def test_doctor_prescription_ui_uses_full_patient_day_agenda():
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    js = (backend / "static" / "doctor.js").read_text(encoding="utf-8")
    css = (backend / "static" / "doctor.css").read_text(encoding="utf-8")
    assert "medicines.length > 5 ? 'is-scrollable'" in js
    assert "DayTimeline.mount(target, state.patientId" in js
    assert "/doctor/patients/${state.patientId}/doses" in js
    assert 'data-record-tab="overview"' in (backend / "static" / "doctor.html").read_text(encoding="utf-8")
    assert 'data-record-tab="prescriptions"' in (backend / "static" / "doctor.html").read_text(encoding="utf-8")
    assert 'data-record-tab="triage"' in (backend / "static" / "doctor.html").read_text(encoding="utf-8")
    patient_js = (backend / "static" / "app.js").read_text(encoding="utf-8")
    assert "clinicianEnabled ?" in patient_js and "genDoctorCodeBtn" in patient_js
    assert "data-tab=\"care\"" in (backend / "static" / "index.html").read_text(encoding="utf-8")
    assert "/patients/${state.patientId}/appointments" in patient_js and "time_preference:" in patient_js
    assert "data-visit-action" in js and "Visit requests" in js
    assert ".doctor-rx-list.is-scrollable" in css
    assert "@media (max-width: 760px)" in css


def test_patient_visit_request_confirm_check_in_call_and_completion():
    from datetime import date, timedelta

    with TestClient(app) as client:
        register_and_login(client, role="patient", name="Visit Request Patient")
        patient_auth = client.headers["Authorization"]
        patient_id = client.post("/patients", json={"name": "Visit Request Patient"}).json()["id"]
        invite = client.post(f"/patients/{patient_id}/doctor-links").json()

        doctor = register_and_login(client, role="doctor", name="Dr Visit")
        doctor_auth = client.headers["Authorization"]
        assert client.post("/doctor/link/redeem", json={"code": invite["code"]}).status_code == 200

        visit_day = (date.today() + timedelta(days=1)).isoformat()
        client.headers["Authorization"] = patient_auth
        requested = client.post(f"/patients/{patient_id}/appointments", json={
            "doctor_user_id": doctor["id"], "visit_date": visit_day, "time_preference": "afternoon",
        })
        assert requested.status_code == 200, requested.text
        visit_id = requested.json()["id"]
        assert requested.json()["status"] == "requested"

        client.headers["Authorization"] = doctor_auth
        queue = client.get(f"/doctor/appointments?visit_date={visit_day}").json()
        assert len(queue) == 1 and queue[0]["patient_name"] == "Visit Request Patient"
        assert client.patch(f"/doctor/appointments/{visit_id}", json={
            "action": "confirm", "scheduled_at": f"{visit_day}T14:30:00",
        }).json()["status"] == "confirmed"
        checked_in = client.patch(f"/doctor/appointments/{visit_id}", json={"action": "check_in"}).json()
        assert checked_in["status"] == "checked_in" and checked_in["token_number"] == 1
        assert client.patch(f"/doctor/appointments/{visit_id}", json={"action": "call"}).json()["status"] == "called"

        client.headers["Authorization"] = patient_auth
        patient_view = client.get(f"/patients/{patient_id}/appointments").json()
        assert patient_view[0]["status"] == "called" and patient_view[0]["token_number"] == 1
        client.headers["Authorization"] = doctor_auth
        assert client.patch(f"/doctor/appointments/{visit_id}", json={"action": "complete"}).json()["status"] == "completed"
def test_clinician_record_reads_follow_patient_selected_share_scopes():
    with TestClient(app) as client:
        register_and_login(client, role="patient", name="Scope Owner")
        patient_id = client.post("/patients", json={"name": "Scope Owner"}).json()["id"]
        rx = client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"],
        }).json()
        from db import Dose, SessionLocal, Patient
        with SessionLocal() as db:
            patient = db.query(Patient).filter(Patient.id == patient_id).first()
            patient.allergies = "Penicillin"
            db.add(Dose(medicine_id=rx["medicines"][0]["id"], scheduled_at=datetime.utcnow(),
                        state="missed"))
            db.commit()
        invite = client.post(f"/patients/{patient_id}/doctor-links", json={
            "scopes": ["current_medicines"],
        }).json()
        register_and_login(client, role="doctor", name="Dr Scope")
        assert client.post("/doctor/link/redeem", json={"code": invite["code"]}).status_code == 200

        detail = client.get(f"/doctor/patients/{patient_id}")
        assert detail.status_code == 200
        assert detail.json()["patient"]["allergies"] is None
        assert detail.json()["adherence"] is None
        assert detail.json()["symptom_history"] == []
        assert client.get(f"/doctor/patients/{patient_id}/doses").status_code == 403
        assert client.get(f"/patients/{patient_id}/doses").status_code == 403
        assert client.post(f"/patients/{patient_id}/notes", json={"note": "Not shared"}).status_code == 403

        # Granting the allergy scope later must reveal the stored value; the
        # clinician detail endpoint intentionally redacts it by default.
        from db import DoctorLink, DoctorShareConsent, SessionLocal
        import json
        with SessionLocal() as db:
            link_id = db.query(DoctorLink).filter(DoctorLink.patient_id == patient_id).one().id
            consent = db.query(DoctorShareConsent).filter(DoctorShareConsent.doctor_link_id == link_id).one()
            consent.scopes = json.dumps(["current_medicines", "allergies"])
            db.commit()
        assert client.get(f"/doctor/patients/{patient_id}").json()["patient"]["allergies"] == "Penicillin"

        with SessionLocal() as db:
            consent = db.query(DoctorShareConsent).filter(DoctorShareConsent.doctor_link_id == link_id).one()
            consent.scopes = json.dumps(["dose_checkins"])
            db.commit()
        detail = client.get(f"/doctor/patients/{patient_id}").json()
        assert detail["adherence"]["missed"] == 1
        assert detail["missed_doses"] == []
        assert detail["prescriptions"] == []


def test_doctor_sees_only_linked_patients_and_detail_matches_report():
    with TestClient(app) as client:
        register_and_login(client, role="patient", name="Doctor Test Patient")
        patient_auth = client.headers["Authorization"]
        r = client.post("/patients", json={"name": "Reviewed Patient"})
        patient_id = r.json()["id"]
        client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"],
        })
        r = client.post(f"/patients/{patient_id}/doctor-links")
        code = r.json()["code"]

        register_and_login(client, role="doctor", name="Dr. Reviewer")
        r = client.get("/doctor/patients")
        assert r.json() == []  # not linked yet

        r = client.post("/doctor/link/redeem", json={"code": code})
        assert r.status_code == 200

        r = client.get("/doctor/patients")
        assert len(r.json()) == 1
        assert r.json()[0]["id"] == patient_id

        r = client.get(f"/doctor/patients/{patient_id}")
        assert r.status_code == 200
        detail = r.json()
        assert detail["patient"]["id"] == patient_id
        assert len(detail["prescriptions"][0]["medicines"]) == 1

        # An unlinked patient must be refused.
        client.headers["Authorization"] = patient_auth
        r = client.post("/patients", json={"name": "Other Patient"})
        other_id = r.json()["id"]
        register_and_login(client, role="doctor", name="Dr. Reviewer")  # fresh, unlinked doctor
        r = client.get(f"/doctor/patients/{other_id}")
        assert r.status_code == 403


def test_doctor_correction_is_never_silent():
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        patient_auth = client.headers["Authorization"]
        r = client.post("/patients", json={"name": "Correction Test"})
        patient_id = r.json()["id"]
        r = client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 500mg 1-0-1 5 days"],
        })
        medicine_id = r.json()["medicines"][0]["id"]
        r = client.post(f"/patients/{patient_id}/doctor-links")
        code = r.json()["code"]

        register_and_login(client, role="doctor", name="Dr. Corrector")
        client.post("/doctor/link/redeem", json={"code": code})

        r = client.post(f"/doctor/medicines/{medicine_id}/correction", json={
            "field": "dose_amount", "corrected_value": "650", "reason": "Actual strength confirmed by doctor.",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["medicine"]["dose_amount"] == "650"
        assert body["medicine"]["status"] == "verified"

        r = client.get(f"/doctor/medicines/{medicine_id}/corrections")
        assert r.status_code == 200
        corrections = r.json()
        assert len(corrections) == 1
        assert corrections[0]["original_value"] == "500"
        assert corrections[0]["corrected_value"] == "650"
        assert corrections[0]["reason"] == "Actual strength confirmed by doctor."

        # raw_text (the original prescription line) is untouched.
        client.headers["Authorization"] = patient_auth
        r = client.get(f"/patients/{patient_id}/report")
        med = r.json()["prescriptions"][0]["medicines"][0]
        assert "500mg" in med["raw_text"]
        assert med["dose_amount"] == "650"


def test_doctor_correction_rejects_unlinked_medicine():
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        r = client.post("/patients", json={"name": "No Link Test"})
        patient_id = r.json()["id"]
        r = client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 500mg 1-0-1 5 days"],
        })
        medicine_id = r.json()["medicines"][0]["id"]

        register_and_login(client, role="doctor")
        r = client.post(f"/doctor/medicines/{medicine_id}/correction", json={
            "field": "dose_amount", "corrected_value": "650", "reason": "no link",
        })
        assert r.status_code == 403
