"""Feedback: patients and caregivers send a rating + message; the owner reads it with a secret."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from conftest import register_and_login  # noqa: E402
from db import Feedback, SessionLocal, init_db  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture()
def client():
    init_db()
    db = SessionLocal()
    db.query(Feedback).delete()
    db.commit()
    db.close()
    with TestClient(app) as c:
        register_and_login(c, role="patient", name="Asha")
        yield c


def send(c, **kw):
    body = {"rating": 5, "category": "praise", "message": "Reminders are great"}
    body.update(kw)
    return c.post("/feedback", json=body)


def test_a_patient_and_a_caregiver_can_each_send_feedback(client):
    r = send(client)
    assert r.status_code == 200 and r.json()["id"] and r.json()["thanks"]
    cg = TestClient(app)
    register_and_login(cg, role="caregiver", name="Sita")
    assert send(cg, rating=3, category="problem", message="Alerts came late").status_code == 200
    db = SessionLocal()
    rows = db.query(Feedback).order_by(Feedback.id).all()
    assert [(f.role, f.rating, f.category) for f in rows] == [("patient", 5, "praise"), ("caregiver", 3, "problem")]
    db.close()


def test_a_rating_alone_is_enough_but_it_must_be_one_to_five(client):
    assert send(client, message="", category="other").status_code == 200           # rated, nothing written
    for bad in (0, 6, -1, "x"):
        assert send(client, rating=bad).status_code == 422, bad
    assert send(client, category="shout").status_code == 422
    assert send(client, message="a" * 1001).status_code == 422
    assert send(client, message="a" * 1000).status_code == 200


def test_it_needs_a_login(client):
    assert TestClient(app).post("/feedback", json={"rating": 5}).status_code == 401
    assert TestClient(app).get("/feedback/mine").status_code == 401


def test_five_a_hour_is_the_limit(client):
    codes = [send(client, message=f"m{i}").status_code for i in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


def test_you_see_your_own_feedback_only_newest_first(client):
    send(client, message="first")
    send(client, message="second", rating=4)
    mine = client.get("/feedback/mine").json()["items"]
    assert [m["message"] for m in mine] == ["second", "first"] and mine[0]["rating"] == 4
    other = TestClient(app)
    register_and_login(other, role="caregiver")
    assert other.get("/feedback/mine").json()["items"] == []


def test_text_is_stored_verbatim(client):
    send(client, message="<script>alert(1)</script>")
    assert client.get("/feedback/mine").json()["items"][0]["message"] == "<script>alert(1)</script>"      # the page escapes it


def test_the_owner_reads_feedback_with_a_secret_and_it_is_hidden_otherwise(client, monkeypatch):
    send(client, rating=5, category="praise", message="Love it", contact_ok=True)
    send(client, rating=1, category="problem", message="Crashed", contact_ok=False)
    monkeypatch.delenv("SMARTPOLI_ADMIN_SECRET", raising=False)
    assert TestClient(app).get("/internal/feedback", headers={"X-Admin-Secret": "x"}).status_code == 404      # not configured
    monkeypatch.setenv("SMARTPOLI_ADMIN_SECRET", "owner-secret-123")
    assert TestClient(app).get("/internal/feedback").status_code == 404                                       # no header
    assert TestClient(app).get("/internal/feedback", headers={"X-Admin-Secret": "wrong"}).status_code == 404
    out = TestClient(app).get("/internal/feedback", headers={"X-Admin-Secret": "owner-secret-123"}).json()
    assert out["count"] == 2 and out["average"] == 3.0 and out["by_rating"]["5"] == 1 and out["by_rating"]["1"] == 1
    by_msg = {i["message"]: i for i in out["items"]}
    assert by_msg["Love it"]["email"] and by_msg["Crashed"]["email"] is None             # contact details only when allowed


def test_the_secret_endpoint_is_not_in_the_public_api_docs(client):
    assert "/internal/feedback" not in client.get("/openapi.json").text
