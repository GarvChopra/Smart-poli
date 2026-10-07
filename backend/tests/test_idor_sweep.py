"""Cross-user sweep: a logged-in stranger (patient, caregiver and doctor accounts) must never get a 2xx from ANY route that
takes someone else's patient/medicine/prescription/dose id. Walks the real route table, so a new route is covered automatically."""
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from conftest import register_and_login  # noqa: E402
from db import Medicine, SessionLocal, init_db  # noqa: E402
from main import app  # noqa: E402
from timing_helpers import add_medicine, new_patient  # noqa: E402

PARAMS = {"patient_id", "medicine_id", "prescription_id", "dose_id"}


def victim():
    init_db()
    owner = TestClient(app)
    pid, _ = new_patient(owner, "Victim")
    med, doses = add_medicine(pid, "Telma", [datetime(2030, 1, 1, 8)])
    db = SessionLocal()
    rx = db.query(Medicine).get(med).prescription_id
    db.close()
    return {"patient_id": pid, "medicine_id": med, "prescription_id": rx, "dose_id": doses[0]}


def routes_with_foreign_ids():
    out = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        names = set(re.findall(r"{(\w+)}", r.path))
        if names and names <= PARAMS:
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                out.append((m, r.path))
    return out


@pytest.mark.parametrize("role", ["patient", "caregiver", "doctor"])
def test_a_stranger_gets_no_2xx_from_any_route_with_someone_elses_ids(role):
    ids = victim()
    stranger = TestClient(app)
    register_and_login(stranger, role=role, name="Stranger")
    stranger.post("/patients", json={"name": "Mine"}) if role == "patient" else None
    leaks = []
    for method, path in routes_with_foreign_ids():
        url = path.format(**ids)
        r = stranger.request(method, url, json={} if method in ("POST", "PUT", "PATCH") else None)
        if 200 <= r.status_code < 300:
            leaks.append(f"{role}: {method} {path} -> {r.status_code}")
    assert not leaks, "\n".join(leaks)


def test_the_sweep_is_not_vacuous_the_owner_does_get_through():
    owner = TestClient(app)
    pid, _ = new_patient(owner, "Owner")
    routes = routes_with_foreign_ids()
    assert len(routes) > 30
    assert owner.get(f"/patients/{pid}").status_code == 200
