"""
SmartPoli canonical data model.

One model. Every stage after extraction (scheduler, adherence, triage, report)
reads and writes these tables — nothing downstream invents its own shape for
a medicine, a dose, or a patient. See CLAUDE.md section 6.

Two invariants enforced elsewhere in the codebase, not here:
  1. Dose rows are generated once at confirmation and never recomputed.
  2. Medicine.raw_text is never discarded, even when confidence is low.
"""

import os
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey, inspect, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

DATABASE_URL = os.getenv("SMARTPOLI_DATABASE_URL", "sqlite:///./smartpoli.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class User(Base):
    """
    Real account, real password, real role. Added so the doctor/caregiver
    dashboards can enforce actual authorization instead of a frontend
    `role` field the backend just takes on faith. See auth.py.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, nullable=True, index=True)   # nullable: WhatsApp-created accounts have none
    phone = Column(String, unique=True, nullable=True, index=True)   # E.164, e.g. "+919876543210" — WhatsApp identity
    password_hash = Column(String, nullable=True)  # nullable: WhatsApp accounts never set one, interact via chat only
    role = Column(String, nullable=False)  # 'patient' | 'caregiver' | 'doctor'
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # owning patient account
    name = Column(String, nullable=False)
    age = Column(Integer, nullable=True)
    sex = Column(String, nullable=True)
    blood_group = Column(String, nullable=True)          # optional, emergency card only
    allergies = Column(String, nullable=True)          # free text, patient-supplied — optional Feature J
    emergency_contact = Column(String, nullable=True)   # "name, phone" free text — optional Feature J
    created_at = Column(DateTime, default=datetime.utcnow)

    prescriptions = relationship("Prescription", back_populates="patient", cascade="all, delete-orphan")
    symptom_checks = relationship("SymptomCheck", back_populates="patient", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="patient", cascade="all, delete-orphan")


class CaregiverLink(Base):
    """
    A real patient<->caregiver relationship, not a caregiver typing a
    patient id into a box. `code` is a one-time invite the patient
    generates and shares; redeeming it fills in caregiver_user_id and
    flips status to 'active'. The patient can revoke at any time.
    """
    __tablename__ = "caregiver_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    caregiver_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    code = Column(String, unique=True, nullable=False)
    status = Column(String, nullable=False, default="pending")  # 'pending' | 'active' | 'revoked'
    created_at = Column(DateTime, default=datetime.utcnow)
    accepted_at = Column(DateTime, nullable=True)


class DoctorLink(Base):
    """Same shape as CaregiverLink, kept as a separate table because a
    doctor's access and a caregiver's access are different in kind (Part 3
    of the brief), not just a role label on the same row."""
    __tablename__ = "doctor_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    code = Column(String, unique=True, nullable=False)
    status = Column(String, nullable=False, default="pending")  # 'pending' | 'active' | 'revoked'
    created_at = Column(DateTime, default=datetime.utcnow)
    accepted_at = Column(DateTime, nullable=True)


class MedicineCorrection(Base):
    """
    Audit trail for a doctor correcting a medicine entry. The original
    Medicine row IS updated (so the schedule stays consistent) but never
    silently — every correction leaves one of these rows behind with the
    before/after value, who made it, and why.
    """
    __tablename__ = "medicine_corrections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    medicine_id = Column(Integer, ForeignKey("medicines.id"), nullable=False)
    doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    field = Column(String, nullable=False)
    original_value = Column(Text, nullable=True)
    corrected_value = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Prescription(Base):
    __tablename__ = "prescriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    doctor_name = Column(String, nullable=True)
    issued_date = Column(String, nullable=True)  # YYYY-MM-DD, as written on the script
    source = Column(String, nullable=False, default="manual")  # 'manual' | 'ocr'
    status = Column(String, nullable=False, default="draft")  # 'draft' | 'confirmed'
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="prescriptions")
    medicines = relationship("Medicine", back_populates="prescription", cascade="all, delete-orphan")


class Medicine(Base):
    __tablename__ = "medicines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    prescription_id = Column(Integer, ForeignKey("prescriptions.id"), nullable=False)

    raw_text = Column(Text, nullable=False)  # original line — NEVER discarded

    name = Column(String, nullable=True)
    normalized_name = Column(String, nullable=True)
    dose_amount = Column(String, nullable=True)
    dose_unit = Column(String, nullable=True)

    schedule_code = Column(String, nullable=True)  # '1-0-1', 'BD', 'SOS', ...
    slots = Column(Text, nullable=True)             # JSON list
    times = Column(Text, nullable=True)              # JSON list
    food = Column(String, nullable=False, default="any")  # 'before' | 'after' | 'any'
    duration_days = Column(Integer, nullable=True)   # null = ongoing
    is_prn = Column(Boolean, nullable=False, default=False)

    confidence = Column(Float, nullable=False, default=0.0)
    field_confidence = Column(Text, nullable=True)    # JSON dict
    status = Column(String, nullable=False, default="needs_confirmation")
    # 'verified' | 'review' | 'needs_confirmation'

    plain_language_hi = Column(Text, nullable=True)  # Hindi rendering — optional Feature G

    prescription = relationship("Prescription", back_populates="medicines")
    doses = relationship("Dose", back_populates="medicine", cascade="all, delete-orphan")


class Dose(Base):
    __tablename__ = "doses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    medicine_id = Column(Integer, ForeignKey("medicines.id"), nullable=False)

    scheduled_at = Column(DateTime, nullable=False)
    state = Column(String, nullable=False, default="pending")
    # 'pending' | 'taken' | 'missed' | 'skipped' | 'snoozed'
    acted_at = Column(DateTime, nullable=True)
    reason = Column(String, nullable=True)
    snooze_count = Column(Integer, nullable=False, default=0)
    whatsapp_reminder_sent = Column(Boolean, nullable=False, default=False)  # at-most-once WhatsApp nudge per dose

    medicine = relationship("Medicine", back_populates="doses")


class SymptomCheck(Base):
    __tablename__ = "symptom_checks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    symptoms = Column(Text, nullable=False)   # JSON list
    answers = Column(Text, nullable=False)    # JSON dict

    severity = Column(String, nullable=False)  # 'LOW' | 'MODERATE' | 'EMERGENCY'
    reasons = Column(Text, nullable=False)     # JSON list of "because" strings
    action = Column(String, nullable=False)
    ruleset_version = Column(String, nullable=False)

    patient = relationship("Patient", back_populates="symptom_checks")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    at = Column(DateTime, default=datetime.utcnow)
    actor = Column(String, nullable=False, default="system")
    action = Column(String, nullable=False)
    detail = Column(Text, nullable=True)

    patient = relationship("Patient", back_populates="audit_logs")


class WhatsAppSession(Base):
    """
    Conversation state for one WhatsApp number (whatsapp_bot.py). Separate
    from the auth.py JWT session model on purpose: a WhatsApp user never
    logs in with a password — the phone number *is* the credential, the
    same way a caregiver/doctor invite code is a credential — so this
    table, not `auth.py`, is what recognizes a returning WhatsApp sender.
    """
    __tablename__ = "whatsapp_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String, unique=True, nullable=False, index=True)  # E.164, e.g. "+919876543210"
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    state = Column(String, nullable=False, default="new")
    # 'new' -> 'awaiting_name' -> 'ready'
    notifications_opt_in = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def _migrate_sqlite_add_columns() -> None:
    """
    `create_all` only creates tables that don't exist yet — it never alters
    an existing one. On a dev machine that already has a smartpoli.db from
    before the auth work, `patients` exists without `user_id`/`blood_group`.
    SQLite supports simple `ADD COLUMN`, so add whatever's missing rather
    than requiring a fresh DB (Alembic would be overkill for two columns).
    """
    if not DATABASE_URL.startswith("sqlite"):
        return
    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    if "patients" in table_names:
        existing = {c["name"] for c in inspector.get_columns("patients")}
        with engine.begin() as conn:
            if "user_id" not in existing:
                conn.execute(text("ALTER TABLE patients ADD COLUMN user_id INTEGER"))
            if "blood_group" not in existing:
                conn.execute(text("ALTER TABLE patients ADD COLUMN blood_group VARCHAR"))

    if "users" in table_names:
        existing = {c["name"] for c in inspector.get_columns("users")}
        with engine.begin() as conn:
            if "phone" not in existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR"))
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone ON users (phone)"))
        # email/password_hash going from NOT NULL to nullable is a column-type
        # change SQLite's ALTER TABLE can't express directly — existing rows
        # already satisfy NOT NULL (every account so far has both), so this
        # is safe to leave as a forward-only relaxation new inserts rely on
        # rather than rewriting the table.

    if "doses" in table_names:
        existing = {c["name"] for c in inspector.get_columns("doses")}
        with engine.begin() as conn:
            if "whatsapp_reminder_sent" not in existing:
                conn.execute(text(
                    "ALTER TABLE doses ADD COLUMN whatsapp_reminder_sent BOOLEAN NOT NULL DEFAULT 0"
                ))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_add_columns()


def get_db_session() -> Session:
    """FastAPI dependency: yields a session, always closed after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def log_audit(db: Session, patient_id: int, actor: str, action: str, detail: Optional[str] = None) -> None:
    db.add(AuditLog(patient_id=patient_id, actor=actor, action=action, detail=detail))
    db.commit()
