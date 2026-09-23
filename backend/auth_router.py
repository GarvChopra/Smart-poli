"""
SmartPoli — account endpoints: register, login, "who am I".

No external identity provider, no email delivery — bcrypt + JWT only, so
this still runs with an empty .env (CLAUDE.md section 14).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db import get_db_session, User, Patient
from auth import hash_password, verify_password, create_token, get_current_user, ROLES
from schemas import RegisterRequest, LoginRequest

router = APIRouter(prefix="/auth", tags=["auth"])


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
def login(body: LoginRequest, db: Session = Depends(get_db_session)):
    email = body.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password.")
    return {"token": create_token(user), "user": _serialize_user(user, db)}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db_session)):
    return _serialize_user(user, db)
