"""
Repair doses that were marked "taken" far too early - the leftovers of the old "Mark taken" bug, which accepted a dose
that was a day or more away. Tapping repeatedly "took" the whole course (and produced "duplicate dose" alerts and a
"next dose in 46 h").

    python tools/repair_early_takes.py                       # DRY RUN: only lists what would change
    python tools/repair_early_takes.py --only-demo           # ... demo accounts (@smartpoli.demo) only
    python tools/repair_early_takes.py --include-past        # also old doses whose time has already passed
    python tools/repair_early_takes.py --apply --confirm-host <part of the database host shown>   # really do it

A dose is "impossible" when it was recorded taken MORE than the 2-hour early window (scheduler.TAKE_EARLY_WINDOW)
before its scheduled time. Those doses are put back to pending (acted time cleared) and each change is written to the
audit log (action "dose_taken_reverted") with the original acted time, so nothing is lost. Doses taken on time or
late are never touched. By default only doses whose time is STILL IN THE FUTURE are repaired: putting an old one back to
pending would make it "missed" overnight, which would rewrite history rather than undo a mistake (use --include-past if
you really want that).

Safety: dry-run by default; --apply refuses to run unless --confirm-host matches the database host that is printed, so
it cannot be pointed at the wrong database by accident. It never deletes rows.
"""

import argparse
import json
import os
import sys
from datetime import timedelta

# Run as `python tools/repair_early_takes.py` from backend/: make the project's modules importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session  # noqa: E402

from scheduler import TAKE_EARLY_WINDOW  # noqa: E402


def find_early_takes(db: Session, only_demo: bool = False, include_past: bool = False) -> list:
    from clock import patient_now
    from db import Dose, Medicine, Patient, Prescription, User
    q = (db.query(Dose, Patient.id.label("pid")).join(Medicine, Dose.medicine_id == Medicine.id)
         .join(Prescription, Medicine.prescription_id == Prescription.id).join(Patient, Prescription.patient_id == Patient.id)
         .filter(Dose.state == "taken", Dose.acted_at.isnot(None)))
    if only_demo:
        q = q.join(User, Patient.user_id == User.id).filter(User.email.like("%@smartpoli.demo"))
    slack = timedelta(minutes=1)
    clocks: dict = {}                                    # each patient's own "now" (dose times are patient-local)
    out = []
    for dose, pid in q.all():
        if dose.acted_at >= dose.scheduled_at - TAKE_EARLY_WINDOW - slack:
            continue
        if not include_past:
            if pid not in clocks:
                clocks[pid] = patient_now(db, pid)
            if dose.scheduled_at <= clocks[pid]:
                continue
        out.append((dose, pid))
    return out


def revert(db: Session, found: list) -> int:
    from db import AuditLog
    for dose, pid in found:
        db.add(AuditLog(patient_id=pid, actor="system:repair", action="dose_taken_reverted",
                        detail=json.dumps({"dose_id": dose.id, "scheduled_at": dose.scheduled_at.isoformat(),
                                           "was_acted_at": dose.acted_at.isoformat(),
                                           "reason": "recorded taken more than 2 h before its time (old Mark-taken bug)"})))
        dose.state, dose.acted_at = "pending", None
    db.commit()
    return len(found)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="really change the data (default is a dry run)")
    ap.add_argument("--include-past", action="store_true", help="also repair doses whose time has already passed (they will become missed)")
    ap.add_argument("--only-demo", action="store_true", help="only accounts whose email ends in @smartpoli.demo")
    ap.add_argument("--confirm-host", default="", help="a fragment of the database host printed below; required with --apply")
    args = ap.parse_args()

    import db as dbmod
    url = dbmod.DATABASE_URL
    host = url.split("@")[-1] if "@" in url else url
    print(f"Database: {host}")
    session = dbmod.SessionLocal()
    try:
        found = find_early_takes(session, args.only_demo, args.include_past)
        by_patient: dict = {}
        for dose, pid in found:
            by_patient.setdefault(pid, []).append(dose)
        print(f"{len(found)} dose(s) recorded taken more than 2 h early{'' if args.include_past else ' and still in the future'}, across {len(by_patient)} patient(s).")
        for pid, doses in sorted(by_patient.items()):
            print(f"  patient {pid}: " + ", ".join(f"#{d.id} due {d.scheduled_at:%d %b %H:%M} (marked {d.acted_at:%d %b %H:%M})"
                                                  for d in doses[:5]) + (" ..." if len(doses) > 5 else ""))
        if not args.apply:
            print("Dry run: nothing changed. Re-run with --apply --confirm-host <host fragment> to repair.")
            return 0
        if not args.confirm_host or args.confirm_host not in host:
            print("Refusing to apply: --confirm-host must match the database host shown above.")
            return 2
        print(f"Reverted {revert(session, found)} dose(s) to pending.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
