"""
SmartPoli — Doctor linking + doctor dashboard (Part 1 of the brief).

Same real-relationship pattern as caregiver_router.py: a doctor never sees
a patient by knowing their id — a DoctorLink must be active first. A
doctor's corrections to a medicine are never silent: every correction
writes a MedicineCorrection row (original value, corrected value, doctor
id, reason) alongside updating the live Medicine row, and clinical notes
carry the doctor's real identity, not a client-supplied 'actor' string.
"""

import json as _json
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db_session, log_audit, User, DoctorLink, DoctorShareConsent, CareAppointment, MedicineCorrection, PrescriptionTemplate, Dose, Medicine, Prescription
from auth import (PERSON_ROLES, get_current_user, require_patient_write_access, require_doctor_role, doctor_link_for_patient, doctor_has_scope, has_write_access, gen_link_code,
                  doctor_role_enabled)
from schemas import LinkRedeem, MedicineCorrectionCreate, DoctorPrescriptionCreate, PrescriptionTemplateCreate
from serializers import get_patient_or_404, get_medicine_or_404, serialize_patient, serialize_medicine, serialize_dose, slot_of, gather_report_data
from doctor_intelligence import compute_priority, compute_since_last_visit, compute_doctor_brief
from prescription_service import create_prescription_from_lines
from interactions import load_ruleset as load_interaction_ruleset
from food_warnings import load_ruleset as load_food_ruleset

def _doctor_role_on():
    """Every doctor URL answers 404 while the doctor role is switched off (auth.doctor_role_enabled)."""
    if not doctor_role_enabled():
        raise HTTPException(404, "Not found")


router = APIRouter(tags=["doctor"], dependencies=[Depends(_doctor_role_on)])

INTERACTION_RULESET = load_interaction_ruleset()
FOOD_RULESET = load_food_ruleset()

_CORRECTABLE_FIELDS = {"name", "dose_amount", "dose_unit", "food", "duration_days", "schedule_code"}


class AppointmentRequest(BaseModel):
    doctor_user_id: int
    visit_date: date
    time_preference: Literal["morning", "afternoon", "evening", "any"] = "any"


class AppointmentAction(BaseModel):
    action: Literal["confirm", "check_in", "call", "complete", "cancel"]
    scheduled_at: Optional[str] = None


class DoctorLinkCreate(BaseModel):
    scopes: list[Literal["current_medicines", "allergies", "dose_checkins", "visit_notes"]] = ["current_medicines"]


def _require_scope(db, user, patient_id: int, scope: str):
    if not doctor_has_scope(db, user, patient_id, scope):
        raise HTTPException(403, f"The patient has not shared {scope.replace('_', ' ')} with you.")


def _appointment_json(db, item):
    patient = get_patient_or_404(db, item.patient_id)
    doctor = db.query(User).filter(User.id == item.doctor_user_id).first()
    return {"id": item.id, "patient_id": item.patient_id, "patient_name": patient.name,
            "doctor_user_id": item.doctor_user_id, "doctor_name": doctor.name if doctor else "Clinician",
            "visit_date": item.visit_date.isoformat(), "time_preference": item.time_preference,
            "scheduled_at": item.scheduled_at.isoformat() if item.scheduled_at else None,
            "status": item.status, "token_number": item.token_number,
            "created_at": item.created_at.isoformat(),
            "checked_in_at": item.checked_in_at.isoformat() if item.checked_in_at else None,
            "called_at": item.called_at.isoformat() if item.called_at else None}


# ---------------------------------------------------------------- patient side: manage invites

@router.post("/patients/{patient_id}/doctor-links")
def create_doctor_link(patient_id: int, body: DoctorLinkCreate = DoctorLinkCreate(), user: User = Depends(require_patient_write_access),
                        db: Session = Depends(get_db_session)):
    get_patient_or_404(db, patient_id)
    code = gen_link_code()
    while db.query(DoctorLink).filter(DoctorLink.code == code).first():
        code = gen_link_code()
    link = DoctorLink(patient_id=patient_id, code=code, status="pending")
    db.add(link)
    db.flush()
    db.add(DoctorShareConsent(doctor_link_id=link.id, scopes=_json.dumps(sorted(set(body.scopes)))))
    db.commit()
    log_audit(db, patient_id, f"patient:{user.id}", "doctor_link_created", f"invite issued (link {link.id})")
    return {"id": link.id, "code": link.code, "status": link.status}


@router.get("/patients/{patient_id}/doctor-links")
def list_doctor_links(patient_id: int, user: User = Depends(require_patient_write_access),
                       db: Session = Depends(get_db_session)):
    links = (
        db.query(DoctorLink)
        .filter(DoctorLink.patient_id == patient_id)
        .order_by(DoctorLink.created_at.desc())
        .all()
    )
    out = []
    for link in links:
        doctor_name = None
        if link.doctor_user_id:
            doc = db.query(User).filter(User.id == link.doctor_user_id).first()
            doctor_name = doc.name if doc else None
        consent = db.query(DoctorShareConsent).filter(DoctorShareConsent.doctor_link_id == link.id).first()
        try:
            scopes = _json.loads(consent.scopes) if consent else []
        except (TypeError, ValueError):
            scopes = []
        out.append({
            "id": link.id, "code": link.code, "status": link.status, "doctor_name": doctor_name,
            "doctor_user_id": link.doctor_user_id,
            "created_at": link.created_at.isoformat(),
            "accepted_at": link.accepted_at.isoformat() if link.accepted_at else None,
            "scopes": scopes,
        })
    return out


@router.post("/doctor-links/{link_id}/revoke")
def revoke_doctor_link(link_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db_session)):
    link = db.query(DoctorLink).filter(DoctorLink.id == link_id).first()
    if not link:
        raise HTTPException(404, f"No doctor link {link_id}")
    patient = get_patient_or_404(db, link.patient_id)
    if not (user.role in PERSON_ROLES and patient.user_id == user.id):
        raise HTTPException(403, "Only the patient can revoke a doctor's access.")
    link.status = "revoked"
    db.commit()
    log_audit(db, link.patient_id, f"patient:{user.id}", "doctor_link_revoked", f"link {link.id}")
    return {"id": link.id, "status": link.status}


# ---------------------------------------------------------------- patient <-> clinician visit requests

@router.post("/patients/{patient_id}/appointments")
def request_clinic_appointment(patient_id: int, body: AppointmentRequest,
                               user: User = Depends(require_patient_write_access), db: Session = Depends(get_db_session)):
    get_patient_or_404(db, patient_id)
    if not has_write_access(db, user, patient_id):
        raise HTTPException(403, "Only the patient can request a clinician visit.")
    link = db.query(DoctorLink).filter(DoctorLink.patient_id == patient_id,
                                       DoctorLink.doctor_user_id == body.doctor_user_id,
                                       DoctorLink.status == "active").first()
    if not link:
        raise HTTPException(403, "Connect with this clinician before requesting a visit.")
    from clock import patient_now
    today = patient_now(db, patient_id).date()
    if body.visit_date < today or body.visit_date > today + timedelta(days=90):
        raise HTTPException(422, "Choose a date within the next 90 days.")
    duplicate = db.query(CareAppointment).filter(CareAppointment.patient_id == patient_id,
        CareAppointment.doctor_user_id == body.doctor_user_id, CareAppointment.visit_date == body.visit_date,
        CareAppointment.status.in_(["requested", "confirmed"])).first()
    if duplicate:
        raise HTTPException(409, "You already have a visit request for this day.")
    item = CareAppointment(patient_id=patient_id, doctor_user_id=body.doctor_user_id,
                           visit_date=body.visit_date, time_preference=body.time_preference)
    db.add(item)
    db.commit()
    log_audit(db, patient_id, f"patient:{user.id}", "clinic_appointment_requested",
              f"visit request {item.id} for {body.visit_date.isoformat()}")
    return _appointment_json(db, item)


@router.get("/patients/{patient_id}/appointments")
def patient_clinic_appointments(patient_id: int, user: User = Depends(require_patient_write_access),
                                db: Session = Depends(get_db_session)):
    get_patient_or_404(db, patient_id)
    if not has_write_access(db, user, patient_id):
        raise HTTPException(403, "Only the patient can view their clinic visit requests.")
    rows = db.query(CareAppointment).filter(CareAppointment.patient_id == patient_id).order_by(
        CareAppointment.visit_date.desc(), CareAppointment.created_at.desc()).limit(100).all()
    return [_appointment_json(db, row) for row in rows]


@router.post("/appointments/{appointment_id}/cancel")
def patient_cancel_appointment(appointment_id: int, user: User = Depends(get_current_user),
                               db: Session = Depends(get_db_session)):
    item = db.query(CareAppointment).filter(CareAppointment.id == appointment_id).first()
    if not item:
        raise HTTPException(404, "Visit request not found.")
    patient = get_patient_or_404(db, item.patient_id)
    if user.role not in PERSON_ROLES or patient.user_id != user.id:
        raise HTTPException(403, "Only the patient can cancel this visit request.")
    if item.status in ("completed", "cancelled"):
        raise HTTPException(409, "This visit can no longer be cancelled.")
    item.status = "cancelled"
    db.commit()
    log_audit(db, item.patient_id, f"patient:{user.id}", "clinic_appointment_cancelled", f"visit request {item.id}")
    return _appointment_json(db, item)


@router.get("/doctor/appointments")
def doctor_appointments(visit_date: Optional[date] = None, user: User = Depends(require_doctor_role),
                       db: Session = Depends(get_db_session)):
    query = db.query(CareAppointment).join(DoctorLink, DoctorLink.patient_id == CareAppointment.patient_id).filter(
                                             DoctorLink.doctor_user_id == user.id, DoctorLink.status == "active",
                                             CareAppointment.doctor_user_id == user.id,
                                             CareAppointment.status != "cancelled")
    if visit_date:
        query = query.filter(CareAppointment.visit_date == visit_date)
    rows = query.order_by(CareAppointment.visit_date, CareAppointment.scheduled_at,
                          CareAppointment.token_number, CareAppointment.created_at).limit(200).all()
    return [_appointment_json(db, row) for row in rows]


@router.patch("/doctor/appointments/{appointment_id}")
def update_doctor_appointment(appointment_id: int, body: AppointmentAction,
                              user: User = Depends(require_doctor_role), db: Session = Depends(get_db_session)):
    item = db.query(CareAppointment).filter(CareAppointment.id == appointment_id,
                                            CareAppointment.doctor_user_id == user.id).first()
    if not item:
        raise HTTPException(404, "Visit request not found.")
    if not db.query(DoctorLink).filter(DoctorLink.patient_id == item.patient_id,
            DoctorLink.doctor_user_id == user.id, DoctorLink.status == "active").first():
        raise HTTPException(403, "The patient has removed your access.")
    transitions = {"requested": {"confirm", "cancel"}, "confirmed": {"check_in", "cancel"},
                   "checked_in": {"call", "complete", "cancel"}, "called": {"complete", "cancel"},
                   "completed": set(), "cancelled": set()}
    if body.action not in transitions[item.status]:
        raise HTTPException(409, f"Cannot {body.action.replace('_', ' ')} a {item.status.replace('_', ' ')} visit.")
    if body.action == "confirm":
        try:
            slot = datetime.fromisoformat(body.scheduled_at or "")
        except ValueError:
            raise HTTPException(422, "Select a confirmed date and time.")
        if slot.date() != item.visit_date:
            raise HTTPException(422, "The confirmed time must be on the requested day.")
        item.scheduled_at = slot
        item.status = "confirmed"
    elif body.action == "check_in":
        last = db.query(CareAppointment.token_number).filter(CareAppointment.doctor_user_id == user.id,
            CareAppointment.visit_date == item.visit_date, CareAppointment.token_number.isnot(None)).order_by(
            CareAppointment.token_number.desc()).first()
        item.token_number = (last[0] if last else 0) + 1
        item.checked_in_at = datetime.utcnow()
        item.status = "checked_in"
    elif body.action == "call":
        item.called_at = datetime.utcnow()
        item.status = "called"
    else:
        item.status = "completed" if body.action == "complete" else "cancelled"
    db.commit()
    log_audit(db, item.patient_id, f"doctor:{user.id}", f"clinic_appointment_{item.status}", f"visit request {item.id}")
    return _appointment_json(db, item)


# ---------------------------------------------------------------- doctor side

@router.post("/doctor/link/redeem")
def redeem_doctor_link(body: LinkRedeem, user: User = Depends(require_doctor_role),
                        db: Session = Depends(get_db_session)):
    code = body.code.strip().upper()
    link = db.query(DoctorLink).filter(DoctorLink.code == code).first()
    if not link:
        raise HTTPException(404, "Invalid invite code.")
    if link.status == "revoked":
        raise HTTPException(409, "This invite has been revoked.")
    if link.status == "active" and link.doctor_user_id != user.id:
        raise HTTPException(409, "This invite has already been used by another doctor.")

    link.doctor_user_id = user.id
    link.status = "active"
    link.accepted_at = datetime.utcnow()
    db.commit()
    log_audit(db, link.patient_id, f"doctor:{user.id}", "doctor_link_accepted", f"link {link.id}")

    patient = get_patient_or_404(db, link.patient_id)
    return {"linked": True, "patient": {"id": patient.id, "name": patient.name}}


@router.get("/doctor/patients")
def doctor_patients(user: User = Depends(require_doctor_role), db: Session = Depends(get_db_session)):
    links = db.query(DoctorLink).filter(
        DoctorLink.doctor_user_id == user.id, DoctorLink.status == "active",
    ).all()
    patients = [
        {"id": get_patient_or_404(db, link.patient_id).id,
         "name": get_patient_or_404(db, link.patient_id).name,
         "priority": compute_priority(db, link.patient_id) if (
             doctor_has_scope(db, user, link.patient_id, "current_medicines") and
             doctor_has_scope(db, user, link.patient_id, "dose_checkins") and
             doctor_has_scope(db, user, link.patient_id, "visit_notes")) else {"level": "routine", "reasons": []}}
        for link in links
    ]
    order = {"emergency": 0, "high": 1, "medium": 2, "routine": 3}
    patients.sort(key=lambda p: order[p["priority"]["level"]])
    return patients


@router.get("/doctor/patients/{patient_id}")
def doctor_patient_detail(patient_id: int, user: User = Depends(require_doctor_role),
                           db: Session = Depends(get_db_session)):
    """Everything SmartPoli already generated for this patient — prescription,
    OCR/extraction confidence, schedule, adherence, missed doses, symptoms,
    triage history, notes — reusing gather_report_data rather than a second
    query set built just for the doctor view. brief/since_last_visit add the
    pre-consultation summary and change-since-last-note comparison on top."""
    if not doctor_link_for_patient(db, user, patient_id):
        raise HTTPException(403, "You are not linked to this patient.")
    report = gather_report_data(db, patient_id, INTERACTION_RULESET, FOOD_RULESET)
    # Keep the report deliberately minimal for a clinician by default, but do
    # not accidentally discard an allergy after the patient explicitly shared
    # that section of their record.
    shared_allergies = report["patient"].get("allergies")
    report["patient"] = {"id": report["patient"]["id"], "name": report["patient"]["name"], "age": None, "sex": None,
                         "allergies": None, "blood_group": None}
    if not doctor_has_scope(db, user, patient_id, "current_medicines"):
        report["prescriptions"] = []
        report["interactions"] = []
        report["food_warnings"] = []
    if not doctor_has_scope(db, user, patient_id, "allergies"):
        report["patient"]["allergies"] = None
    else:
        report["patient"]["allergies"] = shared_allergies
    if not doctor_has_scope(db, user, patient_id, "dose_checkins"):
        report["adherence"] = None
    if not all(doctor_has_scope(db, user, patient_id, scope) for scope in ("current_medicines", "dose_checkins")):
        report["missed_doses"] = []
    if not doctor_has_scope(db, user, patient_id, "visit_notes"):
        report["symptom_history"] = []
        report["doctor_caregiver_notes"] = []
    report["alerts"] = [
        alert for alert in report.get("alerts", [])
        if ("Adherence" in alert and doctor_has_scope(db, user, patient_id, "dose_checkins"))
        or ("symptom check" in alert and doctor_has_scope(db, user, patient_id, "visit_notes"))
        or ("interaction" in alert and doctor_has_scope(db, user, patient_id, "current_medicines"))
    ]
    report["brief"] = compute_doctor_brief(db, patient_id)
    if not doctor_has_scope(db, user, patient_id, "current_medicines"):
        report["brief"].update(current_medicine_count=None, current_medicine_names=[], pending_verification_count=None)
    if not doctor_has_scope(db, user, patient_id, "allergies"):
        report["brief"]["allergies"] = None
    if not doctor_has_scope(db, user, patient_id, "dose_checkins"):
        report["brief"]["adherence_percent"] = None
    if not doctor_has_scope(db, user, patient_id, "visit_notes"):
        report["brief"]["latest_symptom_check"] = None
    report["since_last_visit"] = compute_since_last_visit(db, patient_id, user.id) if all(
        doctor_has_scope(db, user, patient_id, scope)
        for scope in ("current_medicines", "dose_checkins", "visit_notes")) else []
    return report


@router.get("/doctor/patients/{patient_id}/doses")
def doctor_patient_doses(patient_id: int, date: Optional[str] = None, user: User = Depends(require_doctor_role), db: Session = Depends(get_db_session)):
    _require_scope(db, user, patient_id, "current_medicines")
    _require_scope(db, user, patient_id, "dose_checkins")
    from clock import patient_now
    from scheduler import sweep_missed_for_view
    get_patient_or_404(db, patient_id)
    sweep_missed_for_view(db)
    today = patient_now(db, patient_id).date()
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date() if date else today
    except ValueError:
        raise HTTPException(422, "Date must look like 2026-11-17.")
    if abs((day - today).days) > 366:
        raise HTTPException(422, "Pick a day within a year of today.")
    start = datetime(day.year, day.month, day.day)
    rows = (db.query(Dose, Medicine).join(Medicine, Dose.medicine_id == Medicine.id)
            .join(Prescription, Medicine.prescription_id == Prescription.id)
            .filter(Prescription.patient_id == patient_id, Dose.scheduled_at >= start,
                    Dose.scheduled_at < start + timedelta(days=1), Dose.state != "cancelled")
            .order_by(Dose.scheduled_at, Dose.id).all())
    rows = [(dose, medicine) for dose, medicine in rows if medicine.status != "needs_confirmation" and medicine.name]
    return {"patient_id": patient_id, "date": day.isoformat(), "today": today.isoformat(),
            "doses": [{**serialize_dose(dose), "medicine_name": medicine.name, "food": medicine.food, "slot": slot_of(medicine, dose)} for dose, medicine in rows],
            "left": sum(1 for dose, _ in rows if dose.state in ("pending", "snoozed"))}


@router.get("/doctor/patients/{patient_id}/medicines/{medicine_id}/dose-history")
def doctor_medicine_dose_history(patient_id: int, medicine_id: int, days: int = 7,
                                  user: User = Depends(require_doctor_role), db: Session = Depends(get_db_session)):
    """Read a linked patient's dose events for one medicine over a short, patient-local window.

    The prescription page loads this only after the doctor selects a medicine,
    so large medication lists do not pull every dose record into the initial
    patient report. This is read-only and uses the same active DoctorLink gate
    as the rest of the doctor record.
    """
    _require_scope(db, user, patient_id, "current_medicines")
    _require_scope(db, user, patient_id, "dose_checkins")
    if days < 1 or days > 30:
        raise HTTPException(422, "History window must be between 1 and 30 days.")

    medicine = (db.query(Medicine).join(Prescription, Medicine.prescription_id == Prescription.id)
                .filter(Medicine.id == medicine_id, Prescription.patient_id == patient_id).first())
    if medicine is None or medicine.status == "needs_confirmation":
        raise HTTPException(404, "No confirmed medicine with that id for this patient.")

    from clock import patient_now
    from scheduler import sweep_missed_for_view
    sweep_missed_for_view(db)
    now = patient_now(db, patient_id)
    today = now.date()
    first_day = today - timedelta(days=days - 1)
    start = datetime(first_day.year, first_day.month, first_day.day)
    end_day = today + timedelta(days=1)
    end = datetime(end_day.year, end_day.month, end_day.day)
    doses = (db.query(Dose)
             .filter(Dose.medicine_id == medicine.id, Dose.scheduled_at >= start,
                     Dose.scheduled_at < end, Dose.state != "cancelled")
             .order_by(Dose.scheduled_at, Dose.id).all())
    return {
        "patient_id": patient_id,
        "medicine_id": medicine.id,
        "today": today.isoformat(),
        "patient_now": now.isoformat(),
        "days": [(today - timedelta(days=offset)).isoformat() for offset in reversed(range(days))],
        "doses": [{**serialize_dose(dose), "medicine_name": medicine.name or medicine.raw_text}
                  for dose in doses],
    }


@router.post("/doctor/patients/{patient_id}/prescriptions")
def doctor_write_prescription(patient_id: int, body: DoctorPrescriptionCreate,
                               user: User = Depends(require_doctor_role), db: Session = Depends(get_db_session)):
    """A doctor writing a prescription directly, not just correcting one the
    patient already typed in. Goes through the exact same parser and
    confidence gate as the manual path (CLAUDE.md: one parser, one gate,
    regardless of who typed the line) — a doctor's shorthand can still come
    out needs_confirmation if it's ambiguous. Left as 'draft': the PATIENT
    still confirms it before any doses are scheduled (require_write_access
    stays patient-only everywhere — a doctor never silently schedules
    something onto someone else's phone)."""
    _require_scope(db, user, patient_id, "current_medicines")
    prescription, medicines = create_prescription_from_lines(
        db, patient_id, doctor_name=user.name, issued_date=datetime.utcnow().date().isoformat(),
        lines_with_confidence=[(line, None) for line in body.lines],
        source="manual", actor=f"doctor:{user.id}:{user.name}",
    )
    return {
        "prescription_id": prescription.id,
        "status": prescription.status,
        "medicines": [serialize_medicine(m) for m, _parsed in medicines],
    }


@router.get("/doctor/templates")
def list_prescription_templates(user: User = Depends(require_doctor_role), db: Session = Depends(get_db_session)):
    templates = (
        db.query(PrescriptionTemplate)
        .filter(PrescriptionTemplate.doctor_user_id == user.id)
        .order_by(PrescriptionTemplate.created_at.desc())
        .all()
    )
    return [
        {"id": t.id, "label": t.label, "lines": _json.loads(t.lines), "created_at": t.created_at.isoformat()}
        for t in templates
    ]


@router.post("/doctor/templates")
def create_prescription_template(body: PrescriptionTemplateCreate, user: User = Depends(require_doctor_role),
                                  db: Session = Depends(get_db_session)):
    if not body.label.strip() or not any(line.strip() for line in body.lines):
        raise HTTPException(400, "A template needs a label and at least one non-empty line.")
    template = PrescriptionTemplate(
        doctor_user_id=user.id, label=body.label.strip(),
        lines=_json.dumps([line for line in body.lines if line.strip()]),
    )
    db.add(template)
    db.commit()
    return {"id": template.id, "label": template.label, "lines": _json.loads(template.lines),
            "created_at": template.created_at.isoformat()}


@router.delete("/doctor/templates/{template_id}")
def delete_prescription_template(template_id: int, user: User = Depends(require_doctor_role),
                                  db: Session = Depends(get_db_session)):
    template = db.query(PrescriptionTemplate).filter(PrescriptionTemplate.id == template_id).first()
    if not template:
        raise HTTPException(404, f"No template {template_id}")
    if template.doctor_user_id != user.id:
        raise HTTPException(403, "You can only delete your own templates.")
    db.delete(template)
    db.commit()
    return {"deleted": True}


@router.post("/doctor/medicines/{medicine_id}/correction")
def doctor_correct_medicine(medicine_id: int, body: MedicineCorrectionCreate,
                             user: User = Depends(require_doctor_role), db: Session = Depends(get_db_session)):
    """
    Doctor corrections are never silent (Part 7 of the brief): this writes
    a MedicineCorrection row with the original and corrected value before
    touching the live Medicine row. raw_text (the original prescription
    line) is never overwritten — only the parsed fields are.
    """
    medicine = get_medicine_or_404(db, medicine_id)
    patient_id = medicine.prescription.patient_id
    _require_scope(db, user, patient_id, "current_medicines")
    if body.field not in _CORRECTABLE_FIELDS:
        raise HTTPException(400, f"field must be one of: {', '.join(sorted(_CORRECTABLE_FIELDS))}")

    original_value = str(getattr(medicine, body.field))
    warning = None

    if body.field == "schedule_code":
        from shorthand import decode_schedule
        schedule = decode_schedule(body.corrected_value)
        medicine.schedule_code = schedule["scheduleCode"] or body.corrected_value
        medicine.slots = _json.dumps(schedule["slots"])
        medicine.times = _json.dumps(schedule["times"])
        medicine.is_prn = schedule["prn"]
        if schedule["durationDays"] is not None or schedule["ongoing"]:
            medicine.duration_days = schedule["durationDays"]
        if medicine.prescription.status == "confirmed":
            warning = ("This prescription was already confirmed and scheduled. Existing dose rows were "
                       "NOT regenerated — ask the patient to reconfirm if the new schedule should take effect.")
    elif body.field == "duration_days":
        medicine.duration_days = int(body.corrected_value) if body.corrected_value.strip() else None
    else:
        setattr(medicine, body.field, body.corrected_value)

    medicine.status = "verified"
    medicine.confidence = 1.0

    correction = MedicineCorrection(
        medicine_id=medicine.id, doctor_user_id=user.id, field=body.field,
        original_value=original_value, corrected_value=body.corrected_value, reason=body.reason,
    )
    db.add(correction)
    db.commit()

    log_audit(db, patient_id, f"doctor:{user.id}:{user.name}", "medicine_corrected",
              f"medicine {medicine.id} field={body.field}: {original_value!r} -> {body.corrected_value!r} "
              f"({body.reason})")

    return {"medicine": serialize_medicine(medicine), "correction_id": correction.id, "warning": warning}


@router.get("/doctor/medicines/{medicine_id}/corrections")
def get_medicine_corrections(medicine_id: int, user: User = Depends(require_doctor_role),
                              db: Session = Depends(get_db_session)):
    medicine = get_medicine_or_404(db, medicine_id)
    patient_id = medicine.prescription.patient_id
    _require_scope(db, user, patient_id, "current_medicines")
    corrections = (
        db.query(MedicineCorrection)
        .filter(MedicineCorrection.medicine_id == medicine_id)
        .order_by(MedicineCorrection.created_at.desc())
        .all()
    )
    return [
        {"id": c.id, "field": c.field, "original_value": c.original_value, "corrected_value": c.corrected_value,
         "reason": c.reason, "doctor_user_id": c.doctor_user_id, "created_at": c.created_at.isoformat()}
        for c in corrections
    ]
