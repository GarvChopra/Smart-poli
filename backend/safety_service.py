"""
SmartPoli - database glue for safety_engine.

safety_engine.py is pure (plain dicts, caller-supplied 'now'); this module
loads a patient's medicines and doses from the database in the shape the
engine wants, in the patient's own local time, and records decisions in the
audit log (rule version + inputs + outcome) so every suggestion is
reproducible later.
"""

import json
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from clock import patient_now
from db import Dose, Medicine, Prescription, log_audit
import safety_engine

_RULES = None


def rules() -> dict:
    global _RULES
    if _RULES is None:
        _RULES = safety_engine.load_rules()
    return _RULES


def engine_inputs(db: Session, patient_id: int, utc_now: Optional[datetime] = None) -> tuple[list[dict], datetime]:
    """(medicines, patient_local_now). Only medicines a human has confirmed
    and that have scheduled doses take part; PRN medicines have no doses."""
    now = patient_now(db, patient_id, utc_now)
    meds = (db.query(Medicine).join(Prescription)
            .filter(Prescription.patient_id == patient_id, Medicine.status != "needs_confirmation").all())
    out = []
    for m in meds:
        if not m.doses:
            continue
        out.append({
            "medicine_id": m.id,
            "name": m.name or m.raw_text,
            "doses": [{"id": d.id, "scheduled_at": d.scheduled_at, "state": d.state, "acted_at": d.acted_at}
                      for d in m.doses],
        })
    return out, now


def conflicts_for_patient(db: Session, patient_id: int, utc_now: Optional[datetime] = None) -> dict:
    medicines, now = engine_inputs(db, patient_id, utc_now)
    r = rules()
    return {
        "checked_at_local": now.isoformat(),
        "ruleset_version": r["ruleset_version"],
        "review_status": r["review_status"],
        "conflicts": safety_engine.detect_schedule_conflicts(medicines, r, now),
        "not_verified_note": "SmartPoli only checks the medicine pairs in its rule list. A pair that is not listed "
                             "has NOT been shown to be safe together - ask your pharmacist if unsure.",
    }


def missed_guidance_for_dose(db: Session, dose: Dose, utc_now: Optional[datetime] = None) -> dict:
    patient_id = dose.medicine.prescription.patient_id
    medicines, now = engine_inputs(db, patient_id, utc_now)
    mine = next((m for m in medicines if m["medicine_id"] == dose.medicine_id), None)
    if mine is None:
        mine = {"medicine_id": dose.medicine_id, "name": dose.medicine.name or dose.medicine.raw_text,
                "doses": [{"id": d.id, "scheduled_at": d.scheduled_at, "state": d.state, "acted_at": d.acted_at}
                          for d in dose.medicine.doses]}
    this = next(d for d in mine["doses"] if d["id"] == dose.id)
    others = [m for m in medicines if m["medicine_id"] != dose.medicine_id]
    r = rules()
    guidance = safety_engine.missed_dose_guidance(mine, this, r, now, others)
    guidance["ruleset_version"] = r["ruleset_version"]
    return guidance


def record_decision(db: Session, patient_id: int, actor: str, action: str, inputs: dict, decision: dict) -> None:
    """Audit entry: which ruleset, what went in, what came out. Kept small and
    free of free-text health detail beyond the medicine name."""
    slim = {k: decision.get(k) for k in ("action", "rule_id", "kind", "status", "earliest_safe_time",
                                        "hours_to_next", "hours_since_due", "proposal")}
    log_audit(db, patient_id, actor, action,
              json.dumps({"ruleset_version": rules()["ruleset_version"], "inputs": inputs, "decision": slim},
                         default=str))
