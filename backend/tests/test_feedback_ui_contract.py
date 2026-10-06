"""Static contracts for the Feedback page, the occasional rate popup, the menu entries and the login clean-up."""
import os
import re

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def read(name):
    return open(os.path.join(STATIC, name), encoding="utf-8").read()


FB, LOGIN, INDEX, APP = read("feedback.js"), read("login.html"), read("index.html"), read("app.js")
CG_HTML, CG_SHELL, CG_PAGES = read("caregiver.html"), read("caregiver.js"), read("caregiver-pages.js")


def fn(name, src=FB):
    start = src.index(f"function {name}(")
    nxt = re.search(r"\n(?:async )?function \w+\(", src[start + 10:])
    return src[start:start + 10 + (nxt.start() if nxt else len(src))]


def test_the_demo_logins_are_gone_from_the_login_page():
    assert "demo1234" not in LOGIN and "smartpoli.demo" not in LOGIN and "auth-demo-note" not in LOGIN and "Demo accounts" not in LOGIN


def test_the_feedback_page_is_full_with_stars_topics_message_contact_and_history():
    body = fn("renderFeedbackPage")
    for needle in ("How would you rate", "This is about", "Your message", "contact me", "maxlength=\"1000\"", "'POST', '/feedback'"):
        assert needle in body, needle
    assert "/feedback/mine" in FB and "Your earlier feedback" in FB
    for topic in ("Problem", "Idea", "Praise", "Other"):
        assert topic in FB, topic
    assert "esc(f.message)" in FB


def test_all_server_text_is_escaped_in_the_feedback_ui():
    assert "const esc = " in FB or "function esc(" in FB
    for raw in ("${f.message}", "${m.message}", "${e.message}</"):
        assert raw not in FB, raw


def test_the_rate_popup_asks_by_stars_then_a_short_message_with_not_now_and_never():
    body = fn("showRatePrompt")
    assert "Enjoying SmartPoli" in body and "Not now" in body and "Don" in body and "source: 'popup'" in body
    assert "longer" in body.lower()                                       # a way to the full page


def test_the_popup_never_covers_another_popup_and_waits_before_showing():
    body = fn("init")
    assert "safety-modal-overlay" in body and "setTimeout" in body and "shouldPrompt" in body


def test_patients_get_a_feedback_page_in_their_menu_and_the_popup():
    assert 'data-tab="feedback"' in INDEX and 'id="view-feedback"' in INDEX and "/static/feedback.js" in INDEX
    assert "feedback" in re.search(r"function renderActiveTab\(\)[\s\S]*?\n\}", APP).group(0).lower()
    assert "SmartFeedback.init" in APP


def test_caregivers_get_a_feedback_page_in_their_menu_and_the_popup():
    assert "/static/feedback.js" in CG_HTML and CG_HTML.index("feedback.js") < CG_HTML.index("caregiver.js\"")
    assert "#/feedback" in CG_SHELL and "Feedback" in CG_SHELL and "pageFeedback" in CG_SHELL
    assert "function pageFeedback(" in CG_PAGES
    assert "SmartFeedback.init" in CG_SHELL
