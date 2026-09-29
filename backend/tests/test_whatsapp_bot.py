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
    monkeypatch.setattr(whatsapp_bot, "read_prescription_image", lambda image_bytes: [
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
        from serializers import emergency_card_data
        card_path = emergency_card_data(db, patient.id)["card_path"]
        assert card_path != f"/emergency/{patient.id}"
        assert card_path in reply
    finally:
        db.close()


def test_webhook_rejects_bad_signature():
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        r = client.post("/whatsapp/webhook", data={"From": "whatsapp:+15550005555", "Body": "hi"})
        assert r.status_code in (403, 503)  # 503 if TWILIO_* env vars aren't set at all in this test env


def test_a_brand_new_users_first_message_being_a_photo_is_not_lost(monkeypatch):
    """Twilio never resends media: if onboarding silently drops the photo
    while it asks 'what's your name?', that prescription is gone for good."""
    monkeypatch.setattr(whatsapp_bot, "_download_media", lambda url: b"fake-image-bytes")
    monkeypatch.setattr(whatsapp_bot, "read_prescription_image", lambda image_bytes: [
        {"text": "Tab Dolo 650mg 1-0-1 PC x5d", "confidence": 0.95},
    ])

    db = SessionLocal()
    try:
        phone = "+15550004444"
        reply = whatsapp_bot.handle_incoming_message(db, phone, "", media_url="https://api.twilio.com/fake.jpg")
        assert "Dolo" in reply
        assert "CONFIRM" in reply

        session = db.query(WhatsAppSession).filter(WhatsAppSession.phone == phone).first()
        assert session.state == "ready"
        assert session.patient_id is not None
    finally:
        db.close()


def test_whatsapp_join_link_prefills_the_sandbox_join_message(monkeypatch):
    monkeypatch.setattr(whatsapp_bot, "TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
    monkeypatch.setattr(whatsapp_bot, "WHATSAPP_JOIN_CODE", "feet-cheese")
    link = whatsapp_bot.whatsapp_join_link()
    assert link == "https://wa.me/14155238886?text=join%20feet-cheese"


def test_whatsapp_join_link_is_none_without_a_configured_number(monkeypatch):
    monkeypatch.setattr(whatsapp_bot, "TWILIO_WHATSAPP_NUMBER", None)
    assert whatsapp_bot.whatsapp_join_link() is None


def test_whatsapp_available_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    monkeypatch.setattr(whatsapp_bot, "TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setattr(whatsapp_bot, "TWILIO_AUTH_TOKEN", "test_token")
    monkeypatch.setattr(whatsapp_bot, "TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
    monkeypatch.setattr(whatsapp_bot, "WHATSAPP_JOIN_CODE", "feet-cheese")
    with TestClient(app) as client:
        body = client.get("/whatsapp/available").json()
    assert body == {"available": True, "link": "https://wa.me/14155238886?text=join%20feet-cheese"}


def test_download_media_follows_twilios_redirect_to_its_cdn(monkeypatch):
    """Confirmed via production logs: Twilio's media URL 307-redirects to
    mms.twiliocdn.com, and httpx.Client doesn't follow redirects unless
    told to -- every real WhatsApp photo was failing to download."""
    import httpx as httpx_module

    def handler(request):
        if "twiliocdn" in str(request.url):
            return httpx_module.Response(200, content=b"real-image-bytes")
        return httpx_module.Response(307, headers={"Location": "https://mms.twiliocdn.com/fake-file"})

    monkeypatch.setattr(whatsapp_bot, "TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setattr(whatsapp_bot, "TWILIO_AUTH_TOKEN", "test_token")
    real_client = httpx_module.Client
    monkeypatch.setattr(httpx_module, "Client",
                        lambda **kw: real_client(transport=httpx_module.MockTransport(handler), **kw))

    result = whatsapp_bot._download_media("https://api.twilio.com/fake/Media/XYZ")
    assert result == b"real-image-bytes"


def test_photo_gets_an_instant_ack_and_the_real_reply_arrives_as_a_follow_up(monkeypatch):
    """Twilio's messaging webhook gives a hard 15s response ceiling and
    retries (silently, to the patient) on timeout -- confirmed in production
    that real OCR (12s+ preprocessing alone on Render's free tier) lost that
    race. The webhook must ack instantly and deliver the real read via a
    separate outbound message instead of the synchronous TwiML reply."""
    import whatsapp_router
    from fastapi.testclient import TestClient
    from main import app

    monkeypatch.setattr(whatsapp_router, "_SKIP_SIGNATURE", True)
    monkeypatch.setattr(whatsapp_bot, "_download_media", lambda url: b"fake-image-bytes")
    monkeypatch.setattr(whatsapp_bot, "read_prescription_image", lambda image_bytes: [
        {"text": "Tab Dolo 650mg 1-0-1 PC x5d", "confidence": 0.95},
    ])
    sent = []
    monkeypatch.setattr(whatsapp_bot, "send_whatsapp_message",
                        lambda to_phone, body: sent.append((to_phone, body)) or True)

    with TestClient(app) as client:
        r = client.post("/whatsapp/webhook", data={
            "From": "whatsapp:+15550006666", "Body": "",
            "NumMedia": "1", "MediaUrl0": "https://api.twilio.com/fake.jpg",
            "MediaContentType0": "image/jpeg",
        })
    assert r.status_code == 200
    assert "reading it now" in r.text.lower()
    assert "Dolo" not in r.text  # the real read never blocks the webhook response

    assert len(sent) == 1
    to_phone, reply = sent[0]
    assert to_phone == "+15550006666"
    assert "Dolo" in reply
    assert "CONFIRM" in reply


def test_send_whatsapp_message_truncates_body_over_twilios_length_limit(monkeypatch):
    """Confirmed in production: a real prescription photo with many lines
    produces a summary long enough that Twilio's outbound API rejects it
    with a 400 (the WhatsApp body length limit is 1600 chars) -- the ack
    already arrived, so the patient was left with no result at all and no
    error either. Truncate before sending rather than let Twilio reject it."""
    import httpx as httpx_module

    monkeypatch.setattr(whatsapp_bot, "TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setattr(whatsapp_bot, "TWILIO_AUTH_TOKEN", "test_token")
    monkeypatch.setattr(whatsapp_bot, "TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

    posted = {}

    def handler(request):
        posted["body"] = dict(x.split("=") for x in request.content.decode().split("&"))
        return httpx_module.Response(201, json={"sid": "SM123"})

    real_client = httpx_module.Client
    monkeypatch.setattr(httpx_module, "Client",
                        lambda **kw: real_client(transport=httpx_module.MockTransport(handler), **kw))

    ok = whatsapp_bot.send_whatsapp_message("+15550006666", "A" * 3000)
    assert ok is True
    from urllib.parse import unquote_plus
    sent_body = unquote_plus(posted["body"]["Body"])
    assert len(sent_body) <= 1600
    assert sent_body.endswith("(truncated — see the app for full details)")


def test_send_whatsapp_message_logs_twilios_error_body_on_failure(monkeypatch, caplog):
    """The old failure log only had httpx's generic 'Client error 400' with
    no Twilio error code or message, which is exactly why the earlier
    corrupted-number bug (error 21212) took a local repro script to
    diagnose instead of being readable straight from the logs."""
    import httpx as httpx_module

    monkeypatch.setattr(whatsapp_bot, "TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setattr(whatsapp_bot, "TWILIO_AUTH_TOKEN", "test_token")
    monkeypatch.setattr(whatsapp_bot, "TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

    def handler(request):
        return httpx_module.Response(400, json={"code": 21617, "message": "Message body exceeds limit"})

    real_client = httpx_module.Client
    monkeypatch.setattr(httpx_module, "Client",
                        lambda **kw: real_client(transport=httpx_module.MockTransport(handler), **kw))

    with caplog.at_level("WARNING"):
        ok = whatsapp_bot.send_whatsapp_message("+15550006666", "hi")
    assert ok is False
    assert "21617" in caplog.text
    assert "Message body exceeds limit" in caplog.text
