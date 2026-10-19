"""
Camera scan of a medicine strip/box: OCR text -> candidate names for the
patient to confirm. OCR itself is mocked (the Tesseract binary is not part of
the test environment); everything after it is exercised for real.
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import medicine_scan as ms  # noqa: E402
from conftest import register_and_login  # noqa: E402
from db import AuditLog, Medicine, SessionLocal  # noqa: E402
from main import app  # noqa: E402
from ocr_plugin import OCRUnavailable  # noqa: E402
from timing_helpers import new_patient  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"body"

BOX = [
    {"text": "DOLO 650", "confidence": 0.9},
    {"text": "Paracetamol Tablets IP 650 mg", "confidence": 0.88},
    {"text": "Micro Labs Limited", "confidence": 0.9},
    {"text": "Batch No. AB1234  MRP Rs 30.00", "confidence": 0.8},
]


def L(*texts):
    return [{"text": t, "confidence": 0.9} for t in texts]


# ---------------------------------------------------------------- candidate extraction

def test_brand_and_ingredient_are_suggested_with_strength_and_the_text_they_came_from():
    out = ms.candidates_from_lines(BOX)
    names = {c["name"]: c for c in out["candidates"]}
    assert "dolo 650" in names and "paracetamol" in names
    assert "dolo" not in names                          # prefix of the more specific "dolo 650"
    assert names["dolo 650"]["strength"] == "650mg" and names["dolo 650"]["read_from"] == "DOLO 650"
    assert out["form_guess"] == "tab"
    assert "you choose" in out["note"]


def test_manufacturer_and_batch_text_never_become_suggestions():
    out = ms.candidates_from_lines(L("Micro Labs Limited", "Batch No. AB1234", "Mfg 03/2026 Exp 02/2028", "Store below 25 C"))
    assert out["candidates"] == []


def test_a_misread_name_is_marked_approximate_not_exact():
    out = ms.candidates_from_lines(L("Paracetmol 500 mg"))      # one letter lost by OCR
    c = out["candidates"][0]
    assert c["name"] == "paracetamol" and c["match"] == "approximate" and c["score"] < 100


def test_garbage_text_suggests_nothing_instead_of_guessing():
    out = ms.candidates_from_lines(L("Xyzzyq Qwerty 123", "~~~ ||| ^^^"))
    assert out["candidates"] == [] and out["lines_read"]


def test_empty_ocr_result_is_handled():
    assert ms.candidates_from_lines([])["candidates"] == []
    assert ms.candidates_from_lines(L("   ", ""))["candidates"] == []


def test_form_is_read_from_the_packaging_words():
    assert ms.candidates_from_lines(L("Amoxicillin Capsules IP 500 mg"))["form_guess"] == "cap"
    assert ms.candidates_from_lines(L("Crocin Syrup 125 mg/5 ml"))["form_guess"] == "syrup"


def test_at_most_five_suggestions():
    names = ["paracetamol", "metformin", "amlodipine", "telmisartan", "atorvastatin", "omeprazole", "pantoprazole"]
    out = ms.candidates_from_lines(L(*names))
    assert 1 <= len(out["candidates"]) <= ms.MAX_CANDIDATES


def test_confirmed_choices_become_an_ordinary_prescription_line_the_parser_accepts():
    from parser import parse_medicine_line
    line = ms.build_prescription_line("tab", "Dolo 650", "650mg", "1-0-1", 5, False)
    assert line == "Tab Dolo 650 1-0-1 x5d"                       # no duplicated "650 650mg"
    assert ms.build_prescription_line("tab", "Metformin", "500mg", "1-0-1", 5, False) == "Tab Metformin 500mg 1-0-1 x5d"
    assert ms.build_prescription_line("tab", "Dolo 650", "1000mg", "SOS", None, True) == "Tab Dolo 650 1000mg SOS continue"
    p = parse_medicine_line(ms.build_prescription_line("tab", "Telmisartan", "40mg", "1-0-0", None, True))
    assert p["schedule_code"] == "1-0-0" and p["duration_days"] is None
    sos = ms.build_prescription_line("tab", "Dolo", "650mg", "SOS", None, True)
    assert parse_medicine_line(sos)["is_prn"] is True


# ---------------------------------------------------------------- endpoint

def test_scan_endpoint_returns_candidates_audits_and_adds_nothing(monkeypatch):
    monkeypatch.setattr(ms, "read_pack_text", lambda b: BOX)
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        db = SessionLocal()
        before = db.query(Medicine).count()
        r = client.post("/medicines/scan", data={"patient_id": pid}, files={"file": ("box.png", PNG, "image/png")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["candidates"][0]["name"] == "dolo 650" and body["candidates"][0]["generic"] == "paracetamol"
        assert db.query(Medicine).count() == before                         # scanning alone adds no medicine
        entry = db.query(AuditLog).filter_by(patient_id=pid, action="medicine_scanned").one()
        assert "candidate" in entry.detail and "Dolo" not in entry.detail     # counts only; no packaging text logged
        db.close()


def test_confirmed_scan_goes_through_the_normal_review_gate_and_needs_a_schedule():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        line = ms.build_prescription_line("tab", "Dolo 650", "650mg", "1-0-1", 5, False)
        pres = client.post("/prescriptions", json={"patient_id": pid, "lines": [line]}).json()
        med = pres["medicines"][0]
        assert med["name"] and med["status"] in ("verified", "review")
        # nothing is scheduled until the patient confirms the prescription
        assert client.get(f"/patients/{pid}/dashboard").json()["upcoming_doses"] == []
        assert client.post(f"/prescriptions/{pres['prescription_id']}/confirm").status_code == 200


def test_scan_without_a_readable_photo_degrades_to_manual_entry(monkeypatch):
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        monkeypatch.setattr(ms, "read_pack_text", lambda b: (_ for _ in ()).throw(OCRUnavailable("no engine")))
        r = client.post("/medicines/scan", data={"patient_id": pid}, files={"file": ("b.png", PNG, "image/png")})
        assert r.status_code == 422 and "enter it manually" in r.json()["detail"].lower()
        monkeypatch.setattr(ms, "read_pack_text", lambda b: [])
        r = client.post("/medicines/scan", data={"patient_id": pid}, files={"file": ("b.png", PNG, "image/png")})
        assert r.status_code == 422 and "enter it manually" in r.json()["detail"].lower()


def test_scan_rejects_bad_uploads_and_other_peoples_records(monkeypatch):
    monkeypatch.setattr(ms, "read_pack_text", lambda b: BOX)
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        assert client.post("/medicines/scan", data={"patient_id": pid},
                           files={"file": ("x.png", b"not an image", "image/png")}).status_code == 415
        assert client.post("/medicines/scan", data={"patient_id": pid},
                           files={"file": ("x.png", b"", "image/png")}).status_code == 400
        monkeypatch.setattr(main, "MAX_IMAGE_BYTES", 16)
        assert client.post("/medicines/scan", data={"patient_id": pid},
                           files={"file": ("x.png", PNG + b"0" * 100, "image/png")}).status_code == 413
        monkeypatch.setattr(main, "MAX_IMAGE_BYTES", 10 * 1024 * 1024)

        stranger = TestClient(app)
        register_and_login(stranger)
        assert stranger.post("/medicines/scan", data={"patient_id": pid},
                             files={"file": ("x.png", PNG, "image/png")}).status_code == 403
        assert TestClient(app).post("/medicines/scan", data={"patient_id": pid},
                                    files={"file": ("x.png", PNG, "image/png")}).status_code == 401


def test_prescriptions_screen_offers_the_camera_scan():
    app_js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "app.js"),
                  encoding="utf-8").read()
    assert 'id="scanInput" accept="image/*" capture="environment"' in app_js
    assert "'/medicines/scan'" in app_js and "Is this your medicine?" in app_js


# ---------------------------------------------------------------- vision (the main way to read a box)

def test_a_vision_answer_is_validated_before_it_is_trusted():
    ok = ms.parse_vision_answer({"found": True, "brand_name": "Dolo 650", "generic_name": "Paracetamol", "strength": "650 mg",
                                 "form": "tab", "text_read": ["Dolo 650", "Paracetamol IP 650 mg"]})
    c = ok["candidates"][0]
    assert c["name"] == "Dolo 650" and c["generic"] == "Paracetamol" and c["strength"] == "650mg" and c["form"] == "tab"
    assert ok["source"] == "vision" and ok["form_guess"] == "tab"
    # not found / junk / no name -> nothing (the screen then offers manual entry)
    assert ms.parse_vision_answer({"found": False}) is None
    assert ms.parse_vision_answer({"found": True, "brand_name": "  ", "generic_name": None}) is None
    assert ms.parse_vision_answer({"found": True, "brand_name": "123"}) is None
    assert ms.parse_vision_answer(None) is None
    odd = ms.parse_vision_answer({"found": True, "brand_name": "Crocin", "strength": "lots", "form": "potion"})["candidates"][0]
    assert odd["strength"] is None and odd["form"] is None             # an invented form/strength is dropped, not shown


def test_json_is_found_inside_chatty_model_output():
    assert ms._json_from('```json' + chr(10) + '{"found": true, "brand_name": "Crocin"}' + chr(10) + '```')["brand_name"] == "Crocin"
    assert ms._json_from("no json here") is None


def test_scan_endpoint_puts_the_vision_answer_first_and_offers_a_different_keyword_reading(monkeypatch):
    vision = ms.parse_vision_answer({"found": True, "brand_name": "Telma 40", "generic_name": "Telmisartan", "strength": "40mg", "form": "tab"})
    monkeypatch.setattr(ms, "identify_with_vision", lambda b, why=None: vision)
    monkeypatch.setattr(ms, "read_pack_text", lambda b: BOX)          # the keyword reader runs too, and disagrees
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        r = client.post("/medicines/scan", data={"patient_id": pid}, files={"file": ("b.png", PNG, "image/png")})
        body = r.json()
        assert r.status_code == 200 and body["candidates"][0]["name"] == "Telma 40"     # the AI reading comes first
        assert body["candidates"][1]["name"] == "dolo 650" and body["source"] == "vision+keywords"   # offered as "Or is it..."


def test_vision_failure_falls_back_to_ocr_then_to_manual(monkeypatch):
    monkeypatch.setattr(ms, "identify_with_vision", lambda b, why=None: None)
    monkeypatch.setattr(ms, "read_pack_text", lambda b: BOX)
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        assert client.post("/medicines/scan", data={"patient_id": pid}, files={"file": ("b.png", PNG, "image/png")}).status_code == 200
        monkeypatch.setattr(ms, "read_pack_text", lambda b: [])
        assert client.post("/medicines/scan", data={"patient_id": pid}, files={"file": ("b.png", PNG, "image/png")}).status_code == 422


def test_vision_is_off_without_a_key_or_when_switched_off(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert ms.vision_available() is False and ms.identify_with_vision(b"x") is None
    monkeypatch.setenv("GROQ_API_KEY", "k"); monkeypatch.setenv("SMARTPOLI_AI_SCAN", "0")
    assert ms.vision_available() is False


class _Boom(Exception):
    def __init__(self, name, retry_after=None):
        super().__init__(name)
        self.__class__ = type(name, (Exception,), {})
        self.response = type("R", (), {"headers": {"retry-after": str(retry_after)} if retry_after is not None else {}})()


def _fake_client(behaviour):
    """behaviour: list of results; an Exception instance is raised, anything else is returned as the model's text."""
    calls = {"n": 0}

    class _C:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    r = behaviour[min(calls["n"], len(behaviour) - 1)]
                    calls["n"] += 1
                    if isinstance(r, Exception):
                        raise r
                    return type("Resp", (), {"choices": [type("Ch", (), {"message": type("M", (), {"content": r})()})()]})()
    return _C, calls


GOOD = '{"found": true, "brand_name": "Telma 40", "generic_name": "Telmisartan", "strength": "40mg", "form": "tab"}'


def _tiny_jpeg():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), (255, 255, 255)).save(buf, "JPEG")
    return buf.getvalue()


def test_a_long_rate_limit_on_groq_moves_straight_to_gemini(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k"); monkeypatch.setenv("SMARTPOLI_AI_SCAN", "1")
    groq, gcalls = _fake_client([_Boom("RateLimitError", retry_after=19)])
    gem, mcalls = _fake_client([GOOD])
    monkeypatch.setattr(ms, "_providers", lambda: [("groq", groq, "m1"), ("gemini", gem, "m2")])
    monkeypatch.setattr(ms.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not wait 19 s")))
    out = ms.identify_with_vision(_tiny_jpeg())
    assert out["candidates"][0]["name"] == "Telma 40" and gcalls["n"] == 1 and mcalls["n"] == 1


def test_a_short_rate_limit_is_waited_out_once_then_succeeds(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k"); monkeypatch.setenv("SMARTPOLI_AI_SCAN", "1")
    groq, gcalls = _fake_client([_Boom("RateLimitError", retry_after=2), GOOD])
    slept = []
    monkeypatch.setattr(ms, "_providers", lambda: [("groq", groq, "m1")])
    monkeypatch.setattr(ms.time, "sleep", slept.append)
    assert ms.identify_with_vision(_tiny_jpeg())["candidates"][0]["name"] == "Telma 40"
    assert gcalls["n"] == 2 and len(slept) == 1


def test_a_server_hiccup_is_retried_once_and_total_failure_returns_none(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k"); monkeypatch.setenv("SMARTPOLI_AI_SCAN", "1")
    flaky, calls = _fake_client([_Boom("InternalServerError"), GOOD])
    monkeypatch.setattr(ms, "_providers", lambda: [("gemini", flaky, "m")])
    monkeypatch.setattr(ms.time, "sleep", lambda s: None)
    assert ms.identify_with_vision(_tiny_jpeg())["candidates"][0]["name"] == "Telma 40" and calls["n"] == 2
    dead, dcalls = _fake_client([_Boom("InternalServerError")])
    monkeypatch.setattr(ms, "_providers", lambda: [("a", dead, "m"), ("b", dead, "m")])
    assert ms.identify_with_vision(_tiny_jpeg()) is None


def test_gemini_alone_is_enough_to_enable_scanning(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False); monkeypatch.setenv("GEMINI_API_KEY", "g"); monkeypatch.setenv("SMARTPOLI_AI_SCAN", "1")
    assert ms.vision_available() is True


# ---------------------------------------------------------------- back of the strip: expiry and "do we know this name"

def test_expiry_is_read_next_to_the_exp_word_not_the_mfg_date_or_price():
    assert ms.parse_expiry("Mfg 03/2025 Exp 02/2028")["label"] == "Feb 2028"
    assert ms.parse_expiry("MFG.JAN.2025 EXP.DEC.2026") == {"month": 12, "year": 2026, "label": "Dec 2026"}
    assert ms.parse_expiry("Exp. AUG.27")["year"] == 2027
    assert ms.parse_expiry("MRP Rs 10.26 incl. of all taxes") is None          # a price is not a date
    assert ms.parse_expiry("EXP 13/2027") is None and ms.parse_expiry(None) is None


def test_vision_answer_carries_expiry_and_whether_the_name_is_known():
    out = ms.parse_vision_answer({"found": True, "brand_name": "Dolo 650", "generic_name": "Paracetamol",
                                  "strength": "650 mg", "form": "tab", "expiry": "08/2027"})
    assert out["expiry"]["label"] == "Aug 2027" and out["candidates"][0]["known"] is True
    odd = ms.parse_vision_answer({"found": True, "brand_name": "Qzxvol 20", "generic_name": None})
    assert odd["candidates"][0]["known"] is False and odd["expiry"] is None
    # an unknown brand whose ingredient is known still counts as recognised
    assert ms.parse_vision_answer({"found": True, "brand_name": "Newbrand 500", "generic_name": "Paracetamol"})["candidates"][0]["known"]


def test_ocr_backup_also_reads_the_expiry():
    out = ms.candidates_from_lines(BOX + [{"text": "MFG 01/2026 EXP 12/2027", "confidence": 0.8}])
    assert out["expiry"]["label"] == "Dec 2027"


def test_name_suggestions_need_login_and_list_known_medicines():
    with TestClient(app) as client:
        assert client.get("/medicines/names").status_code == 401
        register_and_login(client)
        names = client.get("/medicines/names").json()["names"]
        assert "Dolo 650" in names and "Paracetamol" in names and len(names) > 300


def test_scan_ui_offers_retry_tips_expiry_warning_and_name_suggestions():
    app_js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "app.js"),
                  encoding="utf-8").read()
    assert 'id="scanBtn"' in app_js and "Scan instead" in app_js and "Scan again" in app_js
    assert "may have expired" in app_js and 'list="wzNameList"' in app_js and "'/medicines/names'" in app_js


def test_the_two_readers_agreeing_give_one_answer_and_the_keyword_reader_works_alone():
    ai = ms.parse_vision_answer({"found": True, "brand_name": "DOLO-650", "generic_name": "Paracetamol", "strength": "650mg"})
    kw = ms.candidates_from_lines(BOX)
    both = ms.combine(ai, kw)
    assert [c["name"] for c in both["candidates"]] == ["Dolo-650"]       # same medicine: no second choice; capitals tidied
    alone = ms.combine(None, kw)
    assert alone["candidates"][0]["name"] == "dolo 650" and alone["candidates"][0]["generic"] == "paracetamol"
    assert alone["source"] == "keywords" and ms.combine(None, None) is None


def test_keyword_reader_ranks_the_capitalised_repeated_brand_above_the_ingredient():
    out = ms.candidates_from_lines(L("GLYCOMET 500", "Metformin Hydrochloride Tablets IP 500 mg", "GLYCOMET 500", "USV Private Limited"))
    assert out["candidates"][0]["name"] == "glycomet" and out["candidates"][0]["generic"] == "metformin"
    assert out["candidates"][0]["strength"] == "500mg"


def test_keyword_expiry_needs_a_four_digit_year_and_reads_ocr_zero_lookalikes():
    assert ms.candidates_from_lines(L("DOLO 650", "B.No A1 MFG.12/2025 EXP.@8/2027"))["expiry"]["label"] == "Aug 2027"
    assert ms.candidates_from_lines(L("DOLO 650", "EXP 05/20"))["expiry"] is None       # "05/2027" cut short is not 2020


def test_short_scraps_of_foil_text_never_become_a_medicine():
    # real strip photo: "core" (from a torn warning line) was offered as Corex, a cough syrup
    assert ms.candidates_from_lines(L("core Man daly", "Mfg Lic No 78 UA"))["candidates"] == []
    assert ms.candidates_from_lines(L("PAN 40"))["candidates"][0]["name"] == "pan 40"         # exact short names still work


def test_a_long_ingredient_with_a_garbled_ending_is_still_found():
    # real strip photo: OCR read "Paracetamol" as "Paracetaened"
    assert ms.candidates_from_lines(L("Paracetaened 4"))["candidates"][0]["name"] == "paracetamol"


def test_a_failed_scan_records_why_and_says_busy_when_the_ai_was_unavailable(monkeypatch):
    def rate_limited(b, why=None):
        why["ai"] = "groq:RateLimitError"
        return None
    monkeypatch.setattr(ms, "identify_with_vision", rate_limited)
    monkeypatch.setattr(ms, "read_pack_text", lambda b: L("core Man daly"))
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        r = client.post("/medicines/scan", data={"patient_id": pid}, files={"file": ("b.png", PNG, "image/png")})
        assert r.status_code == 422 and "busy" in r.json()["detail"]
        db = SessionLocal()
        entry = db.query(AuditLog).filter_by(patient_id=pid, action="medicine_scan_failed").one()
        assert entry.detail == "ai=groq:RateLimitError keywords=0 match(es) in 1 line(s)"
        db.close()

        def nothing_seen(b, why=None):
            why["ai"] = "no_medicine_seen"
            return None
        monkeypatch.setattr(ms, "identify_with_vision", nothing_seen)
        r = client.post("/medicines/scan", data={"patient_id": pid}, files={"file": ("b.png", PNG, "image/png")})
        assert r.status_code == 422 and "couldn't find a medicine name" in r.json()["detail"]
