"""
SmartPoli authentication & role-based authorization.

Real accounts, real password hashing (bcrypt), real signed session tokens
(JWT). Every patient-scoped endpoint in main.py, caregiver_router.py and
doctor_router.py is wired through one of the `require_*` dependencies
below — none of them ever trust a client-supplied role or id. The caller's
identity comes only from a verified token; the caller's *access* to a given
patient_id comes only from a real relationship row (Patient.user_id for a
patient, an active CaregiverLink/DoctorLink for anyone else).

Runs with zero external services — no OAuth provider, no email delivery,
matching CLAUDE.md section 14 ("runs with zero API keys").
"""

import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from db import get_db_session, User, Patient, CaregiverLink, DoctorLink, Medicine, Dose, Prescription, RevokedToken

JWT_SECRET = os.getenv("SMARTPOLI_JWT_SECRET") or "dev-only-insecure-secret-change-me-in-.env"
JWT_ALGO = "HS256"
JWT_TTL_HOURS = 24 * 7                  # the default (WhatsApp links, older callers)
REMEMBER_HOURS = 24 * 30                # "Remember me" ticked: stay signed in on this phone for a month
SESSION_HOURS = 24                      # not ticked: one day

ROLES = ("patient", "caregiver", "both", "doctor")
# A person's account can hold their own medicines AND look after family: the label only says where they landed first,
# never what they may see. Access to anyone else's record still comes only from an active link (see has_read_access).
PERSON_ROLES = ("patient", "caregiver", "both")


def doctor_role_enabled() -> bool:
    """The doctor role is switched off for now: only patients and caregivers. Its code stays and SMARTPOLI_DOCTOR_ROLE=1
    turns it back on (sign-up, the /doctor page and API, and doctors' access to linked patients)."""
    return os.getenv("SMARTPOLI_DOCTOR_ROLE") == "1"


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        # Older bcrypt silently used only the first 72 bytes, so accounts made then still log in the same way.
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- tokens

def create_token(user: User, remember: Optional[bool] = None) -> str:
    """`remember` True/False picks the long/short session; None keeps the default length. Whatever was picked is written
    into the token (`rem`), so a later refresh gives the same kind of session."""
    hours = JWT_TTL_HOURS if remember is None else (REMEMBER_HOURS if remember else SESSION_HOURS)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "email": user.email,
        "rem": True if remember is None else bool(remember),
        "exp": datetime.utcnow() + timedelta(hours=hours),
        "iat": datetime.utcnow(),
        "jti": secrets.token_urlsafe(16),   # lets a logout revoke exactly this session
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def gen_link_code() -> str:
    """Short, shareable, hard-to-guess invite code (patient -> caregiver/doctor)."""
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))     # no 0/O or 1/I


def revoke_token(db: Session, token: str) -> None:
    """Remember a logged-out token until it would have expired anyway."""
    payload = _decode_token(token)
    jti = payload.get("jti")
    if not jti or db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
        return
    db.add(RevokedToken(jti=jti, expires_at=datetime.utcfromtimestamp(payload["exp"])))
    # opportunistic cleanup of rows that no longer matter
    db.query(RevokedToken).filter(RevokedToken.expires_at < datetime.utcnow()).delete(synchronize_session=False)
    db.commit()


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired session — please log in again.")


# ---------------------------------------------------------------- identity dependency

def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db_session),
) -> User:
    """The only place a user's identity is ever established. Everything
    downstream reads from the DB row this returns, never from anything the
    client sent in the request body."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing session. Log in and try again.")
    token = authorization.split(" ", 1)[1].strip()
    payload = _decode_token(token)
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(401, "Malformed session token.")
    jti = payload.get("jti")
    if jti and db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
        raise HTTPException(401, "You have been logged out. Please log in again.")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(401, "This account no longer exists.")
    return user


def require_role(*roles: str):
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, f"This action requires role: {', '.join(roles)}.")
        return user
    return _dep


require_patient_role = require_role(*PERSON_ROLES)
require_caregiver_role = require_role(*PERSON_ROLES)
require_doctor_role = require_role("doctor")


# ---------------------------------------------------------------- patient access

def _owns_patient(db: Session, user: User, patient: Patient) -> bool:
    return user.role in PERSON_ROLES and patient.user_id == user.id


def _has_active_caregiver_link(db: Session, user: User, patient_id: int) -> bool:
    return db.query(CaregiverLink).filter(
        CaregiverLink.patient_id == patient_id,
        CaregiverLink.caregiver_user_id == user.id,
        CaregiverLink.status == "active",
    ).first() is not None


def _has_active_doctor_link(db: Session, user: User, patient_id: int) -> bool:
    return db.query(DoctorLink).filter(
        DoctorLink.patient_id == patient_id,
        DoctorLink.doctor_user_id == user.id,
        DoctorLink.status == "active",
    ).first() is not None


def has_read_access(db: Session, user: User, patient_id: int) -> bool:
    """Owner, or an explicitly linked (active) caregiver/doctor. Used for
    everything that only VIEWS a patient's records."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        return False
    if _owns_patient(db, user, patient):
        return True
    if user.role in PERSON_ROLES:
        return _has_active_caregiver_link(db, user, patient_id)
    if user.role == "doctor":
        return doctor_role_enabled() and _has_active_doctor_link(db, user, patient_id)
    return False


def has_write_access(db: Session, user: User, patient_id: int) -> bool:
    """Only the owning patient can create/edit prescriptions, log doses, or
    run a triage check on their own record — caregivers and doctors never
    silently act as the patient (Part 3: 'cannot modify original
    prescriptions')."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        return False
    return _owns_patient(db, user, patient)


def is_linked_caregiver(db: Session, user: User, patient_id: int) -> bool:
    return user.role in PERSON_ROLES and _has_active_caregiver_link(db, user, patient_id)


def can_add_medicine(db: Session, user: User, patient_id: int) -> bool:
    """The patient, or a caregiver the patient has linked, may add a medicine (it is labelled with who added it).
    Everything else a caregiver does stays read-only."""
    return has_write_access(db, user, patient_id) or is_linked_caregiver(db, user, patient_id)


def require_patient_read_access(patient_id: int, user: User = Depends(get_current_user),
                                 db: Session = Depends(get_db_session)) -> User:
    if not has_read_access(db, user, patient_id):
        raise HTTPException(403, "You do not have access to this patient's records.")
    return user


def require_patient_write_access(patient_id: int, user: User = Depends(get_current_user),
                                  db: Session = Depends(get_db_session)) -> User:
    if not has_write_access(db, user, patient_id):
        raise HTTPException(403, "Only the patient can make this change to their own record.")
    return user


# ---------------------------------------------------------------- resolving patient_id from a child id

def _patient_id_of_medicine(db: Session, medicine_id: int) -> int:
    medicine = db.query(Medicine).filter(Medicine.id == medicine_id).first()
    if not medicine:
        raise HTTPException(404, f"No medicine {medicine_id}")
    return medicine.prescription.patient_id


def _patient_id_of_dose(db: Session, dose_id: int) -> int:
    dose = db.query(Dose).filter(Dose.id == dose_id).first()
    if not dose:
        raise HTTPException(404, f"No dose {dose_id}")
    return dose.medicine.prescription.patient_id


def _patient_id_of_prescription(db: Session, prescription_id: int) -> int:
    prescription = db.query(Prescription).filter(Prescription.id == prescription_id).first()
    if not prescription:
        raise HTTPException(404, f"No prescription {prescription_id}")
    return prescription.patient_id


def require_medicine_write_access(medicine_id: int, user: User = Depends(get_current_user),
                                   db: Session = Depends(get_db_session)) -> User:
    patient_id = _patient_id_of_medicine(db, medicine_id)
    if not has_write_access(db, user, patient_id):
        raise HTTPException(403, "Only the patient can make this change to their own record.")
    return user


def require_dose_write_access(dose_id: int, user: User = Depends(get_current_user),
                               db: Session = Depends(get_db_session)) -> User:
    patient_id = _patient_id_of_dose(db, dose_id)
    if not has_write_access(db, user, patient_id):
        raise HTTPException(403, "Only the patient can make this change to their own record.")
    return user


def require_prescription_write_access(prescription_id: int, user: User = Depends(get_current_user),
                                       db: Session = Depends(get_db_session)) -> User:
    patient_id = _patient_id_of_prescription(db, prescription_id)
    if not has_write_access(db, user, patient_id):
        raise HTTPException(403, "Only the patient can make this change to their own record.")
    return user
