"""
Tests for the rule-based triage engine.

test_answers_never_decrease_severity is the most important test in the
project (CLAUDE.md section 10, rule 1): it walks every possible answer
combination, for every symptom, against every possible starting severity,
and asserts the result never ranks lower than where it started. A wrong
triage rule is dangerous, not merely annoying — this is the one place that
must be proven exhaustively rather than spot-checked.
"""

import itertools
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from triage import (  # noqa: E402
    load_ruleset, evaluate_symptom, evaluate_check, next_question,
    SEVERITY_ORDER, escalate,
)

RULESET = load_ruleset()


def _rank(sev):
    return SEVERITY_ORDER.index(sev)


# ------------------------------------------------------------ THE critical test

def test_answers_never_decrease_severity():
    for symptom_id, symptom in RULESET["symptoms"].items():
        question_ids = [q["id"] for q in symptom["questions"]]
        for combo in itertools.product([True, False], repeat=len(question_ids)):
            answers = dict(zip(question_ids, combo))
            for starting_severity in SEVERITY_ORDER:
                result = evaluate_symptom(RULESET, symptom_id, answers, starting_severity=starting_severity)
                assert _rank(result["severity"]) >= _rank(starting_severity), (
                    f"{symptom_id} with answers {answers} starting at {starting_severity} "
                    f"produced {result['severity']} — severity decreased!"
                )


def test_escalate_is_max_over_ordered_enum():
    assert escalate("LOW", "MODERATE") == "MODERATE"
    assert escalate("MODERATE", "LOW") == "MODERATE"  # false answer never lowers it
    assert escalate("EMERGENCY", "LOW") == "EMERGENCY"
    assert escalate("LOW", "LOW") == "LOW"


# ------------------------------------------------------------ the other three safety rules

def test_stop_asking_once_emergency():
    answers = {"difficulty_breathing": True}
    q = next_question(RULESET, "chest_pain", answers, current_severity="EMERGENCY")
    assert q is None


def test_keeps_asking_while_not_emergency():
    q = next_question(RULESET, "chest_pain", {}, current_severity="MODERATE")
    assert q is not None
    assert q["id"] == "difficulty_breathing"


def test_first_result_before_any_follow_up_is_real():
    r = evaluate_symptom(RULESET, "fever", {})
    assert r["severity"] == "LOW"
    assert r["reasons"] == []
    assert r["action"]


def test_explanation_lists_the_rules_that_fired_in_order():
    r = evaluate_symptom(RULESET, "chest_pain", {
        "difficulty_breathing": True,
        "fainting": True,
        "pain_radiating": False,
        "sweating": False,
    })
    assert r["severity"] == "EMERGENCY"
    assert r["reasons"] == [
        "Chest pain with difficulty breathing",
        "Chest pain with fainting or loss of consciousness",
    ]


# ------------------------------------------------------------ specific scenarios

def test_chest_pain_with_difficulty_breathing_is_emergency():
    r = evaluate_symptom(RULESET, "chest_pain", {"difficulty_breathing": True})
    assert r["severity"] == "EMERGENCY"
    assert "112" in r["action"]


def test_fever_alone_is_low():
    r = evaluate_symptom(RULESET, "fever", {"stiff_neck": False, "rash_with_fever": False,
                                             "temp_above_103": False, "duration_over_3_days": False})
    assert r["severity"] == "LOW"


def test_multi_symptom_check_takes_the_highest_severity():
    r = evaluate_check(RULESET, ["fever", "chest_pain"], {"difficulty_breathing": True})
    assert r["severity"] == "EMERGENCY"


def test_no_diagnosis_language_in_action_text():
    for severity, info in [(s, evaluate_symptom(RULESET, "chest_pain", {}, starting_severity=s))
                            for s in SEVERITY_ORDER]:
        forbidden = ["heart attack", "myocardial", "diagnosis", "you have"]
        text = info["action"].lower()
        assert not any(f in text for f in forbidden)


def test_ruleset_version_is_stamped_on_every_result():
    r = evaluate_symptom(RULESET, "headache", {})
    assert r["ruleset_version"] == RULESET["ruleset_version"]
