"""
SmartPoli — FastAPI app.

Wires together the full journey end to end (CLAUDE.md section 5):

    image  -> OCR plugin  -----\
                                 -> parser -> Medicine[] -> review gate -> confirm -> Dose[] -> dashboard -> report
    textarea (manual) ---------/
                                                     (triage runs independently of the prescription)

Runs with an empty .env: nothing here calls an external API or needs a key.
The manual path never touches the OCR plugin (backend/ocr_plugin.py) —
that only loads (and downloads its ~1.3GB model) the first time someone
actually uploads an image, and any failure there falls back to "use manual
entry" rather than breaking anything else (CLAUDE.md section 5).
"""

import io
import json
import logging
import os
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
# .env lives at the project root (one level up from backend/), not inside
# backend/ itself — matches .env.example's location and CLAUDE.md section 13.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy.orm import Session

from apscheduler.schedulers.background import BackgroundScheduler

from db import init_db, get_db_session, SessionLocal, log_audit, Patient, Prescription, Medicine, Dose, SymptomCheck, AuditLog, User
from parser import parse_medicine_line, compute_status
from prescription_service import create_prescription_from_lines
from scheduler import (
    generate_doses, SchedulingBlocked, compute_adherence, compute_medicine_breakdown,
    mark_taken, mark_missed, mark_skipped, snooze, sweep_missed,
)
from triage import load_ruleset, evaluate_check, next_question
from interactions import load_ruleset as load_interaction_ruleset, check_interactions
from food_warnings import load_ruleset as load_food_ruleset, check_food_warnings
from ocr_plugin import run_ocr_on_image, OCRUnavailable
from report_pdf import build_report_pdf
from llm_helper import interpret_free_text, is_available as llm_is_available, LLMUnavailable
from i18n import to_plain_language_hi, localized_symptom_label, localized_question_text, localized_action
from schemas import (
    PatientCreate, PatientEdit, PrescriptionCreate, MedicineEdit, SkipDose, PrnLog,
    TriageCheckRequest, NextQuestionRequest, ClinicalNoteCreate, FreeTextTriageRequest,
)
from serializers import (
    get_patient_or_404, get_prescription_or_404, get_medicine_or_404, get_dose_or_404,
    serialize_patient, serialize_medicine, serialize_dose,
    gather_report_data, emergency_card_data as _emergency_card_data,
)
from auth import (
    get_current_user, require_patient_write_access, require_patient_read_access,
    require_medicine_write_access, require_dose_write_access, require_prescription_write_access,
    has_write_access, has_read_access,
)
from nudges import compute_nudges
from safety import build_safety_center
from ics_export import build_ics
import auth_router
import caregiver_router
import doctor_router
import whatsapp_router
import whatsapp_bot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="SmartPoli API", version="1.0.0",
              description="Prescription decoder, adherence scheduler, symptom triage and care report.")

_default_origins = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000"
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("SMARTPOLI_ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RULESET = load_ruleset()
INTERACTION_RULESET = load_interaction_ruleset()
FOOD_RULESET = load_food_ruleset()

app.include_router(auth_router.router)
app.include_router(caregiver_router.router)
app.include_router(doctor_router.router)
app.include_router(whatsapp_router.router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
def serve_frontend():
    return FileResponse("static/index.html")


reminder_scheduler = BackgroundScheduler()


def _reminder_sweep_job():
    """
    The proactive half of Feature 2's reminders (CLAUDE.md section 9):
    runs on a timer instead of only when a page happens to load, so a dose
    left untouched gets marked missed — and that transition logged to
    AuditLog — even if nobody opens the dashboard. In-browser notifications
    (the countdown hero, the voice 'remind me in N minutes') cover the
    "upcoming dose" side; this covers "dose is now overdue."
    """
    db = SessionLocal()
    try:
        count = sweep_missed(db)
        if count:
            logger.info(f"Reminder sweep: auto-marked {count} overdue dose(s) as missed.")
        if whatsapp_bot.is_configured():
            sent = whatsapp_bot.send_due_dose_reminders(db)
            if sent:
                logger.info(f"Reminder sweep: sent {sent} WhatsApp dose reminder(s).")
    finally:
        db.close()


@app.on_event("startup")
def on_startup():
    init_db()
    reminder_scheduler.add_job(_reminder_sweep_job, "interval", minutes=5, id="reminder_sweep")
    reminder_scheduler.start()
    logger.info("SmartPoli API up. Manual path only — zero API keys required. Reminder sweep running every 5 minutes.")


@app.on_event("shutdown")
def on_shutdown():
    reminder_scheduler.shutdown(wait=False)


# ---------------------------------------------------------------- patients
#
# Every patient row now has a real owner (Patient.user_id). Creating one
# requires a logged-in 'patient' account and ties the row to it; reading or
# editing requires either being that owner or (for GET) holding an active
# caregiver/doctor link — auth.require_patient_write_access /
# require_patient_read_access, never a frontend-supplied id alone (Part 18).

@app.post("/patients")
def create_patient(body: PatientCreate, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db_session)):
    if user.role != "patient":
        raise HTTPException(403, "Only a patient account can create a patient profile.")
    patient = Patient(user_id=user.id, name=body.name, age=body.age, sex=body.sex,
                       blood_group=body.blood_group,
                       allergies=body.allergies, emergency_contact=body.emergency_contact)
    db.add(patient)
    db.commit()
    log_audit(db, patient.id, f"patient:{user.id}", "patient_created", body.name)
    return serialize_patient(patient)


@app.get("/patients")
def list_patients(user: User = Depends(get_current_user), db: Session = Depends(get_db_session)):
    """Only the calling patient's own profiles — this used to return every
    patient in the system to anyone (Part 18: 'no unrestricted patient
    APIs'). Caregivers/doctors have their own /caregiver and /doctor
    listings, which are relationship-scoped."""
    if user.role != "patient":
        raise HTTPException(403, "Use /caregiver/patients or /doctor/patients for this role.")
    return [serialize_patient(p) for p in db.query(Patient).filter(Patient.user_id == user.id).all()]


@app.get("/patients/{patient_id}")
def get_patient(patient_id: int, user: User = Depends(require_patient_read_access),
                 db: Session = Depends(get_db_session)):
    return serialize_patient(get_patient_or_404(db, patient_id))


@app.patch("/patients/{patient_id}")
def edit_patient(patient_id: int, body: PatientEdit, user: User = Depends(require_patient_write_access),
                  db: Session = Depends(get_db_session)):
    patient = get_patient_or_404(db, patient_id)
    for field in ("name", "age", "sex", "blood_group", "allergies", "emergency_contact"):
        value = getattr(body, field)
        if value is not None:
            setattr(patient, field, value)
    db.commit()
    log_audit(db, patient.id, f"patient:{user.id}", "patient_updated", "profile edited")
    return serialize_patient(patient)


# ---------------------------------------------------------------- prescriptions (Feature 1)
#
# create_prescription_from_lines lives in prescription_service.py now —
# shared with whatsapp_bot.py, so a prescription line typed, photographed,
# or sent over WhatsApp goes through the exact same parser/confidence path
# (CLAUDE.md section 5/6).
_create_prescription_from_lines = create_prescription_from_lines


@app.post("/prescriptions")
def create_prescription(body: PrescriptionCreate, user: User = Depends(get_current_user),
                         db: Session = Depends(get_db_session)):
    """Manual path: raw lines in, parsed+confidence-scored Medicine rows out. Nothing scheduled yet.
    Only the owning patient may add a prescription to their own record."""
    get_patient_or_404(db, body.patient_id)
    if not has_write_access(db, user, body.patient_id):
        raise HTTPException(403, "Only the patient can add a prescription to their own record.")
    prescription, medicines = _create_prescription_from_lines(
        db, body.patient_id, body.doctor_name, body.issued_date,
        [(line, None) for line in body.lines], source="manual", actor=f"patient:{user.id}",
    )

    return {
        "prescription_id": prescription.id,
        "status": prescription.status,
        "medicines": [
            {**serialize_medicine(m), "plain_language": parsed["plain_language"], "notes": parsed["notes"]}
            for m, parsed in medicines
        ],
    }


@app.post("/prescriptions/from-image")
async def create_prescription_from_image(
    patient_id: int = Form(...),
    doctor_name: Optional[str] = Form(None),
    issued_date: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """
    Image path: photo -> OpenCV preprocessing -> per-line TrOCR -> the SAME
    parser.py the manual path uses. No LLM extraction step and no API key —
    OCR only turns pixels into text lines; the deterministic parser (already
    the source of truth for the manual path) does everything after that,
    so there is exactly one extraction/confidence/scheduling path regardless
    of source (CLAUDE.md section 5/6).

    If the OCR plugin can't run at all (model failed to load, bad image),
    this returns a clear 503 telling the caller to use manual entry —
    it never leaves the manual path degraded.
    """
    get_patient_or_404(db, patient_id)
    if not has_write_access(db, user, patient_id):
        raise HTTPException(403, "Only the patient can add a prescription to their own record.")

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(400, "Empty file upload.")

    try:
        ocr_lines = run_ocr_on_image(image_bytes)
    except OCRUnavailable as e:
        raise HTTPException(
            503,
            f"OCR is unavailable right now ({e}). Use manual prescription entry instead — "
            "it covers every feature the image path does.",
        )

    if not ocr_lines:
        raise HTTPException(
            422,
            "No readable text lines were found in that image. Try a clearer photo, "
            "or use manual entry.",
        )

    prescription, medicines = _create_prescription_from_lines(
        db, patient_id, doctor_name, issued_date,
        [(line["text"], line["confidence"]) for line in ocr_lines], source="ocr", actor=f"patient:{user.id}",
    )

    return {
        "prescription_id": prescription.id,
        "status": prescription.status,
        "ocr_lines_found": len(ocr_lines),
        "medicines": [
            {**serialize_medicine(m), "plain_language": parsed["plain_language"], "notes": parsed["notes"]}
            for m, parsed in medicines
        ],
    }


@app.patch("/medicines/{medicine_id}")
def edit_medicine(medicine_id: int, body: MedicineEdit, user: User = Depends(require_medicine_write_access),
                   db: Session = Depends(get_db_session)):
    """
    Review & confirm screen (CLAUDE.md section 8): a human corrects a
    needs_confirmation medicine. Re-derives the schedule from any corrected
    schedule_code and marks it verified — a human just looked at it.
    """
    medicine = get_medicine_or_404(db, medicine_id)

    from shorthand import decode_schedule

    if body.name is not None:
        medicine.name = body.name
        medicine.normalized_name = body.name
    if body.dose_amount is not None:
        medicine.dose_amount = body.dose_amount
    if body.dose_unit is not None:
        medicine.dose_unit = body.dose_unit

    if body.schedule_code is not None:
        schedule = decode_schedule(body.schedule_code)
        medicine.schedule_code = schedule["scheduleCode"] or body.schedule_code
        medicine.slots = json.dumps(schedule["slots"])
        medicine.times = json.dumps(schedule["times"])
        medicine.is_prn = schedule["prn"]
        if schedule["durationDays"] is not None or schedule["ongoing"]:
            medicine.duration_days = schedule["durationDays"]
        medicine.plain_language_hi = to_plain_language_hi(schedule)

    if body.food is not None:
        medicine.food = body.food
    if body.duration_days is not None:
        medicine.duration_days = body.duration_days
    if body.ongoing:
        medicine.duration_days = None

    # A human has now reviewed and corrected this line — it is verified.
    medicine.confidence = 1.0
    medicine.field_confidence = json.dumps({"name": 1.0, "schedule": 1.0, "source": "human_confirmed"})
    medicine.status = "verified"

    db.commit()
    log_audit(db, medicine.prescription.patient_id, f"patient:{user.id}", "medicine_confirmed",
              f"medicine {medicine.id}: {medicine.raw_text!r}")
    return serialize_medicine(medicine)


@app.post("/prescriptions/{prescription_id}/confirm")
def confirm_prescription(prescription_id: int, user: User = Depends(require_prescription_write_access),
                          db: Session = Depends(get_db_session)):
    """
    Generate Dose rows for every medicine that is safe to schedule.

    needs_confirmation medicines are silently skipped here (they stay in the
    prescription, unscheduled, for later editing) — but if anything upstream
    ever tries to force one through generate_doses() directly, that raises
    SchedulingBlocked. The gate lives in the scheduler, not in this endpoint.
    """
    prescription = get_prescription_or_404(db, prescription_id)
    medicines = db.query(Medicine).filter(Medicine.prescription_id == prescription_id).all()

    scheduled, blocked, prn = [], [], []
    for medicine in medicines:
        if medicine.status == "needs_confirmation":
            blocked.append(medicine.id)
            continue
        try:
            doses = generate_doses(medicine)
        except SchedulingBlocked:
            blocked.append(medicine.id)
            continue
        if medicine.is_prn:
            prn.append(medicine.id)
            continue
        db.add_all(doses)
        scheduled.append({"medicine_id": medicine.id, "doses_generated": len(doses)})

    prescription.status = "confirmed"
    db.commit()
    log_audit(db, prescription.patient_id, f"patient:{user.id}", "prescription_confirmed",
              f"scheduled={len(scheduled)} blocked={len(blocked)} prn={len(prn)}")

    return {"prescription_id": prescription.id, "scheduled": scheduled, "blocked_needs_confirmation": blocked, "prn": prn}


# ---------------------------------------------------------------- doses & dashboard (Feature 2)

@app.post("/doses/{dose_id}/take")
def take_dose(dose_id: int, user: User = Depends(require_dose_write_access), db: Session = Depends(get_db_session)):
    dose = get_dose_or_404(db, dose_id)
    mark_taken(dose)
    db.commit()
    log_audit(db, dose.medicine.prescription.patient_id, f"patient:{user.id}", "dose_taken", f"dose {dose.id}")
    return serialize_dose(dose)


@app.post("/doses/{dose_id}/miss")
def miss_dose(dose_id: int, user: User = Depends(require_dose_write_access), db: Session = Depends(get_db_session)):
    dose = get_dose_or_404(db, dose_id)
    mark_missed(dose)
    db.commit()
    log_audit(db, dose.medicine.prescription.patient_id, f"patient:{user.id}", "dose_missed", f"dose {dose.id}")
    return serialize_dose(dose)


@app.post("/doses/{dose_id}/skip")
def skip_dose(dose_id: int, body: SkipDose, user: User = Depends(require_dose_write_access),
               db: Session = Depends(get_db_session)):
    dose = get_dose_or_404(db, dose_id)
    try:
        mark_skipped(dose, body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    log_audit(db, dose.medicine.prescription.patient_id, f"patient:{user.id}", "dose_skipped",
              f"dose {dose.id}: {body.reason}")
    return serialize_dose(dose)


@app.post("/doses/{dose_id}/snooze")
def snooze_dose(dose_id: int, user: User = Depends(require_dose_write_access), db: Session = Depends(get_db_session)):
    dose = get_dose_or_404(db, dose_id)
    snooze(dose)
    db.commit()
    return serialize_dose(dose)


@app.post("/medicines/{medicine_id}/log-prn")
def log_prn(medicine_id: int, user: User = Depends(require_medicine_write_access),
            db: Session = Depends(get_db_session)):
    """PRN/SOS medicines never get Dose rows — logging a use is an AuditLog entry."""
    medicine = get_medicine_or_404(db, medicine_id)
    if not medicine.is_prn:
        raise HTTPException(400, "This medicine is not PRN/SOS.")
    log_audit(db, medicine.prescription.patient_id, f"patient:{user.id}", "prn_taken",
              f"medicine {medicine.id} ({medicine.name or medicine.raw_text}) at {datetime.utcnow().isoformat()}")
    return {"logged": True, "medicine_id": medicine_id, "at": datetime.utcnow().isoformat()}


@app.get("/patients/{patient_id}/dashboard")
def dashboard(patient_id: int, user: User = Depends(require_patient_read_access),
              db: Session = Depends(get_db_session)):
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

    return {
        "patient_id": patient_id,
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
        "interactions": check_interactions(
            INTERACTION_RULESET,
            [m.name for pres in prescriptions for m in pres.medicines if m.status != "needs_confirmation" and m.name],
        ),
        "food_warnings": check_food_warnings(
            FOOD_RULESET,
            [m.name for pres in prescriptions for m in pres.medicines if m.status != "needs_confirmation" and m.name],
        ),
        # Part 4 of the brief — deterministic, derived only from the Dose
        # rows already fetched above. Never invents a dosage or overrides
        # the prescription; see nudges.py.
        "nudges": compute_nudges(medicines_with_doses, now),
    }


@app.get("/patients/{patient_id}/safety-center")
def safety_center(patient_id: int, user: User = Depends(require_patient_read_access),
                   db: Session = Depends(get_db_session)):
    """Part 3 of the brief — one aggregated view over the EXISTING interaction
    and food-warning engines plus a narrow dosage sanity check. See safety.py."""
    get_patient_or_404(db, patient_id)
    prescriptions = db.query(Prescription).filter(Prescription.patient_id == patient_id).all()
    medicines = [m for pres in prescriptions for m in pres.medicines]
    return build_safety_center(INTERACTION_RULESET, FOOD_RULESET, medicines)


@app.get("/patients/{patient_id}/calendar.ics")
def patient_calendar_ics(patient_id: int, user: User = Depends(require_patient_read_access),
                          db: Session = Depends(get_db_session)):
    """Part 8 of the brief — exports the EXISTING schedule (Dose rows already
    generated at confirmation) as a standard .ics feed. No second scheduler."""
    patient = get_patient_or_404(db, patient_id)
    prescriptions = db.query(Prescription).filter(Prescription.patient_id == patient_id).all()
    medicines_with_doses = [(m, m.doses) for pres in prescriptions for m in pres.medicines if not m.is_prn]
    ics_bytes = build_ics(medicines_with_doses, datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"))
    filename = f"smartpoli_schedule_{patient.name.replace(' ', '_')}.ics"
    return Response(
        content=ics_bytes, media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/patients/{patient_id}/interactions")
def patient_interactions(patient_id: int, user: User = Depends(require_patient_read_access),
                          db: Session = Depends(get_db_session)):
    """
    Feature D (optional, CLAUDE.md section 4) — pairwise-checks the
    patient's active medicine names against a local, deterministic
    interaction table. Never a diagnosis or treatment instruction, and
    never blocks scheduling — purely informational.
    """
    get_patient_or_404(db, patient_id)
    prescriptions = db.query(Prescription).filter(Prescription.patient_id == patient_id).all()
    names = [m.name for pres in prescriptions for m in pres.medicines if m.status != "needs_confirmation" and m.name]
    return {"patient_id": patient_id, "interactions": check_interactions(INTERACTION_RULESET, names)}


@app.get("/patients/{patient_id}/food-warnings")
def patient_food_warnings(patient_id: int, user: User = Depends(require_patient_read_access),
                           db: Session = Depends(get_db_session)):
    """Feature E (optional, CLAUDE.md section 4) — specific foods/substances
    to avoid per active medicine. Distinct from the shorthand engine's
    before/after-meals timing field."""
    get_patient_or_404(db, patient_id)
    prescriptions = db.query(Prescription).filter(Prescription.patient_id == patient_id).all()
    names = [m.name for pres in prescriptions for m in pres.medicines if m.status != "needs_confirmation" and m.name]
    return {"patient_id": patient_id, "food_warnings": check_food_warnings(FOOD_RULESET, names)}


# ---------------------------------------------------------------- triage (Feature 3)

@app.get("/triage/symptoms")
def get_symptoms(lang: str = "en"):
    return [
        {"id": sid, "label": localized_symptom_label(s, lang)}
        for sid, s in RULESET["symptoms"].items()
    ]


@app.get("/triage/llm-available")
def triage_llm_available():
    return {"available": llm_is_available()}


@app.post("/triage/interpret-free-text")
def triage_interpret_free_text(body: FreeTextTriageRequest):
    """
    Optional LLM assist: free text -> candidate symptom_ids + answers, for
    the user to review before anything is submitted. Never returns a
    severity — evaluate_check() is the only thing that ever decides that.
    """
    try:
        return interpret_free_text(body.text, RULESET)
    except LLMUnavailable as e:
        raise HTTPException(503, f"Free-text interpretation is unavailable ({e}). Use the symptom picker instead.")


@app.get("/triage/symptoms/{symptom_id}/questions")
def get_symptom_questions(symptom_id: str, lang: str = "en"):
    if symptom_id not in RULESET["symptoms"]:
        raise HTTPException(404, f"Unknown symptom {symptom_id}")
    return [
        {"id": q["id"], "text": localized_question_text(q, lang), "type": q["type"]}
        for q in RULESET["symptoms"][symptom_id]["questions"]
    ]


@app.post("/triage/next-question")
def get_next_question(body: NextQuestionRequest):
    if body.symptom_id not in RULESET["symptoms"]:
        raise HTTPException(404, f"Unknown symptom {body.symptom_id}")
    q = next_question(RULESET, body.symptom_id, body.answers, body.current_severity)
    return {"question": q}


@app.post("/triage/preview")
def triage_preview(body: TriageCheckRequest, lang: str = "en"):
    """Same evaluation as /triage/check but never persisted — used by the
    question-by-question UI to decide whether to keep asking (rule 2:
    stop once EMERGENCY) without writing a SymptomCheck row per keystroke."""
    for sid in body.symptom_ids:
        if sid not in RULESET["symptoms"]:
            raise HTTPException(404, f"Unknown symptom {sid}")
    result = evaluate_check(RULESET, body.symptom_ids, body.answers)
    return {**result, "action": localized_action(result["action"], lang)}


@app.post("/triage/check")
def triage_check(body: TriageCheckRequest, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db_session), lang: str = "en"):
    get_patient_or_404(db, body.patient_id)
    if not has_write_access(db, user, body.patient_id):
        raise HTTPException(403, "Only the patient can log a symptom check on their own record.")
    for sid in body.symptom_ids:
        if sid not in RULESET["symptoms"]:
            raise HTTPException(404, f"Unknown symptom {sid}")

    result = evaluate_check(RULESET, body.symptom_ids, body.answers)

    check = SymptomCheck(
        patient_id=body.patient_id,
        symptoms=json.dumps(body.symptom_ids),
        answers=json.dumps(body.answers),
        severity=result["severity"],
        reasons=json.dumps(result["reasons"]),
        action=result["action"],
        ruleset_version=result["ruleset_version"],
    )
    db.add(check)
    db.commit()

    log_audit(db, body.patient_id, f"patient:{user.id}", "triage_check",
              f"severity={result['severity']} symptoms={body.symptom_ids}")

    return {**result, "check_id": check.id, "action": localized_action(result["action"], lang)}


# ---------------------------------------------------------------- clinical notes (doctor view)
#
# Reuses AuditLog rather than a new model — CLAUDE.md/the implementation
# brief both call for exactly one audit trail, not a second table just for
# notes. A note is simply an audit entry with action='clinical_note' whose
# actor is the AUTHENTICATED doctor's real identity — never a client-
# supplied 'actor' string (that was the exact hole Part 8 of the brief
# flags: `actor = "doctor"` in the request body proved nothing).

@app.post("/patients/{patient_id}/notes")
def add_clinical_note(patient_id: int, body: ClinicalNoteCreate, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db_session)):
    if user.role != "doctor":
        raise HTTPException(403, "Only a doctor account can add a clinical note.")
    if not has_read_access(db, user, patient_id):
        raise HTTPException(403, "You are not linked to this patient.")
    get_patient_or_404(db, patient_id)
    log_audit(db, patient_id, f"doctor:{user.id}:{user.name}", "clinical_note", body.note)
    return {"logged": True}


_TIMELINE_LABELS = {
    "patient_created": "Patient profile created",
    "patient_updated": "Patient profile updated",
    "prescription_created": "Prescription added",
    "prescription_confirmed": "Prescription confirmed and scheduled",
    "medicine_confirmed": "A flagged medicine was reviewed and confirmed",
    "dose_taken": "Dose marked as taken",
    "dose_missed": "Dose marked as missed",
    "dose_skipped": "Dose skipped",
    "dose_auto_missed": "Dose automatically marked missed (no response after 2h)",
    "prn_taken": "As-needed medicine logged",
    "triage_check": "Symptom check completed",
    "clinical_note": "Doctor/caregiver note added",
    "demo_seeded": "Demo data seeded",
}


@app.get("/patients/{patient_id}/timeline")
def patient_timeline(patient_id: int, user: User = Depends(require_patient_read_access),
                      db: Session = Depends(get_db_session)):
    """
    Feature 'unified patient timeline' — one chronological view over the
    same AuditLog every other feature already writes to (no second,
    competing history table). Turns SmartPoli's separate features into one
    visible treatment journey, which is the whole point of the product.
    """
    get_patient_or_404(db, patient_id)
    entries = (
        db.query(AuditLog)
        .filter(AuditLog.patient_id == patient_id)
        .order_by(AuditLog.at.desc())
        .limit(200)
        .all()
    )
    return [
        {
            "id": e.id,
            "at": e.at.isoformat(),
            "actor": e.actor,
            "action": e.action,
            "summary": _TIMELINE_LABELS.get(e.action, e.action.replace("_", " ")),
            "detail": e.detail,
        }
        for e in entries
    ]


@app.get("/patients/{patient_id}/notes")
def get_clinical_notes(patient_id: int, user: User = Depends(require_patient_read_access),
                        db: Session = Depends(get_db_session)):
    get_patient_or_404(db, patient_id)
    notes = (
        db.query(AuditLog)
        .filter(AuditLog.patient_id == patient_id, AuditLog.action == "clinical_note")
        .order_by(AuditLog.at.desc())
        .all()
    )
    return [{"id": n.id, "actor": n.actor, "note": n.detail, "at": n.at.isoformat()} for n in notes]


# ---------------------------------------------------------------- report (Feature 4)
#
# gather_report_data lives in serializers.py now — the doctor dashboard
# (doctor_router.py) reuses the exact same function, so there is one report
# shape, not a second one built for the doctor view.

@app.get("/patients/{patient_id}/report")
def patient_report(patient_id: int, user: User = Depends(require_patient_read_access),
                    db: Session = Depends(get_db_session)):
    return gather_report_data(db, patient_id, INTERACTION_RULESET, FOOD_RULESET)


@app.get("/patients/{patient_id}/report/pdf")
def patient_report_pdf(patient_id: int, user: User = Depends(require_patient_read_access),
                        db: Session = Depends(get_db_session)):
    """A real, downloadable PDF — not just the browser's Print to PDF —
    built with ReportLab, SmartPoli's own canonical report renderer."""
    data = gather_report_data(db, patient_id, INTERACTION_RULESET, FOOD_RULESET)
    pdf_bytes = build_report_pdf(data)
    filename = f"smartpoli_report_{data['patient']['name'].replace(' ', '_')}.pdf"
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------- emergency card (Feature J, optional)
#
# _emergency_card_data (imported above from serializers.py as
# emergency_card_data) is used two ways: the authenticated JSON endpoint
# below requires patient/caregiver/doctor read access like everything else,
# but /emergency/{patient_id} — what the printed QR code actually opens —
# is DELIBERATELY left with no login requirement. A stranger finding an
# unconscious patient does not have that patient's password; the whole
# point of an emergency card is that it works without one (CLAUDE.md
# section 11, Part 15/16 of the brief). It exposes only name/age/sex/
# blood group/allergies/emergency contact/current medicines — nothing else
# from the record.

@app.get("/patients/{patient_id}/emergency-card")
def get_emergency_card_data(patient_id: int, user: User = Depends(require_patient_read_access),
                             db: Session = Depends(get_db_session)):
    return _emergency_card_data(db, patient_id)


@app.get("/patients/{patient_id}/emergency-card/qr.png")
def emergency_card_qr(patient_id: int, request: Request, user: User = Depends(require_patient_read_access),
                       db: Session = Depends(get_db_session)):
    get_patient_or_404(db, patient_id)
    import qrcode
    url = str(request.base_url).rstrip("/") + f"/emergency/{patient_id}"
    img = qrcode.make(url, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/emergency/{patient_id}", response_class=HTMLResponse, include_in_schema=False)
def emergency_card_page(patient_id: int, db: Session = Depends(get_db_session)):
    """Public, standalone, no-login card — what a QR scan opens. Deliberately
    plain server-rendered HTML, not the SPA, so it works even on a phone
    browser with nothing cached and renders instantly."""
    data = _emergency_card_data(db, patient_id)
    p = data["patient"]

    def rows(items, empty_text):
        if not items:
            return f'<p class="muted">{empty_text}</p>'
        lis = "".join(
            f'<li><strong>{i["name"]}</strong>'
            f'{" " + i["dose_amount"] + (i.get("dose_unit") or "") if i.get("dose_amount") else ""}'
            f'{" — " + i["schedule_code"] if i.get("schedule_code") else ""}</li>'
            for i in items
        )
        return f"<ul>{lis}</ul>"

    emergency_flag = (
        '<div class="flag">This patient has a history of an EMERGENCY-graded symptom check in SmartPoli.</div>'
        if data["has_emergency_triage_history"] else ""
    )

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Emergency card — {p['name']}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #fff; color: #1a1a1a; margin: 0; padding: 24px; max-width: 480px; }}
  h1 {{ color: #AE2B22; font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: #555; font-size: 14px; margin-bottom: 20px; }}
  .flag {{ background: #AE2B22; color: #fff; padding: 10px 14px; border-radius: 6px; font-weight: 600; margin-bottom: 18px; }}
  section {{ margin-bottom: 18px; }}
  h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 0.04em; color: #777; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  ul {{ padding-left: 20px; margin: 6px 0; }}
  .muted {{ color: #888; font-size: 14px; }}
  .allergy {{ background: #F5DBD7; color: #AE2B22; padding: 8px 12px; border-radius: 6px; font-weight: 600; }}
  .offline-note {{ background: #444; color: #fff; font-size: 12px; padding: 6px 10px; border-radius: 6px; margin-bottom: 14px; }}
  footer {{ font-size: 11px; color: #999; margin-top: 24px; border-top: 1px solid #eee; padding-top: 10px; }}
</style></head>
<body>
  <h1>Emergency medical information</h1>
  <div class="sub">{p['name']}{', ' + str(p['age']) if p['age'] else ''}{', ' + p['sex'] if p['sex'] else ''}{', blood group ' + p['blood_group'] if p.get('blood_group') else ''}</div>
  <div class="offline-note" id="offlineNote" style="display:none;">Showing OFFLINE / CACHED information — this may be out of date.</div>
  {emergency_flag}
  <section>
    <h2>Allergies</h2>
    {f'<div class="allergy">{p["allergies"]}</div>' if p['allergies'] else '<p class="muted">None recorded.</p>'}
  </section>
  <section>
    <h2>Emergency contact</h2>
    <p>{p['emergency_contact'] or '<span class="muted">None recorded.</span>'}</p>
  </section>
  <section>
    <h2>Current scheduled medicines</h2>
    {rows(data['scheduled_medicines'], 'None on file.')}
  </section>
  <section>
    <h2>As-needed medicines</h2>
    {rows(data['as_needed_medicines'], 'None on file.')}
  </section>
  <footer>{data['disclaimer']}</footer>
  <script>
    // Offline emergency fallback (Part 16 of the brief): this page registers
    // its own tiny service worker scoped ONLY to itself and the QR image, so
    // once a patient/caregiver has opened it once, it still opens with no
    // signal — the badge below makes clear when that's what's happening.
    // The rest of the app is NOT made offline-capable; this is deliberately
    // narrow to "critical emergency information only".
    function updateOfflineBadge() {{
      document.getElementById('offlineNote').style.display = navigator.onLine ? 'none' : 'block';
    }}
    window.addEventListener('online', updateOfflineBadge);
    window.addEventListener('offline', updateOfflineBadge);
    updateOfflineBadge();
    if ('serviceWorker' in navigator) {{
      navigator.serviceWorker.register('/static/sw-emergency.js', {{ scope: '/emergency/' }}).catch(() => {{}});
    }}
  </script>
</body></html>"""
    return HTMLResponse(content=html)
