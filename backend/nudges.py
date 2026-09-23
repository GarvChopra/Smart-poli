"""
SmartPoli — deterministic medication nudges (Part 4 of the brief).

Every nudge is generated from the same Dose/adherence data the dashboard
already renders — nothing here invents a dosage, calls an LLM, or
second-guesses the prescription. A nudge is one of:
  - an upcoming-dose reminder ("due in N minutes"),
  - a not-yet-logged reminder for a dose whose window has passed but isn't
    auto-missed yet (>2h — that's the scheduler's own threshold), or
  - a same-day adherence summary.

Reused, not duplicated: this reads db.Medicine/db.Dose rows the scheduler
already generated (CLAUDE.md invariant 1) — it does not compute or store
anything new.
"""

from datetime import datetime, timedelta
from typing import Optional

from db import Dose, Medicine

DUE_SOON_WINDOW = timedelta(minutes=30)


def _slot_label(scheduled_at: datetime) -> str:
    hour = scheduled_at.hour
    if hour < 11:
        return "morning"
    if hour < 16:
        return "afternoon"
    if hour < 19:
        return "evening"
    return "night"


def compute_nudges(
    medicines_with_doses: list[tuple[Medicine, list[Dose]]],
    now: Optional[datetime] = None,
) -> list[dict]:
    now = now or datetime.utcnow()
    today = now.date()

    todays = [
        (medicine, dose)
        for medicine, doses in medicines_with_doses
        for dose in doses
        if dose.scheduled_at.date() == today
    ]

    nudges = []

    for medicine, dose in todays:
        if dose.state in ("pending", "snoozed") and now <= dose.scheduled_at <= now + DUE_SOON_WINDOW:
            minutes = max(0, int((dose.scheduled_at - now).total_seconds() // 60))
            name = medicine.name or medicine.raw_text
            nudges.append({
                "level": "upcoming",
                "text": f"Your {_slot_label(dose.scheduled_at)} dose of {name} is due in "
                        f"{minutes} minute{'s' if minutes != 1 else ''}.",
            })

    for medicine, dose in todays:
        if dose.state == "pending" and dose.scheduled_at < now:
            name = medicine.name or medicine.raw_text
            nudges.append({
                "level": "overdue",
                "text": f"You have not recorded your {_slot_label(dose.scheduled_at)} dose of {name} yet. "
                        "Follow the prescribed instructions before taking any additional dose.",
            })

    acted_today = [d for _, d in todays if d.state in ("taken", "missed", "skipped")]
    if acted_today:
        taken = sum(1 for d in acted_today if d.state == "taken")
        pct = round((taken / len(acted_today)) * 100)
        if pct == 100:
            nudges.append({"level": "info", "text": "Today's medication adherence is 100%."})
        else:
            nudges.append({"level": "info", "text": f"Today's medication adherence is {pct}% so far."})

    return nudges
