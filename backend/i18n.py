"""
SmartPoli — optional Feature G: Hindi/English display.

Deliberately NOT machine translation. Medical dosage instructions are the
one place a wrong word matters, so Hindi output is built the same way the
English plain-language text is (backend/shorthand.py::to_plain_language) —
straight off the deterministic, already-tested structured schedule dict,
substituting fixed, human-checked Hindi phrases for fixed English ones.
There is no LLM anywhere in this file. shorthand.py itself, and its 26
tests, are untouched; this is a second, additive renderer of the exact
same structured result, not a replacement.

Covers: prescription plain-language text, and the fixed triage vocabulary
(symptom labels, questions, actions) via parallel *_hi fields already
carried by triage_rules.json.
"""

from shorthand import parse_slot_code, fmt_qty

SLOT_LABEL_HI = {
    "morning": "सुबह",
    "afternoon": "दोपहर",
    "evening": "शाम",
    "night": "रात",
    "bedtime": "सोने से पहले",
}

FREQUENCY_LABEL_HI = {
    "once daily": "दिन में एक बार",
    "once daily in the morning": "सुबह एक बार",
    "twice daily": "दिन में दो बार",
    "three times daily": "दिन में तीन बार",
    "four times daily": "दिन में चार बार",
    "at bedtime": "सोने से पहले",
    "every 4 hours": "हर 4 घंटे में",
    "every 6 hours": "हर 6 घंटे में",
    "every 8 hours": "हर 8 घंटे में",
    "every 12 hours": "हर 12 घंटे में",
    "as needed": "आवश्यकता होने पर",
    "immediately, once": "अभी, एक बार",
    "no doses scheduled": "कोई खुराक निर्धारित नहीं",
}


def _unit_word_hi(_n: float) -> str:
    return "गोली"  # uninflected — kept simple and unambiguous on purpose


def _frequency_label_hi(label: str) -> str:
    if label in FREQUENCY_LABEL_HI:
        return FREQUENCY_LABEL_HI[label]
    # dynamic "{n} time(s) daily" from the raw slot-code path
    import re
    m = re.match(r"(\d+) time\(s\) daily", label or "")
    if m:
        return f"दिन में {m.group(1)} बार"
    return label  # unknown label: show the English original rather than guess


def to_plain_language_hi(result: dict) -> str:
    """Same branching as shorthand.py::to_plain_language, Hindi phrases."""
    lines = []
    r = result

    if r["prn"]:
        lines.append("केवल आवश्यकता होने पर लें।")
        lines.append("कोई निश्चित समय नहीं — यह दवा आपके दैनिक शेड्यूल में शामिल नहीं है।")
    elif r["stat"]:
        lines.append("अभी एक बार खुराक लें।")
    elif r["scheduleCode"] and r["scheduleCode"][:1].isdigit() and "-" in r["scheduleCode"]:
        parsed = parse_slot_code(r["scheduleCode"])
        for i, name in enumerate(parsed["names"]):
            qty = parsed["quantities"][i]
            lines.append(f"{SLOT_LABEL_HI[name]} — {fmt_qty(qty)} {_unit_word_hi(qty)}")
    elif r["times"] and not r["slots"]:
        lines.append(f"लें {_frequency_label_hi(r['frequencyLabel'])} — समय: {', '.join(r['times'])}।")
    else:
        lines.append(f"{_frequency_label_hi(r['frequencyLabel'])} लें।")
        for d in r["doses"]:
            lines.append(f"{SLOT_LABEL_HI[d['slot']]} — {fmt_qty(d['qty'])} {_unit_word_hi(d['qty'])}, समय {d['time']}")

    if r["food"] == "after":
        lines.append("भोजन के बाद लें।")
    elif r["food"] == "before":
        lines.append("भोजन से पहले, खाली पेट लें।")

    if r["ongoing"]:
        lines.append("डॉक्टर के कहने तक जारी रखें।")
    elif r["durationDays"]:
        lines.append(f"{r['durationDays']} दिन के लिए।")

    return "\n".join(lines)


def localized_plain_language(result: dict, lang: str = "en") -> str:
    if lang == "hi":
        return to_plain_language_hi(result)
    return result["plainLanguage"]


# ---------------------------------------------------------------- triage

def localized_symptom_label(symptom: dict, lang: str) -> str:
    return symptom.get("label_hi") if lang == "hi" and symptom.get("label_hi") else symptom["label"]


def localized_question_text(question: dict, lang: str) -> str:
    return question.get("text_hi") if lang == "hi" and question.get("text_hi") else question["text"]


ACTION_HI = {
    "Seek emergency medical attention immediately. Call 112.":
        "तुरंत आपातकालीन चिकित्सा सहायता लें। 112 पर कॉल करें।",
    "Book a routine consultation within 24-48 hours.":
        "24-48 घंटों के भीतर डॉक्टर से सामान्य सलाह लें।",
    "Continue monitoring. Recheck if symptoms worsen.":
        "निगरानी जारी रखें। लक्षण बढ़ने पर फिर से जाँच करें।",
}


def localized_action(action: str, lang: str) -> str:
    return ACTION_HI.get(action, action) if lang == "hi" else action
