"""
SmartPoli — WhatsApp bot (Twilio).

A patient can do almost the entire manual path — get an account, send a
prescription photo, get it decoded, opt into reminders, and pull up their
emergency card — without ever opening the web app. Nothing here is a
second implementation of any of that: every step calls straight into the
same modules main.py's own endpoints use (prescription_service.py,
ocr_plugin.py, scheduler.py, serializers.py) — this module is only the
conversation state machine and the WhatsApp-shaped request/response glue
around them.

Runs with zero API keys if Twilio isn't configured: is_configured() is
False, the router refuses the webhook with a clear 503 instead of crashing
(same "manual path always works" instinct as ocr_plugin.py — CLAUDE.md
section 5), and every other SmartPoli feature is completely unaffected.
"""

import logging
import os
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from db import User, Patient, Prescription, WhatsAppSession, log_audit
from auth import create_token
from prescription_service import create_prescription_from_lines
from scheduler import generate_doses, SchedulingBlocked
from serializers import emergency_card_data, serialize_medicine
from ocr_plugin import run_ocr_on_image, OCRUnavailable

logger = logging.getLogger(__name__)

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")  # e.g. "whatsapp:+14155238886"

# Public base URL this server is reachable at (for the emergency-card link
# and web-dashboard magic link sent back over WhatsApp). Twilio's sandbox
# itself needs its OWN separate public tunnel to reach US — see
# WHATSAPP_SETUP.md — this is only for links WE send THEM.
PUBLIC_BASE_URL = os.getenv("SMARTPOLI_PUBLIC_BASE_URL", "http://127.0.0.1:8000")


def is_configured() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER)


WELCOME_MESSAGE = (
    "👋💊 *Welcome to SmartPoli!*\n\n"
    "I'm your medication assistant. I can:\n"
    "📸 Read a *photo of your prescription* and turn it into a clear, "
    "plain-language schedule\n"
    "⏰ Send you reminders when a dose is due\n"
    "🚨 Give you a quick *emergency card* link with your allergies and "
    "current medicines\n\n"
    "Nothing here is a diagnosis — just help reading and following what "
    "your doctor already prescribed.\n\n"
    "First, what's your name? I'll set up your SmartPoli account with "
    "this WhatsApp number."
)

MENU_MESSAGE = (
    "Here's what I can do:\n"
    "📸 Send a *photo* of a prescription — I'll read it\n"
    "✅ Reply *CONFIRM* to schedule the last prescription I read you\n"
    "🔔 Reply *NOTIFY YES* or *NOTIFY NO* for dose reminders\n"
    "🚨 Reply *EMERGENCY* for your emergency card link\n"
    "📅 Reply *TODAY* for today's medicines\n"
    "❓ Reply *HELP* to see this again"
)


def _get_or_create_session(db: Session, phone: str) -> WhatsAppSession:
    session = db.query(WhatsAppSession).filter(WhatsAppSession.phone == phone).first()
    if session:
        return session
    session = WhatsAppSession(phone=phone, state="new")
    db.add(session)
    db.commit()
    return session


def _slot_label(scheduled_at: datetime) -> str:
    hour = scheduled_at.hour
    if hour < 11:
        return "morning"
    if hour < 16:
        return "afternoon"
    if hour < 19:
        return "evening"
    return "night"


def _format_medicine_summary(medicines: list[tuple]) -> str:
    lines = []
    for medicine, parsed in medicines:
        status = parsed["status"]
        icon = {"verified": "✅", "review": "⚠️", "needs_confirmation": "❌"}.get(status, "❓")
        name = medicine.name or medicine.raw_text
        lines.append(f"{icon} *{name}* {medicine.dose_amount or ''}{medicine.dose_unit or ''}")
        if status == "needs_confirmation":
            lines.append(f'   Could not read confidently: "{medicine.raw_text}" — please confirm on the web app.')
        else:
            plain = parsed.get("plain_language") or ""
            for line in plain.splitlines():
                if line.strip():
                    lines.append(f"   {line.strip()}")
    return "\n".join(lines)


def _download_media(media_url: str) -> Optional[bytes]:
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
        return None
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
            r.raise_for_status()
            return r.content
    except Exception as e:
        logger.warning(f"WhatsApp media download failed: {e}")
        return None


def handle_incoming_message(
    db: Session, from_phone: str, body: str,
    media_url: Optional[str] = None, media_content_type: Optional[str] = None,
) -> str:
    """
    Returns the plain-text reply to send back. `from_phone` is already
    normalized to bare E.164 (the "whatsapp:" prefix stripped by the
    router before this is called).
    """
    body = (body or "").strip()
    session = _get_or_create_session(db, from_phone)

    # ---------------------------------------------------------- onboarding
    if session.state == "new":
        session.state = "awaiting_name"
        db.commit()
        return WELCOME_MESSAGE

    if session.state == "awaiting_name":
        name = body[:120] if body else None
        if not name:
            return "I didn't catch a name there — what should I call you?"

        user = User(name=name, phone=from_phone, role="patient", password_hash=None, email=None)
        db.add(user)
        db.commit()
        patient = Patient(user_id=user.id, name=name)
        db.add(patient)
        db.commit()
        log_audit(db, patient.id, f"whatsapp:{from_phone}", "patient_created", f"{name} (via WhatsApp)")

        session.user_id = user.id
        session.patient_id = patient.id
        session.state = "ready"
        db.commit()

        return (
            f"Nice to meet you, {name}! ✅ Your SmartPoli account is ready.\n\n"
            f"{MENU_MESSAGE}"
        )

    # ---------------------------------------------------------- steady state
    patient_id = session.patient_id
    text = body.lower()

    if media_url:
        return _handle_prescription_photo(db, session, patient_id, media_url, media_content_type)

    if text in ("confirm", "yes confirm", "schedule"):
        return _handle_confirm(db, session, patient_id)

    if "notify yes" in text or text == "notify":
        session.notifications_opt_in = True
        db.commit()
        return "🔔 Reminders are on — I'll message you when a dose is due or overdue."

    if "notify no" in text:
        session.notifications_opt_in = False
        db.commit()
        return "🔕 Reminders are off. Reply NOTIFY YES any time to turn them back on."

    if "emergency" in text:
        return _handle_emergency(db, patient_id)

    if text in ("today", "schedule today", "doses"):
        return _handle_today(db, patient_id)

    if text in ("help", "menu", "hi", "hello"):
        return MENU_MESSAGE

    return (
        "I didn't quite catch that. Send a *photo* of a prescription any time, "
        f"or here's what else I can do:\n\n{MENU_MESSAGE}"
    )


def _handle_prescription_photo(
    db: Session, session: WhatsAppSession, patient_id: int,
    media_url: str, media_content_type: Optional[str],
) -> str:
    image_bytes = _download_media(media_url)
    if not image_bytes:
        return "I couldn't download that image — please try sending the photo again."

    try:
        ocr_lines = run_ocr_on_image(image_bytes)
    except OCRUnavailable as e:
        return f"I couldn't read that photo right now ({e}). You can also type the prescription as plain text."

    if not ocr_lines:
        return "I couldn't find any readable text in that photo. Try a clearer, well-lit picture, one line per medicine."

    prescription, medicines = create_prescription_from_lines(
        db, patient_id, None, None,
        [(line["text"], line["confidence"]) for line in ocr_lines], source="whatsapp",
        actor=f"whatsapp:{session.phone}",
    )
    summary = _format_medicine_summary(medicines)
    blocked = [m for m, p in medicines if p["status"] == "needs_confirmation"]
    footer = "\n\nReply *CONFIRM* to schedule these and start reminders."
    if blocked:
        footer += f"\n⚠️ {len(blocked)} line(s) need manual confirmation on the web app before they can be scheduled."

    return f"📋 Here's what I read:\n\n{summary}{footer}"


def _handle_confirm(db: Session, session: WhatsAppSession, patient_id: int) -> str:
    prescription = (
        db.query(Prescription)
        .filter(Prescription.patient_id == patient_id, Prescription.status == "draft")
        .order_by(Prescription.created_at.desc())
        .first()
    )
    if not prescription:
        return "There's no unconfirmed prescription to schedule — send a photo first."

    medicines = prescription.medicines
    scheduled, blocked, prn = [], [], []
    for medicine in medicines:
        if medicine.status == "needs_confirmation":
            blocked.append(medicine)
            continue
        try:
            doses = generate_doses(medicine)
        except SchedulingBlocked:
            blocked.append(medicine)
            continue
        if medicine.is_prn:
            prn.append(medicine)
            continue
        db.add_all(doses)
        scheduled.append(medicine)

    prescription.status = "confirmed"
    db.commit()
    log_audit(db, patient_id, f"whatsapp:{session.phone}", "prescription_confirmed",
              f"scheduled={len(scheduled)} blocked={len(blocked)} prn={len(prn)} (via WhatsApp)")

    lines = [f"✅ Scheduled {len(scheduled)} medicine(s)."]
    if prn:
        lines.append(f"💊 {len(prn)} marked as-needed — no fixed times, just log when you take them.")
    if blocked:
        lines.append(f"⚠️ {len(blocked)} still need confirmation on the web app.")
    if session.notifications_opt_in:
        lines.append("🔔 I'll remind you when each dose is due.")
    else:
        lines.append("Reply NOTIFY YES if you'd like reminders for these.")
    return "\n".join(lines)


def _handle_emergency(db: Session, patient_id: int) -> str:
    data = emergency_card_data(db, patient_id)
    p = data["patient"]
    url = f"{PUBLIC_BASE_URL}/emergency/{patient_id}"
    lines = [
        "🚨 *Emergency card*",
        f"{p['name']}" + (f", {p['age']}" if p["age"] else "") + (f", {p['sex']}" if p["sex"] else ""),
        f"Allergies: {p['allergies'] or 'none recorded'}",
        f"Emergency contact: {p['emergency_contact'] or 'none recorded'}",
        "",
        f"Full card (no login needed): {url}",
    ]
    return "\n".join(lines)


def _handle_today(db: Session, patient_id: int) -> str:
    from scheduler import sweep_missed
    sweep_missed(db)
    prescriptions = db.query(Prescription).filter(Prescription.patient_id == patient_id).all()
    now = datetime.utcnow()
    today_doses = [
        (m, d) for pres in prescriptions for m in pres.medicines for d in m.doses
        if d.scheduled_at.date() == now.date()
    ]
    if not today_doses:
        return "Nothing scheduled for today. Send a prescription photo to get started!"
    today_doses.sort(key=lambda pair: pair[1].scheduled_at)
    lines = ["📅 *Today's medicines*"]
    for medicine, dose in today_doses:
        icon = {"taken": "✅", "missed": "❌", "pending": "⏳", "snoozed": "💤", "skipped": "⏭️"}.get(dose.state, "•")
        name = medicine.name or medicine.raw_text
        lines.append(f"{icon} {dose.scheduled_at.strftime('%H:%M')} — {name} ({dose.state})")
    return "\n".join(lines)


def send_whatsapp_message(to_phone: str, body: str) -> bool:
    """Outbound send via Twilio's REST API directly (httpx, no SDK) — used
    for reminders, which aren't a reply to an inbound webhook so there's no
    TwiML response to piggyback on. Returns False on any failure; callers
    must never let a failed send block or crash the reminder sweep."""
    if not is_configured():
        return False
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                url,
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                data={"From": TWILIO_WHATSAPP_NUMBER, "To": f"whatsapp:{to_phone}", "Body": body},
            )
            r.raise_for_status()
        return True
    except Exception as e:
        logger.warning(f"WhatsApp outbound send failed to {to_phone}: {e}")
        return False


def send_due_dose_reminders(db: Session) -> int:
    """
    The WhatsApp half of Feature 2's reminders (CLAUDE.md section 9) —
    parallels main.py's _reminder_sweep_job, which marks doses missed. This
    finds doses due within the next 30 minutes for patients who opted in
    (NOTIFY YES), sends one WhatsApp message per dose, and marks it sent —
    at most one reminder per dose, ever, so a 5-minute sweep interval can
    never spam the same dose six times before it's due.
    """
    from datetime import timedelta
    from db import Dose

    if not is_configured():
        return 0

    now = datetime.utcnow()
    window_end = now + timedelta(minutes=30)

    due_soon = (
        db.query(Dose)
        .join(Dose.medicine)
        .filter(Dose.state == "pending", Dose.whatsapp_reminder_sent.is_(False))
        .filter(Dose.scheduled_at >= now, Dose.scheduled_at <= window_end)
        .all()
    )

    sent = 0
    for dose in due_soon:
        patient_id = dose.medicine.prescription.patient_id
        session = (
            db.query(WhatsAppSession)
            .filter(WhatsAppSession.patient_id == patient_id, WhatsAppSession.notifications_opt_in.is_(True))
            .first()
        )
        if not session:
            continue
        medicine_name = dose.medicine.name or dose.medicine.raw_text
        when = dose.scheduled_at.strftime("%H:%M")
        body = f"⏰ Reminder: your {when} dose of *{medicine_name}* is coming up. Reply once you've taken it if you'd like — I'm tracking your adherence."
        if send_whatsapp_message(session.phone, body):
            dose.whatsapp_reminder_sent = True
            sent += 1
    if sent:
        db.commit()
    return sent


def web_dashboard_link(user: User) -> str:
    """A magic link so a WhatsApp-only user can review their full record on
    the web without ever setting a password — reuses the same JWT auth.py
    already issues for a normal login, just delivered over chat instead of
    a login form."""
    token = create_token(user)
    return f"{PUBLIC_BASE_URL}/static/index.html?token={token}"
