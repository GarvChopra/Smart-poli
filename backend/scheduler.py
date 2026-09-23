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


def generate_doses(medicine: Medicine, start_at: Optional[datetime] = None) -> list[Dose]:
    """
    Build the Dose rows for one confirmed medicine.

    - needs_confirmation medicines are refused outright (the gate).
    - PRN/SOS medicines generate ZERO dose rows — they are logged manually.
    - STAT medicines generate exactly one dose, right now, not repeated daily.
    - Ongoing (duration_days is None) medicines are scheduled 30 days forward,
      per CLAUDE.md 7.4 — never invent a stop date beyond that window.
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
            doses.append(Dose(medicine_id=medicine.id, scheduled_at=scheduled_at, state="pending"))

    return doses


# ---------------------------------------------------------------- state machine

def mark_taken(dose: Dose, acted_at: Optional[datetime] = None) -> Dose:
    dose.state = "taken"
    dose.acted_at = acted_at or datetime.utcnow()
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


def sweep_missed(db: Session, now: Optional[datetime] = None) -> int:
    """
    Auto-mark still-pending doses as missed once >2h past their scheduled
    time. Runs both reactively (dashboard/report calls) and proactively
    (main.py's background reminder job, CLAUDE.md section 9) — either way,
    every auto-miss is logged to AuditLog, same as a human-initiated one.
    """
    from db import AuditLog  # local import: avoids a circular import with db.py at module load time

    now = now or datetime.utcnow()
    cutoff = now - MISSED_AFTER
    pending = db.query(Dose).filter(Dose.state == "pending", Dose.scheduled_at < cutoff).all()
    for dose in pending:
        mark_missed(dose, acted_at=now)
        patient_id = dose.medicine.prescription.patient_id
        db.add(AuditLog(
            patient_id=patient_id, actor="system", action="dose_auto_missed",
            detail=f"dose {dose.id} ({dose.medicine.name or dose.medicine.raw_text}) "
                   f"scheduled {dose.scheduled_at.isoformat()}, auto-marked missed after 2h",
        ))
    db.commit()
    return len(pending)


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
