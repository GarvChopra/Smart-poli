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
from triage import evaluate_check, ACTIONS


def record_symptom_check(db: Session, patient_id: int, actor: str, ruleset: dict,
                         symptom_ids: list[str], answers: dict,
                         extra_reasons: Optional[list[str]] = None,
                         force_emergency: bool = False, source: str = "") -> tuple[dict, SymptomCheck]:
    """`force_emergency` is only for the deterministic red-flag check
    (voice_safety.py) — it can raise the rule result, never lower it."""
    result = evaluate_check(ruleset, symptom_ids, answers)
    if force_emergency:
        result = {**result, "severity": "EMERGENCY",
                  "action": ACTIONS["EMERGENCY"]["action"], "route": ACTIONS["EMERGENCY"]["route"]}
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
