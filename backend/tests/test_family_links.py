"""Patient and caregiver are relationships, not account types: one ordinary account tracks its own medicines, keeps a
profile for a family member without a phone, and looks after a family member who has their own account."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from db import init_db  # noqa: E402
from main import app  # noqa: E402
from timing_helpers import new_patient  # noqa: E402

MED = {"name": "Dolo 650", "strength": "650mg", "form": "tab", "as_needed": False, "times": ["08:00", "20:00"],
       "weekdays": None, "food": "after", "duration_days": 5}


def _two_people():
    init_db()
    son, mom = TestClient(app), TestClient(app)
    son_pid, son_user = new_patient(son, "Rahul")
    mom_pid, _ = new_patient(mom, "Mom")
    return son, son_pid, son_user, mom, mom_pid


def _link(owner, pid, other):
    code = owner.post(f"/patients/{pid}/caregiver-links").json()["code"]
    return other.post("/caregiver/link/redeem", json={"code": code})


def test_a_patient_account_can_look_after_a_family_member_and_still_track_its_own_medicines():
    son, son_pid, son_user, mom, mom_pid = _two_people()
    assert _link(mom, mom_pid, son).status_code == 200
    assert [p["id"] for p in son.get("/caregiver/patients").json()] == [mom_pid]
    assert son.get(f"/caregiver/patients/{mom_pid}/overview").status_code == 200
    added = son.post("/medicines/manual", json={**MED, "patient_id": mom_pid})
    assert added.status_code == 200 and added.json()["medicine"]["added_by"]["user_id"] == son_user["id"]
    assert son.post("/medicines/manual", json={**MED, "patient_id": son_pid}).json()["medicine"]["added_by"] is None
    assert [p["id"] for p in son.get("/patients").json()] == [son_pid]       # own profiles stay separate from the linked
    # ...and looking after Mom gives no owner rights over her record.
    assert son.post(f"/patients/{mom_pid}/caregiver-links").status_code == 403


def test_a_family_member_without_a_phone_is_a_second_profile_under_the_same_account():
    son, son_pid, son_user, _, _ = _two_people()
    dad = son.post("/patients", json={"name": "Dad"}).json()["id"]
    assert sorted(p["id"] for p in son.get("/patients").json()) == sorted([son_pid, dad])
    assert son.post("/medicines/manual", json={**MED, "patient_id": dad}).json()["medicine"]["added_by"] is None
    assert son.get("/auth/me").json()["patient_ids"] == [son_pid, dad]


def test_no_one_links_to_their_own_profile_and_strangers_stay_out():
    son, son_pid, _, mom, mom_pid = _two_people()
    assert _link(son, son_pid, son).status_code == 400
    assert son.get(f"/caregiver/patients/{mom_pid}/overview").status_code == 403
    assert son.post("/medicines/manual", json={**MED, "patient_id": mom_pid}).status_code == 403
    # the owner can't use the caregiver routes on their own profile (care-team-only notes stay hidden from them)
    assert mom.get(f"/caregiver/patients/{mom_pid}/notes").status_code == 403


def test_either_side_can_end_the_link():
    son, _, _, mom, mom_pid = _two_people()
    for leaver in ("son", "mom"):
        assert _link(mom, mom_pid, son).status_code == 200
        link = [l for l in mom.get(f"/patients/{mom_pid}/caregiver-links").json() if l["status"] == "active"][0]
        assert (son if leaver == "son" else mom).post(f"/caregiver-links/{link['id']}/revoke").status_code == 200
        assert son.get(f"/caregiver/patients/{mom_pid}/overview").status_code == 403
    stranger = TestClient(app)
    new_patient(stranger, "Stranger")
    assert stranger.post(f"/caregiver-links/{link['id']}/revoke").status_code == 403
