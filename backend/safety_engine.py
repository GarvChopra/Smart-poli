"""
SmartPoli — medication timing safety engine.

One place that answers three questions from the SAME rule file
(safety_rules.json), so they can never contradict each other:

  1. Do two scheduled doses sit too close together?     detect_schedule_conflicts()
  2. A dose was missed - what does the label say to do?  missed_dose_guidance()
  3. Can a conflicting dose be moved, and to when?       conflict proposals

Hard rules (deliberate, tested):
  * Rules are DATA: each one quotes a US FDA label sentence and carries its
    set_id / effective date. Nothing here invents an interval. No rule for a
    pair -> the answer is "not verified", never "safe".
  * An LLM is never involved in producing or choosing a rule.
  * Gaps are only ever LENGTHENED. A dose is never moved earlier, a gap is
    never shortened, a dose is never doubled or combined.
  * High-risk medicines (insulin, anticoagulants, ...) never get a computed
    catch-up time - only the label sentence and "contact your prescriber".
  * Pure functions over plain dicts: no database, no clock, no network. The
    caller supplies 'now'. That keeps every decision reproducible and
    testable, and lets the caller record inputs + decision in the audit log.

Rules are unreviewed by a clinician (see review_status in the rule file);
that flag is surfaced to the caller on every result.
"""

import json
import os
import re
from datetime import datetime, timedelta
from typing import Optional

_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "safety_rules.json")

# Aliases that are only trusted when they ARE the whole medicine name, because
# the same word is routinely a salt/part of an unrelated drug's name
# ("atorvastatin calcium", "omeprazole magnesium", "zinc oxide cream" ...).
_BARE_ONLY = {"calcium", "iron", "zinc", "antacid"}
_NOISE_TOKENS = {
    "tab", "tablet", "tablets", "cap", "capsule", "capsules", "syp", "syrup", "inj", "injection", "sr", "xr", "er",
    "mg", "mcg", "g", "ml", "iu", "supplement", "supplements", "plus", "forte", "ip", "bp", "usp",
}

RULES_DISCLAIMER = (
    "Based on the US FDA label for this ingredient. Indian brands can have different labels. "
    "Not reviewed by a clinician - confirm with your pharmacist or doctor."
)


def load_rules(path: Optional[str] = None) -> dict:
    with open(path or _RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- ingredient matching

def _tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+|\d+", (name or "").lower())]


def _clean_tokens(name: str) -> list[str]:
    return [t for t in _tokens(name) if t not in _NOISE_TOKENS and not t.isdigit()]


def _contains_seq(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def resolve_ingredients(name: str, rules: dict) -> set[str]:
    """Rule-file ingredient keys a medicine name refers to (empty = unknown).
    A '+' separates components of a combination product."""
    found: set[str] = set()
    aliases = rules.get("ingredient_aliases", {})
    for part in re.split(r"\s*\+\s*", name or ""):
        cleaned = _clean_tokens(part)
        if not cleaned:
            continue
        for key, names in aliases.items():
            for alias in names:
                a_tokens = _tokens(alias)
                if alias in _BARE_ONLY or key in _BARE_ONLY and len(a_tokens) == 1:
                    if cleaned == a_tokens:
                        found.add(key)
                elif _contains_seq(cleaned, a_tokens):
                    found.add(key)
    return found


def high_risk_class(name: str, rules: dict) -> Optional[str]:
    cleaned = _clean_tokens(name)
    for cls, names in rules.get("high_risk", {}).get("ingredients", {}).items():
        for alias in names:
            if _contains_seq(cleaned, _tokens(alias)):
                return cls
    return None


def _source_view(src: dict) -> dict:
    return {
        "title": src.get("title"),
        "set_id": src.get("set_id"),
        "effective": src.get("effective"),
        "section": src.get("section"),
        "quote": src.get("quote"),
        "url": f"https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid={src['set_id']}" if src.get("set_id") else None,
    }


def _all_spacing_rules(rules: dict) -> list[dict]:
    out = list(rules.get("spacing_rules", []))
    if rules.get("alendronate_rule"):
        out.append(rules["alendronate_rule"])
    return out


# ---------------------------------------------------------------- schedule model

def _effective_time(dose: dict) -> datetime:
    """When a dose actually happened (taken) or is planned to happen."""
    if dose["state"] == "taken" and dose.get("acted_at"):
        return dose["acted_at"]
    return dose["scheduled_at"]


def _live_dose(dose: dict) -> bool:
    """Doses that still constrain timing: taken ones (they happened) and
    pending/snoozed ones (they will). Missed and skipped doses never
    entered the body, so they impose nothing."""
    return dose["state"] in ("taken", "pending", "snoozed")


def _hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600.0


def _pair_rule_matches(rule: dict, a_keys: set[str], b_keys: set[str]) -> bool:
    return rule["a"] in a_keys and (rule["b"] == "*" or rule["b"] in b_keys)


def _timing_violation(rule: dict, t_a: datetime, t_b: datetime) -> Optional[tuple[float, float, str]]:
    """(required_hours, actual_hours, order) if the A/B dose pair breaks the
    rule, else None. order is 'a_first', 'b_first' or 'same_time'."""
    gap = _hours(t_a, t_b)  # >0: A then B
    if gap > 0:
        req = rule.get("a_then_b_min_hours")
        order = "a_first"
    elif gap < 0:
        req = rule.get("b_then_a_min_hours")
        order = "b_first"
    else:
        # Same minute: the stricter of the two requirements applies.
        reqs = [r for r in (rule.get("a_then_b_min_hours"), rule.get("b_then_a_min_hours")) if r is not None]
        req = max(reqs) if reqs else None
        order = "same_time"
    if req is None:
        return None
    actual = abs(gap)
    return (req, actual, order) if actual < req else None


# ---------------------------------------------------------------- conflicts

def detect_schedule_conflicts(medicines: list[dict], rules: dict, now: datetime,
                              horizon_hours: float = 48.0) -> list[dict]:
    """
    medicines: [{"medicine_id", "name", "doses": [{"id","scheduled_at","state","acted_at"}]}]
    (all datetimes naive, in the SAME clock - the patient's local time).

    Returns conflicts of four kinds:
      timing               a verified rule is broken by two real doses
      interval_unspecified the label says 'separate' but gives no time
      duplicate_dose       the same medicine twice within an hour
      duplicate_ingredient two different medicines with the same active ingredient
    A pair with no rule at all produces nothing: silence means 'not verified',
    and callers must not present it as 'safe'.
    """
    lo, hi = now - timedelta(hours=horizon_hours / 2), now + timedelta(hours=horizon_hours)
    ctx = []
    for m in medicines:
        doses = [d for d in m["doses"] if _live_dose(d) and lo <= _effective_time(d) <= hi]
        if not doses:
            continue
        ctx.append({
            "medicine_id": m["medicine_id"], "name": m["name"], "doses": doses,
            "keys": resolve_ingredients(m["name"], rules), "high_risk": high_risk_class(m["name"], rules),
            "all_doses": m["doses"],
        })

    conflicts: list[dict] = []
    seen: set = set()

    spacing = _all_spacing_rules(rules)
    for i, ma in enumerate(ctx):
        for j, mb in enumerate(ctx):
            if i == j:
                continue
            for rule in spacing:
                if not _pair_rule_matches(rule, ma["keys"], mb["keys"]):
                    continue
                if rule["b"] == "*" and ma["keys"] & mb["keys"]:
                    continue
                for da in ma["doses"]:
                    for db in mb["doses"]:
                        v = _timing_violation(rule, _effective_time(da), _effective_time(db))
                        if not v:
                            continue
                        key = (rule["id"], da["id"], db["id"])
                        if key in seen:
                            continue
                        seen.add(key)
                        conflicts.append(_timing_conflict(rule, ma, mb, da, db, v, rules, now, medicines))

    for rule in rules.get("interval_unspecified_rules", []):
        for ma in ctx:
            for mb in ctx:
                if ma is mb or not _pair_rule_matches(rule, ma["keys"], mb["keys"]):
                    continue
                key = ("iu", rule["id"], ma["medicine_id"], mb["medicine_id"])
                if key in seen:
                    continue
                seen.add(key)
                conflicts.append({
                    "kind": "interval_unspecified", "status": "needs_clarification",
                    "rule_id": rule["id"],
                    "medicines": [ma["name"], mb["name"]],
                    "medicine_ids": [ma["medicine_id"], mb["medicine_id"]],
                    "dose_ids": [],
                    "message": rule["plain"],
                    "required_hours": None, "proposal": None,
                    "source": _source_view(rule["source"]),
                    "reviewed": rule.get("reviewed", False), "disclaimer": RULES_DISCLAIMER,
                })

    for m in ctx:
        ds = sorted(m["doses"], key=_effective_time)
        for x, y in zip(ds, ds[1:]):
            if abs(_hours(_effective_time(x), _effective_time(y))) < 1.0:
                key = ("dup", m["medicine_id"], x["id"], y["id"])
                if key not in seen:
                    seen.add(key)
                    conflicts.append({
                        "kind": "duplicate_dose", "status": "needs_clarification", "rule_id": None,
                        "medicines": [m["name"]], "medicine_ids": [m["medicine_id"]],
                        "dose_ids": [x["id"], y["id"]],
                        "message": f"{m['name']} appears twice within an hour. Check that this is what the prescription says.",
                        "required_hours": None, "proposal": None, "source": None, "reviewed": False,
                        "disclaimer": None,
                    })
    for i, ma in enumerate(ctx):
        for mb in ctx[i + 1:]:
            shared = ma["keys"] & mb["keys"]
            if shared and ma["medicine_id"] != mb["medicine_id"]:
                key = ("dupi", min(ma["medicine_id"], mb["medicine_id"]), max(ma["medicine_id"], mb["medicine_id"]))
                if key not in seen:
                    seen.add(key)
                    conflicts.append({
                        "kind": "duplicate_ingredient", "status": "needs_clarification", "rule_id": None,
                        "medicines": [ma["name"], mb["name"]],
                        "medicine_ids": [ma["medicine_id"], mb["medicine_id"]], "dose_ids": [],
                        "message": f"{ma['name']} and {mb['name']} contain the same active ingredient. "
                                   "Ask your pharmacist or doctor whether you should take both.",
                        "required_hours": None, "proposal": None, "source": None, "reviewed": False,
                        "disclaimer": None,
                    })
    return conflicts


def _timing_conflict(rule, ma, mb, da, db, violation, rules, now, medicines) -> dict:
    required, actual, order = violation
    proposal = _propose_move(rule, ma, mb, da, db, order, required, rules, now, medicines)
    return {
        "kind": "timing",
        "status": "proposal_available" if proposal else "needs_clarification",
        "rule_id": rule["id"],
        "medicines": [ma["name"], mb["name"]],
        "medicine_ids": [ma["medicine_id"], mb["medicine_id"]],
        "dose_ids": [da["id"], db["id"]],
        "dose_times": [_effective_time(da).isoformat(), _effective_time(db).isoformat()],
        "order": order,
        "required_hours": required, "actual_hours": round(actual, 2),
        "message": f"{rule['plain']} Keep at least {_fmt_hours(required)} between "
                   f"{ma['name']} and {mb['name']}; these two are {_fmt_hours(actual)} apart.",
        "proposal": proposal,
        "source": _source_view(rule["source"]),
        "reviewed": rule.get("reviewed", False), "disclaimer": RULES_DISCLAIMER,
    }


def _fmt_hours(h: float) -> str:
    if h < 1:
        return f"{int(round(h * 60))} minutes"
    h = round(h, 1)
    return f"{int(h)} hour{'s' if int(h) != 1 else ''}" if h == int(h) else f"{h} hours"


def _next_dose_after(m: dict, t: datetime, exclude_id: int) -> Optional[datetime]:
    nxt = [d["scheduled_at"] for d in m["all_doses"]
           if d["id"] != exclude_id and d["state"] in ("pending", "snoozed") and d["scheduled_at"] > t]
    return min(nxt) if nxt else None


def _propose_move(rule, ma, mb, da, db, order, required, rules, now, medicines) -> Optional[dict]:
    """
    The only adjustment the engine will ever suggest: push the LATER of the
    two doses later, to the earliest time that satisfies the rule. Refused
    (None -> 'needs clarification') when:
      * the later dose is not an upcoming, unrecorded dose,
      * its medicine is high-risk,
      * the new time would reach the medicine's own next dose, or
      * moving it would create a new conflict.
    Never moves anything earlier and never changes a dose that was taken.
    """
    ta, tb = _effective_time(da), _effective_time(db)
    if order == "same_time":
        mover, mover_dose, anchor_t = (mb, db, ta)
    elif order == "a_first":
        mover, mover_dose, anchor_t = (mb, db, ta)
    else:
        mover, mover_dose, anchor_t = (ma, da, tb)
    if mover_dose["state"] not in ("pending", "snoozed") or mover_dose["scheduled_at"] <= now:
        return None
    if mover["high_risk"]:
        return None
    target = anchor_t + timedelta(hours=required)
    if target <= mover_dose["scheduled_at"]:
        return None
    nxt = _next_dose_after(mover, mover_dose["scheduled_at"], mover_dose["id"])
    if nxt is not None and target >= nxt:
        return None
    # Re-check against EVERY medicine: the moved dose must not break any verified rule.
    if _violates_after_move(medicines, rules, now, mover_dose["id"], target):
        return None
    return {
        "dose_id": mover_dose["id"], "medicine": mover["name"],
        "from": mover_dose["scheduled_at"].isoformat(), "to": target.isoformat(),
        "reason_rule_id": rule["id"],
        "note": "Suggested only. Nothing changes unless you accept it. Your prescribed dose and course do not change.",
    }


def _violates_after_move(medicines, rules, now, dose_id: int, new_time: datetime) -> bool:
    moved = [{"medicine_id": m["medicine_id"], "name": m["name"],
              "doses": [dict(d, scheduled_at=new_time) if d["id"] == dose_id else d for d in m["doses"]]}
             for m in medicines]
    return any(dose_id in c["dose_ids"] for c in detect_schedule_conflicts_no_proposals(moved, rules, now))


def detect_schedule_conflicts_no_proposals(medicines, rules, now, horizon_hours: float = 48.0) -> list[dict]:
    """Timing conflicts only, without computing proposals (used to validate a proposal)."""
    lo, hi = now - timedelta(hours=horizon_hours / 2), now + timedelta(hours=horizon_hours)
    ctx = [{"medicine_id": m["medicine_id"], "name": m["name"],
            "doses": [d for d in m["doses"] if _live_dose(d) and lo <= _effective_time(d) <= hi],
            "keys": resolve_ingredients(m["name"], rules)} for m in medicines]
    out = []
    for ma in ctx:
        for mb in ctx:
            if ma is mb:
                continue
            for rule in _all_spacing_rules(rules):
                if not _pair_rule_matches(rule, ma["keys"], mb["keys"]):
                    continue
                if rule["b"] == "*" and ma["keys"] & mb["keys"]:
                    continue
                for da in ma["doses"]:
                    for db in mb["doses"]:
                        if _timing_violation(rule, _effective_time(da), _effective_time(db)):
                            out.append({"kind": "timing", "dose_ids": [da["id"], db["id"]]})
    return out


def validate_reschedule(medicines: list[dict], rules: dict, now: datetime, dose_id: int,
                        to: datetime) -> tuple[bool, str]:
    """Is moving dose_id to `to` allowed? Used when a patient accepts a proposal:
    the server re-derives the answer instead of trusting the client's time."""
    conflicts = detect_schedule_conflicts(medicines, rules, now)
    for c in conflicts:
        p = c.get("proposal")
        if p and p["dose_id"] == dose_id:
            proposed = datetime.fromisoformat(p["to"])
            if to < proposed:
                return False, "That time is earlier than the rules allow."
            nxt = None
            for m in medicines:
                for d in m["doses"]:
                    if d["id"] == dose_id:
                        nxt = _next_dose_after({"all_doses": m["doses"]}, d["scheduled_at"], dose_id)
            if nxt is not None and to >= nxt:
                return False, "That time reaches the next dose of the same medicine."
            if _violates_after_move(medicines, rules, now, dose_id, to):
                return False, "That time would break another medicine-spacing rule."
            return True, "ok"
    return False, "No verified rule supports moving this dose."


# ---------------------------------------------------------------- missed dose

def missed_dose_guidance(medicine: dict, dose: dict, rules: dict, now: datetime,
                         other_medicines: Optional[list[dict]] = None) -> dict:
    """
    What the label says to do about a missed dose, plus the facts around it.

    medicine: {"medicine_id","name","doses":[...]}  (all of this medicine's doses)
    dose:     the missed dose.
    Always returns: never_double=True, hours_since_due, next_dose_at,
    hours_to_next, action, headline, label_quote/source (if any rule).

    action values:
      take_now_ok            label window met and no spacing rule blocks it now
      wait_until             label allows it but a spacing rule needs a later time
      skip_and_continue      label says skip (window missed / label says continue schedule)
      follow_label_text      label gives text but no number the engine can apply
      contact_provider       high-risk medicine, or no usable rule: ask a human
    """
    scheduled = dose["scheduled_at"]
    nxt = _next_dose_after({"all_doses": medicine["doses"]}, scheduled, dose["id"])
    result = {
        "dose_id": dose["id"], "medicine": medicine["name"],
        "scheduled_at": scheduled.isoformat(),
        "hours_since_due": round(_hours(scheduled, now), 2),
        "next_dose_at": nxt.isoformat() if nxt else None,
        "hours_to_next": round(_hours(now, nxt), 2) if nxt else None,
        "never_double": True,
        "universal_warning": "Never take a double dose to make up for a missed one.",
        "reviewed": False, "disclaimer": RULES_DISCLAIMER,
        "earliest_safe_time": None, "spacing_notes": [],
    }

    hr = high_risk_class(medicine["name"], rules)
    keys = resolve_ingredients(medicine["name"], rules)

    if hr:
        label = rules["high_risk"].get("label_text", {}).get(hr)
        result.update({
            "action": "contact_provider", "high_risk": True,
            "headline": "This is a medicine where a missed dose needs care. Please contact your doctor or pharmacist "
                        "now to ask what to do. Do not take extra to catch up.",
            "label_quote": label["source"]["quote"] if label else None,
            "source": _source_view(label["source"]) if label else None,
        })
        return result

    rule = next((rules["missed_dose_rules"][k] for k in sorted(keys) if k in rules.get("missed_dose_rules", {})), None)
    if rule is None:
        result.update({
            "action": "contact_provider", "high_risk": False,
            "headline": "SmartPoli does not have a verified missed-dose rule for this medicine. "
                        "Check the leaflet or ask your pharmacist or doctor. Do not take a double dose.",
            "label_quote": None, "source": None,
        })
        return result

    result["label_quote"] = rule["source"]["quote"]
    result["source"] = _source_view(rule["source"])
    result["reviewed"] = rule.get("reviewed", False)
    rtype = rule["type"]

    if rtype == "window_hours_before_next":
        if nxt is None:
            result.update({
                "action": "contact_provider",
                "headline": "The label ties a missed dose to the time of the next dose, and there is no next dose "
                            "scheduled. Ask your pharmacist or doctor.",
            })
            return result
        remaining = _hours(now, nxt)
        need = rule["min_hours_before_next_dose"]
        if remaining >= need:
            result.update({
                "action": "take_now_ok",
                "headline": f"The label allows taking it now because the next dose is {_fmt_hours(remaining)} away "
                            f"(at least {need} hours are needed).",
            })
            earliest, notes = _earliest_after_spacing(medicine, dose, keys, rules, now, other_medicines)
            result["spacing_notes"] = notes
            if earliest and earliest > now:
                if _hours(earliest, nxt) >= need:
                    result.update({
                        "action": "wait_until", "earliest_safe_time": earliest.isoformat(),
                        "headline": f"The label allows this dose, but to keep the right gap from your other "
                                    f"medicine wait until {earliest.strftime('%H:%M')}.",
                    })
                else:
                    result.update({
                        "action": "skip_and_continue",
                        "headline": "By the time the gap from your other medicine is respected, it would be too "
                                    "close to the next dose. Skip it and continue as scheduled, or ask your pharmacist.",
                    })
        else:
            result.update({
                "action": "skip_and_continue",
                "headline": f"The label says not to take it when less than {need} hours remain before the next dose. "
                            f"Only {_fmt_hours(max(remaining, 0))} remain. Skip it and carry on with the next dose.",
            })
        return result

    if rtype == "skip_and_continue":
        result.update({
            "action": "skip_and_continue",
            "headline": "According to the label, skip the missed dose and take your next dose as prescribed "
                        "(unless your doctor told you differently).",
        })
        return result

    result.update({
        "action": "follow_label_text",
        "headline": "SmartPoli cannot work out the exact timing for this medicine. Here is what the label says. "
                    "If you are unsure, ask your pharmacist or doctor.",
        "applies_to_note": rule.get("applies_to_note"),
    })
    return result


def _earliest_after_spacing(medicine, dose, keys, rules, now, other_medicines) -> tuple[Optional[datetime], list[str]]:
    """Earliest time `now` or later at which taking this dose breaks no
    verified spacing rule against other doses already taken or still planned."""
    earliest = now
    notes: list[str] = []
    for om in (other_medicines or []):
        okeys = resolve_ingredients(om["name"], rules)
        for rule in _all_spacing_rules(rules):
            if _pair_rule_matches(rule, keys, okeys) and not (rule["b"] == "*" and keys & okeys):
                after_theirs, before_theirs = rule.get("b_then_a_min_hours"), rule.get("a_then_b_min_hours")
            elif _pair_rule_matches(rule, okeys, keys) and not (rule["b"] == "*" and keys & okeys):
                after_theirs, before_theirs = rule.get("a_then_b_min_hours"), rule.get("b_then_a_min_hours")
            else:
                continue
            for od in om["doses"]:
                if not _live_dose(od):
                    continue
                t_o = _effective_time(od)
                if t_o <= now and after_theirs:
                    need_at = t_o + timedelta(hours=after_theirs)
                    if need_at > earliest:
                        earliest = need_at
                        notes.append(f"Keep at least {_fmt_hours(after_theirs)} after {om['name']} "
                                     f"({rule['source']['title']}).")
                elif t_o > now and before_theirs and _hours(now, t_o) < before_theirs:
                    notes.append(f"{om['name']} is planned at {t_o.strftime('%H:%M')}, closer than the "
                                 f"{_fmt_hours(before_theirs)} the label asks for; it may need to move later.")
    return (earliest if earliest > now else None), notes


def curated_rule_exists(name_a: str, name_b: str, rules: dict) -> bool:
    """True if a curated spacing rule (with or without a stated interval) covers this pair in either order."""
    ka, kb = resolve_ingredients(name_a, rules), resolve_ingredients(name_b, rules)
    for rule in _all_spacing_rules(rules):
        for x, y in ((ka, kb), (kb, ka)):
            if _pair_rule_matches(rule, x, y) and not (rule["b"] == "*" and ka & kb):
                return True
    return False


def take_time_spacing(this_name: str, other_name: str, other_taken_at: datetime, now: datetime,
                      rules: dict) -> Optional[dict]:
    """Does taking `this` medicine NOW break a curated label rule against `other`, which was ACTUALLY taken at
    other_taken_at? Returns the binding rule's details (with the earliest allowed time) or None."""
    this_keys, other_keys = resolve_ingredients(this_name, rules), resolve_ingredients(other_name, rules)
    best = None
    for rule in _all_spacing_rules(rules):
        if _pair_rule_matches(rule, this_keys, other_keys) and not (rule["b"] == "*" and this_keys & other_keys):
            wait = rule.get("b_then_a_min_hours")        # this is 'a'; the other (b) came first
        elif _pair_rule_matches(rule, other_keys, this_keys) and not (rule["b"] == "*" and this_keys & other_keys):
            wait = rule.get("a_then_b_min_hours")        # the other is 'a' and came first; this is 'b'
        else:
            continue
        if wait is None:
            continue
        earliest = other_taken_at + timedelta(hours=wait)
        if earliest > now and (best is None or earliest > best["earliest"]):
            best = {"rule_id": rule["id"], "earliest": earliest, "required_hours": wait,
                    "quote": rule["source"]["quote"], "source": _source_view(rule["source"])}
    return best


def build_missed_notification(guidance: dict, conflict_hint: Optional[str] = None) -> dict:
    """Short text for the single notification sent when a dose is missed:
    what was missed, never double, when the next dose is, and any gap rule."""
    when = datetime.fromisoformat(guidance["scheduled_at"]).strftime("%H:%M")
    parts = [f"You missed {guidance['medicine']} ({when}). Don't take a double dose."]
    if guidance.get("next_dose_at"):
        nxt = datetime.fromisoformat(guidance["next_dose_at"])
        parts.append(f"Your next dose of it is at {nxt.strftime('%H:%M')}.")
    if guidance.get("action") == "contact_provider":
        parts.append("Please ask your doctor or pharmacist what to do.")
    elif guidance.get("earliest_safe_time"):
        parts.append(f"If you take it, wait until {datetime.fromisoformat(guidance['earliest_safe_time']).strftime('%H:%M')}.")
    if conflict_hint:
        parts.append(conflict_hint)
    return {"title": "Missed dose", "body": " ".join(parts)}
