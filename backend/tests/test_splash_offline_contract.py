"""App-opening animation and the no-internet popup / page."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def read(name):
    return open(os.path.join(STATIC, name), encoding="utf-8").read()


def test_every_app_page_opens_with_the_splash_animation_once_per_launch():
    for page in ("index.html", "login.html", "caregiver.html"):
        html = read(page)
        assert 'id="splash"' in html and "/static/splash.css" in html and "/static/splash.js" in html and "/static/offline.js" in html, page
        assert "sp_splash" in html                                                     # not replayed when moving between pages in one launch
    css = read("splash.css")
    for needle in ("sp-blob", "sp-cross", "sp-drift", "sp-rise", "prefers-reduced-motion"):    # the landing page's background motion
        assert needle in css, needle
    js = read("splash.js")
    assert "1200" in js and "3000" in js and "sp_splash" in js


def test_no_internet_shows_a_popup_not_a_blank_screen():
    js = read("offline.js")
    assert "No internet connection" in js and "'offline'" in js and "'online'" in js and "navigator.onLine" in js and "Try again" in js
    sw = read("sw-push.js")
    assert "OFFLINE_URL" in sw and "caches.match(OFFLINE_URL)" in sw and "mode !== 'navigate'" in sw     # opening the app offline shows the page
    page = read("offline.html")
    assert "No internet connection" in page and "Try again" in page and "<style>" in page             # self-contained: needs no network


def test_the_offline_page_is_served_without_login_and_the_manifest_matches_the_splash():
    r = TestClient(app).get("/offline")
    assert r.status_code == 200 and "No internet connection" in r.text
    assert '"background_color": "#F6FBF9"' in read("app.webmanifest")


def test_iphone_install_button_shows_the_two_taps_instead_of_doing_nothing():
    js = read("install.js")
    assert "if (isIOS) { showIosSheet(); return; }" in js and "if (isIOS && !standalone) setTimeout(showIosSheet" in js and "Add to Home Screen" in js and "Open in Safari" in js
    for page in ("index.html", "login.html", "caregiver.html", "install.html"):          # what iOS needs to make a proper home-screen app
        html = read(page)
        assert 'rel="apple-touch-icon"' in html and 'name="apple-mobile-web-app-capable"' in html, page
