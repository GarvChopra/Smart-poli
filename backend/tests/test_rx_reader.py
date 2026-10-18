"""Prescription files: PDFs (typed and scanned), AI reading with its guard rails, and the OCR fallback."""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

import main  # noqa: E402
import rx_reader  # noqa: E402
from conftest import register_and_login  # noqa: E402
from main import app  # noqa: E402

TEXT_LINES = ["Dr. R. Mehta, MBBS  Reg No 12345", "Patient: A. Kumar   Age 54", "Diagnosis: Fever",
              "Tab Dolo 650mg 1-0-1 PC x5d", "Cap Amoxicillin 500mg TDS AC 7 days", "Signature"]


def typed_pdf(lines=TEXT_LINES) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 780
    for line in lines:
        c.drawString(60, y, line)
        y -= 24
    c.save()
    return buf.getvalue()


def scanned_pdf(pages=1) -> bytes:
    imgs = []
    for _ in range(pages):
        img = Image.new("RGB", (800, 1000), "white")
        ImageDraw.Draw(img).text((60, 100), "Tab Dolo 650mg 1-0-1", fill="black")
        imgs.append(img)
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def photo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1200, 900), "white").save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def ai_on(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("SMARTPOLI_AI_RX", "1")


def no_ocr(_):
    raise AssertionError("OCR must not run here")


def test_typed_pdf_is_read_from_its_own_text_without_ai_or_ocr():
    out = rx_reader.read_prescription_file(typed_pdf(), ocr_fn=no_ocr)
    assert out["method"] == "pdf_text"
    texts = [t for t, _ in out["lines"]]
    assert "Tab Dolo 650mg 1-0-1 PC x5d" in texts and "Cap Amoxicillin 500mg TDS AC 7 days" in texts
    assert all(c is None for _, c in out["lines"])                     # exact text: no reading uncertainty


def test_ai_can_pick_lines_from_a_typed_pdf_but_never_write_new_ones(ai_on):
    invented = "Tab Warfarin 5mg OD"
    out = rx_reader.read_prescription_file(
        typed_pdf(), ocr_fn=no_ocr,
        text_fn=lambda text: ["Tab Dolo 650mg 1-0-1 PC x5d", invented, "Cap Amoxicillin 500mg TDS AC 7 days"])
    texts = [t for t, _ in out["lines"]]
    assert texts == ["Tab Dolo 650mg 1-0-1 PC x5d", "Cap Amoxicillin 500mg TDS AC 7 days"]


def test_scanned_pdf_pages_go_to_ai_as_pictures_and_are_always_reviewed(ai_on):
    seen = {}

    def fake_ai(jpegs):
        seen["n"] = len(jpegs)
        assert all(j[:3] == b"\xff\xd8\xff" for j in jpegs)
        return ["Tab Dolo 650mg 1-0-1 PC x5d"]
    out = rx_reader.read_prescription_file(scanned_pdf(pages=7), ocr_fn=no_ocr, images_fn=fake_ai)
    assert seen["n"] == rx_reader.MAX_PDF_PAGES                          # never more than 5 pages
    assert out["method"] == "ai" and out["lines"] == [("Tab Dolo 650mg 1-0-1 PC x5d", rx_reader.AI_LINE_CONFIDENCE)]
    assert rx_reader.AI_LINE_CONFIDENCE < 0.85                           # below "verified": a person checks it


def test_ai_failure_falls_back_to_ocr(ai_on):
    def broken(_):
        raise RuntimeError("rate limited")
    out = rx_reader.read_prescription_file(photo(), ocr_fn=lambda b: [{"text": "Tab Dolo 650 1-0-1", "confidence": 0.6}],
                                           images_fn=broken)
    assert out == {"lines": [("Tab Dolo 650 1-0-1", 0.6)], "method": "ocr"}


def test_ai_output_is_cleaned_and_capped():
    raw = ["  Tab   A  1-0-1 ", "Tab   A  1-0-1", "x", 42, "y" * 500] + [f"Tab M{i} OD" for i in range(40)]
    lines = rx_reader._clean_ai_lines(raw)
    assert lines[0] == "Tab A 1-0-1" and lines.count("Tab A 1-0-1") == 1
    assert all(3 <= len(ln) <= rx_reader.MAX_LINE_CHARS for ln in lines) and len(lines) <= rx_reader.MAX_LINES
    assert rx_reader._clean_ai_lines({"not": "a list"}) == []


def test_ai_is_off_without_a_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("SMARTPOLI_AI_RX", "1")
    assert not rx_reader.ai_enabled()


def test_broken_pdf_gives_a_clear_message():
    with pytest.raises(rx_reader.ReadFailed):
        rx_reader.read_prescription_file(b"%PDF-1.4 this is not really a pdf", ocr_fn=no_ocr)


def test_upload_endpoint_accepts_a_pdf_and_makes_a_draft_for_review():
    with TestClient(app) as client:
        register_and_login(client)
        pid = client.post("/patients", json={"name": "Pdf Upload"}).json()["id"]
        r = client.post("/prescriptions/from-image", data={"patient_id": pid},
                        files={"file": ("rx.pdf", typed_pdf(), "application/pdf")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["read_by"] == "pdf_text" and body["status"] == "draft"
        names = {(m.get("name") or "").lower() for m in body["medicines"]}
        assert any("dolo" in n for n in names) and any("amoxicillin" in n for n in names)
        # something that only claims to be a PDF is still refused
        r = client.post("/prescriptions/from-image", data={"patient_id": pid},
                        files={"file": ("rx.pdf", b"hello world, not a pdf", "application/pdf")})
        assert r.status_code == 415


def test_random_photo_text_is_not_turned_into_medicines():
    # a photo that is not a prescription: OCR finds words, none of them a medicine
    junk = [{"text": t, "confidence": 0.9} for t in
            ["Happy Birthday Rahul", "Screenshot 2026-10-05", "Total amount Rs 450", "Battery 45%"]]
    with pytest.raises(rx_reader.ReadFailed, match="doesn't look like a prescription"):
        rx_reader.read_prescription_file(b"\xff\xd8\xff not really a jpeg", ocr_fn=lambda b: junk)


def test_non_medicine_lines_dropped_but_medicines_kept():
    mixed = [{"text": t, "confidence": 0.9} for t in
             ["City Care Hospital", "Happy Diwali", "Tab Dolo 650 1-0-1 x5d", "Cap Rabekind DSR SOS"]]
    out = rx_reader.read_prescription_file(b"\xff\xd8\xff not really a jpeg", ocr_fn=lambda b: mixed)
    assert [t for t, _ in out["lines"]] == ["Tab Dolo 650 1-0-1 x5d", "Cap Rabekind DSR SOS"]
