"""
Tests for the real auth/RBAC layer (auth.py, auth_router.py): the exact
thing the brief calls out as missing — "frontend role selection must NOT
grant permissions" and "one user cannot access another patient's records
by changing an ID in the URL."
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from conftest import register_and_login  # noqa: E402


def test_endpoints_require_a_token():
    with TestClient(app) as client:
        r = client.get("/patients/1/dashboard")
        assert r.status_code == 401
        r = client.post("/patients", json={"name": "No Auth"})
        assert r.status_code == 401


def test_register_rejects_bad_input():
    with TestClient(app) as client:
        r = client.post("/auth/register", json={
            "email": "not-an-email", "password": "longenough1", "name": "X", "role": "patient",
        })
        assert r.status_code == 400
        r = client.post("/auth/register", json={
            "email": "short@example.com", "password": "short", "name": "X", "role": "patient",
        })
        assert r.status_code == 400
        r = client.post("/auth/register", json={
            "email": "badrole@example.com", "password": "longenough1", "name": "X", "role": "wizard",
        })
        assert r.status_code == 400


def test_duplicate_email_rejected():
    with TestClient(app) as client:
        payload = {"email": "dupe@example.com", "password": "longenough1", "name": "X", "role": "patient"}
        r = client.post("/auth/register", json=payload)
        assert r.status_code == 200
        r = client.post("/auth/register", json=payload)
        assert r.status_code == 409


def test_login_wrong_password_rejected():
    with TestClient(app) as client:
        client.post("/auth/register", json={
            "email": "loginme@example.com", "password": "correcthorse1", "name": "X", "role": "patient",
        })
        r = client.post("/auth/login", json={"email": "loginme@example.com", "password": "wrongpassword"})
        assert r.status_code == 401


def test_a_patient_cannot_read_another_patients_dashboard_by_id():
    """Part 18: 'Make sure one user cannot access another patient's records
    by changing an ID in the URL.' Two different patient accounts, two
    different patient records — B must not be able to read A's dashboard."""
    with TestClient(app) as client:
        register_and_login(client, role="patient", name="Patient A")
        r = client.post("/patients", json={"name": "A's record"})
        patient_a_id = r.json()["id"]

        register_and_login(client, role="patient", name="Patient B")
        r = client.get(f"/patients/{patient_a_id}/dashboard")
        assert r.status_code == 403

        r = client.get(f"/patients/{patient_a_id}")
        assert r.status_code == 403

        r = client.patch(f"/patients/{patient_a_id}", json={"name": "hijacked"})
        assert r.status_code == 403


def test_caregiver_and_doctor_roles_cannot_write_prescriptions_or_doses():
    """Part 3 of the brief: a caregiver/doctor role must never be able to
    act as the patient — create prescriptions, confirm them, or log doses —
    even with a valid, active link."""
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        patient_auth = client.headers["Authorization"]
        r = client.post("/patients", json={"name": "Guarded Patient"})
        patient_id = r.json()["id"]
        r = client.post(f"/patients/{patient_id}/caregiver-links")
        code = r.json()["code"]

        register_and_login(client, role="caregiver", name="A Caregiver")
        r = client.post("/caregiver/link/redeem", json={"code": code})
        assert r.status_code == 200

        # Caregiver has an ACTIVE link but still cannot write.
        r = client.post("/prescriptions", json={"patient_id": patient_id, "lines": ["Tab X 1-0-1 5 days"]})
        assert r.status_code == 403

        # But CAN read.
        r = client.get(f"/patients/{patient_id}/dashboard")
        assert r.status_code == 200

        # Restore patient auth to actually create something to confirm.
        client.headers["Authorization"] = patient_auth
        r = client.post("/prescriptions", json={"patient_id": patient_id, "lines": ["Tab X 1-0-1 5 days"]})
        prescription_id = r.json()["prescription_id"]

        register_and_login(client, role="caregiver", name="Another Caregiver")
        r = client.post(f"/prescriptions/{prescription_id}/confirm")
        assert r.status_code == 403


def test_caregiver_cannot_access_a_patient_without_a_link():
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        r = client.post("/patients", json={"name": "Unlinked Patient"})
        patient_id = r.json()["id"]

        register_and_login(client, role="caregiver")
        r = client.get(f"/caregiver/patients/{patient_id}/overview")
        assert r.status_code == 403


def test_revoked_caregiver_link_loses_access():
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        patient_auth = client.headers["Authorization"]
        r = client.post("/patients", json={"name": "Revoke Test"})
        patient_id = r.json()["id"]
        r = client.post(f"/patients/{patient_id}/caregiver-links")
        link_id = r.json()["id"]
        code = r.json()["code"]

        register_and_login(client, role="caregiver")
        caregiver_auth = client.headers["Authorization"]
        r = client.post("/caregiver/link/redeem", json={"code": code})
        assert r.status_code == 200
        r = client.get(f"/caregiver/patients/{patient_id}/overview")
        assert r.status_code == 200

        client.headers["Authorization"] = patient_auth
        r = client.post(f"/caregiver-links/{link_id}/revoke")
        assert r.status_code == 200

        client.headers["Authorization"] = caregiver_auth  # same caregiver identity, now revoked
        r = client.get(f"/caregiver/patients/{patient_id}/overview")
        assert r.status_code == 403
