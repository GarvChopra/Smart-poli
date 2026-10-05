"""
The Timeline screen: just which medicine, when, and whether it was taken - nothing else.

Rows come from the audit log, but only dose outcomes are shown (taken / missed / skipped). Prescriptions, settings,
routine changes, links and other bookkeeping stay in the log and never appear here.
"""

import re

from db import Dose, Medicine

_DOSE_ID = re.compile(r"\bdose (\d+)\b")
_MED_ID = re.compile(r"\bmedicine (\d+)\b")

# audit action -> what the screen shows
STATUS = {
    "dose_taken": "taken", "dose_taken_late": "taken", "dose_taken_via_voice": "taken", "prn_taken": "taken",
    "dose_missed": "missed", "dose_auto_missed": "missed", "dose_skipped": "skipped",
}


def friendly_entries(db, entries) -> list:
    entries = [e for e in entries if e.action in STATUS]
    dose_ids = {int(m.group(1)) for e in entries if (m := _DOSE_ID.search(e.detail or ""))}
    med_ids = {int(m.group(1)) for e in entries if (m := _MED_ID.search(e.detail or ""))}
    doses, meds = {}, {}
    if dose_ids:
        for dose, med in (db.query(Dose, Medicine).join(Medicine, Dose.medicine_id == Medicine.id)
                          .filter(Dose.id.in_(dose_ids)).all()):
            doses[dose.id] = med.name or med.raw_text
    if med_ids:
        for med in db.query(Medicine).filter(Medicine.id.in_(med_ids)).all():
            meds[med.id] = med.name or med.raw_text

    out = []
    for e in entries:
        d = e.detail or ""
        m = _DOSE_ID.search(d)
        n = _MED_ID.search(d)
        name = (doses.get(int(m.group(1))) if m else None) or (meds.get(int(n.group(1))) if n else None) or "A medicine"
        out.append({"id": e.id, "at": e.at.isoformat(), "action": e.action, "status": STATUS[e.action],
                    "summary": name, "detail": "", "actor": ""})
    return out
