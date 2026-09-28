"""
SmartPoli — deterministic red-flag check for the voice assistant.

Runs on the patient's raw words on EVERY voice turn, before any LLM call.
A hit means EMERGENCY immediately. This is a fixed phrase table in code —
not an LLM judgement — and it can only ever raise severity, never lower it
(triage.py's rule engine stays the source of every other severity).

Covers English, romanised Hindi (Hinglish) and Devanagari, because that is
how patients actually speak to the assistant.
"""

import re

# (reason shown to the patient/doctor, patterns — any match triggers)
RED_FLAGS: list[tuple[str, list[str]]] = [
    ("Severe difficulty breathing", [
        r"\bcan'?t breathe\b", r"\bcannot breathe\b", r"\bunable to breathe\b", r"\bnot able to breathe\b",
        r"\bsaa?ns\b.{0,20}\b(nahi|nahin|nhi)\b.{0,12}\b(aa|aa rahi|aa raha|le pa)",
        r"\bsaa?ns\b.{0,25}\b(bahut|bohot|kaafi)\b.{0,15}\b(dikkat|takleef|problem)",
        r"साँस नहीं", r"सांस नहीं", r"साँस लेने में बहुत",
    ]),
    ("Loss of consciousness", [
        r"\bunconscious\b", r"\bpassed out\b", r"\bnot waking up\b", r"\bfainted\b",
        r"\bbehosh\b", r"\bhosh nahi\b", r"बेहोश",
    ]),
    ("Seizure", [
        r"\bseizures?\b", r"\bfits?\b.{0,10}\b(aa|pad)", r"\bconvuls", r"\bdaura\b", r"\bdaure\b", r"\bmirgi\b", r"दौरा", r"मिर्गी",
    ]),
    ("Chest pain with sweating", [
        r"chest pain.{0,40}sweat", r"sweat.{0,40}chest pain",
        r"(seen[ae]|chhati|chhaati).{0,25}dard.{0,40}(pasina|paseena)",
        r"(pasina|paseena).{0,40}(seen[ae]|chhati|chhaati).{0,25}dard",
        r"सीने.{0,20}दर्द.{0,30}पसीना",
    ]),
    ("Signs of stroke", [
        r"face.{0,15}droop", r"slurred speech", r"speech is slurred", r"\bslurr",
        r"\bmunh\b.{0,15}\b(tedha|terha)\b", r"\bbol nahi pa", r"मुँह टेढ़ा",
    ]),
    ("Heavy bleeding", [
        r"heavy bleeding", r"bleeding (a lot|heavily|won'?t stop|that won'?t stop|not stopping)",
        r"\b(bahut|bohot) (zyada |jyada )?khoon\b", r"khoon.{0,15}(ruk nahi|band nahi)", r"बहुत खून",
    ]),
    ("Thoughts of self-harm", [
        r"\bkill myself\b", r"\bend my life\b", r"\bsuicid", r"\bhurt myself\b",
        r"\bkhudkushi\b", r"\bkhud ko (khatam|nuksan|nuksaan)", r"\bjaan de (dunga|dungi|du)\b",
        r"आत्महत्या", r"खुदकुशी",
    ]),
    ("Possible overdose", [
        r"too many (pills|tablets|medicines)", r"\boverdose\b",
        r"\b(zyada|jyada|bahut saari|bahut sari) (goliyan|goliyaan|tablets|dawai|dawaiyan)\b.{0,15}\b(kha|le) li",
        r"ज़्यादा गोलियाँ",
    ]),
]

_COMPILED = [(reason, [re.compile(p, re.IGNORECASE) for p in patterns]) for reason, patterns in RED_FLAGS]


def scan_red_flags(text: str) -> list[str]:
    """Reasons for every red flag present in `text` (empty if none)."""
    if not text:
        return []
    return [reason for reason, patterns in _COMPILED if any(p.search(text) for p in patterns)]
