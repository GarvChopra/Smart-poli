"""The 'How SmartPoli works' tour: five plain cards, shown once after the first-login popup, reopenable from the menu."""
import os
import re

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def read(name):
    return open(os.path.join(STATIC, name), encoding="utf-8").read()


def test_the_tour_explains_the_main_things_in_plain_words():
    js = read("tour.js")
    titles = re.findall(r"title: '([^']+)'", js)
    assert len(titles) == 8
    text = js.lower()
    for needle in ("enter manually", "dashboard", "notifications", "missed", "double up", "official labels", "estimate", "safety centre"):
        assert needle in text, needle
    for tab in ("prescriptions", "dashboard", "settings", "safety"):                 # each step opens the real screen it explains
        assert f"tab: '{tab}'" in js, tab
    assert "button[data-tab=" in js and "tour-spot" in js


def test_the_tour_is_wired_in_first_login_and_the_menu():
    html, app = read("index.html"), read("app.js")
    assert '/static/tour.js' in html and 'id="tourBtn"' in html and "How SmartPoli works" in html
    assert "SmartTour.showFirstTime()" in app and "SmartTour.show()" in app and "tour-root" in app      # shown at login; due popup waits
    tour = read("tour.js")
    assert "smartpoli_tour_done" in tour and "prefers-reduced-motion" in read("style.css")
