"""
SmartPoli - dose reminders.

Every upcoming dose gets several notifications, so a single dismissed alert
never means a silent miss:

    lead      an early heads-up                   (default 30 min before; 0 = off; up to 2 h)
    soon      a last heads-up                     (10 min before; only when `lead` is longer)
    due       at the dose time
    followup  still not marked taken, 15 min after (before it auto-misses at 2h)
    missed    sent once when the dose is auto-marked missed: what to do,
              when the next dose is, and any spacing rule that applies

Beyond doses, the other things a patient should hear about:

    conflict    right after a prescription is confirmed, once per medicine pair, when the
                label-based rules say two medicines need spacing (or need a pharmacist)
    course_end  the last dose of a finite course is today / tomorrow

Each (dose, kind, channel) is sent AT MOST ONCE - ReminderLog has a unique
constraint on it - so the 1-minute sweep, a restarted server or two workers
can never double-notify. Channels: Web Push (the app / Android TWA) and
WhatsApp (patients who opted in). Either can be absent; the other still works.

All time maths is on the patient's own clock (clock.py).
"""

import logging
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import safety_engine
import safety_service
import webpush_service
from clock import MAX_UTC_OFFSET, local_now, resolve_timezone
from db import Dose, PatientSettings, PushSubscription, ReminderLog, WhatsAppSession

logger = logging.getLogger(__name__)

DEFAULT_LEAD_MINUTES = 30
SOON_MINUTES = 10
FOLLOWUP_AFTER = timedelta(minutes=15)
DUE_LATE_GRACE = timedelta(minutes=60)       # a 'due' ping is still useful this long after
FOLLOWUP_UNTIL = timedelta(minutes=110)      # stop before the 2h auto-miss


def _food_hint(medicine) -> str:
    return {"before": " Take it before food.", "after": " Take it after food."}.get(medicine.food or "any", "")


def build_message(kind: str, dose: Dose, minutes_until: float) -> dict:
    medicine = dose.medicine
    name = medicine.name or medicine.raw_text
    at = dose.scheduled_at.strftime("%H:%M")
    dose_txt = f" {medicine.dose_amount}{medicine.dose_unit or ''}" if medicine.dose_amount else ""
    if kind in ("lead", "soon"):
        mins = max(1, int(round(minutes_until)))
        when = f"in {mins} min" if mins < 90 else f"in {mins // 60} h {mins % 60} min" if mins % 60 else f"in {mins // 60} h"
        title = f"Coming up: {name}" if kind == "lead" else f"Almost time: {name}"
        return {"title": title, "body": f"{name}{dose_txt} at {at} ({when}).{_food_hint(medicine)}"}
    if kind == "due":
        return {"title": f"Time to take {name}", "body": f"{name}{dose_txt} is due now ({at}).{_food_hint(medicine)}"}
    return {"title": f"Did you take {name}?",
            "body": f"Your {at} dose of {name} isn't marked as taken yet. Open SmartPoli to mark it taken or skipped."}


def _due_kinds(minutes_until: float, lead: int, soon_on: bool = True, followup_on: bool = True) -> list[str]:
    """Which reminder kinds are currently owed for a dose `minutes_until` away
    (negative = overdue)."""
    kinds = []
    if lead > 0:
        if lead <= SOON_MINUTES:
            if 0 < minutes_until <= lead:
                kinds.append("lead")
        else:
            if SOON_MINUTES < minutes_until <= lead:
                kinds.append("lead")
            if soon_on and 0 < minutes_until <= SOON_MINUTES:
                kinds.append("soon")
    if -DUE_LATE_GRACE.total_seconds() / 60 < minutes_until <= 0:
        kinds.append("due")
    if followup_on and -FOLLOWUP_UNTIL.total_seconds() / 60 < minutes_until <= -FOLLOWUP_AFTER.total_seconds() / 60:
        kinds.append("followup")
    return kinds


def _claim(db: Session, dose_id: int, kind: str, channel: str) -> bool:
    """Atomically record 'this reminder is being sent'. False = already sent."""
    try:
        db.add(ReminderLog(dose_id=dose_id, kind=kind, channel=channel))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _release(db: Session, dose_id: int, kind: str, channel: str) -> None:
    db.query(ReminderLog).filter_by(dose_id=dose_id, kind=kind, channel=channel).delete()
    db.commit()


def _send_push(db: Session, patient_id: int, dose_id: int, kind: str, msg: dict, push_fn) -> int:
    subs = db.query(PushSubscription).filter(PushSubscription.patient_id == patient_id).all()
    sent = 0
    for sub in subs:
        status = push_fn(sub.endpoint, sub.p256dh, sub.auth, {
            "title": msg["title"], "body": msg["body"], "url": "/",
            "tag": f"dose-{dose_id}-{kind}", "dose_id": dose_id, "kind": kind,
        })
        if status == "sent":
            sub.last_success_at = datetime.utcnow()
            sent += 1
        elif status == "gone":
            db.delete(sub)
    db.commit()
    return sent


def _send_whatsapp(db: Session, patient_id: int, msg: dict, wa_fn) -> int:
    sessions = (db.query(WhatsAppSession)
                .filter(WhatsAppSession.patient_id == patient_id, WhatsAppSession.notifications_opt_in.is_(True)).all())
    return sum(1 for s in sessions if wa_fn(s.phone, f"*{msg['title']}*\n{msg['body']}"))


def _notify(db: Session, patient_id: int, dose: Dose, kind: str, msg: dict, push_fn, wa_fn, wa_on: bool) -> int:
    """Send one reminder over every available channel, at most once each."""
    sent = 0
    if webpush_service.is_configured() or push_fn is not webpush_service.send:
        if db.query(PushSubscription).filter(PushSubscription.patient_id == patient_id).count():
            if _claim(db, dose.id, kind, "push"):
                n = _send_push(db, patient_id, dose.id, kind, msg, push_fn)
                if n == 0:
                    _release(db, dose.id, kind, "push")  # nothing delivered: allow a retry next minute
                sent += n
    if wa_on:
        if db.query(WhatsAppSession).filter(WhatsAppSession.patient_id == patient_id,
                                            WhatsAppSession.notifications_opt_in.is_(True)).count():
            if _claim(db, dose.id, kind, "whatsapp"):
                n = _send_whatsapp(db, patient_id, msg, wa_fn)
                if n == 0:
                    _release(db, dose.id, kind, "whatsapp")
                sent += n
    return sent


def _patient_prefs(db: Session, patient_id: int, cache: dict) -> tuple[str, int, bool, bool]:
    """(timezone, early heads-up minutes, last-heads-up on, follow-up on) for one patient, cached per sweep."""
    if patient_id not in cache:
        from db import PatientRoutine
        row = db.query(PatientSettings).filter(PatientSettings.patient_id == patient_id).first()
        tz = resolve_timezone(row.timezone if row else None)
        lead = row.reminder_lead_minutes if row and row.reminder_lead_minutes is not None else DEFAULT_LEAD_MINUTES
        rr = db.query(PatientRoutine).filter(PatientRoutine.patient_id == patient_id).first()
        soon_on = True if rr is None or rr.notify_soon is None else bool(rr.notify_soon)
        follow_on = True if rr is None or rr.notify_followup is None else bool(rr.notify_followup)
        cache[patient_id] = (tz, max(0, min(int(lead), 120)), soon_on, follow_on)
    return cache[patient_id]


def run_reminder_sweep(db: Session, utc_now: Optional[datetime] = None,
                       push_fn: Callable = webpush_service.send, wa_fn: Optional[Callable] = None) -> int:
    """Send every reminder currently owed. Returns how many messages went out."""
    import whatsapp_bot
    wa_fn = wa_fn or whatsapp_bot.send_whatsapp_message
    wa_on = whatsapp_bot.is_configured() or wa_fn is not whatsapp_bot.send_whatsapp_message
    utc_now = utc_now or datetime.utcnow()

    # Coarse filter on the server clock: local time is within +-14h of UTC.
    lo = utc_now - MAX_UTC_OFFSET - FOLLOWUP_UNTIL - timedelta(hours=1)
    hi = utc_now + MAX_UTC_OFFSET + timedelta(hours=3)
    doses = (db.query(Dose).filter(Dose.state.in_(("pending", "snoozed")), Dose.scheduled_at >= lo,
                                   Dose.scheduled_at <= hi).all())
    prefs: dict = {}
    sent_total = 0
    for dose in doses:
        patient_id = dose.medicine.prescription.patient_id
        tz, lead, soon_on, follow_on = _patient_prefs(db, patient_id, prefs)
        minutes_until = (dose.scheduled_at - local_now(tz, utc_now)).total_seconds() / 60
        for kind in _due_kinds(minutes_until, lead, soon_on, follow_on):
            sent_total += _notify(db, patient_id, dose, kind, build_message(kind, dose, minutes_until),
                                  push_fn, wa_fn, wa_on)
    try:
        sent_total += notify_course_ending(db, utc_now, push_fn, wa_fn)
    except Exception:  # noqa: BLE001 - never let the optional notice break dose reminders
        logger.exception("course-end notices failed")
    return sent_total


def notify_missed(db: Session, missed: list[Dose], utc_now: Optional[datetime] = None,
                  push_fn: Callable = webpush_service.send, wa_fn: Optional[Callable] = None) -> int:
    """One notification per newly-missed dose: never double up, when the next
    dose is, and the spacing rule (if a verified one applies)."""
    import whatsapp_bot
    wa_fn = wa_fn or whatsapp_bot.send_whatsapp_message
    wa_on = whatsapp_bot.is_configured() or wa_fn is not whatsapp_bot.send_whatsapp_message
    sent = 0
    for dose in missed:
        patient_id = dose.medicine.prescription.patient_id
        try:
            guidance = safety_service.missed_guidance_for_dose(db, dose, utc_now)
            hint = None
            if guidance.get("action") == "wait_until" and guidance.get("spacing_notes"):
                hint = guidance["spacing_notes"][0]
            msg = safety_engine.build_missed_notification(guidance, hint)
            safety_service.record_decision(db, patient_id, "system", "missed_dose_guidance",
                                           {"dose_id": dose.id}, guidance)
        except Exception:  # noqa: BLE001 - the notification must still go out, with the safe default text
            logger.exception("missed-dose guidance failed for dose %s", dose.id)
            msg = {"title": "Missed dose",
                   "body": f"You missed {dose.medicine.name or dose.medicine.raw_text}. Don't take a double dose; "
                           "ask your pharmacist or doctor what to do."}
        sent += _notify(db, patient_id, dose, "missed", msg, push_fn, wa_fn, wa_on)
    return sent


# ---------------------------------------------------------------- other major notices

COURSE_END_FROM_HOUR = 9     # not before 09:00 local, so it never wakes anyone


def notify_course_ending(db: Session, utc_now: Optional[datetime] = None,
                         push_fn: Callable = webpush_service.send, wa_fn: Optional[Callable] = None) -> int:
    """Tell the patient when the LAST dose of a finite course is today or
    tomorrow. Ongoing medicines and PRN are never included. Once per dose."""
    import whatsapp_bot
    wa_fn = wa_fn or whatsapp_bot.send_whatsapp_message
    wa_on = whatsapp_bot.is_configured() or wa_fn is not whatsapp_bot.send_whatsapp_message
    from db import Medicine
    utc_now = utc_now or datetime.utcnow()
    lo = utc_now - MAX_UTC_OFFSET
    hi = utc_now + MAX_UTC_OFFSET + timedelta(days=2)
    candidates = (db.query(Dose).join(Medicine, Dose.medicine_id == Medicine.id)
                  .filter(Dose.state.in_(("pending", "snoozed")), Medicine.duration_days.isnot(None),
                          Medicine.is_prn.is_(False), Dose.scheduled_at >= lo, Dose.scheduled_at <= hi).all())
    prefs: dict = {}
    sent = 0
    for dose in candidates:
        med = dose.medicine
        if any(d.id != dose.id and d.state in ("pending", "snoozed") and d.scheduled_at > dose.scheduled_at
               for d in med.doses):
            continue                                  # not the last dose of the course
        patient_id = med.prescription.patient_id
        tz = _patient_prefs(db, patient_id, prefs)[0]
        now_local = local_now(tz, utc_now)
        days_left = (dose.scheduled_at.date() - now_local.date()).days
        if days_left not in (0, 1) or now_local.hour < COURSE_END_FROM_HOUR:
            continue
        name = med.name or med.raw_text
        when = "today" if days_left == 0 else "tomorrow"
        msg = {"title": f"Last dose of {name} {when}",
               "body": f"Your last scheduled dose of {name} is {when} at {dose.scheduled_at.strftime('%H:%M')}. "
                       "If you think you need more, ask your doctor - don't extend or stop it on your own."}
        sent += _notify(db, patient_id, dose, "course_end", msg, push_fn, wa_fn, wa_on)
    return sent


def notify_new_conflicts(db: Session, patient_id: int, utc_now: Optional[datetime] = None,
                         push_fn: Callable = webpush_service.send, wa_fn: Optional[Callable] = None) -> int:
    """Right after a prescription is confirmed: tell the patient once per medicine
    pair that two medicines need spacing (or a pharmacist's answer). The text is
    deliberately short and generic - the lock screen is not the place for the
    clinical detail; the app shows the rule and its source."""
    import whatsapp_bot
    wa_fn = wa_fn or whatsapp_bot.send_whatsapp_message
    wa_on = whatsapp_bot.is_configured() or wa_fn is not whatsapp_bot.send_whatsapp_message
    data = safety_service.conflicts_for_patient(db, patient_id, utc_now)
    sent, done = 0, set()
    for c in data["conflicts"]:
        if c["kind"] not in ("timing", "interval_unspecified"):
            continue
        pair = tuple(sorted(c["medicine_ids"]))
        key = (c["rule_id"], pair)
        if key in done:
            continue
        done.add(key)
        anchor = db.query(Dose).filter(Dose.medicine_id == pair[0]).order_by(Dose.id).first()
        if anchor is None:
            continue
        msg = {"title": "Check your medicine timing",
               "body": f"{c['medicines'][0]} and {c['medicines'][1]} may need to be taken apart. "
                       "Open SmartPoli to see what the label says."}
        sent += _notify(db, patient_id, anchor, f"conflict:{c['rule_id']}:{pair[0]}-{pair[1]}"[:60], msg,
                        push_fn, wa_fn, wa_on)
    return sent
