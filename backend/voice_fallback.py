"""
SmartPoli — voice assistant without an LLM.

Used when GROQ_API_KEY isn't set or the Groq call fails, so the voice page
still does the everyday things by keyword matching (English, Hinglish,
Devanagari): I took my medicine, next dose, today's medicines, open a
screen. Symptoms are sent to the existing symptom-check screen — this mode
never tries to hold a clinical conversation. Every action goes through the
same voice_tools router as the Groq path.
"""

import re

from voice_tools import ToolContext, run_tool

_TOOK = re.compile(r"\b(le li|le liya|kha li|kha liya|li hai|took|taken|have taken|had my)\b|ले ली|खा ली", re.I)
_MED = re.compile(r"\b(dawai|dawa|dava|medicine|medicines|goli|tablet|pill|pills|dose)\b|दवा|दवाई|गोली", re.I)
_NEXT = re.compile(r"\b(agli|next|kab hai|when)\b|अगली", re.I)
_TODAY = re.compile(r"\b(aaj|today|dikhao|show|list|meri medicines)\b|आज", re.I)
_SYMPTOM = re.compile(
    r"\b(dard|pain|ache|chakkar|dizzy|bukhar|fever|ulti|vomit|nausea|ji machla|khansi|cough|rash|khujli|"
    r"khoon|bleed|saans|breath|theek nahi|not feeling well|headache)\b|दर्द|चक्कर|बुखार|उल्टी", re.I)
_SCREENS = [
    ("timeline", r"\b(timeline|history|itihaas)\b"),
    ("report", r"\b(report|riport)\b"),
    ("prescriptions", r"\b(prescription|parcha|purchi)\b"),
    ("dashboard", r"\b(adherence|dashboard)\b"),
    ("emergency", r"\b(emergency card|card)\b"),
    ("safety", r"\b(safety|interaction)\b"),
    ("triage", r"\b(symptom check|lakshan)\b"),
]

T = {
    "en": {
        "marked": "Done. {medicine} is marked taken.",
        "next_after": " Your next medicine is {next} at {time}.",
        "which": "More than one dose is due now. Which one did you take?",
        "none_due": "I couldn't find a dose due around now. Open your dashboard to mark it.",
        "next": "Your next medicine is {next} at {time}.",
        "no_next": "You have no more doses scheduled.",
        "today": "Today you have: {list}.",
        "today_none": "No medicines are scheduled for today.",
        "opening": "Opening it for you.",
        "symptom": "Let's check that with SmartPoli's symptom check. I'm opening it for you.",
        "unknown": "I can help with your medicines right now: say \"I took my medicine\", "
                   "\"when is my next medicine\" or \"show my timeline\".",
    },
    "hi": {
        "marked": "Ho gaya. {medicine} le li gayi hai, maine note kar liya.",
        "next_after": " Aapki agli dawai {next} hai, {time} baje.",
        "which": "Abhi ek se zyada dawai ka samay hai. Aapne kaun si li?",
        "none_due": "Abhi koi dawai ka samay nahi mila. Dashboard par mark kar dijiye.",
        "next": "Aapki agli dawai {next} hai, {time} baje.",
        "no_next": "Aapki aage koi dawai scheduled nahi hai.",
        "today": "Aaj aapki dawaiyan: {list}.",
        "today_none": "Aaj koi dawai scheduled nahi hai.",
        "opening": "Khol raha hoon.",
        "symptom": "Chaliye SmartPoli ke symptom check se dekhte hain. Main use khol raha hoon.",
        "unknown": "Abhi main dawaiyon mein madad kar sakta hoon: boliye \"maine dawai le li\", "
                   "\"agli dawai kab hai\" ya \"timeline dikhao\".",
    },
}


def fallback_turn(ctx: ToolContext, text: str) -> dict:
    t = T.get(ctx.lang, T["en"])

    def reply(message: str) -> dict:
        return {"reply": message, "actions": ctx.actions, "state": ctx.state, "mode": "fallback"}

    if _TOOK.search(text) and (_MED.search(text) or re.search(r"subah|shaam|raat|morning|evening|night", text, re.I)):
        due = [d for d in run_tool(ctx, "get_today_schedule", {})["doses"]
               if d["can_mark_taken"] and d["state"] != "taken"]
        if len(due) == 1:
            result = run_tool(ctx, "mark_dose_taken", {"dose_id": due[0]["dose_id"]})
            msg = t["marked"].format(medicine=due[0]["medicine"])
            if result.get("next_dose"):
                msg += t["next_after"].format(next=result["next_dose"]["medicine"], time=result["next_dose"]["time"])
            return reply(msg)
        if len(due) > 1:
            ctx.actions.append({"type": "choose_dose", "options": [
                {"dose_id": d["dose_id"], "medicine": d["medicine"], "time": d["time"]} for d in due]})
            return reply(t["which"])
        return reply(t["none_due"])

    if _NEXT.search(text):
        nxt = run_tool(ctx, "get_next_dose", {})["next_dose"]
        return reply(t["next"].format(next=nxt["medicine"], time=nxt["time"]) if nxt else t["no_next"])

    for screen, pattern in _SCREENS:
        if re.search(pattern, text, re.I):
            run_tool(ctx, "open_screen", {"screen": screen})
            return reply(t["opening"])

    if _SYMPTOM.search(text):
        run_tool(ctx, "open_screen", {"screen": "triage"})
        return reply(t["symptom"])

    if _MED.search(text) and _TODAY.search(text):
        doses = run_tool(ctx, "get_today_schedule", {})["doses"]
        if not doses:
            return reply(t["today_none"])
        return reply(t["today"].format(list=", ".join(f"{d['medicine']} {d['time']}" for d in doses)))

    return reply(t["unknown"])
