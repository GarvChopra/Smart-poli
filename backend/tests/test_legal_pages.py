"""Terms of Use and Privacy Policy exist, are linked where people sign up and in Settings, and say the things that matter."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
read = lambda n: open(os.path.join(STATIC, n), encoding="utf-8").read()  # noqa: E731


def test_both_pages_are_public_served_safely_and_cross_link():
    c = TestClient(app)
    for path, other in (("/terms", "/privacy"), ("/privacy", "/terms")):
        r = c.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert f'href="{other}"' in r.text and 'href="/"' in r.text


def test_the_terms_say_it_is_not_medical_advice_and_to_call_112():
    t = read("terms.html")
    for must in ("not a medical device", "does not diagnose", "112", "confirm", "best effort", "delete your account", "caregiver"):
        assert must.lower() in t.lower(), must


def test_the_privacy_policy_matches_what_the_app_really_does():
    p = read("privacy.html")
    for must in ("do not sell", "Groq", "Gemini", "openFDA", "Twilio", "Google", "not stored", "14 days", "Delete my account",
                 "emergency card link", "hashed", "Feedback"):
        assert must.lower() in p.lower(), must
    for tracker in ("google-analytics", "gtag(", "facebook", "hotjar"):
        assert tracker not in read("login.html").lower() and tracker not in read("index.html").lower()


def test_people_see_the_terms_when_they_sign_up_and_can_find_them_in_settings():
    login = read("login.html")
    assert 'href="/terms"' in login and 'href="/privacy"' in login and "agree" in login
    assert 'href="/terms"' in read("index.html") and 'href="/privacy"' in read("index.html")          # patient app Settings
    assert 'href="/terms"' in read("caregiver-pages.js") and 'href="/privacy"' in read("caregiver-pages.js")   # caregiver Settings
