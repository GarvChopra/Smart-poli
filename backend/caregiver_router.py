"""
SmartPoli — Caregiver linking + caregiver dashboard (Part 2 of the brief).

A caregiver never gets access by typing a patient id into a box. The
patient generates a one-time invite code; a caregiver account redeems it;
only then does an active CaregiverLink row exist, and every caregiver
endpoint here checks that row via auth.has_read_access — never a
frontend-supplied role or id.

Reuses the existing dashboard/adherence/triage/emergency-card data
(serializers.py, nudges.py) rather than a second medication system.
"""

from datetime import datetime, timedelta
from typing import Optional

import care_notes
import care_push

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db import get_db_session, log_audit, User, CaregiverLink, SymptomCheck, Dose
from auth import get_current_user, require_patient_write_access, require_caregiver_role, has_read_access, gen_link_code
from schemas import LinkRedeem, CareNoteCreate
from serializers import get_patient_or_404, serialize_patient, gather_dashboard_data, emergency_card_data
from doctor_intelligence import compute_priority
from nudges import compute_nudges
from interactions import load_ruleset as load_interaction_ruleset
from food_warnings import load_ruleset as load_food_ruleset

router = APIRouter(tags=["caregiver"])

INTERACTION_RULESET = load_interaction_ruleset()
FOOD_RULESET = load_food_ruleset()


# ---------------------------------------------------------------- patient side: manage invites

@router.post("/patients/{patient_id}/caregiver-links")
def create_caregiver_link(patient_id: int, user: User = Depends(require_patient_write_access),
                           db: Session = Depends(get_db_session)):
    get_patient_or_404(db, patient_id)
    code = gen_link_code()
    while db.query(CaregiverLink).filter(CaregiverLink.code == code).first():
        code = gen_link_code()
    link = CaregiverLink(patient_id=patient_id, code=code, status="pending")
    db.add(link)
    db.commit()
    log_audit(db, patient_id, f"patient:{user.id}", "caregiver_link_created", f"invite issued (link {link.id})")
    return {"id": link.id, "code": link.code, "status": link.status}


@router.get("/patients/{patient_id}/caregiver-links")
def list_caregiver_links(patient_id: int, user: User = Depends(require_patient_write_access),
                          db: Session = Depends(get_db_session)):
    links = (
        db.query(CaregiverLink)
        .filter(CaregiverLink.patient_id == patient_id)
        .order_by(CaregiverLink.created_at.desc())
        .all()
    )
    out = []
    for link in links:
        caregiver_name = None
        if link.caregiver_user_id:
            cg = db.query(User).filter(User.id == link.caregiver_user_id).first()
            caregiver_name = cg.name if cg else None
        out.append({
            "id": link.id, "code": link.code, "status": link.status, "caregiver_name": caregiver_name,
            "created_at": link.created_at.isoformat(),
            "accepted_at": link.accepted_at.isoformat() if link.accepted_at else None,
        })
    return out


@router.post("/caregiver-links/{link_id}/revoke")
def revoke_caregiver_link(link_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db_session)):
    link = db.query(CaregiverLink).filter(CaregiverLink.id == link_id).first()
    if not link:
        raise HTTPException(404, f"No caregiver link {link_id}")
    patient = get_patient_or_404(db, link.patient_id)
    if not (user.role == "patient" and patient.user_id == user.id):
        raise HTTPException(403, "Only the patient can revoke a caregiver's access.")
    link.status = "revoked"
    db.commit()
    log_audit(db, link.patient_id, f"patient:{user.id}", "caregiver_link_revoked", f"link {link.id}")
    return {"id": link.id, "status": link.status}


# ---------------------------------------------------------------- caregiver side

@router.post("/caregiver/link/redeem")
def redeem_caregiver_link(body: LinkRedeem, user: User = Depends(require_caregiver_role),
                           db: Session = Depends(get_db_session)):
    code = body.code.strip().upper()
    link = db.query(CaregiverLink).filter(CaregiverLink.code == code).first()
    if not link:
        raise HTTPException(404, "Invalid invite code.")
    if link.status == "revoked":
        raise HTTPException(409, "This invite has been revoked.")
    if link.status == "active" and link.caregiver_user_id != user.id:
        raise HTTPException(409, "This invite has already been used by another caregiver.")

    link.caregiver_user_id = user.id
    link.status = "active"
    link.accepted_at = datetime.utcnow()
    db.commit()
    log_audit(db, link.patient_id, f"caregiver:{user.id}", "caregiver_link_accepted", f"link {link.id}")

    patient = get_patient_or_404(db, link.patient_id)
    return {"linked": True, "patient": serialize_patient(patient)}


@router.get("/caregiver/patients")
def caregiver_patients(user: User = Depends(require_caregiver_role), db: Session = Depends(get_db_session)):
    links = db.query(CaregiverLink).filter(
        CaregiverLink.caregiver_user_id == user.id, CaregiverLink.status == "active",
    ).all()
    patients = [
        {**serialize_patient(get_patient_or_404(db, link.patient_id)),
         "priority": compute_priority(db, link.patient_id)}
        for link in links
    ]
    order = {"emergency": 0, "high": 1, "medium": 2, "routine": 3}
    patients.sort(key=lambda p: order[p["priority"]["level"]])
    return patients


@router.get("/caregiver/patients/{patient_id}/overview")
def caregiver_patient_overview(patient_id: int, user: User = Depends(require_caregiver_role),
                                db: Session = Depends(get_db_session)):
    if not has_read_access(db, user, patient_id):
        raise HTTPException(403, "You are not linked to this patient.")

    patient = get_patient_or_404(db, patient_id)
    dash = gather_dashboard_data(db, patient_id, INTERACTION_RULESET, FOOD_RULESET)
    nudges = compute_nudges(dash["medicines_with_doses"])

    recent_checks = (
        db.query(SymptomCheck)
        .filter(SymptomCheck.patient_id == patient_id)
        .order_by(SymptomCheck.created_at.desc())
        .limit(5)
        .all()
    )
    ever_emergency = any(c.severity == "EMERGENCY" for c in recent_checks)

    cutoff = datetime.utcnow() - timedelta(hours=24)
    missed_recent_count = sum(
        1 for m in dash["prescriptions"] for med in m.medicines for d in med.doses
        if d.state == "missed" and d.acted_at and d.acted_at >= cutoff
    )

    alerts = []
    if dash["adherence"]["adherence_percent"] is not None and dash["adherence"]["adherence_percent"] < 80:
        alerts.append(f"Adherence is {dash['adherence']['adherence_percent']}% — below the 80% watch line.")
    if missed_recent_count:
        alerts.append(f"{missed_recent_count} dose(s) were missed in the last 24 hours.")
    if ever_emergency:
        alerts.append("An EMERGENCY-graded symptom check is in this patient's recent history.")
    if dash["unconfirmed_medicines"]:
        alerts.append(f"{len(dash['unconfirmed_medicines'])} medicine(s) are unconfirmed and were never scheduled.")

    return {
        "patient": serialize_patient(patient),
        "priority": compute_priority(db, patient_id),
        "adherence": dash["adherence"],
        "per_medicine": dash["per_medicine"],
        "upcoming_doses": dash["upcoming_doses"],
        "prn_medicines": dash["prn_medicines"],
        "unconfirmed_medicines": dash["unconfirmed_medicines"],
        "nudges": nudges,
        "alerts": alerts,
        "recent_symptom_checks": [
            {"id": c.id, "created_at": c.created_at.isoformat(), "severity": c.severity, "action": c.action}
            for c in recent_checks
        ],
        "emergency_card": emergency_card_data(db, patient_id),
    }


# ---------------------------------------------------------------- notes the caregiver writes

def _require_link(db: Session, user: User, patient_id: int) -> None:
    if not has_read_access(db, user, patient_id):
        raise HTTPException(403, "You are not linked to this patient.")


@router.get("/caregiver/patients/{patient_id}/notes")
def caregiver_list_notes(patient_id: int, kind: Optional[str] = None, medicine_id: Optional[int] = None,
                         user: User = Depends(require_caregiver_role), db: Session = Depends(get_db_session)):
    _require_link(db, user, patient_id)
    return {"notes": care_notes.list_notes(db, patient_id, "caregiver", kind=kind, medicine_id=medicine_id),
            "disclaimer": care_notes.DISCLAIMER}


@router.post("/caregiver/patients/{patient_id}/notes")
def caregiver_create_note(patient_id: int, body: CareNoteCreate, user: User = Depends(require_caregiver_role),
                          db: Session = Depends(get_db_session)):
    _require_link(db, user, patient_id)
    try:
        note = care_notes.create_note(db, patient_id, user, body.kind, body.body, body.medicine_id)
    except care_notes.NoteRateLimited as e:
        raise HTTPException(429, str(e))
    except care_notes.NoteError as e:
        raise HTTPException(422, str(e))
    log_audit(db, patient_id, f"caregiver:{user.id}", "care_note_created", f"{note.kind} note {note.id}")
    out = care_notes.serialize_note(note)
    out["notified"] = 0
    if body.notify and note.kind == "message":
        text = " ".join(note.body.split())
        out["notified"] = care_push.push_to_patient(db, patient_id, f"Note from {user.name}", text[:80], tag=f"care-note-{note.id}")
    return out


@router.delete("/caregiver/notes/{note_id}")
def caregiver_delete_note(note_id: int, user: User = Depends(require_caregiver_role), db: Session = Depends(get_db_session)):
    from db import CareNote
    note = db.query(CareNote).filter(CareNote.id == note_id, CareNote.deleted_at.is_(None)).first()
    if note is None or note.author_user_id != user.id:
        raise HTTPException(404, "No such note.")
    _require_link(db, user, note.patient_id)             # a revoked caregiver cannot touch the record any more, even their own note
    care_notes.soft_delete(db, note_id, user)
    log_audit(db, note.patient_id, f"caregiver:{user.id}", "care_note_deleted", f"note {note_id}")
    return {"deleted": True}


# ---------------------------------------------------------------- Remind now

NUDGE_COOLDOWN = timedelta(minutes=15)


@router.post("/caregiver/patients/{patient_id}/nudge")
def caregiver_nudge(patient_id: int, user: User = Depends(require_caregiver_role), db: Session = Depends(get_db_session)):
    """'Remind now': a gentle push to the patient's phone. At most once per 15 minutes per patient (whoever pressed it)."""
    from db import AuditLog
    _require_link(db, user, patient_id)
    last = (db.query(AuditLog).filter(AuditLog.patient_id == patient_id, AuditLog.action == "caregiver_nudge")
            .order_by(AuditLog.at.desc()).first())
    if last is not None and datetime.utcnow() - last.at < NUDGE_COOLDOWN:
        wait = int((NUDGE_COOLDOWN - (datetime.utcnow() - last.at)).total_seconds()) + 1
        raise HTTPException(429, f"You just sent a reminder. You can send another in {max(1, wait // 60 + (1 if wait % 60 else 0))} minute(s).",
                            headers={"Retry-After": str(wait)})
    patient = get_patient_or_404(db, patient_id)
    sent = care_push.push_to_patient(db, patient_id, f"{user.name} is thinking of you",
                                     "Have you taken your medicine? Open SmartPoli to check.", tag=f"care-nudge-{patient_id}")
    log_audit(db, patient_id, f"caregiver:{user.id}", "caregiver_nudge", f"reminder sent to {sent} device(s)")
    return {"sent": sent, "patient": patient.name,
            "note": None if sent else f"{patient.name} has not turned on phone reminders, so nothing could be delivered. Try calling."}
