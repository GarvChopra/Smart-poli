import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from db import SessionLocal, Dose, Medicine, Prescription, SymptomCheck  # noqa: E402
from conftest import register_and_login  # noqa: E402
import voice_assistant  # noqa: E402


# ---------------------------------------------------------------- fake Groq

def _call(name, args, call_id="c1"):
    return SimpleNamespace(id=call_id, type="function",
                           function=SimpleNamespace(name=name, arguments=args if isinstance(args, str) else json.dumps(args)))


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def _guard(reply):
    """A scripted answer from the grounding check."""
    m = _msg(content=reply)
    m.is_guard = True
    return m


def _is_guard_call(kwargs):
    return kwargs["messages"][0]["content"].startswith("You check a health assistant")


def install_fake_groq(monkeypatch, script, calls=None):
    """`script` is a list of responses, or callables(messages) -> response."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    steps = list(script)

    class FakeCompletions:
        def create(self, **kwargs):
            if calls is not None:
                calls.append(kwargs)
            if _is_guard_call(kwargs) and (not steps or not getattr(steps[0], "is_guard", False)):
                # the grounding check: unless a test scripts it, pass the draft through unchanged
                return _msg(content=json.loads(kwargs["messages"][-1]["content"])["draft_reply"])
            step = steps.pop(0)
            if isinstance(step, Exception):
                raise step
            return step(kwargs["messages"]) if callable(step) else step

    class FakeClient:
        def __init__(self, api_key=None, **_):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("groq.Groq", FakeClient)


def install_fake_gemini(monkeypatch, script, calls=None):
    """`script` is a list of responses, or callables(messages) -> response.
    Mimics Gemini's OpenAI-compatible endpoint, which voice_assistant reaches
    through the plain `openai` client."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    steps = list(script)

    class FakeCompletions:
        def create(self, **kwargs):
            if calls is not None:
                calls.append(kwargs)
            if _is_guard_call(kwargs) and (not steps or not getattr(steps[0], "is_guard", False)):
                # the grounding check: unless a test scripts it, pass the draft through unchanged
                return _msg(content=json.loads(kwargs["messages"][-1]["content"])["draft_reply"])
            step = steps.pop(0)
            if isinstance(step, Exception):
                raise step
            return step(kwargs["messages"]) if callable(step) else step

    class FakeClient:
        def __init__(self, api_key=None, base_url=None, **_):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeClient)


def _setup(client, due_now=1):
    register_and_login(client)
    pid = client.post("/patients", json={"name": "Ramesh Kumar", "emergency_contact": "Anita, +91 98765 43210"}).json()["id"]
    pres = client.post("/prescriptions", json={"patient_id": pid, "lines": ["Tab Metformin 500mg 1-0-1 x5d",
                                                                           "Tab Telma 40mg 1-0-0 x5d"]}).json()
    client.post(f"/prescriptions/{pres['prescription_id']}/confirm")
    db = SessionLocal()
    try:
        doses = (db.query(Dose).join(Medicine).join(Prescription).filter(Prescription.patient_id == pid)
                 .order_by(Dose.id).all())
        now = datetime.now()
        for i, d in enumerate(doses):
            d.scheduled_at = now - timedelta(minutes=20) if i < due_now else now + timedelta(hours=5 + i)
            d.state = "pending"
        db.commit()
        due_ids = [d.id for d in doses[:due_now]]
    finally:
        db.close()
    return pid, due_ids


def _turn(client, pid, text, **extra):
    body = {"text": text, "lang": "en", "client_time": datetime.now().astimezone().isoformat(),
            "history": [], "state": {}, **extra}
    return client.post(f"/patients/{pid}/voice/turn", json=body)


def _dose_state(dose_id):
    db = SessionLocal()
    try:
        return db.query(Dose).get(dose_id).state
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    voice_assistant.reset_rate_limits()


# ---------------------------------------------------------------- tests

def test_if_groq_fails_the_safety_net_still_gives_help(monkeypatch):
    calls = []
    install_fake_groq(monkeypatch, [RuntimeError("groq down")], calls)
    with TestClient(app) as client:
        pid, _ = _setup(client)
        r = _turn(client, pid, "papa behosh ho gaye")
        assert r.status_code == 200, r.text
        body = r.json()
    assert len(calls) == 1  # Groq was tried first
    assert body["actions"][0]["type"] == "emergency"
    assert "112" in body["reply"]
    db = SessionLocal()
    try:
        assert db.query(SymptomCheck).filter(SymptomCheck.patient_id == pid,
                                             SymptomCheck.severity == "EMERGENCY").count() == 1
    finally:
        db.close()


def test_groq_marks_dose_through_router(monkeypatch):
    with TestClient(app) as client:
        pid, due = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("get_today_schedule", {})]),
            _msg(tool_calls=[_call("mark_dose_taken", {"dose_id": due[0]}, "c2")]),
            _msg(content="Done. Morning dose recorded."),
        ])
        body = _turn(client, pid, "Maine subah wali le li").json()
    assert body["reply"] == "Done. Morning dose recorded."
    assert body["actions"][0]["type"] == "dose_taken"
    assert _dose_state(due[0]) == "taken"


def test_model_cannot_downgrade_rule_engine_emergency(monkeypatch):
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("update_symptom_check",
                                   {"symptom_ids": ["chest_pain"], "answers": {"difficulty_breathing": True}})]),
            _msg(content="This is nothing serious, just rest."),
        ])
        body = _turn(client, pid, "thoda theek nahi lag raha").json()
    assert body["actions"][-1]["type"] == "emergency"
    assert "nothing serious" not in body["reply"]
    assert "112" in body["reply"]


def test_symptom_state_round_trips_between_turns(monkeypatch):
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("update_symptom_check", {"symptom_ids": ["headache"], "symptom_labels": ["sir dard"]})]),
            _msg(content="Ye kab se ho raha hai?"),
        ])
        body = _turn(client, pid, "Mere sir mein bahut dard ho raha hai").json()
    assert body["reply"] == "Ye kab se ho raha hai?"
    assert body["state"]["symptom"]["ids"] == ["headache"]


def test_bad_tool_calls_do_not_break_the_turn(monkeypatch):
    with TestClient(app) as client:
        pid, due = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("drop_database", {}), _call("mark_dose_taken", "{not json", "c2")]),
            _msg(content="Sorry, could you say that again?"),
        ])
        r = _turn(client, pid, "hmm")
    assert r.status_code == 200
    assert r.json()["reply"] == "Sorry, could you say that again?"
    assert _dose_state(due[0]) == "pending"


def test_groq_failure_falls_back(monkeypatch):
    with TestClient(app) as client:
        pid, due = _setup(client)
        install_fake_groq(monkeypatch, [RuntimeError("groq down")])
        body = _turn(client, pid, "maine dawai le li").json()
    assert body["mode"] == "fallback"
    assert body["actions"][0]["type"] == "choose_dose"


def test_fallback_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(app) as client:
        pid, due = _setup(client)
        body = _turn(client, pid, "maine dawai le li").json()
        assert body["mode"] == "fallback"
        assert body["actions"][0]["type"] == "choose_dose"

        body = _turn(client, pid, "meri agli medicine kab hai").json()
        assert "Telma" in body["reply"] or "Metformin" in body["reply"]

        body = _turn(client, pid, "timeline dikhao").json()
        assert {"type": "navigate", "screen": "timeline"} in body["actions"]

        body = _turn(client, pid, "mujhe chakkar aa rahe hain").json()
        assert {"type": "navigate", "screen": "triage"} in body["actions"]


def test_fallback_asks_when_two_doses_are_due(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(app) as client:
        pid, due = _setup(client, due_now=2)
        body = _turn(client, pid, "I took my medicine").json()
    assert body["actions"][0]["type"] == "choose_dose"
    assert {o["dose_id"] for o in body["actions"][0]["options"]} == set(due)
    assert all(_dose_state(d) == "pending" for d in due)


def test_validation_and_permissions(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(app) as client:
        pid, _ = _setup(client)
        assert _turn(client, pid, "").status_code == 422
        assert _turn(client, pid, "   ").status_code == 422
        assert _turn(client, pid, "x" * 1001).status_code == 422
        assert _turn(client, pid, "hi", lang="fr").status_code == 422
        register_and_login(client)
        assert _turn(client, pid, "hi").status_code == 403


def test_rate_limit(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(voice_assistant, "RATE_LIMIT_PER_MINUTE", 2)
    with TestClient(app) as client:
        pid, _ = _setup(client)
        assert _turn(client, pid, "hello").status_code == 200
        assert _turn(client, pid, "hello").status_code == 200
        assert _turn(client, pid, "hello").status_code == 429


def test_voice_available(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(app) as client:
        register_and_login(client)
        assert client.get("/voice/available").json() == {"available": False, "stt": False}


def test_short_voice_link_redirects():
    with TestClient(app) as client:
        r = client.get("/voice", follow_redirects=False)
        assert r.status_code in (302, 307)
        assert r.headers["location"] == "/static/voice.html"
        assert client.get("/static/voice.html").status_code == 200
        assert client.get("/static/manifest.webmanifest").status_code == 200


def test_empty_model_reply_gets_one_forced_text_answer(monkeypatch):
    calls = []
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("get_next_dose", {})]),
            _msg(content=""),
            _msg(content="Aapki agli dawai 8:30 baje hai."),
        ], calls)
        body = _turn(client, pid, "mera agla dose kya hai").json()
    assert body["reply"] == "Aapki agli dawai 8:30 baje hai."
    assert calls[-1]["tool_choice"] == "none"


def test_symptom_labels_are_not_duplicated(monkeypatch):
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("update_symptom_check", {"symptom_ids": ["headache"], "symptom_labels": ["sir dard"]})]),
            _msg(content="Kab se?"),
        ])
        body = _turn(client, pid, "sir dard", state={"symptom": {"ids": ["headache"], "labels": ["sir dard"], "answers": {}}}).json()
    assert body["state"]["symptom"]["labels"] == ["sir dard"]


def test_open_symptom_questions_are_in_every_turns_prompt(monkeypatch):
    """Question ids only arrive in a tool result, which the next turn doesn't
    see — so each turn's system prompt must carry the check in progress, or
    the model can't record the patient's answers."""
    calls = []
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_groq(monkeypatch, [_msg(content="Kya aapko confusion ho raha hai?")], calls)
        _turn(client, pid, "nahi, dheere dheere shuru hua",
              state={"symptom": {"ids": ["headache"], "labels": ["sir dard"], "answers": {"vision_changes": False}}})
    system = calls[0]["messages"][0]["content"]
    assert "worst_ever_sudden" in system and "confusion" in system
    assert "vision_changes" in system and "false" in system.lower()


# ---------------------------------------------------------------- review fixes

@pytest.mark.parametrize("text", [
    "I have not taken my medicine yet", "Maine dawai nahi li hai", "have I taken my medicine today?",
    "kya maine dawai le li?", "I had my breakfast this morning", "I took a walk this evening",
    "raat ko khana kha liya", "my son took me to the doctor this morning",
])
def test_fallback_never_marks_a_dose_on_negations_questions_or_unrelated_text(monkeypatch, text):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(app) as client:
        pid, due = _setup(client)
        body = _turn(client, pid, text).json()
    assert _dose_state(due[0]) == "pending", text
    assert not any(a["type"] == "dose_taken" for a in body["actions"])


def test_fallback_confirms_before_marking_even_one_dose(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(app) as client:
        pid, due = _setup(client)
        body = _turn(client, pid, "maine dawai le li").json()
    assert body["actions"][0]["type"] == "choose_dose"
    assert [o["dose_id"] for o in body["actions"][0]["options"]] == due
    assert _dose_state(due[0]) == "pending"


def test_fallback_when_sentence_about_symptom_goes_to_symptom_check(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(app) as client:
        pid, _ = _setup(client)
        body = _turn(client, pid, "when I stand up I get dizzy").json()
    assert {"type": "navigate", "screen": "triage"} in body["actions"]


@pytest.mark.parametrize("bad_state", [
    {"symptom": {"answers": [1]}}, {"symptom": {"labels": 5}}, {"symptom": {"ids": 5}},
    {"symptom": "x"}, {"symptom": {"ids": ["headache"], "answers": {"confusion": True}, "asked": 7}},
])
def test_malformed_state_never_breaks_the_emergency_path(monkeypatch, bad_state):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(app) as client:
        pid, _ = _setup(client)
        r = _turn(client, pid, "papa behosh ho gaye", state=bad_state)
    assert r.status_code == 200
    assert r.json()["actions"][0]["type"] == "emergency"


def test_tool_crash_after_emergency_still_speaks_emergency_reply(monkeypatch):
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[
                _call("update_symptom_check", {"symptom_ids": ["chest_pain"], "answers": {"difficulty_breathing": True}}),
                _call("mark_dose_taken", {"dose_id": 1e30}, "c2"),
            ]),
        ])
        r = _turn(client, pid, "theek nahi lag raha")
    assert r.status_code == 200
    assert "112" in r.json()["reply"]
    assert r.json()["actions"][-1]["type"] == "emergency"



# ---------------------------------------------------------------- grounding check

def test_medical_reply_is_checked_against_the_evidence(monkeypatch):
    calls = []
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("search_clinical_guidance", {"query": "cough"})]),
            _msg(content="MoHFW ke hisaab se steam lijiye aur inhaler lijiye."),
            _guard("Aaram kijiye aur khoob paani piyiye. Kab se khansi hai?"),
        ], calls)
        body = _turn(client, pid, "mujhe khansi ho rahi hai kya karu").json()
    assert body["reply"] == "Aaram kijiye aur khoob paani piyiye. Kab se khansi hai?"
    guard_call = next(c for c in calls if _is_guard_call(c))
    payload = json.loads(guard_call["messages"][-1]["content"])
    assert payload["draft_reply"].startswith("MoHFW ke hisaab se steam")
    assert any("Coughing is a reflex" in json.dumps(e) for e in payload["evidence"])


def test_pure_medicine_schedule_turn_skips_the_check(monkeypatch):
    calls = []
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("get_next_dose", {})]),
            _msg(content="Agli dawai 8 baje."),
        ], calls)
        body = _turn(client, pid, "mera agla dose kya hai").json()
    assert body["reply"] == "Agli dawai 8 baje."
    assert not any(_is_guard_call(c) for c in calls)


def test_if_the_check_fails_the_unchecked_advice_is_not_spoken(monkeypatch):
    with TestClient(app) as client:
        pid, _ = _setup(client)
        guard_error = RuntimeError("guard down")
        guard_error.is_guard = True
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("search_clinical_guidance", {"query": "cough"})]),
            _msg(content="Steam lijiye."),
            guard_error,
        ])
        body = _turn(client, pid, "khansi hai").json()
    assert "Steam" not in body["reply"]


def test_if_the_check_fails_the_sourced_card_steps_are_spoken_instead(monkeypatch):
    with TestClient(app) as client:
        pid, _ = _setup(client)
        guard_error = RuntimeError("json_validate_failed")
        guard_error.is_guard = True
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("update_symptom_check", {"symptom_labels": ["khansi"]}),
                             _call("finish_symptom_check", {}, "c2")]),
            _msg(content="Steam lijiye aur inhaler lijiye."),
            guard_error,
        ])
        body = _turn(client, pid, "khansi hai").json()
    assert "Steam" not in body["reply"] and "inhaler" not in body["reply"].lower()
    assert "Rest" in body["reply"] or "Aaram" in body["reply"]


def test_a_rate_limited_model_falls_back_to_the_next_one(monkeypatch):
    class RateLimited(Exception):
        status_code = 429
    models = []
    with TestClient(app) as client:
        pid, _ = _setup(client)
        monkeypatch.setenv("GROQ_VOICE_MODELS", "big-model,small-model")

        def by_model(messages):
            raise AssertionError("unused")
        install_fake_groq(monkeypatch, [RateLimited("tokens per day"), _msg(content="Agli dawai 8 baje.")], None)
        import groq
        real = groq.Groq

        class Spy(real):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                inner = self.chat.completions.create

                def create(**kw):
                    models.append(kw["model"])
                    return inner(**kw)
                self.chat.completions.create = create
        monkeypatch.setattr("groq.Groq", Spy)
        body = _turn(client, pid, "mera agla dose kya hai").json()
    assert body["reply"] == "Agli dawai 8 baje."
    assert models[:2] == ["big-model", "small-model"]


def test_the_grounding_check_uses_the_lighter_model_by_default(monkeypatch):
    calls = []
    monkeypatch.delenv("GROQ_GUARD_MODEL", raising=False)
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_groq(monkeypatch, [
            _msg(tool_calls=[_call("search_clinical_guidance", {"query": "cough"})]),
            _msg(content="Aaram kijiye."),
        ], calls)
        _turn(client, pid, "khansi hai")
    guard = next(c for c in calls if _is_guard_call(c))
    assert guard["model"] == "openai/gpt-oss-20b"


# ---------------------------------------------------------------- Gemini fallback

def test_gemini_is_used_when_only_a_gemini_key_is_configured(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    calls = []
    with TestClient(app) as client:
        pid, _ = _setup(client)
        install_fake_gemini(monkeypatch, [_msg(content="Agli dawai 8 baje.")], calls)
        body = _turn(client, pid, "mera agla dose kya hai").json()
    assert body["reply"] == "Agli dawai 8 baje."
    assert calls[0]["model"] == "gemini-flash-latest"


def test_falls_back_to_gemini_when_every_groq_model_is_rate_limited(monkeypatch):
    class RateLimited(Exception):
        status_code = 429
    models = []
    with TestClient(app) as client:
        pid, _ = _setup(client)
        monkeypatch.setenv("GROQ_VOICE_MODELS", "big-model,small-model")
        install_fake_groq(monkeypatch, [RateLimited("tokens per day"), RateLimited("tokens per day")], None)

        def record_model(messages):
            return _msg(content="Agli dawai 8 baje.")
        install_fake_gemini(monkeypatch, [record_model], None)
        import openai
        real = openai.OpenAI

        class Spy(real):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                inner = self.chat.completions.create

                def create(**kw):
                    models.append(kw["model"])
                    return inner(**kw)
                self.chat.completions.create = create
        monkeypatch.setattr("openai.OpenAI", Spy)
        body = _turn(client, pid, "mera agla dose kya hai").json()
    assert body["reply"] == "Agli dawai 8 baje."
    assert models == ["gemini-flash-latest"]


def test_voice_available_is_true_with_only_a_gemini_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    with TestClient(app) as client:
        register_and_login(client)
        assert client.get("/voice/available").json()["available"] is True


# ---------------------------------------------------------------- pre-LLM known-action router

def test_next_dose_question_never_calls_the_llm_at_all(monkeypatch):
    def boom(messages):
        raise AssertionError("the LLM should never be called for a mechanical next-dose question")
    install_fake_groq(monkeypatch, [boom])
    with TestClient(app) as client:
        pid, _ = _setup(client)
        body = _turn(client, pid, "agli dawai kab hai").json()
    assert "8" in body["reply"] or body["mode"] == "fallback"
    assert body["mode"] == "fallback"


def test_mark_dose_taken_never_calls_the_llm_at_all(monkeypatch):
    def boom(messages):
        raise AssertionError("the LLM should never be called for a mechanical dose-taken report")
    install_fake_groq(monkeypatch, [boom])
    with TestClient(app) as client:
        pid, due = _setup(client)
        body = _turn(client, pid, "maine dawai le li").json()
    assert body["mode"] == "fallback"
    assert body["actions"][0]["type"] == "choose_dose"


def test_symptom_text_still_goes_to_the_llm_not_the_router(monkeypatch):
    calls = []
    install_fake_groq(monkeypatch, [
        _msg(tool_calls=[_call("update_symptom_check", {"symptom_ids": ["cough"], "symptom_labels": ["khansi"]})]),
        _msg(content="Kitne din se khansi hai?"),
    ], calls)
    with TestClient(app) as client:
        pid, _ = _setup(client)
        body = _turn(client, pid, "mujhe khansi ho rahi hai").json()
    assert len(calls) >= 1  # the router didn't intercept it; the LLM actually ran
    assert body["mode"] == "assistant"


def test_known_action_text_mid_symptom_check_still_goes_to_the_llm(monkeypatch):
    """A phrase that would normally be a mechanical next-dose question must not
    hijack an in-progress symptom conversation — that continuity matters more
    than the shortcut."""
    calls = []
    install_fake_groq(monkeypatch, [_msg(content="Theek hai, kya aapko bukhar bhi hai?")], calls)
    with TestClient(app) as client:
        pid, _ = _setup(client)
        state = {"symptom": {"ids": ["cough"], "labels": ["khansi"], "answers": {}, "asked": []}}
        body = _turn(client, pid, "agli dawai kab hai", state=state).json()
    assert len(calls) >= 1  # the LLM ran; the router was skipped
    assert body["mode"] == "assistant"
