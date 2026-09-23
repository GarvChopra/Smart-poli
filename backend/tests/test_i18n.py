import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from i18n import to_plain_language_hi, localized_action  # noqa: E402
from shorthand import decode_schedule  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from conftest import register_and_login  # noqa: E402


def test_slot_notation_hindi_shows_every_slot_including_zero():
    r = decode_schedule("1-0-1 after meals")
    hi = to_plain_language_hi(r)
    assert "सुबह" in hi  # morning
    assert "रात" in hi   # night
    assert "भोजन के बाद" in hi  # after meals


def test_sos_hindi_says_as_needed():
    r = decode_schedule("Syrup Crocin 5ml SOS")
    hi = to_plain_language_hi(r)
    assert "आवश्यकता होने पर" in hi


def test_english_default_is_unchanged_by_i18n_module_existing():
    # shorthand.py's own English output must be byte-identical to before —
    # i18n.py is additive, never a replacement.
    r = decode_schedule("Tab Dolo 650mg 1-0-1 PC x5d")
    assert r["plainLanguage"].startswith("Morning")


def test_symptom_labels_localize_via_api():
    with TestClient(app) as client:
        r = client.get("/triage/symptoms", params={"lang": "hi"})
        labels = {s["label"] for s in r.json()}
        assert "सीने में दर्द" in labels  # chest pain

        r = client.get("/triage/symptoms", params={"lang": "en"})
        labels = {s["label"] for s in r.json()}
        assert "Chest pain" in labels


def test_triage_action_localizes_for_emergency():
    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "i18n test"})
        patient_id = r.json()["id"]
        r = client.post("/triage/check", params={"lang": "hi"}, json={
            "patient_id": patient_id, "symptom_ids": ["chest_pain"],
            "answers": {"difficulty_breathing": True},
        })
        assert "112" in r.json()["action"]
        assert "आपातकालीन" in r.json()["action"]


def test_prescription_response_includes_hindi_plain_language():
    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "i18n rx test"})
        patient_id = r.json()["id"]
        r = client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"],
        })
        med = r.json()["medicines"][0]
        assert "सुबह" in med["plain_language_hi"]
