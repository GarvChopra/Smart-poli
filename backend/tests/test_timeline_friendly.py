"""The Timeline shows only: medicine, time, and taken / not taken. No ids, JSON, routine changes or bookkeeping."""
from fastapi.testclient import TestClient

from conftest import register_and_login
from db import AuditLog, Dose, SessionLocal
from main import app


def test_timeline_is_only_dose_outcomes():
    with TestClient(app) as client:
        register_and_login(client)
        pid = client.post("/patients", json={"name": "Plain Words"}).json()["id"]
        rx = client.post("/prescriptions", json={"patient_id": pid, "lines": ["Tab Metformin 500mg BD"]}).json()["prescription_id"]
        client.post(f"/prescriptions/{rx}/confirm")
        db = SessionLocal()
        try:
            dose = db.query(Dose).order_by(Dose.id.desc()).first()
            db.add_all([
                AuditLog(patient_id=pid, actor="patient:1", action="dose_taken", detail=f"dose {dose.id} was pending; scheduled x"),
                AuditLog(patient_id=pid, actor="system", action="dose_auto_missed", detail=f"dose {dose.id}"),
                AuditLog(patient_id=pid, actor="patient:1", action="routine_updated", detail='{"times": {}}'),
                AuditLog(patient_id=pid, actor="doctor:9:Rao", action="clinical_note", detail="Take with food"),
                AuditLog(patient_id=pid, actor="seed", action="demo_seeded", detail="x"),
            ])
            db.commit()
        finally:
            db.close()
        events = client.get(f"/patients/{pid}/timeline").json()
        assert {e["action"] for e in events} == {"dose_taken", "dose_auto_missed"}
        assert {e["status"] for e in events} == {"taken", "missed"}
        assert all(e["summary"] == "Metformin" and e["detail"] == "" and e["actor"] == "" for e in events)
