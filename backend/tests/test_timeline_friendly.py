"""The Timeline must read as sentences, never ids, JSON or machine codes."""
from fastapi.testclient import TestClient

from conftest import register_and_login
from db import AuditLog, Dose, SessionLocal
from main import app


def test_timeline_reads_as_plain_sentences():
    with TestClient(app) as client:
        register_and_login(client)
        pid = client.post("/patients", json={"name": "Plain Words"}).json()["id"]
        rx = client.post("/prescriptions", json={"patient_id": pid, "lines": ["Tab Metformin 500mg BD"]}).json()["prescription_id"]
        client.post(f"/prescriptions/{rx}/confirm")
        db = SessionLocal()
        try:
            dose = db.query(Dose).order_by(Dose.id.desc()).first()
            db.add_all([
                AuditLog(patient_id=pid, actor=f"patient:1", action="dose_taken", detail=f"dose {dose.id} was pending; scheduled x, recorded y"),
                AuditLog(patient_id=pid, actor="system:repair", action="dose_taken_reverted", detail="{\"dose_id\": 1}"),
                AuditLog(patient_id=pid, actor="doctor:9:Rao", action="clinical_note", detail="Take with food"),
                AuditLog(patient_id=pid, actor="seed", action="demo_seeded", detail="x"),
            ])
            db.commit()
        finally:
            db.close()
        events = client.get(f"/patients/{pid}/timeline").json()
        summaries = [e["summary"] for e in events]
        assert any(s.startswith("Took Metformin") for s in summaries), summaries
        assert "Note added" in summaries
        assert not any(a in {"dose_taken_reverted", "demo_seeded"} for a in (e["action"] for e in events))
        blob = " ".join(e["summary"] + " " + e["detail"] + " " + e["actor"] for e in events)
        assert "dose_" not in blob and "patient:" not in blob and "{" not in blob
        assert {e["actor"] for e in events} <= {"You", "Dr. Rao", "SmartPoli"}
