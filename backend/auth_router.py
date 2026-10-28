"""
SmartPoli — account endpoints: register, login, "who am I".

No email delivery — bcrypt + JWT, so this still runs with an empty .env
(CLAUDE.md section 14). "Continue with Google" is optional: it only appears
when SMARTPOLI_GOOGLE_CLIENT_ID is set (google_auth.py).
"""

import os
import time
from typing import Optional

from web_security import rate_limit
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from db import get_db_session, User, Patient
from auth import hash_password, verify_password, create_token, get_current_user, revoke_token, ROLES, PERSON_ROLES, doctor_role_enabled
from schemas import RegisterRequest, LoginRequest, GoogleLoginRequest, DeleteAccountRequest, SetPasswordRequest
import google_auth

router = APIRouter(prefix="/auth", tags=["auth"])

# --- brute-force protection -------------------------------------------------
# Too many wrong passwords for one (email, client address) in a window -> 429.
# In-memory and per process: fine for a single instance (the Render free
# plan); with several workers each keeps its own count, so put a shared store
# (Redis) or a proxy-level limit in front when scaling out.
MAX_FAILED_LOGINS = 8
LOCKOUT_WINDOW_SECONDS = 15 * 60
_failed_logins: dict = {}
# A real bcrypt hash to compare against when the email is unknown, so "no such
# account" takes as long as "wrong password" (no account-existence timing leak).
_DUMMY_HASH = hash_password("not-a-real-password")


def _throttle_key(request: Request, email: str) -> tuple:
    return (email, request.client.host if request.client else "unknown")


def _recent_failures(key: tuple, now: float) -> list:
    recent = [t for t in _failed_logins.get(key, []) if now - t < LOCKOUT_WINDOW_SECONDS]
    if recent:
        _failed_logins[key] = recent
    else:
        _failed_logins.pop(key, None)
    return recent


def doctor_signup_open() -> bool:
    """Doctor sign-up follows the doctor role, which is switched off for now (auth.doctor_role_enabled)."""
    return doctor_role_enabled()


def _serialize_user(user: User, db: Session) -> dict:
    out = {"id": user.id, "email": user.email, "name": user.name, "role": user.role,
           "has_password": bool(user.password_hash)}          # lets "Delete my account" ask for the password, or the email for Google accounts
    if user.role in PERSON_ROLES:
        out["patient_ids"] = [p.id for p in db.query(Patient).filter(Patient.user_id == user.id).all()]
    return out


@router.post("/register")
def register(body: RegisterRequest, db: Session = Depends(get_db_session), _rl=Depends(rate_limit("register", 10, 3600))):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email is required.")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if len(body.password.encode("utf-8")) > 72:          # bcrypt's limit: longer would be cut short, or crash
        raise HTTPException(400, "Password must be at most 72 characters.")
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of: {', '.join(ROLES)}")
    if body.role == "doctor" and not doctor_signup_open():
        raise HTTPException(400, "Doctor accounts are not open yet. Sign up as a patient or caregiver.")
    existing = find_account_for_email(db, email)
    if existing:
        if not existing.password_hash:       # made with Google: the same account, it just has no password yet
            raise HTTPException(409, "This email already has a SmartPoli account made with Google. Tap Continue with Google, "
                                     "then choose Set a password in Settings to also log in with a password.")
        raise HTTPException(409, "An account with this email already exists.")

    user = User(email=email, password_hash=hash_password(body.password), role=body.role, name=body.name.strip())
    db.add(user)
    db.commit()

    return {"token": create_token(user, body.remember), "user": _serialize_user(user, db)}


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db_session),
          _rl=Depends(rate_limit("login", 30, 300))):
    """Two limits: wrong passwords for one email (lockout below), and attempts from one device across MANY emails."""
    email = body.email.strip().lower()
    key, now = _throttle_key(request, email), time.time()
    recent = _recent_failures(key, now)
    if len(recent) >= MAX_FAILED_LOGINS:
        retry = int(LOCKOUT_WINDOW_SECONDS - (now - recent[0])) + 1
        raise HTTPException(429, "Too many failed attempts. Please wait a few minutes and try again.",
                            headers={"Retry-After": str(retry)})
    user = db.query(User).filter(User.email == email).first()
    valid = verify_password(body.password, user.password_hash if user and user.password_hash else _DUMMY_HASH)
    if not user or not user.password_hash or not valid:
        _failed_logins.setdefault(key, []).append(now)
        raise HTTPException(401, "Incorrect email or password.")
    _failed_logins.pop(key, None)
    if user.role == "doctor" and not doctor_role_enabled():
        raise HTTPException(403, "Doctor accounts are switched off for now. SmartPoli is for patients and caregivers.")
    return {"token": create_token(user, body.remember), "user": _serialize_user(user, db)}


@router.get("/google/config")
def google_config():
    """Whether to show "Continue with Google", and the (public) client id the button needs."""
    return {"enabled": google_auth.is_enabled(), "client_id": google_auth.client_id() or None}


def find_account_for_email(db: Session, email: str) -> Optional[User]:
    """The account that exact email belongs to (capital letters do not matter). A different email is a different account,
    always: nothing is merged by guessing that two spellings are the same inbox."""
    from sqlalchemy import func
    return db.query(User).filter(func.lower(User.email) == (email or "").strip().lower()).first()


@router.post("/google")
def google_login(body: GoogleLoginRequest, db: Session = Depends(get_db_session),
                 _rl=Depends(rate_limit("google", 20, 300))):
    """Sign in (or sign up) with a Google ID token the server verifies itself. An existing account with the same,
    Google-verified email is opened. Someone new gets a regular account: their own medicines, and family if they add any."""
    try:
        info = google_auth.verify_id_token(body.credential)
    except google_auth.GoogleTokenError as e:
        raise HTTPException(401, str(e))
    user = find_account_for_email(db, info["email"])
    if user is None:
        role = body.role or "both"
        if role not in ("both", "patient", "caregiver") and not (role == "doctor" and doctor_signup_open()):
            raise HTTPException(400, "That account type is not available.")
        user = User(email=info["email"], password_hash=None, role=role, name=info["name"])
        db.add(user)
        db.commit()
    elif user.role == "doctor" and not doctor_role_enabled():
        raise HTTPException(403, "Doctor accounts are switched off for now. SmartPoli is for patients and caregivers.")
    return {"token": create_token(user, body.remember), "user": _serialize_user(user, db)}


@router.post("/refresh")
def refresh(authorization: Optional[str] = Header(None), user: User = Depends(get_current_user),
            db: Session = Depends(get_db_session), _rl=Depends(rate_limit("refresh", 60, 3600))):
    """A fresh session of the same kind ("Remember me" or not) for someone who is signed in. The app asks for one once a
    session is a third used up, so a person who keeps using SmartPoli is not signed out; the old token simply runs out."""
    from auth import _decode_token
    claims = _decode_token(authorization.split(" ", 1)[1].strip())
    return {"token": create_token(user, bool(claims.get("rem", True))), "user": _serialize_user(user, db)}


@router.post("/set-password")
def set_password(body: SetPasswordRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db_session),
                 _rl=Depends(rate_limit("set-password", 10, 3600))):
    """Give the signed-in account a password (an account made with Google has none), or change it. Same account, same data:
    afterwards the email + password form and Continue with Google both open it. Changing an existing password needs the
    current one."""
    if len(body.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if len(body.new_password.encode("utf-8")) > 72:
        raise HTTPException(400, "Password must be at most 72 characters.")
    if user.password_hash:
        if not body.current_password or not verify_password(body.current_password, user.password_hash):
            raise HTTPException(403, "Your current password is not right.")
    elif not user.email:
        raise HTTPException(400, "This account has no email address to log in with.")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True, "has_password": True}


@router.post("/delete-account")
def delete_account(body: DeleteAccountRequest, authorization: Optional[str] = Header(None),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db_session),
                   _rl=Depends(rate_limit("delete-account", 5, 3600))):
    """Delete the signed-in person's account and everything of theirs. Cannot be undone. Proves it is really them first:
    the password, or (an account made with Google has none) their email typed out. People they looked after are not
    deleted - only this person's link to them goes; people who looked after them lose access with the data."""
    import logging
    import account_deletion
    if user.password_hash:
        if not body.password or not verify_password(body.password, user.password_hash):
            raise HTTPException(403, "That password is not right.")
    else:
        typed = (body.confirm_email or "").strip().lower()
        expected = (user.email or "DELETE").lower()
        if not typed or typed != expected:
            raise HTTPException(403, "Type your email address exactly to confirm." if user.email else 'Type DELETE to confirm.')
    try:
        revoke_token(db, authorization.split(" ", 1)[1].strip())
        account_deletion.delete_account(db, user.id)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("account deletion failed for user %s", user.id)
        raise HTTPException(500, "We could not delete the account, and nothing was removed. Please try again.")
    return {"deleted": True}


@router.post("/logout")
def logout(authorization: Optional[str] = Header(None), user: User = Depends(get_current_user),
           db: Session = Depends(get_db_session)):
    """Revoke this session's token server-side, so it stops working even if
    someone copied it. (The client also forgets it.)"""
    revoke_token(db, authorization.split(" ", 1)[1].strip())
    return {"logged_out": True}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db_session)):
    return _serialize_user(user, db)
