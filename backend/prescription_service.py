"""
Shared prescription-creation logic — the one place raw lines (typed,
OCR'd, or sent over WhatsApp) turn into Medicine rows. Used by main.py's
manual and image-upload endpoints AND by whatsapp_bot.py, so there is
exactly one extraction/confidence path regardless of source
(CLAUDE.md section 5/6) — never a second implementation living inside the
WhatsApp integration.
"""

import json
from typing import Optional

from sqlalchemy.orm import Session

from db import log_audit, Prescription, Medicine
from parser import parse_medicine_line, compute_status
from scheduler import generate_doses, SchedulingBlocked
from clock import patient_now


def create_prescription_from_lines(
    db: Session, patient_id: int, doctor_name: Optional[str], issued_date: Optional[str],
    lines_with_confidence: list[tuple[str, Optional[float]]], source: str, actor: str = "user",
) -> tuple[Prescription, list[tuple[Medicine, dict]]]:
    """
    `lines_with_confidence` pairs each raw line with an optional OCR
    confidence (None for manually-typed/WhatsApp-typed lines); when
    present, it is folded into the overall confidence as one more thing
    that had to be read correctly, on top of the name and schedule
    confidence.
    """
    prescription = Prescription(
        patient_id=patient_id, doctor_name=doctor_name, issued_date=issued_date,
        source=source, status="draft",
    )
    db.add(prescription)
    db.commit()

    medicines = []
    for line, ocr_confidence in lines_with_confidence:
        if not line.strip():
            continue
        parsed = parse_medicine_line(line)

        overall = parsed["confidence"]
        field_confidence = dict(parsed["field_confidence"])
        if ocr_confidence is not None:
            field_confidence["ocr"] = round(ocr_confidence, 4)
            overall = min(overall, ocr_confidence)
            parsed["status"] = compute_status(overall)
        parsed["confidence"] = overall
        parsed["field_confidence"] = field_confidence

        medicine = Medicine(
            prescription_id=prescription.id,
            raw_text=parsed["raw_text"],
            name=parsed["name"],
            normalized_name=parsed["normalized_name"],
            dose_amount=parsed["dose_amount"],
            dose_unit=parsed["dose_unit"],
            schedule_code=parsed["schedule_code"],
            slots=json.dumps(parsed["slots"]),
            times=json.dumps(parsed["times"]),
            food=parsed["food"],
            duration_days=parsed["duration_days"],
            is_prn=parsed["is_prn"],
            confidence=parsed["confidence"],
            field_confidence=json.dumps(parsed["field_confidence"]),
            status=parsed["status"],
            plain_language_hi=parsed["plain_language_hi"],
        )
        db.add(medicine)
        medicines.append((medicine, parsed))

    db.commit()
    log_audit(db, patient_id, actor, "prescription_created",
              f"prescription {prescription.id}, {len(medicines)} lines, source={source}")
    return prescription, medicines


def confirm_prescription_doses(db: Session, prescription: Prescription, actor: str) -> dict:
    """
    Generate Dose rows for every medicine that is safe to schedule — shared
    by the Confirm button (main.py) and the voice assistant.

    needs_confirmation medicines are silently skipped here (they stay in the
    prescription, unscheduled, for later editing) — but if anything upstream
    ever tries to force one through generate_doses() directly, that raises
    SchedulingBlocked. The gate lives in the scheduler, not in this function.
    """
    medicines = db.query(Medicine).filter(Medicine.prescription_id == prescription.id).all()
    # Dose times are the patient's local wall-clock, so "now" must be too.
    local_start = patient_now(db, prescription.patient_id)

    scheduled, blocked, prn = [], [], []
    for medicine in medicines:
        if medicine.status == "needs_confirmation":
            blocked.append(medicine.id)
            continue
        try:
            doses = generate_doses(medicine, start_at=local_start, skip_past=True)
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
    log_audit(db, prescription.patient_id, actor, "prescription_confirmed",
              f"scheduled={len(scheduled)} blocked={len(blocked)} prn={len(prn)}")

    # Tell the patient now (once per pair) if the new medicines need spacing from their others.
    try:
        import reminders
        reminders.notify_new_conflicts(db, prescription.patient_id)
    except Exception:  # noqa: BLE001 - a notification problem must never fail a confirmation
        import logging
        logging.getLogger(__name__).exception("conflict notice failed after confirm")

    return {"prescription_id": prescription.id, "scheduled": scheduled, "blocked_needs_confirmation": blocked, "prn": prn}
