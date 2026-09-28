"""
Tests for ocr_engine/preprocess.py's size handling — a real phone photo
(multi-megapixel) measured 24-28s through cv2.fastNlMeansDenoising on
Render's free-tier CPU before the downscale step existed, long enough that
a client's own request timeout could give up before the server responds.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from ocr_engine.preprocess import preprocess_prescription_image  # noqa: E402


def _png_bytes(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_a_large_photo_is_downscaled_before_the_expensive_steps():
    result, meta = preprocess_prescription_image(_png_bytes(2105, 1489))
    assert max(result.size) <= 1600
    assert meta.get("was_upscaled") is False  # already large enough post-downscale


def test_a_normal_sized_photo_is_left_alone():
    result, _meta = preprocess_prescription_image(_png_bytes(1200, 900))
    assert result.size == (1200, 900)


def test_a_small_photo_still_gets_upscaled_as_before():
    result, meta = preprocess_prescription_image(_png_bytes(400, 300))
    assert min(result.size) >= 1200
    assert meta.get("was_upscaled") is True
