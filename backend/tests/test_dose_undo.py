import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from db import SessionLocal, Dose  # noqa: E402
from conftest import register_and_login  # noqa: E402


def _patient_with_dose(client):
    register_and_login(client)
    pid = client.post("/patients", json={"name": "Undo Test"}).json()["id"]
    pres = client.post("/prescriptions", json={"patient_id": pid, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"]}).json()
    client.post(f"/prescriptions/{pres['prescription_id']}/confirm")
    dose_id = client.get(f"/patients/{pid}/dashboard").json()["upcoming_doses"][0]["id"]
    return pid, dose_id


def test_undo_recent_take_returns_dose_to_pending():
    with TestClient(app) as client:
        pid, dose_id = _patient_with_dose(client)
        assert client.post(f"/doses/{dose_id}/take").json()["state"] == "taken"
        r = client.post(f"/doses/{dose_id}/undo")
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "pending"
        assert r.json()["acted_at"] is None


def test_undo_refused_after_ten_minutes():
    with TestClient(app) as client:
        pid, dose_id = _patient_with_dose(client)
        client.post(f"/doses/{dose_id}/take")
        db = SessionLocal()
        try:
            dose = db.query(Dose).get(dose_id)
            dose.acted_at = datetime.utcnow() - timedelta(minutes=11)
            db.commit()
        finally:
            db.close()
        assert client.post(f"/doses/{dose_id}/undo").status_code == 400


def test_undo_refused_for_dose_not_taken():
    with TestClient(app) as client:
        pid, dose_id = _patient_with_dose(client)
        assert client.post(f"/doses/{dose_id}/undo").status_code == 400


def test_undo_refused_for_other_user():
    with TestClient(app) as owner:
        pid, dose_id = _patient_with_dose(owner)
        owner.post(f"/doses/{dose_id}/take")
    with TestClient(app) as other:
        register_and_login(other)
        assert other.post(f"/doses/{dose_id}/undo").status_code == 403
