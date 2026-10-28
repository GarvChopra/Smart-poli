"""One account is one person: their own medicines ("You") plus the family they look after. The account label never grants
access to anyone else's record - only an active link does - and a link never lets the caregiver act AS the patient."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from conftest import register_and_login  # noqa: E402
from db import init_db  # noqa: E402
from main import app  # noqa: E402
from timing_helpers import new_patient  # noqa: E402

MED = {"name": "Metformin", "strength": "500mg", "form": "tab", "times": ["08:00", "20:00"], "food": "any", "duration_days": 5}


def _person(role, name):
    c = TestClient(app)
    user = register_and_login(c, role=role, name=name)
    return c, user


@pytest.fixture()
def world():
    init_db()
    mum = TestClient(app)
    mum_pid, _ = new_patient(mum, "Mum")
    mum.post("/medicines/manual", json={**MED, "patient_id": mum_pid})
    return mum, mum_pid


@pytest.mark.parametrize("role", ["both", "patient", "caregiver"])
def test_every_person_account_has_its_own_you_profile(role):
    init_db()
    c, user = _person(role, "Gita")
    r = c.post("/patients", json={"name": "Gita"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert c.get("/auth/me").json().get("patient_ids") == [pid]
    assert c.post("/medicines/manual", json={**MED, "patient_id": pid}).status_code == 200
    assert c.get(f"/patients/{pid}/dashboard").status_code == 200
    assert [p["id"] for p in c.get("/patients").json()] == [pid]


@pytest.mark.parametrize("role", ["both", "patient", "caregiver"])
def test_no_link_means_no_access_whatever_the_account_label(world, role):
    mum, mum_pid = world
    stranger, _ = _person(role, "Stranger")
    assert stranger.get(f"/patients/{mum_pid}").status_code == 403
    assert stranger.get(f"/patients/{mum_pid}/dashboard").status_code == 403
    assert stranger.get(f"/caregiver/patients/{mum_pid}/overview").status_code == 403
    assert stranger.post(f"/caregiver/patients/{mum_pid}/notes", json={"kind": "message", "body": "hi"}).status_code == 403
    assert stranger.post(f"/caregiver/patients/{mum_pid}/nudge").status_code == 403
    assert stranger.post("/medicines/manual", json={**MED, "patient_id": mum_pid}).status_code == 403
    assert stranger.get("/caregiver/home").json()["family"] == []


def test_a_linked_person_looks_after_mum_but_never_acts_as_mum(world):
    mum, mum_pid = world
    gita, gita_user = _person("both", "Gita")
    gita_pid = gita.post("/patients", json={"name": "Gita"}).json()["id"]
    code = mum.post(f"/patients/{mum_pid}/caregiver-links").json()["code"]
    assert gita.post("/caregiver/link/redeem", json={"code": code, "nickname": "Mum"}).status_code == 200

    # sees Mum through the family screens ...
    assert gita.get(f"/caregiver/patients/{mum_pid}/overview").status_code == 200
    assert gita.get(f"/patients/{mum_pid}/dashboard").status_code == 200
    # ... may add a medicine for her (labelled "Added by Gita") ...
    r = gita.post("/medicines/manual", json={**MED, "name": "Telmisartan", "patient_id": mum_pid})
    assert r.status_code == 200 and r.json()["medicine"]["added_by"]["name"] == "Gita"
    # ... but cannot log Mum's doses, rewrite her record, or revoke her own access
    mum_dose = mum.get(f"/patients/{mum_pid}/dashboard").json()["upcoming_doses"][0]["id"]
    assert gita.post(f"/doses/{mum_dose}/take").status_code == 403
    assert gita.patch(f"/patients/{mum_pid}", json={"name": "Hacked"}).status_code == 403
    link = [l for l in mum.get(f"/patients/{mum_pid}/caregiver-links").json() if l["status"] == "active"][0]
    assert gita.post(f"/caregiver-links/{link['id']}/revoke").status_code == 403
    assert mum.get(f"/patients/{mum_pid}").json()["name"] == "Mum"
    # Gita's own record stays Gita's: Mum has no way in
    assert mum.get(f"/patients/{gita_pid}").status_code == 403
    assert mum.get(f"/caregiver/patients/{gita_pid}/overview").status_code == 403


def test_a_person_cannot_use_the_caregiver_screens_on_their_own_record():
    init_db()
    c, _ = _person("both", "Gita")
    pid = c.post("/patients", json={"name": "Gita"}).json()["id"]
    assert c.get(f"/caregiver/patients/{pid}/overview").status_code == 403
    assert c.post(f"/caregiver/patients/{pid}/notes", json={"kind": "message", "body": "x"}).status_code == 403


def test_notes_written_about_family_are_labelled_caregiver_even_from_a_patient_label_account(world):
    mum, mum_pid = world
    gita, _ = _person("patient", "Gita")
    code = mum.post(f"/patients/{mum_pid}/caregiver-links").json()["code"]
    gita.post("/caregiver/link/redeem", json={"code": code})
    note = gita.post(f"/caregiver/patients/{mum_pid}/notes", json={"kind": "handover", "body": "Check BP"}).json()
    assert note["author_role"] == "caregiver"
    # a care-team-only note stays hidden from the patient (the label could have exposed it as the patient's own)
    notes = mum.get(f"/patients/{mum_pid}/notes").json() if mum.get(f"/patients/{mum_pid}/notes").status_code == 200 else None
    if notes is not None:
        assert all(n["id"] != note["id"] for n in (notes["notes"] if isinstance(notes, dict) else notes))


def test_new_accounts_default_to_both_and_a_doctor_label_is_still_refused_when_the_role_is_off():
    init_db()
    c = TestClient(app)
    r = c.post("/auth/register", json={"email": "newperson@example.test", "password": "testpassword123", "name": "New"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"] == "both" and r.json()["user"]["patient_ids"] == []
    assert c.post("/auth/register", json={"email": "bad@example.test", "password": "testpassword123", "name": "X", "role": "admin"}).status_code == 400
