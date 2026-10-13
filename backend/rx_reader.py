"""
SmartPoli - read a prescription file (photo or PDF) into medicine lines.

What comes out is a list of raw medicine lines ("Tab Dolo 650 1-0-1 x 5 days"); the SAME parser.py the typed path uses
turns them into medicines, and every medicine still goes to the review screen before anything is scheduled. This
module never decides a dose, a time or a schedule.

How each file is read, best first:
  1. PDF made on a computer: its text is inside the file, copied out exactly (no reading errors possible).
  2. AI vision (Groq, a vision model): far better than classic OCR on phone photos and handwriting. It is told to copy
     the medicine lines as written and never to guess; for a text PDF every line it returns must appear in the PDF's
     own text, so it can only pick lines, never invent them. Lines it reads from a picture are capped below the
     "verified" confidence, so a person always checks them.
  3. Tesseract OCR (ocr_plugin): the fallback when there is no AI key or the AI call fails.

Off switches: SMARTPOLI_AI_RX=0 disables the AI step (also when GROQ_API_KEY is unset).
"""

import base64
import io
import json
import logging
import os
import re
from difflib import SequenceMatcher
from typing import Callable, Optional

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

MAX_PDF_PAGES = 5
MAX_LINES = 30
MAX_LINE_CHARS = 200
AI_LINE_CONFIDENCE = 0.84          # just below parser.VERIFIED_THRESHOLD: a picture read by AI is always reviewed
IMAGE_MAX_SIDE = 1600              # enough detail for handwriting, small enough for the API's request limit
DEFAULT_VISION_MODEL = "qwen/qwen3.8-27b"     # Groq; override with GROQ_VISION_MODEL


class ReadFailed(Exception):
    """Nothing could read this file. The message is safe to show the person."""


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def ai_enabled() -> bool:
    return bool(os.getenv("GROQ_API_KEY")) and os.getenv("SMARTPOLI_AI_RX", "1") != "0"


# ---------------------------------------------------------------- PDF

def _open_pdf(data: bytes):
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as e:  # noqa: BLE001 - broken / password-protected PDF
        raise ReadFailed("That PDF could not be opened. If it has a password, remove it, or upload a photo instead.") from e
    if len(pdf) == 0:
        raise ReadFailed("That PDF has no pages.")
    return pdf


def pdf_text(data: bytes) -> str:
    """The text stored inside a computer-made PDF (first pages only). Empty for a scanned PDF."""
    pdf = _open_pdf(data)
    out = []
    for i in range(min(len(pdf), MAX_PDF_PAGES)):
        page = pdf[i]
        tp = page.get_textpage()
        out.append(tp.get_text_range() or "")
        tp.close()
        page.close()
    pdf.close()
    return "\n".join(out)


def pdf_page_images(data: bytes) -> list[bytes]:
    """Each page (first MAX_PDF_PAGES) drawn as a JPEG, for a scanned PDF."""
    pdf = _open_pdf(data)
    images = []
    for i in range(min(len(pdf), MAX_PDF_PAGES)):
        page = pdf[i]
        w, h = page.get_size()
        scale = min(3.0, IMAGE_MAX_SIDE / max(w, h, 1))
        pil = page.render(scale=scale).to_pil()
        images.append(_to_jpeg(pil))
        page.close()
    pdf.close()
    return images


def _text_lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", ln).strip() for ln in text.replace("\r", "\n").split("\n") if ln.strip()]


def has_text_layer(text: str) -> bool:
    return sum(len(ln) for ln in _text_lines(text)) >= 20


# ---------------------------------------------------------------- images

def _to_jpeg(img: Image.Image) -> bytes:
    img = ImageOps.exif_transpose(img).convert("RGB")       # phone photos carry their rotation in EXIF
    img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def image_to_jpeg(data: bytes) -> bytes:
    try:
        return _to_jpeg(Image.open(io.BytesIO(data)))
    except Exception as e:  # noqa: BLE001
        raise ReadFailed("That image could not be opened. Try another photo.") from e


# ---------------------------------------------------------------- AI

_PROMPT = (
    "You are reading a doctor's prescription for a medicine-reminder app. Copy ONLY the medicine lines, one string "
    "per medicine, exactly as written: form (Tab/Cap/Syp/Inj...), name, strength, how often (e.g. 1-0-1, BD, TDS, "
    "OD, SOS), food instruction (AC/PC/before/after food) and duration (e.g. x 5 days), keeping the doctor's own "
    "abbreviations. Do not include the doctor, clinic, patient, diagnosis, tests, advice or signatures. Never guess: "
    "if a word or number cannot be read, write ? in its place. Never add a medicine, dose or timing that is not "
    'written. Reply with only JSON: {"medicines": ["line 1", "line 2"]}. If no medicine is readable, '
    '{"medicines": []}.'
)


def _clean_ai_lines(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:MAX_LINES]:
        if isinstance(item, str):
            line = re.sub(r"\s+", " ", item).strip()[:MAX_LINE_CHARS]
            if len(line) >= 3 and line not in out:
                out.append(line)
    return out


def _groq_json(content: list) -> list[str]:
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"), max_retries=1, timeout=45)
    model = os.getenv("GROQ_VISION_MODEL", DEFAULT_VISION_MODEL)
    resp = client.chat.completions.create(
        model=model, temperature=0, max_tokens=1500,
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
    )
    text = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        parsed = json.loads(m.group(0)) if m else {}
    return _clean_ai_lines(parsed.get("medicines") if isinstance(parsed, dict) else None)


def ai_read_images(jpegs: list[bytes]) -> list[str]:
    content = [{"type": "text", "text": _PROMPT}]
    for jpg in jpegs[:MAX_PDF_PAGES]:
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}})
    return _groq_json(content)


def ai_pick_lines_from_text(text: str) -> list[str]:
    return _groq_json([{"type": "text", "text": _PROMPT + "\n\nThe prescription's text:\n" + text[:8000]}])


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def only_lines_in_source(lines: list[str], source: str) -> list[str]:
    """Keep an AI-picked line only if it really is in the PDF's own text (exactly or almost), so for a text PDF the
    AI can choose lines but never write one."""
    src_lines = _text_lines(source)
    src_all = _norm(source)
    kept = []
    for line in lines:
        n = _norm(line)
        if not n:
            continue
        if n in src_all or any(SequenceMatcher(None, n, _norm(s)).ratio() >= 0.85 for s in src_lines):
            kept.append(line)
    return kept


# ---------------------------------------------------------------- entrypoint

def read_prescription_file(data: bytes, ocr_fn: Optional[Callable] = None,
                           images_fn: Callable = ai_read_images,
                           text_fn: Callable = ai_pick_lines_from_text) -> dict:
    """Returns {"lines": [(text, confidence or None)], "method": "pdf_text" | "ai" | "ocr"}.
    Raises ReadFailed (message safe to show) when nothing could be read."""
    from parser import is_likely_header_line

    if is_pdf(data):
        text = pdf_text(data)
        if has_text_layer(text):
            if ai_enabled():
                try:
                    picked = only_lines_in_source(text_fn(text), text)
                    if picked:
                        return {"lines": [(ln, None) for ln in picked], "method": "pdf_text"}
                except Exception:  # noqa: BLE001 - fall through to the plain text lines
                    logger.warning("AI line picking for a text PDF failed; using the PDF's lines as they are.")
            lines = [ln for ln in _text_lines(text) if not is_likely_header_line(ln)][:MAX_LINES]
            if lines:
                return {"lines": [(ln[:MAX_LINE_CHARS], None) for ln in lines], "method": "pdf_text"}
        pictures = pdf_page_images(data)                  # scanned PDF: read its pages as pictures
    else:
        pictures = [data]

    if ai_enabled():
        try:
            lines = images_fn([p if is_pdf(data) else image_to_jpeg(p) for p in pictures])
            if lines:
                return {"lines": [(ln, AI_LINE_CONFIDENCE) for ln in lines], "method": "ai"}
        except Exception as e:  # noqa: BLE001 - AI down / rate-limited: fall back to OCR
            logger.warning("AI prescription reading failed (%s); falling back to OCR.", type(e).__name__)

    if ocr_fn is None:
        from ocr_plugin import read_prescription_image as ocr_fn
    lines = []
    for picture in pictures:
        lines.extend(ocr_fn(picture))
    if not lines:
        raise ReadFailed("No medicines could be read. Try a clearer, well-lit photo, or type them in.")
    return {"lines": [(ln["text"], ln["confidence"]) for ln in lines[:MAX_LINES]], "method": "ocr"}
