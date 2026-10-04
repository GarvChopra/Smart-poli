"""
SmartPoli — Feature 2: medication scheduler, dose state machine, adherence.

Dose rows are generated ONCE at confirmation and never recomputed (CLAUDE.md
section 6, invariant 1). Adherence is then plain row counting — no date maths
at render time, no drift.

The confidence gate is enforced HERE, not in the UI. A `needs_confirmation`
medicine reaching this module raises SchedulingBlocked. A disabled button in
the frontend is not a safety control.
"""

import json
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from db import Medicine, Dose

MISSED_AFTER = timedelta(hours=2)
MAX_SNOOZES = 3
SNOOZE_MINUTES = 15


class SchedulingBlocked(Exception):
    """Raised when something tries to schedule a medicine that hasn't been confirmed safe."""


def generate_doses(medicine: Medicine, start_at: Optional[datetime] = None, skip_past: bool = False) -> list[Dose]:
    """
    Build the Dose rows for one confirmed medicine.

    - needs_confirmation medicines are refused outright (the gate).
    - PRN/SOS medicines generate ZERO dose rows — they are logged manually.
    - STAT medicines generate exactly one dose, right now, not repeated daily.
    - Ongoing (duration_days is None) medicines are scheduled 30 days forward,
      per CLAUDE.md 7.4 — never invent a stop date beyond that window.
    - skip_past=True drops today's slots that are already behind `start_at`
      (which must then be the patient's LOCAL now). Without it, confirming a
      prescription at 3pm created a pending 8am dose that the sweeper would
      immediately count as missed. The course still ends on the same
      calendar day; no replacement dose is invented.
    """
    if medicine.status == "needs_confirmation":
        raise SchedulingBlocked(
            f"Medicine {medicine.id} ({medicine.raw_text!r}) is needs_confirmation "
            "and cannot be scheduled until a human confirms it."
        )

    if medicine.is_prn:
        return []

    start_at = start_at or datetime.utcnow()
    start_date = start_at.date()

    if medicine.schedule_code == "STAT":
        return [Dose(medicine_id=medicine.id, scheduled_at=start_at, state="pending")]

    times = json.loads(medicine.times) if medicine.times else []
    if not times:
        return []

    span = medicine.duration_days if medicine.duration_days is not None else 30

    doses = []
    for day in range(span):
        for t in times:
            if not t:
                continue
            hh, mm = (int(x) for x in t.split(":"))
            scheduled_at = datetime.combine(start_date + timedelta(days=day), datetime.min.time())
            scheduled_at = scheduled_at.replace(hour=hh, minute=mm)
            if skip_past and scheduled_at < start_at:
                continue
            doses.append(Dose(medicine_id=medicine.id, scheduled_at=scheduled_at, state="pending"))

    return doses


# ---------------------------------------------------------------- state machine

def mark_taken(dose: Dose, acted_at: Optional[datetime] = None) -> Dose:
    dose.state = "taken"
    dose.acted_at = acted_at or datetime.utcnow()
    return dose


UNDO_WINDOW = timedelta(minutes=10)


def undo_taken(dose: Dose, now: Optional[datetime] = None) -> Dose:
    """Voice matching can pick the wrong dose, so a just-marked 'taken' can
    be put back — only within UNDO_WINDOW, so history can't be rewritten."""
    now = now or datetime.utcnow()
    if dose.state != "taken" or not dose.acted_at or now - dose.acted_at > UNDO_WINDOW:
        raise ValueError("Only a dose marked taken in the last 10 minutes can be undone.")
    dose.state = "pending"
    dose.acted_at = None
    return dose


def mark_missed(dose: Dose, acted_at: Optional[datetime] = None) -> Dose:
    dose.state = "missed"
    dose.acted_at = acted_at or datetime.utcnow()
    return dose


def mark_skipped(dose: Dose, reason: str, acted_at: Optional[datetime] = None) -> Dose:
    if not reason or not reason.strip():
        raise ValueError("Skipping a dose requires a reason.")
    dose.state = "skipped"
    dose.reason = reason
    dose.acted_at = acted_at or datetime.utcnow()
    return dose


def snooze(dose: Dose) -> Dose:
    """Push +15 min, up to MAX_SNOOZES times, then force back to pending."""
    if dose.snooze_count >= MAX_SNOOZES:
        dose.state = "pending"
        return dose
    dose.scheduled_at = dose.scheduled_at + timedelta(minutes=SNOOZE_MINUTES)
    dose.snooze_count += 1
    dose.state = "snoozed"
    return dose


def sweep_missed_doses(db: Session, now: Optional[datetime] = None,
                       utc_now: Optional[datetime] = None) -> list[Dose]:
    """
    Auto-mark still-pending (or snoozed) doses as missed once >2h past their
    scheduled time, and return the doses that were just marked.

    'Past' is judged on each PATIENT's clock (Dose.scheduled_at is local
    wall-clock time), not the server's UTC clock. Passing `now` forces one
    shared clock for everyone - used by tests and callers that already
    hold a local time. Idempotent: a dose already missed is not touched.

    Runs both reactively (dashboard/report calls) and proactively
    (main.py's background reminder job, CLAUDE.md section 9) — every
    auto-miss is logged to AuditLog, same as a human-initiated one.
    """
    from db import AuditLog  # local import: avoids a circular import with db.py at module load time
    from clock import local_now, patient_timezone, MAX_UTC_OFFSET

    utc_now = utc_now or datetime.utcnow()
    # Coarse SQL filter: nobody's local clock is more than 14h ahead of UTC.
    coarse = (now if now is not None else utc_now + MAX_UTC_OFFSET) - MISSED_AFTER
    candidates = db.query(Dose).filter(Dose.state.in_(("pending", "snoozed")), Dose.scheduled_at < coarse).all()

    clocks: dict[int, datetime] = {}
    missed: list[Dose] = []
    for dose in candidates:
        patient_id = dose.medicine.prescription.patient_id
        if now is not None:
            local = now
        else:
            if patient_id not in clocks:
                clocks[patient_id] = local_now(patient_timezone(db, patient_id), utc_now)
            local = clocks[patient_id]
        if dose.scheduled_at >= local - MISSED_AFTER:
            continue
        mark_missed(dose, acted_at=local)
        db.add(AuditLog(
            patient_id=patient_id, actor="system", action="dose_auto_missed",
            detail=f"dose {dose.id} ({dose.medicine.name or dose.medicine.raw_text}) "
                   f"scheduled {dose.scheduled_at.isoformat()}, auto-marked missed after 2h",
        ))
        missed.append(dose)
    db.commit()
    return missed


def sweep_missed(db: Session, now: Optional[datetime] = None) -> int:
    return len(sweep_missed_doses(db, now))


# ---------------------------------------------------------------- adherence

def compute_adherence(doses: list[Dose]) -> dict:
    """
    Pending future doses are EXCLUDED from the denominator — otherwise day 1
    of a 5-day course reads 20% and looks broken (CLAUDE.md section 9).
    """
    taken = sum(1 for d in doses if d.state == "taken")
    missed = sum(1 for d in doses if d.state == "missed")
    skipped = sum(1 for d in doses if d.state == "skipped")
    acted = taken + missed + skipped
    pending = sum(1 for d in doses if d.state in ("pending", "snoozed"))

    adherence_pct = round((taken / acted) * 100, 1) if acted > 0 else None

    return {
        "total_doses": len(doses),
        "taken": taken,
        "missed": missed,
        "skipped": skipped,
        "pending": pending,
        "adherence_percent": adherence_pct,
    }


def compute_treatment_progress(medicine: Medicine, doses: list[Dose], now: Optional[datetime] = None) -> dict:
    """
    'Day 3 / 7' — the PS4 brief's own example format. Day 1 is the date of
    the earliest scheduled dose (set once at confirmation, never
    recomputed, same invariant as the doses themselves). PRN medicines and
    ones with no doses yet have no day count — there's nothing to count.
    """
    now = now or datetime.utcnow()
    if not doses or medicine.is_prn:
        return {"current_day": None, "total_days": medicine.duration_days, "ongoing": medicine.duration_days is None}

    start_date = min(d.scheduled_at for d in doses).date()
    current_day = (now.date() - start_date).days + 1
    total_days = medicine.duration_days
    if total_days is not None:
        current_day = max(1, min(current_day, total_days))
    else:
        current_day = max(1, current_day)
    return {"current_day": current_day, "total_days": total_days, "ongoing": total_days is None}


def compute_medicine_breakdown(medicines_with_doses: list[tuple[Medicine, list[Dose]]]) -> list[dict]:
    breakdown = []
    for medicine, doses in medicines_with_doses:
        entry = compute_adherence(doses)
        entry["medicine_id"] = medicine.id
        entry["name"] = medicine.name or medicine.raw_text
        entry["progress"] = compute_treatment_progress(medicine, doses)
        breakdown.append(entry)
    return breakdown
