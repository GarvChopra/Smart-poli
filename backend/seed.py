"""
SmartPoli demo data — three patient personas, one caregiver and one doctor
account, so judges never have to fight a signup form (CLAUDE.md section 14).

Run directly: `python seed.py` (from backend/). Talks to the same DB the
API uses (SMARTPOLI_DATABASE_URL, defaulting to ./smartpoli.db).

  1. Ramesh Kumar  — long-term BP/diabetes meds, day 4 of a 30-day course,
                     adherence with a couple of genuine misses, one LOW triage.
                     Linked to the demo caregiver AND the demo doctor.
  2. Anita Sharma  — a fully-adherent 5-day antibiotic course, one MODERATE
                     triage check (routes to the doctor-booking flow).
                     Linked to the demo doctor.
  3. Vikram Singh  — post-op: a PRN painkiller, ONE malformed line left
                     needs_confirmation on purpose (to demo the gate), and
                     one EMERGENCY triage check (to demo the red banner).
                     Linked to the demo doctor.

All three are linked to the demo doctor on purpose — the priority queue is
one of this app's differentiators and looks like nothing on a queue of one.
With all three, the doctor's patient list shows one of each level: Ramesh
= medium (a recent missed dose), Anita = medium (a recent MODERATE check),
Vikram = emergency — so the "why is this patient here" reasons are visibly
different patient to patient, not just theoretical.

Every persona now needs a real account — auth.py added real login on top of
this same data model, so seeding fake AuditLog actors is no longer enough.
All demo passwords are `demo1234` — kept short on purpose for live demos.
"""

import json
from datetime import datetime, timedelta

from db import (
    init_db, SessionLocal, Patient, Prescription, Medicine, Dose, SymptomCheck, log_audit,
    User, CaregiverLink, DoctorLink,
)
from auth import hash_password, gen_link_code
from parser import parse_medicine_line
from scheduler import generate_doses, mark_taken, mark_missed
from triage import load_ruleset, evaluate_check

RULESET = load_ruleset()
DEMO_PASSWORD = "demo1234"


def add_user(db, email, name, role):
    user = User(email=email, password_hash=hash_password(DEMO_PASSWORD), role=role, name=name)
    db.add(user)
    db.commit()
    return user


def add_prescription(db, patient, doctor_name, lines, confirm=True):
    prescription = Prescription(patient_id=patient.id, doctor_name=doctor_name,
                                 issued_date=datetime.utcnow().date().isoformat(),
                                 source="manual", status="draft")
    db.add(prescription)
    db.commit()

    medicines = []
    for line in lines:
        parsed = parse_medicine_line(line)
        medicine = Medicine(
            prescription_id=prescription.id, raw_text=parsed["raw_text"],
            name=parsed["name"], normalized_name=parsed["normalized_name"],
            dose_amount=parsed["dose_amount"], dose_unit=parsed["dose_unit"],
            schedule_code=parsed["schedule_code"], slots=json.dumps(parsed["slots"]),
            times=json.dumps(parsed["times"]), food=parsed["food"],
            duration_days=parsed["duration_days"], is_prn=parsed["is_prn"],
            confidence=parsed["confidence"], field_confidence=json.dumps(parsed["field_confidence"]),
            status=parsed["status"],
        )
        db.add(medicine)
        medicines.append(medicine)
    db.commit()

    if confirm:
        for medicine in medicines:
            if medicine.status == "needs_confirmation":
                continue
            doses = generate_doses(medicine)
            db.add_all(doses)
        prescription.status = "confirmed"
        db.commit()

    return prescription, medicines


def seed():
    init_db()
    db = SessionLocal()
    try:
        if db.query(Patient).count() > 0:
            print("Demo data already present — skipping (delete smartpoli.db to reseed).")
            return

        # ---------------------------------------------------- Accounts
        ramesh_user = add_user(db, "ramesh@smartpoli.demo", "Ramesh Kumar", "patient")
        anita_user = add_user(db, "anita@smartpoli.demo", "Anita Sharma", "patient")
        vikram_user = add_user(db, "vikram@smartpoli.demo", "Vikram Singh", "patient")
        caregiver_user = add_user(db, "caregiver@smartpoli.demo", "Suresh Kumar", "caregiver")
        doctor_user = add_user(db, "doctor@smartpoli.demo", "Dr. Rao", "doctor")

        # ---------------------------------------------------- Persona 1
        ramesh = Patient(user_id=ramesh_user.id, name="Ramesh Kumar", age=58, sex="M", blood_group="B+",
                          allergies="Penicillin", emergency_contact="Suresh Kumar (son), +91 98765 43210")
        db.add(ramesh)
        db.commit()

        pres, meds = add_prescription(db, ramesh, "Dr. Iyer", [
            "Tab Telma 40mg OD x30d",
            "Tab Metformin 500mg 1-0-1 continue",
            "Tab Brufen 400mg BD x3d",  # demos the drug-interaction check: telmisartan + ibuprofen
        ])
        db.commit()
        # Day 1-3 mostly taken, one missed dose on day 2 (evening Metformin).
        for m in meds:
            for i, dose in enumerate(sorted(m.doses, key=lambda d: d.scheduled_at)[:6]):
                dose.scheduled_at = datetime.utcnow() - timedelta(days=3) + timedelta(hours=i * 8)
                if m.name == "Metformin" and i == 3:
                    mark_missed(dose, acted_at=dose.scheduled_at + timedelta(hours=3))
                else:
                    mark_taken(dose, acted_at=dose.scheduled_at + timedelta(minutes=10))
        db.commit()

        low_check = evaluate_check(RULESET, ["fever"], {"temp_above_103": False, "duration_over_3_days": False,
                                                          "stiff_neck": False, "rash_with_fever": False})
        db.add(SymptomCheck(patient_id=ramesh.id, symptoms=json.dumps(["fever"]),
                             answers=json.dumps({"temp_above_103": False}), severity=low_check["severity"],
                             reasons=json.dumps(low_check["reasons"]), action=low_check["action"],
                             ruleset_version=low_check["ruleset_version"]))
        log_audit(db, ramesh.id, "seed", "demo_seeded", "Ramesh Kumar persona")

        # Demo caregiver is linked to Ramesh, active, ready to explore.
        ramesh_caregiver_link = CaregiverLink(patient_id=ramesh.id, caregiver_user_id=caregiver_user.id,
                                                code=gen_link_code(), status="active", accepted_at=datetime.utcnow())
        db.add(ramesh_caregiver_link)
        # Also linked to the demo doctor — see module docstring: all three
        # personas are linked to the doctor so the priority queue shows real
        # variety (this one lands on "medium" from the recent missed dose).
        ramesh_doctor_link = DoctorLink(patient_id=ramesh.id, doctor_user_id=doctor_user.id,
                                          code=gen_link_code(), status="active", accepted_at=datetime.utcnow())
        db.add(ramesh_doctor_link)
        db.commit()

        # ---------------------------------------------------- Persona 2
        anita = Patient(user_id=anita_user.id, name="Anita Sharma", age=34, sex="F", blood_group="O+",
                         emergency_contact="Rina Sharma (sister), +91 91234 56780")
        db.add(anita)
        db.commit()

        pres2, meds2 = add_prescription(db, anita, "Dr. Fernandes", [
            "Cap Amoxicillin 500mg TDS AC 5 days",
        ])
        db.commit()
        for m in meds2:
            for i, dose in enumerate(sorted(m.doses, key=lambda d: d.scheduled_at)[:6]):
                dose.scheduled_at = datetime.utcnow() - timedelta(days=2) + timedelta(hours=i * 6)
                mark_taken(dose, acted_at=dose.scheduled_at + timedelta(minutes=5))
        db.commit()

        moderate_check = evaluate_check(RULESET, ["headache"], {"vision_changes": True, "worst_ever_sudden": False,
                                                                  "confusion": False, "fever_and_stiff_neck": False})
        db.add(SymptomCheck(patient_id=anita.id, symptoms=json.dumps(["headache"]),
                             answers=json.dumps({"vision_changes": True}), severity=moderate_check["severity"],
                             reasons=json.dumps(moderate_check["reasons"]), action=moderate_check["action"],
                             ruleset_version=moderate_check["ruleset_version"]))
        log_audit(db, anita.id, "seed", "demo_seeded", "Anita Sharma persona")

        # Linked to the demo doctor too (lands on "medium" from the recent
        # MODERATE-graded check — see module docstring).
        anita_doctor_link = DoctorLink(patient_id=anita.id, doctor_user_id=doctor_user.id,
                                         code=gen_link_code(), status="active", accepted_at=datetime.utcnow())
        db.add(anita_doctor_link)
        db.commit()

        # ---------------------------------------------------- Persona 3
        vikram = Patient(user_id=vikram_user.id, name="Vikram Singh", age=45, sex="M", blood_group="A-",
                         allergies="Sulfa drugs, Latex", emergency_contact="Priya Singh (wife), +91 99887 76655")
        db.add(vikram)
        db.commit()

        pres3, meds3 = add_prescription(db, vikram, "Dr. Rao", [
            "Tab Tramadol 50mg SOS",
            "Tab Simvastatin 20mg OD",  # demos the food warning: avoid grapefruit
            "Tab Cefixime 1-?-1",  # deliberately malformed — demos the confidence gate
        ], confirm=True)
        db.commit()

        emergency_check = evaluate_check(RULESET, ["chest_pain"], {"difficulty_breathing": True})
        db.add(SymptomCheck(patient_id=vikram.id, symptoms=json.dumps(["chest_pain"]),
                             answers=json.dumps({"difficulty_breathing": True}), severity=emergency_check["severity"],
                             reasons=json.dumps(emergency_check["reasons"]), action=emergency_check["action"],
                             ruleset_version=emergency_check["ruleset_version"]))
        log_audit(db, vikram.id, "seed", "demo_seeded", "Vikram Singh persona")

        # Demo doctor is linked to Vikram too — pairs with the malformed
        # line (needs_confirmation gate) and the EMERGENCY triage check, so
        # a doctor login has something worth reviewing and correcting, on
        # top of Ramesh and Anita already linked above.
        vikram_link = DoctorLink(patient_id=vikram.id, doctor_user_id=doctor_user.id,
                                  code=gen_link_code(), status="active", accepted_at=datetime.utcnow())
        db.add(vikram_link)

        db.commit()
        print(f"Seeded 3 demo patients: Ramesh Kumar (id={ramesh.id}), "
              f"Anita Sharma (id={anita.id}), Vikram Singh (id={vikram.id}).")
        print(f"\nDemo logins (password for all: {DEMO_PASSWORD}):")
        print("  Patient   ramesh@smartpoli.demo    (also has an active caregiver: caregiver@smartpoli.demo)")
        print("  Patient   anita@smartpoli.demo")
        print("  Patient   vikram@smartpoli.demo")
        print("  Caregiver caregiver@smartpoli.demo (linked to Ramesh Kumar)")
        print("  Doctor    doctor@smartpoli.demo    (linked to all 3: Ramesh, Anita, Vikram)")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
