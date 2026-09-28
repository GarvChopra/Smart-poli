import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from main import app  # noqa: E402
from conftest import register_and_login  # noqa: E402


def _png_data_url(size=(8, 8)):
    buf = io.BytesIO()
    Image.new("RGB", size, (18, 135, 111)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _patient(client, **fields):
    register_and_login(client)
    body = {"name": "Card Holder", "age": 58, "sex": "M", "blood_group": "B+",
            "allergies": "Penicillin", "emergency_contact": "Son, 9999999999", **fields}
    pid = client.post("/patients", json=body).json()["id"]
    client.post("/prescriptions", json={"patient_id": pid, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d"]})
    return pid


def _public_path(client, pid):
    return client.get(f"/patients/{pid}/emergency-card").json()["card_path"]


def test_profile_defaults_and_round_trip():
    with TestClient(app) as client:
        pid = _patient(client)
        r = client.get(f"/patients/{pid}/emergency-card/profile")
        assert r.status_code == 200
        body = r.json()
        assert body["conditions"] is None and body["instructions"] is None
        assert body["has_photo"] is False
        assert all(body["share"].values())

        r = client.put(f"/patients/{pid}/emergency-card/profile", json={
            "conditions": "Type 2 diabetes, hypertension",
            "instructions": "Diabetic — check blood sugar",
            "photo": _png_data_url(),
            "share": {"medicines": False},
        })
        assert r.status_code == 200, r.text
        body = client.get(f"/patients/{pid}/emergency-card/profile").json()
        assert body["conditions"] == "Type 2 diabetes, hypertension"
        assert body["has_photo"] is True
        assert body["share"]["medicines"] is False
        assert body["share"]["allergies"] is True

        card = client.get(f"/patients/{pid}/emergency-card").json()
        assert card["profile"]["instructions"] == "Diabetic — check blood sugar"
        assert card["profile"]["share"]["medicines"] is False

        r = client.get(f"/patients/{pid}/emergency-card/photo")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"


def test_partial_put_keeps_other_fields_and_empty_string_clears():
    with TestClient(app) as client:
        pid = _patient(client)
        client.put(f"/patients/{pid}/emergency-card/profile",
                   json={"conditions": "Asthma", "instructions": "Inhaler in bag"})
        client.put(f"/patients/{pid}/emergency-card/profile", json={"conditions": ""})
        body = client.get(f"/patients/{pid}/emergency-card/profile").json()
        assert body["conditions"] is None
        assert body["instructions"] == "Inhaler in bag"


def test_profile_validation():
    with TestClient(app) as client:
        pid = _patient(client)
        url = f"/patients/{pid}/emergency-card/profile"
        assert client.put(url, json={"conditions": "x" * 501}).status_code == 422
        assert client.put(url, json={"instructions": "x" * 501}).status_code == 422
        assert client.put(url, json={"photo": "data:text/html;base64,PHNjcmlwdD4="}).status_code == 422
        assert client.put(url, json={"photo": "data:image/png;base64,not-base64!!"}).status_code == 422
        big = "data:image/png;base64," + base64.b64encode(b"\x89PNG" + b"0" * 210_000).decode()
        assert client.put(url, json={"photo": big}).status_code == 422
        assert client.put(url, json={"share": {"password": True}}).status_code == 422


def test_photo_can_be_removed():
    with TestClient(app) as client:
        pid = _patient(client)
        url = f"/patients/{pid}/emergency-card/profile"
        client.put(url, json={"photo": _png_data_url()})
        client.put(url, json={"photo": ""})
        assert client.get(url).json()["has_photo"] is False
        assert client.get(f"/patients/{pid}/emergency-card/photo").status_code == 404


def test_read_only_caregiver_cannot_edit_profile_but_is_listed():
    with TestClient(app) as client:
        pid = _patient(client)
        code = client.post(f"/patients/{pid}/caregiver-links").json()["code"]
        doc_code = client.post(f"/patients/{pid}/doctor-links").json()["code"]
        path = _public_path(client, pid)

        register_and_login(client, role="caregiver", name="Suresh Caregiver")
        assert client.post("/caregiver/link/redeem", json={"code": code}).status_code == 200
        r = client.put(f"/patients/{pid}/emergency-card/profile", json={"conditions": "hack"})
        assert r.status_code == 403

        register_and_login(client, role="doctor", name="Dr. Rao Test")
        assert client.post("/doctor/link/redeem", json={"code": doc_code}).status_code == 200

        card = client.get(f"/patients/{pid}/emergency-card").json()
        assert card["care_team"]["caregivers"] == ["Suresh Caregiver"]
        assert card["care_team"]["doctors"] == ["Dr. Rao Test"]

        text = client.get(path).text
        assert "Suresh Caregiver" in text
        assert "Dr. Rao Test" in text


def test_public_page_respects_share_choices():
    with TestClient(app) as client:
        pid = _patient(client)
        client.put(f"/patients/{pid}/emergency-card/profile", json={
            "conditions": "Epilepsy", "instructions": "Turn on side during seizure",
        })
        path = _public_path(client, pid)
        text = client.get(path).text
        for s in ("Card Holder", "Penicillin", "Epilepsy", "Dolo", "Son, 9999999999",
                  "Turn on side during seizure", "B+"):
            assert s in text

        client.put(f"/patients/{pid}/emergency-card/profile", json={"share": {
            "allergies": False, "conditions": False, "medicines": False,
            "emergency_contact": False, "instructions": False, "blood_group": False,
        }})
        text = client.get(path).text
        assert "Card Holder" in text
        for s in ("Penicillin", "Epilepsy", "Dolo", "Son, 9999999999", "Turn on side", "B+"):
            assert s not in text


def test_public_photo_follows_share_and_token():
    with TestClient(app) as client:
        pid = _patient(client)
        url = f"/patients/{pid}/emergency-card/profile"
        client.put(url, json={"photo": _png_data_url()})
        path = _public_path(client, pid)
        client.headers.pop("Authorization")
        assert client.get(path + "/photo").status_code == 200
        assert 'src="' + path + '/photo"' in client.get(path).text

    with TestClient(app) as client:
        pid = _patient(client)
        url = f"/patients/{pid}/emergency-card/profile"
        client.put(url, json={"photo": _png_data_url(), "share": {"photo": False}})
        path = _public_path(client, pid)
        assert client.get(path + "/photo").status_code == 404
        assert path + "/photo" not in client.get(path).text

        client.put(url, json={"share": {"photo": True}})
        client.post(f"/patients/{pid}/emergency-card/revoke")
        assert client.get(path + "/photo").status_code == 404


def test_public_page_escapes_patient_text():
    with TestClient(app) as client:
        pid = _patient(client, allergies="<script>alert(1)</script>")
        client.put(f"/patients/{pid}/emergency-card/profile",
                   json={"conditions": "<img src=x onerror=alert(2)>"})
        text = client.get(_public_path(client, pid)).text
        assert "<script>alert(1)</script>" not in text
        assert "<img src=x" not in text
        assert "&lt;script&gt;" in text


def test_wallet_card_pdf():
    with TestClient(app) as client:
        pid = _patient(client)
        client.put(f"/patients/{pid}/emergency-card/profile",
                   json={"conditions": "Asthma", "photo": _png_data_url()})
        r = client.get(f"/patients/{pid}/emergency-card/card.pdf")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content[:4] == b"%PDF"


def test_profile_edit_moves_last_updated():
    with TestClient(app) as client:
        pid = _patient(client)
        before = client.get(f"/patients/{pid}/emergency-card").json()["last_updated"]
        client.put(f"/patients/{pid}/emergency-card/profile", json={"conditions": "Asthma"})
        after = client.get(f"/patients/{pid}/emergency-card").json()["last_updated"]
        assert after > before
