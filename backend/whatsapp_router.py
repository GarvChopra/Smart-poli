"""
SmartPoli — Twilio WhatsApp webhook.

Signature validation is implemented directly against Twilio's documented
algorithm (HMAC-SHA1 over the request URL + sorted form params, base64,
compared to X-Twilio-Signature) using only the stdlib — this is the one
new integration in the whole codebase that talks to an external service by
webhook, so it gets its own explicit auth check rather than pulling in the
`twilio` SDK for one function. See CLAUDE.md section 13: input validation
on every endpoint, and never trust an unauthenticated caller.

Set SMARTPOLI_SKIP_TWILIO_SIGNATURE=1 in .env only for local testing with
curl — never in anything resembling production.
"""

import base64
import hashlib
import hmac
import logging
import os

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse
from typing import Optional

from db import SessionLocal
import whatsapp_bot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

_SKIP_SIGNATURE = os.getenv("SMARTPOLI_SKIP_TWILIO_SIGNATURE") == "1"


def _validate_twilio_signature(url: str, form: dict, signature: Optional[str]) -> bool:
    if _SKIP_SIGNATURE:
        return True
    if not whatsapp_bot.TWILIO_AUTH_TOKEN or not signature:
        return False
    data = url
    for key in sorted(form.keys()):
        data += key + form[key]
    computed = base64.b64encode(
        hmac.new(whatsapp_bot.TWILIO_AUTH_TOKEN.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    ).decode("utf-8")
    return hmac.compare_digest(computed, signature)


def _twiml(message: str) -> str:
    escaped = (
        message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escaped}</Message></Response>'


@router.post("/webhook")
async def whatsapp_webhook(request: Request):
    """
    Twilio POSTs application/x-www-form-urlencoded here on every inbound
    WhatsApp message. Returns TwiML telling Twilio what to reply with —
    no outbound REST call needed for a simple text reply.
    """
    if not whatsapp_bot.is_configured() and not _SKIP_SIGNATURE:
        raise HTTPException(503, "WhatsApp integration is not configured on this server.")

    form_data = await request.form()
    form = {k: str(v) for k, v in form_data.items()}

    signature = request.headers.get("X-Twilio-Signature")
    url = str(request.url)
    if not _validate_twilio_signature(url, form, signature):
        logger.warning("Rejected WhatsApp webhook with invalid Twilio signature.")
        raise HTTPException(403, "Invalid signature.")

    from_raw = form.get("From", "")  # "whatsapp:+919876543210"
    from_phone = from_raw.replace("whatsapp:", "").strip()
    if not from_phone:
        raise HTTPException(400, "Missing From.")

    body = form.get("Body", "")
    num_media = int(form.get("NumMedia", "0") or "0")
    media_url = form.get("MediaUrl0") if num_media > 0 else None
    media_content_type = form.get("MediaContentType0") if num_media > 0 else None

    db = SessionLocal()
    try:
        reply = whatsapp_bot.handle_incoming_message(db, from_phone, body, media_url, media_content_type)
    finally:
        db.close()

    return PlainTextResponse(content=_twiml(reply), media_type="application/xml")
