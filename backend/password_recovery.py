"""Opt-in email password recovery. Reset secrets never enter request URLs or logs."""

import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from auth import JWT_SECRET, doctor_role_enabled, hash_password, verify_password
from db import PasswordRecovery, SessionLocal, User, get_db_session
from web_security import rate_limit

class RecoveryRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "Please check your details and try again.") from None

        return safe_handler


router = APIRouter(prefix="/auth/password-recovery", tags=["auth"], route_class=RecoveryRoute)
logger = logging.getLogger(__name__)
LINK_LIFETIME = timedelta(minutes=20)
SEND_COOLDOWN = timedelta(minutes=5)
GENERIC_MESSAGE = (
    "If a password reset is available for this email, we’ll send a link. "
    "If you sign in with Google and haven’t set a SmartPoli password, use Continue with Google."
)
INVALID_LINK = "This reset link is invalid, expired, or already used. Request a new one."


@dataclass(frozen=True)
class MailSettings:
    api_key: str
    sender: str
    origin: str
    reply_to: str


def mail_settings() -> MailSettings | None:
    if os.getenv("SMARTPOLI_PASSWORD_RESET_ENABLED") != "1":
        return None
    key = os.getenv("RESEND_API_KEY", "").strip()
    sender = os.getenv("SMARTPOLI_RESET_FROM_EMAIL", "").strip()
    origin = os.getenv("SMARTPOLI_RESET_BASE_URL", "").strip().rstrip("/")
    try:
        url = urlsplit(origin)
    except ValueError:
        return None
    if not (key and sender and url.scheme == "https" and url.hostname and not (
        url.username or url.password or url.path or url.query or url.fragment
    )):
        return None
    return MailSettings(key, sender, origin, os.getenv("SMARTPOLI_RESET_REPLY_TO", "").strip())


def require_mail_settings() -> MailSettings:
    settings = mail_settings()
    if settings is None:
        raise HTTPException(503, "Password reset is unavailable right now.")
    return settings


class ResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class ResetConfirmation(BaseModel):
    token: str = Field(min_length=43, max_length=43)
    password: str = Field(min_length=8, max_length=72)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _credential_stamp(password_hash: str) -> str:
    return hmac.new(JWT_SECRET.encode(), f"password-reset:{password_hash}".encode(), hashlib.sha256).hexdigest()


def _eligible(user: User | None) -> bool:
    return bool(user and user.password_hash and (user.role != "doctor" or doctor_role_enabled()))


def _send_reset(settings: MailSettings, email: str, link: str) -> None:
    payload = {
        "from": settings.sender,
        "to": [email],
        "subject": "Reset your SmartPoli password",
        "text": f"Use this link within 20 minutes to choose a new password:\n\n{link}\n\n"
                "If you didn’t request this, you can ignore this email.",
    }
    if settings.reply_to:
        payload["reply_to"] = settings.reply_to
    response = httpx.post(
        "https://api.resend.com/emails", json=payload,
        headers={"Authorization": f"Bearer {settings.api_key}"}, timeout=10,
    )
    response.raise_for_status()


def _prepare_reset(email: str, settings: MailSettings) -> None:
    try:
        with SessionLocal() as db:
            user = db.query(User).filter(User.email == email).first()
            if not _eligible(user):
                return
            address = user.email
            now = datetime.utcnow()
            token = secrets.token_urlsafe(32)
            values = {
                "token_digest": _digest(token),
                "credential_stamp": _credential_stamp(user.password_hash),
                "requested_at": now,
                "expires_at": now + LINK_LIFETIME,
            }
            insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
            issued = db.execute(insert(PasswordRecovery).values(user_id=user.id, **values).on_conflict_do_update(
                index_elements=[PasswordRecovery.user_id],
                set_=values,
                where=PasswordRecovery.requested_at <= now - SEND_COOLDOWN,
            )).rowcount
            db.commit()
        if issued:
            # Fragments are kept out of HTTP access logs; only the confirmation body carries the secret.
            _send_reset(settings, address, f"{settings.origin}/reset-password#token={token}")
    except Exception:
        # Provider errors can include addresses and message bodies. Log neither.
        logger.error("Password reset preparation or delivery failed")


@router.get("/config")
def recovery_config():
    return {"enabled": mail_settings() is not None}


@router.post("/request", status_code=202, dependencies=[Depends(rate_limit("password-reset-request", 5, 900))])
def request_reset(body: ResetRequest, tasks: BackgroundTasks, settings: MailSettings = Depends(require_mail_settings)):
    # Both account lookup and delivery occur after sending the same public response.
    tasks.add_task(_prepare_reset, body.email.strip().lower(), settings)
    return {"message": GENERIC_MESSAGE}


@router.post("/confirm", dependencies=[
    Depends(require_mail_settings), Depends(rate_limit("password-reset-confirm", 10, 900)),
])
def confirm_reset(body: ResetConfirmation, db: Session = Depends(get_db_session)):
    if len(body.password.encode("utf-8")) > 72:
        raise HTTPException(400, "Password must be at most 72 bytes.")
    digest = _digest(body.token)
    recovery = db.query(PasswordRecovery).filter(
        PasswordRecovery.token_digest == digest, PasswordRecovery.expires_at > datetime.utcnow(),
    ).first()
    user = db.get(User, recovery.user_id) if recovery else None
    if not _eligible(user) or not hmac.compare_digest(recovery.credential_stamp or "", _credential_stamp(user.password_hash)):
        raise HTTPException(400, INVALID_LINK)

    old_hash = user.password_hash
    if verify_password(body.password, old_hash):
        raise HTTPException(400, "Choose a password different from your current one.")
    new_hash = hash_password(body.password)
    now = datetime.utcnow()
    consumed = db.query(PasswordRecovery).filter(
        PasswordRecovery.user_id == user.id, PasswordRecovery.token_digest == digest,
        PasswordRecovery.expires_at > now,
    ).update({
        PasswordRecovery.token_digest: None,
        PasswordRecovery.credential_stamp: None,
        PasswordRecovery.expires_at: None,
        PasswordRecovery.completed_at: now,
    }, synchronize_session=False)
    if not consumed:
        raise HTTPException(400, INVALID_LINK)
    changed = db.query(User).filter(User.id == user.id, User.password_hash == old_hash).update(
        {User.password_hash: new_hash}, synchronize_session=False,
    )
    if not changed:
        db.rollback()
        raise HTTPException(400, INVALID_LINK)
    db.commit()
    return {"message": "Password updated. Your previous password no longer works, and your other sessions have been signed out. Log in with your new password."}
