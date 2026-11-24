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
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import care_push
from auth import PERSON_ROLES, gen_link_code, require_role
from db import AuditLog, CaregiverLink, FamilyInvite, Patient, User, get_db_session
from public_urls import public_base_url
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


def _serialize(inv: FamilyInvite, origin: Optional[str] = None) -> dict:
    expired = inv.status == "pending" and inv.expires_at < datetime.utcnow()
    out = {"id": inv.id, "code": pretty(inv.code), "nickname": inv.nickname,
           "status": "expired" if expired else inv.status, "expires_at": inv.expires_at.isoformat() + "Z",
           "accepted_at": inv.accepted_at.isoformat() + "Z" if inv.accepted_at else None}
    if origin is not None:
        out["url"] = f"{origin}/join/{inv.code}"
    return out


@router.post("/family/invites")
def create_invite(body: InviteCreate, user: User = Depends(require_person),
                  db: Session = Depends(get_db_session), _rl=Depends(rate_limit("family-invite", 30, 3600))):
    origin = public_base_url()
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
    return _serialize(inv, origin)


@router.get("/family/invites")
def my_invites(user: User = Depends(require_person), db: Session = Depends(get_db_session)):
    origin = public_base_url()
    rows = (db.query(FamilyInvite).filter(FamilyInvite.from_user_id == user.id)
            .order_by(FamilyInvite.created_at.desc()).limit(30).all())
    return [_serialize(i, origin) for i in rows]


@router.post("/family/invites/{invite_id}/cancel")
def cancel_invite(invite_id: int, user: User = Depends(require_person), db: Session = Depends(get_db_session)):
    own_invite = db.query(FamilyInvite).filter(FamilyInvite.id == invite_id, FamilyInvite.from_user_id == user.id)
    inv = own_invite.first()
    if inv is None:
        raise HTTPException(404, "No such invite.")
    # A concurrent accept must not be overwritten by a stale pending read.
    own_invite.filter(FamilyInvite.status == "pending").update(
        {FamilyInvite.status: "cancelled"}, synchronize_session=False,
    )
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

    # Serialize accepts for this account on PostgreSQL, including distinct
    # invites that could otherwise create duplicate profiles. NO KEY UPDATE
    # is compatible with the locks taken by other foreign-key checks.
    if db.query(User.id).filter(User.id == user.id).with_for_update(key_share=True).scalar() is None:
        raise HTTPException(401, "This account no longer exists.")

    # Claim the code in the same transaction as creating the link or profile.
    # The pending check also serializes the claim against cancellation.
    now = datetime.utcnow()
    claimed = db.query(FamilyInvite).filter(
        FamilyInvite.id == inv.id, FamilyInvite.status == "pending", FamilyInvite.expires_at >= now,
    ).update({
        FamilyInvite.status: "accepted", FamilyInvite.accepted_by_user_id: user.id,
        FamilyInvite.accepted_at: now,
    }, synchronize_session=False)
    if not claimed:
        db.expire_all()
        _open_invite(db, code)  # Preserve the ordinary used, expired and cancelled errors.
        raise HTTPException(409, "This invite is no longer available.")

    patient = db.query(Patient).filter(Patient.user_id == user.id).order_by(Patient.id).first()
    if patient is None:                                   # first time: their own profile ("You") is created now
        patient = Patient(user_id=user.id, name=user.name)
        db.add(patient)
        db.flush()
        db.add(AuditLog(patient_id=patient.id, actor=f"user:{user.id}", action="patient_created", detail=user.name))
    link = db.query(CaregiverLink).filter(CaregiverLink.patient_id == patient.id,
                                          CaregiverLink.caregiver_user_id == inv.from_user_id).first()
    if link is None:
        link = CaregiverLink(patient_id=patient.id, caregiver_user_id=inv.from_user_id, code=gen_link_code(),
                             status="active", accepted_at=now, nickname=inv.nickname)
        db.add(link)
    else:
        link.status, link.accepted_at = "active", now
        link.nickname = link.nickname or inv.nickname
    db.add(AuditLog(patient_id=patient.id, actor=f"user:{user.id}", action="family_invite_accepted",
                    detail=f"caregiver user {inv.from_user_id} (invite {inv.id})"))
    db.commit()
    try:                                                  # tell the person who invited that it worked
        care_push.push_to_user(db, inv.from_user_id, f"{user.name} joined your family",
                               "You can now see their medicines.", tag=f"family-joined-{inv.id}", url="/caregiver")
    except Exception:  # noqa: BLE001 - a push problem must never undo a confirmed link
        logger.exception("family-joined notice failed")
    inviter = db.query(User).filter(User.id == inv.from_user_id).first()
    return {"joined": True, "patient_id": patient.id, "caregiver": inviter.name if inviter else None}
