"""
SmartPoli - per-medicine verification status.

Four SEPARATE checks. Passing one says nothing about the others, and none of
them is a clinical judgement:

  transcription      Does the entry match what is written on the prescription?
                     'confirmed_transcription' only once the patient has confirmed
                     the prescription (or corrected the line themselves).
  catalogue_match    How well does the name match a known medicine name?
                     exact | approximate | unresolved. A catalogue match is NOT
                     proof the line was read correctly.
  schedule           Is there a readable schedule to remind from?
                     ready | requires_clarification
  regulatory         Always 'verification_pending' here: official-source status is a
                     separate lookup (regulatory.py, Safety center) and never
                     blocks reminders on its own.

`overall` is the first thing in the way of activating reminders, or
'ready_for_reminder_activation'. Patient confirmation verifies the
transcription to the best of the patient's ability; it does not establish that
the prescription is clinically appropriate.
"""

import json

CATALOGUE_EXACT = 0.95
CATALOGUE_APPROX = 0.85

NOTE = ("Confirming checks that this entry matches what is written on the prescription. It does not mean the "
        "prescription is clinically appropriate for you - ask your doctor or pharmacist about that.")


def _field_confidence(m) -> dict:
    try:
        return json.loads(m.field_confidence) if m.field_confidence else {}
    except ValueError:
        return {}


def verification_status(m) -> dict:
    fc = _field_confidence(m)
    human = fc.get("source") == "human_confirmed"
    prescription_confirmed = bool(getattr(getattr(m, "prescription", None), "status", None) == "confirmed")

    if m.status == "needs_confirmation":
        transcription = "unreadable_needs_patient_input"
    elif human or prescription_confirmed:
        transcription = "confirmed_transcription"
    else:
        transcription = "needs_patient_review"

    name_conf = fc.get("name")
    if not m.name or name_conf is None:
        catalogue = "unresolved"
    elif name_conf >= CATALOGUE_EXACT:
        catalogue = "exact"
    elif name_conf >= CATALOGUE_APPROX:
        catalogue = "approximate"
    else:
        catalogue = "unresolved"

    has_schedule = bool(m.is_prn or (m.schedule_code and m.times and json.loads(m.times)))
    schedule = "ready" if has_schedule and m.status != "needs_confirmation" else "requires_clarification"

    if transcription == "unreadable_needs_patient_input" or schedule == "requires_clarification":
        overall = "schedule_requires_clarification" if schedule == "requires_clarification" else "needs_patient_review"
    elif catalogue == "unresolved":
        overall = "medicine_match_unresolved"
    elif transcription == "needs_patient_review":
        overall = "needs_patient_review"
    else:
        overall = "ready_for_reminder_activation"

    return {
        "transcription": transcription,
        "catalogue_match": catalogue,
        "schedule": schedule,
        "regulatory": "verification_pending",
        "overall": overall,
        "note": NOTE,
    }
