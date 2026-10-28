"""Add member: an invite link/code that does nothing until the invited person is signed in and confirms."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from conftest import register_and_login  # noqa: E402
from db import FamilyInvite, SessionLocal, init_db  # noqa: E402
from main import app  # noqa: E402


def _person(name, role="both"):
    c = TestClient(app)
    user = register_and_login(c, role=role, name=name)
    return c, user


def _invite(gita, nickname="Mum"):
    r = gita.post("/family/invites", json={"nickname": nickname})
    assert r.status_code == 200, r.text
    return r.json()


def test_full_flow_gita_invites_mum_who_confirms_and_gita_then_looks_after_her():
    init_db()
    gita, gita_user = _person("Gita")
    mum, mum_user = _person("Sunita", role="patient")
    inv = _invite(gita)
    assert inv["url"].endswith(f"/join/{inv['code'].replace('-', '')}") and len(inv["code"]) == 9 and inv["status"] == "pending"

    # nothing is granted by the code alone: the preview only says who is asking
    assert mum.get(f"/family/join/{inv['code']}").json() == {"inviter_name": "Gita", "nickname": "Mum", "own": False}
    assert gita.get("/caregiver/home").json()["family"] == []

    r = mum.post(f"/family/join/{inv['code']}")                       # dashed code as pasted from WhatsApp
    assert r.status_code == 200 and r.json()["joined"] and r.json()["caregiver"] == "Gita"
    home = gita.get("/caregiver/home").json()
    assert [(f["label"], f["name"]) for f in home["family"]] == [("Mum", "Sunita")]
    assert gita.get(f"/caregiver/patients/{r.json()['patient_id']}/overview").status_code == 200
    # Mum can see who looks after her, and the link is an ordinary revocable one
    links = mum.get(f"/patients/{r.json()['patient_id']}/caregiver-links").json()
    assert [l["status"] for l in links] == ["active"]
    # one use only
    other, _ = _person("Dad")
    assert other.post(f"/family/join/{inv['code']}").status_code == 409
    assert gita.get("/family/invites").json()[0]["status"] == "accepted"


def test_an_invite_needs_a_signed_in_account_and_cannot_be_used_on_yourself():
    init_db()
    gita, _ = _person("Gita")
    inv = _invite(gita)
    assert TestClient(app).get(f"/family/join/{inv['code']}").status_code == 401
    assert TestClient(app).post(f"/family/join/{inv['code']}").status_code == 401
    assert gita.post(f"/family/join/{inv['code']}").status_code == 400
    assert gita.get("/caregiver/home").json()["family"] == []


def test_cancelled_expired_and_unknown_codes_do_nothing():
    init_db()
    gita, _ = _person("Gita")
    mum, _ = _person("Sunita")
    a, b = _invite(gita), _invite(gita)
    assert gita.post(f"/family/invites/{a['id']}/cancel").json()["status"] == "cancelled"
    assert mum.post(f"/family/join/{a['code']}").status_code == 410
    db = SessionLocal()
    db.query(FamilyInvite).filter(FamilyInvite.id == b["id"]).update({"expires_at": datetime.utcnow() - timedelta(minutes=1)})
    db.commit()
    db.close()
    assert mum.post(f"/family/join/{b['code']}").status_code == 410
    assert mum.post("/family/join/ZZZZZZZZ").status_code == 404
    assert gita.get("/caregiver/home").json()["family"] == []


def test_only_the_creator_can_cancel_or_list_an_invite():
    init_db()
    gita, _ = _person("Gita")
    sneaky, _ = _person("Sneaky")
    inv = _invite(gita)
    assert sneaky.post(f"/family/invites/{inv['id']}/cancel").status_code == 404
    assert sneaky.get("/family/invites").json() == []
    assert gita.get("/family/invites").json()[0]["status"] == "pending"


def test_joining_again_is_harmless_and_a_new_account_gets_its_own_you_profile():
    init_db()
    gita, _ = _person("Gita")
    mum, mum_user = _person("Sunita")
    assert mum.get("/auth/me").json()["patient_ids"] == []
    r = mum.post(f"/family/join/{_invite(gita)['code']}").json()
    assert mum.get("/auth/me").json()["patient_ids"] == [r["patient_id"]]           # profile created on joining
    again = mum.post(f"/family/join/{_invite(gita)['code']}").json()                  # a second invite from the same person
    assert again["patient_id"] == r["patient_id"]
    assert len(gita.get("/caregiver/home").json()["family"]) == 1


def test_too_many_open_invites_are_refused():
    init_db()
    gita, _ = _person("Gita")
    for _ in range(10):
        _invite(gita)
    assert gita.post("/family/invites", json={}).status_code == 429


# ---- the other direction: a patient makes a code to share THEIR medicines; the person who enters it becomes the caregiver

def test_a_code_made_by_the_patient_links_whoever_enters_it_and_can_be_previewed_first():
    init_db()
    mum, mum_user = _person("Sunita")
    pid = mum.post("/patients", json={"name": "Sunita"}).json()["id"]
    code = mum.post(f"/patients/{pid}/caregiver-links").json()["code"]
    gita, _ = _person("Gita")
    assert TestClient(app).get(f"/caregiver/link/{code}").status_code == 401           # a stray link shows nothing
    assert gita.get(f"/caregiver/link/{code}").json() == {"patient_name": "Sunita", "own": False}
    assert gita.get("/caregiver/home").json()["family"] == []                          # previewing links nothing
    r = gita.post("/caregiver/link/redeem", json={"code": code, "nickname": "Mum"})
    assert r.status_code == 200
    assert [(f["label"], f["name"]) for f in gita.get("/caregiver/home").json()["family"]] == [("Mum", "Sunita")]
    other, _ = _person("Dad")
    assert other.post("/caregiver/link/redeem", json={"code": code}).status_code == 409   # already used by another


def test_your_own_code_and_unknown_or_old_codes_are_refused():
    init_db()
    mum, _ = _person("Sunita")
    pid = mum.post("/patients", json={"name": "Sunita"}).json()["id"]
    code = mum.post(f"/patients/{pid}/caregiver-links").json()["code"]
    assert mum.get(f"/caregiver/link/{code}").json()["own"] is True
    assert mum.post("/caregiver/link/redeem", json={"code": code}).status_code == 400
    gita, _ = _person("Gita")
    assert gita.get("/caregiver/link/NOPE0000").status_code == 404
    assert gita.post("/caregiver/link/redeem", json={"code": "NOPE0000"}).status_code == 404
    from db import CaregiverLink
    db = SessionLocal()
    db.query(CaregiverLink).filter(CaregiverLink.code == code).update({"created_at": datetime.utcnow() - timedelta(days=8)})
    db.commit()
    db.close()
    assert gita.post("/caregiver/link/redeem", json={"code": code}).status_code == 410   # a week-old code is no longer a key
    assert gita.get("/caregiver/home").json()["family"] == []


def test_the_patient_is_told_the_moment_someone_uses_their_code(monkeypatch):
    import care_push
    init_db()
    told = []
    monkeypatch.setattr(care_push, "push_to_patient", lambda db, pid, title, body, tag, **kw: told.append((pid, title)) or 1)
    mum, _ = _person("Sunita")
    pid = mum.post("/patients", json={"name": "Sunita"}).json()["id"]
    code = mum.post(f"/patients/{pid}/caregiver-links").json()["code"]
    gita, _ = _person("Gita")
    assert gita.post("/caregiver/link/redeem", json={"code": code}).status_code == 200
    assert gita.post("/caregiver/link/redeem", json={"code": code}).status_code == 200        # same person again: no second alert
    assert told == [(pid, "Gita can now see your medicines")]


def test_codes_a_patient_makes_avoid_look_alike_characters_and_ignore_dashes_and_case():
    import auth
    for _ in range(300):
        c = auth.gen_link_code()
        assert len(c) == 8 and not set(c) & set("0O1I"), c
    init_db()
    mum, _ = _person("Sunita")
    pid = mum.post("/patients", json={"name": "Sunita"}).json()["id"]
    code = mum.post(f"/patients/{pid}/caregiver-links").json()["code"]
    gita, _ = _person("Gita")
    typed = f" {code[:4].lower()}-{code[4:].lower()} "                       # as someone might type or paste it
    assert gita.get(f"/caregiver/link/{typed}").json()["patient_name"] == "Sunita"
    assert gita.post("/caregiver/link/redeem", json={"code": typed}).status_code == 200


def test_links_people_share_always_point_at_the_real_site_never_this_computer(monkeypatch):
    from starlette.requests import Request
    import family_router as fr
    def req(base):
        return Request({"type": "http", "scheme": base.split(":")[0], "server": (base.split("//")[1].split(":")[0], 80), "path": "/",
                        "headers": [(b"host", base.split("//")[1].encode())], "query_string": b""})
    monkeypatch.delenv("SMARTPOLI_PUBLIC_URL", raising=False)
    assert fr.public_base_url(req("http://127.0.0.1:8123")) == "https://smartpoli.onrender.com"
    assert fr.public_base_url(req("http://localhost:8000")) == "https://smartpoli.onrender.com"
    assert fr.public_base_url(req("http://myapp.onrender.com")) == "https://myapp.onrender.com"      # proxy hides https
    monkeypatch.setenv("SMARTPOLI_PUBLIC_BASE_URL", "http://127.0.0.1:8000")                      # a local setting never wins
    assert fr.public_base_url(req("http://127.0.0.1:8123")) == "https://smartpoli.onrender.com"
    monkeypatch.setenv("SMARTPOLI_PUBLIC_BASE_URL", "https://live.example.org/")                  # the live server's existing setting does
    assert fr.public_base_url(req("http://127.0.0.1:8123")) == "https://live.example.org"
    monkeypatch.setenv("SMARTPOLI_PUBLIC_URL", "https://care.example.org/")                       # and the explicit one is first
    assert fr.public_base_url(req("http://127.0.0.1:8123")) == "https://care.example.org"
    # and both kinds of invite carry it
    init_db()
    mum, _ = _person("Sunita")
    pid = mum.post("/patients", json={"name": "Sunita"}).json()["id"]
    code = mum.post(f"/patients/{pid}/caregiver-links").json()
    assert code["url"] == f"https://care.example.org/join/{code['code']}"
    gita, _ = _person("Gita")
    assert gita.post("/family/invites", json={}).json()["url"].startswith("https://care.example.org/join/")
