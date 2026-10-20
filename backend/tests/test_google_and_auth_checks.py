"""Continue with Google (server-verified ID token), doctor sign-up closed, password limits, login rate limit."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt  # noqa: E402
import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import google_auth  # noqa: E402
from main import app  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "static"
CLIENT_ID = "1234-test.apps.googleusercontent.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def google_on(monkeypatch):
    from db import init_db
    init_db()
    monkeypatch.setenv("SMARTPOLI_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(google_auth, "_signing_key", lambda token: KEY.public_key())


def id_token(email, key=KEY, **over):
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "g-" + email, "email": email,
              "email_verified": True, "name": "Asha Verma", "iat": now, "exp": now + 3600}
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256")


def test_config_shows_button_only_when_client_id_set(monkeypatch):
    c = TestClient(app)
    assert c.get("/auth/google/config").json() == {"enabled": True, "client_id": CLIENT_ID}
    monkeypatch.delenv("SMARTPOLI_GOOGLE_CLIENT_ID")
    assert c.get("/auth/google/config").json() == {"enabled": False, "client_id": None}
    assert c.post("/auth/google", json={"credential": id_token("x@gmail.com")}).status_code == 401


def test_new_person_picks_a_role_then_gets_a_working_session():
    c = TestClient(app)
    tok = id_token("new.google.user@gmail.com")
    first = c.post("/auth/google", json={"credential": tok})
    assert first.status_code == 200 and first.json() == {"needs_role": True, "name": "Asha Verma"}
    r = c.post("/auth/google", json={"credential": tok, "role": "patient"})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "patient" and body["user"]["email"] == "new.google.user@gmail.com"
    me = c.get("/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    # next time: straight in, same account
    again = c.post("/auth/google", json={"credential": id_token("new.google.user@gmail.com")}).json()
    assert again["user"]["id"] == body["user"]["id"]
    # a Google-made account has no password, so the password form can never open it
    assert c.post("/auth/login", json={"email": "new.google.user@gmail.com", "password": "anything123"}).status_code == 401


def test_existing_password_account_is_opened_by_the_same_google_email():
    c = TestClient(app)
    reg = c.post("/auth/register", json={"email": "Both.Ways@Gmail.com", "password": "password123", "name": "B", "role": "caregiver"})
    assert reg.status_code == 200
    r = c.post("/auth/google", json={"credential": id_token("both.ways@gmail.com")})
    assert r.status_code == 200 and r.json()["user"]["id"] == reg.json()["user"]["id"]
    assert r.json()["user"]["role"] == "caregiver"


@pytest.mark.parametrize("bad", [
    {"key": OTHER_KEY},                                   # not signed by Google
    {"aud": "someone-elses-app.apps.googleusercontent.com"},
    {"iss": "https://evil.example"},
    {"exp": int(time.time()) - 3600},
    {"email_verified": False},
])
def test_forged_or_foreign_tokens_are_refused(bad):
    key = bad.pop("key", KEY)
    r = TestClient(app).post("/auth/google", json={"credential": id_token("victim@gmail.com", key=key, **bad), "role": "patient"})
    assert r.status_code == 401


def test_google_cannot_create_a_doctor_while_doctor_signup_is_closed(monkeypatch):
    monkeypatch.delenv("SMARTPOLI_DOCTOR_ROLE", raising=False)
    r = TestClient(app).post("/auth/google", json={"credential": id_token("doc.google@gmail.com"), "role": "doctor"})
    assert r.status_code == 400


def test_doctor_signup_is_closed_and_not_offered(monkeypatch):
    monkeypatch.delenv("SMARTPOLI_DOCTOR_ROLE", raising=False)
    r = TestClient(app).post("/auth/register", json={"email": "doc.closed@example.com", "password": "password123", "name": "D", "role": "doctor"})
    assert r.status_code == 400 and "not open" in r.json()["detail"]
    assert 'value="doctor"' not in (STATIC / "login.html").read_text(encoding="utf-8")


def test_password_limits_never_crash_or_truncate():
    c = TestClient(app)
    r = c.post("/auth/register", json={"email": "longpw@example.com", "password": "x" * 73, "name": "L", "role": "patient"})
    assert r.status_code == 400
    r = c.post("/auth/register", json={"email": "hugepw@example.com", "password": "x" * 5000, "name": "L", "role": "patient"})
    assert r.status_code == 422
    assert c.post("/auth/login", json={"email": "a@b.c", "password": "x" * 5000}).status_code == 422


def test_login_page_allows_google_button_but_other_pages_stay_strict():
    c = TestClient(app)
    login = c.get("/login").headers
    assert "https://accounts.google.com/gsi/client" in login["content-security-policy"]
    assert "frame-src https://accounts.google.com/gsi/" in login["content-security-policy"]
    home = c.get("/").headers
    assert "accounts.google.com" not in home["content-security-policy"]
    assert home["referrer-policy"] == "no-referrer" and home["cross-origin-opener-policy"] == "same-origin"


def test_login_is_rate_limited_per_device(monkeypatch):
    import web_security
    monkeypatch.delenv("SMARTPOLI_DISABLE_RATE_LIMIT", raising=False)
    web_security._hits.clear()
    c = TestClient(app)
    codes = [c.post("/auth/login", json={"email": f"spray{i}@example.com", "password": "wrongpass1"}).status_code
             for i in range(35)]
    web_security._hits.clear()
    assert 429 in codes and codes[0] == 401
