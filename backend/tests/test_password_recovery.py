from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from fastapi.testclient import TestClient

import auth
import google_auth
import password_recovery
from conftest import register_and_login
from db import PasswordRecovery, SessionLocal, init_db
from main import app

BASE = "/auth/password-recovery"
NEW_PASSWORD = "new-demo-password"


@pytest.fixture
def recovery():
    init_db()
    return TestClient(app)


@pytest.fixture
def outbox(monkeypatch):
    monkeypatch.setenv("SMARTPOLI_PASSWORD_RESET_ENABLED", "1")
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("SMARTPOLI_RESET_FROM_EMAIL", "security@example.test")
    monkeypatch.setenv("SMARTPOLI_RESET_BASE_URL", "https://staging.example.test")
    deliveries = []
    monkeypatch.setattr(password_recovery, "_send_reset", lambda settings, email, link: deliveries.append((email, link)))
    return deliveries


def request_link(client, email, outbox):
    response = client.post(f"{BASE}/request", json={"email": email})
    assert response.status_code == 202
    recipient, link = outbox[-1]
    assert recipient == email.lower()
    parts = urlsplit(link)
    assert (parts.scheme, parts.netloc, parts.path, parts.query) == (
        "https", "staging.example.test", "/reset-password", "",
    )
    return parse_qs(parts.fragment)["token"][0]


def test_recovery_defaults_to_off(recovery, monkeypatch):
    monkeypatch.setenv("SMARTPOLI_PASSWORD_RESET_ENABLED", "0")
    assert recovery.get(f"{BASE}/config").json() == {"enabled": False}
    assert recovery.post(f"{BASE}/request", json={"email": "nobody@example.test"}).status_code == 503
    headers = recovery.get("/reset-password").headers
    assert headers["cache-control"] == "no-store"
    assert headers["referrer-policy"] == "no-referrer"


def test_email_link_uses_configured_origin_even_when_request_arrives_via_another_host(recovery, outbox):
    user = register_and_login(recovery)
    # Staging may receive requests through an alternate proxy hostname.
    # The mail link must still point to its configured public origin.
    via_proxy = TestClient(app, base_url="https://proxy.example.test")
    via_proxy.headers["X-Forwarded-Host"] = "edge.example.test"
    request_link(via_proxy, user["email"], outbox)


def test_reset_is_single_use_and_existing_sessions_are_revoked(recovery, outbox):
    user = register_and_login(recovery, role="both")
    current_headers = {"Authorization": recovery.headers.pop("Authorization")}
    legacy_claims = auth._decode_token(current_headers["Authorization"].split()[1])
    legacy_claims.pop("rv", None)
    legacy_headers = {"Authorization": "Bearer " + jwt.encode(legacy_claims, auth.JWT_SECRET, algorithm=auth.JWT_ALGO)}
    assert recovery.get("/auth/me", headers=legacy_headers).status_code == 200

    reset = request_link(recovery, user["email"].upper(), outbox)
    known = recovery.post(f"{BASE}/request", json={"email": user["email"]})
    unknown = recovery.post(f"{BASE}/request", json={"email": "nobody@example.test"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert len(outbox) == 1
    with SessionLocal() as db:
        assert db.get(PasswordRecovery, user["id"]).token_digest == password_recovery._digest(reset)

    same_password = recovery.post(f"{BASE}/confirm", json={"token": reset, "password": "testpassword123"})
    assert same_password.status_code == 400
    assert same_password.json()["detail"] == "Choose a password different from your current one."
    assert recovery.get("/auth/me", headers=current_headers).status_code == 200

    payload = {"token": reset, "password": NEW_PASSWORD}
    assert recovery.post(f"{BASE}/confirm", json=payload).status_code == 200
    assert recovery.post(f"{BASE}/confirm", json=payload).status_code == 400
    for headers in (current_headers, legacy_headers):
        assert recovery.get("/auth/me", headers=headers).status_code == 401
        assert recovery.post("/auth/refresh", headers=headers).status_code == 401
    assert recovery.post("/auth/login", json={"email": user["email"], "password": "testpassword123"}).status_code == 401
    login = recovery.post("/auth/login", json={"email": user["email"], "password": NEW_PASSWORD})
    assert login.status_code == 200
    assert recovery.get("/auth/me", headers={"Authorization": f"Bearer {login.json()['token']}"}).status_code == 200


def test_google_account_can_reset_after_it_sets_a_smartpoli_password(recovery, outbox, monkeypatch):
    email = "recovery-google@example.test"
    monkeypatch.setattr(google_auth, "verify_id_token", lambda credential: {"email": email, "name": "Google User"})
    google_credentials = {"credential": "test-google-token"}
    google_login = recovery.post("/auth/google", json=google_credentials)
    assert google_login.status_code == 200
    account = google_login.json()["user"]
    assert not account["has_password"]

    google_only = recovery.post(f"{BASE}/request", json={"email": email})
    unknown = recovery.post(f"{BASE}/request", json={"email": "unknown@example.test"})
    assert google_only.status_code == unknown.status_code == 202
    assert google_only.json() == unknown.json()
    assert not outbox

    headers = {"Authorization": f"Bearer {google_login.json()['token']}"}
    set_password = recovery.post("/auth/set-password", headers=headers, json={"new_password": "initial-demo-password"})
    assert set_password.status_code == 200
    reset = request_link(recovery, email, outbox)
    assert recovery.post(f"{BASE}/confirm", json={"token": reset, "password": NEW_PASSWORD}).status_code == 200
    assert recovery.get("/auth/me", headers=headers).status_code == 401

    google_again = recovery.post("/auth/google", json=google_credentials)
    password_login = recovery.post("/auth/login", json={"email": email, "password": NEW_PASSWORD})
    assert google_again.status_code == password_login.status_code == 200
    assert google_again.json()["user"]["id"] == password_login.json()["user"]["id"] == account["id"]


def test_expired_link_and_validation_errors_do_not_expose_credentials(recovery, outbox):
    user = register_and_login(recovery)
    reset = request_link(recovery, user["email"], outbox)
    with SessionLocal() as db:
        db.get(PasswordRecovery, user["id"]).expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    assert recovery.post(f"{BASE}/confirm", json={"token": reset, "password": NEW_PASSWORD}).status_code == 400
    response = recovery.post(f"{BASE}/confirm", json={"token": reset, "password": "short"})
    assert response.status_code == 422
    assert reset not in response.text and "short" not in response.text
