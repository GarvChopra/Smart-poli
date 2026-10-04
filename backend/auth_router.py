"""
SmartPoli — account endpoints: register, login, "who am I".

No external identity provider, no email delivery — bcrypt + JWT only, so
this still runs with an empty .env (CLAUDE.md section 14).
"""

import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from db import get_db_session, User, Patient
from auth import hash_password, verify_password, create_token, get_current_user, revoke_token, ROLES
from schemas import RegisterRequest, LoginRequest

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


def _serialize_user(user: User, db: Session) -> dict:
    out = {"id": user.id, "email": user.email, "name": user.name, "role": user.role}
    if user.role == "patient":
        out["patient_ids"] = [p.id for p in db.query(Patient).filter(Patient.user_id == user.id).all()]
    return out


@router.post("/register")
def register(body: RegisterRequest, db: Session = Depends(get_db_session)):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email is required.")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of: {', '.join(ROLES)}")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "An account with this email already exists.")

    user = User(email=email, password_hash=hash_password(body.password), role=body.role, name=body.name.strip())
    db.add(user)
    db.commit()

    return {"token": create_token(user), "user": _serialize_user(user, db)}


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db_session)):
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
    return {"token": create_token(user), "user": _serialize_user(user, db)}


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
