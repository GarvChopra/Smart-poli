"""Adding a medicine that clashes with one already taken -> a sourced warning plus a concrete safer time."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import gap_ai  # noqa: E402
import pair_check as pc  # noqa: E402
from main import app  # noqa: E402
from timing_helpers import new_patient  # noqa: E402


def T(h, m=0, d=0):
    return datetime(2030, 1, 1 + d, h, m)


# ---------------------------------------------------------------- pure helpers

def test_closest_distance_and_smallest_later_shift():
    assert pc.closest_hours([T(9)], [T(9)]) == 0
    assert pc.closest_hours([T(9), T(21)], [T(12)]) == 3
    assert pc.suggest_shift([T(9)], [T(9)], 2) == 120                       # iron 2 h after calcium
    assert pc.suggest_shift([T(9), T(21)], [T(9), T(21)], 2) == 120         # every dose moves the same amount
    assert pc.suggest_shift([T(23, 0)], [T(23, 0)], 2) is None              # would cross midnight: no invented time
    assert pc.suggest_shift([T(9)], [T(9)], 20) is None                     # beyond the 12 h search limit


# ---------------------------------------------------------------- end to end (the AI lookup is faked)

def _fake_pair(hours, basis="ai_estimate", quote=None):
    return lambda db, a, b, **k: {"hours": hours, "basis": basis, "quote": quote, "applies_when": "taken together", "confidence": "medium"}


def _add(client, pid, name, time):
    r = client.post("/medicines/manual", json={"patient_id": pid, "name": name, "times": [time], "duration_days": 10})
    assert r.status_code == 200, r.text
    return r.json()["medicine"]["id"]


def test_calcium_and_iron_together_are_flagged_with_a_suggested_time_and_can_be_moved(monkeypatch):
    monkeypatch.setattr(gap_ai, "pair_gap", _fake_pair(2.0))
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _add(client, pid, "Calcium carbonate", "09:00")
        iron = _add(client, pid, "Ferrous sulfate", "09:00")
        out = client.get(f"/medicines/{iron}/pair-check").json()
        assert len(out["warnings"]) == 1
        w = out["warnings"][0]
        assert w["kind"] == "pair_gap" and w["required_hours"] == 2.0 and w["source"] == "ai_estimate"
        assert w["suggestion"]["shift_minutes"] == 120 and w["suggestion"]["new_times"] == ["11:00"]
        assert "AI estimate" in w["source_label"]
        # accept the suggestion: the iron doses move 2 h later, and the warning is gone
        moved = client.post(f"/medicines/{iron}/shift", json={"minutes": 120})
        assert moved.status_code == 200 and moved.json()["times"] == ["11:00"] and moved.json()["doses_moved"] >= 1
        assert client.get(f"/medicines/{iron}/pair-check").json()["warnings"] == []


def test_far_apart_or_unrelated_medicines_are_not_flagged(monkeypatch):
    monkeypatch.setattr(gap_ai, "pair_gap", _fake_pair(2.0))
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _add(client, pid, "Calcium carbonate", "09:00")
        iron = _add(client, pid, "Ferrous sulfate", "15:00")                  # 6 h apart: fine
        assert client.get(f"/medicines/{iron}/pair-check").json()["warnings"] == []
        monkeypatch.setattr(gap_ai, "pair_gap", lambda *a, **k: None)         # no source knows of a problem
        assert client.get(f"/medicines/{iron}/pair-check").json()["warnings"] == []
        monkeypatch.setattr(gap_ai, "pair_gap", _fake_pair(None))             # "no separation needed"
        assert client.get(f"/medicines/{iron}/pair-check").json()["warnings"] == []


def test_a_label_graded_answer_carries_its_quote(monkeypatch):
    monkeypatch.setattr(gap_ai, "pair_gap", _fake_pair(4.0, "label", "Take at least 4 hours apart."))
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _add(client, pid, "Levothyroxine", "08:00")
        other = _add(client, pid, "Calcium carbonate", "08:00")
        w = client.get(f"/medicines/{other}/pair-check").json()["warnings"][0]
        assert w["source"] == "label" and w["quote"] == "Take at least 4 hours apart." and "official" in w["source_label"].lower()


def test_shift_refuses_midnight_crossing_and_strangers():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        late = _add(client, pid, "Melatonin", "23:00")
        assert client.post(f"/medicines/{late}/shift", json={"minutes": 120}).status_code == 422
        assert client.post(f"/medicines/{late}/shift", json={"minutes": 5}).status_code == 422     # below the 15 min step
    with TestClient(app) as stranger:
        from conftest import register_and_login
        register_and_login(stranger)
        assert stranger.get(f"/medicines/{late}/pair-check").status_code == 403
        assert stranger.post(f"/medicines/{late}/shift", json={"minutes": 30}).status_code == 403


def test_only_real_problems_interrupt_small_gaps_and_unsure_answers_are_ignored(monkeypatch):
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _add(client, pid, "Calcium carbonate", "09:00")
        iron = _add(client, pid, "Ferrous sulfate", "09:00")
        monkeypatch.setattr(gap_ai, "pair_gap", _fake_pair(0.5))                       # a 30-minute gap: not worth a popup
        assert client.get(f"/medicines/{iron}/pair-check").json()["warnings"] == []
        monkeypatch.setattr(gap_ai, "pair_gap", lambda *a, **k: {"hours": 3, "basis": "ai_estimate", "quote": None,
                                                                 "applies_when": None, "confidence": "low"})
        assert client.get(f"/medicines/{iron}/pair-check").json()["warnings"] == []   # the AI is not sure: stay silent
        monkeypatch.setattr(gap_ai, "pair_gap", lambda *a, **k: {"hours": 3, "basis": "ai_estimate", "quote": None,
                                                                 "applies_when": None, "confidence": "medium"})
        assert len(client.get(f"/medicines/{iron}/pair-check").json()["warnings"]) == 1   # a confident answer does warn


# ---------------------------------------------------------------- typed prescriptions and the Safety center

def _typed(client, pid, lines):
    pres = client.post("/prescriptions", json={"patient_id": pid, "lines": lines}).json()
    assert client.post(f"/prescriptions/{pres['prescription_id']}/confirm").status_code == 200
    return pres["prescription_id"]


def test_a_typed_prescription_with_a_clashing_pair_is_flagged_once(monkeypatch):
    monkeypatch.setattr(gap_ai, "pair_gap", _fake_pair(2.0))
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        rx = _typed(client, pid, ["Tab Calcium carbonate 500mg 1-0-0 x5d", "Tab Ferrous sulfate 200mg 1-0-0 x5d"])
        out = client.get(f"/prescriptions/{rx}/pair-check").json()
        assert len(out["warnings"]) == 1                                  # A-vs-B and B-vs-A are one warning, not two
        assert {m.lower() for m in out["warnings"][0]["medicines"]} == {"calcium carbonate", "ferrous sulfate"}


def test_a_typed_prescription_of_unrelated_medicines_is_silent(monkeypatch):
    monkeypatch.setattr(gap_ai, "pair_gap", lambda *a, **k: {"hours": None, "basis": "none", "quote": None, "applies_when": None, "confidence": "high"})
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        rx = _typed(client, pid, ["Tab Telmisartan 40mg 1-0-0 x5d", "Tab Vitamin D3 60000IU 1-0-0 x5d"])
        assert client.get(f"/prescriptions/{rx}/pair-check").json()["warnings"] == []


def test_the_safety_center_check_reads_the_cache_reports_pending_and_flags_real_clashes(monkeypatch):
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _add(client, pid, "Calcium carbonate", "09:00")
        _add(client, pid, "Ferrous sulfate", "09:00")
        monkeypatch.setattr(gap_ai, "pair_gap", lambda *a, **k: None)                  # nothing cached yet
        monkeypatch.setattr(gap_ai, "warm_patient", lambda *a, **k: {})
        out = client.get(f"/patients/{pid}/pair-check").json()
        assert out["warnings"] == [] and out["pending"] == 1
        monkeypatch.setattr(gap_ai, "pair_gap", _fake_pair(2.0))                       # now it is cached
        out = client.get(f"/patients/{pid}/pair-check").json()
        assert out["pending"] == 0 and len(out["warnings"]) == 1
        w = out["warnings"][0]
        assert w["medicines"][0].lower() == "ferrous sulfate"                          # the later-added one is the one to move
        assert w["suggestion"]["shift_minutes"] == 120
        monkeypatch.setattr(gap_ai, "pair_gap", _fake_pair(None))                       # a safe pair says nothing
        assert client.get(f"/patients/{pid}/pair-check").json()["warnings"] == []


def test_pair_check_endpoints_are_for_the_owner_only():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        rx = _typed(client, pid, ["Tab Telmisartan 40mg 1-0-0 x5d"])
    with TestClient(app) as stranger:
        from conftest import register_and_login
        register_and_login(stranger)
        assert stranger.get(f"/patients/{pid}/pair-check").status_code == 403
        assert stranger.get(f"/prescriptions/{rx}/pair-check").status_code == 403
