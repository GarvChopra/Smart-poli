"""'What is this medicine for?' - label-grounded plain summaries, graded, cached, never required."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import medicine_info as mi  # noqa: E402
from db import AIGapCache, SessionLocal, init_db  # noqa: E402

LABEL = "Telmisartan tablets, USP are indicated for the treatment of hypertension, to lower blood pressure. " * 2


@pytest.fixture()
def db(monkeypatch):
    init_db()
    s = SessionLocal()
    s.query(AIGapCache).filter(AIGapCache.kind == "info").delete()
    s.commit()
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("SMARTPOLI_AI_INFO", "1")
    monkeypatch.setattr(mi, "warm_patient", lambda *a, **k: 0)        # no background lookups with the fake key
    yield s
    s.close()


def good(quote="Telmisartan tablets, USP are indicated for the treatment of hypertension, to lower blood pressure."):
    return {"generic_name": "telmisartan", "what_for": "Lowers high blood pressure", "conditions": ["High blood pressure"],
            "side_effects": ["Dizziness"], "take_care": ["Not for use in pregnancy"], "quote": quote}


def test_a_quote_really_in_the_label_is_graded_label_otherwise_ai_estimate():
    assert mi.grade(good(), LABEL, "telmisartan")["source"] == "label"
    assert mi.grade(good("This sentence is not in the label at all, honest."), LABEL, "telmisartan")["source"] == "ai_estimate"
    assert mi.grade(good(), "", "telmisartan")["source"] == "ai_estimate"             # no label found -> never "label"


def test_unusable_answers_are_dropped_and_lists_are_bounded():
    assert mi.grade({"what_for": None}, LABEL, "x") is None
    assert mi.grade({"what_for": "ab"}, LABEL, "x") is None
    big = mi.grade({"what_for": "Lowers blood pressure", "conditions": ["a" * 200] * 9, "side_effects": ["x"] * 20}, "", "x")
    assert len(big["conditions"]) == 3 and len(big["conditions"][0]) <= 60 and len(big["side_effects"]) == 4


def test_lookup_is_cached_so_the_sources_are_asked_once(db):
    calls = {"label": 0, "llm": 0}

    def label_fn(g):
        calls["label"] += 1
        return LABEL

    def llm_fn(system, user):
        calls["llm"] += 1
        return good()

    a = mi.info_for(db, "Telma 40", label_fn=label_fn, llm_fn=llm_fn)
    b = mi.info_for(db, "Telma 40", label_fn=label_fn, llm_fn=llm_fn)
    assert a["what_for"] == "Lowers high blood pressure" and a["generic"] == "telmisartan" and a["source"] == "label"
    assert b == a and calls == {"label": 1, "llm": 1}
    assert "official" in a["note"].lower()


def test_failures_and_switch_off_mean_no_information_not_an_error(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    assert mi.info_for(db, "Telma 40", label_fn=boom, llm_fn=boom) is None
    assert mi.info_for(db, "Telma 40", allow_network=False) is None                   # nothing cached, network not allowed
    monkeypatch.setenv("SMARTPOLI_AI_INFO", "0")
    assert mi.info_for(db, "Dolo 650", label_fn=lambda g: LABEL, llm_fn=lambda s, u: good()) is None


def test_an_unknown_brand_gets_a_second_pass_from_the_real_ingredients_label(db):
    seen = []

    def label_fn(g):
        seen.append(g)
        return LABEL if g == "telmisartan" else ""

    def llm_fn(system, user):
        return good() if "telmisartan" in user and "indicated for" in user else {**good(), "quote": None, "generic_name": "telmisartan"}

    out = mi.info_for(db, "Zzbrand 40", label_fn=label_fn, llm_fn=llm_fn)
    assert out["source"] == "label" and "telmisartan" in seen


def test_conditions_are_merged_across_medicines_and_called_a_guess(db):
    from timing_helpers import new_patient
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        for n in ("Telmisartan", "Amlodipine"):
            client.post("/medicines/manual", json={"patient_id": pid, "name": n, "times": ["08:00"], "duration_days": 5})
        info = lambda name: {**good(), "generic_name": name.lower()}  # noqa: E731
        out = mi.summarize_patient(db, pid, allow_network=True, label_fn=lambda g: LABEL, llm_fn=lambda s, u: info("x"))
        assert out["conditions"] == ["High blood pressure"]                       # two medicines, one condition, shown once
        assert "not a diagnosis" in out["disclaimer"]
