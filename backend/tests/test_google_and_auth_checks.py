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


def test_new_person_is_signed_straight_in_with_a_regular_account():
    c = TestClient(app)
    tok = id_token("new.google.user@gmail.com")
    r = c.post("/auth/google", json={"credential": tok})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "both" and body["user"]["email"] == "new.google.user@gmail.com"
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


def test_a_google_account_has_You_and_Family_like_any_other_and_can_join_an_invite():
    """Google sign-up gives a regular account: its own profile, an invite from a family member, an Add member of its own."""
    from conftest import register_and_login
    gita = TestClient(app)
    register_and_login(gita, role="both", name="Gita")
    invite = gita.post("/family/invites", json={"nickname": "Mum"}).json()
    mum = TestClient(app)
    body = mum.post("/auth/google", json={"credential": id_token("mum.google@gmail.com")}).json()
    mum.headers["Authorization"] = f"Bearer {body['token']}"
    assert body["user"]["role"] == "both" and body["user"]["patient_ids"] == []
    assert mum.get(f"/family/join/{invite['code']}").json()["inviter_name"] == "Gita"
    joined = mum.post(f"/family/join/{invite['code']}").json()
    assert joined["joined"] and mum.get("/auth/me").json()["patient_ids"] == [joined["patient_id"]]
    assert [f["label"] for f in gita.get("/caregiver/home").json()["family"]] == ["Mum"]
    # and it can look after someone itself
    assert mum.post("/family/invites", json={}).status_code == 200
    assert mum.get("/caregiver/home").status_code == 200


def test_one_email_is_one_account_whichever_way_you_come_in():
    """Google first, then a password on the SAME account: afterwards the form and Google both open that one account."""
    c = TestClient(app)
    first = c.post("/auth/google", json={"credential": id_token("op.agent@gmail.com")}).json()
    h = {"Authorization": f"Bearer {first['token']}"}
    uid = first["user"]["id"]
    assert first["user"]["has_password"] is False
    # the form refuses a second account for that email and says why
    dup = c.post("/auth/register", json={"email": "OP.agent@gmail.com", "password": "password123", "name": "X"})
    assert dup.status_code == 409 and "Google" in dup.json()["detail"] and "Set a password" in dup.json()["detail"]
    # no password yet: the form cannot log in; setting one needs a signed-in session and a decent password
    assert c.post("/auth/login", json={"email": "op.agent@gmail.com", "password": "password123"}).status_code == 401
    assert TestClient(app).post("/auth/set-password", json={"new_password": "password123"}).status_code == 401
    assert c.post("/auth/set-password", json={"new_password": "short"}, headers=h).status_code == 400
    assert c.post("/auth/set-password", json={"new_password": "password123"}, headers=h).json() == {"ok": True, "has_password": True}
    # now both ways open the same account
    form = c.post("/auth/login", json={"email": "op.agent@gmail.com", "password": "password123"}).json()
    again = c.post("/auth/google", json={"credential": id_token("op.agent@gmail.com")}).json()
    assert form["user"]["id"] == uid == again["user"]["id"] and form["user"]["has_password"] is True
    # changing it later needs the current one
    assert c.post("/auth/set-password", json={"new_password": "another-pass1"}, headers=h).status_code == 403
    assert c.post("/auth/set-password", json={"new_password": "another-pass1", "current_password": "wrong"}, headers=h).status_code == 403
    assert c.post("/auth/set-password", json={"new_password": "another-pass1", "current_password": "password123"}, headers=h).status_code == 200
    assert c.post("/auth/login", json={"email": "op.agent@gmail.com", "password": "another-pass1"}).status_code == 200
    assert c.post("/auth/login", json={"email": "op.agent@gmail.com", "password": "password123"}).status_code == 401


def test_password_first_then_google_is_also_the_same_account():
    c = TestClient(app)
    reg = c.post("/auth/register", json={"email": "op.two@gmail.com", "password": "password123", "name": "Op"}).json()
    g = c.post("/auth/google", json={"credential": id_token("OP.Two@gmail.com")}).json()
    assert g["user"]["id"] == reg["user"]["id"] and g["user"]["has_password"] is True


def test_the_same_email_is_one_account_and_a_different_email_is_always_a_different_account():
    """Garv.Chopra@gmail.com made with the form opens with Google (capital letters do not matter); but another email -
    even one that differs only by a dot, a +tag or googlemail - is another person's account and is never merged."""
    c = TestClient(app)
    reg = c.post("/auth/register", json={"email": "Garv.Chopra@gmail.com", "password": "password123", "name": "Garv"}).json()
    uid = reg["user"]["id"]
    same = c.post("/auth/google", json={"credential": id_token("garv.chopra@gmail.com")}).json()
    assert same["user"]["id"] == uid and same["user"]["has_password"] is True
    others = ["garvchopra@gmail.com", "garv.chopra+x@gmail.com", "garv.chopra@googlemail.com", "garv.chopra@outlook.com"]
    ids = {c.post("/auth/google", json={"credential": id_token(e)}).json()["user"]["id"] for e in others}
    assert uid not in ids and len(ids) == len(others)                      # each is its own account
    # and the form does not treat them as taken either
    assert c.post("/auth/register", json={"email": "garvchopra2@gmail.com", "password": "password123", "name": "G2"}).status_code == 200
