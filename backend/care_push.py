"""
Small push helpers for the caregiver features: to a patient's phones, or to one user's (caregiver's) phones.
Both look webpush_service.send up at call time (so tests can replace it), delete a subscription the push service says is
gone, and never raise: a push problem must not fail the request that triggered it.
"""

import logging
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy.orm import Session

import webpush_service
from db import PushSubscription, UserPushSubscription, mark_push_endpoint_dead

logger = logging.getLogger(__name__)


def _deliver(db: Session, subs: list, payload: dict, push_fn: Optional[Callable]) -> int:
    if not subs:
        return 0
    push_fn = push_fn or webpush_service.send
    if push_fn is webpush_service.send and not webpush_service.is_configured():
        return 0
    sent = 0
    for sub in subs:
        try:
            status = push_fn(sub.endpoint, sub.p256dh, sub.auth, payload)
        except Exception:  # noqa: BLE001
            logger.exception("push to a caregiver-feature device failed")
            continue
        if status == "sent":
            sub.last_success_at = datetime.utcnow()
            sent += 1
        elif isinstance(sub, PushSubscription):        # a patient's phone: remember why, and never re-use a dead one
            webpush_service.note_failure(db, sub.patient_id, sub.endpoint, status)
            if status == "gone":
                mark_push_endpoint_dead(db, sub)
        elif status == "gone":
            db.delete(sub)
    db.commit()
    return sent


def push_to_patient(db: Session, patient_id: int, title: str, body: str, tag: str, url: str = "/",
                    push_fn: Optional[Callable] = None, extra: Optional[dict] = None) -> int:
    subs = db.query(PushSubscription).filter(PushSubscription.patient_id == patient_id).all()
    return _deliver(db, subs, {"title": title, "body": body, "url": url, "tag": tag, "kind": "care", **(extra or {})}, push_fn)


def push_to_user(db: Session, user_id: int, title: str, body: str, tag: str, url: str = "/caregiver",
                 push_fn: Optional[Callable] = None, extra: Optional[dict] = None) -> int:
    """`extra` adds payload fields (a `kind`, notification `actions`, an `action_token`) the service worker understands."""
    subs = db.query(UserPushSubscription).filter(UserPushSubscription.user_id == user_id).all()
    return _deliver(db, subs, {"title": title, "body": body, "url": url, "tag": tag, "kind": "care", **(extra or {})}, push_fn)
