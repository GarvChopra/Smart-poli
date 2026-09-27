"""
Tests for doctor-authored prescriptions and saved prescription templates
(doctor_router.py). A doctor writing a prescription goes through the exact
same parser/confidence gate as the patient manual path, and still leaves
scheduling to the patient — a doctor's write access never extends to
generating dose rows on someone else's record.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from db import SessionLocal, AuditLog  # noqa: E402
from conftest import register_and_login  # noqa: E402


def _link_doctor(client, patient_id):
    r = client.post(f"/patients/{patient_id}/doctor-links")
    code = r.json()["code"]
    register_and_login(client, role="doctor", name="Dr. Writer")
    client.post("/doctor/link/redeem", json={"code": code})


def test_doctor_can_write_a_prescription_for_a_linked_patient():
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        patient_auth = client.headers["Authorization"]
        r = client.post("/patients", json={"name": "Doctor Written Patient"})
        patient_id = r.json()["id"]
        _link_doctor(client, patient_id)

        r = client.post(f"/doctor/patients/{patient_id}/prescriptions", json={
            "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "draft"
        assert body["medicines"][0]["name"]
        assert body["medicines"][0]["status"] in ("verified", "review")

        # It's a draft like any other — the PATIENT still confirms it before
        # any doses are scheduled, never the doctor.
        client.headers["Authorization"] = patient_auth
        r = client.get(f"/patients/{patient_id}/dashboard")
        assert r.json()["upcoming_doses"] == []


def test_doctor_prescription_rejects_unlinked_patient():
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        r = client.post("/patients", json={"name": "No Link Patient"})
        patient_id = r.json()["id"]

        register_and_login(client, role="doctor")
        r = client.post(f"/doctor/patients/{patient_id}/prescriptions", json={
            "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"],
        })
        assert r.status_code == 403


def test_doctor_prescription_audit_trail_records_the_doctor_as_actor():
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        r = client.post("/patients", json={"name": "Audited Patient"})
        patient_id = r.json()["id"]
        _link_doctor(client, patient_id)
        r = client.post(f"/doctor/patients/{patient_id}/prescriptions", json={
            "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"],
        })
        prescription_id = r.json()["prescription_id"]

        db = SessionLocal()
        try:
            entry = (
                db.query(AuditLog)
                .filter(AuditLog.patient_id == patient_id, AuditLog.action == "prescription_created")
                .order_by(AuditLog.at.desc())
                .first()
            )
        finally:
            db.close()
        assert entry is not None
        assert entry.actor.startswith("doctor:")
        assert "Dr. Writer" in entry.actor
        assert str(prescription_id) in entry.detail


def test_prescription_template_crud():
    with TestClient(app) as client:
        register_and_login(client, role="doctor")

        r = client.post("/doctor/templates", json={
            "label": "Common cold combo",
            "lines": ["Tab Dolo 650mg 1-0-1 PC x5d", "Cap Amoxicillin 500mg TDS AC 7 days"],
        })
        assert r.status_code == 200, r.text
        template = r.json()
        assert template["label"] == "Common cold combo"
        assert len(template["lines"]) == 2

        r = client.get("/doctor/templates")
        assert len(r.json()) == 1
        assert r.json()[0]["id"] == template["id"]

        r = client.delete(f"/doctor/templates/{template['id']}")
        assert r.status_code == 200
        r = client.get("/doctor/templates")
        assert r.json() == []


def test_prescription_template_rejects_empty_label_or_lines():
    with TestClient(app) as client:
        register_and_login(client, role="doctor")
        r = client.post("/doctor/templates", json={"label": "  ", "lines": ["Tab Dolo 650mg 1-0-1"]})
        assert r.status_code == 400
        r = client.post("/doctor/templates", json={"label": "Empty", "lines": ["   ", ""]})
        assert r.status_code == 400


def test_templates_are_scoped_per_doctor():
    with TestClient(app) as client:
        register_and_login(client, role="doctor", name="Dr. One")
        client.post("/doctor/templates", json={"label": "Dr One's template", "lines": ["Tab Dolo 650mg OD"]})

        register_and_login(client, role="doctor", name="Dr. Two")
        r = client.get("/doctor/templates")
        assert r.json() == []

        r = client.post("/doctor/templates", json={"label": "Dr Two's template", "lines": ["Tab X OD"]})
        template_id = r.json()["id"]

        # Dr. One can't delete Dr. Two's template.
        register_and_login(client, role="doctor", name="Dr. One Again")
        r = client.delete(f"/doctor/templates/{template_id}")
        assert r.status_code == 403
