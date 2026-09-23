"""
Shared row->dict serializers and lookups, used by main.py, caregiver_router.py
and doctor_router.py alike, so the caregiver/doctor dashboards render the
SAME shape of data the patient app does — no second representation of a
Medicine, Dose or report (CLAUDE.md section 6: "One model.").
"""

import json
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from db import Patient, Prescription, Medicine, Dose, SymptomCheck, AuditLog
from scheduler import compute_adherence, compute_medicine_breakdown, sweep_missed
from interactions import check_interactions
from food_warnings import check_food_warnings


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
            if m.status == "needs_confirmation"
        ],
        "interactions": check_interactions(interaction_ruleset, active_names),
        "food_warnings": check_food_warnings(food_ruleset, active_names),
    }


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
    unconfirmed = [m for pres in prescriptions for m in pres.medicines if m.status == "needs_confirmation"]

    adherence = compute_adherence(all_doses)

    active_names = [m.name for pres in prescriptions for m in pres.medicines if m.status != "needs_confirmation" and m.name]
    interactions = check_interactions(interaction_ruleset, active_names)
    food_warnings = check_food_warnings(food_ruleset, active_names)

    alerts = []
    if adherence["adherence_percent"] is not None and adherence["adherence_percent"] < 80:
        alerts.append(f"Adherence is {adherence['adherence_percent']}% — below the 80% watch line.")
    if any(c.severity == "EMERGENCY" for c in checks):
        alerts.append("At least one symptom check was graded EMERGENCY.")
    if unconfirmed:
        alerts.append(f"{len(unconfirmed)} medicine(s) are unconfirmed and were never scheduled.")
    if any(i["severity"] == "CRITICAL" for i in interactions):
        alerts.append("A critical drug interaction was found — see Drug Interactions below.")

    return {
        "patient": {"id": patient.id, "name": patient.name, "age": patient.age, "sex": patient.sex},
        "generated_at": datetime.utcnow().isoformat(),
        "prescriptions": [
            {
                "id": pres.id,
                "doctor_name": pres.doctor_name,
                "issued_date": pres.issued_date,
                "status": pres.status,
                "medicines": [serialize_medicine(m) for m in pres.medicines],
            }
            for pres in prescriptions
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


def emergency_card_data(db: Session, patient_id: int) -> dict:
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

    return {
        "patient": {"name": patient.name, "age": patient.age, "sex": patient.sex,
                     "blood_group": patient.blood_group,
                     "allergies": patient.allergies, "emergency_contact": patient.emergency_contact},
        "scheduled_medicines": scheduled,
        "as_needed_medicines": prn,
        "has_emergency_triage_history": ever_emergency,
        "disclaimer": "Generated by SmartPoli. Assistive tool, not a medical device. Not a diagnosis. "
                       "In a real emergency, call 112.",
    }
