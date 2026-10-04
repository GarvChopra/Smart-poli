"""
SmartPoli Clinical Intelligence — patient priority queue, pre-consultation
brief, and since-last-visit comparison, for the doctor and caregiver
dashboards.

Every signal here is already computed elsewhere in the app (adherence,
alerts, symptom severity) — this module only classifies and summarizes
data that already exists. Nothing here calls an LLM or invents a value
that wasn't read from the database. Priority is escalate-only, the same
invariant triage.py uses: a level only ever goes up as more reasons are
found in a single pass, never down.
"""

from datetime import datetime, timedelta

from verification import needs_patient_review
from db import SymptomCheck, Dose, Medicine, Prescription, AuditLog, MedicineCorrection

_LEVELS = ["routine", "medium", "high", "emergency"]
_SEVERITY_ORDER = {"LOW": 0, "MODERATE": 1, "EMERGENCY": 2}


def _escalate(current: str, candidate: str) -> str:
    return candidate if _LEVELS.index(candidate) > _LEVELS.index(current) else current


def compute_priority(db, patient_id: int) -> dict:
    """{"level": "routine"|"medium"|"high"|"emergency", "reasons": [str, ...]}
    Cheap enough to run once per patient in a list view — used to sort and
    badge the doctor/caregiver patient picker. Every reason is a plain
    sentence, not a score, so "why is this patient here" is always answerable."""
    prescriptions = db.query(Prescription).filter(Prescription.patient_id == patient_id).all()
    medicines = [m for p in prescriptions for m in p.medicines]
    all_doses = [d for m in medicines for d in m.doses]

    unconfirmed = [m for m in medicines if needs_patient_review(m)]
    cutoff = datetime.utcnow() - timedelta(days=7)
    missed_recent = sum(1 for d in all_doses if d.state == "missed" and d.acted_at and d.acted_at >= cutoff)
    ever_emergency = db.query(SymptomCheck).filter(
        SymptomCheck.patient_id == patient_id, SymptomCheck.severity == "EMERGENCY"
    ).count() > 0
    recent_moderate = db.query(SymptomCheck).filter(
        SymptomCheck.patient_id == patient_id, SymptomCheck.severity == "MODERATE",
        SymptomCheck.created_at >= cutoff,
    ).count() > 0

    taken = sum(1 for d in all_doses if d.state == "taken")
    total_acted = sum(1 for d in all_doses if d.state in ("taken", "missed", "skipped"))
    adherence_percent = round(taken / total_acted * 100, 1) if total_acted else None

    level = "routine"
    reasons = []
    if ever_emergency:
        level = _escalate(level, "emergency")
        reasons.append("An EMERGENCY-graded symptom check is in this patient's history.")
    if recent_moderate:
        level = _escalate(level, "medium")
        reasons.append("A MODERATE-graded symptom check in the last 7 days routes to a routine consultation.")
    if unconfirmed:
        level = _escalate(level, "high")
        reasons.append(f"{len(unconfirmed)} prescription(s) awaiting verification.")
    if missed_recent >= 3:
        level = _escalate(level, "high")
        reasons.append(f"{missed_recent} dose(s) missed in the last 7 days.")
    elif missed_recent:
        level = _escalate(level, "medium")
        reasons.append(f"{missed_recent} dose(s) missed in the last 7 days.")
    if adherence_percent is not None and adherence_percent < 80:
        level = _escalate(level, "medium")
        reasons.append(f"Adherence is {adherence_percent}% — below the 80% watch line.")

    return {"level": level, "reasons": reasons}


def compute_since_last_visit(db, patient_id: int, doctor_user_id: int) -> dict | None:
    """None if this doctor has never left a note or correction for this
    patient (nothing to compare against yet). Otherwise a summary of what
    changed since that timestamp — real counts over a real window, never
    a fabricated "before" value we never actually recorded."""
    last_note = (
        db.query(AuditLog)
        .filter(AuditLog.patient_id == patient_id, AuditLog.action == "clinical_note",
                AuditLog.actor.like(f"doctor:{doctor_user_id}%"))
        .order_by(AuditLog.at.desc()).first()
    )
    last_correction = (
        db.query(MedicineCorrection)
        .join(Medicine, MedicineCorrection.medicine_id == Medicine.id)
        .join(Prescription, Medicine.prescription_id == Prescription.id)
        .filter(Prescription.patient_id == patient_id, MedicineCorrection.doctor_user_id == doctor_user_id)
        .order_by(MedicineCorrection.created_at.desc()).first()
    )
    candidates = [t for t in (
        last_note.at if last_note else None,
        last_correction.created_at if last_correction else None,
    ) if t is not None]
    if not candidates:
        return None
    last_visit_at = max(candidates)

    prescriptions = db.query(Prescription).filter(Prescription.patient_id == patient_id).all()
    doses_since = [
        d for p in prescriptions for m in p.medicines for d in m.doses
        if d.scheduled_at >= last_visit_at
    ]
    taken = sum(1 for d in doses_since if d.state == "taken")
    missed = sum(1 for d in doses_since if d.state == "missed")
    skipped = sum(1 for d in doses_since if d.state == "skipped")
    total_acted = taken + missed + skipped
    window_adherence = round(taken / total_acted * 100, 1) if total_acted else None

    new_checks = (
        db.query(SymptomCheck)
        .filter(SymptomCheck.patient_id == patient_id, SymptomCheck.created_at >= last_visit_at)
        .all()
    )
    worst_severity = None
    if new_checks:
        worst_severity = max(new_checks, key=lambda c: _SEVERITY_ORDER.get(c.severity, 0)).severity

    new_prescriptions = [p for p in prescriptions if p.created_at and p.created_at >= last_visit_at]

    changes = []
    if total_acted:
        changes.append({"label": "Adherence since last visit", "value": f"{window_adherence}%"})
    if missed:
        changes.append({"label": "Doses missed since last visit", "value": str(missed)})
    if new_checks:
        changes.append({"label": "New symptom checks", "value": f"{len(new_checks)} (worst: {worst_severity})"})
    if new_prescriptions:
        changes.append({"label": "New prescription(s) added", "value": str(len(new_prescriptions))})

    return {
        "last_visit_at": last_visit_at.isoformat(),
        "days_since": (datetime.utcnow() - last_visit_at).days,
        "changes": changes,
    }


def compute_doctor_brief(db, patient_id: int) -> dict:
    """The pre-consultation summary: what a doctor would otherwise spend
    the first five minutes reconstructing by hand. Every field is either
    a direct DB value or a plain count — nothing generated."""
    from serializers import get_patient_or_404

    patient = get_patient_or_404(db, patient_id)
    prescriptions = db.query(Prescription).filter(Prescription.patient_id == patient_id).all()
    medicines = [m for p in prescriptions for m in p.medicines]
    active_medicines = [m for m in medicines if m.status != "needs_confirmation" and not m.is_prn]
    unconfirmed = [m for m in medicines if needs_patient_review(m)]
    all_doses = [d for m in medicines for d in m.doses]

    taken = sum(1 for d in all_doses if d.state == "taken")
    total_acted = sum(1 for d in all_doses if d.state in ("taken", "missed", "skipped"))
    adherence_percent = round(taken / total_acted * 100, 1) if total_acted else None

    latest_check = (
        db.query(SymptomCheck)
        .filter(SymptomCheck.patient_id == patient_id)
        .order_by(SymptomCheck.created_at.desc())
        .first()
    )

    return {
        "patient_name": patient.name,
        "current_medicine_count": len(active_medicines),
        "current_medicine_names": [m.name or m.raw_text for m in active_medicines],
        "allergies": patient.allergies or "None recorded",
        "adherence_percent": adherence_percent,
        "pending_verification_count": len(unconfirmed),
        "latest_symptom_check": {
            "created_at": latest_check.created_at.isoformat(),
            "severity": latest_check.severity,
            "action": latest_check.action,
        } if latest_check else None,
    }
