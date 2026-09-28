import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from db import SessionLocal  # noqa: E402
from conftest import register_and_login  # noqa: E402
import emergency_tokens  # noqa: E402
from serializers import emergency_card_data  # noqa: E402


def _make_patient(client, name="Token Test"):
    return client.post("/patients", json={"name": name}).json()["id"]


def test_token_is_stable_until_rotated():
    with TestClient(app) as client:
        register_and_login(client)
        pid = _make_patient(client)
    db = SessionLocal()
    try:
        t1 = emergency_tokens.get_or_create_active_token(db, pid)
        t2 = emergency_tokens.get_or_create_active_token(db, pid)
        assert t1 == t2
        assert len(t1) >= 20
        assert str(pid) != t1
        assert emergency_tokens.patient_id_for_token(db, t1) == pid

        t3 = emergency_tokens.rotate_token(db, pid)
        assert t3 != t1
        assert emergency_tokens.patient_id_for_token(db, t1) is None
        assert emergency_tokens.patient_id_for_token(db, t3) == pid
        assert emergency_tokens.get_or_create_active_token(db, pid) == t3
    finally:
        db.close()


def test_unknown_token_returns_none():
    db = SessionLocal()
    try:
        assert emergency_tokens.patient_id_for_token(db, "does-not-exist") is None
        assert emergency_tokens.patient_id_for_token(db, "") is None
    finally:
        db.close()


def test_two_profiles_have_independent_tokens():
    with TestClient(app) as client:
        register_and_login(client)
        a = _make_patient(client, "Profile A")
        b = _make_patient(client, "Profile B")
    db = SessionLocal()
    try:
        ta = emergency_tokens.get_or_create_active_token(db, a)
        tb = emergency_tokens.get_or_create_active_token(db, b)
        assert ta != tb
        emergency_tokens.rotate_token(db, a)
        assert emergency_tokens.patient_id_for_token(db, tb) == b
    finally:
        db.close()


def test_card_data_has_path_and_last_updated_and_is_idempotent():
    with TestClient(app) as client:
        register_and_login(client)
        pid = _make_patient(client)
        db = SessionLocal()
        try:
            d1 = emergency_card_data(db, pid)
            d2 = emergency_card_data(db, pid)
            assert d1["card_path"] == d2["card_path"]
            assert d1["card_path"].startswith("/emergency/")
            assert d1["card_path"] != f"/emergency/{pid}"
            first_updated = d1["last_updated"]
        finally:
            db.close()

        time.sleep(0.01)
        client.patch(f"/patients/{pid}", json={"allergies": "Sulfa"})
        db = SessionLocal()
        try:
            assert emergency_card_data(db, pid)["last_updated"] > first_updated
        finally:
            db.close()
