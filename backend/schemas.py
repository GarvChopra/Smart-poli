"""SmartPoli API request/response schemas (Pydantic). Thin mirrors of db.py's
canonical ORM model for (de)serialisation only — not a second data shape."""

import base64
import binascii
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


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


class DoctorPrescriptionCreate(BaseModel):
    lines: list[str]  # same manual-path lines a patient would type — same parser, same gate


class PrescriptionTemplateCreate(BaseModel):
    label: str
    lines: list[str]


# ---------------------------------------------------------------- 3D health card

CARD_SHARE_FIELDS = ("photo", "blood_group", "allergies", "conditions", "medicines",
                     "emergency_contact", "caregiver", "doctor", "instructions")
CARD_PHOTO_MAX_BYTES = 200_000


class EmergencyProfileUpdate(BaseModel):
    """Every field optional: only what's sent changes. An empty string
    clears a text field or removes the photo."""
    conditions: Optional[str] = Field(None, max_length=500)
    instructions: Optional[str] = Field(None, max_length=500)
    photo: Optional[str] = None
    share: Optional[dict[str, bool]] = None

    @field_validator("photo")
    @classmethod
    def _valid_photo(cls, v):
        if not v:
            return v
        prefix, _, payload = v.partition(",")
        if prefix not in ("data:image/jpeg;base64", "data:image/png;base64"):
            raise ValueError("Photo must be a JPEG or PNG data URL.")
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("Photo is not valid base64.")
        if len(raw) > CARD_PHOTO_MAX_BYTES:
            raise ValueError("Photo is too large (max 200 KB).")
        return v

    @field_validator("share")
    @classmethod
    def _valid_share(cls, v):
        if v is not None:
            unknown = set(v) - set(CARD_SHARE_FIELDS)
            if unknown:
                raise ValueError(f"Unknown share fields: {sorted(unknown)}")
        return v


# ---------------------------------------------------------------- voice assistant

class VoiceMessage(BaseModel):
    role: str
    content: str


class VoiceTurnRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    lang: Literal["en", "hi"] = "en"
    client_time: Optional[str] = None   # browser's local time, ISO 8601 with offset
    history: list[VoiceMessage] = Field(default_factory=list, max_length=40)
    state: dict = Field(default_factory=dict)
