"""
What "Mark taken" does when the dose is not right to take: the gap since the last dose of the SAME medicine,
the gap from OTHER medicines actually taken, and how AI answers are graded and used.

The LLM and openFDA are never called: gap_ai's lookups are replaced with fixed fakes, so every case is
deterministic and no real patient data or network is involved.
"""

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import clock  # noqa: E402
import gap_ai  # noqa: E402
import take_guard  # noqa: E402
from db import AIGapCache, AuditLog, Dose, SessionLocal, init_db  # noqa: E402
from main import app  # noqa: E402
from timing_helpers import add_medicine, new_patient  # noqa: E402


def now_local():
    return clock.local_now("UTC").replace(second=0, microsecond=0)


@pytest.fixture()
def db():
    init_db()
    s = SessionLocal()
    s.query(AIGapCache).delete()
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def ai_on(monkeypatch):
    monkeypatch.setenv("SMARTPOLI_AI_GAPS", "1")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")


def label_fn(text="label text"):
    return lambda generic, fields: text


# ---------------------------------------------------------------- grading AI answers

def test_a_number_counts_as_from_the_label_only_if_the_quote_contains_it():
    text = "Administer levothyroxine sodium tablets at least 4 hours apart from these agents. Calcium impairs absorption."
    good = gap_ai.grade_pair({"separate_hours": 4, "quote": "Administer levothyroxine sodium tablets at least 4 hours apart from these agents."}, text)
    assert good["basis"] == "label" and good["hours"] == 4 and good["quote"]
    # a real quote that does not contain the number only proves the interaction exists - the 2 is the AI's own
    weak = gap_ai.grade_pair({"separate_hours": 2, "quote": "Calcium impairs absorption."}, text)
    assert weak["basis"] == "ai_estimate" and weak["hours"] == 2
    # a quote that is not in the label is discarded
    fake = gap_ai.grade_pair({"separate_hours": 6, "quote": "Keep them 6 hours apart, always."}, text)
    assert fake["basis"] == "ai_estimate" and fake["quote"] is None


def test_number_words_and_interval_phrases_in_quotes():
    assert gap_ai._quote_states_hours("take 1 tablet every 4 to 6 hours", 6)
    assert gap_ai._quote_states_hours("at least two hours before or two hours after", 2)
    assert gap_ai._quote_states_hours("Take ZITUVIMET orally twice daily with meals.", 12)
    assert gap_ai._quote_states_hours("once a day in the morning", 24)
    assert not gap_ai._quote_states_hours("take with meals", 8)


def test_nonsense_numbers_and_malformed_output_are_discarded():
    assert gap_ai.grade_single({"min_hours_between_doses": 500}, "")["basis"] == "none"
    assert gap_ai.grade_single({"min_hours_between_doses": -3}, "")["basis"] == "none"
    assert gap_ai.grade_single({"min_hours_between_doses": "soon"}, "")["min_hours"] is None
    assert gap_ai.grade_single({}, "")["basis"] == "none"
    assert gap_ai.grade_pair({"separate_hours": 99}, "")["basis"] == "none"
    g = gap_ai.grade_single({"min_hours_between_doses": 6, "max_doses_per_24h": 4, "confidence": "certain!!"}, "")
    assert g["basis"] == "ai_estimate" and g["max_per_24h"] == 4 and g["confidence"] == "low"


def test_medicine_names_are_reduced_to_generic_names_before_leaving_the_server():
    assert gap_ai.canonical_name("Shelcal") == "calcium carbonate"
    assert gap_ai.canonical_name("Tab Glycomet 500mg") == "metformin"
    assert gap_ai.canonical_name("Tab Dolo 650mg") == "paracetamol"


# ---------------------------------------------------------------- lookups: cache, outages, switch

def test_a_lookup_is_cached_so_the_llm_is_asked_once(db, ai_on):
    calls = []

    def llm(system, user):
        calls.append(user)
        return {"min_hours_between_doses": 12, "quote": "Take it twice daily with meals.", "confidence": "high"}

    first = gap_ai.single_gap(db, "Metformin", label_fn=label_fn("Take it twice daily with meals."), llm_fn=llm)
    assert first["basis"] == "label" and first["min_hours"] == 12
    again = gap_ai.single_gap(db, "metformin", allow_network=False)          # cache only - no network, no LLM
    assert again == first and len(calls) == 1
    assert "patient" not in calls[0].lower() and "metformin" in calls[0].lower()   # only the generic name is sent


def test_no_cache_and_no_network_means_no_ai_answer(db, ai_on):
    assert gap_ai.single_gap(db, "Metformin", allow_network=False) is None
    assert gap_ai.pair_gap(db, "Metformin", "Amlodipine", allow_network=False) is None


def test_an_llm_failure_is_not_cached_and_does_not_raise(db, ai_on):
    def boom(system, user):
        raise RuntimeError("429 rate limit")
    assert gap_ai.single_gap(db, "Metformin", label_fn=label_fn(), llm_fn=boom) is None
    assert db.query(AIGapCache).count() == 0
    ok = gap_ai.single_gap(db, "Metformin", label_fn=label_fn(), llm_fn=lambda s, u: {"min_hours_between_doses": 12})
    assert ok["basis"] == "ai_estimate"


def test_no_gap_needed_is_a_valid_cached_answer(db, ai_on):
    calls = []
    llm = lambda s, u: calls.append(1) or {"separate_hours": None, "confidence": "high"}  # noqa: E731
    r = gap_ai.pair_gap(db, "Metformin", "Amlodipine", label_fn=label_fn(), llm_fn=llm)
    assert r["basis"] == "none" and r["hours"] is None
    gap_ai.pair_gap(db, "Amlodipine", "Metformin", llm_fn=llm)               # either order hits the same cache row
    assert len(calls) == 1


def test_the_whole_feature_can_be_switched_off(db, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("SMARTPOLI_AI_GAPS", "0")
    assert not gap_ai.is_enabled()
    assert gap_ai.single_gap(db, "Metformin", label_fn=label_fn(), llm_fn=lambda s, u: {"min_hours_between_doses": 12}) is None
    monkeypatch.setenv("SMARTPOLI_AI_GAPS", "1")
    monkeypatch.delenv("GROQ_API_KEY")
    assert not gap_ai.is_enabled()


# ---------------------------------------------------------------- schedule-derived floor and how AI may adjust it

class M:
    def __init__(self, times, name="Metformin"):
        self.times, self.name, self.raw_text = json.dumps(times), name, name


def test_prescribed_interval_comes_from_the_shortest_gap_in_the_schedule():
    assert take_guard.prescribed_interval_hours(M(["08:30"])) == 24
    assert take_guard.prescribed_interval_hours(M(["08:30", "20:30"])) == 12
    assert take_guard.prescribed_interval_hours(M(["06:00", "14:00", "22:00"])) == 8
    assert take_guard.prescribed_interval_hours(M(["08:30", "14:00", "20:30"])) == 5.5
    assert take_guard.prescribed_interval_hours(M([])) is None
    assert take_guard.prescribed_interval_hours(type("X", (), {"times": "{bad"})()) is None


def test_ai_can_raise_the_gap_but_never_loosen_it_and_never_beyond_three_quarters_of_the_interval(db, monkeypatch):
    bd = M(["08:30", "20:30"])                                                 # interval 12 -> floor 6, cap 9
    monkeypatch.setattr(gap_ai, "single_gap", lambda *a, **k: None)
    assert take_guard.min_gap_info(db, bd)["hours"] == 6 and take_guard.min_gap_info(db, bd)["source"] == "schedule"
    monkeypatch.setattr(gap_ai, "single_gap", lambda *a, **k: {"min_hours": 3, "basis": "ai_estimate", "quote": None})
    assert take_guard.min_gap_info(db, bd)["hours"] == 6                       # AI asked for less: ignored
    monkeypatch.setattr(gap_ai, "single_gap", lambda *a, **k: {"min_hours": 8, "basis": "ai_estimate", "quote": None})
    i = take_guard.min_gap_info(db, bd)
    assert i["hours"] == 8 and i["source"] == "ai_estimate"
    monkeypatch.setattr(gap_ai, "single_gap", lambda *a, **k: {"min_hours": 20, "basis": "label", "quote": "q"})
    i = take_guard.min_gap_info(db, bd)
    assert i["hours"] == 9 and i["source"] == "schedule"                       # clamped: a scheduled dose is never blocked
    monkeypatch.setattr(gap_ai, "single_gap", lambda *a, **k: {"min_hours": 6, "basis": "label", "quote": "every 6 hours", "max_per_24h": 4})
    prn = M([], "Paracetamol")                                                 # as-needed: the label's own gap, uncapped
    i = take_guard.min_gap_info(db, prn)
    assert i["hours"] == 6 and i["source"] == "label" and i["max_per_24h"] == 4


# ---------------------------------------------------------------- the tap, end to end

def _taken(pid, name, hours_ago, times=None, slots=None, food="any"):
    t = now_local() - timedelta(hours=hours_ago)
    _, (d,) = add_medicine(pid, name, [t], states=["taken"], acted=[t], times=times, slots=slots, food=food)
    return t


def _due(pid, name, in_minutes=0, times=None):
    _, (d,) = add_medicine(pid, name, [now_local() + timedelta(minutes=in_minutes)], times=times)
    return d


def test_a_second_dose_of_the_same_medicine_too_soon_is_refused_with_the_time_it_is_okay(db):
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        mid, (first, second) = add_medicine(
            pid, "Metformin", [now_local() - timedelta(hours=1), now_local() + timedelta(hours=1)],
            states=["taken", "pending"], acted=[now_local() - timedelta(hours=1), None], times=["08:30", "20:30"])
        r = client.post(f"/doses/{second}/take")
        assert r.status_code == 409
        body = r.json()
        assert body["code"] == "too_soon" and body["can_override"] is False and body["source"] == "schedule"
        assert body["detail"].startswith("You took Metformin at") and "Wait until" in body["detail"]
        assert "follows the schedule on your prescription" in body["detail"]
        assert datetime.fromisoformat(body["earliest"]) == now_local() - timedelta(hours=1) + timedelta(hours=6)
        # asking to override does not help for a prescription-schedule gap
        assert client.post(f"/doses/{second}/take", json={"override": True}).status_code == 409
        db.query(Dose).filter_by(id=second).first()
        d = SessionLocal()
        d.query(Dose).filter_by(id=first).update({"acted_at": now_local() - timedelta(hours=7)})
        d.commit()
        d.close()
        assert client.post(f"/doses/{second}/take").status_code == 200          # 7 h after the last one: fine


def test_a_first_dose_is_never_blocked_by_the_gap_rule(db):
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _, (only,) = add_medicine(pid, "Metformin", [now_local()], times=["08:30", "20:30"])
        assert client.post(f"/doses/{only}/take").status_code == 200


def test_calcium_just_taken_blocks_ciprofloxacin_using_the_curated_label_rule_and_can_be_overridden(db):
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        taken_at = _taken(pid, "Shelcal", 1)
        cipro = _due(pid, "Ciprofloxacin")
        r = client.post(f"/doses/{cipro}/take")
        assert r.status_code == 409
        b = r.json()
        assert b["code"] == "spacing" and b["can_override"] is True and b["source"] == "curated"
        assert datetime.fromisoformat(b["earliest"]) == taken_at + timedelta(hours=6)
        assert "6 hours after" in b["quote"] and "FDA" in b["source_label"] and b["other_medicine"] == "Shelcal"
        assert dose_state(cipro) == "pending"
        # "I already took it" is honoured and recorded as an override
        ok = client.post(f"/doses/{cipro}/take", json={"override": True})
        assert ok.status_code == 200 and ok.json()["state"] == "taken"
        assert "dose_taken_override" in audit(pid)


def dose_state(dose_id):
    s = SessionLocal()
    try:
        return s.query(Dose).get(dose_id).state
    finally:
        s.close()


def audit(pid):
    s = SessionLocal()
    try:
        return [a.action for a in s.query(AuditLog).filter_by(patient_id=pid).all()]
    finally:
        s.close()


def test_the_label_rule_is_asymmetric_cipro_first_needs_only_two_hours_before_calcium(db):
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        taken_at = _taken(pid, "Ciprofloxacin", 1)
        calcium = _due(pid, "Shelcal")
        r = client.post(f"/doses/{calcium}/take")
        assert r.status_code == 409 and datetime.fromisoformat(r.json()["earliest"]) == taken_at + timedelta(hours=2)
    with TestClient(app) as client2:
        pid2, _ = new_patient(client2)
        _taken(pid2, "Ciprofloxacin", 3)
        calcium2 = _due(pid2, "Shelcal")
        assert client2.post(f"/doses/{calcium2}/take").status_code == 200           # 3 h after cipro: fine


def test_a_pair_with_no_rule_and_no_ai_is_allowed_and_not_claimed_safe(db):
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _taken(pid, "Iron", 0.2)
        calcium = _due(pid, "Shelcal")
        assert client.post(f"/doses/{calcium}/take").status_code == 200


def test_an_ai_pair_answer_fills_the_gap_where_the_curated_list_is_silent(db, monkeypatch):
    seen = []

    def fake_pair(db_, a, b, **k):
        seen.append((a, b))
        return {"hours": 2.0, "basis": "ai_estimate", "quote": None, "applies_when": "absorption"}
    monkeypatch.setattr(gap_ai, "pair_gap", fake_pair)
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _taken(pid, "Iron", 0.5)
        calcium = _due(pid, "Shelcal")
        r = client.post(f"/doses/{calcium}/take")
        assert r.status_code == 409
        b = r.json()
        assert b["code"] == "spacing" and b["source"] == "ai_estimate" and b["can_override"] is True
        assert "AI estimate" in b["detail"] and "pharmacist" in b["detail"]
        assert client.post(f"/doses/{calcium}/take", json={"override": True}).status_code == 200
    assert seen


def test_a_curated_rule_always_wins_over_a_looser_ai_answer(db, monkeypatch):
    def must_not_be_asked(*a, **k):
        raise AssertionError("AI must not be consulted when a curated label rule covers the pair")
    monkeypatch.setattr(gap_ai, "pair_gap", must_not_be_asked)
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _taken(pid, "Shelcal", 1)
        cipro = _due(pid, "Ciprofloxacin")
        assert client.post(f"/doses/{cipro}/take").json()["code"] == "spacing"


def test_an_ai_estimated_same_medicine_gap_can_be_overridden_a_label_gap_cannot(db, monkeypatch):
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        t = now_local() - timedelta(hours=1)
        _, (first, second) = add_medicine(pid, "Metformin", [t, now_local()], states=["taken", "pending"],
                                          acted=[t, None], times=["08:30", "20:30"])
        monkeypatch.setattr(gap_ai, "single_gap", lambda *a, **k: {"min_hours": 8, "basis": "ai_estimate", "quote": None})
        r = client.post(f"/doses/{second}/take")
        assert r.status_code == 409 and r.json()["can_override"] is True and r.json()["source"] == "ai_estimate"
        monkeypatch.setattr(gap_ai, "single_gap", lambda *a, **k: {"min_hours": 8, "basis": "label", "quote": "q"})
        r2 = client.post(f"/doses/{second}/take", json={"override": True})
        assert r2.status_code == 409 and r2.json()["can_override"] is False and r2.json()["source"] == "label"


def test_an_ai_failure_never_blocks_or_breaks_a_tap(db, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(gap_ai, "single_gap", broken)
    monkeypatch.setattr(gap_ai, "pair_gap", broken)
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _taken(pid, "Iron", 0.2)
        d = _due(pid, "Shelcal")
        assert client.post(f"/doses/{d}/take").status_code == 200


def test_the_dashboard_labels_each_dose_with_its_slot_and_food_instruction(db):
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        t = now_local()
        add_medicine(pid, "Pantop", [t], times=[t.strftime("%H:%M")], slots=["morning"], food="before")
        add_medicine(pid, "Dolo", [t + timedelta(minutes=5)], times=["99:99"], slots=None)
        today = client.get(f"/patients/{pid}/dashboard").json()["today_doses"]
        pantop = next(d for d in today if d["medicine_name"] == "Pantop")
        assert pantop["slot"] == "morning" and pantop["food"] == "before"
        assert next(d for d in today if d["medicine_name"] == "Dolo")["slot"] is None


def test_warm_up_fills_the_cache_for_each_medicine_and_pair_and_stops_when_the_provider_keeps_failing(db, ai_on, monkeypatch):
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        for n in ("Metformin", "Shelcal", "Iron"):
            add_medicine(pid, n, [now_local() + timedelta(hours=3)], times=["08:30"])
        monkeypatch.setattr(gap_ai, "default_label_fn", lambda g, f: "label")
        monkeypatch.setattr(gap_ai, "default_llm_fn",
                            lambda system, user: {"min_hours_between_doses": 24, "separate_hours": 2, "confidence": "high"})
        out = gap_ai.warm_patient(pid, pause=0)
        assert out["looked_up"] == 6 and out["failed"] == 0                  # 3 singles + 3 pairs
        assert db.query(AIGapCache).filter_by(kind="pair").count() == 3
        assert gap_ai.warm_patient(pid, pause=0)["failed"] == 0              # second run: all cache hits

        db.query(AIGapCache).delete()
        db.commit()
        monkeypatch.setattr(gap_ai, "default_llm_fn", lambda s, u: (_ for _ in ()).throw(RuntimeError("429")))
        monkeypatch.setattr(gap_ai.time, "sleep", lambda s: None)
        assert gap_ai.warm_patient(pid, pause=0)["failed"] == 3             # gave up after three failures, did not hammer
