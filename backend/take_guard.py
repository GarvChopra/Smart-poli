"""
SmartPoli - what happens when the patient taps "Mark taken".

Everything the app knows is checked against what the patient has ACTUALLY taken, not just what was scheduled:

  1. WINDOW       a dose can be marked taken from 2 h before to 12 h after its time (scheduler.take_window_error).
  2. SAME MEDICINE  two doses of one medicine need a minimum gap. The floor comes from the prescription itself
                  (half the shortest interval its schedule implies); an official label or an AI estimate
                  (gap_ai.py) can raise it, but never above 3/4 of the interval, so a dose that is properly
                  due is never blocked.
  3. OTHER MEDICINES  curated FDA-label spacing rules (safety_rules.json) first - they always win; where the label
                  says "separate" without a time, or there is no curated rule, an AI answer grounded in the
                  label text fills in. AI can only make the app ask the patient to wait longer.

Each problem is an "issue" the API turns into a popup:
  hard    -> the dose is not recorded (too early / too old / a label- or schedule-based same-medicine gap)
  soft    -> the patient is told to wait and can say "I already took it" (cross-medicine spacing, AI-estimated gaps)

Nothing here changes a dose or a schedule. It only decides whether a tap is recorded now.
"""

import json
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

import gap_ai
import safety_engine
from db import Dose, Medicine, Prescription
from scheduler import take_window_error

FLOOR_FRACTION = 0.5       # default minimum gap = half the shortest interval the prescription implies
CAP_FRACTION = 0.75        # ...and no estimate may push it beyond three quarters of that interval

SOURCE_NOTE = {
    "schedule": "This follows the schedule on your prescription.",
    "label": "From the official drug label.",
    "curated": "From the FDA drug label.",
    "ai_estimate": "This is an AI estimate, not taken from a label. Ask your pharmacist if you are unsure.",
}


def _when(dt: datetime, now: datetime) -> str:
    gap = (dt.date() - now.date()).days
    t = dt.strftime("%H:%M")
    return t if gap == 0 else (f"tomorrow {t}" if gap == 1 else (f"yesterday {t}" if gap == -1 else f"{dt.strftime('%d %b')} {t}"))


def _hours_text(h: float) -> str:
    if h < 1:
        return f"{int(round(h * 60))} minutes"
    return f"{int(h)} hours" if abs(h - round(h)) < 0.05 else f"{h:.1f} hours"


def prescribed_interval_hours(medicine) -> Optional[float]:
    """Shortest gap (hours) between consecutive daily doses implied by the medicine's times; 24 for once a day."""
    try:
        times = json.loads(medicine.times) if medicine.times else []
    except ValueError:
        return None
    mins = sorted({int(t[:2]) * 60 + int(t[3:5]) for t in times if isinstance(t, str) and len(t) >= 5 and t[2] == ":"})
    if not mins:
        return None
    if len(mins) == 1:
        return 24.0
    gaps = [(b - a) / 60 for a, b in zip(mins, mins[1:])] + [(24 * 60 - (mins[-1] - mins[0])) / 60]
    return min(gaps)


def min_gap_info(db: Session, medicine, allow_network: bool = False) -> dict:
    """{'hours', 'source', 'quote', 'max_per_24h'} - the minimum hours between two doses of this medicine."""
    interval = prescribed_interval_hours(medicine)
    floor = FLOOR_FRACTION * interval if interval else None
    cap = CAP_FRACTION * interval if interval else None
    info = {"hours": floor, "source": "schedule" if floor else None, "quote": None, "max_per_24h": None}
    try:
        ai = gap_ai.single_gap(db, medicine.name or medicine.raw_text, allow_network=allow_network)
    except Exception:  # noqa: BLE001 - an AI/cache problem must never stop a tap; the schedule floor still applies
        ai = None
    if ai:
        info["max_per_24h"] = ai.get("max_per_24h")
        est = ai.get("min_hours")
        if est:
            candidate = max(floor, est) if floor else est
            if cap:
                candidate = min(candidate, cap)
            if floor is None or candidate > floor:
                info["hours"] = candidate
                if abs(candidate - est) < 1e-6:                      # not clamped: the answer is the AI/label's own
                    info["source"], info["quote"] = ai["basis"], ai.get("quote")
                else:
                    info["source"] = "schedule"
    return info


def _issue(code, hard, message, **extra):
    return {"code": code, "hard": hard, "message": message, **extra}


def evaluate(db: Session, dose: Dose, now: datetime, *, allow_network: bool = False, rules: Optional[dict] = None) -> list[dict]:
    """All reasons this dose should not be recorded as taken right now (empty list = go ahead)."""
    window = take_window_error(dose, now)
    if window:
        return [_issue("too_early" if dose.scheduled_at > now else "too_old", True, window,
                       earliest=(dose.scheduled_at - timedelta(hours=2)).isoformat() if dose.scheduled_at > now else None)]
    rules = rules or safety_engine.load_rules()
    medicine = dose.medicine
    name = medicine.name or medicine.raw_text
    issues: list[dict] = []

    # ---- same medicine
    last = (db.query(Dose).filter(Dose.medicine_id == medicine.id, Dose.id != dose.id, Dose.state == "taken",
                                  Dose.acted_at.isnot(None), Dose.acted_at <= now)
            .order_by(Dose.acted_at.desc()).first())
    if last:
        gap = min_gap_info(db, medicine, allow_network)
        if gap["hours"]:
            earliest = last.acted_at + timedelta(hours=gap["hours"])
            if earliest > now:
                source = gap["source"] or "schedule"
                issues.append(_issue(
                    "too_soon", source != "ai_estimate",
                    f"You took {name} at {_when(last.acted_at, now)}. Wait until {_when(earliest, now)} before the next dose "
                    f"(about {_hours_text(gap['hours'])} between doses). {SOURCE_NOTE[source]}",
                    earliest=earliest.isoformat(), source=source, source_label=SOURCE_NOTE[source], quote=gap["quote"],
                    medicine=name, hours=gap["hours"]))

    # ---- other medicines the patient has actually taken recently
    patient_id = medicine.prescription.patient_id
    recent = (db.query(Dose).join(Medicine, Dose.medicine_id == Medicine.id).join(Prescription)
              .filter(Prescription.patient_id == patient_id, Medicine.id != medicine.id, Dose.state == "taken",
                      Dose.acted_at.isnot(None), Dose.acted_at <= now, Dose.acted_at >= now - timedelta(hours=48))
              .order_by(Dose.acted_at.desc()).all())
    latest_by_med: dict[int, Dose] = {}
    for d in recent:
        latest_by_med.setdefault(d.medicine_id, d)
    for other_dose in latest_by_med.values():
        other = other_dose.medicine
        other_name = other.name or other.raw_text
        t_o = other_dose.acted_at
        curated = safety_engine.take_time_spacing(name, other_name, t_o, now, rules)
        if curated:
            issues.append(_issue(
                "spacing", False,
                f"{name} and {other_name} should be kept apart. You took {other_name} at {_when(t_o, now)}. "
                f"Wait until {_when(curated['earliest'], now)} to take {name}. {SOURCE_NOTE['curated']}",
                earliest=curated["earliest"].isoformat(), source="curated", source_label=SOURCE_NOTE["curated"],
                quote=curated["quote"], medicine=name, other_medicine=other_name, hours=curated["required_hours"]))
            continue
        if safety_engine.curated_rule_exists(name, other_name, rules):
            continue                       # a curated rule covers this pair in the other direction: nothing is due now
        try:
            ai = gap_ai.pair_gap(db, name, other_name, allow_network=allow_network)
        except Exception:  # noqa: BLE001
            ai = None
        if ai and ai.get("hours") and (now - t_o) < timedelta(hours=ai["hours"]):
            earliest = t_o + timedelta(hours=ai["hours"])
            source = "label" if ai["basis"] == "label" else "ai_estimate"
            issues.append(_issue(
                "spacing", False,
                f"{name} and {other_name} should be kept apart. You took {other_name} at {_when(t_o, now)}. "
                f"Wait until {_when(earliest, now)} to take {name} (about {_hours_text(ai['hours'])} apart). {SOURCE_NOTE[source]}",
                earliest=earliest.isoformat(), source=source, source_label=SOURCE_NOTE[source], quote=ai.get("quote"),
                medicine=name, other_medicine=other_name, hours=ai["hours"]))
    return issues
