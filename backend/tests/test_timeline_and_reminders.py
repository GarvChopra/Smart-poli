import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from db import SessionLocal, Dose  # noqa: E402
from scheduler import sweep_missed  # noqa: E402
from conftest import register_and_login  # noqa: E402


def test_timeline_is_empty_until_a_dose_is_acted_on():
    with TestClient(app) as client:
        register_and_login(client)
        patient_id = client.post("/patients", json={"name": "Timeline Test"}).json()["id"]
        client.post("/prescriptions", json={"patient_id": patient_id, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"]})
        r = client.get(f"/patients/{patient_id}/timeline")
        assert r.status_code == 200 and r.json() == []     # profile/prescription events are not timeline rows


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
