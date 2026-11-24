import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from conftest import register_and_login  # noqa: E402


def test_emergency_card_data_and_public_page():
    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={
            "name": "Card Test", "age": 50, "sex": "F",
            "allergies": "Penicillin", "emergency_contact": "Son, 9999999999",
        })
        patient_id = r.json()["id"]
        assert r.json()["allergies"] == "Penicillin"

        client.post("/prescriptions", json={
            "patient_id": patient_id, "lines": ["Tab Dolo 650mg 1-0-1 PC x5d", "Tab Tramadol 50mg SOS"],
        })

        r = client.get(f"/patients/{patient_id}/emergency-card")
        assert r.status_code == 200
        data = r.json()
        assert data["patient"]["allergies"] == "Penicillin"
        assert len(data["scheduled_medicines"]) == 1
        assert len(data["as_needed_medicines"]) == 1
        assert data["has_emergency_triage_history"] is False

        # Public HTML page renders without auth and includes the allergy.
        card_path = data["card_path"]
        r = client.get(card_path)
        assert r.status_code == 200
        assert r.headers["referrer-policy"] == "no-referrer"
        assert 'name="robots" content="noindex"' in r.text
        assert "Last updated" in r.text
        assert "Penicillin" in r.text
        assert "Son, 9999999999" in r.text

        # QR PNG is a real image.
        r = client.get(f"/patients/{patient_id}/emergency-card/qr.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_emergency_flag_appears_after_emergency_triage():
    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "Flag Test"})
        patient_id = r.json()["id"]

        client.post("/triage/check", json={
            "patient_id": patient_id, "symptom_ids": ["chest_pain"],
            "answers": {"difficulty_breathing": True},
        })

        r = client.get(f"/patients/{patient_id}/emergency-card")
        assert r.json()["has_emergency_triage_history"] is True

        r = client.get(client.get(f"/patients/{patient_id}/emergency-card").json()["card_path"])
        assert "EMERGENCY" in r.text


def test_emergency_share_links_use_the_configured_origin(monkeypatch):
    import main
    import qrcode

    monkeypatch.delenv("SMARTPOLI_PUBLIC_URL", raising=False)
    monkeypatch.setenv("SMARTPOLI_PUBLIC_BASE_URL", "https://care.example.test/")
    qr_urls = []
    pdf_urls = []
    make_qr = qrcode.make

    def record_qr(url, **kwargs):
        qr_urls.append(url)
        return make_qr(url, **kwargs)

    def record_pdf(data, url, photo):
        pdf_urls.append(url)
        return b"%PDF-test"

    monkeypatch.setattr(qrcode, "make", record_qr)
    monkeypatch.setattr(main, "build_wallet_card_pdf", record_pdf)
    with TestClient(app) as client:
        register_and_login(client)
        pid = client.post("/patients", json={"name": "Card Test"}).json()["id"]
        card = f"/patients/{pid}/emergency-card"
        via_proxy = {"Host": "proxy.example.test", "X-Forwarded-Host": "edge.example.test"}
        data = client.get(card, headers=via_proxy).json()
        assert data["card_url"] == "https://care.example.test" + data["card_path"]
        assert client.get(card + "/qr.png", headers=via_proxy).status_code == 200
        assert client.get(card + "/card.pdf", headers=via_proxy).status_code == 200
    assert qr_urls == pdf_urls == [data["card_url"]]


def test_patient_edit_updates_allergies():
    with TestClient(app) as client:
        register_and_login(client)
        r = client.post("/patients", json={"name": "Edit Test"})
        patient_id = r.json()["id"]
        r = client.patch(f"/patients/{patient_id}", json={"allergies": "Sulfa drugs"})
        assert r.status_code == 200
        assert r.json()["allergies"] == "Sulfa drugs"
