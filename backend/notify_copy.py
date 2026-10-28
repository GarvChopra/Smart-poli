"""
SmartPoli - the words in notifications.

Two goals: a notification should say what matters in the first few words (the lock screen cuts the rest), and it should not
read the same every single day. Each kind has a few phrasings and one friendly closing line; which ones are used is picked
from the patient's own calendar day, so a day's reminders for one person sound alike but tomorrow's are different.

Never in here: a diagnosis, a dose instruction beyond the schedule the person already confirmed, a claim about what a
medicine does, or pressure ("you must", "dangerous"). The missed-dose text for the patient themselves, which carries the
"don't double up" safety advice, stays in safety_engine.build_missed_notification and is not reworded here.
"""

from datetime import datetime
from typing import Optional

# One short, kind line per day. Nothing medical, nothing that could read as advice.
DAILY_LINES = [
    "Small steps, every day.",
    "You're doing great. Keep it up.",
    "A glass of water makes it easier.",
    "Right on schedule.",
    "One tap and you're done.",
    "Steady wins.",
    "Looking after yourself counts.",
    "Your future self says thanks.",
    "Take it easy, you've got this.",
    "Good habits start with this one.",
    "Another day, another step.",
    "Nicely done so far.",
]


def pick(options: list, seed: int):
    return options[seed % len(options)]


def clock(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _label(item: dict) -> str:
    return f"{item['name']} {item['dose']}".strip()


def _food(items: list) -> str:
    foods = {i.get("food") or "any" for i in items}
    it = "it" if len(items) == 1 else "them"
    if foods == {"before"}:
        return f"Take {it} before food."
    if foods == {"after"}:
        return f"Take {it} after food."
    return ""


def _when(mins: int) -> str:
    if mins < 90:
        return f"{mins} min"
    h, m = divmod(mins, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def self_reminder(kind: str, items: list, minutes_until: float, seed: int) -> dict:
    """A reminder for the person who takes the medicine. `items`: [{name, dose, food, at}] - all due at the same moment
    (one notification, however many medicines). `seed` changes daily (the patient's local date)."""
    n = len(items)
    t = clock(items[0]["at"])
    names = ", ".join(_label(i) for i in items)
    food, line = _food(items), pick(DAILY_LINES, seed)
    mins = max(1, int(round(minutes_until)))

    if kind in ("lead", "soon"):
        if n == 1:
            head = pick(["Coming up: {x}", "{x} at {t}", "Heads-up: {x} soon"], seed).format(x=items[0]["name"], t=t) if kind == "lead" \
                else pick(["Almost time: {x}", "{x} in a few minutes", "Get ready: {x}"], seed).format(x=items[0]["name"])
            return {"title": head, "body": _join(f"{_label(items[0])} at {t}, in {_when(mins)}.", food, line)}
        head = f"{n} medicines coming up at {t}" if kind == "lead" else f"{n} medicines in a few minutes"
        return {"title": head, "body": _join(f"{names}.", food, line)}

    if kind == "due":
        if n == 1:
            head = pick(["Time for {x}", "{x} is due", "Your {t} medicine: {x}"], seed).format(x=items[0]["name"], t=t)
            return {"title": head, "body": _join(f"{_label(items[0])}, due {t}.", food, line)}
        return {"title": pick(["{n} medicines due now", "Time for your {t} medicines"], seed).format(n=n, t=t),
                "body": _join(f"{names}.", food, line)}

    # follow-up: a plain, calm question - no streak talk, no guilt
    if n == 1:
        return {"title": f"Did you take {items[0]['name']}?",
                "body": f"Your {t} dose isn't marked as taken yet. Open SmartPoli to mark it taken or skipped."}
    return {"title": f"Did you take your {t} medicines?",
            "body": f"{names} aren't marked as taken yet. Open SmartPoli to mark them taken or skipped."}


def caregiver_missed(label: str, items: list) -> dict:
    """To a caregiver: who missed what, and the one thing they can do from the notification itself."""
    if len(items) == 1:
        it = items[0]
        return {"title": f"{label} missed the {clock(it['at'])} dose",
                "body": f"{_label(it)} wasn't marked as taken. Tap Remind now to nudge {label}."}
    shown = ", ".join(f"{i['name']} {clock(i['at'])}" for i in items[:3]) + (f" +{len(items) - 3} more" if len(items) > 3 else "")
    return {"title": f"{label} missed {len(items)} doses", "body": f"{shown}. Tap Remind now to nudge {label}."}


def nudge_to_patient(caregiver: str, items: Optional[list]) -> dict:
    """What the person being looked after sees when a caregiver taps Remind now."""
    if items:
        names = ", ".join(_label(i) for i in items[:3])
        return {"title": f"{caregiver} sent you a reminder",
                "body": f"{names} was due at {clock(items[0]['at'])}. Have you taken it? Open SmartPoli to mark it."}
    return {"title": f"{caregiver} sent you a reminder", "body": "Have you taken your medicine today? Open SmartPoli to check."}
