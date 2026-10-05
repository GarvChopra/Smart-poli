"""
Shared row->dict serializers and lookups, used by main.py, caregiver_router.py
and doctor_router.py alike, so the caregiver/doctor dashboards render the
SAME shape of data the patient app does — no second representation of a
Medicine, Dose or report (CLAUDE.md section 6: "One model.").
"""

import base64
import json

from verification import verification_status, needs_patient_review
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from db import (
    Patient, Prescription, Medicine, Dose, SymptomCheck, AuditLog,
    EmergencyCardProfile, CaregiverLink, DoctorLink, User,
)
from scheduler import compute_adherence, compute_medicine_breakdown, sweep_missed
from interactions import check_interactions
from food_warnings import check_food_warnings
from emergency_tokens import get_or_create_active_token
from schemas import CARD_SHARE_FIELDS


def get_patient_or_404(db: Session, patient_id: int) -> Patient:
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(404, f"No patient {patient_id}")
    return patient


def get_prescription_or_404(db: Session, prescription_id: int) -> Prescription:
    p = db.query(Prescription).filter(Prescription.id == prescription_id).first()
    if not p:
        raise HTTPException(404, f"No prescription {prescription_id}")
    return p


def get_medicine_or_404(db: Session, medicine_id: int) -> Medicine:
    m = db.query(Medicine).filter(Medicine.id == medicine_id).first()
    if not m:
        raise HTTPException(404, f"No medicine {medicine_id}")
    return m


def get_dose_or_404(db: Session, dose_id: int) -> Dose:
    d = db.query(Dose).filter(Dose.id == dose_id).first()
    if not d:
        raise HTTPException(404, f"No dose {dose_id}")
    return d


def serialize_patient(p: Patient) -> dict:
    return {
        "id": p.id, "name": p.name, "age": p.age, "sex": p.sex,
        "blood_group": p.blood_group,
        "allergies": p.allergies, "emergency_contact": p.emergency_contact,
    }


def serialize_medicine(m: Medicine) -> dict:
    return {
        "id": m.id,
        "prescription_id": m.prescription_id,
        "raw_text": m.raw_text,
        "name": m.name,
        "normalized_name": m.normalized_name,
        "dose_amount": m.dose_amount,
        "dose_unit": m.dose_unit,
        "schedule_code": m.schedule_code,
        "slots": json.loads(m.slots) if m.slots else [],
        "times": json.loads(m.times) if m.times else [],
        "food": m.food,
        "duration_days": m.duration_days,
        "is_prn": m.is_prn,
        "confidence": m.confidence,
        "field_confidence": json.loads(m.field_confidence) if m.field_confidence else {},
        "status": m.status,
        "plain_language_hi": m.plain_language_hi,
        "verification": verification_status(m),
    }


def serialize_dose(d: Dose) -> dict:
    return {
        "id": d.id,
        "medicine_id": d.medicine_id,
        "scheduled_at": d.scheduled_at.isoformat(),
        "state": d.state,
        "acted_at": d.acted_at.isoformat() if d.acted_at else None,
        "reason": d.reason,
        "snooze_count": d.snooze_count,
    }


def gather_dashboard_data(db: Session, patient_id: int, interaction_ruleset: dict, food_ruleset: dict) -> dict:
    """The same aggregation the patient dashboard uses — reused by the
    caregiver overview and doctor detail views instead of a second query set."""
    get_patient_or_404(db, patient_id)
    sweep_missed(db)

    prescriptions = db.query(Prescription).filter(Prescription.patient_id == patient_id).all()

    medicines_with_doses = []
    upcoming = []
    now = datetime.utcnow()
    for pres in prescriptions:
        for medicine in pres.medicines:
            medicines_with_doses.append((medicine, medicine.doses))
            for dose in medicine.doses:
                if dose.state in ("pending", "snoozed") and dose.scheduled_at >= now:
                    upcoming.append((dose, medicine))

    upcoming.sort(key=lambda pair: pair[0].scheduled_at)
    all_doses = [d for _, doses in medicines_with_doses for d in doses]
    active_names = [m.name for pres in prescriptions for m in pres.medicines
                    if m.status != "needs_confirmation" and m.name]

    return {
        "patient_id": patient_id,
        "prescriptions": prescriptions,
        "medicines_with_doses": medicines_with_doses,
        "adherence": compute_adherence(all_doses),
        "per_medicine": compute_medicine_breakdown(medicines_with_doses),
        "upcoming_doses": [
            {**serialize_dose(dose), "medicine_name": medicine.name or medicine.raw_text}
            for dose, medicine in upcoming[:20]
        ],
        "prn_medicines": [
            serialize_medicine(m) for pres in prescriptions for m in pres.medicines if m.is_prn
        ],
        "unconfirmed_medicines": [
            serialize_medicine(m) for pres in prescriptions for m in pres.medicines
            if needs_patient_review(m)
        ],
        "interactions": check_interactions(interaction_ruleset, active_names),
        "food_warnings": check_food_warnings(food_ruleset, active_names),
    }


def _plain_when(medicine) -> str:
    """'1-0-1 PC' -> 'Morning & night, after food' for the care report; as-needed medicines say so."""
    if medicine.is_prn:
        return "When needed"
    from emergency_page import _when_words
    return _when_words(medicine.schedule_code, medicine.food)


def gather_report_data(db: Session, patient_id: int, interaction_ruleset: dict, food_ruleset: dict) -> dict:
    """Shared by the JSON report endpoint, the PDF export, and the doctor
    detail view — one report, several renderings, never two competing
    implementations of what's in it."""
    patient = get_patient_or_404(db, patient_id)
    sweep_missed(db)

    prescriptions = db.query(Prescription).filter(Prescription.patient_id == patient_id).all()
    checks = db.query(SymptomCheck).filter(SymptomCheck.patient_id == patient_id).order_by(SymptomCheck.created_at).all()
    notes = (
        db.query(AuditLog)
        .filter(AuditLog.patient_id == patient_id, AuditLog.action == "clinical_note")
        .order_by(AuditLog.at.desc())
        .all()
    )

    all_doses = [d for pres in prescriptions for m in pres.medicines for d in m.doses]
    missed = [d for d in all_doses if d.state == "missed"]
    adherence = compute_adherence(all_doses)

    active_names = [m.name for pres in prescriptions for m in pres.medicines if m.status != "needs_confirmation" and m.name]
    interactions = check_interactions(interaction_ruleset, active_names)
    food_warnings = check_food_warnings(food_ruleset, active_names)

    alerts = []
    if adherence["adherence_percent"] is not None and adherence["adherence_percent"] < 80:
        alerts.append(f"Adherence is {adherence['adherence_percent']}% — below the 80% watch line.")
    if any(c.severity == "EMERGENCY" for c in checks):
        alerts.append("At least one symptom check was graded EMERGENCY.")
    if any(i["severity"] == "CRITICAL" for i in interactions):
        alerts.append("A critical drug interaction was found — see Drug Interactions below.")

    return {
        "patient": {"id": patient.id, "name": patient.name, "age": patient.age, "sex": patient.sex,
                    "allergies": patient.allergies, "blood_group": patient.blood_group},
        "generated_at": datetime.utcnow().isoformat(),
        # Only medicines that were actually read and scheduled belong in a care report. Abandoned
        # drafts (every Decode / scan / photo makes one) used to be listed here as "needs confirmation".
        "prescriptions": [
            {
                "id": pres.id,
                "doctor_name": pres.doctor_name,
                "issued_date": pres.issued_date,
                "status": pres.status,
                "medicines": [{**serialize_medicine(m), "when": _plain_when(m)}
                              for m in pres.medicines if m.status != "needs_confirmation"],
            }
            for pres in prescriptions
            if any(m.status != "needs_confirmation" for m in pres.medicines)
        ],
        "adherence": adherence,
        "interactions": interactions,
        "food_warnings": food_warnings,
        "missed_doses": [
            {**serialize_dose(d), "medicine_name": d.medicine.name or d.medicine.raw_text}
            for d in missed
        ],
        "symptom_history": [
            {
                "id": c.id,
                "created_at": c.created_at.isoformat(),
                "symptoms": json.loads(c.symptoms),
                "severity": c.severity,
                "reasons": json.loads(c.reasons),
                "action": c.action,
                "ruleset_version": c.ruleset_version,
            }
            for c in checks
        ],
        "doctor_caregiver_notes": [
            {"id": n.id, "actor": n.actor, "note": n.detail, "at": n.at.isoformat()} for n in notes
        ],
        "alerts": alerts,
        "disclaimer": "Generated by SmartPoli. Assistive tool, not a medical device. Not a diagnosis.",
    }


CARD_AUDIT_ACTIONS = (
    "patient_created", "patient_updated", "prescription_confirmed", "medicine_confirmed",
    "medicine_corrected", "triage_check", "caregiver_link_accepted", "caregiver_link_revoked",
    "doctor_link_accepted", "doctor_link_revoked", "emergency_profile_updated",
)


def _card_profile_row(db: Session, patient_id: int) -> Optional[EmergencyCardProfile]:
    return db.query(EmergencyCardProfile).filter(EmergencyCardProfile.patient_id == patient_id).first()


def card_profile(db: Session, patient_id: int) -> dict:
    """The 3D card's extra content and share choices, with defaults applied
    (every field shared unless the patient turned it off)."""
    row = _card_profile_row(db, patient_id)
    stored = json.loads(row.share) if row and row.share else {}
    return {
        "conditions": row.conditions if row else None,
        "instructions": row.instructions if row else None,
        "has_photo": bool(row and row.photo),
        "share": {f: stored.get(f, True) for f in CARD_SHARE_FIELDS},
    }


def card_photo(db: Session, patient_id: int) -> Optional[tuple[bytes, str]]:
    """(image bytes, media type) or None."""
    row = _card_profile_row(db, patient_id)
    if not row or not row.photo:
        return None
    prefix, _, payload = row.photo.partition(",")
    return base64.b64decode(payload), prefix[len("data:"):-len(";base64")]


def save_card_profile(db: Session, patient_id: int, update: dict) -> None:
    """`update` holds only the fields the client sent; "" clears a field."""
    row = _card_profile_row(db, patient_id)
    if row is None:
        row = EmergencyCardProfile(patient_id=patient_id)
        db.add(row)
    for field in ("conditions", "instructions", "photo"):
        if field in update:
            setattr(row, field, update[field] or None)
    if "share" in update and update["share"] is not None:
        merged = json.loads(row.share) if row.share else {}
        merged.update(update["share"])
        row.share = json.dumps(merged)
    db.commit()


def _care_team(db: Session, patient_id: int) -> dict:
    def names(link_model, user_col):
        return [name for (name,) in db.query(User.name)
                .join(link_model, user_col == User.id)
                .filter(link_model.patient_id == patient_id, link_model.status == "active")
                .order_by(link_model.accepted_at).all()]
    return {"caregivers": names(CaregiverLink, CaregiverLink.caregiver_user_id),
            "doctors": names(DoctorLink, DoctorLink.doctor_user_id)}


def emergency_card_data(db: Session, patient_id: int) -> dict:
    """Note: the first call for a patient creates (and commits) their
    emergency-card token, even from read-only callers like the caregiver
    overview."""
    patient = get_patient_or_404(db, patient_id)
    prescriptions = db.query(Prescription).filter(Prescription.patient_id == patient_id).all()

    scheduled = [
        {"name": m.name or m.raw_text, "dose_amount": m.dose_amount, "dose_unit": m.dose_unit,
         "schedule_code": m.schedule_code, "food": m.food}
        for pres in prescriptions for m in pres.medicines
        if m.status != "needs_confirmation" and not m.is_prn
    ]
    prn = [
        {"name": m.name or m.raw_text, "dose_amount": m.dose_amount, "dose_unit": m.dose_unit}
        for pres in prescriptions for m in pres.medicines
        if m.status != "needs_confirmation" and m.is_prn
    ]
    ever_emergency = db.query(SymptomCheck).filter(
        SymptomCheck.patient_id == patient_id, SymptomCheck.severity == "EMERGENCY"
    ).count() > 0

    # Only actions that change what the card shows — dose activity (incl.
    # the timed auto-miss sweep) would otherwise make a year-old card look
    # "updated minutes ago" to a responder.
    latest_audit = db.query(func.max(AuditLog.at)).filter(
        AuditLog.patient_id == patient_id, AuditLog.action.in_(CARD_AUDIT_ACTIONS)
    ).scalar()
    candidates = [patient.created_at, latest_audit] + [pres.created_at for pres in prescriptions]
    last_updated = max(c for c in candidates if c is not None)

    return {
        "profile": card_profile(db, patient_id),
        "care_team": _care_team(db, patient_id),
        "card_path": f"/emergency/{get_or_create_active_token(db, patient_id)}",
        "last_updated": last_updated.isoformat(),
        "patient": {"name": patient.name, "age": patient.age, "sex": patient.sex,
                     "blood_group": patient.blood_group,
                     "allergies": patient.allergies, "emergency_contact": patient.emergency_contact},
        "scheduled_medicines": scheduled,
        "as_needed_medicines": prn,
        "has_emergency_triage_history": ever_emergency,
        "disclaimer": "Generated by SmartPoli. Assistive tool, not a medical device. Not a diagnosis. "
                       "In a real emergency, call 112.",
    }
