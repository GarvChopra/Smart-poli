"""
SmartPoli — the voice assistant's action router.

Groq never touches the database. It can only ask for one of the tools
below by name; each tool validates its arguments, checks the dose /
medicine / prescription belongs to this patient, and then calls the SAME
functions the app's buttons use (scheduler.mark_taken,
prescription_service, triage_service, serializers). Tools return JSON for
the model and may queue UI actions (popups, navigation, emergency panel)
for the voice page.

Symptom checks: Groq holds the conversation, but severity only ever comes
from triage.evaluate_check over triage_rules.json (via triage_service).
Symptoms the rules don't cover come back NOT_ASSESSED — never a guess.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from db import AuditLog, Dose, Medicine, Prescription, SymptomCheck, User, VoiceMessage, log_audit
from interactions import load_ruleset as load_interaction_ruleset, check_interactions
from food_warnings import load_ruleset as load_food_ruleset, check_food_warnings
from prescription_service import create_prescription_from_lines, confirm_prescription_doses
from scheduler import mark_taken, compute_adherence
from serializers import emergency_card_data
from triage import load_ruleset, evaluate_check
from triage_service import record_symptom_check
from care_guidance import guidance_for
import clinical_knowledge

RULESET = load_ruleset()
INTERACTION_RULESET = load_interaction_ruleset()
FOOD_RULESET = load_food_ruleset()

SCREENS = ("dashboard", "prescriptions", "safety", "triage", "report", "timeline", "emergency", "settings")
TAKE_WINDOW_BEFORE = timedelta(hours=12)
TAKE_WINDOW_AFTER = timedelta(hours=2)


@dataclass
class ToolContext:
    db: Session
    patient_id: int
    user: User
    now: datetime            # patient's local wall-clock time (dose times are stored that way)
    lang: str
    state: dict              # carried between turns by the client; re-validated here
    actions: list = field(default_factory=list)
    decoded_this_turn: set = field(default_factory=set)
    # Rule questions already put in front of the patient BEFORE this turn —
    # only those may be answered "no" (see update_symptom_check).
    asked_before: set = field(init=False)

    def __post_init__(self):
        if not isinstance(self.state, dict):
            self.state = {}
        self.asked_before = set(clean_symptom_state(self.state.get("symptom"))["asked"])

    @property
    def actor(self) -> str:
        return f"patient:{self.user.id}"


def _err(message: str) -> dict:
    return {"ok": False, "error": message}


def _as_int(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if 0 < n < 2**31 else None


def _dose_text(m: Medicine) -> str:
    return f"{m.dose_amount} {m.dose_unit or ''}".strip() if m.dose_amount else ""


def _med_label(m: Medicine) -> str:
    return " ".join(x for x in (m.name or m.raw_text, _dose_text(m)) if x)


def _patient_doses(ctx: ToolContext):
    return (ctx.db.query(Dose).join(Medicine).join(Prescription)
            .filter(Prescription.patient_id == ctx.patient_id).order_by(Dose.scheduled_at).all())


def _can_mark_taken(ctx: ToolContext, dose: Dose) -> bool:
    in_window = ctx.now - TAKE_WINDOW_BEFORE <= dose.scheduled_at <= ctx.now + TAKE_WINDOW_AFTER
    # "missed" is allowed: the 2-hour sweep auto-marks late doses missed, and
    # the patient saying they took it corrects that guess.
    return in_window and dose.state in ("pending", "snoozed", "missed")


def _dose_row(ctx: ToolContext, dose: Dose) -> dict:
    return {"dose_id": dose.id, "medicine": _med_label(dose.medicine),
            "time": dose.scheduled_at.strftime("%H:%M"), "date": dose.scheduled_at.date().isoformat(),
            "state": dose.state, "food": dose.medicine.food, "can_mark_taken": _can_mark_taken(ctx, dose)}


def _next_dose(ctx: ToolContext):
    upcoming = [d for d in _patient_doses(ctx) if d.state in ("pending", "snoozed") and d.scheduled_at >= ctx.now]
    return _dose_row(ctx, upcoming[0]) if upcoming else None


# ---------------------------------------------------------------- medication tools

def get_today_schedule(ctx: ToolContext) -> dict:
    today = ctx.now.date()
    doses = [d for d in _patient_doses(ctx) if d.scheduled_at.date() == today]
    return {"ok": True, "date": today.isoformat(), "now": ctx.now.strftime("%H:%M"),
            "doses": [_dose_row(ctx, d) for d in doses]}


def get_next_dose(ctx: ToolContext) -> dict:
    return {"ok": True, "next_dose": _next_dose(ctx)}


def mark_dose_taken(ctx: ToolContext, dose_id=None) -> dict:
    dose_id = _as_int(dose_id)
    dose = ctx.db.query(Dose).get(dose_id) if dose_id is not None else None
    if not dose or dose.medicine.prescription.patient_id != ctx.patient_id:
        return _err("No such dose for this patient. Call get_today_schedule and use a dose_id from it.")
    if dose.state == "taken":
        return _err("That dose is already marked taken.")
    if not _can_mark_taken(ctx, dose):
        return _err("That dose isn't due around now, so it can't be marked taken. Check get_today_schedule.")
    mark_taken(dose)
    ctx.db.commit()
    log_audit(ctx.db, ctx.patient_id, ctx.actor, "dose_taken_via_voice", f"dose {dose.id}")
    ctx.actions.append({"type": "dose_taken", "dose_id": dose.id, "medicine": _med_label(dose.medicine),
                        "scheduled": dose.scheduled_at.strftime("%H:%M"),
                        "taken_at": dose.acted_at.isoformat()})
    return {"ok": True, "marked": _dose_row(ctx, dose), "next_dose": _next_dose(ctx)}


def log_prn_taken(ctx: ToolContext, medicine_id=None) -> dict:
    medicine = ctx.db.query(Medicine).get(_as_int(medicine_id)) if _as_int(medicine_id) is not None else None
    if not medicine or medicine.prescription.patient_id != ctx.patient_id:
        return _err("No such medicine for this patient.")
    if not medicine.is_prn:
        return _err("That medicine is scheduled, not as-needed. Use mark_dose_taken for it.")
    log_audit(ctx.db, ctx.patient_id, ctx.actor, "prn_taken",
              f"medicine {medicine.id} ({medicine.name or medicine.raw_text}) at {datetime.utcnow().isoformat()} via voice")
    ctx.actions.append({"type": "prn_logged", "medicine": _med_label(medicine)})
    return {"ok": True, "logged": _med_label(medicine)}


def get_medicines(ctx: ToolContext) -> dict:
    prescriptions = ctx.db.query(Prescription).filter(Prescription.patient_id == ctx.patient_id).all()
    meds = [
        {"medicine_id": m.id, "name": m.name or m.raw_text, "dose": _dose_text(m),
         "schedule": m.schedule_code, "times": json.loads(m.times) if m.times else [],
         "food": m.food, "as_needed": m.is_prn, "days": m.duration_days}
        for p in prescriptions for m in p.medicines if m.status != "needs_confirmation"
    ]
    return {"ok": True, "medicines": meds}


def get_adherence(ctx: ToolContext) -> dict:
    doses = _patient_doses(ctx)
    today = [d for d in doses if d.scheduled_at.date() == ctx.now.date()]
    return {"ok": True, "overall": compute_adherence(doses), "today": compute_adherence(today),
            "missed_today": [_med_label(d.medicine) for d in today if d.state == "missed"]}


# ---------------------------------------------------------------- information / navigation

def get_emergency_card(ctx: ToolContext) -> dict:
    data = emergency_card_data(ctx.db, ctx.patient_id)
    p = data["patient"]
    ctx.actions.append({"type": "open_card", "card_path": data["card_path"]})
    return {"ok": True, "name": p["name"], "blood_group": p["blood_group"], "allergies": p["allergies"],
            "emergency_contact": p["emergency_contact"], "conditions": data["profile"]["conditions"]}


def prescribed_as_needed(ctx: ToolContext) -> list[dict]:
    """As-needed (SOS) medicines the patient's own doctor prescribed, with the
    instruction exactly as recorded — the only medicines guidance may mention."""
    meds = (ctx.db.query(Medicine).join(Prescription)
            .filter(Prescription.patient_id == ctx.patient_id, Medicine.is_prn.is_(True),
                    Medicine.status != "needs_confirmation").all())
    return [{"name": _med_label(m), "instruction": m.raw_text,
             "prescribed_by": m.prescription.doctor_name} for m in meds]


def get_patient_context(ctx: ToolContext) -> dict:
    """The patient's existing record, so a symptom is never handled as if they
    were a new patient: conditions, allergies, current and as-needed
    medicines, doctor notes and recent symptom checks. Context only — never
    proof of what is causing the current symptom."""
    profile = emergency_card_data(ctx.db, ctx.patient_id)
    notes = (ctx.db.query(AuditLog).filter(AuditLog.patient_id == ctx.patient_id, AuditLog.action == "clinical_note")
             .order_by(AuditLog.at.desc()).limit(5).all())
    checks = (ctx.db.query(SymptomCheck).filter(SymptomCheck.patient_id == ctx.patient_id)
              .order_by(SymptomCheck.created_at.desc()).limit(5).all())
    return {
        "ok": True,
        "conditions": profile["profile"]["conditions"],
        "allergies": profile["patient"]["allergies"],
        "blood_group": profile["patient"]["blood_group"],
        "emergency_contact": profile["patient"]["emergency_contact"],
        "care_team": profile["care_team"],
        "current_medicines": [m["name"] for m in get_medicines(ctx)["medicines"] if not m["as_needed"]],
        "as_needed_medicines": prescribed_as_needed(ctx),
        "adherence": get_adherence(ctx)["overall"],
        "doctor_notes": [{"date": n.at.date().isoformat(), "note": n.detail} for n in notes],
        "recent_symptom_checks": [{"date": c.created_at.date().isoformat(), "symptoms": json.loads(c.symptoms),
                                   "severity": c.severity} for c in checks],
    }


def get_doctor_notes(ctx: ToolContext, limit=5) -> dict:
    n = max(1, min(_as_int(limit) or 5, 20))
    notes = (ctx.db.query(AuditLog).filter(AuditLog.patient_id == ctx.patient_id, AuditLog.action == "clinical_note")
             .order_by(AuditLog.at.desc()).limit(n).all())
    return {"ok": True, "notes": [{"date": x.at.date().isoformat(), "note": x.detail} for x in notes],
            "use": "Their doctor's own words — surface them as written; they take priority over general guidance."}


def get_prn_history(ctx: ToolContext, days=30) -> dict:
    since = datetime.utcnow() - timedelta(days=max(1, min(_as_int(days) or 30, 365)))
    uses = (ctx.db.query(AuditLog).filter(AuditLog.patient_id == ctx.patient_id, AuditLog.action == "prn_taken",
                                          AuditLog.at >= since).order_by(AuditLog.at.desc()).limit(20).all())
    return {"ok": True, "uses": [{"date": u.at.isoformat(timespec="minutes"), "detail": u.detail} for u in uses]}


def get_safety_warnings(ctx: ToolContext) -> dict:
    """Drug–drug interactions and food warnings for their current medicines —
    the same checks the Safety center screen runs."""
    names = [m["name"] for m in get_medicines(ctx)["medicines"] if m["name"]]
    return {"ok": True, "interactions": check_interactions(INTERACTION_RULESET, names),
            "food_warnings": check_food_warnings(FOOD_RULESET, names),
            "use": "Tell them what to avoid exactly as listed; never tell them to stop or change a medicine."}


# ---------------------------------------------------------------- memory (earlier days)

_MEMORY_STOP = {"hai", "hain", "mujhe", "mera", "meri", "mere", "ho", "raha", "rahi", "tha", "thi", "aaj", "kal",
                "phir", "wahi", "same", "problem", "the", "and", "is", "my", "i", "a", "to", "me", "se", "ki", "ka", "ke"}


def recall_previous(ctx: ToolContext, query=None, days=14) -> dict:
    """What this patient said on earlier days and their past symptom checks
    (with outcome), for "wahi problem jo kal thi". Only stored data, only this
    patient — and it is context, never a diagnosis of today's cause."""
    span = max(1, min(_as_int(days) or 14, 90))
    since = datetime.utcnow() - timedelta(days=span)
    today_start = datetime.utcnow() - timedelta(hours=6)
    words = {w for w in re.findall(r"\w+", str(query or "").lower()) if w not in _MEMORY_STOP and len(w) > 2}
    msgs = (ctx.db.query(VoiceMessage).filter(VoiceMessage.patient_id == ctx.patient_id, VoiceMessage.role == "user",
                                              VoiceMessage.created_at >= since, VoiceMessage.created_at < today_start)
            .order_by(VoiceMessage.created_at.desc()).limit(200).all())
    matched = [m for m in msgs if not words or words & set(re.findall(r"\w+", m.content.lower()))][:8]
    checks = (ctx.db.query(SymptomCheck).filter(SymptomCheck.patient_id == ctx.patient_id,
                                                SymptomCheck.created_at >= since)
              .order_by(SymptomCheck.created_at.desc()).limit(10).all())
    how_often = Counter(s for c in checks for s in json.loads(c.symptoms))
    return {
        "ok": True,
        "earlier_conversations": [{"date": m.created_at.date().isoformat(), "text": m.content} for m in matched],
        "earlier_symptom_checks": [{"date": c.created_at.date().isoformat(), "symptoms": json.loads(c.symptoms),
                                    "severity": c.severity, "outcome": json.loads(c.reasons)} for c in checks],
        "how_often": dict(how_often),
        "use": "This is context, not a diagnosis: say what happened before (\"kal bhi aapko … hua tha\") and ask "
               "whether today feels the same — do not assume it has the same cause. Run today's check as usual.",
    }


def get_treatment_history(ctx: ToolContext) -> dict:
    prescriptions = (ctx.db.query(Prescription).filter(Prescription.patient_id == ctx.patient_id)
                     .order_by(Prescription.created_at.desc()).limit(10).all())
    checks = (ctx.db.query(SymptomCheck).filter(SymptomCheck.patient_id == ctx.patient_id)
              .order_by(SymptomCheck.created_at.desc()).limit(5).all())
    return {"ok": True,
            "prescriptions": [{"date": p.issued_date or p.created_at.date().isoformat(), "doctor": p.doctor_name,
                               "status": p.status, "medicines": [_med_label(m) for m in p.medicines]}
                              for p in prescriptions],
            "recent_symptom_checks": [{"date": c.created_at.date().isoformat(), "symptoms": json.loads(c.symptoms),
                                       "severity": c.severity} for c in checks]}


def open_screen(ctx: ToolContext, screen=None) -> dict:
    if screen not in SCREENS:
        return _err(f"Unknown screen. Choose one of: {', '.join(SCREENS)}.")
    ctx.actions.append({"type": "navigate", "screen": screen})
    return {"ok": True, "opened": screen}


# ---------------------------------------------------------------- prescriptions

def decode_prescription(ctx: ToolContext, text=None) -> dict:
    lines = [ln.strip() for ln in re.split(r"[\n;]", text or "") if ln.strip()]
    if not lines:
        return _err("No prescription lines were given.")
    prescription, medicines = create_prescription_from_lines(
        ctx.db, ctx.patient_id, None, None, [(ln, None) for ln in lines[:20]], source="manual", actor=ctx.actor)
    ctx.state["pending_prescription_id"] = prescription.id
    ctx.decoded_this_turn.add(prescription.id)
    meds = [{"line": m.raw_text, "name": m.name, "dose": _dose_text(m), "schedule": m.schedule_code,
             "food": m.food, "days": m.duration_days, "status": m.status} for m, _ in medicines]
    ctx.actions.append({"type": "prescription_draft", "prescription_id": prescription.id, "medicines": meds})
    return {"ok": True, "prescription_id": prescription.id, "medicines": meds,
            "next_step": "Read the medicines back and ask the patient whether to schedule them. "
                         "Only call confirm_prescription after they say yes in their next message."}


def confirm_prescription(ctx: ToolContext, prescription_id=None) -> dict:
    pres_id = _as_int(prescription_id)
    if pres_id is None or pres_id != ctx.state.get("pending_prescription_id"):
        return _err("There is no decoded prescription waiting for confirmation with that id.")
    if pres_id in ctx.decoded_this_turn:
        return _err("Ask the patient to confirm first; schedule it only after they say yes.")
    prescription = ctx.db.query(Prescription).get(pres_id)
    if not prescription or prescription.patient_id != ctx.patient_id or prescription.status != "draft":
        return _err("That prescription can't be scheduled.")
    result = confirm_prescription_doses(ctx.db, prescription, ctx.actor)
    ctx.state.pop("pending_prescription_id", None)
    ctx.actions.append({"type": "prescription_confirmed", "prescription_id": pres_id,
                        "scheduled": len(result["scheduled"]),
                        "needs_review": len(result["blocked_needs_confirmation"])})
    return {"ok": True, **result}


# ---------------------------------------------------------------- symptom checks

def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    return [value] if isinstance(value, str) else []


def clean_symptom_state(raw: Any) -> dict:
    """The client carries this between turns — never trust it: keep only
    real rule symptom ids, boolean answers to their real question ids, and
    real question ids for `asked`. Never raises, whatever shape arrives."""
    raw = raw if isinstance(raw, dict) else {}
    ids = [s for s in _as_list(raw.get("ids")) if isinstance(s, str) and s in RULESET["symptoms"]]
    ids = list(dict.fromkeys(ids))
    valid_q = {q["id"] for sid in ids for q in RULESET["symptoms"][sid]["questions"]}
    raw_answers = raw.get("answers") if isinstance(raw.get("answers"), dict) else {}
    answers = {k: v for k, v in raw_answers.items() if k in valid_q and isinstance(v, bool)}
    labels = list(dict.fromkeys(s.strip()[:60] for s in _as_list(raw.get("labels")) if isinstance(s, str) and s.strip()))[:5]
    asked = [q for q in dict.fromkeys(_as_list(raw.get("asked"))) if q in valid_q]
    out = {"ids": ids, "labels": labels, "answers": answers, "asked": asked}
    # floor: "MODERATE" when re-assessing a symptom that got worse on recheck
    if raw.get("floor") == "MODERATE":
        out["floor"] = "MODERATE"
    # verify: the calm first question asked from voice_safety, awaiting an answer
    if isinstance(raw.get("verify"), str) and raw["verify"] in valid_q and raw["verify"] not in answers:
        out["verify"] = raw["verify"]
    return out


def mark_asked(sym: dict, questions: list[dict]) -> None:
    """Record that these rule questions have been put to the patient (via a
    tool result or the turn prompt), so a later "no" to them is accepted."""
    sym["asked"] = list(dict.fromkeys(sym.get("asked", []) + [q["id"] for q in questions]))


def open_questions(sym: dict) -> list[dict]:
    # Same rule as triage.next_question: once EMERGENCY, stop asking.
    if evaluate_check(RULESET, sym["ids"], sym["answers"])["severity"] == "EMERGENCY":
        return []
    return [{"id": q["id"], "symptom": sid, "text": q["text"], "text_hi": q.get("text_hi", "")}
            for sid in sym["ids"] for q in RULESET["symptoms"][sid]["questions"]
            if q["id"] not in sym["answers"]]


def _schedule_recheck(ctx: ToolContext, sym: dict, minutes: int | None) -> None:
    if not minutes:
        return
    due = ctx.now + timedelta(minutes=minutes)
    ctx.state["recheck"] = {"symptom_ids": sym["ids"], "labels": sym["labels"], "due_at": due.isoformat()}
    ctx.actions.append({"type": "recheck", "minutes": minutes, "due_at": due.isoformat()})


def _finish(ctx: ToolContext, sym: dict) -> dict:
    ctx.state.pop("symptom", None)
    prescribed = prescribed_as_needed(ctx)
    if not sym["ids"]:
        guidance = guidance_for([], "NOT_ASSESSED", ctx.lang, prescribed)
        ctx.actions.append({"type": "triage_result", "severity": "NOT_ASSESSED", "labels": sym["labels"],
                            "guidance": guidance})
        _schedule_recheck(ctx, sym, guidance["recheck_minutes"])
        return {"ok": True, "severity": "NOT_ASSESSED", "guidance": guidance,
                "explain": "SmartPoli's rules can't rate this symptom automatically. Say so calmly; if their doctor "
                           "prescribed something for it, remind them to follow that instruction; say you'll check back, "
                           "and to see their doctor if it persists or gets worse."}
    result, check = record_symptom_check(ctx.db, ctx.patient_id, ctx.actor, RULESET, sym["ids"], sym["answers"],
                                         min_severity=sym.get("floor"), source="voice")
    guidance = guidance_for(sym["ids"], result["severity"], ctx.lang, prescribed)
    action = {"type": "emergency" if result["severity"] == "EMERGENCY" else "triage_result",
              "severity": result["severity"], "reasons": result["reasons"], "action": result["action"],
              "route": result["route"], "check_id": check.id, "guidance": guidance}
    ctx.actions.append(action)
    _schedule_recheck(ctx, sym, guidance["recheck_minutes"])
    return {"ok": True, "severity": result["severity"], "reasons": result["reasons"],
            "next_action": result["action"], "guidance": guidance,
            "explain": "Severity comes from SmartPoli's clinical rules — never change it. Order your reply as: "
                       "1) what to try now — any 'prescribed' instruction first (their own doctor's, as written; say "
                       "'agar doctor ne ise isi problem ke liye diya hai'), then the 'general' steps exactly as listed, "
                       "nothing else; 2) that you'll ask how they feel in recheck_minutes; 3) only then: if it isn't "
                       "better by then, see a doctor (for MODERATE add: within a day or two either way). "
                       "Don't lead with 'see a doctor'. Say at most the first three general steps aloud — "
                       "the screen shows them all."}


def update_symptom_check(ctx: ToolContext, symptom_ids=None, symptom_labels=None, answers=None) -> dict:
    current = clean_symptom_state(ctx.state.get("symptom"))
    # A "yes" can only raise severity, so it is always accepted. A "no" is
    # accepted only for a question the patient was actually asked in an
    # earlier turn — the model can't quietly clear a red-flag question.
    new_answers = {k: v for k, v in (answers if isinstance(answers, dict) else {}).items()
                   if v is True or (v is False and k in ctx.asked_before)}
    ignored = sorted(set(answers if isinstance(answers, dict) else {}) - set(new_answers))
    sym = clean_symptom_state({
        "ids": current["ids"] + _as_list(symptom_ids),
        "labels": current["labels"] + _as_list(symptom_labels),
        "answers": {**current["answers"], **new_answers},
        "asked": current["asked"],
        "floor": current.get("floor"),
        "verify": current.get("verify"),
    })
    ctx.state["symptom"] = sym
    if sym["ids"] and evaluate_check(RULESET, sym["ids"], sym["answers"])["severity"] == "EMERGENCY":
        return _finish(ctx, sym)
    questions = open_questions(sym)
    mark_asked(sym, questions)
    return {"ok": True, "tracked_symptoms": sym["ids"], "unrated_symptoms": sym["labels"] if not sym["ids"] else [],
            "ignored_answers": ignored,
            "questions_to_ask": questions,
            "next_step": "Ask the listed questions one at a time, in the patient's language, then call "
                         "finish_symptom_check." if sym["ids"] else
                         "No SmartPoli rule covers this symptom; call finish_symptom_check when you've understood it."}


IMMEDIATE_RISK_CATEGORIES = {
    "unconscious": "Loss of consciousness",
    "seizure": "Seizure happening now",
    "self_harm": "Thoughts of self-harm",
    "overdose": "Possible overdose",
    "stroke_signs": "Signs of stroke",
    "cannot_breathe_or_speak": "Gasping, choking or too breathless to speak",
}


def report_immediate_risk(ctx: ToolContext, category=None, patient_words=None) -> dict:
    """Groq recognises the danger; the category must be one of a fixed list,
    and this can only ever raise severity."""
    reason = IMMEDIATE_RISK_CATEGORIES.get(category)
    if not reason:
        return _err("Not an immediate-risk category. Continue the symptom check instead.")
    sym = clean_symptom_state(ctx.state.get("symptom"))
    words = str(patient_words or "")[:200]
    result, check = record_symptom_check(
        ctx.db, ctx.patient_id, ctx.actor, RULESET, sym["ids"], sym["answers"],
        extra_reasons=[f"{reason} (patient said: \"{words}\")"], force_emergency=True, source="voice")
    ctx.state.pop("symptom", None)
    ctx.state.pop("recheck", None)
    ctx.actions.append({"type": "emergency", "severity": "EMERGENCY", "reasons": result["reasons"],
                        "action": result["action"], "route": result["route"], "check_id": check.id})
    return {"ok": True, "severity": "EMERGENCY"}


def record_recheck(ctx: ToolContext, status=None) -> dict:
    """better → keep monitoring; same → medical review (MODERATE floor, the
    rules can only raise it); worse → the rules re-assess from the start."""
    if status not in ("better", "same", "worse"):
        return _err("status must be better, same or worse.")
    recheck = ctx.state.pop("recheck", None)
    if not isinstance(recheck, dict):
        return _err("There is no recheck waiting.")
    ids = [s for s in _as_list(recheck.get("symptom_ids")) if isinstance(s, str) and s in RULESET["symptoms"]]
    labels = [s[:60] for s in _as_list(recheck.get("labels")) if isinstance(s, str)][:5]
    ctx.actions.append({"type": "recheck_done", "status": status})
    if status == "better":
        return {"ok": True, "next_step": "Say you're glad; they should keep resting and tell you if it comes back or gets worse."}
    if status == "same":
        result, check = record_symptom_check(ctx.db, ctx.patient_id, ctx.actor, RULESET, ids, {},
                                             extra_reasons=["Not improving when rechecked"],
                                             min_severity="MODERATE", source="voice-recheck")
        ctx.actions.append({"type": "triage_result", "severity": result["severity"], "reasons": result["reasons"],
                            "action": result["action"], "route": result["route"], "check_id": check.id,
                            "guidance": None})
        return {"ok": True, "severity": result["severity"],
                "next_step": "It isn't improving: calmly suggest seeing a doctor in the next day or two."}
    sym = clean_symptom_state({"ids": ids, "labels": labels})
    sym["floor"] = "MODERATE"
    questions = open_questions(sym)
    mark_asked(sym, questions)
    ctx.state["symptom"] = sym
    return {"ok": True, "questions_to_ask": questions,
            "next_step": "It got worse: say you'll ask a few questions again, then ask them one at a time "
                         "(most safety-critical first) and finish_symptom_check."}


def search_clinical_guidance(ctx: ToolContext, query=None, symptom_ids=None) -> dict:
    hits = clinical_knowledge.search(str(query or "")[:200], _as_list(symptom_ids))
    if not hits:
        return {"ok": True, "guidance": [],
                "use": "No trusted SmartPoli guidance matches. Say you don't have reliable information on this, "
                       "don't guess, and suggest asking their doctor if it's worrying them."}
    guidance = [{"topic": h["topic"], "kind": h["kind"],
                 "text": (h.get("text_hi") or h["text"]) if ctx.lang == "hi" and h["kind"] == "self_care" else h["text"],
                 "source": h["publisher"], "document": h["document"], "version": h["version"], "url": h["url"]}
                for h in hits]
    seen, shown = set(), []
    for h in hits:
        if h["url"] not in seen:
            seen.add(h["url"])
            shown.append({"publisher": h["publisher"], "document": h["document"], "url": h["url"]})
    ctx.actions.append({"type": "sources", "items": shown[:4]})
    return {"ok": True, "guidance": guidance,
            "use": "Explain only this, simply and in the patient's language; you may say whose guidance it is "
                   "(e.g. 'MoHFW guidance ke hisaab se'). It is general information, not a diagnosis: never name a "
                   "medicine or treatment from it, and it never changes SmartPoli's rules result. The patient's own "
                   "prescription and doctor's notes come first."}


def finish_symptom_check(ctx: ToolContext) -> dict:
    if "symptom" not in ctx.state:
        return _err("No symptom check in progress. Call update_symptom_check first.")
    sym = clean_symptom_state(ctx.state["symptom"])
    remaining = open_questions(sym)
    if remaining:
        mark_asked(sym, remaining)
        ctx.state["symptom"] = sym
        return {**_err("These questions still need an answer before SmartPoli can decide."),
                "questions_to_ask": remaining}
    return _finish(ctx, sym)


# ---------------------------------------------------------------- registry

HANDLERS: dict[str, Callable[..., dict]] = {
    "get_today_schedule": get_today_schedule, "get_next_dose": get_next_dose,
    "mark_dose_taken": mark_dose_taken, "log_prn_taken": log_prn_taken,
    "get_medicines": get_medicines, "get_adherence": get_adherence,
    "get_emergency_card": get_emergency_card, "get_treatment_history": get_treatment_history,
    "get_patient_context": get_patient_context, "get_doctor_notes": get_doctor_notes,
    "get_prn_history": get_prn_history, "get_safety_warnings": get_safety_warnings,
    "recall_previous": recall_previous,
    "open_screen": open_screen, "decode_prescription": decode_prescription,
    "confirm_prescription": confirm_prescription, "update_symptom_check": update_symptom_check,
    "finish_symptom_check": finish_symptom_check,
    "report_immediate_risk": report_immediate_risk, "record_recheck": record_recheck,
    "search_clinical_guidance": search_clinical_guidance,
}


def _fn(name: str, description: str, properties: dict | None = None, required: list | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or []}}}


TOOLS = [
    _fn("get_today_schedule", "Today's doses with dose_id, time, state and can_mark_taken."),
    _fn("get_next_dose", "The next upcoming dose."),
    _fn("mark_dose_taken", "Mark one scheduled dose as taken. Use a dose_id from get_today_schedule. "
        "If more than one dose could match what the patient said, ask which one first.",
        {"dose_id": {"type": "integer"}}, ["dose_id"]),
    _fn("log_prn_taken", "Record that the patient took an as-needed (SOS) medicine.",
        {"medicine_id": {"type": "integer"}}, ["medicine_id"]),
    _fn("get_medicines", "The patient's current medicines with dose, timing and food instructions."),
    _fn("get_adherence", "Taken / missed / skipped counts today and overall, and doses missed today."),
    _fn("get_emergency_card", "The patient's emergency card details; also opens the card on screen."),
    _fn("get_treatment_history", "Past prescriptions and recent symptom checks."),
    _fn("get_patient_context", "The patient's existing record: conditions, allergies, current and as-needed "
        "medicines (with the doctor's instruction), doctor notes and recent symptom checks. Use it as context "
        "before responding to a symptom; never assume a past condition causes the current symptom."),
    _fn("open_screen", "Open a screen of the full SmartPoli app.",
        {"screen": {"type": "string", "enum": list(SCREENS)}}, ["screen"]),
    _fn("decode_prescription", "Read prescription text (one medicine per line) into a draft to review.",
        {"text": {"type": "string"}}, ["text"]),
    _fn("confirm_prescription", "Schedule the decoded draft — only after the patient said yes to it.",
        {"prescription_id": {"type": "integer"}}, ["prescription_id"]),
    _fn("update_symptom_check",
        "Start or update a symptom check. symptom_ids: rule ids that match what the patient describes "
        f"({', '.join(RULESET['symptoms'])}); symptom_labels: the patient's own words for symptoms; "
        "answers: {question_id: true/false} for questions the patient has clearly answered. "
        "Returns the questions still to ask.",
        {"symptom_ids": {"type": "array", "items": {"type": "string"}},
         "symptom_labels": {"type": "array", "items": {"type": "string"}},
         "answers": {"type": "object", "additionalProperties": {"type": "boolean"}}}),
    _fn("finish_symptom_check", "Ask SmartPoli's clinical rules for the result once the questions are answered."),
    _fn("report_immediate_risk",
        "ONLY for something unmistakably life-threatening happening RIGHT NOW (not in the past, not a worry, "
        "not a symptom that still needs checking). Shows the patient how to get help immediately.",
        {"category": {"type": "string", "enum": list(IMMEDIATE_RISK_CATEGORIES)},
         "patient_words": {"type": "string"}}, ["category", "patient_words"]),
    _fn("get_doctor_notes", "The patient's doctor's notes and instructions, newest first.",
        {"limit": {"type": "integer"}}),
    _fn("get_prn_history", "When the patient used their as-needed (SOS) medicines recently.",
        {"days": {"type": "integer"}}),
    _fn("get_safety_warnings", "Medicine interactions and foods to avoid with their current medicines."),
    _fn("recall_previous",
        "What the patient said on earlier days and their past symptom checks with outcomes — use when they refer "
        "to before (\"wahi problem\", \"phir se\", \"kal wala\") or a symptom may be recurring. Context only, "
        "never a diagnosis.", {"query": {"type": "string"}, "days": {"type": "integer"}}),
    _fn("search_clinical_guidance",
        "Trusted clinical information (MoHFW India treatment guidelines, MedlinePlus, sourced self-care). "
        "Call this BEFORE explaining anything medical — what a symptom can relate to, prevention, self-care, "
        "when to see a doctor. Use ONLY what it returns. query: plain English words for the symptom/condition.",
        {"query": {"type": "string"}, "symptom_ids": {"type": "array", "items": {"type": "string"}}}, ["query"]),
    _fn("record_recheck", "Record how the patient feels when you check back after self-care: better, same or worse.",
        {"status": {"type": "string", "enum": ["better", "same", "worse"]}}, ["status"]),
]


def run_tool(ctx: ToolContext, name: str, args: Any) -> dict:
    handler = HANDLERS.get(name)
    if handler is None:
        return _err(f"Unknown tool {name!r}.")
    if not isinstance(args, dict):
        return _err("Tool arguments must be a JSON object.")
    try:
        return handler(ctx, **args)
    except TypeError:
        return _err(f"Bad arguments for {name}.")
    except Exception:  # a bad value must never break the turn or leave a half-written change
        ctx.db.rollback()
        return _err(f"{name} could not be completed.")
