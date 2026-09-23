"""
Tests for the doctor dashboard/correction workflow (doctor_router.py, Part 1
and Part 7 of the brief): a doctor only sees linked patients, and a
correction to a medicine is never silent — it must leave an audit trail
with the original value, the corrected value, who made it, and why.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from conftest import register_and_login  # noqa: E402


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
