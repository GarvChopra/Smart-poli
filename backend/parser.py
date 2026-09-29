"""
SmartPoli — prescription line parser and confidence gate.

Line of text -> Medicine fields. Extracts form (Tab/Cap/Syp/Inj), name, dose
amount + unit, then hands the whole line to the shorthand engine for the
schedule (shorthand.py already ignores tokens it doesn't recognise, so it
does not need a "remainder" — see shorthand.decode_schedule).

Confidence is computed, never invented:
    name_confidence     = fuzzy match score vs the local Indian drug list
    schedule_confidence = structural parse score from the shorthand engine
    overall              = min(name_confidence, schedule_confidence)

See CLAUDE.md section 8 for the threshold table this file implements.
"""

import re
from typing import Optional

from rapidfuzz import fuzz, process

from indian_drugs import INDIAN_DRUG_NAMES
from shorthand import decode_schedule, parse_slot_code, normalise_code, FREQUENCY_CODES, \
    INTERVAL_CODES, PRN_CODES, STAT_CODES
from i18n import to_plain_language_hi

_DRUG_LIST = sorted(INDIAN_DRUG_NAMES)

FORM_WORDS = {
    'tab', 'tabs', 'tablet', 'tablets',
    'cap', 'caps', 'capsule', 'capsules',
    'syp', 'syrup', 'susp', 'suspension',
    'inj', 'injection',
    'drops', 'drop',
    'oint', 'ointment', 'cream', 'gel',
}

_DOSE_RE = re.compile(r'^(\d+(?:\.\d+)?)(mg|mcg|ml|g|iu|%)$', re.I)

VERIFIED_THRESHOLD = 0.85
REVIEW_THRESHOLD = 0.70


def _looks_like_schedule_token(token: str) -> bool:
    code = normalise_code(token)
    if code in FREQUENCY_CODES or code in INTERVAL_CODES or code in PRN_CODES or code in STAT_CODES:
        return True
    if parse_slot_code(token) is not None:
        return True
    return False


def extract_form_name_dose(raw_text: str) -> dict:
    """Pull form / name / dose_amount / dose_unit off the front of a line."""
    tokens = [t for t in raw_text.split() if t]
    idx = 0
    form = None

    if tokens and tokens[0].strip('.').lower() in FORM_WORDS:
        form = tokens[0].strip('.')
        idx = 1

    dose_amount = None
    dose_unit = None
    stop = len(tokens)

    for i in range(idx, len(tokens)):
        m = _DOSE_RE.match(tokens[i])
        if m:
            dose_amount, dose_unit = m.group(1), m.group(2).lower()
            stop = i
            break
        if _looks_like_schedule_token(tokens[i]):
            stop = i
            break

    name_tokens = tokens[idx:stop]
    name = ' '.join(name_tokens).strip()

    return {
        'form': form,
        'name': name,
        'dose_amount': dose_amount,
        'dose_unit': dose_unit,
    }


def match_drug_name(candidate: str) -> tuple[Optional[str], float]:
    """Fuzzy-match a candidate name against the local Indian drug list.

    Returns (best_match_or_None, confidence_0_to_1). Never invents a name —
    the caller decides what to do with a low score (show raw_text instead).
    """
    if not candidate or not candidate.strip():
        return None, 0.0
    result = process.extractOne(candidate.lower().strip(), _DRUG_LIST, scorer=fuzz.WRatio)
    if result is None:
        return None, 0.0
    matched, score, _ = result
    return matched, round(score / 100, 4)


# Prescription photos include doctor/patient/complaint boilerplate above
# the actual medicines. Fuzzy-matching those lines against the drug list
# correctly scores near zero -- that's not a confidence bug, it's parsing
# a non-medicine line as a medicine in the first place. Deliberately
# conservative: only used on lines with none of a medicine's structural
# signals (checked by the caller below), so a real medicine already
# scored by name/dose/schedule is never reclassified as a header.
_HEADER_KEYWORDS = re.compile(
    r'\b(prescription|patient|doctor|dr\.?|mbbs|md|chief\s*complaints?|diagnosis|'
    r'history|address|signature|hospital|clinic|reg(?:istration)?\.?\s*no|date|'
    r'age|sex|male|female|vitals?|temperature|blood\s*pressure|weight|'
    r'follow[\s-]?up|advice|investigation|referred|sample)\b',
    re.I,
)


def is_likely_header_line(raw_text: str) -> bool:
    """True when a line is almost certainly prescription boilerplate rather
    than a medicine -- used to drop OCR noise before it ever reaches the
    parser/confidence gate. A line with a form word, a dose amount+unit, or
    a schedule token always returns False regardless of wording, so a real
    medicine is never silently dropped just for sharing a word with this
    keyword list."""
    tokens = [t for t in raw_text.split() if t]
    if not tokens:
        return True
    if tokens[0].strip('.').lower() in FORM_WORDS:
        return False
    if any(_DOSE_RE.match(t) for t in tokens):
        return False
    if any(_looks_like_schedule_token(t) for t in tokens):
        return False
    return bool(_HEADER_KEYWORDS.search(raw_text))


def compute_status(confidence: float) -> str:
    if confidence >= VERIFIED_THRESHOLD:
        return 'verified'
    if confidence >= REVIEW_THRESHOLD:
        return 'review'
    return 'needs_confirmation'


def parse_medicine_line(raw_text: str, slot_times: Optional[dict] = None) -> dict:
    """
    Parse one prescription line into everything needed to build a Medicine row.

    Never fabricates a dosage: when confidence lands below REVIEW_THRESHOLD,
    the caller must show raw_text next to the best guess and MUST NOT
    schedule doses from it (enforced again, independently, in scheduler.py —
    a disabled UI button is not a safety control).
    """
    raw_text = (raw_text or '').strip()

    extracted = extract_form_name_dose(raw_text)
    schedule = decode_schedule(raw_text, {'slotTimes': slot_times} if slot_times else None)

    name_match, name_confidence = match_drug_name(extracted['name'])
    schedule_confidence = schedule['confidence']
    overall = min(name_confidence, schedule_confidence)
    status = compute_status(overall)

    return {
        'raw_text': raw_text,
        'name': extracted['name'] or None,
        'normalized_name': name_match,
        'dose_amount': extracted['dose_amount'],
        'dose_unit': extracted['dose_unit'],
        'schedule_code': schedule['scheduleCode'],
        'slots': schedule['slots'],
        'times': schedule['times'],
        'doses': schedule['doses'],
        'food': schedule['food'],
        'duration_days': schedule['durationDays'],
        'ongoing': schedule['ongoing'],
        'is_prn': schedule['prn'],
        'plain_language': schedule['plainLanguage'],
        'plain_language_hi': to_plain_language_hi(schedule),
        'notes': schedule['notes'],
        'confidence': overall,
        'field_confidence': {'name': name_confidence, 'schedule': schedule_confidence},
        'status': status,
    }
