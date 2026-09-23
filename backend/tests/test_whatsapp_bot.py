"""
Tests for the WhatsApp bot conversation flow (whatsapp_bot.py) and the
webhook's Twilio-signature enforcement (whatsapp_router.py). Media/OCR is
monkeypatched exactly the way test_ocr_endpoint.py already does for the
web upload path — this proves the WhatsApp path reuses the SAME
extraction/confidence/scheduling code, not a second implementation of it.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import whatsapp_bot  # noqa: E402
from db import init_db, SessionLocal, User, Patient, WhatsAppSession  # noqa: E402

init_db()  # these tests talk to the DB directly, never through a TestClient(app) startup event


def test_first_contact_asks_for_name_then_creates_account():
    db = SessionLocal()
    try:
        phone = "+15550001111"
        reply1 = whatsapp_bot.handle_incoming_message(db, phone, "hi")
        assert "SmartPoli" in reply1
        assert "name" in reply1.lower()

        session = db.query(WhatsAppSession).filter(WhatsAppSession.phone == phone).first()
        assert session.state == "awaiting_name"

        reply2 = whatsapp_bot.handle_incoming_message(db, phone, "Asha Verma")
        assert "Asha Verma" in reply2
        assert "ready" in reply2.lower() or "account" in reply2.lower()

        db.refresh(session)
        assert session.state == "ready"
        user = db.query(User).filter(User.id == session.user_id).first()
        assert user.name == "Asha Verma"
        assert user.phone == phone
        assert user.role == "patient"
        assert user.password_hash is None  # WhatsApp accounts never set a password

        patient = db.query(Patient).filter(Patient.id == session.patient_id).first()
        assert patient.name == "Asha Verma"
        assert patient.user_id == user.id
    finally:
        db.close()


def test_menu_and_notify_toggle():
    db = SessionLocal()
    try:
        phone = "+15550002222"
        whatsapp_bot.handle_incoming_message(db, phone, "hi")
        whatsapp_bot.handle_incoming_message(db, phone, "Rohan Mehta")

        reply = whatsapp_bot.handle_incoming_message(db, phone, "help")
        assert "EMERGENCY" in reply

        reply = whatsapp_bot.handle_incoming_message(db, phone, "notify yes")
        assert "on" in reply.lower()
        session = db.query(WhatsAppSession).filter(WhatsAppSession.phone == phone).first()
        assert session.notifications_opt_in is True

        reply = whatsapp_bot.handle_incoming_message(db, phone, "notify no")
        assert "off" in reply.lower()
        db.refresh(session)
        assert session.notifications_opt_in is False
    finally:
        db.close()


def test_prescription_photo_then_confirm_schedules_doses(monkeypatch):
    monkeypatch.setattr(whatsapp_bot, "_download_media", lambda url: b"fake-image-bytes")
    monkeypatch.setattr(whatsapp_bot, "run_ocr_on_image", lambda image_bytes: [
        {"text": "Tab Dolo 650mg 1-0-1 PC x5d", "confidence": 0.95},
        {"text": "Tab X 1-?-1", "confidence": 0.9},
    ])

    db = SessionLocal()
    try:
        phone = "+15550003333"
        whatsapp_bot.handle_incoming_message(db, phone, "hi")
        whatsapp_bot.handle_incoming_message(db, phone, "Test Patient")

        reply = whatsapp_bot.handle_incoming_message(db, phone, "", media_url="https://api.twilio.com/fake.jpg")
        assert "Dolo" in reply
        assert "CONFIRM" in reply
        assert "need manual confirmation" in reply  # the malformed 1-?-1 line

        reply = whatsapp_bot.handle_incoming_message(db, phone, "confirm")
        assert "Scheduled 1" in reply

        session = db.query(WhatsAppSession).filter(WhatsAppSession.phone == phone).first()
        patient = db.query(Patient).filter(Patient.id == session.patient_id).first()
        all_doses = [d for pres in patient.prescriptions for m in pres.medicines for d in m.doses]
        assert len(all_doses) == 10  # 2/day x 5 days
    finally:
        db.close()


def test_emergency_reply_includes_allergies_and_link():
    db = SessionLocal()
    try:
        phone = "+15550004444"
        whatsapp_bot.handle_incoming_message(db, phone, "hi")
        whatsapp_bot.handle_incoming_message(db, phone, "Emergency Test")
        session = db.query(WhatsAppSession).filter(WhatsAppSession.phone == phone).first()
        patient = db.query(Patient).filter(Patient.id == session.patient_id).first()
        patient.allergies = "Penicillin"
        db.commit()

        reply = whatsapp_bot.handle_incoming_message(db, phone, "emergency")
        assert "Penicillin" in reply
        assert f"/emergency/{patient.id}" in reply
    finally:
        db.close()


def test_webhook_rejects_bad_signature():
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        r = client.post("/whatsapp/webhook", data={"From": "whatsapp:+15550005555", "Body": "hi"})
        assert r.status_code in (403, 503)  # 503 if TWILIO_* env vars aren't set at all in this test env
