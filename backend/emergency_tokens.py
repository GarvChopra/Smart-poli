"""
SmartPoli — emergency-card tokens.

The public, no-login emergency page is addressed by a random token, not the
patient id, so the page can't be enumerated and a lost QR/card can be
revoked by rotating the token. This module is the only place tokens are
created, rotated or resolved.
"""

import secrets
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from db import EmergencyCardToken


def _active(db: Session, patient_id: int) -> Optional[EmergencyCardToken]:
    return (db.query(EmergencyCardToken)
            .filter(EmergencyCardToken.patient_id == patient_id,
                    EmergencyCardToken.revoked_at.is_(None))
            .order_by(EmergencyCardToken.id.desc())
            .first())


def get_or_create_active_token(db: Session, patient_id: int) -> str:
    row = _active(db, patient_id)
    if row:
        return row.token
    row = EmergencyCardToken(patient_id=patient_id, token=secrets.token_urlsafe(16))
    db.add(row)
    db.commit()
    return row.token


def rotate_token(db: Session, patient_id: int) -> str:
    # Revoke EVERY active row, not just one: two racing first requests or a
    # double-clicked revoke can leave more than one active, and a revoke
    # that misses one would leave a lost card working.
    (db.query(EmergencyCardToken)
     .filter(EmergencyCardToken.patient_id == patient_id,
             EmergencyCardToken.revoked_at.is_(None))
     .update({"revoked_at": datetime.utcnow()}, synchronize_session=False))
    db.commit()
    return get_or_create_active_token(db, patient_id)


def patient_id_for_token(db: Session, token: str) -> Optional[int]:
    if not token:
        return None
    row = (db.query(EmergencyCardToken)
           .filter(EmergencyCardToken.token == token,
                   EmergencyCardToken.revoked_at.is_(None))
           .first())
    return row.patient_id if row else None
