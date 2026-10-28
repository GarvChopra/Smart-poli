"""Remember me (a longer session, kept alive while the app is used) and Delete my account (proves who you are first, removes
only your own data)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import auth  # noqa: E402
from conftest import register_and_login  # noqa: E402
from db import SessionLocal, User, init_db  # noqa: E402
from main import app  # noqa: E402


def _claims(token):
    return jwt.decode(token, auth.JWT_SECRET, algorithms=[auth.JWT_ALGO])


def test_remember_me_gives_a_month_and_not_ticked_gives_a_day():
    init_db()
    c = TestClient(app)
    base = {"email": f"rem{time.time_ns()}@x.test", "password": "testpassword123", "name": "R"}
    long = c.post("/auth/register", json={**base, "remember": True}).json()["token"]
    hours = lambda t: (_claims(t)["exp"] - _claims(t)["iat"]) / 3600  # noqa: E731
    assert round(hours(long)) == 24 * 30 and _claims(long)["rem"] is True
    short = c.post("/auth/login", json={"email": base["email"], "password": base["password"], "remember": False}).json()["token"]
    assert round(hours(short)) == 24 and _claims(short)["rem"] is False
    default = c.post("/auth/login", json={"email": base["email"], "password": base["password"]}).json()["token"]
    assert round(hours(default)) == 24 * 30                                      # the checkbox is ticked unless someone unticks it


def test_refresh_gives_the_same_kind_of_session_and_needs_a_session():
    init_db()
    c = TestClient(app)
    base = {"email": f"ref{time.time_ns()}@x.test", "password": "testpassword123", "name": "R"}
    short = c.post("/auth/register", json={**base, "remember": False}).json()["token"]
    r = c.post("/auth/refresh", headers={"Authorization": f"Bearer {short}"})
    assert r.status_code == 200 and _claims(r.json()["token"])["rem"] is False
    assert round((_claims(r.json()["token"])["exp"] - _claims(r.json()["token"])["iat"]) / 3600) == 24
    assert r.json()["token"] != short
    assert c.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).status_code == 200
    assert TestClient(app).post("/auth/refresh").status_code == 401
    assert TestClient(app).post("/auth/refresh", headers={"Authorization": "Bearer junk"}).status_code == 401


def _world():
    init_db()
    anita = TestClient(app)
    user = register_and_login(anita, role="both", name="Anita")
    pid = anita.post("/patients", json={"name": "Anita"}).json()["id"]
    anita.post("/medicines/manual", json={"patient_id": pid, "name": "Metformin", "strength": "500mg", "form": "tab", "times": ["08:00"], "food": "any", "duration_days": 3})
    code = anita.post(f"/patients/{pid}/caregiver-links").json()["code"]
    gita = TestClient(app)
    gita_user = register_and_login(gita, role="both", name="Gita")
    gpid = gita.post("/patients", json={"name": "Gita"}).json()["id"]
    gita.post("/medicines/manual", json={"patient_id": gpid, "name": "Vitamin D", "strength": "1000IU", "form": "tab", "times": ["09:00"], "food": "any", "duration_days": 3})
    gita.post("/caregiver/link/redeem", json={"code": code, "nickname": "Mum"})
    return anita, user, pid, gita, gita_user, gpid


def test_delete_needs_the_password_and_removes_only_that_persons_data():
    anita, user, pid, gita, gita_user, gpid = _world()
    assert anita.get("/auth/me").json()["has_password"] is True
    assert anita.post("/auth/delete-account", json={}).status_code == 403
    assert anita.post("/auth/delete-account", json={"password": "wrong-password"}).status_code == 403
    assert anita.get(f"/patients/{pid}").status_code == 200                       # still there after the refused attempts
    assert TestClient(app).post("/auth/delete-account", json={"password": "testpassword123"}).status_code == 401
    r = anita.post("/auth/delete-account", json={"password": "testpassword123"})
    assert r.status_code == 200 and r.json() == {"deleted": True}
    # she is gone, her session no longer works, and she cannot log in again
    assert anita.get("/auth/me").status_code == 401
    assert TestClient(app).post("/auth/login", json={"email": user["email"], "password": "testpassword123"}).status_code == 401
    db = SessionLocal()
    assert db.query(User).filter(User.id == user["id"]).count() == 0
    db.close()
    # Gita (who looked after her) keeps her own account and her own medicines; Anita just disappears from her family
    assert gita.get("/auth/me").status_code == 200
    assert gita.get(f"/patients/{gpid}/dashboard").status_code == 200
    assert "Vitamin D" in gita.get(f"/patients/{gpid}/dashboard").text
    assert gita.get("/caregiver/home").json()["family"] == []
    assert gita.get(f"/caregiver/patients/{pid}/overview").status_code in (403, 404)


def test_a_caregiver_deleting_their_account_leaves_the_patient_and_their_data():
    anita, user, pid, gita, gita_user, gpid = _world()
    assert gita.post("/auth/delete-account", json={"password": "testpassword123"}).status_code == 200
    assert anita.get(f"/patients/{pid}/dashboard").status_code == 200 and "Metformin" in anita.get(f"/patients/{pid}/dashboard").text
    assert [l["status"] for l in anita.get(f"/patients/{pid}/caregiver-links").json() if l["status"] == "active"] == []


def test_a_google_account_confirms_by_typing_its_email():
    init_db()
    c = TestClient(app)
    db = SessionLocal()
    u = User(email=f"goog{time.time_ns()}@gmail.com", password_hash=None, role="both", name="G")
    db.add(u); db.commit()
    token = auth.create_token(u, True)
    email = u.email
    db.close()
    h = {"Authorization": f"Bearer {token}"}
    assert c.get("/auth/me", headers=h).json()["has_password"] is False
    assert c.post("/auth/delete-account", json={"password": "anything"}, headers=h).status_code == 403
    assert c.post("/auth/delete-account", json={"confirm_email": "someone@else.com"}, headers=h).status_code == 403
    assert c.post("/auth/delete-account", json={"confirm_email": email.upper()}, headers=h).status_code == 200
    assert c.get("/auth/me", headers=h).status_code == 401


def test_the_account_is_all_or_nothing_if_the_deletion_fails(monkeypatch):
    anita, user, pid, gita, gita_user, gpid = _world()
    import account_deletion
    def boom(conn, uid):
        raise RuntimeError("database went away")
    monkeypatch.setattr(account_deletion, "_tool", type("T", (), {"delete_user": staticmethod(boom)}))
    r = anita.post("/auth/delete-account", json={"password": "testpassword123"})
    assert r.status_code == 500 and "nothing was removed" in r.json()["detail"]
    monkeypatch.undo()
    assert anita.get(f"/patients/{pid}/dashboard").status_code in (200, 401)   # the account row is untouched (a revoked token is fine)
    db = SessionLocal()
    assert db.query(User).filter(User.id == user["id"]).count() == 1
    db.close()
