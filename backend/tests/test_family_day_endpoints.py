"""The two read endpoints behind the Family redesign: one day's doses, and adherence over 1 / 7 / 30 days."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from db import init_db  # noqa: E402
from main import app  # noqa: E402
from test_care_notes_api import link_caregiver  # noqa: E402
from timing_helpers import add_medicine, new_patient  # noqa: E402

MIDNIGHT = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


def _world():
    init_db()
    patient = TestClient(app)
    pid, _ = new_patient(patient, "Anita")
    cg, _ = link_caregiver(patient, pid, "Sita")
    return pid, patient, cg


def test_doses_for_a_day_are_that_days_only_and_both_the_person_and_a_caregiver_can_read_them():
    pid, patient, cg = _world()
    add_medicine(pid, "Amlodipine", [MIDNIGHT + timedelta(minutes=5), MIDNIGHT - timedelta(days=1) + timedelta(minutes=5)],
                 states=["taken", "missed"])
    today = patient.get(f"/patients/{pid}/doses").json()
    assert today["date"] == today["today"] == MIDNIGHT.date().isoformat()
    assert [(d["medicine_name"], d["state"]) for d in today["doses"]] == [("Amlodipine", "taken")]
    yesterday = cg.get(f"/patients/{pid}/doses", params={"date": (MIDNIGHT - timedelta(days=1)).date().isoformat()}).json()
    assert [d["state"] for d in yesterday["doses"]] == ["missed"]
    assert {"id", "scheduled_at", "medicine_name", "food", "slot"} <= set(yesterday["doses"][0])


def test_doses_skip_removed_medicines_and_refuse_a_bad_date_and_a_stranger():
    pid, patient, cg = _world()
    mid, _ = add_medicine(pid, "Gone", [MIDNIGHT + timedelta(minutes=5)], states=["taken"])
    add_medicine(pid, "Kept", [MIDNIGHT + timedelta(minutes=6)], states=["taken"])
    assert patient.delete(f"/medicines/{mid}").status_code == 200
    assert [d["medicine_name"] for d in patient.get(f"/patients/{pid}/doses").json()["doses"]] == ["Kept"]
    assert patient.get(f"/patients/{pid}/doses", params={"date": "yesterday"}).status_code == 422
    assert patient.get(f"/patients/{pid}/doses", params={"date": "9999-12-31"}).status_code == 422   # was a 500
    stranger = TestClient(app)
    new_patient(stranger, "Someone")
    assert stranger.get(f"/patients/{pid}/doses").status_code == 403


def test_adherence_counts_per_medicine_and_the_strip_marks_each_day():
    pid, patient, cg = _world()
    add_medicine(pid, "Metformin", [MIDNIGHT + timedelta(minutes=5), MIDNIGHT - timedelta(days=1) + timedelta(minutes=5),
                                    MIDNIGHT - timedelta(days=3) + timedelta(minutes=5), MIDNIGHT - timedelta(days=20) + timedelta(minutes=5)],
                 states=["taken", "missed", "taken", "missed"])
    one = cg.get(f"/caregiver/patients/{pid}/adherence", params={"days": 1}).json()
    assert one["summary"]["taken"] == 1 and one["summary"]["missed"] == 0 and one["summary"]["adherence_percent"] == 100.0
    week = cg.get(f"/caregiver/patients/{pid}/adherence", params={"days": 7}).json()
    assert (week["summary"]["taken"], week["summary"]["missed"]) == (2, 1)
    assert week["per_medicine"][0]["name"] == "Metformin" and week["per_medicine"][0]["adherence_percent"] == 66.7
    states = [d["state"] for d in week["daily"]]
    assert len(states) == 7 and states[-1] == "taken" and states[-2] == "missed" and states[-4] == "taken" and states[0] == "none"
    assert week["per_medicine"][0]["daily"] == week["daily"]
    month = cg.get(f"/caregiver/patients/{pid}/adherence", params={"days": 30}).json()
    assert (month["summary"]["taken"], month["summary"]["missed"]) == (2, 2)


def test_adherence_needs_a_link_and_a_valid_window():
    pid, patient, cg = _world()
    assert cg.get(f"/caregiver/patients/{pid}/adherence", params={"days": 14}).status_code == 422
    stranger = TestClient(app)
    new_patient(stranger, "Someone")
    assert stranger.get(f"/caregiver/patients/{pid}/adherence").status_code in (401, 403)
    other = TestClient(app)
    from conftest import register_and_login
    register_and_login(other, role="caregiver", name="Nobody")
    assert other.get(f"/caregiver/patients/{pid}/adherence").status_code == 403


def test_calendar_returns_one_month_of_doses_for_a_linked_caregiver_only_and_refuses_a_bad_month():
    pid, patient, cg = _world()
    add_medicine(pid, "Amlodipine", [MIDNIGHT + timedelta(minutes=5), MIDNIGHT - timedelta(days=40) + timedelta(minutes=5)],
                 states=["taken", "missed"])
    month = MIDNIGHT.strftime("%Y-%m")
    r = cg.get(f"/caregiver/patients/{pid}/calendar", params={"month": month})
    assert r.status_code == 200
    body = r.json()
    assert body["month"] == month and all(d["scheduled_at"].startswith(month) for d in body["doses"])
    assert any(d["state"] == "taken" and d["medicine_name"] == "Amlodipine" for d in body["doses"])
    assert cg.get(f"/caregiver/patients/{pid}/calendar", params={"month": "soon"}).status_code == 422
    assert cg.get(f"/caregiver/patients/{pid}/calendar", params={"month": "2099-01"}).status_code == 422
    assert TestClient(app).get(f"/caregiver/patients/{pid}/calendar").status_code in (401, 403)
