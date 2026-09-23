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

from db import get_db_session, User, Patient, CaregiverLink, DoctorLink, Medicine, Dose, Prescription

JWT_SECRET = os.getenv("SMARTPOLI_JWT_SECRET") or "dev-only-insecure-secret-change-me-in-.env"
JWT_ALGO = "HS256"
JWT_TTL_HOURS = 24 * 7

ROLES = ("patient", "caregiver", "doctor")


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- tokens

def create_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "email": user.email,
        "exp": datetime.utcnow() + timedelta(hours=JWT_TTL_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def gen_link_code() -> str:
    """Short, shareable, hard-to-guess invite code (patient -> caregiver/doctor)."""
    return secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper()


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


require_patient_role = require_role("patient")
require_caregiver_role = require_role("caregiver")
require_doctor_role = require_role("doctor")


# ---------------------------------------------------------------- patient access

def _owns_patient(db: Session, user: User, patient: Patient) -> bool:
    return user.role == "patient" and patient.user_id == user.id


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
    if user.role == "caregiver":
        return _has_active_caregiver_link(db, user, patient_id)
    if user.role == "doctor":
        return _has_active_doctor_link(db, user, patient_id)
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
