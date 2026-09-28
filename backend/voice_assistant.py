"""
SmartPoli — the voice assistant's turn loop: a calm first-response companion.

    CALM → HELP → RECHECK → ESCALATE

With Groq (normal):
    patient's words → Groq understands EVERYTHING — the symptom, whether an
    answer means mild or severe, "better/same/worse", and whether something is
    unmistakably life-threatening right now — and acts only through the
    validated tools in voice_tools. Severity always comes from triage.py.

Without Groq (no key, or Groq failed):
    a small deterministic safety net (voice_safety) — immediate danger gets
    help, worrying words get one calm question, and nothing is ever inferred
    from keywords: the patient answers on the symptom-check screen.

Groq never writes to the database, never decides severity, never invents
treatment. If the rules say EMERGENCY the spoken reply is a fixed calm sentence.
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

from datetime import timedelta

from db import User, VoiceMessage
from serializers import get_patient_or_404
from triage_service import record_symptom_check
from triage import ACTIONS
from voice_fallback import fallback_turn
from voice_safety import scan_red_flags, VERIFY_QUESTION
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


_HINGLISH = re.compile(r"\b(mujhe|mujhko|mera|meri|mere|hai|hain|ho rahi|ho raha|nahi|nahin|mein|kya|aap|kaise|"
                       r"dard|saans|dawai|dawa|le li|kab|abhi|bahut|thoda|thodi|kuch|hoon|haan|theek|chakkar)\b", re.I)
_ENGLISH = re.compile(r"\b(i|i'm|my|the|is|am|have|has|what|when|please|feel|feeling|some|it)\b", re.I)


def spoken_language(text: str, toggle: str) -> str:
    """Reply in the language the patient actually used; the toggle only
    decides when their words don't show it."""
    if re.search(r"[ऀ-ॿ]", text or ""):
        return "hi"
    hi, en = len(_HINGLISH.findall(text or "")), len(_ENGLISH.findall(text or ""))
    if hi > en:
        return "hi"
    if en > hi:
        return "en"
    return toggle


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

def _earlier_lines(ctx: ToolContext) -> str:
    """Up to three things the patient said on earlier days — enough for "wahi problem"
    to land; recall_previous has the rest."""
    try:
        rows = (ctx.db.query(VoiceMessage)
                .filter(VoiceMessage.patient_id == ctx.patient_id, VoiceMessage.role == "user",
                        VoiceMessage.created_at >= datetime.utcnow() - timedelta(days=3),
                        VoiceMessage.created_at < datetime.utcnow() - timedelta(hours=6))
                .order_by(VoiceMessage.created_at.desc()).limit(3).all())
    except Exception:
        return "none"
    return "; ".join(f'{r.created_at.date().isoformat()}: "{r.content[:120]}"' for r in rows) or "none"


def _record_context(ctx: ToolContext) -> str:
    """A compact profile every turn — enough that no one is treated as a blank
    new patient, without dumping the record. Everything else (doctor notes,
    SOS history, safety warnings, earlier days) comes through tools when relevant."""
    try:
        c = get_patient_context(ctx)
    except Exception:
        logger.exception("Could not load patient context")
        return ""
    meds = ", ".join(c["current_medicines"]) or "none"
    prn = "; ".join(f'{m["name"]} — "{m["instruction"]}"' for m in c["as_needed_medicines"]) or "none"
    latest_note = (f'{c["doctor_notes"][0]["date"]} (more via get_doctor_notes)' if c["doctor_notes"] else "none")
    recent = [x for x in c["recent_symptom_checks"]
              if x["date"] >= (datetime.now() - timedelta(days=14)).date().isoformat()][:3]
    episodes = "; ".join(f'{x["date"]}: {", ".join(x["symptoms"]) or "unrated symptom"} ({x["severity"]})'
                         for x in recent) or "none in the last 14 days"
    return f"""
PATIENT RECORD (from SmartPoli — compact; use tools for more):
- Known conditions: {c["conditions"] or "none recorded"}
- Allergies: {c["allergies"] or "none recorded"}
- Current medicines: {meds}
- As-needed medicines prescribed by their doctor (instruction as written): {prn}
- Latest doctor's note: {latest_note}
- Recent symptom episodes: {episodes}
- They said on earlier days (last 3 days): {_earlier_lines(ctx)}
MEMORY RULE: anything from before is context, not a diagnosis. "Aapko kal bhi … hua tha" is fine; "so it's the same
cause" is not. If they say "wahi problem" / "phir se", call recall_previous, say what happened before, ask whether
today feels the same, and run today's check as usual.
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
     Ask the most safety-critical question first — for breathing: can they say a full sentence; for chest pain:
     any breathing difficulty; for bleeding: does it stop with firm pressure.
     Understand answers the way a person would: "nahin, itni jyada nahin hai, thodi si hai" is a clear NO to
     "is it very severe?"; mixed or unclear answers → gently ask again, never guess.
  4. After each answer, call update_symptom_check with answers {{question_id: true/false}} only when the answer is clear.
  5. When nothing is left to ask, call finish_symptom_check and follow its "explain" order: first what to TRY now
     (their own doctor's prescribed instruction first — "agar doctor ne ise isi problem ke liye diya hai, to unke
     bataye tareeke se lijiye" — then the general steps exactly as listed, nothing else), then that you'll check back,
     and only then the doctor if it doesn't get better.

- Immediate danger: call report_immediate_risk ONLY if the patient describes something unmistakably
  life-threatening happening right now (unconscious, a seizure now, wanting to harm themselves, an overdose,
  stroke signs, gasping/choking or too breathless to speak). A worrying symptom is NOT that — check it with the rules.
- Recheck: if the patient tells you how they feel after the self-care (better / same / worse, in any words),
  call record_recheck with what they mean and follow its next_step.

- Medical knowledge: you are not the knowledge base. Before explaining anything medical (what a symptom can be
  related to, prevention, self-care, when to see a doctor) call search_clinical_guidance and use only what it
  returns, mentioning whose guidance it is when helpful ("MoHFW guidance ke hisaab se…"). If it returns nothing,
  say you don't have reliable information on that rather than answering from memory.

Safety rules — never break these:
- You never decide how serious a symptom is. Only finish_symptom_check's severity counts; never contradict or soften it.
- Never suggest a medicine, inhaler, nebulizer or remedy that isn't in the guidance or their own prescription.
  Never change a prescription. Never diagnose.
- Never falsely reassure ("kuch nahi hai", "you're fine", "nothing to worry about"). Be calm, not dismissive.
- Don't use alarming words like "emergency" or "danger" yourself — the app shows help when the rules call for it.
- Every fact about their medicines, doses or history must come from the record or a tool result.
""" + _record_context(ctx) + _symptom_context(ctx)


def _stored_history(ctx: ToolContext) -> list[dict]:
    """This conversation's recent turns, from the server — never trusted from the client."""
    since = datetime.utcnow() - timedelta(hours=6)
    rows = (ctx.db.query(VoiceMessage).filter(VoiceMessage.patient_id == ctx.patient_id,
                                              VoiceMessage.created_at >= since)
            .order_by(VoiceMessage.created_at.desc(), VoiceMessage.id.desc()).limit(MAX_HISTORY).all())
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


def _remember(db: Session, patient_id: int, text: str, reply: str) -> None:
    try:
        db.add_all([VoiceMessage(patient_id=patient_id, role="user", content=text[:1000]),
                    VoiceMessage(patient_id=patient_id, role="assistant", content=(reply or "")[:2000])])
        db.commit()
    except Exception:
        logger.exception("Could not store voice turn")
        db.rollback()


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


def _offline_recheck_turn(ctx: ToolContext, status: str) -> dict:
    result = run_tool(ctx, "record_recheck", {"status": status})
    if not result.get("ok"):
        return _result(ctx, _say(TOOL_LOOP_REPLY, ctx.lang), "recheck")
    reply = _say(RECHECK[status], ctx.lang)
    if status == "worse" and result.get("questions_to_ask"):
        q = result["questions_to_ask"][0]
        reply += q["text_hi"] if ctx.lang == "hi" and q.get("text_hi") else q["text"]
    return _result(ctx, reply, "recheck")


def _recheck_status(text: str) -> Optional[str]:
    """Offline only — with Groq, Groq understands the reply (record_recheck)."""
    for status, pattern in _RECHECK_WORDS:
        if pattern.search(text):
            return status
    return None


def _offline_turn(ctx: ToolContext, text: str) -> dict:
    """No AI available: a deterministic safety net, and nothing inferred from keywords."""
    flags = scan_red_flags(text)
    if flags["immediate"]:
        return _emergency_turn(ctx, flags["immediate"])
    if isinstance(ctx.state.get("recheck"), dict):
        status = _recheck_status(text)
        if status:
            return _offline_recheck_turn(ctx, status)
    # A symptom check is waiting for an answer we can't understand without the AI:
    # the patient answers on the symptom-check screen instead of us guessing.
    if clean_symptom_state(ctx.state.get("symptom")).get("verify"):
        ctx.state.pop("symptom", None)
        run_tool(ctx, "open_screen", {"screen": "triage"})
        return _result(ctx, _say(VERIFY_MILD_OFFLINE, ctx.lang), "fallback")
    for flag in flags["verify"]:
        if _needs_verification(ctx, flag):
            return _verify_turn(ctx, flag, text)
    return fallback_turn(ctx, text)


# ---------------------------------------------------------------- Groq

def _groq_turn(ctx: ToolContext, text: str, history, first_name: str) -> dict:
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    models = _voice_models()

    messages = [{"role": "system", "content": _system_prompt(ctx, first_name)}]
    messages += _history_messages(_stored_history(ctx))
    messages.append({"role": "user", "content": text})

    def complete(tool_choice: str):
        # max_tokens is generous because reasoning models spend part of it
        # thinking before they write; too low and the reply comes back empty.
        return _create_with_fallback(client, models, messages=messages, tools=TOOLS,
                                     tool_choice=tool_choice, temperature=0.3, max_tokens=1500)

    reply: Optional[str] = None
    called: list[str] = []
    evidence: list = []
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
            called.append(c.function.name)
            if c.function.name not in OPERATIONAL_TOOLS and result.get("ok"):
                evidence.append({"tool": c.function.name, "result": result})
            messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result, default=str)})
        if _has_emergency(ctx):
            break

    if _has_emergency(ctx):
        # The rules said EMERGENCY: the spoken words are fixed and calm, not model text.
        return _result(ctx, _say(EMERGENCY_REPLY, ctx.lang), "assistant")
    if not reply:
        # The model sometimes ends without any words; ask once more for text only.
        reply = (complete("none").choices[0].message.content or "").strip()
    if not reply:
        return _result(ctx, _say(TOOL_LOOP_REPLY, ctx.lang), "assistant")
    # Anything beyond pure schedule/app operations may carry medical content:
    # it is only spoken once it's grounded in this turn's evidence.
    if not called or not set(called) <= OPERATIONAL_TOOLS:
        reply = _grounded(client, [_guard_model()] + models, ctx, text, reply, evidence)
    return _result(ctx, reply, "assistant")


def _voice_models() -> list[str]:
    """Models to try in order — when one hits its rate limit (e.g. Groq's free-tier
    tokens-per-day), the next is used instead of dropping to the offline net."""
    configured = os.getenv("GROQ_VOICE_MODELS")
    if configured:
        return [m.strip() for m in configured.split(",") if m.strip()]
    return list(dict.fromkeys([os.getenv("GROQ_VOICE_MODEL", "openai/gpt-oss-120b"), "openai/gpt-oss-20b"]))


def _guard_model() -> str:
    """The grounding check is a simpler task, so it defaults to the lighter model
    and leaves the main model's daily token budget for the conversation."""
    return os.getenv("GROQ_GUARD_MODEL", "openai/gpt-oss-20b")


def _is_rate_limited(err: Exception) -> bool:
    return getattr(err, "status_code", None) == 429 or "rate limit" in str(err).lower() \
        or "tokens per day" in str(err).lower()


def _create_with_fallback(client, models: list[str], **kwargs):
    last = None
    for model in models:
        try:
            return client.chat.completions.create(model=model, **kwargs)
        except Exception as err:  # only a rate limit moves on to the next model
            if not _is_rate_limited(err):
                raise
            logger.warning("Groq model %s rate-limited, trying the next one", model)
            last = err
    raise last


# Tools that only read or change the medicine schedule / app — a reply built only on
# these is operational ("next dose at 8"), not medical advice, so it skips the check.
OPERATIONAL_TOOLS = {"get_today_schedule", "get_next_dose", "mark_dose_taken", "log_prn_taken", "get_medicines",
                     "get_adherence", "get_emergency_card", "open_screen", "decode_prescription",
                     "confirm_prescription", "get_treatment_history"}

SAFE_REPLY = {
    "en": "I don't have reliable information to guide you on that right now. Could you tell me a little more about how you feel?",
    "hi": "Is baare mein abhi mere paas bharosemand jaankari nahi hai. Kya aap thoda aur batayenge ki aap kaisa mehsoos kar rahe hain?",
}

GUARD_PROMPT = """You check a health assistant's draft reply before it is spoken to a patient.
EVIDENCE is the ONLY approved medical information for this turn: retrieved guidance (with its source), SmartPoli's
rules result and self-care list, and the patient's own prescriptions and doctor's notes.
Rewrite the draft so that:
- every medical statement, self-care step, remedy or medicine is supported by EVIDENCE; remove anything that isn't
  (no invented remedies, medicines, devices, doses or home treatments);
- a source is only credited ("MoHFW guidance ke hisaab se…") for what that source's evidence actually says;
- a doctor's instruction or a prescribed as-needed medicine is mentioned only for the situation it names
  (e.g. an instruction "if wheezy" only when the patient reports wheezing), and always as "if your doctor gave this
  for this problem";
- nothing diagnoses, and nothing falsely reassures ("kuch nahi hai", "you're fine");
- questions, empathy, schedule facts and anything non-medical stay as they are; same language and script; short.
If nothing medical survives, keep the empathy and ask a gentle follow-up question.
Reply with ONLY the final text to speak — no quotes, no labels, no explanation."""


def _grounded(client, models: list[str], ctx: ToolContext, patient_text: str, draft: str, evidence: list) -> str:
    """Second, strict pass: the spoken reply may only contain medical content the
    evidence supports. If this check can't run, the unchecked draft is NOT spoken."""
    try:
        record = get_patient_context(ctx)
        evidence = evidence + [{"patient_record": {"as_needed_medicines": record["as_needed_medicines"],
                                                   "doctor_notes": record["doctor_notes"][:3],
                                                   "conditions": record["conditions"],
                                                   "allergies": record["allergies"]}}]
        # plain text, not JSON mode: the lighter model sometimes fails Groq's strict JSON validation
        response = _create_with_fallback(
            client, list(dict.fromkeys(models)), temperature=0, max_tokens=1500,
            messages=[{"role": "system", "content": GUARD_PROMPT},
                      {"role": "user", "content": json.dumps({"patient_said": patient_text, "evidence": evidence,
                                                              "draft_reply": draft}, ensure_ascii=False, default=str)}])
        reply = (response.choices[0].message.content or "").strip().strip('"').strip()
        return reply or _sourced_fallback(ctx)
    except Exception:
        logger.exception("Grounding check failed; not speaking the unchecked reply")
        return _sourced_fallback(ctx)


def _sourced_fallback(ctx: ToolContext) -> str:
    """When the check can't run, never the unchecked draft: speak the approved,
    sourced steps already on the card (if any), else a gentle safe line."""
    card = next((a for a in ctx.actions if a["type"] == "triage_result" and a.get("guidance")), None)
    steps = [g["text"] for g in (card["guidance"].get("general") or [])][:3] if card else []
    if not steps:
        return _say(SAFE_REPLY, ctx.lang)
    minutes = next((a["minutes"] for a in ctx.actions if a["type"] == "recheck"), None)
    intro = {"hi": "Abhi yeh kar sakte hain:", "en": "Here's what you can do for now:"}
    later = {"hi": f" Main {minutes} minute baad poochunga ki aap kaisa mehsoos kar rahe hain.",
             "en": f" I'll check how you feel in {minutes} minutes."}
    return f"{_say(intro, ctx.lang)} {' '.join(steps)}" + (_say(later, ctx.lang) if minutes else "")


def _has_emergency(ctx: ToolContext) -> bool:
    return any(a["type"] == "emergency" for a in ctx.actions)


# ---------------------------------------------------------------- entry point

def run_turn(db: Session, patient_id: int, user: User, text: str, lang: str,
             now_local: datetime, history, state, recheck: Optional[str] = None) -> dict:
    lang = spoken_language(text, lang)
    out = _run_turn(db, patient_id, user, text, lang, now_local, history, state, recheck)
    out.setdefault("lang", lang)  # the page picks the speaking voice from this
    _remember(db, patient_id, text, out.get("reply", ""))
    return out


def _run_turn(db: Session, patient_id: int, user: User, text: str, lang: str,
              now_local: datetime, history, state, recheck: Optional[str]) -> dict:
    patient = get_patient_or_404(db, patient_id)
    ctx = ToolContext(db=db, patient_id=patient_id, user=user, now=now_local, lang=lang,
                      state=state if isinstance(state, dict) else {})

    # A tapped Better / Same / Worse button is the patient's own choice, not an interpretation.
    if recheck in ("better", "same", "worse") and isinstance(ctx.state.get("recheck"), dict):
        return _offline_recheck_turn(ctx, recheck)

    if is_available():
        try:
            return _groq_turn(ctx, text, history, (patient.name or "there").split()[0])
        except Exception as e:  # network, auth, model errors — the page must still work
            logger.warning("Groq voice turn failed, using the offline safety net: %s", e)
            ctx.db.rollback()
            if _has_emergency(ctx):
                return _result(ctx, _say(EMERGENCY_REPLY, lang), "assistant")
            if ctx.actions:  # something already happened this turn; don't repeat it
                return _result(ctx, _say(TOOL_LOOP_REPLY, lang), "assistant")
    return _offline_turn(ctx, text)
