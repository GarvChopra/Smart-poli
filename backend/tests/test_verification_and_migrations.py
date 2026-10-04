"""
Per-medicine verification status, correction audit trail, startup config
guard, and that new tables arrive on an existing (older) database without
touching its data.
"""

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import db as dbmod  # noqa: E402
from db import AuditLog, SessionLocal  # noqa: E402
from main import app  # noqa: E402
from timing_helpers import new_patient  # noqa: E402
from verification import verification_status  # noqa: E402


class Med:
    def __init__(self, **kw):
        defaults = dict(status="verified", name="Dolo", field_confidence=json.dumps({"name": 1.0, "schedule": 1.0}),
                        is_prn=False, schedule_code="1-0-1", times=json.dumps(["08:00", "20:00"]), prescription=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


class Pres:
    def __init__(self, status):
        self.status = status


# ---------------------------------------------------------------- the four separate checks

def test_a_clean_line_is_not_ready_until_the_patient_confirms_it():
    v = verification_status(Med(prescription=Pres("draft")))
    assert v["transcription"] == "needs_patient_review" and v["catalogue_match"] == "exact"
    assert v["schedule"] == "ready" and v["overall"] == "needs_patient_review"


def test_confirming_the_prescription_confirms_the_transcription():
    v = verification_status(Med(prescription=Pres("confirmed")))
    assert v["transcription"] == "confirmed_transcription" and v["overall"] == "ready_for_reminder_activation"


def test_a_human_corrected_line_counts_as_confirmed():
    fc = json.dumps({"name": 1.0, "schedule": 1.0, "source": "human_confirmed"})
    assert verification_status(Med(field_confidence=fc, prescription=Pres("draft")))["transcription"] == "confirmed_transcription"


def test_an_unreadable_line_blocks_activation_and_is_not_guessed():
    v = verification_status(Med(status="needs_confirmation", schedule_code=None, times=None,
                                field_confidence=json.dumps({"name": 0.5, "schedule": 0.1}), prescription=Pres("draft")))
    assert v["transcription"] == "unreadable_needs_patient_input" and v["schedule"] == "requires_clarification"
    assert v["overall"] == "schedule_requires_clarification"


def test_a_catalogue_match_is_not_proof_of_a_correct_reading():
    # Exact name match, but the schedule is unreadable: still blocked.
    v = verification_status(Med(schedule_code=None, times=json.dumps([]), prescription=Pres("confirmed")))
    assert v["catalogue_match"] == "exact" and v["overall"] == "schedule_requires_clarification"


def test_match_levels_and_unresolved_name_blocks():
    assert verification_status(Med(field_confidence=json.dumps({"name": 0.9})))["catalogue_match"] == "approximate"
    unresolved = verification_status(Med(field_confidence=json.dumps({"name": 0.6}), prescription=Pres("confirmed")))
    assert unresolved["catalogue_match"] == "unresolved" and unresolved["overall"] == "medicine_match_unresolved"
    assert verification_status(Med(name=None))["catalogue_match"] == "unresolved"


def test_prn_medicines_need_no_fixed_times():
    v = verification_status(Med(is_prn=True, schedule_code="SOS", times=None, prescription=Pres("confirmed")))
    assert v["schedule"] == "ready"


def test_regulatory_status_is_always_separate_and_never_blocks():
    v = verification_status(Med(prescription=Pres("confirmed")))
    assert v["regulatory"] == "verification_pending" and v["overall"] == "ready_for_reminder_activation"
    assert "clinically appropriate" in v["note"]


def test_corrupt_confidence_json_does_not_crash():
    assert verification_status(Med(field_confidence="{not json"))["catalogue_match"] == "unresolved"


def test_api_includes_verification_and_it_changes_on_confirm():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        pres = client.post("/prescriptions", json={"patient_id": pid, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"]}).json()
        assert pres["medicines"][0]["verification"]["transcription"] == "needs_patient_review"
        client.post(f"/prescriptions/{pres['prescription_id']}/confirm")
        dash = client.get(f"/patients/{pid}/safety-center").json()
        assert dash["medicines"]


# ---------------------------------------------------------------- corrections are audited

def test_a_correction_records_before_after_and_a_timestamp():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        pres = client.post("/prescriptions", json={"patient_id": pid, "lines": ["Tab Dolo 650mg 1-?-1"]}).json()
        med = pres["medicines"][0]
        assert med["status"] == "needs_confirmation"
        r = client.patch(f"/medicines/{med['id']}", json={"schedule_code": "1-0-1"})
        assert r.status_code == 200 and r.json()["verification"]["transcription"] == "confirmed_transcription"
        db = SessionLocal()
        try:
            entry = db.query(AuditLog).filter_by(patient_id=pid, action="medicine_confirmed").one()
            detail = json.loads(entry.detail)
            assert detail["raw_text"] == "Tab Dolo 650mg 1-?-1"                       # the original line is preserved
            assert detail["corrections"]["schedule_code"][1] == "1-0-1"               # after
            assert detail["corrections"]["status"] == ["needs_confirmation", "verified"]
            assert detail["confirmed_at"].endswith("Z")
        finally:
            db.close()


def test_prescription_ui_shows_the_four_checks_and_the_not_clinical_note():
    js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "app.js"),
              encoding="utf-8").read()
    assert "function verificationHtml" in js and "verificationHtml(m.verification)" in js


# ---------------------------------------------------------------- startup config guard

def test_insecure_config_is_warned_and_can_be_made_fatal(monkeypatch, caplog):
    import auth
    import main
    monkeypatch.setattr(auth, "JWT_SECRET", "dev-only-insecure-secret-change-me-in-.env")
    with caplog.at_level("WARNING"):
        main._check_secure_config()
    assert "SMARTPOLI_JWT_SECRET is not set" in caplog.text
    monkeypatch.setenv("SMARTPOLI_REQUIRE_SECURE_CONFIG", "1")
    with pytest.raises(RuntimeError, match="insecure configuration"):
        main._check_secure_config()
    monkeypatch.setattr(auth, "JWT_SECRET", "a-long-random-production-secret-value-0123456789")
    monkeypatch.delenv("SMARTPOLI_SKIP_TWILIO_SIGNATURE", raising=False)
    main._check_secure_config()   # secure config starts cleanly


# ---------------------------------------------------------------- migrations

def test_new_tables_are_added_to_an_existing_database_without_touching_its_data(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    con = sqlite3.connect(tmp.name)
    # An older schema: no user_id/blood_group, no whatsapp_reminder_sent, none of the newer tables.
    con.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR UNIQUE, password_hash VARCHAR NOT NULL,
                            role VARCHAR NOT NULL, name VARCHAR NOT NULL, created_at DATETIME);
        CREATE TABLE patients (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, age INTEGER, sex VARCHAR,
                               allergies VARCHAR, emergency_contact VARCHAR, created_at DATETIME);
        CREATE TABLE prescriptions (id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL, doctor_name VARCHAR,
                               issued_date VARCHAR, source VARCHAR NOT NULL, status VARCHAR NOT NULL, created_at DATETIME);
        CREATE TABLE medicines (id INTEGER PRIMARY KEY, prescription_id INTEGER NOT NULL, raw_text TEXT NOT NULL,
                               name VARCHAR, normalized_name VARCHAR, dose_amount VARCHAR, dose_unit VARCHAR,
                               schedule_code VARCHAR, slots TEXT, times TEXT, food VARCHAR NOT NULL, duration_days INTEGER,
                               is_prn BOOLEAN NOT NULL, confidence FLOAT NOT NULL, field_confidence TEXT,
                               status VARCHAR NOT NULL, plain_language_hi TEXT);
        CREATE TABLE doses (id INTEGER PRIMARY KEY, medicine_id INTEGER NOT NULL, scheduled_at DATETIME NOT NULL,
                            state VARCHAR NOT NULL, acted_at DATETIME, reason VARCHAR, snooze_count INTEGER NOT NULL);
        INSERT INTO users VALUES (1, 'old@example.com', 'x', 'patient', 'Old User', NULL);
        INSERT INTO patients (id, name, age) VALUES (7, 'Old Patient', 61);
        INSERT INTO prescriptions VALUES (3, 7, 'Dr A', NULL, 'manual', 'confirmed', NULL);
        INSERT INTO medicines VALUES (5, 3, 'Tab Dolo 650mg', 'Dolo', 'Dolo', '650', 'mg', '1-0-1', '[]', '[]', 'any', 5, 0, 1.0, '{}', 'verified', NULL);
        INSERT INTO doses VALUES (9, 5, '2026-10-05 08:00:00', 'taken', '2026-10-05 08:05:00', NULL, 0);
    """)
    con.commit()
    con.close()

    from sqlalchemy import create_engine, inspect
    engine = create_engine(f"sqlite:///{tmp.name}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(dbmod, "DATABASE_URL", f"sqlite:///{tmp.name}")
    dbmod.init_db()
    dbmod.init_db()                       # running it twice must be harmless

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert {"patient_settings", "push_subscriptions", "reminder_log", "regulatory_lookup_cache", "revoked_tokens"} <= tables
    assert "whatsapp_reminder_sent" in {c["name"] for c in insp.get_columns("doses")}
    assert {"user_id", "blood_group"} <= {c["name"] for c in insp.get_columns("patients")}

    with engine.connect() as c:
        rows = c.exec_driver_sql("SELECT d.state, m.name, p.name, p.age FROM doses d JOIN medicines m ON m.id=d.medicine_id "
                                 "JOIN prescriptions r ON r.id=m.prescription_id JOIN patients p ON p.id=r.patient_id").fetchall()
        assert rows == [("taken", "Dolo", "Old Patient", 61)]
    engine.dispose()
    os.unlink(tmp.name)
