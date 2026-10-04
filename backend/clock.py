"""
SmartPoli — patient-local time.

Dose.scheduled_at is stored as the patient's local wall-clock time (a
naive datetime: "08:00" means 08:00 where the patient is). The server,
however, runs in UTC (Render). Comparing the two directly made every
reminder and every auto-"missed" fire hours off for a patient in India.
Everything that asks "is this dose due yet?" must therefore ask the
patient's own clock, via patient_now().

The patient's timezone lives in PatientSettings (a separate table rather
than a column on `patients`: init_db() can create tables on Postgres but
not alter existing ones). The browser reports the device timezone and the
app saves it, so a patient who travels is followed automatically.
"""

import os
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# India-first product; tests pin this to UTC so fixtures can keep using utcnow().
DEFAULT_TZ = os.getenv("SMARTPOLI_DEFAULT_TZ", "Asia/Kolkata")

# Largest UTC offset on Earth is +14:00. Used for cheap SQL pre-filters:
# a dose scheduled later than (utc_now + 14h) cannot be due for anyone.
MAX_UTC_OFFSET = timedelta(hours=14)


def is_valid_timezone(name: Optional[str]) -> bool:
    if not name or len(name) > 64:
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def resolve_timezone(name: Optional[str]) -> str:
    """A valid IANA name, falling back to the configured default."""
    return name if is_valid_timezone(name) else (DEFAULT_TZ if is_valid_timezone(DEFAULT_TZ) else "UTC")


def local_now(tz_name: Optional[str], utc_now: Optional[datetime] = None) -> datetime:
    """Naive wall-clock 'now' in the given timezone (matches Dose.scheduled_at)."""
    from datetime import timezone
    tz = ZoneInfo(resolve_timezone(tz_name))
    base = (utc_now.replace(tzinfo=timezone.utc) if utc_now is not None else datetime.now(timezone.utc))
    return base.astimezone(tz).replace(tzinfo=None)


def patient_timezone(db, patient_id: int) -> str:
    from db import PatientSettings
    row = db.query(PatientSettings).filter(PatientSettings.patient_id == patient_id).first()
    return resolve_timezone(row.timezone if row else None)


def patient_now(db, patient_id: int, utc_now: Optional[datetime] = None) -> datetime:
    return local_now(patient_timezone(db, patient_id), utc_now)
