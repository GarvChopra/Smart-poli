"""
SmartPoli - "what is this medicine for?" from public sources.

When someone adds Telma, Dolo or Pan 40, the app looks the medicine up so the Safety center and the Care report can say
WHY it is usually taken, in plain words, instead of showing only a name:

  1. the brand/typed name is resolved to its active ingredient (local catalogue first, the AI second);
  2. the US FDA drug-label database (openFDA - a government source) supplies the official "indications and usage",
     "adverse reactions" and "warnings" text for that ingredient;
  3. an AI model writes a short plain-language summary of ONLY that text (or, if no label is found, from general
     knowledge). The sentence it says the purpose came from is checked against the label text: if it really is in
     the label the answer is graded "label", otherwise "ai_estimate" and the screen says so.

It never gives a dose, a schedule or treatment advice, and "conditions" are labelled as a guess from the medicines,
never a diagnosis. Answers are cached (180 days) in ai_gap_cache (kind "info"); a lookup failure just means no
information is shown - nothing else depends on it. SMARTPOLI_AI_INFO=0 switches it off.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Callable, Optional

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import gap_ai

logger = logging.getLogger(__name__)

LABEL_URL = "https://api.fda.gov/drug/label.json"
CACHE_TTL = timedelta(days=180)
UNKNOWN_TTL = timedelta(days=2)          # "could not find out" is retried soon, not remembered for months
LABEL_TIMEOUT = 6.0
LLM_TIMEOUT = 20.0
# openFDA indexes the US names of a few ingredients that India writes differently.
ALIASES = {"paracetamol": "acetaminophen", "salbutamol": "albuterol", "adrenaline": "epinephrine", "noradrenaline": "norepinephrine",
           "frusemide": "furosemide", "glibenclamide": "glyburide", "rifampicin": "rifampin", "lignocaine": "lidocaine",
           "amoxycillin": "amoxicillin", "pethidine": "meperidine", "cetirizine hydrochloride": "cetirizine",
           "vitamin d3": "cholecalciferol", "vitamin b12": "cyanocobalamin", "methylcobalamin": "cyanocobalamin"}
FIELDS = (("indications_and_usage", 1100), ("adverse_reactions", 900), ("warnings", 700), ("warnings_and_cautions", 700))

SYSTEM = """You write short, plain-language patient information about ONE medicine for a medication-reminder app.
Reply with ONLY a JSON object:
{"generic_name": "active ingredient(s), lowercase",
 "what_for": "what it is used for, plain words, at most 90 characters, e.g. Lowers high blood pressure",
 "conditions": ["up to 3 conditions it treats, plain words, e.g. High blood pressure"],
 "side_effects": ["up to 4 common side effects, plain words"],
 "take_care": ["up to 3 short cautions, e.g. Not for use in pregnancy"],
 "quote": "one sentence copied EXACTLY from the label text that says what the medicine is for, or null"}
Rules: use the label text when it is given; otherwise use well-established general knowledge. Write for a patient, not a doctor.
Most names come from India and are often brand names; work out the active ingredient (for example Telma = telmisartan,
Pan = pantoprazole, Dolo = paracetamol, Azithral = azithromycin, Shelcal = calcium + vitamin D3) and put it in generic_name.
The label text may describe a different product with the same ingredient (for example an antacid label for calcium carbonate,
when the product written is a calcium supplement): describe what the medicine AS WRITTEN is used for, and leave quote null
unless the label sentence truly describes that same use.
Never give a dose, a schedule or treatment advice. If you do not recognise the medicine, set what_for to null."""


def is_enabled() -> bool:
    return os.getenv("SMARTPOLI_AI_INFO", "1") != "0" and (bool(os.getenv("GROQ_API_KEY")) or bool(os.getenv("GEMINI_API_KEY")))


# ---------------------------------------------------------------- network pieces (replaceable in tests)

def default_label_fn(generic: str) -> str:
    """Official label text for an ingredient from openFDA, trimmed to what the summary needs. '' when none is found."""
    safe = re.sub(r"[^A-Za-z0-9 \-]", " ", ALIASES.get(generic.lower(), generic)).strip()
    if not safe:
        return ""
    for search in (f'openfda.generic_name:"{safe}"', f'openfda.brand_name:"{safe}"'):
        r = httpx.get(LABEL_URL, params={"search": search, "limit": 1}, timeout=LABEL_TIMEOUT)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            continue
        chunks = []
        for field, limit in FIELDS:
            text = re.sub(r"\s+", " ", " ".join(results[0].get(field, []) or [])).strip()
            if text:
                chunks.append(text[:limit])
        if chunks:
            return " ".join(chunks)
    return ""


def _json_in(text: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _groq_models() -> list:
    # Each Groq model has its own per-minute token budget, so trying several spreads the load.
    return [m for m in dict.fromkeys([os.getenv("GROQ_MODEL", ""), "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]) if m]


def default_llm_fn(system: str, user: str) -> dict:
    """Groq models in turn, then Gemini; the first usable JSON answer wins. Raises if nothing answered."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last: Optional[Exception] = None
    if os.getenv("GROQ_API_KEY"):
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0, timeout=LLM_TIMEOUT)
        for model in _groq_models():
            try:
                out = client.chat.completions.create(model=model, temperature=0, max_tokens=900, messages=messages)
                data = _json_in(out.choices[0].message.content)
                if data:
                    return data
            except Exception as e:  # noqa: BLE001 - try the next model / provider
                last = e
                logger.warning("medicine info via groq %s failed: %s", model, type(e).__name__)
    if os.getenv("GEMINI_API_KEY"):
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["GEMINI_API_KEY"], max_retries=0, timeout=LLM_TIMEOUT,
                        base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
        try:
            out = client.chat.completions.create(model=os.getenv("GEMINI_INFO_MODEL", "gemini-flash-latest"), temperature=0,
                                                 max_tokens=900, messages=messages)
            data = _json_in(out.choices[0].message.content)
            if data:
                return data
        except Exception as e:  # noqa: BLE001
            last = e
            logger.warning("medicine info via gemini failed: %s", type(e).__name__)
    raise RuntimeError(f"no AI provider answered ({type(last).__name__ if last else 'none configured'})")


# ---------------------------------------------------------------- validation / grading

def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _short_list(v, n: int, limit: int = 60) -> list:
    out = []
    for item in v if isinstance(v, list) else []:
        if isinstance(item, str) and item.strip():
            out.append(re.sub(r"\s+", " ", item).strip()[:limit])
    return out[:n]


def grade(raw: dict, label_text: str, fallback_generic: str) -> Optional[dict]:
    what = raw.get("what_for")
    if not isinstance(what, str) or len(what.strip()) < 4:
        return None
    quote = raw.get("quote") if isinstance(raw.get("quote"), str) else None
    from_label = bool(label_text and quote and len(_norm(quote)) >= 20 and _norm(quote) in _norm(label_text))
    generic = raw.get("generic_name") if isinstance(raw.get("generic_name"), str) else fallback_generic
    return {
        "generic": re.sub(r"\s+", " ", generic).strip().lower()[:60] or fallback_generic,
        "what_for": re.sub(r"\s+", " ", what).strip()[:110],
        "conditions": _short_list(raw.get("conditions"), 3),
        "side_effects": _short_list(raw.get("side_effects"), 4),
        "take_care": _short_list(raw.get("take_care"), 3, 80),
        "source": "label" if from_label else "ai_estimate",
        "quote": quote[:300] if from_label else None,
    }


SOURCE_NOTE = {"label": "From the official US FDA drug label.", "ai_estimate": "AI summary of general medical knowledge - check with your pharmacist."}


# ---------------------------------------------------------------- cache (shares ai_gap_cache, kind "info")

def _get(db: Session, key: str, now: datetime) -> Optional[dict]:
    from db import AIGapCache
    row = db.query(AIGapCache).filter(AIGapCache.kind == "info", AIGapCache.key == key).first()
    if not row:
        return None
    payload = json.loads(row.payload)
    ttl = UNKNOWN_TTL if payload.get("unknown") else CACHE_TTL
    return payload if now - row.created_at < ttl else None


def _put(db: Session, key: str, payload: dict, now: datetime) -> None:
    from db import AIGapCache
    try:
        old = db.query(AIGapCache).filter(AIGapCache.kind == "info", AIGapCache.key == key).first()
        if old:
            db.delete(old)
            db.flush()
        db.add(AIGapCache(kind="info", key=key, payload=json.dumps(payload), model="info", created_at=now))
        db.commit()
    except IntegrityError:
        db.rollback()


# ---------------------------------------------------------------- public API

def info_for(db: Session, name: str, *, allow_network: bool = True, label_fn: Optional[Callable] = None,
             llm_fn: Optional[Callable] = None, now: Optional[datetime] = None) -> Optional[dict]:
    """Plain-language information for one medicine name, from cache or (if allowed) a live lookup. None = nothing known."""
    now = now or datetime.utcnow()
    generic = gap_ai.canonical_name(name)
    if not generic:
        return None
    cached = _get(db, generic.lower(), now)
    if cached is not None:
        return None if cached.get("unknown") else cached
    if not allow_network or not is_enabled():
        return None
    label_fn, llm_fn = label_fn or default_label_fn, llm_fn or default_llm_fn
    try:
        label = label_fn(generic) or ""
        raw = llm_fn(SYSTEM, f"Medicine as written: {name}\nActive ingredient (if known): {generic}\n"
                             f"Label text:\n{label or '(no official label text found)'}")
        graded = grade(raw if isinstance(raw, dict) else {}, label, generic)
        # A brand the catalogue did not know: the AI named the ingredient, so fetch THAT ingredient's official label
        # and answer again from it (a better-grounded answer than general knowledge).
        if not label and isinstance(raw, dict) and isinstance(raw.get("generic_name"), str):
            g2 = re.sub(r"\s+", " ", raw["generic_name"]).strip().lower()
            if g2 and g2 != generic and len(g2) < 60:
                label2 = label_fn(g2) or ""
                if label2:
                    raw2 = llm_fn(SYSTEM, "Medicine as written: " + name + chr(10) + "Active ingredient (if known): " + g2
                                  + chr(10) + "Label text:" + chr(10) + label2)
                    better = grade(raw2 if isinstance(raw2, dict) else {}, label2, g2)
                    graded = better or graded
    except Exception as e:  # noqa: BLE001 - never an error for the patient; just no information
        logger.warning("medicine info failed for %s: %s", generic, type(e).__name__)
        return None
    if graded is None:
        _put(db, generic.lower(), {"unknown": True}, now)
        return None
    graded["name"] = generic
    graded["note"] = SOURCE_NOTE[graded["source"]]
    _put(db, generic.lower(), graded, now)
    return graded


def patient_medicines(db: Session, patient_id: int) -> list:
    """(medicine id, display name) for each distinct confirmed medicine of the patient."""
    from db import Medicine, Prescription
    rows = (db.query(Medicine).join(Prescription, Medicine.prescription_id == Prescription.id)
            .filter(Prescription.patient_id == patient_id, Medicine.status != "needs_confirmation").all())
    seen, out = set(), []
    for m in rows:
        label = (m.name or m.raw_text or "").strip()
        key = label.lower()
        if label and key not in seen:
            seen.add(key)
            out.append((m.id, label))
    return out


def summarize_patient(db: Session, patient_id: int, *, allow_network: bool = False, **kw) -> dict:
    """Information for every medicine of the patient plus the conditions they suggest (a guess, not a diagnosis)."""
    items, conditions, pending = [], {}, []
    for med_id, label in patient_medicines(db, patient_id):
        info = info_for(db, label, allow_network=allow_network, **kw)
        if info is None:
            pending.append(label)
            items.append({"medicine_id": med_id, "name": label, "known": False})
            continue
        items.append({"medicine_id": med_id, "name": label, "known": True, **{k: info[k] for k in
                      ("generic", "what_for", "conditions", "side_effects", "take_care", "source", "note")}})
        for c in info["conditions"]:
            conditions[c.lower()] = conditions.get(c.lower(), {"name": c, "count": 0})
            conditions[c.lower()]["count"] += 1
    ranked = sorted(conditions.values(), key=lambda c: (-c["count"], c["name"]))[:5]
    return {"medicines": items, "conditions": [c["name"] for c in ranked], "pending": pending, "enabled": is_enabled(),
            "disclaimer": "General information from public drug labels. It is a guess from your medicines, not a diagnosis - "
                          "your doctor knows why you take each one."}


def warm_patient(patient_id: int, max_calls: int = 8, pause: float = 1.0) -> int:
    """Background job: look up every medicine of the patient that is not cached yet (bounded, gently paced)."""
    import time
    from db import SessionLocal
    if not is_enabled():
        return 0
    db = SessionLocal()
    done = 0
    try:
        for _, label in patient_medicines(db, patient_id):
            if done >= max_calls:
                break
            if _get(db, (gap_ai.canonical_name(label) or "").lower(), datetime.utcnow()) is None:
                info_for(db, label, allow_network=True)
                done += 1
                time.sleep(pause)
    except Exception:  # noqa: BLE001
        logger.exception("medicine info warm-up failed")
    finally:
        db.close()
    return done
