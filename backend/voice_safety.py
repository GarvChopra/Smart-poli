"""
SmartPoli — deterministic safety check on the patient's own words.

Runs on every voice turn before any LLM call. It never decides a severity
from a single worrying word. Two tiers:

- IMMEDIATE: unmistakable, happening-now danger — unconscious, a seizure,
  self-harm, an overdose, stroke signs, gasping/choking or too breathless
  to talk. These can't wait for a question (the person may not be able to
  answer), so help is shown straight away.
- VERIFY: worrying but unverified — breathing difficulty, chest pain,
  heavy bleeding. No alarm: the assistant starts that symptom's check in
  triage_rules.json and calmly asks its most important rule question
  first. triage.py then decides from the answer.

Past events ("I fainted last year") are history, not an emergency.
Fixed phrase tables in code — not an LLM judgement.
"""

import re

IMMEDIATE: list[tuple[str, list[str]]] = [
    ("Loss of consciousness", [
        r"\bunconscious\b", r"\bpassed out\b", r"\bnot waking up\b", r"\bnot responding\b",
        r"\bbehosh\b", r"\bhosh nahi\b", r"बेहोश",
    ]),
    ("Seizure", [
        r"\bseizures?\b", r"\bconvuls", r"\bfits?\b.{0,10}\b(aa|pad)", r"\bdaura\b", r"\bdaure\b", r"\bmirgi\b",
        r"दौरा", r"मिर्गी",
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
    ("Signs of stroke", [
        r"face.{0,15}droop", r"slurred speech", r"speech is slurred", r"\bslurr",
        r"\bmunh\b.{0,15}\b(tedha|terha)\b", r"मुँह टेढ़ा",
    ]),
    ("Severe breathing difficulty", [
        r"\bgasping\b", r"\bchoking\b", r"\bsaa?ns\b.{0,20}\bbilkul\b.{0,10}\b(nahi|nahin)\b",
        r"saa?ns.{0,30}\b(bol nahi|bol nhi|nahi bol)\b.{0,10}\bpa", r"too breathless to (talk|speak)",
        r"can'?t (talk|speak).{0,20}breath", r"साँस.{0,20}बोल नहीं",
    ]),
]

# symptom id in triage_rules.json, rule questions to ask first, reason, patterns
VERIFY: list[tuple[str, list[str], str, list[str]]] = [
    ("breathlessness", ["cannot_speak_full_sentence", "blue_lips"], "Breathing difficulty", [
        r"\bcan'?t breathe\b", r"\bcannot breathe\b", r"\bunable to breathe\b", r"\bshort of breath\b",
        r"\bbreathless", r"\btrouble breathing\b", r"\bdifficulty breathing\b",
        r"\bsaa?ns\b.{0,30}\b(nahi|nahin|nhi|dikkat|takleef|problem|phool)",
        r"साँस", r"सांस",
    ]),
    ("chest_pain", ["difficulty_breathing", "pain_radiating", "fainting"], "Chest pain", [
        r"chest pain", r"pain in (my )?chest", r"\b(seen[ae]|chhati|chhaati)\b.{0,20}\bdard\b",
        r"सीने.{0,15}दर्द", r"छाती.{0,15}दर्द",
    ]),
    ("bleeding", ["does_not_stop_with_pressure", "heavy_blood_loss"], "Heavy bleeding", [
        r"heavy bleeding", r"bleeding (a lot|heavily|won'?t stop|that won'?t stop|not stopping)",
        r"\b(bahut|bohot) (zyada |jyada )?khoon\b", r"khoon.{0,15}(ruk nahi|band nahi)", r"बहुत खून",
    ]),
]

VERIFY_QUESTION = {
    "breathlessness": {
        "hi": "Main samajh raha hoon. Abhi saans ki dikkat halki hai, ya itni zyada hai ki poora sentence bolne mein bhi dikkat ho rahi hai?",
        "en": "I understand. Is the breathing difficulty mild right now, or is it so bad that it's hard to say a full sentence?",
    },
    "chest_pain": {
        "hi": "Main samajh raha hoon. Kya is dard ke saath saans lene mein bhi dikkat ho rahi hai?",
        "en": "I understand. Along with this pain, are you having any difficulty breathing?",
    },
    "bleeding": {
        "hi": "Theek hai. Saaf kapde se zor se dabane par bhi kya khoon ruk nahi raha?",
        "en": "Okay. Is it still bleeding even when you press firmly on it with a clean cloth?",
    },
}

# Answers to the verification question that are clear enough to record
# without the LLM: "bahut zyada" is a yes, "halki hai, bol pa raha hoon" a no.
_SEVERE = re.compile(r"\b(bahut|bohot|zyada|jyada|bilkul|very|really bad|severe|haan|han|yes|nahi bol|bol nahi|can'?t (speak|talk)|not able)\b", re.I)
_MILD = re.compile(r"\b(halki|halka|thodi|thoda|mild|slight|bol pa raha|bol pa rahi|bol sakta|bol sakti|can (speak|talk)|ruk gaya|stopped|no|not really)\b", re.I)

_PAST = re.compile(r"\b(last (year|month|week)|years? ago|months? ago|pichle (saal|mahine|hafte)|saal pehle|"
                   r"mahine pehle|in the past|as a child|bachpan)\b", re.I)

_NEGATED = re.compile(r"\b(no|not|without|never)\b\W*$", re.I)

_IMMEDIATE = [(reason, [re.compile(p, re.I) for p in pats]) for reason, pats in IMMEDIATE]
_VERIFY = [(sid, qs, reason, [re.compile(p, re.I) for p in pats]) for sid, qs, reason, pats in VERIFY]


def scan_red_flags(text: str) -> dict:
    """{"immediate": [reasons], "verify": [{"symptom", "questions", "reason"}]}"""
    out = {"immediate": [], "verify": []}
    if not text:
        return out
    past = bool(_PAST.search(text))
    if not past:
        out["immediate"] = [r for r, pats in _IMMEDIATE if any(p.search(text) for p in pats)]
    for sid, qs, reason, pats in _VERIFY:
        for p in pats:
            m = p.search(text)
            # "no chest pain" / "without breathing trouble" is not a complaint
            if m and not _NEGATED.search(text[max(0, m.start() - 12):m.start()]):
                out["verify"].append({"symptom": sid, "questions": qs, "reason": reason})
                break
    return out


def interpret_verification_answer(text: str):
    """True (severe), False (mild) or None (unclear — let the conversation ask again)."""
    severe, mild = bool(_SEVERE.search(text or "")), bool(_MILD.search(text or ""))
    if severe and not mild:
        return True
    if mild and not severe:
        return False
    # "haan, bahut zyada, poora sentence nahi bol pa raha" has both kinds of words:
    # an explicit intensity word decides it.
    if re.search(r"\b(bahut|bohot|zyada|jyada|bilkul|very|severe|nahi bol|bol nahi)\b", text or "", re.I):
        return True
    return None
