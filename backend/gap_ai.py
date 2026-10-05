"""
SmartPoli - AI-assisted dose gaps, grounded in official drug labels.

Two questions the take-time guard needs answered:

  single_gap(medicine)      how many hours should pass between two doses of the SAME medicine?
  pair_gap(medicine, other) how far apart should two DIFFERENT medicines be taken?

How the answers are produced (and why they can be trusted only so far):

  1. The official US label text is fetched from the government's openFDA API.
  2. An LLM (Groq) reads that text and returns a small JSON answer. When the label does not say,
     it may fall back on standard pharmacology knowledge.
  3. Every answer is graded, never taken at face value:
       basis "label"        the LLM quoted a sentence that really is in the label text AND the quote itself
                            contains the number of hours  -> shown as "from the FDA label"
       basis "ai_estimate"  everything else (no verifiable quote, or the quote does not contain the number)
                            -> shown as "AI estimate - ask your pharmacist", and the patient can override it
  4. Numbers are range-checked, malformed output is discarded, and nothing here ever changes a dose or a
     schedule: it can only make the app say "wait a little longer" when someone taps "Mark taken".
  5. A curated label rule in safety_rules.json always beats an AI answer. AI never loosens one.

Only generic medicine names are sent to the LLM - never a patient name, id, age or any other detail.

Answers (including "no gap needed") are cached in the database, because the free LLM tier rate-limits
quickly and the label does not change from one tap to the next. A failed lookup is NOT cached, and any
failure simply means "no AI answer" - the guard then relies on the prescription's own schedule.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

LABEL_URL = "https://api.fda.gov/drug/label.json"
CACHE_TTL = timedelta(days=180)
LABEL_TIMEOUT = 6.0
LLM_TIMEOUT = 9.0

# Words the patient/pharmacist uses for an ingredient family -> a name openFDA indexes.
KEY_TO_QUERY = {
    "calcium": "calcium carbonate", "iron": "ferrous sulfate", "zinc": "zinc sulfate",
    "magnesium_aluminium_antacid": "aluminum hydroxide", "ppi": "omeprazole", "sucralfate": "sucralfate",
    "sevelamer_lanthanum": "sevelamer",
}

SINGLE_SYSTEM = """You extract dosing-interval facts from official drug-label text for a medication-reminder app.
Reply with ONLY a JSON object:
{"min_hours_between_doses": number or null, "max_doses_per_24h": integer or null,
 "quote": "one sentence copied EXACTLY from the label text that states the interval or daily maximum, or null",
 "confidence": "high"|"medium"|"low"}
Rules: use ONLY the label text provided. If it gives no minimum interval, use the shortest interval it gives between repeat
doses ('every 6 hours' -> 6, 'twice daily' -> 12, 'once daily' -> 24) and quote that sentence. If the text states nothing usable,
you may give a conservative general estimate of the minimum hours between doses for the usual adult regimen, with quote null.
Never give dosing advice. If you are not sure, return nulls."""

PAIR_SYSTEM = """You help a medication-reminder app decide how far apart a patient should take TWO different medicines.
Reply with ONLY a JSON object:
{"separate_hours": number or null, "applies_when": "short plain text or null",
 "quote": "a sentence copied EXACTLY from the label text given that supports this, or null",
 "confidence": "high"|"medium"|"low"}
- separate_hours: the minimum hours to keep between the two medicines, ONLY if taking them close together is known to reduce
  absorption or effect. null if no separation is needed or you are not sure.
- Prefer the label text provided. If the labels are silent you may use well-established pharmacology, with quote null.
- Never invent a number. Never give dosing advice. If unsure: separate_hours null."""


def is_enabled() -> bool:
    return os.getenv("SMARTPOLI_AI_GAPS", "1") != "0" and bool(os.getenv("GROQ_API_KEY"))


# ---------------------------------------------------------------- default network functions (replaceable in tests)

_label_cache: dict = {}


def default_label_fn(generic: str, fields: tuple) -> str:
    key = (generic.lower(), fields)
    if key in _label_cache:
        return _label_cache[key]
    safe = re.sub(r"[^A-Za-z0-9 \-]", " ", generic).strip()
    r = httpx.get(LABEL_URL, params={"search": f'openfda.generic_name:"{safe}"', "limit": 2}, timeout=LABEL_TIMEOUT)
    text = ""
    if r.status_code == 200:
        chunks = []
        for res in r.json().get("results", []):
            for f in fields:
                chunks += res.get(f, []) or []
        text = re.sub(r"\s+", " ", " ".join(chunks))[:7000]
    elif r.status_code != 404:
        r.raise_for_status()
    _label_cache[key] = text
    return text


def default_llm_fn(system: str, user: str) -> dict:
    """Same provider chain as medicine_info (several Groq models, then Gemini): each Groq model has its own
    per-minute token budget, so a rate limit on one does not stop the lookup."""
    import medicine_info
    return medicine_info.default_llm_fn(system, user)


# ---------------------------------------------------------------- validation and grading

def _norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "twelve": 12, "twenty-four": 24,
             "half": 0.5, "one-half": 0.5}


def _num(v, lo: float, hi: float) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None


def _quote_states_hours(quote: str, hours: float) -> bool:
    """True if the quote itself contains `hours` as a number of hours (digits or a number word)."""
    q = _norm(quote)
    for m in re.finditer(r"(\d+(?:\.\d+)?|one-half|twenty-four|one|two|three|four|five|six|eight|twelve|half)[ -]*(?:hours?|hrs?)\b", q):
        tok = m.group(1)
        val = _WORD_NUM.get(tok) if tok in _WORD_NUM else float(tok)
        if val is not None and abs(val - hours) < 0.01:
            return True
    # 'once daily' / 'twice daily' / 'three times' phrases state an interval without the word 'hours'
    phrase = {24.0: ("once daily", "once a day", "once-daily", "one time daily"), 12.0: ("twice daily", "twice a day", "two times daily"),
              8.0: ("three times daily", "three times a day", "3 times daily"), 6.0: ("four times daily", "four times a day")}
    return any(p in q for p in phrase.get(round(hours, 2), ()))


def grade_single(raw: dict, label_text: str) -> dict:
    hours = _num(raw.get("min_hours_between_doses"), 0.5, 48)
    per_day = raw.get("max_doses_per_24h")
    per_day = int(per_day) if isinstance(per_day, (int, float)) and 1 <= per_day <= 24 else None
    quote = raw.get("quote") if isinstance(raw.get("quote"), str) else None
    verified = bool(quote) and _norm(quote) in _norm(label_text)
    if hours is None:
        basis = "none"
    elif verified and _quote_states_hours(quote, hours):
        basis = "label"
    else:
        basis = "ai_estimate"
    return {"min_hours": hours, "max_per_24h": per_day, "quote": quote if verified else None,
            "basis": basis, "confidence": raw.get("confidence") if raw.get("confidence") in ("high", "medium", "low") else "low"}


def grade_pair(raw: dict, label_texts: str) -> dict:
    hours = _num(raw.get("separate_hours"), 0.25, 24)
    quote = raw.get("quote") if isinstance(raw.get("quote"), str) else None
    verified = bool(quote) and _norm(quote) in _norm(label_texts)
    if hours is None:
        basis = "none"
    elif verified and _quote_states_hours(quote, hours):
        basis = "label"
    else:
        basis = "ai_estimate"        # a verified quote that does not contain the number only confirms the interaction exists
    when = raw.get("applies_when") if isinstance(raw.get("applies_when"), str) else None
    return {"hours": hours, "applies_when": (when or "")[:160] or None, "quote": quote if verified else None,
            "basis": basis, "confidence": raw.get("confidence") if raw.get("confidence") in ("high", "medium", "low") else "low"}


# ---------------------------------------------------------------- cache

def _get(db: Session, kind: str, key: str, now: datetime) -> Optional[dict]:
    from db import AIGapCache
    row = db.query(AIGapCache).filter(AIGapCache.kind == kind, AIGapCache.key == key).first()
    if row and now - row.created_at < CACHE_TTL:
        return json.loads(row.payload)
    return None


def _put(db: Session, kind: str, key: str, payload: dict, now: datetime) -> None:
    from db import AIGapCache
    try:
        db.add(AIGapCache(kind=kind, key=key, payload=json.dumps(payload), model=os.getenv("GROQ_MODEL", ""), created_at=now))
        db.commit()
    except IntegrityError:        # another request stored it first: fine
        db.rollback()


# ---------------------------------------------------------------- public API

def canonical_name(name: str, rules: Optional[dict] = None) -> str:
    """A generic name suitable for openFDA / the LLM: the ingredient family when known, else the lower-cased name."""
    import safety_engine
    from interactions import normalize_for_interactions
    rules = rules or safety_engine.load_rules()
    keys = safety_engine.resolve_ingredients(name, rules)
    if len(keys) == 1:
        k = next(iter(keys))
        return KEY_TO_QUERY.get(k, k.replace("_", " "))
    cleaned = re.sub(r"\b(tab|tablet|tablets|cap|capsule|capsules|syrup|inj|sr|xr|er|\d+(\.\d+)?\s*(mg|mcg|g|ml|iu))\b", " ",
                     (name or "").lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return normalize_for_interactions(cleaned) if cleaned else (name or "").strip().lower()


def single_gap(db: Session, name: str, *, allow_network: bool = True, label_fn: Optional[Callable] = None,
               llm_fn: Optional[Callable] = None, now: Optional[datetime] = None) -> Optional[dict]:
    """Graded answer for 'hours between two doses of this one medicine', from cache or (if allowed) a live lookup."""
    now = now or datetime.utcnow()
    generic = canonical_name(name)
    if not generic:
        return None
    cached = _get(db, "single", generic.lower(), now)
    if cached is not None:
        return cached
    if not allow_network or not is_enabled():
        return None
    label_fn, llm_fn = label_fn or default_label_fn, llm_fn or default_llm_fn
    try:
        text = label_fn(generic, ("dosage_and_administration", "spl_patient_package_insert", "indications_and_usage"))
        raw = llm_fn(SINGLE_SYSTEM, f"Ingredient: {generic}\nLabel text:\n{text or '(no label text found)'}")
        graded = grade_single(raw if isinstance(raw, dict) else {}, text)
    except Exception as e:  # noqa: BLE001 - any failure means "no AI answer", never an error for the patient
        logger.warning("single_gap lookup failed for %s: %s", generic, type(e).__name__)
        return None
    graded["generic"] = generic
    _put(db, "single", generic.lower(), graded, now)
    return graded


def pair_gap(db: Session, name_a: str, name_b: str, *, allow_network: bool = True, label_fn: Optional[Callable] = None,
             llm_fn: Optional[Callable] = None, now: Optional[datetime] = None) -> Optional[dict]:
    """Graded answer for 'how far apart should A and B be taken' (symmetric)."""
    now = now or datetime.utcnow()
    a, b = canonical_name(name_a), canonical_name(name_b)
    if not a or not b or a.lower() == b.lower():
        return None
    first, second = sorted((a.lower(), b.lower()))
    key = f"{first}|{second}"
    cached = _get(db, "pair", key, now)
    if cached is not None:
        return cached
    if not allow_network or not is_enabled():
        return None
    label_fn, llm_fn = label_fn or default_label_fn, llm_fn or default_llm_fn
    try:
        ta, tb = label_fn(first, ("drug_interactions",)), label_fn(second, ("drug_interactions",))
        raw = llm_fn(PAIR_SYSTEM, f"Medicine A: {first}\nLabel interaction text for A:\n{ta or '(none)'}\n\n"
                                  f"Medicine B: {second}\nLabel interaction text for B:\n{tb or '(none)'}")
        graded = grade_pair(raw if isinstance(raw, dict) else {}, f"{ta} {tb}")
    except Exception as e:  # noqa: BLE001
        logger.warning("pair_gap lookup failed for %s: %s", key, type(e).__name__)
        return None
    graded["pair"] = [first, second]
    _put(db, "pair", key, graded, now)
    return graded


def warm_patient(patient_id: int, max_calls: int = 40, pause: float = 0.6) -> dict:
    """Background warm-up after a prescription is confirmed: look up the gap for each of the patient's
    scheduled medicines and every pair among them, so the first 'Mark taken' tap is answered from the cache.
    Stops early after repeated failures (e.g. the LLM rate limit); whatever was fetched stays cached."""
    from db import AIGapCache, Medicine, Prescription, SessionLocal
    if not is_enabled():
        return {"skipped": "disabled"}
    db = SessionLocal()
    done = failed = 0
    try:
        names = sorted({(m.name or m.raw_text) for m in db.query(Medicine).join(Prescription)
                        .filter(Prescription.patient_id == patient_id, Medicine.status != "needs_confirmation").all()
                        if (m.name or m.raw_text)})
        jobs = [("single", n) for n in names] + [("pair", (a, b)) for i, a in enumerate(names) for b in names[i + 1:]]
        for kind, arg in jobs[:max_calls]:
            before = db.query(AIGapCache).count()
            res = single_gap(db, arg) if kind == "single" else pair_gap(db, *arg)
            if res is None and db.query(AIGapCache).count() == before:
                failed += 1
                if failed >= 3:
                    break
                time.sleep(pause * 5)
            else:
                done += 1
                time.sleep(pause)
    finally:
        db.close()
    return {"looked_up": done, "failed": failed}
