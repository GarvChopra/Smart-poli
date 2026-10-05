"""
Turns raw audit-log rows into the plain sentences the Timeline screen shows.

The audit log stores machine text ("dose 412 was pending; scheduled 2026-10-04T20:30:00 ...", actor "patient:5").
A person reading their own treatment journey should see "Took Metformin" and "You", never ids, JSON or codes.
Behind-the-scenes rows (seeding, repairs, technical bookkeeping) are left out of the screen; they stay in the log.
"""

import re

from db import Dose, Medicine

HIDDEN = {"demo_seeded", "routine_applied", "dose_taken_override", "dose_taken_reverted", "voice_history_deleted",
          "medicine_corrected"}

_DOSE_ID = re.compile(r"\bdose (\d+)\b")
_MED_ID = re.compile(r"\bmedicine (\d+)\b")


def _hhmm(dt) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def actor_label(actor: str) -> str:
    kind, _, rest = (actor or "").partition(":")
    if kind == "patient":
        return "You"
    if kind == "doctor":
        name = rest.partition(":")[2]
        return f"Dr. {name}" if name else "Your doctor"
    if kind == "caregiver":
        return "Caregiver"
    if kind == "whatsapp":
        return "WhatsApp"
    if kind in ("voice", "assistant"):
        return "Voice assistant"
    return "SmartPoli"


def _lookup(db, entries) -> dict:
    """dose id -> (medicine name, scheduled time), medicine id -> name, for every row that mentions one."""
    dose_ids, med_ids = set(), set()
    for e in entries:
        d = e.detail or ""
        m = _DOSE_ID.search(d)
        if m:
            dose_ids.add(int(m.group(1)))
        m = _MED_ID.search(d)
        if m:
            med_ids.add(int(m.group(1)))
    doses = {}
    if dose_ids:
        for dose, med in (db.query(Dose, Medicine).join(Medicine, Dose.medicine_id == Medicine.id)
                          .filter(Dose.id.in_(dose_ids)).all()):
            doses[dose.id] = (med.name or med.raw_text, dose.scheduled_at)
    meds = {}
    if med_ids:
        for med in db.query(Medicine).filter(Medicine.id.in_(med_ids)).all():
            meds[med.id] = med.name or med.raw_text
    return {"doses": doses, "meds": meds}


_DOSE_VERBS = {
    "dose_taken": "Took {m}", "dose_taken_late": "Took {m} (late)", "dose_taken_via_voice": "Took {m}",
    "dose_taken_undone": "Undid the “taken” mark on {m}", "dose_missed": "Missed {m}",
    "dose_auto_missed": "Missed {m}", "dose_skipped": "Skipped {m}",
}

_PLAIN = {
    "patient_created": "Profile created", "patient_updated": "Profile updated",
    "prescription_created": "Prescription added", "prescription_confirmed": "Prescription confirmed",
    "medicine_confirmed": "Medicine confirmed", "medicine_scanned": "Medicine scanned",
    "triage_check": "Symptom check done", "settings_updated": "Reminder settings changed",
    "routine_updated": "Daily routine updated", "emergency_card_revoked": "Emergency QR code reset",
    "emergency_profile_updated": "Emergency card updated",
    "caregiver_link_created": "Caregiver invite created", "caregiver_link_accepted": "Caregiver joined",
    "caregiver_link_revoked": "Caregiver access removed", "doctor_link_created": "Doctor invite created",
    "doctor_link_accepted": "Doctor joined", "doctor_link_revoked": "Doctor access removed",
}


def friendly_entries(db, entries) -> list:
    lookup = _lookup(db, entries)
    out = []
    for e in entries:
        if e.action in HIDDEN:
            continue
        detail = e.detail or ""
        subtitle = ""
        if e.action in _DOSE_VERBS:
            m = _DOSE_ID.search(detail)
            name, when = lookup["doses"].get(int(m.group(1)), (None, None)) if m else (None, None)
            title = _DOSE_VERBS[e.action].format(m=name or "a medicine")
            if when:
                subtitle = f"{_hhmm(when)} dose"
            if e.action == "dose_skipped" and ":" in detail and detail.split(":", 1)[1].strip() not in ("", "None"):
                subtitle = (subtitle + " · " if subtitle else "") + detail.split(":", 1)[1].strip()
        elif e.action == "prn_taken":
            m = _MED_ID.search(detail)
            name = lookup["meds"].get(int(m.group(1))) if m else None
            title = f"Took {name} (when needed)" if name else "Took an as-needed medicine"
        elif e.action == "clinical_note":
            title, subtitle = "Note added", detail
        elif e.action in _PLAIN:
            title = _PLAIN[e.action]
        else:
            title = e.action.replace("_", " ").capitalize()
        out.append({"id": e.id, "at": e.at.isoformat(), "actor": actor_label(e.actor), "action": e.action,
                    "summary": title, "detail": subtitle})
    return out
