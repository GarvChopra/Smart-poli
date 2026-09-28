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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from db import Dose, Medicine, Prescription, SymptomCheck, User, log_audit
from prescription_service import create_prescription_from_lines, confirm_prescription_doses
from scheduler import mark_taken, compute_adherence
from serializers import emergency_card_data
from triage import load_ruleset, evaluate_check
from triage_service import record_symptom_check

RULESET = load_ruleset()

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

    @property
    def actor(self) -> str:
        return f"patient:{self.user.id}"


def _err(message: str) -> dict:
    return {"ok": False, "error": message}


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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

def clean_symptom_state(raw: Any) -> dict:
    """The client carries this between turns — never trust it: keep only
    real rule symptom ids and boolean answers to their real question ids."""
    raw = raw if isinstance(raw, dict) else {}
    ids = [s for s in raw.get("ids", []) if isinstance(s, str) and s in RULESET["symptoms"]]
    ids = list(dict.fromkeys(ids))
    valid_q = {q["id"] for sid in ids for q in RULESET["symptoms"][sid]["questions"]}
    answers = {k: v for k, v in (raw.get("answers") or {}).items() if k in valid_q and isinstance(v, bool)}
    labels = list(dict.fromkeys(str(s)[:60] for s in raw.get("labels", []) if s))[:5]
    return {"ids": ids, "labels": labels, "answers": answers}


def _open_questions(sym: dict) -> list[dict]:
    # Same rule as triage.next_question: once EMERGENCY, stop asking.
    if evaluate_check(RULESET, sym["ids"], sym["answers"])["severity"] == "EMERGENCY":
        return []
    return [{"id": q["id"], "symptom": sid, "text": q["text"], "text_hi": q.get("text_hi", "")}
            for sid in sym["ids"] for q in RULESET["symptoms"][sid]["questions"]
            if q["id"] not in sym["answers"]]


def _finish(ctx: ToolContext, sym: dict) -> dict:
    ctx.state.pop("symptom", None)
    if not sym["ids"]:
        ctx.actions.append({"type": "triage_result", "severity": "NOT_ASSESSED", "labels": sym["labels"]})
        return {"ok": True, "severity": "NOT_ASSESSED",
                "explain": "SmartPoli's rules can't rate this symptom automatically. Tell the patient that, "
                           "and to contact their doctor if it is severe, getting worse, or worrying them."}
    result, check = record_symptom_check(ctx.db, ctx.patient_id, ctx.actor, RULESET,
                                         sym["ids"], sym["answers"], source="voice")
    action = {"type": "emergency" if result["severity"] == "EMERGENCY" else "triage_result",
              "severity": result["severity"], "reasons": result["reasons"], "action": result["action"],
              "route": result["route"], "check_id": check.id}
    ctx.actions.append(action)
    return {"ok": True, "severity": result["severity"], "reasons": result["reasons"],
            "next_action": result["action"],
            "explain": "This result comes from SmartPoli's clinical rules. Explain it simply; do not change it."}


def update_symptom_check(ctx: ToolContext, symptom_ids=None, symptom_labels=None, answers=None) -> dict:
    current = ctx.state.get("symptom") or {}
    merged = {
        "ids": list(current.get("ids", [])) + list(symptom_ids or []),
        "labels": list(current.get("labels", [])) + list(symptom_labels or []),
        "answers": {**(current.get("answers") or {}), **(answers if isinstance(answers, dict) else {})},
    }
    sym = clean_symptom_state(merged)
    ctx.state["symptom"] = sym
    if sym["ids"] and evaluate_check(RULESET, sym["ids"], sym["answers"])["severity"] == "EMERGENCY":
        return _finish(ctx, sym)
    return {"ok": True, "tracked_symptoms": sym["ids"], "unrated_symptoms": sym["labels"] if not sym["ids"] else [],
            "questions_to_ask": _open_questions(sym),
            "next_step": "Ask the listed questions one at a time, in the patient's language, then call "
                         "finish_symptom_check." if sym["ids"] else
                         "No SmartPoli rule covers this symptom; call finish_symptom_check when you've understood it."}


def finish_symptom_check(ctx: ToolContext) -> dict:
    if "symptom" not in ctx.state:
        return _err("No symptom check in progress. Call update_symptom_check first.")
    sym = clean_symptom_state(ctx.state["symptom"])
    remaining = _open_questions(sym)
    if remaining:
        return {**_err("These questions still need an answer before SmartPoli can decide."),
                "questions_to_ask": remaining}
    return _finish(ctx, sym)


# ---------------------------------------------------------------- registry

HANDLERS: dict[str, Callable[..., dict]] = {
    "get_today_schedule": get_today_schedule, "get_next_dose": get_next_dose,
    "mark_dose_taken": mark_dose_taken, "log_prn_taken": log_prn_taken,
    "get_medicines": get_medicines, "get_adherence": get_adherence,
    "get_emergency_card": get_emergency_card, "get_treatment_history": get_treatment_history,
    "open_screen": open_screen, "decode_prescription": decode_prescription,
    "confirm_prescription": confirm_prescription, "update_symptom_check": update_symptom_check,
    "finish_symptom_check": finish_symptom_check,
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
