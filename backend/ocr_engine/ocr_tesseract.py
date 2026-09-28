"""
SmartPoli — Tesseract OCR engine: a rule-based fallback for run_ocr_on_image.

Tesseract is a classic, deterministic OCR engine (~10-30MB, no GPU, no
generative model), so it runs where TrOCR's ~2GB torch/transformers stack
can't (Render's free tier). It can misread a character, but unlike a
generative/vision model it never invents a word that isn't on the page —
same "never invent text" guarantee as the TrOCR path.

Needs the `tesseract-ocr` system binary (installed in backend/Dockerfile)
plus the `pytesseract` wrapper — both plain Python/apt, no ML weights to
download at runtime.
"""

import logging
from typing import Tuple

from PIL import Image
import pytesseract

logger = logging.getLogger(__name__)


class TesseractEngine:
    def extract_text(self, image: Image.Image) -> Tuple[str, float]:
        """
        Returns (text, confidence in [0, 1]). Confidence is the mean of
        Tesseract's own per-word confidence scores — a real measurement of
        how sure the engine is, never a guessed or fixed number.
        """
        try:
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        except Exception as e:
            logger.warning(f"Tesseract extraction failed: {e}")
            return "", 0.0

        words, confidences = [], []
        for word, conf in zip(data.get("text", []), data.get("conf", [])):
            word = word.strip()
            conf = float(conf)
            if word and conf >= 0:  # Tesseract reports -1 for non-text regions
                words.append(word)
                confidences.append(conf)

        if not words:
            return "", 0.0
        return " ".join(words), (sum(confidences) / len(confidences)) / 100.0


tesseract_engine = TesseractEngine()
