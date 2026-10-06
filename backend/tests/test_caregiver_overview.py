"""What the portal's Today / History pages read from the overview: phone numbers and recent missed doses."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from db import init_db  # noqa: E402
from main import app  # noqa: E402
from test_care_notes_api import link_caregiver  # noqa: E402
from timing_helpers import add_medicine, new_patient  # noqa: E402


def test_overview_has_missed_doses_and_a_callable_emergency_contact_only_for_a_linked_caregiver():
    init_db()
    patient = TestClient(app)
    pid, _ = new_patient(patient, "Anita")
    patient.patch(f"/patients/{pid}", json={"emergency_contact": "Raj (son), +91 98765 43210"})
    soon = datetime.utcnow() - timedelta(days=1)
    add_medicine(pid, "Metformin", [soon, soon + timedelta(hours=12)], states=["missed", "taken"])
    cg, _ = link_caregiver(patient, pid)
    o = cg.get(f"/caregiver/patients/{pid}/overview").json()
    assert [m["medicine_name"] for m in o["recent_missed"]] == ["Metformin"]
    assert o["phones"]["emergency_contact"] == {"label": "Raj (son)", "phone": "+919876543210"}
    assert "patient" in o["phones"]
    stranger = TestClient(app)
    from conftest import register_and_login
    register_and_login(stranger, role="caregiver")
    assert stranger.get(f"/caregiver/patients/{pid}/overview").status_code == 403
