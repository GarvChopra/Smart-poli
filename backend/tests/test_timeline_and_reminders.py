import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from db import SessionLocal, Dose  # noqa: E402
from scheduler import sweep_missed  # noqa: E402
from conftest import register_and_login  # noqa: E402


def test_timeline_shows_events_in_reverse_chronological_order_with_summaries():
    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "Timeline Test"})
        patient_id = r.json()["id"]

        client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"],
        })
        client.post("/triage/check", json={
            "patient_id": patient_id, "symptom_ids": ["fever"], "answers": {},
        })

        r = client.get(f"/patients/{patient_id}/timeline")
        assert r.status_code == 200
        events = r.json()
        actions = [e["action"] for e in events]
        assert "patient_created" in actions
        assert "prescription_created" in actions
        assert "triage_check" in actions
        # reverse chronological: most recent (triage_check) comes before patient_created
        assert actions.index("triage_check") < actions.index("patient_created")
        assert all(e["summary"] for e in events)


def test_sweep_missed_logs_an_audit_entry_per_auto_missed_dose():
    with TestClient(app) as client:
        register_and_login(client)
        auth_header = client.headers["Authorization"]  # reused below — same account, new client
        r = client.post("/patients", json={"name": "Sweep Test"})
        patient_id = r.json()["id"]
        r = client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 650mg OD"],
        })
        prescription_id = r.json()["prescription_id"]
        client.post(f"/prescriptions/{prescription_id}/confirm")

    # Force the dose's scheduled_at far into the past so the sweep catches it.
    db = SessionLocal()
    try:
        dose = db.query(Dose).filter(Dose.state == "pending").order_by(Dose.id.desc()).first()
        dose.scheduled_at = datetime.utcnow() - timedelta(hours=5)
        db.commit()
        count = sweep_missed(db)
        assert count >= 1
    finally:
        db.close()

    with TestClient(app) as client:
        client.headers["Authorization"] = auth_header
        r = client.get(f"/patients/{patient_id}/timeline")
        actions = [e["action"] for e in r.json()]
        assert "dose_auto_missed" in actions
