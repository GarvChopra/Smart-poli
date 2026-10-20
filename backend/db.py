"""
SmartPoli canonical data model.

One model. Every stage after extraction (scheduler, adherence, triage, report)
reads and writes these tables — nothing downstream invents its own shape for
a medicine, a dose, or a patient. See CLAUDE.md section 6.

Two invariants enforced elsewhere in the codebase, not here:
  1. Dose rows are generated once at confirmation and never recomputed.
  2. Medicine.raw_text is never discarded, even when confidence is low.
"""

import logging
import os
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey, UniqueConstraint,
    event, inspect, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

DATABASE_URL = os.getenv("SMARTPOLI_DATABASE_URL", "sqlite:///./smartpoli.db")

logger = logging.getLogger(__name__)
IS_SQLITE = DATABASE_URL.startswith("sqlite")

connect_args = {"check_same_thread": False} if IS_SQLITE else {}
# Postgres: a bounded pool (one app process; the hosted pooler sits in front), connections recycled before the pooler
# or a network box drops them, and no query may run longer than STATEMENT_TIMEOUT - one stuck query must never hold
# a connection (and the whole app) hostage. The app's real queries take milliseconds.
STATEMENT_TIMEOUT = os.getenv("SMARTPOLI_DB_STATEMENT_TIMEOUT", "15s")
pool_args = {} if IS_SQLITE else {"pool_size": 5, "max_overflow": 10, "pool_timeout": 30, "pool_recycle": 1800}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True, **pool_args)

if not IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _limit_each_connection(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        cur.close()
        dbapi_conn.commit()        # a SET inside a rolled-back transaction would be undone
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Least privilege: the app can run as a database account that may only read and write rows
# (SMARTPOLI_DATABASE_URL), while creating tables / columns / indexes at start-up uses a separate owner account
# (SMARTPOLI_MIGRATION_DATABASE_URL). Unset, both are the same account, as before.
MIGRATION_DATABASE_URL = os.getenv("SMARTPOLI_MIGRATION_DATABASE_URL") or DATABASE_URL
ddl_engine = engine if MIGRATION_DATABASE_URL == DATABASE_URL else create_engine(
    MIGRATION_DATABASE_URL, connect_args=connect_args, pool_pre_ping=True, pool_size=1, max_overflow=0)

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


class PrescriptionTemplate(Base):
    """A doctor's saved shorthand lines for quick reuse across patients —
    e.g. a common combo they prescribe often. Scoped to the doctor who
    created it; never shared across doctors, never patient-visible."""
    __tablename__ = "prescription_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    label = Column(String, nullable=False)
    lines = Column(Text, nullable=False)  # JSON list of raw shorthand lines
    created_at = Column(DateTime, default=datetime.utcnow)


class Prescription(Base):
    __tablename__ = "prescriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    doctor_name = Column(String, nullable=True)
    issued_date = Column(String, nullable=True)  # YYYY-MM-DD, as written on the script
    source = Column(String, nullable=False, default="manual")  # 'manual' | 'ocr'
    status = Column(String, nullable=False, default="draft")  # 'draft' | 'confirmed'
    # The linked caregiver who added this for the patient (None = the patient). No foreign key on purpose: deleting the
    # caregiver's account must not touch the patient's medicines; the name then just reads "your caregiver".
    added_by_user_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="prescriptions")
    # selectin: loading prescriptions fetches all their medicines (and, below,
    # all their doses) in one batched query each, instead of one query per
    # prescription / per medicine. Each query is a full round trip to the
    # database, which dominated page load time.
    medicines = relationship("Medicine", back_populates="prescription", cascade="all, delete-orphan",
                             lazy="selectin")


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
    doses = relationship("Dose", back_populates="medicine", cascade="all, delete-orphan", lazy="selectin")
    days_rule = relationship("MedicineDays", uselist=False, cascade="all, delete-orphan", lazy="selectin")


class MedicineDays(Base):
    """Optional 'only on these weekdays' rule for a medicine (an alarm that repeats Mon/Wed/Fri). No row = every day.
    A separate table, not a new column on medicines, so existing databases (including Postgres) need no ALTER."""
    __tablename__ = "medicine_days"

    medicine_id = Column(Integer, ForeignKey("medicines.id"), primary_key=True)
    weekdays = Column(Text, nullable=False)           # JSON list of 0 (Mon) .. 6 (Sun)


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


class EmergencyCardToken(Base):
    """The random, revocable token a public emergency-card URL/QR carries —
    never the sequential patient id, which anyone could enumerate. A new
    table (not columns on `patients`) because init_db() can create tables
    on Postgres but not alter existing ones. At most one active
    (revoked_at IS NULL) row per patient; revoked rows are kept for audit."""
    __tablename__ = "emergency_card_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    token = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked_at = Column(DateTime, nullable=True)


class VoiceMessage(Base):
    """One line of a voice conversation, stored server-side per patient so the
    assistant can recall earlier days ("wahi problem jo kal thi"). The patient
    can delete their history. A new table (not columns) for the Postgres reason
    noted on EmergencyCardToken."""
    __tablename__ = "voice_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    role = Column(String, nullable=False)       # 'user' | 'assistant'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class EmergencyCardProfile(Base):
    """The extra, patient-chosen content of the 3D health card that the
    core record doesn't have — photo, conditions, instructions — plus which
    fields the public QR view may show. A separate table for the same
    Postgres reason as EmergencyCardToken. Created on first save."""
    __tablename__ = "emergency_card_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, unique=True, index=True)
    conditions = Column(Text, nullable=True)
    instructions = Column(Text, nullable=True)
    photo = Column(Text, nullable=True)   # data:image/(jpeg|png);base64,... — resized in the browser
    share = Column(Text, nullable=True)   # JSON {field: bool}; a missing key means "share"
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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


class PatientSettings(Base):
    """Per-patient preferences. A separate table (not columns on `patients`)
    for the Postgres reason noted on EmergencyCardToken. Created on first
    save; a missing row means 'defaults' (clock.DEFAULT_TZ, standard reminder
    offsets)."""
    __tablename__ = "patient_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, unique=True, index=True)
    timezone = Column(String, nullable=True)            # IANA name, e.g. "Asia/Kolkata"
    reminder_lead_minutes = Column(Integer, nullable=True)  # heads-up before the dose; null = default
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PatientRoutine(Base):
    """When this patient's mornings / afternoons / evenings / nights / bedtimes are, plus which optional
    reminders they want. A separate table (not columns on patient_settings) because init_db() can create
    tables on Postgres but cannot alter existing ones. No row = standard times and all reminders on."""
    __tablename__ = "patient_routine"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, unique=True, index=True)
    morning = Column(String, nullable=True)       # "HH:MM"
    afternoon = Column(String, nullable=True)
    evening = Column(String, nullable=True)
    night = Column(String, nullable=True)
    bedtime = Column(String, nullable=True)
    notify_soon = Column(Boolean, nullable=False, default=True)       # the 10-minute-before heads-up
    notify_followup = Column(Boolean, nullable=False, default=True)   # "did you take it?" 15 minutes after
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AIGapCache(Base):
    """Graded dose-gap answers (gap_ai.py), cached so the LLM / label APIs are asked once per medicine or
    pair, not on every tap. 'single' = one medicine's interval, 'pair' = two medicines (key 'a|b' sorted).
    Negative answers ('no gap needed') are cached too."""
    __tablename__ = "ai_gap_cache"
    __table_args__ = (UniqueConstraint("kind", "key", name="uq_ai_gap_kind_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String, nullable=False, index=True)
    key = Column(String, nullable=False, index=True)
    payload = Column(Text, nullable=False)       # JSON, already graded
    model = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Feedback(Base):
    """A rating and message sent by a patient or caregiver from the Feedback page or the occasional rate-the-app popup."""
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String, nullable=False)              # 'patient' | 'caregiver' | 'doctor'
    rating = Column(Integer, nullable=False)           # 1..5
    category = Column(String, nullable=False)          # 'problem' | 'idea' | 'praise' | 'other'
    message = Column(Text, nullable=False, default="")
    contact_ok = Column(Boolean, nullable=False, default=False)
    source = Column(String, nullable=False, default="page")   # 'page' | 'popup'
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class CareNote(Base):
    """A note written by someone on the care team (caregiver / doctor) about a patient. kind 'message' goes to the patient,
    'medicine' is attached to one of their medicines (patient sees it too), 'handover' is for the care team only."""
    __tablename__ = "care_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    author_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    author_role = Column(String, nullable=False)
    author_name = Column(String, nullable=False)       # copied at write time: the note keeps its author's name
    kind = Column(String, nullable=False)              # 'message' | 'medicine' | 'handover'
    medicine_id = Column(Integer, ForeignKey("medicines.id"), nullable=True)
    body = Column(Text, nullable=False)
    visibility = Column(String, nullable=False)        # 'patient' | 'care_team'
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    seen_by_patient_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)


class UserPushSubscription(Base):
    """A caregiver's (or any user's) own phone for Web Push. Separate from push_subscriptions, which belong to a patient."""
    __tablename__ = "user_push_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(String, nullable=False)
    auth = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_success_at = Column(DateTime, nullable=True)


class CaregiverPrefs(Base):
    """Per caregiver, per patient alert switches. No row = the defaults (alerts on)."""
    __tablename__ = "caregiver_prefs"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), primary_key=True)
    notify_missed = Column(Boolean, nullable=False, default=True)


class PushSubscription(Base):
    """A browser/TWA Web Push endpoint for one patient (RFC 8030 / VAPID).
    `endpoint` is unique: re-subscribing the same device updates the row."""
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(String, nullable=False)
    auth = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_success_at = Column(DateTime, nullable=True)


class DeadPushEndpoint(Base):
    """A push endpoint the push service said is gone (404/410). Kept as a hash, not the URL, with whose it was, so a
    phone that still holds the dead subscription is told to make a fresh one instead of re-registering the dead one
    (which would silently drop every reminder), and a browser-rotated subscription can be moved to the right patient."""
    __tablename__ = "dead_push_endpoints"

    endpoint_hash = Column(String, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    died_at = Column(DateTime, default=datetime.utcnow)


def endpoint_hash(endpoint: str) -> str:
    import hashlib
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def mark_push_endpoint_dead(db: Session, sub: "PushSubscription") -> None:
    """Delete a gone subscription and remember it (caller commits)."""
    h = endpoint_hash(sub.endpoint)
    if db.get(DeadPushEndpoint, h) is None:
        db.add(DeadPushEndpoint(endpoint_hash=h, patient_id=sub.patient_id, user_id=sub.user_id))
    db.delete(sub)


class ReminderLog(Base):
    """One row per (dose, kind, channel) that has been sent. The unique
    constraint is the dedupe: a 1-minute sweep, a restarted server or two
    overlapping workers can never send the same reminder twice."""
    __tablename__ = "reminder_log"
    __table_args__ = (UniqueConstraint("dose_id", "kind", "channel", name="uq_reminder_dose_kind_channel"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    dose_id = Column(Integer, ForeignKey("doses.id"), nullable=False, index=True)
    kind = Column(String, nullable=False)       # 'lead' | 'due' | 'followup' | 'missed'
    channel = Column(String, nullable=False)    # 'push' | 'whatsapp'
    sent_at = Column(DateTime, default=datetime.utcnow)


class RevokedToken(Base):
    """Session tokens that were logged out before they expired. A JWT is
    stateless, so 'logout' has to be remembered server-side or the token keeps
    working until `exp`. Rows can be pruned once expires_at has passed."""
    __tablename__ = "revoked_tokens"

    jti = Column(String, primary_key=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, default=datetime.utcnow)


class RegulatoryLookupCache(Base):
    """Cached result of an official-source lookup (openFDA etc.) so a page
    view never hammers a rate-limited public API, and so every answer can
    say exactly when it was last checked."""
    __tablename__ = "regulatory_lookup_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False, index=True)   # 'openfda_drugsfda' | 'openfda_enforcement'
    query_key = Column(String, nullable=False, index=True)
    payload = Column(Text, nullable=False)                # JSON
    fetched_at = Column(DateTime, default=datetime.utcnow)


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
    inspector = inspect(ddl_engine)
    table_names = inspector.get_table_names()

    if "patients" in table_names:
        existing = {c["name"] for c in inspector.get_columns("patients")}
        with ddl_engine.begin() as conn:
            if "user_id" not in existing:
                conn.execute(text("ALTER TABLE patients ADD COLUMN user_id INTEGER"))
            if "blood_group" not in existing:
                conn.execute(text("ALTER TABLE patients ADD COLUMN blood_group VARCHAR"))

    if "users" in table_names:
        existing = {c["name"] for c in inspector.get_columns("users")}
        with ddl_engine.begin() as conn:
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
        with ddl_engine.begin() as conn:
            if "whatsapp_reminder_sent" not in existing:
                conn.execute(text(
                    "ALTER TABLE doses ADD COLUMN whatsapp_reminder_sent BOOLEAN NOT NULL DEFAULT 0"
                ))


def _add_missing_columns() -> None:
    """Columns added to an existing table after launch, on any database (create_all never alters a table). Each is
    nullable with no default, so adding it is instant and leaves every existing row as it was."""
    inspector = inspect(ddl_engine)
    if "prescriptions" in inspector.get_table_names():
        if "added_by_user_id" not in {c["name"] for c in inspector.get_columns("prescriptions")}:
            with ddl_engine.begin() as conn:
                conn.execute(text("ALTER TABLE prescriptions ADD COLUMN added_by_user_id INTEGER"))


# Lookups the app makes all the time (a patient's prescriptions, a prescription's medicines, a medicine's doses, the
# every-minute "which doses are due" check, ...). Without these every one of them reads the whole table.
INDEXES = [
    ("ix_doses_medicine_id", "doses", "medicine_id"),
    ("ix_doses_state_scheduled_at", "doses", "state, scheduled_at"),
    ("ix_medicines_prescription_id", "medicines", "prescription_id"),
    ("ix_prescriptions_patient_id", "prescriptions", "patient_id"),
    ("ix_patients_user_id", "patients", "user_id"),
    ("ix_audit_log_patient_id_at", "audit_log", "patient_id, at"),
    ("ix_caregiver_links_patient_id", "caregiver_links", "patient_id"),
    ("ix_caregiver_links_caregiver_user_id", "caregiver_links", "caregiver_user_id"),
    ("ix_caregiver_prefs_patient_id", "caregiver_prefs", "patient_id"),
    ("ix_care_notes_medicine_id", "care_notes", "medicine_id"),
    ("ix_symptom_checks_patient_id", "symptom_checks", "patient_id"),
    ("ix_whatsapp_sessions_patient_id", "whatsapp_sessions", "patient_id"),
    ("ix_whatsapp_sessions_user_id", "whatsapp_sessions", "user_id"),
    ("ix_push_subscriptions_user_id", "push_subscriptions", "user_id"),
    ("ix_medicine_corrections_medicine_id", "medicine_corrections", "medicine_id"),
    ("ix_dead_push_endpoints_patient_id", "dead_push_endpoints", "patient_id"),
]


def _add_indexes() -> None:
    """IF NOT EXISTS: safe on every start. Only adds indexes - never changes a row. The tables are small, so each
    builds in milliseconds."""
    tables = set(inspect(ddl_engine).get_table_names())
    for name, table, cols in INDEXES:
        if table not in tables:
            continue
        try:
            with ddl_engine.begin() as conn:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols})"))
        except Exception as e:  # noqa: BLE001 - a missing index makes the app slower, never broken: start anyway
            logger.warning("Could not create index %s: %s", name, type(e).__name__)


def init_db() -> None:
    Base.metadata.create_all(bind=ddl_engine)
    _migrate_sqlite_add_columns()
    _add_missing_columns()
    _add_indexes()


KEEP_TECHNICAL_ROWS_DAYS = 180


def prune_old_rows(db: Session, utc_now: Optional[datetime] = None) -> dict:
    """Housekeeping, hourly with the reminder sweep. Removes only technical rows nobody needs after half a year:
    'this reminder was sent' records (they only stop a dose being reminded twice, and a dose is never reminded that
    long after its time) and phone registrations the push service retired. Patients' records - medicines, doses,
    notes, cards - and the audit log are never touched here."""
    from datetime import timedelta
    cutoff = (utc_now or datetime.utcnow()) - timedelta(days=KEEP_TECHNICAL_ROWS_DAYS)
    out = {
        "reminder_log": db.query(ReminderLog).filter(ReminderLog.sent_at < cutoff).delete(synchronize_session=False),
        "dead_push_endpoints": db.query(DeadPushEndpoint).filter(DeadPushEndpoint.died_at < cutoff).delete(synchronize_session=False),
    }
    db.commit()
    return out


_closer = None


async def get_db_session():
    """FastAPI dependency: yields a session, always closed after the request.

    Closed on threads of its OWN, not the shared pool the endpoints run on. With a plain sync dependency the close
    waits for a free endpoint thread; under a burst every endpoint thread is busy waiting for a database connection,
    and the connections can't come back because their close is waiting for a thread - the app stalls until the pool
    timeout (seen in a 500-user load test). Closing here never waits behind the queue, so connections keep cycling."""
    global _closer
    import anyio
    if _closer is None:
        _closer = anyio.CapacityLimiter(8)
    db = SessionLocal()
    try:
        yield db
    finally:
        await anyio.to_thread.run_sync(db.close, limiter=_closer)


def log_audit(db: Session, patient_id: int, actor: str, action: str, detail: Optional[str] = None) -> None:
    db.add(AuditLog(patient_id=patient_id, actor=actor, action=action, detail=detail))
    db.commit()
