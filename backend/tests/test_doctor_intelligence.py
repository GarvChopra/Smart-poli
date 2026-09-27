"""
Tests for doctor_intelligence.py — the priority queue, since-last-visit
comparison, and pre-consultation brief shared by the doctor and caregiver
dashboards. The one invariant that must hold, mirroring triage.py's
escalate-only rule: a patient's priority level only ever goes up as more
reasons are found, never down.
"""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from db import SessionLocal, Dose, Medicine, Prescription, SymptomCheck  # noqa: E402
from conftest import register_and_login  # noqa: E402


def _link_doctor(client, patient_id):
    r = client.post(f"/patients/{patient_id}/doctor-links")
    code = r.json()["code"]
    register_and_login(client, role="doctor", name="Dr. Priority")
    client.post("/doctor/link/redeem", json={"code": code})


def test_priority_routine_by_default():
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        r = client.post("/patients", json={"name": "Quiet Patient"})
        patient_id = r.json()["id"]
        _link_doctor(client, patient_id)

        r = client.get("/doctor/patients")
        assert r.json()[0]["priority"] == {"level": "routine", "reasons": []}


def test_priority_escalates_to_high_for_unconfirmed_prescription():
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        r = client.post("/patients", json={"name": "Unconfirmed Patient"})
        patient_id = r.json()["id"]
        # A malformed schedule code forces needs_confirmation (confidence 0).
        client.post("/prescriptions", json={"patient_id": patient_id, "lines": ["Tab Cefixime 1-?-1"]})
        _link_doctor(client, patient_id)

        r = client.get("/doctor/patients")
        priority = r.json()[0]["priority"]
        assert priority["level"] == "high"
        assert any("awaiting verification" in reason for reason in priority["reasons"])


def test_priority_missed_doses_medium_below_three_high_at_three():
    """One or two missed doses is common and shouldn't flood the queue with
    "high" patients — only 3+ in 7 days earns that. 1-2 is still surfaced,
    just at "medium", so it's never silently dropped."""
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        patient_auth = client.headers["Authorization"]
        r = client.post("/patients", json={"name": "Missed Dose Patient"})
        patient_id = r.json()["id"]
        r = client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 650mg TDS x5d"],
        })
        prescription_id = r.json()["prescription_id"]
        client.post(f"/prescriptions/{prescription_id}/confirm")

        db = SessionLocal()
        try:
            doses = (
                db.query(Dose)
                .join(Medicine, Dose.medicine_id == Medicine.id)
                .join(Prescription, Medicine.prescription_id == Prescription.id)
                .filter(Prescription.patient_id == patient_id)
                .order_by(Dose.id)
                .all()
            )
            for d in doses:
                d.scheduled_at = datetime.utcnow() - timedelta(days=1)
            db.commit()
            dose_ids = [d.id for d in doses]
        finally:
            db.close()

        client.post(f"/doses/{dose_ids[0]}/miss")
        client.post(f"/doses/{dose_ids[1]}/miss")
        _link_doctor(client, patient_id)
        doctor_auth = client.headers["Authorization"]
        r = client.get("/doctor/patients")
        assert r.json()[0]["priority"]["level"] == "medium"

        client.headers["Authorization"] = patient_auth
        client.post(f"/doses/{dose_ids[2]}/miss")
        client.headers["Authorization"] = doctor_auth
        r = client.get("/doctor/patients")
        assert r.json()[0]["priority"]["level"] == "high"


def test_priority_escalates_to_medium_for_recent_moderate_triage():
    """A MODERATE-graded check is meant to route to a routine consultation
    (triage.py's own action text) — it should surface on the queue, not
    disappear into "routine" priority just because nothing else is wrong."""
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        r = client.post("/patients", json={"name": "Moderate Check Patient"})
        patient_id = r.json()["id"]
        r = client.post("/triage/check", json={
            "patient_id": patient_id, "symptom_ids": ["headache"],
            "answers": {"vision_changes": True},
        })
        assert r.json()["severity"] == "MODERATE"

        _link_doctor(client, patient_id)
        r = client.get("/doctor/patients")
        priority = r.json()[0]["priority"]
        assert priority["level"] == "medium"
        assert any("MODERATE" in reason for reason in priority["reasons"])


def test_priority_never_downgrades_once_emergency():
    """Escalate-only: an EMERGENCY triage plus an otherwise-clean record
    must still report "emergency", the maximum level, not something lower."""
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        r = client.post("/patients", json={"name": "Emergency Patient"})
        patient_id = r.json()["id"]

        r = client.post("/triage/check", json={
            "patient_id": patient_id, "symptom_ids": ["chest_pain"],
            "answers": {"difficulty_breathing": True},
        })
        assert r.json()["severity"] == "EMERGENCY"

        _link_doctor(client, patient_id)
        r = client.get("/doctor/patients")
        priority = r.json()[0]["priority"]
        assert priority["level"] == "emergency"
        assert any("EMERGENCY" in reason for reason in priority["reasons"])


def test_doctor_patients_sorted_emergency_first():
    with TestClient(app) as client:
        register_and_login(client, role="patient", name="Calm Owner")
        r = client.post("/patients", json={"name": "Calm Patient"})
        calm_id = r.json()["id"]
        calm_link = client.post(f"/patients/{calm_id}/doctor-links").json()["code"]

        register_and_login(client, role="patient", name="Urgent Owner")
        r = client.post("/patients", json={"name": "Urgent Patient"})
        urgent_id = r.json()["id"]
        client.post("/triage/check", json={
            "patient_id": urgent_id, "symptom_ids": ["chest_pain"],
            "answers": {"difficulty_breathing": True},
        })
        urgent_link = client.post(f"/patients/{urgent_id}/doctor-links").json()["code"]

        register_and_login(client, role="doctor", name="Dr. Sort")
        client.post("/doctor/link/redeem", json={"code": calm_link})
        client.post("/doctor/link/redeem", json={"code": urgent_link})

        r = client.get("/doctor/patients")
        ids_in_order = [p["id"] for p in r.json()]
        assert ids_in_order.index(urgent_id) < ids_in_order.index(calm_id)


def test_since_last_visit_is_none_before_any_note_or_correction():
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        r = client.post("/patients", json={"name": "First Time Patient"})
        patient_id = r.json()["id"]
        _link_doctor(client, patient_id)

        r = client.get(f"/doctor/patients/{patient_id}")
        assert r.json()["since_last_visit"] is None


def test_since_last_visit_reflects_changes_after_a_note():
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        patient_auth = client.headers["Authorization"]
        r = client.post("/patients", json={"name": "Returning Patient"})
        patient_id = r.json()["id"]
        r = client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"],
        })
        prescription_id = r.json()["prescription_id"]
        client.post(f"/prescriptions/{prescription_id}/confirm")
        _link_doctor(client, patient_id)
        doctor_auth = client.headers["Authorization"]

        note_response = client.post(f"/patients/{patient_id}/notes", json={"note": "First review, stable."})
        assert note_response.status_code == 200

        # Move every dose's schedule to just after "now" so it counts as
        # having happened since the note, then mark one missed.
        db = SessionLocal()
        try:
            doses = (
                db.query(Dose)
                .join(Medicine, Dose.medicine_id == Medicine.id)
                .join(Prescription, Medicine.prescription_id == Prescription.id)
                .filter(Prescription.patient_id == patient_id)
                .all()
            )
            for d in doses:
                d.scheduled_at = datetime.utcnow() + timedelta(seconds=1)
            db.commit()
            first_dose_id = doses[0].id
        finally:
            db.close()

        import time
        time.sleep(1.2)
        client.headers["Authorization"] = patient_auth
        miss_response = client.post(f"/doses/{first_dose_id}/miss")
        assert miss_response.status_code == 200, miss_response.text

        client.headers["Authorization"] = doctor_auth
        r = client.get(f"/doctor/patients/{patient_id}")
        since = r.json()["since_last_visit"]
        assert since is not None
        labels = [c["label"] for c in since["changes"]]
        assert "Doses missed since last visit" in labels


def test_doctor_brief_reports_current_medicine_and_allergy_data():
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        r = client.post("/patients", json={"name": "Brief Patient"})
        patient_id = r.json()["id"]
        client.patch(f"/patients/{patient_id}", json={"allergies": "Penicillin"})
        client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"],
        })
        _link_doctor(client, patient_id)

        r = client.get(f"/doctor/patients/{patient_id}")
        brief = r.json()["brief"]
        assert brief["current_medicine_count"] == 1
        assert brief["allergies"] == "Penicillin"
        assert brief["pending_verification_count"] == 0


def test_caregiver_patients_also_include_priority():
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        r = client.post("/patients", json={"name": "Caregiver Watched Patient"})
        patient_id = r.json()["id"]
        code = client.post(f"/patients/{patient_id}/caregiver-links").json()["code"]

        register_and_login(client, role="caregiver", name="Watchful Caregiver")
        client.post("/caregiver/link/redeem", json={"code": code})

        r = client.get("/caregiver/patients")
        assert r.json()[0]["priority"] == {"level": "routine", "reasons": []}
