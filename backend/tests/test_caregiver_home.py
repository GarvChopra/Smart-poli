"""The caregiver phone home: family rows by nickname, today's doses below them, and Upload PDF for the patient."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from db import init_db  # noqa: E402
from main import app  # noqa: E402
from test_care_notes_api import link_caregiver  # noqa: E402
from timing_helpers import new_patient  # noqa: E402

MED = {"name": "Amlodipine", "strength": "5mg", "form": "tab", "as_needed": False, "times": ["08:00", "20:00"],
       "weekdays": None, "food": "any", "duration_days": 5}


def _world():
    init_db()
    patient = TestClient(app)
    pid, _ = new_patient(patient, "Anita")
    cg, _ = link_caregiver(patient, pid, "Sita")
    return pid, patient, cg


def test_home_has_you_first_family_below_and_today_is_only_my_own_doses():
    pid, patient, cg = _world()
    patient.post("/medicines/manual", json={**MED, "patient_id": pid})              # Mum takes Amlodipine
    home = cg.get("/caregiver/home").json()
    row = [f for f in home["family"] if f["id"] == pid][0]
    assert row["label"] == "Anita" and row["medicines_today"] == 1
    # the caregiver has no medicines of their own yet: Today is empty - Mum's dose is NOT mixed into it
    assert home["you"]["patient_id"] is None and home["today"] == []
    mine = cg.post("/patients", json={"name": "Sita"}).json()["id"]
    cg.post("/medicines/manual", json={**MED, "name": "Metformin", "patient_id": mine})
    home = cg.get("/caregiver/home").json()
    assert home["you"]["patient_id"] == mine and home["you"]["medicines_today"] == 1
    assert {d["medicine_name"] for d in home["today"]} == {"Metformin"}
    assert all(d["state"] in ("pending", "taken", "missed", "skipped", "snoozed") for d in home["today"])


def test_a_nickname_is_what_this_caregiver_sees_and_can_be_cleared():
    pid, patient, cg = _world()
    assert cg.patch(f"/caregiver/patients/{pid}/nickname", json={"nickname": "  Mum "}).json()["nickname"] == "Mum"
    home = cg.get("/caregiver/home").json()
    assert [f["label"] for f in home["family"] if f["id"] == pid] == ["Mum"]
    assert cg.get(f"/caregiver/patients/{pid}/overview").json()["nickname"] == "Mum"
    assert cg.patch(f"/caregiver/patients/{pid}/nickname", json={"nickname": ""}).json()["nickname"] is None
    assert [f["label"] for f in cg.get("/caregiver/home").json()["family"] if f["id"] == pid] == ["Anita"]


def test_a_caregiver_cannot_nickname_someone_they_are_not_linked_to():
    pid, patient, cg = _world()
    other = TestClient(app)
    other_pid, _ = new_patient(other, "Stranger")
    assert cg.patch(f"/caregiver/patients/{other_pid}/nickname", json={"nickname": "Dad"}).status_code == 403


def _pdf(lines):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 780
    for line in lines:
        c.drawString(60, y, line)
        y -= 22
    c.save()
    return buf.getvalue()


def test_pdf_gives_medicine_candidates_and_saves_nothing():
    pid, patient, cg = _world()
    pdf = _pdf(["Dr. Rao Clinic", "Tab Paracetamol 650mg 1-0-1 PC x5d", "Cap Amoxicillin 500mg TDS AC 7 days"])
    r = cg.post("/medicines/from-pdf", data={"patient_id": pid}, files={"file": ("rx.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    names = [c["name"].lower() for c in r.json()["candidates"]]
    assert "paracetamol" in names and "amoxicillin" in names
    assert cg.get(f"/caregiver/patients/{pid}/overview").json()["per_medicine"] == []


def test_pdf_with_no_medicine_in_it_is_refused_and_an_unlinked_user_is_blocked():
    pid, patient, cg = _world()
    r = cg.post("/medicines/from-pdf", data={"patient_id": pid},
                files={"file": ("bill.pdf", _pdf(["Electricity bill", "Amount due 1200"]), "application/pdf")})
    assert r.status_code == 422
    stranger = TestClient(app)
    new_patient(stranger, "Someone")
    r = stranger.post("/medicines/from-pdf", data={"patient_id": pid},
                      files={"file": ("rx.pdf", _pdf(["Tab Paracetamol 650mg 1-0-1"]), "application/pdf")})
    assert r.status_code == 403
