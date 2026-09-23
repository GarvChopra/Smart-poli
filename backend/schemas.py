"""SmartPoli API request/response schemas (Pydantic). Thin mirrors of db.py's
canonical ORM model for (de)serialisation only — not a second data shape."""

from typing import Optional
from pydantic import BaseModel


class PatientCreate(BaseModel):
    name: str
    age: Optional[int] = None
    sex: Optional[str] = None
    blood_group: Optional[str] = None
    allergies: Optional[str] = None
    emergency_contact: Optional[str] = None


class PatientEdit(BaseModel):
    name: Optional[str] = None
    age: Optional[int] = None
    sex: Optional[str] = None
    blood_group: Optional[str] = None
    allergies: Optional[str] = None
    emergency_contact: Optional[str] = None


class PrescriptionCreate(BaseModel):
    patient_id: int
    doctor_name: Optional[str] = None
    issued_date: Optional[str] = None
    lines: list[str]  # raw prescription lines — the manual path (always works)


class MedicineEdit(BaseModel):
    name: Optional[str] = None
    dose_amount: Optional[str] = None
    dose_unit: Optional[str] = None
    schedule_code: Optional[str] = None
    food: Optional[str] = None
    duration_days: Optional[int] = None
    ongoing: Optional[bool] = None


class SkipDose(BaseModel):
    reason: str


class PrnLog(BaseModel):
    medicine_id: int


class TriageCheckRequest(BaseModel):
    patient_id: int
    symptom_ids: list[str]
    answers: dict[str, bool] = {}


class NextQuestionRequest(BaseModel):
    symptom_id: str
    answers: dict[str, bool] = {}
    current_severity: str = "LOW"


class ClinicalNoteCreate(BaseModel):
    note: str
    # actor is deliberately NOT accepted from the client any more — it is
    # derived from the authenticated user (auth.get_current_user). See
    # main.py's add_clinical_note.


class FreeTextTriageRequest(BaseModel):
    text: str


# ---------------------------------------------------------------- auth

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str
    role: str  # 'patient' | 'caregiver' | 'doctor'


class LoginRequest(BaseModel):
    email: str
    password: str


# ---------------------------------------------------------------- caregiver/doctor linking

class LinkRedeem(BaseModel):
    code: str


class MedicineCorrectionCreate(BaseModel):
    field: str          # one of: name, dose_amount, dose_unit, schedule_code, food, duration_days
    corrected_value: str
    reason: str
