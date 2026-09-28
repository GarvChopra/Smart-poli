"""
Tests for ocr_plugin.read_prescription_image's fallback between the two
rule-based OCR engines (TrOCR, then Tesseract) — the seam that lets
main.py and whatsapp_bot.py stay ignorant of which engine actually ran.

These monkeypatch run_ocr_on_image / run_tesseract_ocr_on_image directly,
never the real engines (TrOCR needs a ~1.3GB download; Tesseract needs the
system binary) — that split is exactly what run_ocr_on_image /
run_tesseract_ocr_on_image / read_prescription_image exist to make testable.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ocr_plugin  # noqa: E402
from ocr_plugin import OCRUnavailable, read_prescription_image  # noqa: E402


def test_trocr_result_is_used_when_it_works(monkeypatch):
    monkeypatch.setattr(ocr_plugin, "run_ocr_on_image", lambda b: [{"text": "Tab Dolo 650mg", "confidence": 0.9}])

    def boom(b):
        raise AssertionError("Tesseract should not be tried when TrOCR succeeds")
    monkeypatch.setattr(ocr_plugin, "run_tesseract_ocr_on_image", boom)

    assert read_prescription_image(b"fake") == [{"text": "Tab Dolo 650mg", "confidence": 0.9}]


def test_falls_back_to_tesseract_when_trocr_is_unavailable(monkeypatch):
    def trocr_unavailable(b):
        raise OCRUnavailable("torch is not installed")
    monkeypatch.setattr(ocr_plugin, "run_ocr_on_image", trocr_unavailable)
    monkeypatch.setattr(ocr_plugin, "run_tesseract_ocr_on_image",
                        lambda b: [{"text": "Tab Crocin 500mg", "confidence": 0.6}])

    assert read_prescription_image(b"fake") == [{"text": "Tab Crocin 500mg", "confidence": 0.6}]


def test_raises_unavailable_with_both_reasons_when_both_engines_fail(monkeypatch):
    def trocr_unavailable(b):
        raise OCRUnavailable("torch is not installed")

    def tesseract_unavailable(b):
        raise OCRUnavailable("tesseract binary not found")
    monkeypatch.setattr(ocr_plugin, "run_ocr_on_image", trocr_unavailable)
    monkeypatch.setattr(ocr_plugin, "run_tesseract_ocr_on_image", tesseract_unavailable)

    try:
        read_prescription_image(b"fake")
        assert False, "expected OCRUnavailable"
    except OCRUnavailable as e:
        assert "torch is not installed" in str(e)
        assert "tesseract binary not found" in str(e)
