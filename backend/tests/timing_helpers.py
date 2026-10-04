"""Shared fixtures for the timing / reminder tests (no real patient data)."""

import json
import os
import sys
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Dose, Medicine, Prescription, SessionLocal, init_db  # noqa: E402


def new_patient(client, name="Timing Test"):
    from conftest import register_and_login
    user = register_and_login(client)
    pid = client.post("/patients", json={"name": name}).json()["id"]
    return pid, user


def add_medicine(patient_id: int, name: str, dose_times: list[datetime], states: Optional[list[str]] = None,
                 food: str = "any", acted: Optional[list] = None, duration_days: Optional[int] = None,
                 is_prn: bool = False) -> tuple[int, list[int]]:
    """A confirmed medicine with doses at exactly the given (patient-local) times."""
    init_db()
    db = SessionLocal()
    try:
        pres = Prescription(patient_id=patient_id, status="confirmed", source="manual")
        db.add(pres)
        db.commit()
        med = Medicine(prescription_id=pres.id, raw_text=name, name=name, normalized_name=name,
                       dose_amount="500", dose_unit="mg", schedule_code="OD", times=json.dumps([]),
                       food=food, status="verified", confidence=1.0, duration_days=duration_days, is_prn=is_prn)
        db.add(med)
        db.commit()
        ids = []
        for i, t in enumerate(dose_times):
            d = Dose(medicine_id=med.id, scheduled_at=t, state=(states[i] if states else "pending"),
                     acted_at=(acted[i] if acted else None))
            db.add(d)
            db.commit()
            ids.append(d.id)
        return med.id, ids
    finally:
        db.close()
