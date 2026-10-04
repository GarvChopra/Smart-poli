"""
SmartPoli - the patient's own daily routine.

A prescription says "morning / night" and "before / after food", not "08:30".
Until now every patient got the same fixed clock times and every medicine in a
slot landed on the very same minute. The routine lets each patient say when
their mornings, afternoons, evenings, nights and bedtimes actually are, and
the dose times follow from that:

    after food / any food   -> the routine time for the slot
    before food (AC)        -> 30 minutes earlier (same shift the shorthand decoder already used)

Only medicines written as slots (1-0-1, BD, TDS ...) follow the routine.
Medicines written as exact clock times or "every N hours" (Q8H, Q12H ...) keep
the times they were written with, and as-needed (PRN) medicines have no times.

Until a patient saves a routine, nothing changes: their medicines keep the
standard times they were parsed with. These times and the 30-minute offset are
product defaults for convenience, not medical advice - the prescription, the
label and the pharmacist decide.

The reminder preferences (a last heads-up, a follow-up) live in the same row.
"""

import json
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from shorthand import DEFAULT_SLOT_TIMES, SLOT_ORDER, shift_minutes

BEFORE_FOOD_SHIFT_MIN = -30

SLOT_LABELS = {
    "morning": "Morning (after breakfast)",
    "afternoon": "Afternoon (after lunch)",
    "evening": "Evening",
    "night": "Night (after dinner)",
    "bedtime": "Bedtime",
}

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def valid_hhmm(value) -> bool:
    return isinstance(value, str) and bool(_HHMM.match(value))


def routine_row(db: Session, patient_id: int):
    from db import PatientRoutine
    return db.query(PatientRoutine).filter(PatientRoutine.patient_id == patient_id).first()


def get_routine(db: Session, patient_id: int) -> dict:
    """The slot -> 'HH:MM' map in effect (saved values over the standard defaults) plus reminder toggles."""
    row = routine_row(db, patient_id)
    times = {slot: (getattr(row, slot, None) if row and valid_hhmm(getattr(row, slot, None)) else DEFAULT_SLOT_TIMES[slot])
             for slot in SLOT_ORDER}
    return {
        "times": times,
        "is_saved": row is not None,
        "notify_soon": True if row is None or row.notify_soon is None else bool(row.notify_soon),
        "notify_followup": True if row is None or row.notify_followup is None else bool(row.notify_followup),
        "defaults": dict(DEFAULT_SLOT_TIMES),
        "labels": dict(SLOT_LABELS),
    }


def validate_times(times: dict) -> Optional[str]:
    """None if the five times are valid and in the order of the day, else a plain-language reason."""
    for slot in SLOT_ORDER:
        if not valid_hhmm(times.get(slot)):
            return f"{SLOT_LABELS[slot]}: enter a time like 08:30."
    ordered = [times[s] for s in SLOT_ORDER]
    if any(a >= b for a, b in zip(ordered, ordered[1:])):
        return "Times must go in order through the day: morning, afternoon, evening, night, bedtime."
    return None


def medicine_slots(medicine) -> list[str]:
    try:
        slots = json.loads(medicine.slots) if medicine.slots else []
    except ValueError:
        return []
    return slots if isinstance(slots, list) else []


def times_for_medicine(medicine, routine_times: dict) -> Optional[list[str]]:
    """Dose times from the routine for a slot-based medicine; None when the medicine does not follow
    the routine (exact times, every-N-hours, PRN, no slots)."""
    slots = medicine_slots(medicine)
    if not slots or medicine.is_prn:
        return None
    out = []
    for slot in slots:
        base = routine_times.get(slot)
        if not base:
            return None
        out.append(shift_minutes(base, BEFORE_FOOD_SHIFT_MIN) if medicine.food == "before" else base)
    return out


def apply_to_new_medicine(db: Session, medicine, patient_id: int) -> bool:
    """Called when a prescription is confirmed: if the patient saved a routine, set the medicine's
    times from it before doses are generated. Returns True if the times changed."""
    row = routine_row(db, patient_id)
    if row is None:
        return False
    new = times_for_medicine(medicine, get_routine(db, patient_id)["times"])
    if new is None:
        return False
    try:
        old = json.loads(medicine.times) if medicine.times else []
    except ValueError:
        old = []
    if new == old:
        return False
    medicine.times = json.dumps(new)
    return True


def move_pending_doses(db: Session, patient_id: int, now: datetime) -> dict:
    """Re-time the patient's FUTURE, untouched doses to the routine. A dose is moved only if it is still
    pending/snoozed, is in the future, and sits exactly on the medicine's old time (so a dose that was
    already moved for a verified spacing rule, or one already acted on, is never touched). Idempotent."""
    from db import Dose, Medicine, Prescription, ReminderLog
    routine = get_routine(db, patient_id)["times"]
    meds = (db.query(Medicine).join(Prescription)
            .filter(Prescription.patient_id == patient_id, Medicine.status != "needs_confirmation").all())
    medicines_changed = doses_moved = 0
    for med in meds:
        new = times_for_medicine(med, routine)
        if new is None:
            continue
        try:
            old = json.loads(med.times) if med.times else []
        except ValueError:
            old = []
        if new == old or len(new) != len(old):
            continue
        mapping = dict(zip(old, new))
        moved_here = 0
        for dose in med.doses:
            if dose.state not in ("pending", "snoozed") or dose.scheduled_at <= now:
                continue
            hhmm = dose.scheduled_at.strftime("%H:%M")
            target = mapping.get(hhmm)
            if not target or target == hhmm:
                continue
            h, m = (int(x) for x in target.split(":"))
            new_dt = dose.scheduled_at.replace(hour=h, minute=m)
            if new_dt <= now:
                continue                        # the new time already passed today: leave today's dose alone
            dose.scheduled_at = new_dt
            db.query(ReminderLog).filter(ReminderLog.dose_id == dose.id).delete(synchronize_session=False)
            moved_here += 1
        med.times = json.dumps(new)
        medicines_changed += 1
        doses_moved += moved_here
    db.commit()
    return {"medicines_changed": medicines_changed, "doses_moved": doses_moved}
