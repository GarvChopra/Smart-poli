"""Adding a medicine like setting an alarm: times, repeat days, duration - scheduled straight away, no confirm step."""
from datetime import datetime

from fastapi.testclient import TestClient

from db import Dose, SessionLocal
from main import app
from timing_helpers import new_patient


def _post(client, pid, **kw):
    body = {"patient_id": pid, "name": "Telma 40", "strength": "40mg", "form": "tab", "times": ["08:00", "20:00"],
            "food": "after", "duration_days": 7}
    body.update(kw)
    return client.post("/medicines/manual", json=body)


def _doses(mid):
    db = SessionLocal()
    try:
        return sorted(d.scheduled_at for d in db.query(Dose).filter(Dose.medicine_id == mid).all())
    finally:
        db.close()


def test_added_and_scheduled_in_one_step_with_exact_times():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        r = _post(client, pid)
        assert r.status_code == 200, r.text
        body = r.json()
        med = body["medicine"]
        assert med["name"] == "Telma 40" and med["status"] == "verified" and med["times"] == ["08:00", "20:00"]
        assert body["doses_scheduled"] >= 1 and body["first_dose"]
        assert {d.strftime("%H:%M") for d in _doses(med["id"])} <= {"08:00", "20:00"}
        # it is on the dashboard without any confirm call
        assert client.get(f"/patients/{pid}/dashboard").json()["upcoming_doses"]


def test_weekly_days_only_schedule_those_weekdays():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        r = _post(client, pid, weekdays=[0, 2, 4], times=["09:00"], duration_days=21)        # Mon, Wed, Fri
        assert r.status_code == 200, r.text
        days = {d.weekday() for d in _doses(r.json()["medicine"]["id"])}
        assert days == {0, 2, 4}
        daily = _post(client, pid, name="Iron", weekdays=[0, 1, 2, 3, 4, 5, 6], times=["09:00"], duration_days=10)
        assert len({d.weekday() for d in _doses(daily.json()["medicine"]["id"])}) == 7        # all seven = every day


def test_as_needed_medicine_has_no_doses_and_no_times_needed():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        r = _post(client, pid, name="Paracetamol", as_needed=True, times=[], duration_days=None)
        assert r.status_code == 200 and r.json()["medicine"]["is_prn"] is True and r.json()["doses_scheduled"] == 0


def test_bad_input_is_refused_not_guessed():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        assert _post(client, pid, times=[]).status_code == 422                      # no time chosen
        assert _post(client, pid, weekdays=[9]).status_code == 422                  # a "days" list with no valid day
        assert _post(client, pid, name="x").status_code == 422
        assert _post(client, pid, times=["25:99"]).status_code == 422               # no valid time left
        assert _post(client, pid, duration_days=0).status_code == 422


def test_only_the_patient_can_add_a_medicine():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
    with TestClient(app) as stranger:
        from conftest import register_and_login
        register_and_login(stranger)
        assert _post(stranger, pid).status_code == 403
    assert TestClient(app).post("/medicines/manual", json={"patient_id": pid, "name": "Telma"}).status_code == 401
