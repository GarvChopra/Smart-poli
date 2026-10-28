"""An existing (older) database upgrades in place without losing anything, and no API answer carries secrets."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402

import db as dbmod  # noqa: E402
from conftest import register_and_login  # noqa: E402
from db import init_db  # noqa: E402
from main import app  # noqa: E402
from timing_helpers import new_patient  # noqa: E402


def test_an_older_database_gets_the_new_column_and_table_and_keeps_every_row(tmp_path, monkeypatch):
    """The live database predates caregiver_links.nickname and family_invites: start-up must add them, in place, with every
    existing row untouched."""
    url = f"sqlite:///{tmp_path / 'old.db'}"
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR, phone VARCHAR, password_hash VARCHAR, role VARCHAR NOT NULL, name VARCHAR NOT NULL, created_at DATETIME)"))
        c.execute(text("CREATE TABLE patients (id INTEGER PRIMARY KEY, user_id INTEGER, name VARCHAR, age INTEGER, sex VARCHAR, blood_group VARCHAR, allergies VARCHAR, emergency_contact VARCHAR, created_at DATETIME)"))
        c.execute(text("CREATE TABLE caregiver_links (id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL, caregiver_user_id INTEGER, code VARCHAR NOT NULL UNIQUE, status VARCHAR NOT NULL, created_at DATETIME, accepted_at DATETIME)"))
        c.execute(text("INSERT INTO users VALUES (1,'a@x.test',NULL,'h','patient','Anita',NULL),(2,'b@x.test',NULL,'h','caregiver','Sita',NULL)"))
        c.execute(text("INSERT INTO patients (id,user_id,name) VALUES (1,1,'Anita')"))
        c.execute(text("INSERT INTO caregiver_links VALUES (1,1,2,'OLDCODE1','active',NULL,NULL)"))
    monkeypatch.setattr(dbmod, "ddl_engine", eng)
    monkeypatch.setattr(dbmod, "DATABASE_URL", url)
    dbmod._add_missing_columns()
    dbmod.Base.metadata.create_all(bind=eng)
    cols = {c["name"] for c in inspect(eng).get_columns("caregiver_links")}
    assert "nickname" in cols and "family_invites" in inspect(eng).get_table_names()
    with eng.connect() as c:
        assert c.execute(text("SELECT code, status, caregiver_user_id, nickname FROM caregiver_links")).all() == [("OLDCODE1", "active", 2, None)]
        assert c.execute(text("SELECT COUNT(*) FROM users")).scalar() == 2
    dbmod._add_missing_columns()                                      # running it again is harmless
    assert "nickname" in {c["name"] for c in inspect(eng).get_columns("caregiver_links")}


SECRET_WORDS = ("password_hash", "p256dh", '"auth":', "jti", "JWT", "$2b$", "SMARTPOLI_")


def test_no_answer_to_a_signed_in_person_carries_a_secret_or_someone_elses_account_details():
    init_db()
    anita = TestClient(app)
    register_and_login(anita, role="both", name="Anita")
    pid = anita.post("/patients", json={"name": "Anita"}).json()["id"]
    anita.post("/medicines/manual", json={"patient_id": pid, "name": "Metformin", "strength": "500mg", "form": "tab",
                                           "times": ["08:00", "20:00"], "food": "any", "duration_days": 3})
    anita.post(f"/patients/{pid}/push/subscribe", json={"endpoint": "https://push.example/anita-secret", "keys": {"p256dh": "k" * 20, "auth": "a" * 12}})
    code = anita.post(f"/patients/{pid}/caregiver-links").json()["code"]
    gita = TestClient(app)
    gita_user = register_and_login(gita, role="both", name="Gita")
    gita.post("/caregiver/link/redeem", json={"code": code})
    anita_email = anita.get("/auth/me").json()["email"]

    seen = []
    paths = [f"/caregiver/patients/{pid}/overview", f"/caregiver/patients/{pid}/notes", "/caregiver/home", "/caregiver/patients",
             f"/patients/{pid}", f"/patients/{pid}/dashboard", f"/patients/{pid}/report", f"/patients/{pid}/safety-center",
             f"/patients/{pid}/timeline", f"/patients/{pid}/emergency-card", f"/patients/{pid}/settings", "/auth/me", "/family/invites"]
    for path in paths:
        r = gita.get(path)
        assert r.status_code in (200, 403), (path, r.status_code)
        seen.append((path, r.text))
    # what Anita's own screens may say about herself is fine; what a caregiver reads must not expose secrets or
    # the account details (email) of the person being looked after, nor Anita's phone's push address
    for path, body in seen:
        for word in SECRET_WORDS:
            assert word not in body, f"{path} contains {word!r}"
        assert "anita-secret" not in body, path
        if path != "/auth/me":
            assert anita_email not in body, f"{path} exposes the patient's login email"
    # a person's own session answer: no password hash, no other accounts
    me = gita.get("/auth/me").json()
    assert set(me) <= {"id", "email", "name", "role", "patient_ids"} and me["id"] == gita_user["id"]
    # Anita's links list shows the caregiver's name but never their email or id
    links = anita.get(f"/patients/{pid}/caregiver-links").json()
    assert all(set(l) <= {"id", "code", "status", "caregiver_name", "created_at", "accepted_at"} for l in links)
