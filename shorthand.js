/**
 * SmartPoli — Medical shorthand decoder
 *
 * Deterministic. No LLM, no network, no I/O.
 * Turns "Tab Paracetamol 650mg 1-0-1 PC x5d" into a schedule a human can read
 * and a scheduler can execute.
 *
 * Contract: never invent a dosage. If a token can't be parsed, it goes into
 * `unparsed` and confidence drops. Callers must refuse to schedule anything
 * below the confirmation threshold.
 */

// ---------------------------------------------------------------- config

const DEFAULT_SLOT_TIMES = {
  morning: '08:30',
  afternoon: '14:00',
  evening: '18:00',
  night: '20:30',
  bedtime: '22:00',
};

const SLOT_ORDER = ['morning', 'afternoon', 'evening', 'night', 'bedtime'];

// Latin / clinical abbreviations. Keys are normalised (uppercase, dots stripped).
const FREQUENCY_CODES = {
  OD:   { slots: ['morning'], label: 'once daily' },
  QD:   { slots: ['morning'], label: 'once daily' },
  SID:  { slots: ['morning'], label: 'once daily' },
  OM:   { slots: ['morning'], label: 'once daily in the morning' },
  QAM:  { slots: ['morning'], label: 'once daily in the morning' },
  BD:   { slots: ['morning', 'night'], label: 'twice daily' },
  BID:  { slots: ['morning', 'night'], label: 'twice daily' },
  TDS:  { slots: ['morning', 'afternoon', 'night'], label: 'three times daily' },
  TID:  { slots: ['morning', 'afternoon', 'night'], label: 'three times daily' },
  QID:  { slots: ['morning', 'afternoon', 'evening', 'night'], label: 'four times daily' },
  QDS:  { slots: ['morning', 'afternoon', 'evening', 'night'], label: 'four times daily' },
  HS:   { slots: ['bedtime'], label: 'at bedtime' },
  ON:   { slots: ['bedtime'], label: 'at bedtime' },
  NOCTE:{ slots: ['bedtime'], label: 'at bedtime' },
};

// Fixed-interval codes: explicit clock times, ignore named slots.
const INTERVAL_CODES = {
  Q4H:  { times: ['06:00', '10:00', '14:00', '18:00', '22:00', '02:00'], label: 'every 4 hours' },
  Q6H:  { times: ['06:00', '12:00', '18:00', '00:00'], label: 'every 6 hours' },
  Q8H:  { times: ['06:00', '14:00', '22:00'], label: 'every 8 hours' },
  Q12H: { times: ['08:00', '20:00'], label: 'every 12 hours' },
};

// As-needed. These generate ZERO scheduled doses — logging one is a bug.
const PRN_CODES = ['SOS', 'PRN'];

const STAT_CODES = ['STAT'];

const FOOD_CODES = {
  AC: 'before',
  PC: 'after',
};

const FOOD_PHRASES = [
  { re: /\bafter\s+(meals?|food|breakfast|lunch|dinner)\b/i, value: 'after' },
  { re: /\bbefore\s+(meals?|food|breakfast|lunch|dinner)\b/i, value: 'before' },
  { re: /\bempty\s+stomach\b/i, value: 'before' },
  { re: /\bwith\s+(meals?|food)\b/i, value: 'after' },
];

const ONGOING_RE = /\b(continue|continuous|ongoing|regular|lifelong|as\s+directed)\b/i;

// ---------------------------------------------------------------- helpers

const FRACTIONS = { '½': 0.5, '¼': 0.25, '¾': 0.75, '⅓': 1 / 3, '⅔': 2 / 3 };

/** Parse one slot quantity. Returns null if unparseable — never guesses. */
function parseQuantity(raw) {
  const s = String(raw).trim();
  if (s === '') return null;
  if (FRACTIONS[s] !== undefined) return FRACTIONS[s];

  // mixed fraction glyph, e.g. "1½"
  const mixed = s.match(/^(\d+)([½¼¾⅓⅔])$/);
  if (mixed) return Number(mixed[1]) + FRACTIONS[mixed[2]];

  // written fraction, e.g. "1/2"
  const frac = s.match(/^(\d+)\/(\d+)$/);
  if (frac) {
    const d = Number(frac[2]);
    return d === 0 ? null : Number(frac[1]) / d;
  }

  if (/^\d+(\.\d+)?$/.test(s)) return Number(s);
  return null;
}

function normaliseCode(token) {
  return token.replace(/\./g, '').replace(/[,;:)(]/g, '').toUpperCase();
}

function fmtQty(n) {
  if (n === 0.5) return '½';
  if (n === 0.25) return '¼';
  if (n === 0.75) return '¾';
  return Number.isInteger(n) ? String(n) : String(Number(n.toFixed(2)));
}

function unitWord(n) {
  return n === 1 ? 'tablet' : 'tablets';
}

// ---------------------------------------------------------------- slot codes

/**
 * Detect and parse N-N-N (or N-N-N-N) notation.
 * 3 parts -> morning/afternoon/night.  4 parts -> morning/afternoon/evening/night.
 * A single unparseable part invalidates the whole code. We do not partially guess.
 */
function parseSlotCode(token) {
  const cleaned = token.replace(/[,;:)(]/g, '');
  if (!cleaned.includes('-')) return null;

  const parts = cleaned.split('-');
  if (parts.length < 3 || parts.length > 4) return null;

  // Must look like a slot code at all: every part non-empty and not alphabetic.
  if (parts.some((p) => p === '' || /[a-z]/i.test(p))) return null;

  const quantities = parts.map(parseQuantity);
  if (quantities.some((q) => q === null)) {
    return { malformed: true, raw: cleaned };
  }

  const names = parts.length === 3
    ? ['morning', 'afternoon', 'night']
    : ['morning', 'afternoon', 'evening', 'night'];

  const active = [];
  names.forEach((name, i) => {
    if (quantities[i] > 0) active.push({ slot: name, qty: quantities[i] });
  });

  return {
    malformed: false,
    raw: cleaned,
    quantities,
    names,
    active,
  };
}

// ---------------------------------------------------------------- duration

/**
 * Duration in days. Returns { days, ongoing, matched } or null.
 * `unitsTotal` (e.g. "10 tabs") needs dosesPerDay to resolve, so it's returned
 * as a pending unit count for the caller stage.
 */
function parseDuration(text, unitsPerDay) {
  if (ONGOING_RE.test(text)) {
    return { days: null, ongoing: true, matched: 'continue' };
  }

  let m;

  // "x5d", "x 5 d", "x5days"
  m = text.match(/\bx\s*(\d+)\s*(d|day|days)\b/i);
  if (m) return { days: Number(m[1]), ongoing: false, matched: m[0] };

  // "5/7" = 5 days, "2/52" = 2 weeks, "3/12" = 3 months (UK shorthand)
  m = text.match(/\b(\d+)\s*\/\s*(7|52|12)\b/);
  if (m) {
    const n = Number(m[1]);
    const denom = m[2];
    const days = denom === '7' ? n : denom === '52' ? n * 7 : n * 30;
    return { days, ongoing: false, matched: m[0] };
  }

  // "5 days", "5days"
  m = text.match(/\b(\d+)\s*(days?|dys?)\b/i);
  if (m) return { days: Number(m[1]), ongoing: false, matched: m[0] };

  // "2 weeks"
  m = text.match(/\b(\d+)\s*(weeks?|wks?)\b/i);
  if (m) return { days: Number(m[1]) * 7, ongoing: false, matched: m[0] };

  // "1 month"
  m = text.match(/\b(\d+)\s*(months?|mos?)\b/i);
  if (m) return { days: Number(m[1]) * 30, ongoing: false, matched: m[0] };

  // "10 tabs" / "15 capsules" — derive days from units per day
  m = text.match(/\b(\d+)\s*(tabs?|tablets?|caps?|capsules?)\b/i);
  if (m && unitsPerDay > 0) {
    const total = Number(m[1]);
    const exact = total / unitsPerDay;
    const days = Math.floor(exact);
    return {
      days: days > 0 ? days : null,
      ongoing: false,
      matched: m[0],
      derivedFromUnits: true,
      remainder: Number((exact - days).toFixed(3)),
    };
  }

  return null;
}

// ---------------------------------------------------------------- main

/**
 * Decode a prescription instruction.
 *
 * @param {string} text  Raw instruction or whole prescription line.
 * @param {object} [options]
 * @param {object} [options.slotTimes]  Override default clock times per slot.
 * @returns {object} decoded schedule with a computed structural confidence.
 */
function decodeSchedule(text, options = {}) {
  const slotTimes = { ...DEFAULT_SLOT_TIMES, ...(options.slotTimes || {}) };
  const source = String(text || '');

  const result = {
    input: source,
    scheduleCode: null,
    frequencyLabel: null,
    dosesPerDay: 0,
    unitsPerDay: 0,
    slots: [],
    times: [],
    doses: [],          // [{ slot, time, qty }]
    food: 'any',
    durationDays: null,
    ongoing: false,
    prn: false,
    stat: false,
    plainLanguage: '',
    confidence: 0,
    unparsed: [],
    notes: [],
  };

  if (!source.trim()) {
    result.notes.push('Empty instruction.');
    return result;
  }

  const tokens = source.split(/\s+/).filter(Boolean);
  const schedulesFound = [];

  // --- pass 1: schedule tokens -------------------------------------------
  for (const token of tokens) {
    const code = normaliseCode(token);

    if (PRN_CODES.includes(code)) {
      schedulesFound.push({ kind: 'prn', token });
      continue;
    }
    if (STAT_CODES.includes(code)) {
      schedulesFound.push({ kind: 'stat', token });
      continue;
    }
    if (INTERVAL_CODES[code]) {
      schedulesFound.push({ kind: 'interval', token, code });
      continue;
    }
    if (FREQUENCY_CODES[code]) {
      schedulesFound.push({ kind: 'frequency', token, code });
      continue;
    }

    const slot = parseSlotCode(token);
    if (slot) {
      if (slot.malformed) {
        result.unparsed.push(token);
        result.notes.push(`Could not read dose pattern "${token}".`);
      } else {
        schedulesFound.push({ kind: 'slots', token, slot });
      }
    }
  }

  // --- pass 2: food -------------------------------------------------------
  for (const token of tokens) {
    const code = normaliseCode(token);
    if (FOOD_CODES[code]) {
      result.food = FOOD_CODES[code];
      break;
    }
  }
  if (result.food === 'any') {
    for (const { re, value } of FOOD_PHRASES) {
      if (re.test(source)) {
        result.food = value;
        break;
      }
    }
  }

  // --- resolve schedule ---------------------------------------------------
  const primary = schedulesFound[0] || null;

  if (!primary) {
    result.confidence = 0;
    if (result.unparsed.length === 0) {
      result.notes.push('No dosing frequency found.');
    }
    result.plainLanguage = 'Dosing instructions could not be read. Please confirm with your prescription.';
    return result;
  }

  if (schedulesFound.length > 1) {
    const kinds = new Set(schedulesFound.map((s) => s.token));
    if (kinds.size > 1) {
      result.notes.push(
        `Conflicting dosing instructions: ${[...kinds].join(', ')}. Using "${primary.token}".`
      );
    }
  }

  if (primary.kind === 'prn') {
    result.scheduleCode = normaliseCode(primary.token);
    result.frequencyLabel = 'as needed';
    result.prn = true;
    // Deliberately no doses, no times. PRN must not enter the scheduler.
  } else if (primary.kind === 'stat') {
    result.scheduleCode = 'STAT';
    result.frequencyLabel = 'immediately, once';
    result.stat = true;
    result.dosesPerDay = 1;
    result.unitsPerDay = 1;
    result.doses = [{ slot: 'now', time: null, qty: 1 }];
  } else if (primary.kind === 'interval') {
    const spec = INTERVAL_CODES[primary.code];
    result.scheduleCode = primary.code;
    result.frequencyLabel = spec.label;
    result.times = [...spec.times];
    result.dosesPerDay = spec.times.length;
    result.unitsPerDay = spec.times.length;
    result.doses = spec.times.map((t) => ({ slot: null, time: t, qty: 1 }));
  } else if (primary.kind === 'frequency') {
    const spec = FREQUENCY_CODES[primary.code];
    result.scheduleCode = primary.code;
    result.frequencyLabel = spec.label;
    result.slots = [...spec.slots];
    result.doses = spec.slots.map((s) => ({ slot: s, time: slotTimes[s], qty: 1 }));
    result.times = result.doses.map((d) => d.time);
    result.dosesPerDay = result.doses.length;
    result.unitsPerDay = result.doses.length;
  } else if (primary.kind === 'slots') {
    const { slot } = primary;
    result.scheduleCode = slot.raw;
    result.slots = slot.active.map((a) => a.slot);
    result.doses = slot.active.map((a) => ({
      slot: a.slot,
      time: slotTimes[a.slot],
      qty: a.qty,
    }));
    result.times = result.doses.map((d) => d.time);
    result.dosesPerDay = result.doses.length;
    result.unitsPerDay = slot.active.reduce((sum, a) => sum + a.qty, 0);
    result.frequencyLabel =
      result.dosesPerDay === 0 ? 'no doses scheduled' : `${result.dosesPerDay} time(s) daily`;

    if (result.dosesPerDay === 0) {
      result.notes.push('Dose pattern specifies zero tablets at every time.');
    }
  }

  // Food shifts a "before meals" dose 30 minutes earlier.
  if (result.food === 'before') {
    result.doses = result.doses.map((d) => ({ ...d, time: shiftMinutes(d.time, -30) }));
    result.times = result.doses.map((d) => d.time);
  }

  // --- duration -----------------------------------------------------------
  const duration = parseDuration(source, result.unitsPerDay);
  if (duration) {
    result.durationDays = duration.days;
    result.ongoing = duration.ongoing;
    if (duration.derivedFromUnits) {
      result.notes.push(
        `Duration derived from total quantity (${duration.matched}).` +
          (duration.remainder > 0 ? ' Quantity does not divide evenly — confirm.' : '')
      );
    }
    if (duration.ongoing) {
      result.notes.push('Ongoing treatment — no end date given.');
    }
  }

  // --- confidence ---------------------------------------------------------
  // Structural only: did the rules recognise what they read?
  let confidence = 1.0;
  if (result.unparsed.length > 0) confidence -= 0.35 * result.unparsed.length;
  if (schedulesFound.length > 1 && new Set(schedulesFound.map((s) => s.token)).size > 1) {
    confidence -= 0.4;
  }
  if (!result.prn && result.dosesPerDay === 0) confidence -= 0.5;
  if (duration && duration.derivedFromUnits && duration.remainder > 0) confidence -= 0.1;
  result.confidence = Math.max(0, Math.min(1, Number(confidence.toFixed(2))));

  result.plainLanguage = toPlainLanguage(result);
  return result;
}

function shiftMinutes(hhmm, delta) {
  if (!hhmm) return hhmm;
  const [h, m] = hhmm.split(':').map(Number);
  let total = (h * 60 + m + delta + 1440) % 1440;
  const hh = String(Math.floor(total / 60)).padStart(2, '0');
  const mm = String(total % 60).padStart(2, '0');
  return `${hh}:${mm}`;
}

// ---------------------------------------------------------------- rendering

const SLOT_LABEL = {
  morning: 'Morning',
  afternoon: 'Afternoon',
  evening: 'Evening',
  night: 'Night',
  bedtime: 'Bedtime',
};

function toPlainLanguage(r) {
  const lines = [];

  if (r.prn) {
    lines.push('Take only when needed.');
    lines.push('No fixed times — this medicine is not added to your daily schedule.');
  } else if (r.stat) {
    lines.push('Take one dose now, once only.');
  } else if (/^\d/.test(r.scheduleCode || '') && r.scheduleCode.includes('-')) {
    // Slot notation: show every slot including the zeros, as PS 4 asks.
    const parsed = parseSlotCode(r.scheduleCode);
    parsed.names.forEach((name, i) => {
      const qty = parsed.quantities[i];
      lines.push(`${SLOT_LABEL[name]} — ${fmtQty(qty)} ${unitWord(qty)}`);
    });
  } else if (r.times.length && r.slots.length === 0) {
    lines.push(`Take ${r.frequencyLabel} — at ${r.times.join(', ')}.`);
  } else {
    lines.push(`Take ${r.frequencyLabel}.`);
    r.doses.forEach((d) => {
      lines.push(`${SLOT_LABEL[d.slot]} — ${fmtQty(d.qty)} ${unitWord(d.qty)} at ${d.time}`);
    });
  }

  if (r.food === 'after') lines.push('Take after meals.');
  else if (r.food === 'before') lines.push('Take before meals, on an empty stomach.');

  if (r.ongoing) lines.push('Continue until your doctor tells you to stop.');
  else if (r.durationDays) lines.push(`For ${r.durationDays} day${r.durationDays === 1 ? '' : 's'}.`);

  return lines.join('\n');
}

// ---------------------------------------------------------------- exports

module.exports = {
  decodeSchedule,
  parseSlotCode,
  parseDuration,
  parseQuantity,
  DEFAULT_SLOT_TIMES,
  FREQUENCY_CODES,
  INTERVAL_CODES,
  PRN_CODES,
  SLOT_ORDER,
};
