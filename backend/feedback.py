"""
Feedback from patients and caregivers: validation, the hourly limit, and the owner's summary.
Text is stored and returned verbatim; every screen escapes it when it draws it.
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from db import Feedback, User

MAX_LEN = 1000
RATE_LIMIT = 5                       # submissions per user per hour
CATEGORIES = ("problem", "idea", "praise", "other")


class FeedbackError(ValueError):
    pass


class FeedbackRateLimited(FeedbackError):
    pass


def create(db: Session, user: User, rating: int, category: str, message: str, contact_ok: bool, source: str) -> Feedback:
    if not isinstance(rating, int) or isinstance(rating, bool) or not 1 <= rating <= 5:
        raise FeedbackError("Choose a rating from 1 to 5 stars.")
    if category not in CATEGORIES:
        raise FeedbackError("Choose what this is about.")
    text = (message or "").strip()
    if len(text) > MAX_LEN:
        raise FeedbackError(f"Please keep it under {MAX_LEN} characters.")
    since = datetime.utcnow() - timedelta(hours=1)
    if db.query(Feedback).filter(Feedback.user_id == user.id, Feedback.created_at >= since).count() >= RATE_LIMIT:
        raise FeedbackRateLimited("You have sent a lot of feedback in the last hour. Thank you - please try again a little later.")
    row = Feedback(user_id=user.id, role=user.role, rating=rating, category=category, message=text,
                   contact_ok=bool(contact_ok), source=source if source in ("page", "popup") else "page")
    db.add(row)
    db.commit()
    return row


def serialize(f: Feedback) -> dict:
    return {"id": f.id, "rating": f.rating, "category": f.category, "message": f.message, "source": f.source,
            "created_at": f.created_at.isoformat()}


def mine(db: Session, user: User, limit: int = 50) -> list:
    rows = db.query(Feedback).filter(Feedback.user_id == user.id).order_by(Feedback.created_at.desc(), Feedback.id.desc()).limit(limit).all()
    return [serialize(f) for f in rows]


def summary(db: Session, limit: int = 200) -> dict:
    """For the owner: counts, average, and the latest items. A person's email is included only if they ticked 'you can contact me'."""
    rows = db.query(Feedback).order_by(Feedback.created_at.desc(), Feedback.id.desc()).all()
    by_rating = {str(i): 0 for i in range(1, 6)}
    for f in rows:
        by_rating[str(f.rating)] += 1
    emails = {u.id: u.email for u in db.query(User).filter(User.id.in_({f.user_id for f in rows if f.contact_ok})).all()} if rows else {}
    return {
        "count": len(rows),
        "average": round(sum(f.rating for f in rows) / len(rows), 2) if rows else None,
        "by_rating": by_rating,
        "items": [{**serialize(f), "role": f.role, "contact_ok": f.contact_ok,
                   "email": emails.get(f.user_id) if f.contact_ok else None} for f in rows[:limit]],
    }
