"""
SmartPoli — OCR plugin (Feature 1, image-upload path).

Image -> OpenCV preprocessing -> per-line segmentation -> OCR text per line.
The resulting raw text lines are handed to the SAME parser.py used by the
manual path — there is one Medicine model, one parser, one confidence gate,
regardless of whether a line came from typing or from a photo. This file
only turns pixels into text lines; it never extracts medicine/dose/
frequency itself, which would mean a second, competing extraction path.

Two engines, both rule-based/deterministic (never a generative or vision
LLM — this app never lets anything invent prescription text):
- TrOCR (ocr_engine/ocr.py): most accurate on handwriting, but needs the
  ~2GB torch/transformers stack, so it's dev-only — not installed on
  Render's free tier (see requirements-ocr.txt).
- Tesseract (ocr_engine/ocr_tesseract.py): a small (~10-30MB), classic OCR
  engine with no ML weights to download, installed everywhere including
  Render (via backend/Dockerfile's `tesseract-ocr` apt package). Weaker on
  messy handwriting than TrOCR, fine on printed/typed text.

read_prescription_image() is the one entrypoint callers (main.py,
whatsapp_bot.py) should use: it tries TrOCR first (best accuracy where
available), then Tesseract, and only raises OCRUnavailable if neither can
run — so production (Tesseract-only) and local dev (both) share one path.

Both loaded directly by file path via importlib rather than sys.path/import,
so their module names never collide with this project's own top-level
modules of the same name.

Lazy: importing this module does NOT download or load either engine. TrOCR's
model (~1.3GB, cached under ~/.cache/trocr/ after the first run) is only
fetched when run_ocr_on_image() is actually called, so the server starts
instantly and the manual path is never at risk of being blocked by this
plugin.
"""

import importlib.util
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_OCR_ENGINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_engine")

_engine = None
_tesseract_engine = None
_preprocess_module = None


class OCRUnavailable(Exception):
    """
    Raised whenever the OCR plugin can't run — model download/load failed,
    no internet, unreadable image, whatever. Callers MUST treat this as
    'fall back to manual entry', never let it crash the app: the manual
    path is the one guarantee this product makes (CLAUDE.md section 5).
    """


def _load_ocr_engine_module(filename: str, module_name: str):
    path = os.path.join(_OCR_ENGINE_DIR, filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_preprocess():
    global _preprocess_module
    if _preprocess_module is None:
        try:
            _preprocess_module = _load_ocr_engine_module("preprocess.py", "_smartpoli_ocr_engine_preprocess")
        except Exception as e:
            raise OCRUnavailable(f"Image preprocessing could not be loaded: {e}") from e
    return _preprocess_module


def _ensure_loaded():
    global _engine
    if _engine is not None:
        return
    _load_preprocess()
    try:
        ocr_module = _load_ocr_engine_module("ocr.py", "_smartpoli_ocr_engine_ocr")
    except Exception as e:
        logger.error(f"TrOCR engine failed to load: {e}")
        raise OCRUnavailable(f"TrOCR engine could not be loaded: {e}") from e
    _engine = ocr_module.ocr_engine  # triggers TrOCR download/load — the slow part


def _ensure_tesseract_loaded():
    global _tesseract_engine
    if _tesseract_engine is not None:
        return
    _load_preprocess()
    try:
        tesseract_module = _load_ocr_engine_module("ocr_tesseract.py", "_smartpoli_ocr_engine_tesseract")
    except Exception as e:
        logger.error(f"Tesseract engine failed to load: {e}")
        raise OCRUnavailable(f"Tesseract engine could not be loaded: {e}") from e
    _tesseract_engine = tesseract_module.tesseract_engine


def is_ocr_ready_without_loading() -> bool:
    """True if either engine is already loaded in this process (cheap, no side effects)."""
    return _engine is not None or _tesseract_engine is not None


def _run_with_engine(image_bytes: bytes, extract_text) -> list[dict]:
    preprocess = _load_preprocess()
    try:
        processed_image, _meta = preprocess.preprocess_prescription_image(image_bytes)
        line_images = preprocess.segment_into_lines(processed_image)
    except Exception as e:
        raise OCRUnavailable(f"Could not preprocess image: {e}") from e

    results = []
    for line_image in line_images:
        try:
            text, confidence = extract_text(line_image)
        except Exception as e:
            logger.warning(f"OCR failed on one line, skipping it: {e}")
            continue
        if text and text.strip():
            results.append({"text": text.strip(), "confidence": float(confidence)})
    return results


def run_ocr_on_image(image_bytes: bytes) -> list[dict]:
    """
    TrOCR path — most accurate on handwriting, dev-only (needs torch/
    transformers). Returns a list of {"text": str, "confidence": float}, one
    per detected line, top-to-bottom. Never invents text — an undecodable
    image or a line OCR can't read is simply omitted, never guessed.
    """
    _ensure_loaded()
    return _run_with_engine(image_bytes, _engine.extract_text)


def run_tesseract_ocr_on_image(image_bytes: bytes) -> list[dict]:
    """Tesseract path — small, deterministic, works on Render. Same output
    shape as run_ocr_on_image, so callers can treat the two identically."""
    _ensure_tesseract_loaded()
    return _run_with_engine(image_bytes, _tesseract_engine.extract_text)


def read_prescription_image(image_bytes: bytes) -> list[dict]:
    """The one entrypoint callers (main.py, whatsapp_bot.py) should use:
    tries TrOCR first (best accuracy, where installed), then Tesseract
    (works everywhere, including Render's free tier). Only raises
    OCRUnavailable when neither engine can run at all."""
    try:
        return run_ocr_on_image(image_bytes)
    except OCRUnavailable as trocr_err:
        try:
            return run_tesseract_ocr_on_image(image_bytes)
        except OCRUnavailable as tesseract_err:
            raise OCRUnavailable(f"{trocr_err}; {tesseract_err}") from tesseract_err
