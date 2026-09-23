"""
Tests for the image-upload path (backend/ocr_plugin.py wired into main.py).

These deliberately do NOT invoke the real TrOCR model — that needs a
~1.3GB download and is not something a fast, deterministic test suite
should depend on. Instead they monkeypatch main.run_ocr_on_image, which is
exactly the seam ocr_plugin.py exists to provide: everything downstream of
"here are some text lines with confidences" is the same parser.py the
manual path already exercises, so that is what these tests actually prove.
"""
import io
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
from main import app  # noqa: E402
from ocr_plugin import OCRUnavailable  # noqa: E402
from conftest import register_and_login  # noqa: E402


def _fake_file():
    return {"file": ("prescription.png", io.BytesIO(b"not a real image, just bytes"), "image/png")}


def test_image_upload_reuses_the_manual_parser_and_tags_source_ocr(monkeypatch):
    monkeypatch.setattr(main, "run_ocr_on_image", lambda image_bytes: [
        {"text": "Tab Dolo 650mg 1-0-1 PC x5d", "confidence": 0.92},
        {"text": "Tab X 1-?-1", "confidence": 0.88},
    ])

    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "OCR Test"})
        patient_id = r.json()["id"]

        r = client.post("/prescriptions/from-image", data={"patient_id": patient_id}, files=_fake_file())
        assert r.status_code == 200
        body = r.json()
        assert body["ocr_lines_found"] == 2

        dolo = next(m for m in body["medicines"] if "Dolo" in m["raw_text"])
        unknown = next(m for m in body["medicines"] if "?" in m["raw_text"])

        assert dolo["status"] in ("verified", "review")
        assert "ocr" in dolo["field_confidence"]
        assert unknown["status"] == "needs_confirmation"  # malformed shorthand still gated, OCR or not


def test_low_ocr_confidence_forces_needs_confirmation_even_with_a_perfect_read(monkeypatch):
    """A clean, well-formed line can still get blocked if the OCR itself was unsure —
    confidence is min(ocr, name, schedule), never just the parser's own two signals."""
    monkeypatch.setattr(main, "run_ocr_on_image", lambda image_bytes: [
        {"text": "Tab Dolo 650mg 1-0-1 PC x5d", "confidence": 0.2},
    ])

    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "Low Confidence Test"})
        patient_id = r.json()["id"]

        r = client.post("/prescriptions/from-image", data={"patient_id": patient_id}, files=_fake_file())
        assert r.status_code == 200
        med = r.json()["medicines"][0]
        assert med["confidence"] <= 0.2
        assert med["status"] == "needs_confirmation"


def test_no_readable_lines_returns_422_not_a_crash(monkeypatch):
    monkeypatch.setattr(main, "run_ocr_on_image", lambda image_bytes: [])

    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "Empty Image Test"})
        patient_id = r.json()["id"]
        r = client.post("/prescriptions/from-image", data={"patient_id": patient_id}, files=_fake_file())
        assert r.status_code == 422


def test_ocr_unavailable_returns_503_and_points_to_manual_entry(monkeypatch):
    def boom(image_bytes):
        raise OCRUnavailable("model failed to download")
    monkeypatch.setattr(main, "run_ocr_on_image", boom)

    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "Unavailable Test"})
        patient_id = r.json()["id"]
        r = client.post("/prescriptions/from-image", data={"patient_id": patient_id}, files=_fake_file())
        assert r.status_code == 503
        assert "manual" in r.json()["detail"].lower()


def test_prescriptions_created_from_image_are_confirmable_exactly_like_manual_ones(monkeypatch):
    monkeypatch.setattr(main, "run_ocr_on_image", lambda image_bytes: [
        {"text": "Tab Dolo 650mg 1-0-1 PC x5d", "confidence": 0.95},
    ])

    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "Confirm Test"})
        patient_id = r.json()["id"]
        r = client.post("/prescriptions/from-image", data={"patient_id": patient_id}, files=_fake_file())
        prescription_id = r.json()["prescription_id"]

        r = client.post(f"/prescriptions/{prescription_id}/confirm")
        assert r.status_code == 200
        assert len(r.json()["scheduled"]) == 1
