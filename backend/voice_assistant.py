"""
SmartPoli — the voice assistant's turn loop: a calm first-response companion.

    CALM → HELP → RECHECK → ESCALATE

    patient's words
      → recheck answer?              ("better / same / worse" after self-care)
      → verification answer?         (reply to the calm first question below)
      → voice_safety.scan_red_flags  (deterministic, before any LLM)
           immediate risk  → help straight away (calm screen)
           worrying words  → ONE calm verification question from the rules
      → Groq chat with tool calling  (understanding + conversation, with the
                                      patient's own record as context)
      → voice_tools.run_tool         (validated router over existing services)
      → reply text + UI actions + state for the next turn

Groq is only the conversational layer: it never writes to the database,
never decides severity and never invents treatment. Severity comes from
triage.py; guidance from the patient's own prescriptions and the sourced
self_care.json. If the rules say EMERGENCY the spoken reply is a fixed,
calm sentence. Without a GROQ_API_KEY, or if Groq fails, voice_fallback
handles the turn.
"""

import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from db import User
from serializers import get_patient_or_404
from triage_service import record_symptom_check
from triage import ACTIONS
from voice_fallback import fallback_turn
from voice_safety import scan_red_flags, interpret_verification_answer, VERIFY_QUESTION
from voice_tools import (RULESET, TOOLS, ToolContext, run_tool, clean_symptom_state, open_questions, mark_asked,
                         get_patient_context)

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
MAX_HISTORY = 20
RATE_LIMIT_PER_MINUTE = 30
_turns_by_user: dict[int, deque] = defaultdict(deque)

# Calm on purpose: clear next step, no alarm words.
EMERGENCY_REPLY = {
    "en": "Please get medical help now — call 112, or ask someone near you to call. "
          "Stay calm; the buttons on the screen will call for you.",
    "hi": "Abhi doctor ki madad lena zaroori hai — 112 par call kijiye, ya paas kisi se call karwaiye. "
          "Ghabraiye nahi, screen par button dabane se call ho jayega.",
}
TOOL_LOOP_REPLY = {
    "en": "Sorry, I couldn't finish that. Could you say it again in a different way?",
    "hi": "Maaf kijiye, main yeh poora nahi kar paaya. Kya aap dobara thoda alag tarah se bolenge?",
}
VERIFY_MILD_OFFLINE = {
    "en": "Okay. Let's go through SmartPoli's symptom check together — I'm opening it for you.",
    "hi": "Theek hai. Chaliye SmartPoli ka symptom check saath mein karte hain — main use khol raha hoon.",
}
RECHECK = {
    "better": {"en": "Good to hear it's getting better. Keep resting, and tell me if it comes back or gets worse.",
               "hi": "Yeh sunkar achha laga ki behtar ho raha hai. Aaram karte rahiye, aur agar phir se ho ya badhe to mujhe batayein."},
    "same": {"en": "Since it isn't improving, it's best to see a doctor within the next day or two. "
                   "You can keep checking in with me meanwhile.",
             "hi": "Kyunki abhi farak nahi pada hai, agle ek-do din mein doctor ko dikhana behtar rahega. "
                   "Tab tak aap mujhse haal batate rahiye."},
    "worse": {"en": "Thanks for telling me. Let me ask a few questions again so we choose the right next step. ",
              "hi": "Batane ke liye shukriya. Main kuch sawaal dobara poochta hoon taaki sahi agla kadam chun sakein. "},
}
_RECHECK_WORDS = [
    ("worse", re.compile(r"\b(worse|worst|badh|zyada ho|jyada ho|aur kharab|bigad)", re.I)),
    ("better", re.compile(r"\b(better|behtar|kam ho|theek (ho|lag)|improv|aaram (hai|mila))", re.I)),
    ("same", re.compile(r"\b(same|waisa hi|waise hi|utna hi|farak nahi|no change)\b", re.I)),
]


def is_available() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def reset_rate_limits() -> None:
    _turns_by_user.clear()


def check_rate_limit(user_id: int) -> bool:
    """True if this user may take another turn now."""
    now = time.monotonic()
    turns = _turns_by_user[user_id]
    while turns and now - turns[0] > 60:
        turns.popleft()
    if len(turns) >= RATE_LIMIT_PER_MINUTE:
        return False
    turns.append(now)
    return True


def _say(table: dict, lang: str) -> str:
    return table.get(lang, table["en"])


def _result(ctx: ToolContext, reply: str, mode: str) -> dict:
    return {"reply": reply, "actions": ctx.actions, "state": ctx.state, "mode": mode}


# ---------------------------------------------------------------- prompt

def _record_context(ctx: ToolContext) -> str:
    """The patient's existing record, every turn, so no one is treated as a
    blank new patient. Context only — never proof of a cause."""
    try:
        c = get_patient_context(ctx)
    except Exception:
        logger.exception("Could not load patient context")
        return ""
    meds = ", ".join(c["current_medicines"]) or "none"
    prn = "; ".join(f'{m["name"]} — "{m["instruction"]}"' for m in c["as_needed_medicines"]) or "none"
    notes = "; ".join(f'{n["date"]}: {n["note"]}' for n in c["doctor_notes"][:3]) or "none"
    checks = "; ".join(f'{x["date"]}: {", ".join(x["symptoms"]) or "unrated symptom"} ({x["severity"]})'
                       for x in c["recent_symptom_checks"][:3]) or "none"
    return f"""
PATIENT RECORD (from SmartPoli):
- Known conditions: {c["conditions"] or "none recorded"}
- Allergies: {c["allergies"] or "none recorded"}
- Current medicines: {meds}
- As-needed medicines prescribed by their doctor (with the instruction as written): {prn}
- Doctor's notes: {notes}
- Recent symptom checks: {checks}
"""


def _symptom_context(ctx: ToolContext) -> str:
    """The check in progress, restated every turn: tool results from earlier
    turns aren't in the history, so without this the model can't see which
    question ids are still open and can't record the patient's answers."""
    if "symptom" not in ctx.state:
        return ""
    sym = clean_symptom_state(ctx.state["symptom"])
    questions = open_questions(sym)
    mark_asked(sym, questions)  # they're in front of the model now; next turn's "no" counts
    ctx.state["symptom"] = sym
    answered = ", ".join(f"{k}={'true' if v else 'false'}" for k, v in sym["answers"].items()) or "none yet"
    open_q = "\n".join(f'  - {q["id"]}: "{q["text"]}" / "{q["text_hi"]}"' for q in questions) or "  (none)"
    return f"""
SYMPTOM CHECK IN PROGRESS — symptoms: {', '.join(sym['ids']) or 'none covered by the rules'} (patient's words: {', '.join(sym['labels']) or '-'})
Answers recorded so far: {answered}
Questions still open (id: English / Hindi):
{open_q}
First, if the patient's latest message answers any open question — even indirectly ("dheere dheere shuru hua" means
not sudden; "nazar theek hai" means no vision change) — call update_symptom_check with those answers.
Then ask the next open question, or call finish_symptom_check when none are left.
"""


def _system_prompt(ctx: ToolContext, first_name: str) -> str:
    language = ("the patient's own language and script: Hindi written in Latin letters (Hinglish) gets a "
                "Hinglish reply in Latin letters; Devanagari gets Devanagari; English gets English. "
                "Never mix scripts in one reply")
    return f"""You are SmartPoli, a calm, caring health companion inside a medication and care app — not an alarm.
You are talking with {first_name}. Their local time is {ctx.now.strftime('%A %d %B %Y, %H:%M')}.
Reply in {language}. Replies are spoken aloud: short (1-3 sentences), warm, plain words, one question at a time,
no markdown or lists.

What you can do — always through the tools, never from memory:
- Medicines: today's schedule, next dose, marking a dose taken, as-needed (SOS) medicine, adherence, medicine list.
  To mark a dose: call get_today_schedule, pick the dose the patient means (morning/subah, afternoon/dopahar,
  evening/shaam, night/raat, or by medicine name) among those with can_mark_taken, then call mark_dose_taken.
  If more than one dose could match, ask which one — never guess. After marking, tell them their next dose.
- Emergency card, treatment history, opening a screen of the app, reading a prescription the patient dictates
  (decode_prescription; read it back; schedule it with confirm_prescription only after they say yes).
- Symptoms — CALM, then HELP, then RECHECK, and escalate only when the rules say so:
  1. Look at the PATIENT RECORD below first. If they've had this before or have a related condition, acknowledge it
     gently ("Aapko pehle bhi aisa hua tha…"), but never assume the old condition is causing this one.
  2. Call update_symptom_check with matching rule symptom_ids and the patient's own words as symptom_labels.
  3. Ask follow-up questions ONE at a time, like a caring doctor: you may ask when it started or how bad it is, and you
     MUST cover every question in questions_to_ask, in your own natural words and the patient's language.
  4. After each answer, call update_symptom_check with answers {{question_id: true/false}} only when the answer is clear.
  5. When nothing is left to ask, call finish_symptom_check. Explain the result calmly, then give its guidance:
     first their own doctor's prescribed instruction (say "agar doctor ne ise isi problem ke liye diya hai, to unke
     bataye tareeke se lijiye"), then the general steps exactly as listed — nothing else. Tell them you'll check back.

Safety rules — never break these:
- You never decide how serious a symptom is. Only finish_symptom_check's severity counts; never contradict or soften it.
- Never suggest a medicine, inhaler, nebulizer or remedy that isn't in the guidance or their own prescription.
  Never change a prescription. Never diagnose.
- Never falsely reassure ("kuch nahi hai", "you're fine", "nothing to worry about"). Be calm, not dismissive.
- Don't use alarming words like "emergency" or "danger" yourself — the app shows help when the rules call for it.
- Every fact about their medicines, doses or history must come from the record or a tool result.
""" + _record_context(ctx) + _symptom_context(ctx)


def _history_messages(history) -> list[dict]:
    out = []
    for item in (history or [])[-MAX_HISTORY:]:
        if isinstance(item, dict) and item.get("role") in ("user", "assistant") and isinstance(item.get("content"), str):
            out.append({"role": item["role"], "content": item["content"][:1000]})
    return out


# ---------------------------------------------------------------- deterministic turns

def _emergency_turn(ctx: ToolContext, reasons: list[str]) -> dict:
    """Unmistakable immediate risk. Must never fail: whatever happens while
    recording, the patient still gets the calm help screen and reply."""
    action = {"type": "emergency", "severity": "EMERGENCY", "reasons": reasons,
              "action": ACTIONS["EMERGENCY"]["action"], "route": "emergency"}
    try:
        sym = clean_symptom_state(ctx.state.get("symptom"))
        result, check = record_symptom_check(
            ctx.db, ctx.patient_id, ctx.actor, RULESET, sym["ids"], sym["answers"],
            extra_reasons=[f"Said by patient: {r}" for r in reasons], force_emergency=True, source="voice")
        action.update(reasons=result["reasons"], action=result["action"], check_id=check.id)
    except Exception:
        logger.exception("Could not record immediate-risk symptom check")
        ctx.db.rollback()
    ctx.state.pop("symptom", None)
    ctx.state.pop("recheck", None)
    ctx.actions.append(action)
    return _result(ctx, _say(EMERGENCY_REPLY, ctx.lang), "safety")


def _verify_turn(ctx: ToolContext, flag: dict, text: str) -> dict:
    """Worrying words, not yet verified: start that symptom's rule check and
    ask its most important question — calmly, with no alarm."""
    sym = clean_symptom_state(ctx.state.get("symptom"))
    sym["ids"] = list(dict.fromkeys(sym["ids"] + [flag["symptom"]]))
    sym["labels"] = list(dict.fromkeys(sym["labels"] + [text[:60]]))[:5]
    qid = next(q for q in flag["questions"] if q not in sym["answers"])
    sym["verify"] = qid
    sym["asked"] = list(dict.fromkeys(sym["asked"] + [qid]))
    ctx.state["symptom"] = sym
    ctx.actions.append({"type": "calm_check", "symptom": flag["symptom"]})
    return _result(ctx, VERIFY_QUESTION[flag["symptom"]].get(ctx.lang, VERIFY_QUESTION[flag["symptom"]]["en"]), "safety")


def _needs_verification(ctx: ToolContext, flag: dict) -> bool:
    sym = clean_symptom_state(ctx.state.get("symptom"))
    return any(q not in sym["answers"] and q not in sym["asked"] for q in flag["questions"][:1])


def _recheck_turn(ctx: ToolContext, status: str) -> dict:
    recheck = ctx.state.pop("recheck", None) or {}
    ids = [s for s in recheck.get("symptom_ids", []) if isinstance(s, str) and s in RULESET["symptoms"]]
    labels = [str(x)[:60] for x in recheck.get("labels", []) if isinstance(x, str)][:5]
    ctx.actions.append({"type": "recheck_done", "status": status})
    if status == "better":
        return _result(ctx, _say(RECHECK["better"], ctx.lang), "recheck")
    if status == "same":
        # Not improving → MEDICAL REVIEW. The rules can still raise this; nothing lowers it.
        result, check = record_symptom_check(ctx.db, ctx.patient_id, ctx.actor, RULESET, ids, {},
                                             extra_reasons=["Not improving when rechecked"],
                                             min_severity="MODERATE", source="voice-recheck")
        ctx.actions.append({"type": "triage_result", "severity": result["severity"], "reasons": result["reasons"],
                            "action": result["action"], "route": result["route"], "check_id": check.id,
                            "guidance": None})
        return _result(ctx, _say(RECHECK["same"], ctx.lang), "recheck")
    # worse → re-assess with the rules, at least MEDICAL REVIEW whatever the answers
    sym = clean_symptom_state({"ids": ids, "labels": labels})
    sym["floor"] = "MODERATE"
    questions = open_questions(sym)
    mark_asked(sym, questions[:1])
    ctx.state["symptom"] = sym
    first = questions[0] if questions else None
    ask = (first["text_hi"] if ctx.lang == "hi" and first.get("text_hi") else first["text"]) if first else ""
    return _result(ctx, _say(RECHECK["worse"], ctx.lang) + ask, "recheck")


def _recheck_status(text: str) -> Optional[str]:
    for status, pattern in _RECHECK_WORDS:
        if pattern.search(text):
            return status
    return None


def _apply_verification_answer(ctx: ToolContext, text: str) -> Optional[dict]:
    """The reply to the calm verification question is recorded deterministically
    when it's clear ("bahut zyada" / "halki hai"); the rule engine decides."""
    sym = clean_symptom_state(ctx.state.get("symptom"))
    qid = sym.get("verify")
    if not qid:
        return None
    answer = interpret_verification_answer(text)
    if answer is None:
        return None
    sym.pop("verify", None)
    ctx.state["symptom"] = sym
    return run_tool(ctx, "update_symptom_check", {"answers": {qid: answer}})


# ---------------------------------------------------------------- Groq

def _groq_turn(ctx: ToolContext, text: str, history, first_name: str) -> dict:
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    model = os.getenv("GROQ_VOICE_MODEL", "openai/gpt-oss-120b")

    messages = [{"role": "system", "content": _system_prompt(ctx, first_name)}]
    messages += _history_messages(history)
    messages.append({"role": "user", "content": text})

    def complete(tool_choice: str):
        # max_tokens is generous because reasoning models spend part of it
        # thinking before they write; too low and the reply comes back empty.
        return client.chat.completions.create(model=model, messages=messages, tools=TOOLS,
                                              tool_choice=tool_choice, temperature=0.3, max_tokens=1500)

    reply: Optional[str] = None
    for _ in range(MAX_TOOL_ROUNDS):
        response = complete("auto")
        msg = response.choices[0].message
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            reply = (msg.content or "").strip()
            break
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
            for c in calls]})
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                result = {"ok": False, "error": "Arguments were not valid JSON."}
            else:
                result = run_tool(ctx, c.function.name, args)
            messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result, default=str)})
        if _has_emergency(ctx):
            break

    if _has_emergency(ctx):
        # The rules said EMERGENCY: the spoken words are fixed and calm, not model text.
        reply = _say(EMERGENCY_REPLY, ctx.lang)
    if not reply:
        # The model sometimes ends without any words; ask once more for text only.
        reply = (complete("none").choices[0].message.content or "").strip()
    return _result(ctx, reply or _say(TOOL_LOOP_REPLY, ctx.lang), "assistant")


def _has_emergency(ctx: ToolContext) -> bool:
    return any(a["type"] == "emergency" for a in ctx.actions)


# ---------------------------------------------------------------- entry point

def run_turn(db: Session, patient_id: int, user: User, text: str, lang: str,
             now_local: datetime, history, state, recheck: Optional[str] = None) -> dict:
    patient = get_patient_or_404(db, patient_id)
    ctx = ToolContext(db=db, patient_id=patient_id, user=user, now=now_local, lang=lang,
                      state=state if isinstance(state, dict) else {})

    flags = scan_red_flags(text)
    if flags["immediate"]:
        return _emergency_turn(ctx, flags["immediate"])

    # RECHECK: "better / same / worse" after self-care (button or words)
    if isinstance(ctx.state.get("recheck"), dict):
        status = recheck if recheck in ("better", "same", "worse") else _recheck_status(text)
        if status:
            return _recheck_turn(ctx, status)

    # The reply to the calm verification question: recorded, then the rules decide.
    verified = _apply_verification_answer(ctx, text)
    if verified is not None and _has_emergency(ctx):
        return _result(ctx, _say(EMERGENCY_REPLY, ctx.lang), "safety")

    # Worrying but unverified words: one calm question, no alarm.
    if verified is None:
        for flag in flags["verify"]:
            if _needs_verification(ctx, flag):
                return _verify_turn(ctx, flag, text)

    if not is_available():
        if verified is not None:
            run_tool(ctx, "open_screen", {"screen": "triage"})
            return _result(ctx, _say(VERIFY_MILD_OFFLINE, ctx.lang), "fallback")
        return fallback_turn(ctx, text)
    try:
        return _groq_turn(ctx, text, history, (patient.name or "there").split()[0])
    except Exception as e:  # network, auth, model errors — the page must still work
        logger.warning("Groq voice turn failed, using fallback: %s", e)
        ctx.db.rollback()
        if _has_emergency(ctx):
            return _result(ctx, _say(EMERGENCY_REPLY, lang), "assistant")
        if ctx.actions:  # something already happened this turn; don't repeat it
            return _result(ctx, _say(TOOL_LOOP_REPLY, lang), "assistant")
        return fallback_turn(ctx, text)
