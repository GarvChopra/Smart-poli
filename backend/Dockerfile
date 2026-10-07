# SmartPoli backend — Docker runtime (instead of Render's native Python
# runtime) exists for exactly one reason: Tesseract OCR needs the
# `tesseract-ocr` system binary, and Render's native runtime can only
# `pip install`, with no way to install a non-Python system package.
# See ocr_plugin.py / ocr_engine/ocr_tesseract.py for how it's used.
FROM python:3.14.7-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user, not root.
RUN useradd --system --no-create-home app && chown -R app /app
USER app

# Render sets $PORT at runtime; the sh -c form lets that env var expand
# (a plain CMD ["uvicorn", ..., "$PORT"] would pass the literal string).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
