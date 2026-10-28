"""
SmartPoli — Caregiver linking + caregiver dashboard (Part 2 of the brief).

A caregiver never gets access by typing a patient id into a box. The
patient generates a one-time invite code; a caregiver account redeems it;
only then does an active CaregiverLink row exist, and every caregiver
endpoint here checks that row via auth.is_linked_caregiver (an active link) — never a
frontend-supplied role or id.

Reuses the existing dashboard/adherence/triage/emergency-card data
(serializers.py, nudges.py) rather than a second medication system.
"""

from datetime import datetime, timedelta
from typing import Optional

import care_notes
import care_push

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from db import get_db_session, log_audit, User, CaregiverLink, SymptomCheck, Dose, Prescription
from auth import PERSON_ROLES, get_current_user, require_patient_write_access, require_caregiver_role, is_linked_caregiver, gen_link_code
from schemas import PushActionToken, LinkRedeem, LinkNickname, CareNoteCreate, CaregiverPrefsUpdate, PushSubscribeRequest, PushUnsubscribeRequest
from serializers import get_patient_or_404, serialize_patient, gather_dashboard_data, emergency_card_data
from doctor_intelligence import compute_priority
from web_security import rate_limit
from family_router import public_base_url
from nudges import compute_nudges
from interactions import load_ruleset as load_interaction_ruleset
from food_warnings import load_ruleset as load_food_ruleset

router = APIRouter(tags=["caregiver"])

INTERACTION_RULESET = load_interaction_ruleset()
FOOD_RULESET = load_food_ruleset()


# ---------------------------------------------------------------- patient side: manage invites

@router.post("/patients/{patient_id}/caregiver-links")
def create_caregiver_link(patient_id: int, request: Request, user: User = Depends(require_patient_write_access),
                           db: Session = Depends(get_db_session)):
    get_patient_or_404(db, patient_id)
    code = gen_link_code()
    while db.query(CaregiverLink).filter(CaregiverLink.code == code).first():
        code = gen_link_code()
    link = CaregiverLink(patient_id=patient_id, code=code, status="pending")
    db.add(link)
    db.commit()
    log_audit(db, patient_id, f"patient:{user.id}", "caregiver_link_created", f"invite issued (link {link.id})")
    return {"id": link.id, "code": link.code, "status": link.status, "url": f"{public_base_url(request)}/join/{link.code}"}


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
    if not (user.role in PERSON_ROLES and patient.user_id == user.id):
        raise HTTPException(403, "Only the patient can revoke a caregiver's access.")
    link.status = "revoked"
    db.commit()
    log_audit(db, link.patient_id, f"patient:{user.id}", "caregiver_link_revoked", f"link {link.id}")
    return {"id": link.id, "status": link.status}


# ---------------------------------------------------------------- caregiver side

LINK_CODE_DAYS = 7


def _usable_link(db: Session, code: str, user: User) -> CaregiverLink:
    """The code a patient made to share their medicines. Unknown, used by someone else, revoked or older than a week:
    refused with a plain message (an old code lying in a chat must not stay a key forever)."""
    clean = "".join(ch for ch in (code or "") if ch.isalnum()).upper()          # "K7Q3-XM2P", " k7q3xm2p " are the same code
    link = db.query(CaregiverLink).filter(CaregiverLink.code == clean).first()
    if not link:
        raise HTTPException(404, "Invalid invite code.")
    if link.status == "revoked":
        raise HTTPException(409, "This invite has been revoked.")
    if link.status == "active" and link.caregiver_user_id != user.id:
        raise HTTPException(409, "This invite has already been used by another caregiver.")
    if link.status == "pending" and link.created_at and datetime.utcnow() - link.created_at > timedelta(days=LINK_CODE_DAYS):
        raise HTTPException(410, "This invite has expired. Ask for a new one.")
    return link


@router.get("/caregiver/link/{code}")
def preview_caregiver_link(code: str, user: User = Depends(require_caregiver_role), db: Session = Depends(get_db_session),
                           _rl=Depends(rate_limit("family-code", 30, 3600))):
    """Whose code is this? Only the name, shown before the caregiver confirms. Signed in only."""
    link = _usable_link(db, code, user)
    patient = get_patient_or_404(db, link.patient_id)
    owner = patient.user_id == user.id
    return {"patient_name": patient.name, "own": owner}


@router.post("/caregiver/link/redeem")
def redeem_caregiver_link(body: LinkRedeem, user: User = Depends(require_caregiver_role),
                           db: Session = Depends(get_db_session), _rl=Depends(rate_limit("family-code", 30, 3600))):
    link = _usable_link(db, body.code, user)
    first_time = link.status == "pending"
    if get_patient_or_404(db, link.patient_id).user_id == user.id:
        raise HTTPException(400, "This is your own code. Send it to the person who will look after you.")

    link.caregiver_user_id = user.id
    link.nickname = (body.nickname or "").strip() or link.nickname
    link.status = "active"
    link.accepted_at = datetime.utcnow()
    db.commit()
    log_audit(db, link.patient_id, f"caregiver:{user.id}", "caregiver_link_accepted", f"link {link.id}")
    if first_time:
        try:           # the patient hears about it at once, so a code that reached the wrong person is noticed straight away
            care_push.push_to_patient(db, link.patient_id, f"{user.name} can now see your medicines",
                                      "Not right? Open SmartPoli > Settings and remove them.", tag=f"caregiver-joined-{link.id}")
        except Exception:  # noqa: BLE001 - a notification problem must never undo the link the patient asked for
            pass

    patient = get_patient_or_404(db, link.patient_id)
    return {"linked": True, "patient": serialize_patient(patient)}


@router.get("/caregiver/patients")
def caregiver_patients(user: User = Depends(require_caregiver_role), db: Session = Depends(get_db_session)):
    links = db.query(CaregiverLink).filter(
        CaregiverLink.caregiver_user_id == user.id, CaregiverLink.status == "active",
    ).all()
    patients = [
        {**serialize_patient(get_patient_or_404(db, link.patient_id)), "nickname": link.nickname,
         "priority": compute_priority(db, link.patient_id)}
        for link in links
    ]
    order = {"emergency": 0, "high": 1, "medium": 2, "routine": 3}
    patients.sort(key=lambda p: order[p["priority"]["level"]])
    return patients


@router.post("/caregiver/patients/{patient_id}/unlink")
def caregiver_stop_looking_after(patient_id: int, user: User = Depends(require_caregiver_role),
                                 db: Session = Depends(get_db_session)):
    """The caregiver stops looking after this person. Only THEIR own link ends: the person, their data and anyone else
    who looks after them are untouched. They can be added again with a new code or invite."""
    link = db.query(CaregiverLink).filter(CaregiverLink.patient_id == patient_id, CaregiverLink.caregiver_user_id == user.id,
                                          CaregiverLink.status == "active").first()
    if link is None:
        raise HTTPException(403, "You are not linked to this patient.")
    link.status = "revoked"
    db.commit()
    log_audit(db, patient_id, f"caregiver:{user.id}", "caregiver_link_left", f"link {link.id}")
    try:                                    # tell the person, so it never happens silently
        care_push.push_to_patient(db, patient_id, f"{user.name} is no longer your caregiver",
                                  "They can no longer see your medicines.", tag=f"caregiver-left-{link.id}")
    except Exception:  # noqa: BLE001 - a notice problem must never undo the removal
        pass
    return {"removed": True}


@router.patch("/caregiver/patients/{patient_id}/nickname")
def set_caregiver_nickname(patient_id: int, body: LinkNickname, user: User = Depends(require_caregiver_role),
                           db: Session = Depends(get_db_session)):
    """Rename how this caregiver sees one patient ("Mum"). Only this caregiver's own link changes; the patient's
    name, and what any other caregiver sees, are untouched."""
    link = db.query(CaregiverLink).filter(
        CaregiverLink.caregiver_user_id == user.id, CaregiverLink.patient_id == patient_id,
        CaregiverLink.status == "active").first()
    if not link:
        raise HTTPException(403, "You are not linked to this patient.")
    link.nickname = (body.nickname or "").strip() or None
    db.commit()
    return {"patient_id": patient_id, "nickname": link.nickname}


def _todays_doses(db: Session, patient_id: int) -> list:
    """One person's doses for today on THEIR clock, the same medicines they see (no removed ones, no unconfirmed
    lines read off a photo), earliest first. Reads only today's rows (one query), not every dose of every medicine."""
    from clock import patient_now
    from db import Medicine
    now = patient_now(db, patient_id)
    start = datetime(now.year, now.month, now.day)
    rows = (db.query(Dose, Medicine).join(Medicine, Dose.medicine_id == Medicine.id)
            .join(Prescription, Medicine.prescription_id == Prescription.id)
            .filter(Prescription.patient_id == patient_id, Dose.scheduled_at >= start, Dose.scheduled_at < start + timedelta(days=1), Dose.state != "cancelled")
            .order_by(Dose.scheduled_at, Dose.id).all())
    out = []
    for d, med in rows:
        if med.status == "needs_confirmation" or not med.name or '"removed"' in (med.field_confidence or ""):
            continue
        strength = f"{med.dose_amount}{med.dose_unit or ''}" if med.dose_amount else ""
        out.append({"dose_id": d.id, "medicine_id": med.id, "medicine_name": med.name, "strength": strength,
                    "state": d.state, "scheduled_at": d.scheduled_at.isoformat()})
    return out


@router.get("/caregiver/home")
def caregiver_home(user: User = Depends(require_caregiver_role), db: Session = Depends(get_db_session)):
    """The phone home: "You" first, then the family this person looks after. `today` is ONLY the caller's own doses -
    each family member's day is one tap away on their own screen, never mixed into yours. Read-only."""
    from db import Patient
    mine = db.query(Patient).filter(Patient.user_id == user.id).order_by(Patient.id).first()
    my_today = _todays_doses(db, mine.id) if mine else []
    you = {"patient_id": mine.id if mine else None, "name": mine.name if mine else user.name,
           "medicines_today": len({d["medicine_id"] for d in my_today})}
    links = db.query(CaregiverLink).filter(
        CaregiverLink.caregiver_user_id == user.id, CaregiverLink.status == "active").all()
    family = []
    for link in links:
        patient = get_patient_or_404(db, link.patient_id)
        doses = _todays_doses(db, link.patient_id)
        family.append({"id": patient.id, "name": patient.name, "nickname": link.nickname,
                       "label": link.nickname or patient.name,
                       "medicines_today": len({d["medicine_id"] for d in doses}),
                       "missed_today": sum(1 for d in doses if d["state"] == "missed")})
    return {"you": you, "family": family, "today": my_today}


@router.get("/caregiver/patients/{patient_id}/overview")
def caregiver_patient_overview(patient_id: int, user: User = Depends(require_caregiver_role),
                                db: Session = Depends(get_db_session)):
    if not is_linked_caregiver(db, user, patient_id):
        raise HTTPException(403, "You are not linked to this patient.")

    patient = get_patient_or_404(db, patient_id)
    my_link = db.query(CaregiverLink).filter(
        CaregiverLink.caregiver_user_id == user.id, CaregiverLink.patient_id == patient_id,
        CaregiverLink.status == "active").first()
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

    from emergency_page import _split_contact
    label, ec_phone = _split_contact(patient.emergency_contact)
    own_phone = db.query(User.phone).filter(User.id == patient.user_id).scalar() if patient.user_id else None
    missed_cutoff = datetime.utcnow() - timedelta(days=14)
    recent_missed = sorted(
        ({"dose_id": d.id, "medicine_name": med.name or med.raw_text, "scheduled_at": d.scheduled_at.isoformat()}
         for pres in dash["prescriptions"] for med in pres.medicines for d in med.doses
         if d.state == "missed" and d.scheduled_at >= missed_cutoff),
        key=lambda x: x["scheduled_at"], reverse=True)[:15]

    added_by = {med.id: pres.added_by_user_id for pres in dash["prescriptions"] for med in pres.medicines}
    # The same medicines the patient sees: not ones they removed, and not unconfirmed lines read off a photo.
    active = {med.id for pres in dash["prescriptions"] for med in pres.medicines
              if med.status != "needs_confirmation" and med.name}
    # "Being taken" right now: as-needed ones, and any with a dose still to come. A finished course drops off the
    # Medicines list (its history stays in the History tab's numbers).
    taking = {med.id for pres in dash["prescriptions"] for med in pres.medicines
              if med.is_prn or any(d.state in ("pending", "snoozed") for d in med.doses)}

    return {
        "phones": {"patient": own_phone, "emergency_contact": {"label": label, "phone": ec_phone} if ec_phone else None},
        "recent_missed": recent_missed,
        "patient": serialize_patient(patient),
        "nickname": my_link.nickname if my_link else None,
        "priority": compute_priority(db, patient_id),
        "adherence": dash["adherence"],
        "per_medicine": [{**m, "added_by_me": added_by.get(m["medicine_id"]) == user.id, "taking": m["medicine_id"] in taking}
                         for m in dash["per_medicine"]
                         if m["medicine_id"] in active],
        "upcoming_doses": dash["upcoming_doses"],
        "prn_medicines": dash["prn_medicines"],
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
    if not is_linked_caregiver(db, user, patient_id):
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
        note = care_notes.create_note(db, patient_id, user, body.kind, body.body, body.medicine_id, author_role="caregiver")
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
    _require_link(db, user, patient_id)
    return perform_nudge(db, user, patient_id)


@router.post("/push/action/nudge")
def caregiver_nudge_from_notification(body: PushActionToken, db: Session = Depends(get_db_session),
                                      _rl=Depends(rate_limit("push-action", 30, 60))):
    """The Remind now button on a missed-dose notification. No login (the service worker has none): the signed token in
    the notification stands in, for this one action only. Checked again here: the account must still exist and still be
    linked to that patient, so removing a caregiver cancels every button already on their phone."""
    import push_actions
    try:
        claims = push_actions.read_nudge_token(body.token)
    except ValueError as e:
        raise HTTPException(401, str(e))
    user = db.query(User).filter(User.id == claims["u"]).first()
    if user is None or user.role not in PERSON_ROLES or not is_linked_caregiver(db, user, claims["p"]):
        raise HTTPException(403, "You are not linked to this patient any more.")
    return perform_nudge(db, user, claims["p"], claims.get("d") or [])


def perform_nudge(db: Session, user: User, patient_id: int, dose_ids: Optional[list] = None) -> dict:
    """Send the reminder (the caller has already proved `user` is linked to `patient_id`)."""
    import notify_copy
    from db import AuditLog
    last = (db.query(AuditLog).filter(AuditLog.patient_id == patient_id, AuditLog.action == "caregiver_nudge")
            .order_by(AuditLog.at.desc()).first())
    if last is not None and datetime.utcnow() - last.at < NUDGE_COOLDOWN:
        wait = int((NUDGE_COOLDOWN - (datetime.utcnow() - last.at)).total_seconds()) + 1
        raise HTTPException(429, f"You just sent a reminder. You can send another in {max(1, wait // 60 + (1 if wait % 60 else 0))} minute(s).",
                            headers={"Retry-After": str(wait)})
    patient = get_patient_or_404(db, patient_id)
    from db import PushSubscription
    phones = db.query(PushSubscription).filter(PushSubscription.patient_id == patient_id).count()
    items = []
    for d in (db.query(Dose).filter(Dose.id.in_([int(i) for i in (dose_ids or [])][:10])).all() if dose_ids else []):
        med = d.medicine
        if med.prescription.patient_id == patient_id:         # only this patient's own doses can be named in the text
            items.append({"name": med.name or med.raw_text, "dose": f"{med.dose_amount}{med.dose_unit or ''}" if med.dose_amount else "",
                          "at": d.scheduled_at})
    msg = notify_copy.nudge_to_patient(user.name, items)
    sent = care_push.push_to_patient(db, patient_id, msg["title"], msg["body"], tag=f"care-nudge-{patient_id}")
    log_audit(db, patient_id, f"caregiver:{user.id}", "caregiver_nudge", f"reminder sent to {sent} device(s)")
    link = db.query(CaregiverLink).filter(CaregiverLink.patient_id == patient_id, CaregiverLink.caregiver_user_id == user.id,
                                          CaregiverLink.status == "active").first()
    return {"sent": sent, "patient": patient.name, "label": (link.nickname if link and link.nickname else patient.name),
            "note": None if sent else (
                f"{patient.name}'s phone could not be reached just now. It will reconnect the next time "
                f"{patient.name} opens SmartPoli. Try calling." if phones else
                f"{patient.name} has not turned on phone reminders, so nothing could be delivered. Try calling.")}


# ---------------------------------------------------------------- phone alerts

@router.post("/caregiver/push/subscribe")
def caregiver_push_subscribe(body: PushSubscribeRequest, user: User = Depends(require_caregiver_role),
                             db: Session = Depends(get_db_session)):
    """Remember this phone for the caregiver's alerts. The same device subscribing again just updates its row."""
    from db import UserPushSubscription
    row = db.query(UserPushSubscription).filter(UserPushSubscription.endpoint == body.endpoint).first()
    if row is None:
        db.add(UserPushSubscription(user_id=user.id, endpoint=body.endpoint, p256dh=body.keys.p256dh, auth=body.keys.auth))
    else:
        row.user_id, row.p256dh, row.auth = user.id, body.keys.p256dh, body.keys.auth
    db.commit()
    return {"subscribed": True}


@router.post("/caregiver/push/unsubscribe")
def caregiver_push_unsubscribe(body: PushUnsubscribeRequest, user: User = Depends(require_caregiver_role),
                               db: Session = Depends(get_db_session)):
    from db import UserPushSubscription
    db.query(UserPushSubscription).filter(UserPushSubscription.endpoint == body.endpoint,
                                          UserPushSubscription.user_id == user.id).delete()
    db.commit()
    return {"subscribed": False}


@router.get("/caregiver/patients/{patient_id}/prefs")
def caregiver_get_prefs(patient_id: int, user: User = Depends(require_caregiver_role), db: Session = Depends(get_db_session)):
    from db import CaregiverPrefs
    _require_link(db, user, patient_id)
    row = db.query(CaregiverPrefs).filter(CaregiverPrefs.user_id == user.id, CaregiverPrefs.patient_id == patient_id).first()
    return {"notify_missed": True if row is None else bool(row.notify_missed)}


@router.put("/caregiver/patients/{patient_id}/prefs")
def caregiver_put_prefs(patient_id: int, body: CaregiverPrefsUpdate, user: User = Depends(require_caregiver_role),
                        db: Session = Depends(get_db_session)):
    from db import CaregiverPrefs
    _require_link(db, user, patient_id)
    row = db.query(CaregiverPrefs).filter(CaregiverPrefs.user_id == user.id, CaregiverPrefs.patient_id == patient_id).first()
    if row is None:
        db.add(CaregiverPrefs(user_id=user.id, patient_id=patient_id, notify_missed=body.notify_missed))
    else:
        row.notify_missed = body.notify_missed
    db.commit()
    return {"notify_missed": body.notify_missed}
