"""tools/delete_user.py: removes one user and their data in foreign-key order, leaves everyone else alone."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import delete_user as du  # noqa: E402
from conftest import register_and_login  # noqa: E402
from db import (CareNote, Dose, Feedback, ReminderLog, SessionLocal, engine, init_db)  # noqa: E402
from main import app  # noqa: E402
from timing_helpers import add_medicine, new_patient  # noqa: E402


def make_world():
    """A patient with a medicine, doses, reminder log, notes and feedback, a linked caregiver - plus a bystander patient."""
    init_db()
    patient = TestClient(app)
    pid, user = new_patient(patient, "To Delete")
    med, doses = add_medicine(pid, "Telma", [datetime(2030, 1, 1, 8), datetime(2030, 1, 2, 8)])
    code = patient.post(f"/patients/{pid}/caregiver-links").json()["code"]
    cg = TestClient(app)
    cg_user = register_and_login(cg, role="caregiver", name="Sita")
    cg.post("/caregiver/link/redeem", json={"code": code})
    cg.post(f"/caregiver/patients/{pid}/notes", json={"kind": "medicine", "body": "with milk", "medicine_id": med})
    patient.post("/feedback", json={"rating": 4, "category": "idea", "message": "hello"})
    cg.post("/feedback", json={"rating": 5, "category": "praise", "message": "nice"})
    db = SessionLocal()
    db.add(ReminderLog(dose_id=doses[0], kind="due", channel="push"))
    db.commit()
    db.close()
    other = TestClient(app)
    other_pid, other_user = new_patient(other, "Bystander")
    add_medicine(other_pid, "Iron", [datetime(2030, 1, 1, 9)])
    return dict(pid=pid, user=user, med=med, doses=doses, cg_user=cg_user, other_pid=other_pid, other_user=other_user)


def count(table, where="1=1", **params):
    with engine.connect() as c:
        return c.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {where}"), params).scalar()


def test_deleting_a_patient_user_removes_the_whole_tree_and_nothing_else():
    w = make_world()
    with engine.begin() as conn:
        deleted = du.delete_user(conn, w["user"]["id"])          # SQLite enforces foreign keys inside, so a wrong order would raise
    assert deleted["users"] == 1 and deleted["patients"] == 1 and deleted["medicines"] == 1 and deleted["doses"] == 2
    assert deleted["reminder_log"] == 1 and deleted["care_notes"] == 1
    assert count("users", "id = :i", i=w["user"]["id"]) == 0
    assert count("patients", "id = :i", i=w["pid"]) == 0 and count("medicines", "id = :i", i=w["med"]) == 0
    assert count("feedback", "user_id = :i", i=w["user"]["id"]) == 0
    # the caregiver's own account survives (they only lose the link to this patient); the bystander is untouched
    assert count("users", "id = :i", i=w["cg_user"]["id"]) == 1
    assert count("feedback", "user_id = :i", i=w["cg_user"]["id"]) == 1
    assert count("caregiver_links", "patient_id = :i", i=w["pid"]) == 0
    assert count("patients", "id = :i", i=w["other_pid"]) == 1 and count("medicines", "prescription_id IN (SELECT id FROM prescriptions WHERE patient_id = :i)", i=w["other_pid"]) == 1
    assert count("users", "id = :i", i=w["other_user"]["id"]) == 1


def test_deleting_a_caregiver_removes_their_notes_and_links_but_not_the_patient():
    w = make_world()
    with engine.begin() as conn:
        du.delete_user(conn, w["cg_user"]["id"])
    assert count("users", "id = :i", i=w["cg_user"]["id"]) == 0
    assert count("care_notes", "author_user_id = :i", i=w["cg_user"]["id"]) == 0
    assert count("caregiver_links", "caregiver_user_id = :i", i=w["cg_user"]["id"]) == 0
    assert count("patients", "id = :i", i=w["pid"]) == 1 and count("medicines", "id = :i", i=w["med"]) == 1   # the patient keeps everything


def test_a_failure_halfway_leaves_everything_in_place():
    w = make_world()
    with pytest.raises(Exception):
        with engine.begin() as conn:
            du.delete_user(conn, w["user"]["id"])
            raise RuntimeError("simulate a crash after the deletes")
    assert count("users", "id = :i", i=w["user"]["id"]) == 1 and count("doses", "medicine_id = :i", i=w["med"]) == 2


def test_the_dry_run_counts_without_changing_anything():
    w = make_world()
    with engine.connect() as conn:
        plan = dict(du.counts(conn, w["user"]["id"]))
        who = du.describe_user(conn, w["user"]["id"])
    assert plan["patients"] == 1 and plan["doses"] == 2 and who.role == "patient"
    assert count("users", "id = :i", i=w["user"]["id"]) == 1


def test_the_printed_sql_is_one_transaction_and_runs_cleanly():
    w = make_world()
    script = du.sql_script(w["user"]["id"])
    assert script.count("BEGIN;") == 1 and script.rstrip().endswith("COMMIT;") and ":u" not in script
    assert script.index("DELETE FROM reminder_log") < script.index("DELETE FROM doses") < script.index("DELETE FROM medicines") \
        < script.index("DELETE FROM prescriptions") < script.index("DELETE FROM patients") < script.index("DELETE FROM users")
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys = ON")
        for stmt in script.splitlines():
            if stmt.startswith("DELETE"):
                conn.exec_driver_sql(stmt.rstrip(";"))
    assert count("users", "id = :i", i=w["user"]["id"]) == 0


def test_every_table_that_points_at_users_or_patients_has_a_delete_step():
    """If someone adds a new table with a foreign key to users/patients/prescriptions/medicines/doses, this fails until the tool knows it."""
    import db
    steps = {t for t, _ in du.STEPS}
    for table in db.Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name in ("users", "patients", "prescriptions", "medicines", "doses"):
                assert table.name in steps, f"{table.name} points at {fk.column.table.name} but delete_user.py has no step for it"


def test_apply_needs_the_matching_host(monkeypatch, capsys):
    w = make_world()
    monkeypatch.setattr(sys, "argv", ["delete_user.py", "--user-id", str(w["user"]["id"]), "--apply"])
    assert du.main() == 2                                                                 # no --confirm-host
    monkeypatch.setattr(sys, "argv", ["delete_user.py", "--user-id", str(w["user"]["id"]), "--apply", "--confirm-host", "not-this-host"])
    assert du.main() == 2
    assert count("users", "id = :i", i=w["user"]["id"]) == 1
    monkeypatch.setattr(sys, "argv", ["delete_user.py", "--user-id", "999999"])
    assert du.main() == 1                                                                 # unknown user
