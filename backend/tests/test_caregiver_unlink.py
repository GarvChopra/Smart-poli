"""A caregiver can stop looking after someone: only their own link ends."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import care_push  # noqa: E402
from conftest import register_and_login  # noqa: E402
from db import init_db  # noqa: E402
from main import app  # noqa: E402
from timing_helpers import new_patient  # noqa: E402


def _world():
    init_db()
    mum = TestClient(app)
    pid, _ = new_patient(mum, "Sunita")
    mum.post("/medicines/manual", json={"patient_id": pid, "name": "Amlodipine", "strength": "5mg", "form": "tab",
                                         "times": ["08:00"], "food": "any", "duration_days": 3})

    def caregiver(name):
        c = TestClient(app)
        register_and_login(c, role="both", name=name)
        code = mum.post(f"/patients/{pid}/caregiver-links").json()["code"]
        assert c.post("/caregiver/link/redeem", json={"code": code, "nickname": "Mum"}).status_code == 200
        return c
    return mum, pid, caregiver("Gita"), caregiver("Ravi")


def test_a_caregiver_removes_themselves_and_only_themselves(monkeypatch):
    told = []
    monkeypatch.setattr(care_push, "push_to_patient", lambda db, pid, title, body, tag, **kw: told.append(title) or 1)
    mum, pid, gita, ravi = _world()
    assert [f["label"] for f in gita.get("/caregiver/home").json()["family"]] == ["Mum"]
    assert gita.post(f"/caregiver/patients/{pid}/unlink").json() == {"removed": True}
    # Gita no longer sees Mum anywhere, and cannot do any caregiver thing for her
    assert gita.get("/caregiver/home").json()["family"] == []
    assert gita.get(f"/caregiver/patients/{pid}/overview").status_code == 403
    assert gita.post(f"/caregiver/patients/{pid}/nudge").status_code == 403
    assert gita.post("/medicines/manual", json={"patient_id": pid, "name": "X1", "strength": "1mg", "form": "tab", "times": ["10:00"],
                                                  "food": "any", "duration_days": 1}).status_code == 403
    assert gita.post(f"/caregiver/patients/{pid}/unlink").status_code == 403           # nothing left to remove
    # Ravi, the other caregiver, and Mum's own data are untouched; Mum is told
    assert ravi.get(f"/caregiver/patients/{pid}/overview").status_code == 200
    assert "Amlodipine" in mum.get(f"/patients/{pid}/dashboard").text
    assert [l["status"] for l in mum.get(f"/patients/{pid}/caregiver-links").json() if l["caregiver_name"] == "Gita"] == ["revoked"]
    assert told.count("Gita is no longer your caregiver") == 1 and not any("Ravi" in t and "no longer" in t for t in told)


def test_nobody_can_remove_someone_elses_link_or_a_person_they_never_looked_after():
    mum, pid, gita, ravi = _world()
    stranger = TestClient(app)
    register_and_login(stranger, role="both", name="Stranger")
    assert stranger.post(f"/caregiver/patients/{pid}/unlink").status_code == 403
    assert TestClient(app).post(f"/caregiver/patients/{pid}/unlink").status_code == 401
    assert mum.post(f"/caregiver/patients/{pid}/unlink").status_code == 403           # the patient uses Care team > Remove instead
    assert gita.get(f"/caregiver/patients/{pid}/overview").status_code == 200 and ravi.get(f"/caregiver/patients/{pid}/overview").status_code == 200


def test_a_removed_caregiver_can_be_added_again_with_a_new_code():
    mum, pid, gita, ravi = _world()
    gita.post(f"/caregiver/patients/{pid}/unlink")
    code = mum.post(f"/patients/{pid}/caregiver-links").json()["code"]
    assert gita.post("/caregiver/link/redeem", json={"code": code, "nickname": "Mum"}).status_code == 200
    assert gita.get(f"/caregiver/patients/{pid}/overview").status_code == 200
    inv = gita.post("/family/invites", json={}).json()                                 # and via Add member the other way round
    assert inv["status"] == "pending"
