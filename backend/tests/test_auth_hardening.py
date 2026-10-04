"""
Session lifecycle and login hardening: logout really revokes, expiry is
enforced, brute force is throttled, unknown accounts cost the same as wrong
passwords, and a token can never grant a role the database does not give it.
"""

import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import auth  # noqa: E402
import auth_router  # noqa: E402
from db import RevokedToken, SessionLocal, User  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_throttle():
    auth_router._failed_logins.clear()
    yield
    auth_router._failed_logins.clear()


def _register(client, email, password="correct-horse-1"):
    r = client.post("/auth/register", json={"email": email, "password": password, "name": "Hard Test", "role": "patient"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_logout_revokes_the_token_server_side():
    with TestClient(app) as client:
        token = _register(client, "logout@example.com")
        assert client.get("/auth/me", headers=bearer(token)).status_code == 200
        assert client.post("/auth/logout", headers=bearer(token)).json() == {"logged_out": True}
        assert client.get("/auth/me", headers=bearer(token)).status_code == 401
        assert client.post("/auth/logout", headers=bearer(token)).status_code == 401       # already revoked
        # logging in again issues a fresh, working session
        new = client.post("/auth/login", json={"email": "logout@example.com", "password": "correct-horse-1"}).json()["token"]
        assert client.get("/auth/me", headers=bearer(new)).status_code == 200


def test_logging_out_one_device_leaves_other_sessions_alone():
    with TestClient(app) as client:
        a = _register(client, "twodev@example.com")
        b = client.post("/auth/login", json={"email": "twodev@example.com", "password": "correct-horse-1"}).json()["token"]
        client.post("/auth/logout", headers=bearer(a))
        assert client.get("/auth/me", headers=bearer(b)).status_code == 200


def test_expired_and_tampered_tokens_are_rejected():
    with TestClient(app) as client:
        token = _register(client, "expiry@example.com")
        claims = jwt.decode(token, auth.JWT_SECRET, algorithms=[auth.JWT_ALGO])
        expired = jwt.encode({**claims, "exp": datetime.utcnow() - timedelta(minutes=1)}, auth.JWT_SECRET, algorithm=auth.JWT_ALGO)
        assert client.get("/auth/me", headers=bearer(expired)).status_code == 401
        forged = jwt.encode(claims, "some-other-secret-that-is-long-enough-123", algorithm=auth.JWT_ALGO)
        assert client.get("/auth/me", headers=bearer(forged)).status_code == 401
        assert client.get("/auth/me", headers=bearer(token + "x")).status_code == 401
        assert client.get("/auth/me", headers={"Authorization": "Bearer"}).status_code == 401
        assert client.get("/auth/me").status_code == 401
        none_alg = jwt.encode(claims, None, algorithm="none")
        assert client.get("/auth/me", headers=bearer(none_alg)).status_code == 401


def test_session_expiry_is_bounded():
    assert auth.JWT_TTL_HOURS <= 24 * 7


def test_a_token_cannot_claim_a_role_the_database_did_not_grant():
    with TestClient(app) as client:
        token = _register(client, "rolefake@example.com")
        claims = jwt.decode(token, auth.JWT_SECRET, algorithms=[auth.JWT_ALGO])
        fake = jwt.encode({**claims, "role": "doctor"}, auth.JWT_SECRET, algorithm=auth.JWT_ALGO)   # validly signed, lying
        assert client.get("/auth/me", headers=bearer(fake)).json()["role"] == "patient"
        assert client.get("/doctor/patients", headers=bearer(fake)).status_code == 403
        assert client.get("/caregiver/patients", headers=bearer(fake)).status_code == 403


def test_tokens_issued_before_revocation_existed_still_work_until_they_expire():
    with TestClient(app) as client:
        token = _register(client, "legacy@example.com")
        claims = jwt.decode(token, auth.JWT_SECRET, algorithms=[auth.JWT_ALGO])
        claims.pop("jti")
        legacy = jwt.encode(claims, auth.JWT_SECRET, algorithm=auth.JWT_ALGO)
        assert client.get("/auth/me", headers=bearer(legacy)).status_code == 200


def test_revoked_rows_older_than_expiry_are_cleaned_up():
    with TestClient(app) as client:
        db = SessionLocal()
        db.add(RevokedToken(jti="stale-one", expires_at=datetime.utcnow() - timedelta(days=1)))
        db.commit()
        token = _register(client, "cleanup@example.com")
        client.post("/auth/logout", headers=bearer(token))
        assert db.query(RevokedToken).filter_by(jti="stale-one").count() == 0
        db.close()


def test_repeated_wrong_passwords_lock_the_attempt_even_if_the_next_one_is_right():
    with TestClient(app) as client:
        _register(client, "brute@example.com")
        for _ in range(auth_router.MAX_FAILED_LOGINS):
            assert client.post("/auth/login", json={"email": "brute@example.com", "password": "nope-nope-1"}).status_code == 401
        r = client.post("/auth/login", json={"email": "brute@example.com", "password": "correct-horse-1"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
        # a different account is not affected by this one's lockout
        _register(client, "other-brute@example.com")
        assert client.post("/auth/login", json={"email": "other-brute@example.com", "password": "correct-horse-1"}).status_code == 200


def test_a_successful_login_clears_the_failure_count():
    with TestClient(app) as client:
        _register(client, "clears@example.com")
        for _ in range(auth_router.MAX_FAILED_LOGINS - 1):
            client.post("/auth/login", json={"email": "clears@example.com", "password": "wrong-wrong-1"})
        assert client.post("/auth/login", json={"email": "clears@example.com", "password": "correct-horse-1"}).status_code == 200
        for _ in range(auth_router.MAX_FAILED_LOGINS - 1):
            assert client.post("/auth/login", json={"email": "clears@example.com", "password": "wrong-wrong-1"}).status_code == 401


def test_lockout_expires_after_the_window(monkeypatch):
    with TestClient(app) as client:
        _register(client, "window@example.com")
        for _ in range(auth_router.MAX_FAILED_LOGINS):
            client.post("/auth/login", json={"email": "window@example.com", "password": "wrong-wrong-1"})
        assert client.post("/auth/login", json={"email": "window@example.com", "password": "correct-horse-1"}).status_code == 429
        real = time.time
        monkeypatch.setattr(auth_router.time, "time", lambda: real() + auth_router.LOCKOUT_WINDOW_SECONDS + 5)
        assert client.post("/auth/login", json={"email": "window@example.com", "password": "correct-horse-1"}).status_code == 200


def test_unknown_email_and_wrong_password_look_and_cost_the_same(monkeypatch):
    with TestClient(app) as client:
        _register(client, "exists@example.com")
        calls = []
        real = auth_router.verify_password
        monkeypatch.setattr(auth_router, "verify_password", lambda pw, h: calls.append(h) or real(pw, h))
        a = client.post("/auth/login", json={"email": "ghost@example.com", "password": "whatever-1"})
        b = client.post("/auth/login", json={"email": "exists@example.com", "password": "whatever-2"})
        assert a.status_code == b.status_code == 401 and a.json() == b.json()
        assert len(calls) == 2 and calls[0] == auth_router._DUMMY_HASH     # the unknown account still paid for a bcrypt check


def test_phone_only_whatsapp_accounts_cannot_be_logged_into_with_a_password():
    with TestClient(app) as client:
        db = SessionLocal()
        db.add(User(name="WA", phone="+15559876540", role="patient", password_hash=None, email=None))
        db.commit()
        db.close()
        assert client.post("/auth/login", json={"email": "", "password": "anything-12"}).status_code == 401


def test_register_rejects_weak_input():
    with TestClient(app) as client:
        assert client.post("/auth/register", json={"email": "a@b.co", "password": "short", "name": "x", "role": "patient"}).status_code == 400
        assert client.post("/auth/register", json={"email": "notanemail", "password": "longenough1", "name": "x", "role": "patient"}).status_code == 400
        assert client.post("/auth/register", json={"email": "a@b.co", "password": "longenough1", "name": "x", "role": "admin"}).status_code == 400
        _register(client, "dup@example.com")
        assert client.post("/auth/register", json={"email": "DUP@example.com", "password": "longenough1", "name": "x", "role": "patient"}).status_code == 409
