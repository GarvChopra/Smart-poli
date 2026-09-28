"""
SmartPoli — run a symptom check through the rule engine and record it.

Shared by POST /triage/check (the symptom-check screen) and the voice
assistant, so a check started by voice is evaluated by the same
triage.evaluate_check and saved in exactly the same shape — the doctor
dashboard, care report and emergency card can't tell them apart.
"""

import json
from typing import Optional

from sqlalchemy.orm import Session

from db import SymptomCheck, log_audit
from triage import evaluate_check, escalate, ACTIONS


def record_symptom_check(db: Session, patient_id: int, actor: str, ruleset: dict,
                         symptom_ids: list[str], answers: dict,
                         extra_reasons: Optional[list[str]] = None,
                         force_emergency: bool = False, min_severity: Optional[str] = None,
                         source: str = "") -> tuple[dict, SymptomCheck]:
    """`force_emergency` (deterministic immediate-risk words, voice_safety.py)
    and `min_severity` (e.g. MODERATE when a symptom isn't improving on
    recheck) can only RAISE the rule engine's result, never lower it."""
    result = evaluate_check(ruleset, symptom_ids, answers)
    floor = "EMERGENCY" if force_emergency else min_severity
    if floor and escalate(result["severity"], floor) != result["severity"]:
        result = {**result, "severity": floor,
                  "action": ACTIONS[floor]["action"], "route": ACTIONS[floor]["route"]}
    if extra_reasons:
        result = {**result, "reasons": list(result["reasons"]) + list(extra_reasons)}

    check = SymptomCheck(
        patient_id=patient_id,
        symptoms=json.dumps(symptom_ids),
        answers=json.dumps(answers),
        severity=result["severity"],
        reasons=json.dumps(result["reasons"]),
        action=result["action"],
        ruleset_version=result["ruleset_version"] + (f"+{source}" if source else ""),
    )
    db.add(check)
    db.commit()
    log_audit(db, patient_id, actor, "triage_check",
              f"severity={result['severity']} symptoms={symptom_ids}" + (f" via {source}" if source else ""))
    return result, check
