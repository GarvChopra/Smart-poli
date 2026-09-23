"""
Tests for the optional free-text -> structured-symptom LLM assist.

Mocks the Groq call (no network dependency in the test suite, same
approach as test_ocr_endpoint.py mocking TrOCR) — but the validation logic
these tests actually exercise is real: any symptom_id/question_id the
"model" returns is checked against the real ruleset before being trusted.
That validation, not the LLM call itself, is the part that matters for
safety and is what must never regress.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_helper  # noqa: E402
from triage import load_ruleset  # noqa: E402

RULESET = load_ruleset()


class _FakeGroqResponse:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


def test_valid_llm_output_is_passed_through(monkeypatch):
    class FakeClient:
        def __init__(self, api_key): pass
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return _FakeGroqResponse(
                        '{"symptom_ids": ["chest_pain"], "answers": {"difficulty_breathing": true}}'
                    )

    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")
    monkeypatch.setattr("groq.Groq", FakeClient)

    result = llm_helper.interpret_free_text("I have chest pain and can't breathe", RULESET)
    assert result["symptom_ids"] == ["chest_pain"]
    assert result["answers"] == {"difficulty_breathing": True}


def test_hallucinated_symptom_id_is_dropped(monkeypatch):
    class FakeClient:
        def __init__(self, api_key): pass
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return _FakeGroqResponse(
                        '{"symptom_ids": ["chest_pain", "not_a_real_symptom"], "answers": {}}'
                    )

    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")
    monkeypatch.setattr("groq.Groq", FakeClient)

    result = llm_helper.interpret_free_text("some text", RULESET)
    assert result["symptom_ids"] == ["chest_pain"]


def test_hallucinated_question_id_for_a_real_symptom_is_dropped(monkeypatch):
    class FakeClient:
        def __init__(self, api_key): pass
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return _FakeGroqResponse(
                        '{"symptom_ids": ["chest_pain"], '
                        '"answers": {"difficulty_breathing": true, "not_a_real_question": true}}'
                    )

    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")
    monkeypatch.setattr("groq.Groq", FakeClient)

    result = llm_helper.interpret_free_text("some text", RULESET)
    assert result["answers"] == {"difficulty_breathing": True}


def test_non_boolean_answer_value_is_dropped(monkeypatch):
    class FakeClient:
        def __init__(self, api_key): pass
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return _FakeGroqResponse(
                        '{"symptom_ids": ["chest_pain"], "answers": {"difficulty_breathing": "maybe"}}'
                    )

    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")
    monkeypatch.setattr("groq.Groq", FakeClient)

    result = llm_helper.interpret_free_text("some text", RULESET)
    assert result["answers"] == {}


def test_unavailable_without_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert llm_helper.is_available() is False
    try:
        llm_helper.interpret_free_text("chest pain", RULESET)
        assert False, "should have raised LLMUnavailable"
    except llm_helper.LLMUnavailable:
        pass


def test_endpoint_returns_503_when_key_missing(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with TestClient(main.app) as client:
        r = client.post("/triage/interpret-free-text", json={"text": "chest pain"})
        assert r.status_code == 503
