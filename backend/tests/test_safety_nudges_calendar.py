"""
Tests for Parts 3 (Medication Safety Center), 4 (nudges) and 8 (.ics
calendar export) of the brief — all deterministic, all reusing existing
data, no new engine.
"""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from db import SessionLocal, Dose  # noqa: E402
from conftest import register_and_login  # noqa: E402


def test_safety_center_surfaces_interactions_food_warnings_and_dosage_flag():
    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "Safety Test"})
        patient_id = r.json()["id"]
        # Telmisartan + Brufen(ibuprofen) is a known interaction pair in
        # interaction_rules.json (used by seed.py's own Ramesh persona);
        # Simvastatin has a grapefruit food warning; the third line has an
        # implausible dose amount that should be flagged, not silently used.
        client.post("/prescriptions", json={
            "patient_id": patient_id,
            "lines": [
                "Tab Telma 40mg OD x30d",
                "Tab Brufen 400mg BD x3d",
                "Tab Simvastatin 20000mg OD",
            ],
        })

        r = client.get(f"/patients/{patient_id}/safety-center")
        assert r.status_code == 200
        body = r.json()
        assert body["interactions"], "expected the telmisartan+ibuprofen interaction to surface"
        assert body["food_warnings"], "expected the simvastatin grapefruit warning to surface"
        assert any(w["severity"] == "REVIEW" for w in body["dosage_warnings"])
        assert "Deterministic" in body["disclaimer"]


def test_dashboard_nudges_flag_overdue_dose():
    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "Nudge Test"})
        patient_id = r.json()["id"]
        r = client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 650mg OD"],
        })
        prescription_id = r.json()["prescription_id"]
        client.post(f"/prescriptions/{prescription_id}/confirm")

        # Push today's dose into the past — but less than 2h, so the sweep
        # leaves it 'pending' — so the "not yet recorded" nudge fires
        # deterministically rather than depending on real wall-clock timing.
        db = SessionLocal()
        try:
            dose = db.query(Dose).filter(Dose.state == "pending").order_by(Dose.id.desc()).first()
            dose.scheduled_at = datetime.utcnow() - timedelta(minutes=30)
            db.commit()
        finally:
            db.close()

        r = client.get(f"/patients/{patient_id}/dashboard")
        assert r.status_code == 200
        nudges = r.json()["nudges"]
        assert any(n["level"] == "overdue" for n in nudges)


def test_calendar_export_returns_ics_with_events():
    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "Calendar Test"})
        patient_id = r.json()["id"]
        r = client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"],
        })
        prescription_id = r.json()["prescription_id"]
        client.post(f"/prescriptions/{prescription_id}/confirm")

        r = client.get(f"/patients/{patient_id}/calendar.ics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/calendar")
        text = r.content.decode("utf-8")
        assert text.startswith("BEGIN:VCALENDAR")
        assert "BEGIN:VEVENT" in text
        assert "Dolo" in text
        assert text.strip().endswith("END:VCALENDAR")


def test_calendar_export_requires_read_access():
    with TestClient(app) as client:
        register_and_login(client, role="patient", name="Owner")
        r = client.post("/patients", json={"name": "Private Calendar"})
        patient_id = r.json()["id"]

        register_and_login(client, role="patient", name="Someone Else")
        r = client.get(f"/patients/{patient_id}/calendar.ics")
        assert r.status_code == 403
