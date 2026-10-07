"""Remove a medicine; the 'shouldn't be taken together' popup data shown when the app opens."""
from fastapi.testclient import TestClient

import gap_ai
from db import Dose, SessionLocal
from main import app
from timing_helpers import new_patient


def _add(client, pid, name, time="09:00", **kw):
    body = {"patient_id": pid, "name": name, "times": [time], "duration_days": 10}
    body.update(kw)
    r = client.post("/medicines/manual", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _fake_pair(hours, confidence="high"):
    return lambda db, a, b, **k: {"hours": hours, "basis": "ai_estimate", "quote": None, "applies_when": None, "confidence": confidence}


# ---------------------------------------------------------------- removing a medicine

def test_removing_a_medicine_stops_reminders_hides_it_and_keeps_history():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        med = _add(client, pid, "Ferrous sulfate")["medicine"]
        db = SessionLocal()
        first = db.query(Dose).filter(Dose.medicine_id == med["id"]).order_by(Dose.scheduled_at).first()
        first.state = "taken"                                                   # some history already exists
        db.commit()
        db.close()
        r = client.delete(f"/medicines/{med['id']}")
        assert r.status_code == 200 and r.json()["removed"] is True and r.json()["upcoming_doses_cancelled"] >= 1
        db = SessionLocal()
        states = [d.state for d in db.query(Dose).filter(Dose.medicine_id == med["id"]).all()]
        db.close()
        assert "taken" in states and "pending" not in states and "cancelled" in states     # history kept, future cancelled
        dash = client.get(f"/patients/{pid}/dashboard").json()
        assert all(d["medicine_name"] != "Ferrous sulfate" for d in dash["upcoming_doses"])
        names = [m["name"] for m in client.get(f"/patients/{pid}/safety-center").json()["medicines"]]
        assert "Ferrous sulfate" not in names
        rep = client.get(f"/patients/{pid}/report").json()
        assert all(m["name"] != "Ferrous sulfate" for p in rep["prescriptions"] for m in p["medicines"])


def test_only_the_owner_can_remove_a_medicine():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        mid = _add(client, pid, "Telmisartan")["medicine"]["id"]
    with TestClient(app) as stranger:
        from conftest import register_and_login
        register_and_login(stranger)
        assert stranger.delete(f"/medicines/{mid}").status_code == 403
    assert TestClient(app).delete(f"/medicines/{mid}").status_code == 401


# ---------------------------------------------------------------- the popup shown when the app is opened

def test_open_warnings_list_real_problems_with_medicine_ids_and_a_changing_key(monkeypatch):
    monkeypatch.setattr(gap_ai, "pair_gap", _fake_pair(2.0))
    monkeypatch.setattr(gap_ai, "warm_patient", lambda *a, **k: {})
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        a = _add(client, pid, "Calcium carbonate")["medicine"]["id"]
        b = _add(client, pid, "Ferrous sulfate")["medicine"]["id"]
        out = client.get(f"/patients/{pid}/open-warnings").json()
        assert out["has_problems"] is True and len(out["pair_warnings"]) == 1
        assert sorted(out["pair_warnings"][0]["medicine_ids"]) == sorted([a, b])      # so the popup can offer "Remove ..."
        key = out["key"]
        client.delete(f"/medicines/{b}")                                               # remove one: the problem is gone
        out = client.get(f"/patients/{pid}/open-warnings").json()
        assert out["has_problems"] is False and out["key"] != key


def test_open_warnings_are_silent_when_everything_is_fine_or_only_minor_or_unsure(monkeypatch):
    monkeypatch.setattr(gap_ai, "warm_patient", lambda *a, **k: {})
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _add(client, pid, "Calcium carbonate")
        _add(client, pid, "Ferrous sulfate")
        monkeypatch.setattr(gap_ai, "pair_gap", _fake_pair(None))                       # no separation needed
        assert client.get(f"/patients/{pid}/open-warnings").json()["has_problems"] is False
        monkeypatch.setattr(gap_ai, "pair_gap", _fake_pair(3.0, confidence="low"))      # the AI is unsure
        assert client.get(f"/patients/{pid}/open-warnings").json()["has_problems"] is False
        monkeypatch.setattr(gap_ai, "pair_gap", _fake_pair(0.5))                         # a tiny gap
        assert client.get(f"/patients/{pid}/open-warnings").json()["has_problems"] is False


def test_open_warnings_include_known_interactions_but_not_minor_ones(monkeypatch):
    import json
    from interactions import load_ruleset
    rules = load_ruleset()
    pairs = rules["pairs"] if isinstance(rules, dict) else rules
    sample = {}
    for p in pairs:
        sample.setdefault(p["severity"], p)
    assert "CRITICAL" in sample and "MINOR" in sample
    monkeypatch.setattr(gap_ai, "pair_gap", lambda *a, **k: None)
    monkeypatch.setattr(gap_ai, "warm_patient", lambda *a, **k: {})

    def names(p):
        return [p[k] for k in p if k in ("a", "b", "drug_a", "drug_b")][:2]

    with TestClient(app) as client:
        pid, _ = new_patient(client)
        for n in names(sample["CRITICAL"]):
            _add(client, pid, n, time="09:00")
        out = client.get(f"/patients/{pid}/open-warnings").json()
        assert any(i["severity"] == "CRITICAL" for i in out["interactions"])
        assert all(i["medicine_ids"][0] and i["medicine_ids"][1] for i in out["interactions"])
        pid2, _ = new_patient(client)
        for n in names(sample["MINOR"]):
            _add(client, pid2, n, time="09:00")
        assert client.get(f"/patients/{pid2}/open-warnings").json()["interactions"] == []


def test_open_warnings_need_read_access():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
    with TestClient(app) as stranger:
        from conftest import register_and_login
        register_and_login(stranger)
        assert stranger.get(f"/patients/{pid}/open-warnings").status_code == 403


def test_a_removed_medicine_leaves_todays_list_and_the_missed_popup_data():
    from datetime import datetime, timedelta
    from clock import patient_now
    from db import SessionLocal
    from timing_helpers import add_medicine, new_patient
    c = TestClient(app)
    pid, _ = new_patient(c, "Remover")
    db = SessionLocal()
    now = patient_now(db, pid)
    db.close()
    keep, _ = add_medicine(pid, "Keeper", [now + timedelta(minutes=1)])
    gone, _ = add_medicine(pid, "Goner", [now + timedelta(minutes=2), now - timedelta(minutes=90)], states=["pending", "missed"])
    assert c.delete(f"/medicines/{gone}").status_code == 200
    dash = c.get(f"/patients/{pid}/dashboard").json()
    assert "Goner" not in {d["medicine_name"] for d in dash["today_doses"]}
    assert "Goner" not in {d["medicine_name"] for d in dash["recent_doses"]}
    assert "Keeper" in {d["medicine_name"] for d in dash["today_doses"]} or now.date() != (now + timedelta(minutes=1)).date()


def test_a_dose_taken_or_skipped_before_the_two_hours_never_becomes_missed():
    from datetime import timedelta
    from clock import patient_now
    from db import Dose, SessionLocal
    from scheduler import sweep_missed
    from timing_helpers import add_medicine, new_patient
    c = TestClient(app)
    pid, _ = new_patient(c, "Acts Early")
    db = SessionLocal()
    now = patient_now(db, pid)
    db.close()
    _, (skipped, taken) = add_medicine(pid, "Creatine", [now - timedelta(minutes=30), now - timedelta(minutes=20)])
    assert c.post(f"/doses/{skipped}/skip", json={"reason": "x"}).status_code == 200
    assert c.post(f"/doses/{taken}/take").status_code == 200
    db = SessionLocal()
    sweep_missed(db, now + timedelta(hours=5))                     # long after the 2-hour mark
    states = {d.id: d.state for d in db.query(Dose).filter(Dose.id.in_([skipped, taken])).all()}
    db.close()
    assert states[skipped] == "skipped" and states[taken] == "taken"
