"""
SmartPoli - "Add member": invite a family member with a link or a code.

The person adding (Gita) creates an invite and shares the link (WhatsApp, copy) or the code. The invited person (Mum)
opens it, signs in or signs up, and is asked to confirm. Only on that confirmation does an ordinary active
CaregiverLink appear, with Gita as the caregiver and Mum's own profile as the patient - so everything a caregiver may
or may not do is decided in the one place it always was (auth.py, caregiver_router.py).

Safety: an invite is single use, expires after 7 days, can be cancelled, and never grants anything by itself - not
when the link is opened, only when the invited person confirms while signed in. Codes use an alphabet without look-alike
characters and are rate limited per device.
"""

import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import care_push
from auth import PERSON_ROLES, gen_link_code, require_role
from db import CaregiverLink, FamilyInvite, Patient, User, get_db_session, log_audit
from web_security import rate_limit

logger = logging.getLogger(__name__)
router = APIRouter()

INVITE_DAYS = 7
MAX_PENDING_PER_USER = 10
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no 0/O or 1/I: read aloud or off a screenshot without mix-ups
require_person = require_role(*PERSON_ROLES)


class InviteCreate(BaseModel):
    nickname: Optional[str] = Field(None, max_length=30)


def new_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def pretty(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def clean_code(raw: str) -> str:
    return "".join(ch for ch in (raw or "").upper() if ch.isalnum())[:12]


LIVE_URL = "https://smartpoli.onrender.com"


def _is_local(url: str) -> bool:
    return url.split("//", 1)[-1].startswith(("localhost", "127.0.0.1", "[::1]", "0.0.0.0"))


def public_base_url(request: Request) -> str:
    """The address to put in a link someone else will open. Always the real site, never this computer's: the first of
    SMARTPOLI_PUBLIC_URL / SMARTPOLI_PUBLIC_BASE_URL (already set on the live server for WhatsApp links) that is not a
    local address; else the request's own address when it is a hosted one, given as https (behind the host's proxy the
    request looks like plain http); else the live site. A copy running on localhost therefore still shares the live link."""
    for name in ("SMARTPOLI_PUBLIC_URL", "SMARTPOLI_PUBLIC_BASE_URL"):
        configured = (os.getenv(name) or "").strip().rstrip("/")
        if configured and not _is_local(configured):
            return configured
    base = str(request.base_url).rstrip("/")
    if _is_local(base):
        return LIVE_URL
    return "https://" + base.split("//", 1)[-1] if base.startswith("http://") else base


def _serialize(inv: FamilyInvite, request: Optional[Request] = None) -> dict:
    expired = inv.status == "pending" and inv.expires_at < datetime.utcnow()
    out = {"id": inv.id, "code": pretty(inv.code), "nickname": inv.nickname,
           "status": "expired" if expired else inv.status, "expires_at": inv.expires_at.isoformat() + "Z",
           "accepted_at": inv.accepted_at.isoformat() + "Z" if inv.accepted_at else None}
    if request is not None:
        out["url"] = f"{public_base_url(request)}/join/{inv.code}"
    return out


@router.post("/family/invites")
def create_invite(body: InviteCreate, request: Request, user: User = Depends(require_person),
                  db: Session = Depends(get_db_session), _rl=Depends(rate_limit("family-invite", 30, 3600))):
    now = datetime.utcnow()
    pending = db.query(FamilyInvite).filter(FamilyInvite.from_user_id == user.id, FamilyInvite.status == "pending",
                                            FamilyInvite.expires_at > now).count()
    if pending >= MAX_PENDING_PER_USER:
        raise HTTPException(429, "You have a lot of open invites. Cancel some, or wait for them to be used.")
    code = new_code()
    while db.query(FamilyInvite).filter(FamilyInvite.code == code).first():
        code = new_code()
    inv = FamilyInvite(code=code, from_user_id=user.id, nickname=(body.nickname or "").strip() or None,
                       expires_at=now + timedelta(days=INVITE_DAYS))
    db.add(inv)
    db.commit()
    return _serialize(inv, request)


@router.get("/family/invites")
def my_invites(request: Request, user: User = Depends(require_person), db: Session = Depends(get_db_session)):
    rows = (db.query(FamilyInvite).filter(FamilyInvite.from_user_id == user.id)
            .order_by(FamilyInvite.created_at.desc()).limit(30).all())
    return [_serialize(i, request) for i in rows]


@router.post("/family/invites/{invite_id}/cancel")
def cancel_invite(invite_id: int, user: User = Depends(require_person), db: Session = Depends(get_db_session)):
    inv = db.query(FamilyInvite).filter(FamilyInvite.id == invite_id, FamilyInvite.from_user_id == user.id).first()
    if inv is None:
        raise HTTPException(404, "No such invite.")
    if inv.status == "pending":
        inv.status = "cancelled"
        db.commit()
    return _serialize(inv)


def _open_invite(db: Session, raw_code: str) -> FamilyInvite:
    inv = db.query(FamilyInvite).filter(FamilyInvite.code == clean_code(raw_code)).first()
    if inv is None:
        raise HTTPException(404, "That code doesn't match an invite. Check it and try again.")
    if inv.status == "accepted":
        raise HTTPException(409, "This invite has already been used. Ask for a new one.")
    if inv.status == "cancelled":
        raise HTTPException(410, "This invite was cancelled. Ask for a new one.")
    if inv.expires_at < datetime.utcnow():
        raise HTTPException(410, "This invite has expired. Ask for a new one.")
    return inv


@router.get("/family/join/{code}")
def preview_invite(code: str, user: User = Depends(require_person), db: Session = Depends(get_db_session),
                   _rl=Depends(rate_limit("family-code", 30, 3600))):
    """What the invited person sees before confirming: who is asking. Needs a signed-in account, so a stray link
    shows nothing to anyone passing by."""
    inv = _open_invite(db, code)
    inviter = db.query(User).filter(User.id == inv.from_user_id).first()
    return {"inviter_name": inviter.name if inviter else "Someone", "nickname": inv.nickname,
            "own": inv.from_user_id == user.id}


@router.post("/family/join/{code}")
def accept_invite(code: str, user: User = Depends(require_person), db: Session = Depends(get_db_session),
                  _rl=Depends(rate_limit("family-code", 30, 3600))):
    inv = _open_invite(db, code)
    if inv.from_user_id == user.id:
        raise HTTPException(400, "This is your own invite. Send it to the person you are adding.")
    patient = db.query(Patient).filter(Patient.user_id == user.id).order_by(Patient.id).first()
    if patient is None:                                   # first time: their own profile ("You") is created now
        patient = Patient(user_id=user.id, name=user.name)
        db.add(patient)
        db.commit()
        log_audit(db, patient.id, f"user:{user.id}", "patient_created", user.name)
    link = db.query(CaregiverLink).filter(CaregiverLink.patient_id == patient.id,
                                          CaregiverLink.caregiver_user_id == inv.from_user_id).first()
    if link is None:
        link = CaregiverLink(patient_id=patient.id, caregiver_user_id=inv.from_user_id, code=gen_link_code(),
                             status="active", accepted_at=datetime.utcnow(), nickname=inv.nickname)
        db.add(link)
    else:
        link.status, link.accepted_at = "active", datetime.utcnow()
        link.nickname = link.nickname or inv.nickname
    inv.status, inv.accepted_by_user_id, inv.accepted_at = "accepted", user.id, datetime.utcnow()
    db.commit()
    log_audit(db, patient.id, f"user:{user.id}", "family_invite_accepted",
              f"caregiver user {inv.from_user_id} (invite {inv.id})")
    try:                                                  # tell the person who invited that it worked
        care_push.push_to_user(db, inv.from_user_id, f"{user.name} joined your family",
                               "You can now see their medicines.", tag=f"family-joined-{inv.id}", url="/caregiver")
    except Exception:  # noqa: BLE001 - a push problem must never undo a confirmed link
        logger.exception("family-joined notice failed")
    inviter = db.query(User).filter(User.id == inv.from_user_id).first()
    return {"joined": True, "patient_id": patient.id, "caregiver": inviter.name if inviter else None}
