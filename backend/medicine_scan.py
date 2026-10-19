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


_COMPOSITION = re.compile(r"\b(tablets?|capsules?|contains|each|ip|bp|usp|hydrochloride|sodium|gastro|resistant|syrup)\b", re.I)


def candidates_from_lines(ocr_lines: list[dict]) -> dict:
    """ocr_lines: [{"text": str, "confidence": float|None}, ...] - the printed text read off the pack, no AI involved.

    Every word (and pair of words) is matched against the known-medicine list. The brand comes first: it is printed in
    capitals, repeated across the strip, on a line of its own; the ingredient (from the 'Paracetamol Tablets IP 650 mg'
    line) is attached to it as `generic`, and also kept as its own choice."""
    texts = [(l.get("text") or "").strip() for l in ocr_lines if (l.get("text") or "").strip()]
    names = sorted(INDIAN_DRUG_NAMES)
    found: dict[str, dict] = {}
    for line in texts:
        composition_line = bool(_COMPOSITION.search(line))
        for gram in _ngrams(_tokens(line)):
            hit = process.extractOne(gram.lower(), names, scorer=fuzz.ratio)
            if not hit or hit[1] < MATCH_THRESHOLD:
                continue
            name, score = hit[0], float(hit[1])
            f = found.setdefault(name, {"name": name, "score": 0.0, "count": 0, "brand_votes": 0, "lines": set(),
                                        "read_from": line[:120], "strength": None})
            f["count"] += 1
            f["lines"].add(line)
            if gram.isupper() and not composition_line:
                f["brand_votes"] += 1
            if score > f["score"]:
                f["score"], f["read_from"] = score, line[:120]
            strength = _STRENGTH.search(line)
            if strength and not f["strength"]:
                f["strength"] = f"{strength.group(1)}{strength.group(2).lower()}"
    # "dolo" and "dolo 650" read from the same line are one finding: keep the more specific.
    items = [f for f in found.values()
             if not any(o is not f and f["name"] in o["name"] and f["lines"] & o["lines"] for o in found.values())]
    items.sort(key=lambda f: (-(f["brand_votes"] > 0), -f["count"], -f["score"], f["name"]))
    any_strength = next((m for m in (_STRENGTH.search(t) for t in texts) if m), None)
    form = _form_of(texts)
    generic = next((f["name"] for f in items if f["brand_votes"] == 0), None)
    ranked = []
    for f in items[:MAX_CANDIDATES]:
        ranked.append({
            "name": f["name"],
            "generic": generic if generic and f["brand_votes"] and generic != f["name"] else None,
            "match": "exact" if f["score"] >= 99.5 else "approximate",
            "score": round(f["score"], 1),
            "strength": f["strength"] or (f"{any_strength.group(1)}{any_strength.group(2).lower()}" if any_strength else None),
            "read_from": f["read_from"],
            "form": form,
            "known": True,
        })
    return {
        "candidates": ranked,
        "expiry": parse_expiry(" ".join(texts), four_digit_year=True),
        "lines_read": [t[:120] for t in texts[:30]],
        "form_guess": form,
        "note": ("Check the name and strength against the box or strip in your hand. SmartPoli only suggests names - "
                 "you choose, and you enter how your doctor told you to take it."),
    }


def read_pack_text(image_bytes: bytes) -> list[dict]:
    """The printed text on a medicine pack, read with Tesseract - no AI. Tuned for PRINT, unlike the prescription
    reader (which straightens and thresholds for handwriting and turns foil print into noise): a grey, contrast-
    stretched copy is read as a page and as scattered text, and once more slightly turned each way, because a strip
    held in the hand is rarely level. The lines from every pass are pooled. Raises OCRUnavailable without Tesseract."""
    from PIL import Image, ImageOps
    from ocr_plugin import OCRUnavailable
    try:
        import pytesseract
    except ImportError as e:
        raise OCRUnavailable("Tesseract is not installed") from e
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("L")
    side = max(im.size)
    if side > 2000 or side < 1200:                       # text a few dozen pixels tall reads best
        k = 1600 / side
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))))
    im = ImageOps.autocontrast(im, cutoff=1)
    passes = [(im, 3), (im, 11), (im.rotate(7, expand=True, fillcolor=255), 3), (im.rotate(-7, expand=True, fillcolor=255), 3)]
    seen, lines = set(), []
    try:
        for picture, psm in passes:
            for text in pytesseract.image_to_string(picture, config=f"--psm {psm}", timeout=20).splitlines():
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) >= 3 and text not in seen:
                    seen.add(text)
                    lines.append({"text": text, "confidence": None})
    except (pytesseract.TesseractNotFoundError, RuntimeError) as e:
        raise OCRUnavailable(f"Tesseract could not run: {type(e).__name__}") from e
    return lines


def combine(ai: Optional[dict], keywords: Optional[dict]) -> Optional[dict]:
    """One answer from the two readers that ran side by side: the AI's reading first (it read 16 of 16 test strips
    right), then any different medicine the keyword reader found as a second choice; when the AI could not answer
    (no key, rate limit, error) the keyword reading stands on its own. Expiry from whichever read one."""
    ai_c = (ai or {}).get("candidates") or []
    kw_c = (keywords or {}).get("candidates") or []
    if not ai_c and not kw_c:
        return None
    base = dict(ai if ai_c else keywords)
    if ai_c:
        same = {catalogue_match(ai_c[0]["name"]), catalogue_match(ai_c[0].get("generic"))} - {None}
        extra = [c for c in kw_c if catalogue_match(c["name"]) not in same and c["name"] not in same
                 and not (c.get("generic") and c["generic"] in same)]
        base["candidates"] = ai_c[:1] + extra[:2]
        base["source"] = "vision+keywords" if kw_c else "vision"
    else:
        top = kw_c[0]
        base["candidates"] = [top] + [c for c in kw_c[1:] if c["name"] != top.get("generic")][:2]
        base["source"] = "keywords"
    base["expiry"] = (ai or {}).get("expiry") or (keywords or {}).get("expiry")
    base["lines_read"] = (ai or {}).get("lines_read") or (keywords or {}).get("lines_read") or []
    return base


def catalogue_match(name: Optional[str]) -> Optional[str]:
    """The known medicine (Indian brand or generic) a printed name is, or None. Used to tell the person whether the
    name read off the pack is one SmartPoli recognises, so a misread ('Dol0 65') is flagged for checking."""
    if not name or len(name.strip()) < 3:
        return None
    names = sorted(INDIAN_DRUG_NAMES)
    name = re.sub(r"[-_/]+", " ", name)                 # strips print "DOLO-650"; the list says "dolo 650"
    for part in re.split(r"\s*\+\s*", name.lower()):
        hit = process.extractOne(part.strip(), names, scorer=fuzz.ratio)
        if hit and hit[1] >= MATCH_THRESHOLD:
            return hit[0]
        first = part.split()[0] if part.split() else ""
        hit = process.extractOne(first, names, scorer=fuzz.ratio) if len(first) >= 4 else None
        if hit and hit[1] >= 92:
            return hit[0]
    return None


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_EXP_NUM = re.compile(r"(?<!\d)(\d{1,2})\s*[/\-.]\s*(\d{4}|\d{2})(?!\d)")
_EXP_WORD = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*[-/.]?\s*(\d{4}|\d{2})(?!\d)", re.I)
_EXP_HINT = re.compile(r"exp|use\s*before|best\s*before", re.I)


def parse_expiry(text: Optional[str], four_digit_year: bool = False) -> Optional[dict]:
    """'EXP 08/2027', 'Exp. AUG.27', 'Use before 12-2026' -> {"month": 8, "year": 2027, "label": "Aug 2027"}.
    Only a date next to an EXP / Use before word counts when the text has one (the MFG date is printed right beside it)."""
    if not text:
        return None
    text = re.sub(r"(?<=[\s.:/\-])[Oo@](?=\d)|(?<=\d)[Oo@](?=[\s/\-.]|\d)", "0", text)
    hint = _EXP_HINT.search(text)
    scope = text[hint.start():hint.start() + 40] if hint else text
    m = _EXP_WORD.search(scope)
    if m:
        month, year = _MONTHS[m.group(1).lower()[:3]], m.group(2)
    else:
        m = _EXP_NUM.search(scope) if hint else None      # a bare "10.26" is as likely a price as a date
        if not m:
            return None
        month, year = int(m.group(1)), m.group(2)
    if four_digit_year and len(year) != 4:               # keyword reading: "05/2027" cut to "05/20" must not say 2020
        return None
    year = int(year) + (2000 if len(year) == 2 else 0)
    if not (1 <= month <= 12 and 2015 <= year <= 2045):
        return None
    return {"month": month, "year": year, "label": f"{list(_MONTHS)[month - 1].capitalize()} {year}"}


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
import time

logger = logging.getLogger(__name__)

VISION_PROMPT = (
    "You read the PRINTED text on a photo of a medicine pack: usually the BACK of a tablet strip (shiny foil, small "
    "repeated text), or a box, bottle or tube. Reply with ONLY a JSON object: "
    '{"found": true|false, "brand_name": string|null, "generic_name": string|null, "strength": string|null, '
    '"form": "tab"|"cap"|"syrup"|"inj"|"other"|null, "expiry": string|null, "text_read": [up to 8 short strings you read]}. '
    "brand_name is the product name as printed in big letters (e.g. Dolo 650). generic_name is the active ingredient "
    "from the composition line (e.g. 'Each uncoated tablet contains: Paracetamol IP 650 mg' -> Paracetamol); several "
    "ingredients joined with ' + '. strength is the amount from that line (650mg, 5ml, 10mg/5ml). expiry is the date "
    "after EXP / Expiry / Use before, exactly as printed (e.g. 08/2027 or AUG.2027). The strip text repeats - read the "
    "clearest copy. Only report what is actually printed and legible; never guess a letter or digit. Never suggest a "
    "dose, schedule, use or advice. If no medicine name is legible (blurry, glare, not a medicine), set found to false."
)
_FORM_OK = {"tab", "cap", "syrup", "inj", "other"}


def vision_available() -> bool:
    return (bool(os.getenv("GROQ_API_KEY")) or bool(os.getenv("GEMINI_API_KEY"))) and os.getenv("SMARTPOLI_AI_SCAN", "1") != "0"


def _vision_models() -> list:
    # qwen3.8-27b is the vision-capable model on this Groq account (the llama-4 vision models are no longer offered).
    return list(dict.fromkeys([os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")]))


def _prepare_image(image_bytes: bytes) -> str:
    """Shrink a full-size phone photo to a small JPEG data URL. Smaller = fewer image tokens, and Groq's free tier
    allows only ~8,000 tokens a minute per model, so a big photo can use up most of a minute's budget by itself."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    im.thumbnail((1600, 1600))                      # the back of a strip has small print: keep enough detail
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
    if name and name.isupper():
        name = name.title()
    if not name or len(name) < 2 or not re.search(r"[A-Za-z]", name):
        return None
    generic = clean(data.get("generic_name"))
    strength_raw = clean(data.get("strength"), 30)
    sm = _STRENGTH.search(strength_raw or "")
    strength = f"{sm.group(1)}{sm.group(2).lower()}" if sm else None
    form = data.get("form") if data.get("form") in _FORM_OK else None
    read = [s[:120] for s in (data.get("text_read") or []) if isinstance(s, str)][:8]
    known = catalogue_match(name) or catalogue_match(generic)
    return {
        "candidates": [{"name": name, "generic": generic if generic and generic.lower() != name.lower() else None,
                        "strength": strength, "form": form, "match": "read", "score": None, "known": bool(known),
                        "read_from": ", ".join(read)[:120] or "the packaging"}],
        "expiry": parse_expiry("EXP " + clean(data.get("expiry"), 30)) if clean(data.get("expiry"), 30) else parse_expiry(" ".join(read)),
        "lines_read": read,
        "form_guess": form,
        "source": "vision",
        "note": "Check the name and strength against the box or strip in your hand.",
    }


MAX_WAIT_SECONDS = 15         # a rate-limit pause this short is waited out once (Groq's free tier asks for ~11 s when
                              # two scans land in the same minute); longer goes to the next provider


def _retry_after_seconds(exc) -> Optional[float]:
    try:
        resp = getattr(exc, "response", None)
        raw = resp.headers.get("retry-after") if resp is not None else None
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _providers() -> list:
    """(label, client, model) in the order they are tried. A provider without a key simply isn't in the list."""
    out = []
    try:
        if os.getenv("GROQ_API_KEY"):
            from groq import Groq
            client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0, timeout=20)
            out += [("groq", client, m) for m in _vision_models()]
        if os.getenv("GEMINI_API_KEY"):
            from openai import OpenAI
            client = OpenAI(api_key=os.environ["GEMINI_API_KEY"], max_retries=0, timeout=30,
                            base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
            out.append(("gemini", client, os.getenv("GEMINI_VISION_MODEL", "gemini-flash-latest")))
    except Exception as e:  # noqa: BLE001
        logger.warning("Vision provider setup failed: %s", type(e).__name__)
    return out


def identify_with_vision(image_bytes: bytes) -> Optional[dict]:
    """Ask a vision model to read the packaging. None = it could not (no key, errors, unreadable) - the caller then
    offers manual entry. Rate limits are handled: a short pause is waited out once, otherwise the next provider
    (Groq, then Gemini) is tried. The photo is sent for this one call and is not stored."""
    if not vision_available():
        return None
    try:
        url = _prepare_image(image_bytes)
    except Exception as e:  # noqa: BLE001
        logger.warning("Vision scan could not prepare the image: %s", type(e).__name__)
        return None
    messages = [{"role": "user", "content": [{"type": "text", "text": VISION_PROMPT},
                                             {"type": "image_url", "image_url": {"url": url}}]}]
    for label, client, model in _providers():
        for attempt in (1, 2):
            try:
                resp = client.chat.completions.create(model=model, temperature=0, max_tokens=300, messages=messages)
                return parse_vision_answer(_json_from(resp.choices[0].message.content))
            except Exception as e:  # noqa: BLE001 - never raise into the request
                name = type(e).__name__
                wait = _retry_after_seconds(e) if name == "RateLimitError" else None
                if wait is None and name in ("InternalServerError", "APIConnectionError", "APITimeoutError"):
                    wait = 1.0                            # a one-off server hiccup: try the same provider once more
                logger.warning("Vision scan via %s failed: %s%s", label, name, f" (retry in {wait}s)" if wait else "")
                if attempt == 1 and wait is not None and wait <= MAX_WAIT_SECONDS:
                    time.sleep(wait + 0.3)
                    continue
                break                                     # next provider
    return None
