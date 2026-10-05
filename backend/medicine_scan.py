"""
SmartPoli - scan a medicine strip / box with the camera.

The camera photo goes through the same OCR plugin as prescription photos
(ocr_plugin.py); this module only turns the printed text into a short list of
CANDIDATE medicine names for the patient to confirm. It never:

  * adds anything by itself - the patient picks (or types) the name, strength
    and how to take it, and the medicine then goes through the normal
    prescription review/confirm gate like any typed line;
  * guesses a schedule or a dose - packaging says what the product IS, not how
    THIS patient should take it;
  * substitutes a similar-sounding medicine - every candidate shows its match
    score and the exact text it was read from, and 'approximate' matches are
    labelled as such.

Barcodes (EAN/GS1) are deliberately not used: mapping one to a medicine needs
a licensed product database that this project does not have.
"""

import re
from typing import Optional

from rapidfuzz import fuzz, process

from indian_drugs import INDIAN_DRUG_NAMES

MATCH_THRESHOLD = 88          # rapidfuzz ratio (0-100); below this nothing is suggested
MAX_CANDIDATES = 5

_STRENGTH = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|mcg|µg|g|ml|iu)\b", re.I)
_FORMS = (
    ("syrup", re.compile(r"\b(syrup|suspension|oral solution)\b", re.I)),
    ("cap", re.compile(r"\bcapsules?\b", re.I)),
    ("tab", re.compile(r"\btablets?\b", re.I)),
    ("inj", re.compile(r"\binjection\b", re.I)),
)
# Words printed on every box that are never the product name.
_STOP = {
    "tablets", "tablet", "capsules", "capsule", "ip", "bp", "usp", "each", "film", "coated", "uncoated", "contains",
    "store", "below", "batch", "mfg", "exp", "mrp", "price", "rs", "ltd", "pvt", "limited", "manufactured", "marketed",
    "schedule", "prescription", "drug", "only", "keep", "reach", "children", "strip", "strips", "pack", "of", "the", "and",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z][A-Za-z\-]{2,}|\d{2,4}", text)]


def _ngrams(tokens: list[str]) -> list[str]:
    out = [t for t in tokens if not t.isdigit() and t.lower() not in _STOP]
    out += [f"{a} {b}" for a, b in zip(tokens, tokens[1:]) if a.lower() not in _STOP and b.lower() not in _STOP]
    return out


def _form_of(lines: list[str]) -> Optional[str]:
    blob = " ".join(lines)
    for form, rx in _FORMS:
        if rx.search(blob):
            return form
    return None


def candidates_from_lines(ocr_lines: list[dict]) -> dict:
    """ocr_lines: [{"text": str, "confidence": float|None}, ...] from ocr_plugin."""
    texts = [(l.get("text") or "").strip() for l in ocr_lines if (l.get("text") or "").strip()]
    names = sorted(INDIAN_DRUG_NAMES)
    best: dict[str, dict] = {}
    for line in texts:
        for gram in _ngrams(_tokens(line)):
            hit = process.extractOne(gram.lower(), names, scorer=fuzz.ratio)
            if not hit or hit[1] < MATCH_THRESHOLD:
                continue
            name, score = hit[0], hit[1]
            strength = _STRENGTH.search(line) or next((m for m in (_STRENGTH.search(t) for t in texts) if m), None)
            cand = {
                "name": name,
                "match": "exact" if score >= 99.5 else "approximate",
                "score": round(float(score), 1),
                "strength": f"{strength.group(1)}{strength.group(2).lower()}" if strength else None,
                "read_from": line[:120],
            }
            if name not in best or cand["score"] > best[name]["score"]:
                best[name] = cand
    ranked = sorted(best.values(), key=lambda c: (-c["score"], c["name"]))
    # "dolo" and "dolo 650" read from one line are one finding: keep the more specific.
    ranked = [c for c in ranked
              if not any(o is not c and o["read_from"] == c["read_from"] and c["name"] != o["name"]
                         and c["name"] in o["name"] for o in ranked)][:MAX_CANDIDATES]
    form = _form_of(texts)
    for c in ranked:
        c["form"] = form
    return {
        "candidates": ranked,
        "lines_read": [t[:120] for t in texts[:30]],
        "form_guess": form,
        "note": ("Check the name and strength against the box or strip in your hand. SmartPoli only suggests names - "
                 "you choose, and you enter how your doctor told you to take it."),
    }


def build_prescription_line(form: Optional[str], name: str, strength: Optional[str], schedule: str,
                            days: Optional[int], ongoing: bool) -> str:
    """The typed-in prescription line the patient's confirmed choices become,
    so the normal parser/review gate handles it exactly like any other line."""
    name = name.strip()
    parts = [(form or "tab").capitalize(), name]
    if strength:
        number = re.match(r"\s*(\d+(?:\.\d+)?)", strength)
        # "Dolo 650" already carries its strength: do not print "Dolo 650 650mg".
        if not (number and re.search(rf"\b{re.escape(number.group(1))}\s*$", name)):
            parts.append(strength.strip())
    parts.append(schedule.strip())
    if ongoing or not days:
        parts.append("continue")
    else:
        parts.append(f"x{int(days)}d")
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------- vision (Groq) - the primary way to read a box

import base64
import io
import json
import logging
import os

logger = logging.getLogger(__name__)

VISION_PROMPT = (
    "You read the PRINTED text on a photo of a medicine strip, bottle, tube or box. Reply with ONLY a JSON object: "
    '{"found": true|false, "brand_name": string|null, "generic_name": string|null, "strength": string|null, '
    '"form": "tab"|"cap"|"syrup"|"inj"|"other"|null, "text_read": [up to 8 short strings you read]}. '
    "brand_name is the product name as printed (e.g. Dolo 650). generic_name is the active ingredient printed on the pack. "
    "strength looks like 500mg, 5ml or 10mg/5ml. Only report what is actually printed and legible. "
    "Never guess, never suggest a dose, schedule, use or advice. If you cannot read a medicine name, set found to false."
)
_FORM_OK = {"tab", "cap", "syrup", "inj", "other"}


def vision_available() -> bool:
    return bool(os.getenv("GROQ_API_KEY")) and os.getenv("SMARTPOLI_AI_SCAN", "1") != "0"


def _vision_models() -> list:
    # qwen3.8-27b is the vision-capable model on this Groq account (the llama-4 vision models are no longer offered).
    return list(dict.fromkeys([os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")]))


def _prepare_image(image_bytes: bytes) -> str:
    """Shrink a full-size phone photo to a small JPEG data URL (fast upload, well under the API's size limit)."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    im.thumbnail((1400, 1400))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _json_from(text: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_vision_answer(data: Optional[dict]) -> Optional[dict]:
    """Validate what the model said. Returns the same shape as candidates_from_lines, or None if nothing usable."""
    if not data or data.get("found") is False:
        return None

    def clean(v, limit=80):
        return re.sub(r"\s+", " ", v).strip()[:limit] if isinstance(v, str) and v.strip() else None

    name = clean(data.get("brand_name")) or clean(data.get("generic_name"))
    if not name or len(name) < 2 or not re.search(r"[A-Za-z]", name):
        return None
    generic = clean(data.get("generic_name"))
    strength_raw = clean(data.get("strength"), 30)
    sm = _STRENGTH.search(strength_raw or "")
    strength = f"{sm.group(1)}{sm.group(2).lower()}" if sm else None
    form = data.get("form") if data.get("form") in _FORM_OK else None
    read = [s[:120] for s in (data.get("text_read") or []) if isinstance(s, str)][:8]
    return {
        "candidates": [{"name": name, "generic": generic if generic and generic.lower() != name.lower() else None,
                        "strength": strength, "form": form, "match": "read", "score": None,
                        "read_from": ", ".join(read)[:120] or "the packaging"}],
        "lines_read": read,
        "form_guess": form,
        "source": "vision",
        "note": "Check the name and strength against the box or strip in your hand.",
    }


def identify_with_vision(image_bytes: bytes) -> Optional[dict]:
    """Ask a Groq vision model to read the packaging. None = it could not (no key, error, unreadable) - the caller
    then offers manual entry. The photo is sent for this one call and is not stored."""
    if not vision_available():
        return None
    try:
        from groq import Groq
        url = _prepare_image(image_bytes)
        client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0, timeout=30)
    except Exception as e:  # noqa: BLE001
        logger.warning("Vision scan setup failed: %s", type(e).__name__)
        return None
    for model in _vision_models():
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0, max_tokens=300,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": url}}]}])
            return parse_vision_answer(_json_from(resp.choices[0].message.content))
        except Exception as e:  # noqa: BLE001 - try the next model; never raise into the request
            logger.warning("Vision scan with %s failed: %s", model, type(e).__name__)
    return None
