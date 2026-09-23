"""
SmartPoli — Feature 3: rule-based symptom triage.

Deterministic. The severity decision is NEVER LLM-generated — it is a table
lookup over a versioned JSON ruleset (triage_rules.json). This file only
knows how to walk that table; it contains no medical judgement of its own.

The four rules that make this safe (CLAUDE.md section 10):
  1. Answers escalate only — severity = max(current, rule_result) over an
     ordered enum. A `false` answer is "no new information", never "less
     urgent than we thought". See tests/test_triage.py::test_answers_never_decrease_severity
     — the most important test in the project.
  2. Stop asking once EMERGENCY (see `next_question`).
  3. Answers are optional — the base severity with zero answers is itself a
     real, valid result.
  4. Explain with the rules that fired ("because" strings), in order. Never
     a diagnosis, never a condition name.
"""

import json
import os
from typing import Optional

SEVERITY_ORDER = ["LOW", "MODERATE", "EMERGENCY"]

_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "triage_rules.json")

ACTIONS = {
    "EMERGENCY": {
        "action": "Seek emergency medical attention immediately. Call 112.",
        "route": "emergency",
        "tel": "112",
    },
    "MODERATE": {
        "action": "Book a routine consultation within 24-48 hours.",
        "route": "book_doctor",
    },
    "LOW": {
        "action": "Continue monitoring. Recheck if symptoms worsen.",
        "route": "monitor",
    },
}


def load_ruleset(path: Optional[str] = None) -> dict:
    with open(path or _RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _rank(severity: str) -> int:
    return SEVERITY_ORDER.index(severity)


def escalate(current: str, new: str) -> str:
    """severity = max(current, new) over the ordered enum. Never decreases."""
    return current if _rank(current) >= _rank(new) else new


def list_symptoms(ruleset: dict) -> list[dict]:
    return [{"id": sid, "label": s["label"]} for sid, s in ruleset["symptoms"].items()]


def next_question(ruleset: dict, symptom_id: str, answers: dict, current_severity: str) -> Optional[dict]:
    """
    Which question to ask next, or None if there isn't one — either every
    question has been answered, or `current_severity` is already EMERGENCY
    (rule 2: that person needs an ambulance, not a questionnaire).
    """
    if current_severity == "EMERGENCY":
        return None
    for q in ruleset["symptoms"][symptom_id]["questions"]:
        if q["id"] not in answers:
            return q
    return None


def evaluate_symptom(ruleset: dict, symptom_id: str, answers: dict, starting_severity: Optional[str] = None) -> dict:
    """
    Evaluate one symptom's answers against the ruleset.

    `starting_severity` defaults to the symptom's base severity — that IS
    the real result before any follow-up (rule 3). It is exposed as a
    parameter purely so the escalate-only test can start from every possible
    severity and prove the result never ranks lower.
    """
    symptom = ruleset["symptoms"][symptom_id]
    severity = starting_severity if starting_severity is not None else symptom["base"]
    reasons: list[str] = []

    for rule in ruleset["rules"]:
        cond = rule["if"]
        if cond["symptom"] != symptom_id:
            continue
        if all(answers.get(k) == v for k, v in cond["answers"].items()):
            severity = escalate(severity, rule["then"])
            reasons.append(rule["because"])

    action_info = ACTIONS[severity]
    return {
        "symptom": symptom_id,
        "severity": severity,
        "reasons": reasons,
        "action": action_info["action"],
        "route": action_info["route"],
        "ruleset_version": ruleset["ruleset_version"],
    }


def evaluate_check(ruleset: dict, symptom_ids: list[str], answers: dict) -> dict:
    """Evaluate a symptom check covering possibly more than one reported symptom."""
    severity = "LOW"
    reasons: list[str] = []
    for sid in symptom_ids:
        result = evaluate_symptom(ruleset, sid, answers)
        severity = escalate(severity, result["severity"])
        reasons.extend(result["reasons"])

    action_info = ACTIONS[severity]
    return {
        "symptoms": symptom_ids,
        "severity": severity,
        "reasons": reasons,
        "action": action_info["action"],
        "route": action_info["route"],
        "ruleset_version": ruleset["ruleset_version"],
    }
