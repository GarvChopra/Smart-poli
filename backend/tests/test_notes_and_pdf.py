import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from conftest import register_and_login  # noqa: E402


def test_clinical_notes_require_a_real_linked_doctor_and_reuse_audit_log():
    """
    Notes used to accept `actor: "doctor"` straight from the request body —
    exactly the trust-the-frontend hole the brief calls out. Now a note can
    only be added by an authenticated doctor account holding an ACTIVE
    DoctorLink to that patient; the actor recorded is the doctor's real
    identity, never a client-supplied string.

    One TestClient/app lifecycle is reused for both identities (patient and
    doctor) by swapping the Authorization header between calls — two
    concurrent TestClient(app) context managers would each re-run FastAPI's
    startup event (including starting the module-level APScheduler
    instance twice), which is what a second lifecycle would collide on.
    """
    with TestClient(app) as client:
        register_and_login(client, role="patient", name="Notes Test Patient")
        patient_auth = client.headers["Authorization"]
        r = client.post("/patients", json={"name": "Notes Test"})
        patient_id = r.json()["id"]

        # A doctor with no link at all is refused.
        register_and_login(client, role="doctor", name="Dr. Unlinked")
        doctor_auth = client.headers["Authorization"]
        r = client.post(f"/patients/{patient_id}/notes", json={"note": "should be refused"})
        assert r.status_code == 403

        # Patient issues an invite code and the doctor redeems it.
        client.headers["Authorization"] = patient_auth
        r = client.post(f"/patients/{patient_id}/doctor-links")
        assert r.status_code == 200
        code = r.json()["code"]

        client.headers["Authorization"] = doctor_auth
        r = client.post("/doctor/link/redeem", json={"code": code})
        assert r.status_code == 200

        # Now the same doctor can add a note.
        r = client.post(f"/patients/{patient_id}/notes",
                         json={"note": "Reduce dose if BP drops below 110/70."})
        assert r.status_code == 200

        client.headers["Authorization"] = patient_auth
        r = client.get(f"/patients/{patient_id}/notes")
        assert r.status_code == 200
        notes = r.json()
        assert len(notes) == 1
        assert notes[0]["actor"].startswith("doctor:")
        assert "Dr. Unlinked" in notes[0]["actor"]

        r = client.get(f"/patients/{patient_id}/report")
        assert any(n["note"].startswith("Reduce dose") for n in r.json()["doctor_caregiver_notes"])


def test_notes_reject_non_doctor_roles():
    """A caregiver — even one with an active link — cannot add a clinical
    note; only a doctor role can (Part 8 of the brief)."""
    with TestClient(app) as client:
        register_and_login(client, role="patient")
        patient_auth = client.headers["Authorization"]
        r = client.post("/patients", json={"name": "Bad Actor Test"})
        patient_id = r.json()["id"]
        r = client.post(f"/patients/{patient_id}/caregiver-links")
        code = r.json()["code"]

        register_and_login(client, role="caregiver", name="A Caregiver")
        r = client.post("/caregiver/link/redeem", json={"code": code})
        assert r.status_code == 200

        r = client.post(f"/patients/{patient_id}/notes", json={"note": "hi"})
        assert r.status_code == 403


def test_pdf_report_downloads_as_real_pdf():
    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "PDF Test", "age": 40})
        patient_id = r.json()["id"]
        client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"],
        })

        r = client.get(f"/patients/{patient_id}/report/pdf")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content[:5] == b"%PDF-"
        assert "attachment" in r.headers["content-disposition"]
