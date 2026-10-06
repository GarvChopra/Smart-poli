"""
Care notes: what caregivers (and doctors) write about a patient.

    message   a note to the patient                       (patient sees it)
    medicine  a note on one of the patient's medicines     (patient sees it)
    handover  a note for the rest of the care team         (patient does NOT see it)

Pure rules and queries - no HTTP. The endpoints (caregiver_router.py, main.py) decide WHO may call these; this module only
enforces what a note may contain, how many one person may write, and which notes a given viewer may see.
Note text is stored and returned verbatim; every screen escapes it when it draws it.
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from db import CareNote, Medicine, Prescription

MAX_LEN = 500
RATE_LIMIT = 30                     # notes per author per hour
KINDS = {"message": "patient", "medicine": "patient", "handover": "care_team"}   # kind -> visibility
DISCLAIMER = "Notes from your caregiver are not medical advice."


class NoteError(ValueError):
    """A note that cannot be saved; the message is plain words for the person."""


class NoteRateLimited(NoteError):
    pass


def create_note(db: Session, patient_id: int, author, kind: str, body: Optional[str], medicine_id: Optional[int] = None) -> CareNote:
    """`author` needs .id, .role and .name (a User or anything shaped like one)."""
    if kind not in KINDS:
        raise NoteError("Unknown note type.")
    text = (body or "").strip()
    if not text:
        raise NoteError("Write a note first.")
    if len(text) > MAX_LEN:
        raise NoteError(f"Notes can be at most {MAX_LEN} characters.")
    if kind == "medicine":
        owned = medicine_id is not None and db.query(Medicine.id).join(Prescription, Medicine.prescription_id == Prescription.id).filter(
            Medicine.id == medicine_id, Prescription.patient_id == patient_id).first() is not None
        if not owned:
            raise NoteError("Choose one of this patient's medicines.")
    else:
        medicine_id = None
    since = datetime.utcnow() - timedelta(hours=1)
    recent = db.query(CareNote).filter(CareNote.author_user_id == author.id, CareNote.created_at >= since).count()
    if recent >= RATE_LIMIT:
        raise NoteRateLimited("You have written a lot of notes in the last hour. Please wait a little.")
    note = CareNote(patient_id=patient_id, author_user_id=author.id, author_role=author.role, author_name=author.name,
                    kind=kind, medicine_id=medicine_id, body=text, visibility=KINDS[kind])
    db.add(note)
    db.commit()
    return note


def serialize_note(n: CareNote) -> dict:
    return {"id": n.id, "kind": n.kind, "medicine_id": n.medicine_id, "body": n.body, "author_user_id": n.author_user_id,
            "author_name": n.author_name, "author_role": n.author_role, "created_at": n.created_at.isoformat(),
            "seen": n.seen_by_patient_at is not None}


def list_notes(db: Session, patient_id: int, viewer_role: str, kind: Optional[str] = None,
               medicine_id: Optional[int] = None, limit: int = 200) -> list:
    """Newest first. The patient only ever sees notes meant for them; care-team viewers see everything."""
    q = db.query(CareNote).filter(CareNote.patient_id == patient_id, CareNote.deleted_at.is_(None))
    if viewer_role == "patient":
        q = q.filter(CareNote.visibility == "patient")
    if kind:
        q = q.filter(CareNote.kind == kind)
    if medicine_id is not None:
        q = q.filter(CareNote.medicine_id == medicine_id)
    return [serialize_note(n) for n in q.order_by(CareNote.created_at.desc(), CareNote.id.desc()).limit(limit).all()]


def unread_count(db: Session, patient_id: int) -> int:
    return db.query(CareNote).filter(CareNote.patient_id == patient_id, CareNote.visibility == "patient",
                                     CareNote.deleted_at.is_(None), CareNote.seen_by_patient_at.is_(None)).count()


def mark_seen(db: Session, patient_id: int) -> int:
    rows = db.query(CareNote).filter(CareNote.patient_id == patient_id, CareNote.visibility == "patient",
                                     CareNote.deleted_at.is_(None), CareNote.seen_by_patient_at.is_(None)).all()
    now = datetime.utcnow()
    for n in rows:
        n.seen_by_patient_at = now
    db.commit()
    return len(rows)


def soft_delete(db: Session, note_id: int, user) -> bool:
    """Only the author can delete a note, and only once."""
    n = db.query(CareNote).filter(CareNote.id == note_id, CareNote.author_user_id == user.id, CareNote.deleted_at.is_(None)).first()
    if not n:
        return False
    n.deleted_at = datetime.utcnow()
    db.commit()
    return True
