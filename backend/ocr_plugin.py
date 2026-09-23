"""
SmartPoli — optional OCR plugin (Feature 1, image-upload path).

Image -> OpenCV preprocessing -> per-line segmentation -> TrOCR text per
line. The resulting raw text lines are handed to the SAME parser.py used by
the manual path — there is one Medicine model, one parser, one confidence
gate, regardless of whether a line came from typing or from a photo. This
file only turns pixels into text lines; it never extracts medicine/dose/
frequency itself, which would mean a second, competing extraction path.

Uses backend/ocr_engine/ocr.py (TrOCR wrapper) and preprocess.py (OpenCV
pipeline) — loaded directly by file path via importlib rather than
sys.path/import, so their module names (`ocr`/`preprocess`) never collide
with this project's own top-level modules of the same name.

Lazy: importing this module does NOT download or load TrOCR. The model
(~1.3GB, cached under ~/.cache/trocr/ after the first run) is only fetched
when run_ocr_on_image() is actually called, so the server starts instantly
and the manual path is never at risk of being blocked by this plugin.
"""

import importlib.util
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_OCR_ENGINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_engine")

_engine = None
_preprocess_fn = None
_segment_fn = None


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


def _ensure_loaded():
    global _engine, _preprocess_fn, _segment_fn
    if _engine is not None:
        return
    try:
        ocr_module = _load_ocr_engine_module("ocr.py", "_smartpoli_ocr_engine_ocr")
        preprocess_module = _load_ocr_engine_module("preprocess.py", "_smartpoli_ocr_engine_preprocess")
    except Exception as e:
        logger.error(f"OCR plugin failed to load: {e}")
        raise OCRUnavailable(f"OCR engine could not be loaded: {e}") from e

    _engine = ocr_module.ocr_engine  # triggers TrOCR download/load — the slow part
    _preprocess_fn = preprocess_module.preprocess_prescription_image
    _segment_fn = preprocess_module.segment_into_lines


def is_ocr_ready_without_loading() -> bool:
    """True if the engine is already loaded in this process (cheap, no side effects)."""
    return _engine is not None


def run_ocr_on_image(image_bytes: bytes) -> list[dict]:
    """
    Returns a list of {"text": str, "confidence": float}, one per detected
    line, top-to-bottom. Never invents text — an undecodable image or a
    line OCR can't read is simply omitted, never guessed.
    """
    _ensure_loaded()

    try:
        processed_image, _meta = _preprocess_fn(image_bytes)
        line_images = _segment_fn(processed_image)
    except Exception as e:
        raise OCRUnavailable(f"Could not preprocess image: {e}") from e

    results = []
    for line_image in line_images:
        try:
            text, confidence = _engine.extract_text(line_image)
        except Exception as e:
            logger.warning(f"OCR failed on one line, skipping it: {e}")
            continue
        if text and text.strip():
            results.append({"text": text.strip(), "confidence": float(confidence)})
    return results
