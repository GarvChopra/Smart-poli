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
