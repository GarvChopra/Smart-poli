"""
SmartPoli - "should this new medicine be kept apart from the ones I already take?"

Run right after a medicine is added. For every other medicine the patient takes, the same sourced lookup the
"Mark taken" guard uses (gap_ai.pair_gap: official FDA label interaction text + an AI reading of it, graded "label"
or "ai_estimate", cached) says how many hours the two should be kept apart. If the doses are scheduled closer than
that, a warning is returned together with the smallest later time for the NEW medicine that fixes it.

Nothing here changes a schedule by itself: the app shows the warning and the person taps to accept the suggestion.
Silence means "no known problem found", never "verified safe".
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

import gap_ai

logger = logging.getLogger(__name__)

MAX_PAIRS = 5
MIN_REQUIRED_HOURS = 1.0     # a shorter gap than this is noise, not a reason to interrupt someone

HORIZON = timedelta(days=3)
STEP_MIN = 15
MAX_SHIFT_MIN = 12 * 60
SOURCE_LABEL = {"label": "From the official FDA drug label.", "ai_estimate": "AI estimate - ask your pharmacist."}


def _hours_apart(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds()) / 3600


def closest_hours(new_doses: list, other_doses: list) -> Optional[float]:
    pairs = [_hours_apart(n, o) for n in new_doses for o in other_doses]
    return min(pairs) if pairs else None


def suggest_shift(new_doses: list, other_doses: list, required_hours: float) -> Optional[int]:
    """Smallest LATER shift (minutes, multiple of 15) of every new dose so each is >= required_hours from every other
    dose, and no dose moves onto another calendar day. None when no such shift exists."""
    for minutes in range(STEP_MIN, MAX_SHIFT_MIN + 1, STEP_MIN):
        moved = [d + timedelta(minutes=minutes) for d in new_doses]
        if any(m.date() != d.date() for m, d in zip(moved, new_doses)):
            return None                                   # it would cross midnight: do not invent a time
        if all(_hours_apart(m, o) >= required_hours for m in moved for o in other_doses):
            return minutes
    return None


def _pending_doses(db, medicine_id: int, now: datetime) -> list:
    from db import Dose
    return sorted(d.scheduled_at for d in db.query(Dose).filter(
        Dose.medicine_id == medicine_id, Dose.state.in_(("pending", "snoozed")),
        Dose.scheduled_at >= now - timedelta(hours=12), Dose.scheduled_at <= now + HORIZON).all())


def _lookup(name_a: str, name_b: str, pair_fn) -> Optional[dict]:
    """One pair lookup in its own DB session (so lookups can run side by side)."""
    from db import SessionLocal
    s = SessionLocal()
    try:
        return pair_fn(s, name_a, name_b)
    except Exception:  # noqa: BLE001 - a failed lookup is "no answer", never an error for the patient
        logger.warning("pair lookup failed")
        return None
    finally:
        s.close()


def warnings_for_new_medicine(db, medicine, now_local: datetime, pair_fn=None) -> list:
    from db import Medicine, Prescription
    pair_fn = pair_fn or gap_ai.pair_gap
    patient_id = medicine.prescription.patient_id
    new_name = medicine.name or medicine.raw_text
    new_doses = _pending_doses(db, medicine.id, now_local)
    if not new_doses:
        return []
    mine = (gap_ai.canonical_name(new_name) or "").lower()
    others, seen = [], {mine}
    for m in (db.query(Medicine).join(Prescription, Medicine.prescription_id == Prescription.id)
              .filter(Prescription.patient_id == patient_id, Medicine.status != "needs_confirmation", Medicine.id != medicine.id).all()):
        g = (gap_ai.canonical_name(m.name or m.raw_text) or "").lower()
        if g and g not in seen:
            seen.add(g)
            others.append((m.id, m.name or m.raw_text))
    others = others[:MAX_PAIRS]
    if not others:
        return []
    with ThreadPoolExecutor(max_workers=3) as pool:
        answers = list(pool.map(lambda o: _lookup(new_name, o[1], pair_fn), others))
    out = []
    for (other_id, other_name), ans in zip(others, answers):
        if not ans or not ans.get("hours"):
            continue
        required = float(ans["hours"])
        # Only warn about real problems: ignore tiny gaps and answers the AI itself is not confident about.
        if required < MIN_REQUIRED_HOURS or (ans.get("basis") == "ai_estimate" and ans.get("confidence") == "low"):
            continue
        other_doses = _pending_doses(db, other_id, now_local)
        close = closest_hours(new_doses, other_doses)
        if close is None or close >= required:
            continue
        shift = suggest_shift(new_doses, other_doses, required)
        basis = ans.get("basis") or "ai_estimate"
        out.append({
            "kind": "pair_gap", "medicines": [new_name, other_name], "required_hours": required, "closest_hours": round(close, 2),
            "message": (f"{new_name} and {other_name} work less well, or can cause problems, when taken close together. "
                        f"Keep them about {required:g} hour{'s' if required != 1 else ''} apart."),
            "applies_when": ans.get("applies_when"), "source": basis, "source_label": SOURCE_LABEL.get(basis, SOURCE_LABEL["ai_estimate"]),
            "quote": ans.get("quote"), "confidence": ans.get("confidence"),
            "suggestion": ({"medicine_id": medicine.id, "medicine": new_name, "shift_minutes": shift,
                            "new_times": sorted({(d + timedelta(minutes=shift)).strftime("%H:%M") for d in new_doses})}
                           if shift else None),
        })
    return out


def _warning(new_name, other_name, required, close, ans, medicine_id, new_doses, shift):
    basis = ans.get("basis") or "ai_estimate"
    return {
        "kind": "pair_gap", "medicines": [new_name, other_name], "required_hours": required, "closest_hours": round(close, 2),
        "message": (f"{new_name} and {other_name} work less well, or can cause problems, when taken close together. "
                    f"Keep them about {required:g} hour{'s' if required != 1 else ''} apart."),
        "applies_when": ans.get("applies_when"), "source": basis, "source_label": SOURCE_LABEL.get(basis, SOURCE_LABEL["ai_estimate"]),
        "quote": ans.get("quote"), "confidence": ans.get("confidence"),
        "suggestion": ({"medicine_id": medicine_id, "medicine": new_name, "shift_minutes": shift,
                        "new_times": sorted({(d + timedelta(minutes=shift)).strftime("%H:%M") for d in new_doses})}
                       if shift else None),
    }


def _is_real_problem(ans) -> bool:
    if not ans or not ans.get("hours"):
        return False
    return not (float(ans["hours"]) < MIN_REQUIRED_HOURS or (ans.get("basis") == "ai_estimate" and ans.get("confidence") == "low"))


def warnings_for_prescription(db, prescription, now_local: datetime, pair_fn=None, max_medicines: int = 4) -> list:
    """Warnings for every medicine of one prescription against everything the patient takes (a typed or photographed
    prescription adds several at once). A pair that appears from both sides is reported once."""
    from db import Medicine
    out, seen = [], set()
    meds = [m for m in db.query(Medicine).filter(Medicine.prescription_id == prescription.id).all()
            if m.status != "needs_confirmation" and not m.is_prn][:max_medicines]
    for m in meds:
        for w in warnings_for_new_medicine(db, m, now_local, pair_fn):
            key = frozenset(x.lower() for x in w["medicines"])
            if key not in seen:
                seen.add(key)
                out.append(w)
    return out


def warnings_for_patient(db, patient_id: int, now_local: datetime, pair_fn=None, max_medicines: int = 8) -> dict:
    """Every pair among the patient's medicines, from the cache only (pair_fn default never calls the network):
    {'warnings': [...], 'pending': number of pairs not looked up yet}. The medicine added LATER is the one suggested to move."""
    from db import Medicine, Prescription
    pair_fn = pair_fn or (lambda s, a, b: gap_ai.pair_gap(s, a, b, allow_network=False))
    meds, seen = [], set()
    for m in (db.query(Medicine).join(Prescription, Medicine.prescription_id == Prescription.id)
              .filter(Prescription.patient_id == patient_id, Medicine.status != "needs_confirmation", Medicine.is_prn.is_(False))
              .order_by(Medicine.id).all()):
        g = (gap_ai.canonical_name(m.name or m.raw_text) or "").lower()
        if g and g not in seen:
            seen.add(g)
            meds.append(m)
    meds = meds[:max_medicines]
    doses = {m.id: _pending_doses(db, m.id, now_local) for m in meds}
    out, pending = [], 0
    for i, a in enumerate(meds):
        for b in meds[i + 1:]:
            if not doses[a.id] or not doses[b.id]:
                continue
            ans = pair_fn(db, a.name or a.raw_text, b.name or b.raw_text)
            if ans is None:
                pending += 1
                continue
            if not _is_real_problem(ans):
                continue
            required = float(ans["hours"])
            close = closest_hours(doses[a.id], doses[b.id])
            if close is None or close >= required:
                continue
            shift = suggest_shift(doses[b.id], doses[a.id], required)
            out.append(_warning(b.name or b.raw_text, a.name or a.raw_text, required, close, ans, b.id, doses[b.id], shift))
    return {"warnings": out, "pending": pending}
