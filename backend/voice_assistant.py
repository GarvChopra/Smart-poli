"""
SmartPoli — the voice assistant's turn loop.

    patient's words
      → voice_safety.scan_red_flags   (deterministic; EMERGENCY before any LLM)
      → Groq chat with tool calling   (understanding + conversation)
      → voice_tools.run_tool          (validated router over existing services)
      → reply text + UI actions + state for the next turn

Groq is only the conversational layer: it never writes to the database and
never decides severity. If a tool returns an EMERGENCY (rule engine) the
spoken reply is a fixed sentence, whatever the model wrote. Without a
GROQ_API_KEY, or if Groq fails, voice_fallback handles the turn.
"""

import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from db import User
from serializers import get_patient_or_404
from triage_service import record_symptom_check
from voice_fallback import fallback_turn
from voice_safety import scan_red_flags
from voice_tools import RULESET, TOOLS, ToolContext, run_tool, clean_symptom_state, open_questions, mark_asked

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
MAX_HISTORY = 20
RATE_LIMIT_PER_MINUTE = 30
_turns_by_user: dict[int, deque] = defaultdict(deque)

EMERGENCY_REPLY = {
    "en": "This could be an emergency. Call 112 now, or ask someone near you to call. "
          "Your emergency contact and card are on the screen.",
    "hi": "Yeh emergency ho sakti hai. Abhi 112 par call kijiye, ya paas kisi se call karwaiye. "
          "Aapka emergency contact aur card screen par hai.",
}
TOOL_LOOP_REPLY = {
    "en": "Sorry, I couldn't finish that. Could you say it again in a different way?",
    "hi": "Maaf kijiye, main yeh poora nahi kar paaya. Kya aap dobara thoda alag tarah se bolenge?",
}


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


def _system_prompt(ctx: ToolContext, first_name: str) -> str:
    language = ("the patient's own language and script: Hindi written in Latin letters (Hinglish) gets a "
                "Hinglish reply in Latin letters; Devanagari gets Devanagari; English gets English. "
                "Never mix scripts in one reply")
    return f"""You are SmartPoli, a friendly voice assistant inside a medication and care app.
You are talking with {first_name}. Their local time is {ctx.now.strftime('%A %d %B %Y, %H:%M')}.
Reply in {language}. Replies are spoken aloud: keep them short (1-3 sentences), warm, plain words, no markdown or lists.

What you can do — always through the tools, never from memory:
- Medicines: today's schedule, next dose, marking a dose taken, as-needed (SOS) medicine, adherence, medicine list.
  To mark a dose: call get_today_schedule, pick the dose the patient means (morning/subah, afternoon/dopahar,
  evening/shaam, night/raat, or by medicine name) among those with can_mark_taken, then call mark_dose_taken.
  If more than one dose could match, ask which one — never guess. After marking, tell them their next dose.
- Emergency card, treatment history, opening a screen of the app, reading a prescription the patient dictates
  (decode_prescription; read it back; schedule it with confirm_prescription only after they say yes).
- Symptoms: when the patient describes feeling unwell, run a symptom check.
  1. Call update_symptom_check with matching rule symptom_ids and the patient's own words as symptom_labels.
  2. Ask follow-up questions ONE at a time, like a caring doctor would: you may ask when it started or how bad it is,
     and you MUST cover every question in questions_to_ask, in your own natural words and the patient's language.
  3. After each answer, call update_symptom_check with answers {{question_id: true/false}} only when the answer is clear.
  4. When nothing is left to ask, call finish_symptom_check and explain its result simply.

Safety rules — never break these:
- You never decide how serious a symptom is. Only finish_symptom_check's severity counts; never contradict it,
  soften it or invent one. If it says NOT_ASSESSED, say SmartPoli can't rate this symptom automatically and they
  should contact their doctor if it is severe, getting worse, or worrying them.
- Never diagnose. Never tell the patient to start, stop, skip or change any medicine or dose.
- Every fact about their medicines, doses or history must come from a tool result in this conversation.
- If they mention something life-threatening, tell them to call 112 immediately.
""" + _symptom_context(ctx)


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


def _history_messages(history) -> list[dict]:
    out = []
    for item in (history or [])[-MAX_HISTORY:]:
        if isinstance(item, dict) and item.get("role") in ("user", "assistant") and isinstance(item.get("content"), str):
            out.append({"role": item["role"], "content": item["content"][:1000]})
    return out


def _red_flag_turn(ctx: ToolContext, reasons: list[str]) -> dict:
    """Must never fail: whatever happens while recording, the patient still
    gets the fixed emergency reply and the help panel."""
    action = {"type": "emergency", "severity": "EMERGENCY", "reasons": reasons,
              "action": "Seek emergency medical attention immediately. Call 112.", "route": "emergency"}
    try:
        sym = clean_symptom_state(ctx.state.get("symptom"))
        result, check = record_symptom_check(
            ctx.db, ctx.patient_id, ctx.actor, RULESET, sym["ids"], sym["answers"],
            extra_reasons=[f"Red flag in patient's words: {r}" for r in reasons], force_emergency=True, source="voice")
        action.update(reasons=result["reasons"], action=result["action"], route=result["route"], check_id=check.id)
    except Exception:
        logger.exception("Could not record red-flag symptom check")
        ctx.db.rollback()
    ctx.state.pop("symptom", None)
    ctx.actions.append(action)
    return {"reply": EMERGENCY_REPLY.get(ctx.lang, EMERGENCY_REPLY["en"]), "actions": ctx.actions,
            "state": ctx.state, "mode": "safety"}


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
        if any(a["type"] == "emergency" for a in ctx.actions):
            break

    if any(a["type"] == "emergency" for a in ctx.actions):
        # The rule engine said EMERGENCY: the spoken words are fixed, not model text.
        reply = EMERGENCY_REPLY.get(ctx.lang, EMERGENCY_REPLY["en"])
    if not reply:
        # The model sometimes ends without any words; ask once more for text only.
        reply = (complete("none").choices[0].message.content or "").strip()
    if not reply:
        reply = TOOL_LOOP_REPLY.get(ctx.lang, TOOL_LOOP_REPLY["en"])
    return {"reply": reply, "actions": ctx.actions, "state": ctx.state, "mode": "assistant"}


def run_turn(db: Session, patient_id: int, user: User, text: str, lang: str,
             now_local: datetime, history, state) -> dict:
    patient = get_patient_or_404(db, patient_id)
    ctx = ToolContext(db=db, patient_id=patient_id, user=user, now=now_local, lang=lang,
                      state=state if isinstance(state, dict) else {})

    red_flags = scan_red_flags(text)
    if red_flags:
        return _red_flag_turn(ctx, red_flags)

    if not is_available():
        return fallback_turn(ctx, text)
    try:
        return _groq_turn(ctx, text, history, (patient.name or "there").split()[0])
    except Exception as e:  # network, auth, model errors — the page must still work
        logger.warning("Groq voice turn failed, using fallback: %s", e)
        ctx.db.rollback()
        if any(a["type"] == "emergency" for a in ctx.actions):
            return {"reply": EMERGENCY_REPLY.get(lang, EMERGENCY_REPLY["en"]), "actions": ctx.actions,
                    "state": ctx.state, "mode": "assistant"}
        if ctx.actions:  # something already happened this turn; don't repeat it
            return {"reply": TOOL_LOOP_REPLY.get(lang, TOOL_LOOP_REPLY["en"]), "actions": ctx.actions,
                    "state": ctx.state, "mode": "assistant"}
        return fallback_turn(ctx, text)
