"""
SmartPoli — Medical shorthand decoder

Deterministic. No LLM, no network, no I/O.
Turns "Tab Paracetamol 650mg 1-0-1 PC x5d" into a schedule a human can read
and a scheduler can execute.

Contract: never invent a dosage. If a token can't be parsed, it goes into
`unparsed` and confidence drops. Callers must refuse to schedule anything
below the confirmation threshold.

Ported from shorthand.js — keep behaviour identical to that file.
"""

import re
from typing import Optional

# ---------------------------------------------------------------- config

DEFAULT_SLOT_TIMES = {
    'morning': '08:30',
    'afternoon': '14:00',
    'evening': '18:00',
    'night': '20:30',
    'bedtime': '22:00',
}

SLOT_ORDER = ['morning', 'afternoon', 'evening', 'night', 'bedtime']

# Latin / clinical abbreviations. Keys are normalised (uppercase, dots stripped).
FREQUENCY_CODES = {
    'OD':    {'slots': ['morning'], 'label': 'once daily'},
    'QD':    {'slots': ['morning'], 'label': 'once daily'},
    'SID':   {'slots': ['morning'], 'label': 'once daily'},
    'OM':    {'slots': ['morning'], 'label': 'once daily in the morning'},
    'QAM':   {'slots': ['morning'], 'label': 'once daily in the morning'},
    'BD':    {'slots': ['morning', 'night'], 'label': 'twice daily'},
    'BID':   {'slots': ['morning', 'night'], 'label': 'twice daily'},
    'TDS':   {'slots': ['morning', 'afternoon', 'night'], 'label': 'three times daily'},
    'TID':   {'slots': ['morning', 'afternoon', 'night'], 'label': 'three times daily'},
    'QID':   {'slots': ['morning', 'afternoon', 'evening', 'night'], 'label': 'four times daily'},
    'QDS':   {'slots': ['morning', 'afternoon', 'evening', 'night'], 'label': 'four times daily'},
    'HS':    {'slots': ['bedtime'], 'label': 'at bedtime'},
    'ON':    {'slots': ['bedtime'], 'label': 'at bedtime'},
    'NOCTE': {'slots': ['bedtime'], 'label': 'at bedtime'},
}

# Fixed-interval codes: explicit clock times, ignore named slots.
INTERVAL_CODES = {
    'Q4H':  {'times': ['06:00', '10:00', '14:00', '18:00', '22:00', '02:00'], 'label': 'every 4 hours'},
    'Q6H':  {'times': ['06:00', '12:00', '18:00', '00:00'], 'label': 'every 6 hours'},
    'Q8H':  {'times': ['06:00', '14:00', '22:00'], 'label': 'every 8 hours'},
    'Q12H': {'times': ['08:00', '20:00'], 'label': 'every 12 hours'},
}

# As-needed. These generate ZERO scheduled doses — logging one is a bug.
PRN_CODES = ['SOS', 'PRN']

STAT_CODES = ['STAT']

FOOD_CODES = {
    'AC': 'before',
    'PC': 'after',
}

FOOD_PHRASES = [
    (re.compile(r'\bafter\s+(meals?|food|breakfast|lunch|dinner)\b', re.I), 'after'),
    (re.compile(r'\bbefore\s+(meals?|food|breakfast|lunch|dinner)\b', re.I), 'before'),
    (re.compile(r'\bempty\s+stomach\b', re.I), 'before'),
    (re.compile(r'\bwith\s+(meals?|food)\b', re.I), 'after'),
]

ONGOING_RE = re.compile(r'\b(continue|continuous|ongoing|regular|lifelong|as\s+directed)\b', re.I)

# ---------------------------------------------------------------- helpers

FRACTIONS = {'½': 0.5, '¼': 0.25, '¾': 0.75, '⅓': 1 / 3, '⅔': 2 / 3}

_MIXED_FRACTION_RE = re.compile(r'^(\d+)([½¼¾⅓⅔])$')
_WRITTEN_FRACTION_RE = re.compile(r'^(\d+)/(\d+)$')
_PLAIN_NUMBER_RE = re.compile(r'^\d+(\.\d+)?$')
_STRIP_PUNCT_RE = re.compile(r'[,;:)(]')


def parse_quantity(raw) -> Optional[float]:
    """Parse one slot quantity. Returns None if unparseable — never guesses."""
    s = str(raw).strip()
    if s == '':
        return None
    if s in FRACTIONS:
        return FRACTIONS[s]

    mixed = _MIXED_FRACTION_RE.match(s)
    if mixed:
        return int(mixed.group(1)) + FRACTIONS[mixed.group(2)]

    frac = _WRITTEN_FRACTION_RE.match(s)
    if frac:
        d = int(frac.group(2))
        return None if d == 0 else int(frac.group(1)) / d

    if _PLAIN_NUMBER_RE.match(s):
        return float(s)
    return None


def normalise_code(token: str) -> str:
    return _STRIP_PUNCT_RE.sub('', token.replace('.', '')).upper()


def fmt_qty(n: float) -> str:
    if n == 0.5:
        return '½'
    if n == 0.25:
        return '¼'
    if n == 0.75:
        return '¾'
    if n == int(n):
        return str(int(n))
    return str(round(n, 2))


def unit_word(n: float) -> str:
    return 'tablet' if n == 1 else 'tablets'


# ---------------------------------------------------------------- slot codes

def parse_slot_code(token: str) -> Optional[dict]:
    """
    Detect and parse N-N-N (or N-N-N-N) notation.
    3 parts -> morning/afternoon/night.  4 parts -> morning/afternoon/evening/night.
    A single unparseable part invalidates the whole code. We do not partially guess.
    """
    cleaned = _STRIP_PUNCT_RE.sub('', token)
    if '-' not in cleaned:
        return None

    parts = cleaned.split('-')
    if len(parts) < 3 or len(parts) > 4:
        return None

    # Must look like a slot code at all: every part non-empty and not alphabetic.
    if any(p == '' or re.search(r'[a-zA-Z]', p) for p in parts):
        return None

    quantities = [parse_quantity(p) for p in parts]
    if any(q is None for q in quantities):
        return {'malformed': True, 'raw': cleaned}

    names = ['morning', 'afternoon', 'night'] if len(parts) == 3 \
        else ['morning', 'afternoon', 'evening', 'night']

    active = [{'slot': name, 'qty': quantities[i]} for i, name in enumerate(names) if quantities[i] > 0]

    return {
        'malformed': False,
        'raw': cleaned,
        'quantities': quantities,
        'names': names,
        'active': active,
    }


# ---------------------------------------------------------------- duration

def parse_duration(text: str, units_per_day: float) -> Optional[dict]:
    """
    Duration in days. Returns {days, ongoing, matched, ...} or None.
    `unitsTotal` (e.g. "10 tabs") needs dosesPerDay to resolve, so it's handled
    here directly using the units-per-day figure already computed by the caller.
    """
    if ONGOING_RE.search(text):
        return {'days': None, 'ongoing': True, 'matched': 'continue'}

    m = re.search(r'\bx\s*(\d+)\s*(d|day|days)\b', text, re.I)
    if m:
        return {'days': int(m.group(1)), 'ongoing': False, 'matched': m.group(0)}

    # "5/7" = 5 days, "2/52" = 2 weeks, "3/12" = 3 months (UK shorthand)
    m = re.search(r'\b(\d+)\s*/\s*(7|52|12)\b', text)
    if m:
        n = int(m.group(1))
        denom = m.group(2)
        days = n if denom == '7' else (n * 7 if denom == '52' else n * 30)
        return {'days': days, 'ongoing': False, 'matched': m.group(0)}

    m = re.search(r'\b(\d+)\s*(days?|dys?)\b', text, re.I)
    if m:
        return {'days': int(m.group(1)), 'ongoing': False, 'matched': m.group(0)}

    m = re.search(r'\b(\d+)\s*(weeks?|wks?)\b', text, re.I)
    if m:
        return {'days': int(m.group(1)) * 7, 'ongoing': False, 'matched': m.group(0)}

    m = re.search(r'\b(\d+)\s*(months?|mos?)\b', text, re.I)
    if m:
        return {'days': int(m.group(1)) * 30, 'ongoing': False, 'matched': m.group(0)}

    m = re.search(r'\b(\d+)\s*(tabs?|tablets?|caps?|capsules?)\b', text, re.I)
    if m and units_per_day > 0:
        total = int(m.group(1))
        exact = total / units_per_day
        days = int(exact)  # floor for positive numbers
        return {
            'days': days if days > 0 else None,
            'ongoing': False,
            'matched': m.group(0),
            'derivedFromUnits': True,
            'remainder': round(exact - days, 3),
        }

    return None


# ---------------------------------------------------------------- main

def shift_minutes(hhmm: Optional[str], delta: int) -> Optional[str]:
    if not hhmm:
        return hhmm
    h, m = (int(x) for x in hhmm.split(':'))
    total = (h * 60 + m + delta + 1440) % 1440
    return f'{total // 60:02d}:{total % 60:02d}'


SLOT_LABEL = {
    'morning': 'Morning',
    'afternoon': 'Afternoon',
    'evening': 'Evening',
    'night': 'Night',
    'bedtime': 'Bedtime',
}


def decode_schedule(text: str, options: Optional[dict] = None) -> dict:
    """
    Decode a prescription instruction.

    text: Raw instruction or whole prescription line.
    options: optional dict with 'slotTimes' overriding default clock times per slot.
    Returns a decoded schedule dict with a computed structural confidence.
    """
    options = options or {}
    slot_times = {**DEFAULT_SLOT_TIMES, **(options.get('slotTimes') or {})}
    source = str(text or '')

    result = {
        'input': source,
        'scheduleCode': None,
        'frequencyLabel': None,
        'dosesPerDay': 0,
        'unitsPerDay': 0,
        'slots': [],
        'times': [],
        'doses': [],  # [{slot, time, qty}]
        'food': 'any',
        'durationDays': None,
        'ongoing': False,
        'prn': False,
        'stat': False,
        'plainLanguage': '',
        'confidence': 0,
        'unparsed': [],
        'notes': [],
    }

    if not source.strip():
        result['notes'].append('Empty instruction.')
        return result

    tokens = [t for t in re.split(r'\s+', source) if t]
    schedules_found = []

    # --- pass 1: schedule tokens -------------------------------------------
    for token in tokens:
        code = normalise_code(token)

        if code in PRN_CODES:
            schedules_found.append({'kind': 'prn', 'token': token})
            continue
        if code in STAT_CODES:
            schedules_found.append({'kind': 'stat', 'token': token})
            continue
        if code in INTERVAL_CODES:
            schedules_found.append({'kind': 'interval', 'token': token, 'code': code})
            continue
        if code in FREQUENCY_CODES:
            schedules_found.append({'kind': 'frequency', 'token': token, 'code': code})
            continue

        slot = parse_slot_code(token)
        if slot:
            if slot['malformed']:
                result['unparsed'].append(token)
                result['notes'].append(f'Could not read dose pattern "{token}".')
            else:
                schedules_found.append({'kind': 'slots', 'token': token, 'slot': slot})

    # --- pass 2: food -------------------------------------------------------
    for token in tokens:
        code = normalise_code(token)
        if code in FOOD_CODES:
            result['food'] = FOOD_CODES[code]
            break
    if result['food'] == 'any':
        for regex, value in FOOD_PHRASES:
            if regex.search(source):
                result['food'] = value
                break

    # --- resolve schedule ---------------------------------------------------
    primary = schedules_found[0] if schedules_found else None

    if not primary:
        result['confidence'] = 0
        if len(result['unparsed']) == 0:
            result['notes'].append('No dosing frequency found.')
        result['plainLanguage'] = 'Dosing instructions could not be read. Please confirm with your prescription.'
        return result

    if len(schedules_found) > 1:
        kinds = {s['token'] for s in schedules_found}
        if len(kinds) > 1:
            result['notes'].append(
                f'Conflicting dosing instructions: {", ".join(kinds)}. Using "{primary["token"]}".'
            )

    if primary['kind'] == 'prn':
        result['scheduleCode'] = normalise_code(primary['token'])
        result['frequencyLabel'] = 'as needed'
        result['prn'] = True
        # Deliberately no doses, no times. PRN must not enter the scheduler.
    elif primary['kind'] == 'stat':
        result['scheduleCode'] = 'STAT'
        result['frequencyLabel'] = 'immediately, once'
        result['stat'] = True
        result['dosesPerDay'] = 1
        result['unitsPerDay'] = 1
        result['doses'] = [{'slot': 'now', 'time': None, 'qty': 1}]
    elif primary['kind'] == 'interval':
        spec = INTERVAL_CODES[primary['code']]
        result['scheduleCode'] = primary['code']
        result['frequencyLabel'] = spec['label']
        result['times'] = list(spec['times'])
        result['dosesPerDay'] = len(spec['times'])
        result['unitsPerDay'] = len(spec['times'])
        result['doses'] = [{'slot': None, 'time': t, 'qty': 1} for t in spec['times']]
    elif primary['kind'] == 'frequency':
        spec = FREQUENCY_CODES[primary['code']]
        result['scheduleCode'] = primary['code']
        result['frequencyLabel'] = spec['label']
        result['slots'] = list(spec['slots'])
        result['doses'] = [{'slot': s, 'time': slot_times[s], 'qty': 1} for s in spec['slots']]
        result['times'] = [d['time'] for d in result['doses']]
        result['dosesPerDay'] = len(result['doses'])
        result['unitsPerDay'] = len(result['doses'])
    elif primary['kind'] == 'slots':
        slot = primary['slot']
        result['scheduleCode'] = slot['raw']
        result['slots'] = [a['slot'] for a in slot['active']]
        result['doses'] = [
            {'slot': a['slot'], 'time': slot_times[a['slot']], 'qty': a['qty']}
            for a in slot['active']
        ]
        result['times'] = [d['time'] for d in result['doses']]
        result['dosesPerDay'] = len(result['doses'])
        result['unitsPerDay'] = sum(a['qty'] for a in slot['active'])
        result['frequencyLabel'] = (
            'no doses scheduled' if result['dosesPerDay'] == 0 else f'{result["dosesPerDay"]} time(s) daily'
        )
        if result['dosesPerDay'] == 0:
            result['notes'].append('Dose pattern specifies zero tablets at every time.')

    # Food shifts a "before meals" dose 30 minutes earlier.
    if result['food'] == 'before':
        result['doses'] = [{**d, 'time': shift_minutes(d['time'], -30)} for d in result['doses']]
        result['times'] = [d['time'] for d in result['doses']]

    # --- duration -----------------------------------------------------------
    duration = parse_duration(source, result['unitsPerDay'])
    if duration:
        result['durationDays'] = duration['days']
        result['ongoing'] = duration['ongoing']
        if duration.get('derivedFromUnits'):
            note = f'Duration derived from total quantity ({duration["matched"]}).'
            if duration.get('remainder', 0) > 0:
                note += ' Quantity does not divide evenly — confirm.'
            result['notes'].append(note)
        if duration['ongoing']:
            result['notes'].append('Ongoing treatment — no end date given.')

    # --- confidence ---------------------------------------------------------
    # Structural only: did the rules recognise what they read?
    confidence = 1.0
    if len(result['unparsed']) > 0:
        confidence -= 0.35 * len(result['unparsed'])
    if len(schedules_found) > 1 and len({s['token'] for s in schedules_found}) > 1:
        confidence -= 0.4
    if not result['prn'] and result['dosesPerDay'] == 0:
        confidence -= 0.5
    if duration and duration.get('derivedFromUnits') and duration.get('remainder', 0) > 0:
        confidence -= 0.1
    result['confidence'] = max(0.0, min(1.0, round(confidence, 2)))

    result['plainLanguage'] = to_plain_language(result)
    return result


def to_plain_language(r: dict) -> str:
    lines = []

    if r['prn']:
        lines.append('Take only when needed.')
        lines.append('No fixed times — this medicine is not added to your daily schedule.')
    elif r['stat']:
        lines.append('Take one dose now, once only.')
    elif r['scheduleCode'] and re.match(r'^\d', r['scheduleCode']) and '-' in r['scheduleCode']:
        # Slot notation: show every slot including the zeros, as PS 4 asks.
        parsed = parse_slot_code(r['scheduleCode'])
        for i, name in enumerate(parsed['names']):
            qty = parsed['quantities'][i]
            lines.append(f'{SLOT_LABEL[name]} — {fmt_qty(qty)} {unit_word(qty)}')
    elif r['times'] and not r['slots']:
        lines.append(f'Take {r["frequencyLabel"]} — at {", ".join(r["times"])}.')
    else:
        lines.append(f'Take {r["frequencyLabel"]}.')
        for d in r['doses']:
            lines.append(f'{SLOT_LABEL[d["slot"]]} — {fmt_qty(d["qty"])} {unit_word(d["qty"])} at {d["time"]}')

    if r['food'] == 'after':
        lines.append('Take after meals.')
    elif r['food'] == 'before':
        lines.append('Take before meals, on an empty stomach.')

    if r['ongoing']:
        lines.append('Continue until your doctor tells you to stop.')
    elif r['durationDays']:
        lines.append(f'For {r["durationDays"]} day{"" if r["durationDays"] == 1 else "s"}.')

    return '\n'.join(lines)
