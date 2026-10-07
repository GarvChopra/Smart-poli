"""Request-size cap, safe 500 answer, AI rate limits, masked phone numbers in logs."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import web_security  # noqa: E402
from main import app  # noqa: E402
from whatsapp_bot import _mask_phone  # noqa: E402


def test_an_oversized_request_is_refused_before_it_is_read():
    c = TestClient(app)
    r = c.post("/auth/login", content=b"x", headers={"Content-Length": str(web_security.MAX_BODY_BYTES + 1), "Content-Type": "application/json"})
    assert r.status_code == 413 and "too large" in r.json()["detail"]


def test_a_crash_answers_with_a_plain_message_and_no_internals():
    @app.get("/_boom_test")
    def boom():
        raise RuntimeError("secret-db-host.internal:5432 password=hunter2")

    r = TestClient(app, raise_server_exceptions=False).get("/_boom_test")
    assert r.status_code == 500
    assert "hunter2" not in r.text and "secret-db-host" not in r.text and "Traceback" not in r.text
    assert r.json()["ref"]


def test_phone_numbers_are_masked_for_logs():
    assert _mask_phone("+919876543210") == "+91••••••3210"
    assert "9876" not in _mask_phone("+919876543210") and _mask_phone(None) == "•••"


def test_ai_endpoints_have_a_rate_limit_dependency():
    import inspect
    import main
    for fn in (main.medicine_pair_check, main.prescription_pair_check, main.patient_pair_check, main.patient_open_warnings,
               main.patient_medicine_info, main.create_prescription_from_image):
        assert "_ai" in inspect.signature(fn).parameters, fn.__name__
