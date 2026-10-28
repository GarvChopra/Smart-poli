"""No one is ever connected to someone else's record by accident.

This walks EVERY route the app has (so a route added later is covered without anyone remembering to add it here) with
four different callers against one person's data (Anita's):
  - nobody signed in            -> every route that needs an account refuses with 401
  - a stranger with an account  -> never a 2xx on Anita's ids
  - a caregiver linked to Anita -> may do the caregiver things only; never acts AS Anita
  - the same caregiver after Anita removed them -> refused everywhere again
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from conftest import register_and_login  # noqa: E402
from db import init_db  # noqa: E402
from main import app  # noqa: E402

MED = {"name": "Metformin", "strength": "500mg", "form": "tab", "times": ["08:00", "20:00"], "food": "any", "duration_days": 5}

# Routes that are public on purpose (they carry their own secret, or return nothing personal).
PUBLIC_BY_DESIGN = {
    "/emergency/{token}", "/emergency/{token}/photo",          # the printed emergency card: the token IS the key
    "/triage/symptoms/{symptom_id}/questions",                 # the fixed question list
    "/join/{code}",                                            # an HTML page; the code is only used after sign-in
}


def _deps(route):
    names = []

    def walk(d):
        for s in d.dependencies:
            names.append(getattr(s.call, "__name__", str(s.call)))
            walk(s)
    walk(route.dependant)
    return set(names)


def _routes():
    for r in app.routes:
        if isinstance(r, APIRoute) and not r.path.startswith("/static"):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield r, m


@pytest.fixture(scope="module")
def world():
    init_db()
    anita = TestClient(app)
    register_and_login(anita, role="both", name="Anita")
    pid = anita.post("/patients", json={"name": "Anita"}).json()["id"]
    made = anita.post("/medicines/manual", json={**MED, "patient_id": pid}).json()
    med = made["medicine"]["id"]
    dose = anita.get(f"/patients/{pid}/dashboard").json()["upcoming_doses"][0]["id"]
    code = anita.post(f"/patients/{pid}/caregiver-links").json()["code"]

    gita = TestClient(app)
    register_and_login(gita, role="both", name="Gita")
    assert gita.post("/caregiver/link/redeem", json={"code": code}).status_code == 200
    link = [l for l in anita.get(f"/patients/{pid}/caregiver-links").json() if l["status"] == "active"][0]["id"]
    note = gita.post(f"/caregiver/patients/{pid}/notes", json={"kind": "message", "body": "hello"}).json()["id"]
    invite = anita.post("/family/invites", json={"nickname": "X"}).json()["id"]

    stranger = TestClient(app)
    register_and_login(stranger, role="both", name="Stranger")
    stranger.post("/patients", json={"name": "Stranger"})
    # Never sign-in a client by default: copy the headers so each call states who it is.
    return {
        "anita": anita, "gita": gita, "stranger": stranger,
        "ids": {"patient_id": pid, "medicine_id": med, "dose_id": dose, "prescription_id": made["prescription_id"],
                "link_id": link, "note_id": note, "invite_id": invite, "template_id": 1},
        "token": {n: c.headers["Authorization"] for n, c in (("anita", anita), ("gita", gita), ("stranger", stranger))},
    }


def _call(world, who, route, method):
    """One request to this route as `who` (None = nobody) against Anita's ids."""
    ids = world["ids"]
    path = route.path
    for name in re.findall(r"\{(\w+)\}", path):
        if name not in ids:
            return None                                          # a code / token route: covered by its own tests
        path = path.replace("{" + name + "}", str(ids[name]))
    headers = {"Authorization": world["token"][who]} if who else {}
    client = TestClient(app)
    kw = {"headers": headers}
    if method in ("POST", "PUT", "PATCH"):
        kw["json"] = _BODIES.get((method, route.path), {})
    return client.request(method, path, **kw)


_PID = None
_BODIES = {
    ("POST", "/patients/{patient_id}/notes"): {"note": "x"},
    ("PUT", "/patients/{patient_id}/settings"): {"timezone": "UTC"},
    ("PATCH", "/patients/{patient_id}"): {"name": "Hacked"},
    ("POST", "/caregiver/patients/{patient_id}/notes"): {"kind": "message", "body": "x"},
    ("PUT", "/caregiver/patients/{patient_id}/prefs"): {"notify_missed": False},
    ("PATCH", "/caregiver/patients/{patient_id}/nickname"): {"nickname": "Hacked"},
    ("PATCH", "/medicines/{medicine_id}"): {"name": "Hacked"},
    ("POST", "/medicines/{medicine_id}/shift"): {"minutes": 30},
    ("POST", "/doses/{dose_id}/skip"): {"reason": "x"},
    ("POST", "/doses/{dose_id}/snooze"): {},
    ("POST", "/doses/{dose_id}/reschedule"): {"new_time": "2030-01-01T09:00"},
    ("PUT", "/patients/{patient_id}/routine"): {"morning": "08:00"},
    ("PUT", "/patients/{patient_id}/emergency-card/profile"): {"blood_group": "O+"},
    ("POST", "/patients/{patient_id}/push/subscribe"): {"endpoint": "https://push.example/x", "keys": {"p256dh": "k" * 20, "auth": "a" * 12}},
    ("POST", "/patients/{patient_id}/push/unsubscribe"): {"endpoint": "https://push.example/x"},
}

_OK = {200, 201, 202, 204}


def test_every_route_that_needs_an_account_refuses_nobody(world):
    checked = 0
    for route, method in _routes():
        if route.path in PUBLIC_BY_DESIGN or "get_current_user" not in _deps(route):
            continue
        r = _call(world, None, route, method) if "{" not in route.path or all(
            n in world["ids"] for n in re.findall(r"\{(\w+)\}", route.path)) else None
        if r is None:
            r = TestClient(app).request(method, re.sub(r"\{\w+\}", "ZZZZ", route.path), json={} if method in ("POST", "PUT", "PATCH") else None)
        assert r.status_code == 401, f"{method} {route.path} answered {r.status_code} with nobody signed in"
        checked += 1
    assert checked > 60


def test_a_stranger_gets_nothing_from_anitas_ids_on_any_route(world):
    checked, inconclusive = 0, []
    for route, method in _routes():
        if route.path in PUBLIC_BY_DESIGN:
            continue
        ids_in_path = re.findall(r"\{(\w+)\}", route.path)
        if not ids_in_path or any(n not in world["ids"] for n in ids_in_path):
            continue
        r = _call(world, "stranger", route, method)
        assert r.status_code not in _OK, f"STRANGER got {r.status_code} from {method} {route.path}: {r.text[:200]}"
        checked += 1
        if r.status_code == 422:
            inconclusive.append(f"{method} {route.path}")
    assert checked > 60
    assert not inconclusive, f"these answered 422 (body rejected before the access check could be proven): {inconclusive}"


# What a linked caregiver may do on Anita's record - and nothing else.
CAREGIVER_MAY = {
    ("GET", "/caregiver/patients/{patient_id}/overview"), ("GET", "/caregiver/patients/{patient_id}/notes"),
    ("POST", "/caregiver/patients/{patient_id}/notes"), ("POST", "/caregiver/patients/{patient_id}/nudge"),
    ("GET", "/caregiver/patients/{patient_id}/prefs"), ("PUT", "/caregiver/patients/{patient_id}/prefs"),
    ("PATCH", "/caregiver/patients/{patient_id}/nickname"), ("DELETE", "/caregiver/notes/{note_id}"),
    ("POST", "/caregiver/patients/{patient_id}/unlink"),
    # looking (read-only) at the same screens the patient sees
    ("GET", "/patients/{patient_id}"), ("GET", "/patients/{patient_id}/dashboard"),
    ("GET", "/patients/{patient_id}/calendar.ics"), ("GET", "/patients/{patient_id}/emergency-card"),
    ("GET", "/patients/{patient_id}/emergency-card/card.pdf"), ("GET", "/patients/{patient_id}/emergency-card/photo"),
    ("GET", "/patients/{patient_id}/emergency-card/profile"), ("GET", "/patients/{patient_id}/emergency-card/qr.png"),
    ("GET", "/patients/{patient_id}/food-warnings"), ("GET", "/patients/{patient_id}/interactions"),
    ("GET", "/patients/{patient_id}/medicine-info"), ("GET", "/patients/{patient_id}/notes"),
    ("GET", "/patients/{patient_id}/open-warnings"), ("GET", "/patients/{patient_id}/pair-check"),
    ("GET", "/patients/{patient_id}/regulatory"), ("GET", "/patients/{patient_id}/report"),
    ("GET", "/patients/{patient_id}/report/pdf"), ("GET", "/patients/{patient_id}/routine"),
    ("GET", "/patients/{patient_id}/safety-center"), ("GET", "/patients/{patient_id}/schedule-conflicts"),
    ("GET", "/patients/{patient_id}/settings"), ("GET", "/patients/{patient_id}/timeline"),
    ("GET", "/doses/{dose_id}/missed-guidance"),
}


def test_a_linked_caregiver_does_only_caregiver_things_and_never_acts_as_the_patient(world):
    checked = 0
    for route, method in _routes():
        if route.path in PUBLIC_BY_DESIGN:
            continue
        ids_in_path = re.findall(r"\{(\w+)\}", route.path)
        if not ids_in_path or any(n not in world["ids"] for n in ids_in_path):
            continue
        if (method, route.path) in CAREGIVER_MAY:
            continue
        # a fresh copy of the world for each state-changing probe is not needed: everything here must be REFUSED
        r = _call(world, "gita", route, method)
        assert r.status_code not in _OK, f"CAREGIVER got {r.status_code} from {method} {route.path}: {r.text[:200]}"
        checked += 1
    assert checked > 30


def test_the_caregiver_cannot_take_remove_or_rewrite_what_the_patient_owns(world):
    gita, anita = world["gita"], world["anita"]
    ids = world["ids"]
    pid = ids["patient_id"]
    assert gita.post(f"/doses/{ids['dose_id']}/take").status_code == 403
    assert gita.delete(f"/medicines/{ids['medicine_id']}").status_code == 403          # Anita's own medicine, not Gita's
    assert gita.patch(f"/patients/{pid}", json={"name": "Hacked"}).status_code == 403
    assert gita.post(f"/caregiver-links/{ids['link_id']}/revoke").status_code == 403
    assert gita.post(f"/patients/{pid}/caregiver-links").status_code == 403           # cannot mint more access
    assert anita.get(f"/patients/{pid}").json()["name"] == "Anita"


def test_nobody_but_the_owner_can_use_anitas_invites(world):
    stranger = world["stranger"]
    assert stranger.post(f"/family/invites/{world['ids']['invite_id']}/cancel").status_code == 404
    assert world["gita"].post(f"/family/invites/{world['ids']['invite_id']}/cancel").status_code == 404
    assert world["stranger"].get("/family/invites").json() == []


def test_posting_anitas_patient_id_in_a_body_is_refused_for_a_stranger(world):
    pid = world["ids"]["patient_id"]
    s = world["stranger"]
    assert s.post("/medicines/manual", json={**MED, "patient_id": pid}).status_code == 403
    assert s.post("/prescriptions", json={"patient_id": pid, "lines": ["Tab Paracetamol 650mg 1-0-1"]}).status_code in (403, 404, 422)
    assert s.post("/triage/check", json={"patient_id": pid, "symptoms": ["fever"], "answers": {}}).status_code in (403, 404, 422)
    assert s.post("/medicines/scan", data={"patient_id": pid}, files={"file": ("a.png", b"x", "image/png")}).status_code == 403
    assert s.post("/medicines/from-pdf", data={"patient_id": pid}, files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")}).status_code == 403
    assert s.post("/prescriptions/from-image", data={"patient_id": pid}, files={"file": ("a.png", b"x", "image/png")}).status_code == 403
    # Anita's data never changed
    assert len(world["anita"].get(f"/patients/{pid}/dashboard").json()["upcoming_doses"]) > 0


def test_once_anita_removes_the_caregiver_everything_is_refused_again(world):
    anita, gita, ids = world["anita"], world["gita"], world["ids"]
    pid = ids["patient_id"]
    assert gita.get(f"/caregiver/patients/{pid}/overview").status_code == 200
    assert anita.post(f"/caregiver-links/{ids['link_id']}/revoke").status_code == 200
    for route, method in _routes():
        if route.path in PUBLIC_BY_DESIGN:
            continue
        ids_in_path = re.findall(r"\{(\w+)\}", route.path)
        if not ids_in_path or any(n not in world["ids"] for n in ids_in_path):
            continue
        if route.path in ("/caregiver/notes/{note_id}",):        # the note's own author: checked for the link on use
            continue
        r = _call(world, "gita", route, method)
        assert r.status_code not in _OK, f"REMOVED CAREGIVER got {r.status_code} from {method} {route.path}"
    assert gita.get("/caregiver/home").json()["family"] == []
