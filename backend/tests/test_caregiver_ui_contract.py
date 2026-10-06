"""Static contracts for the caregiver portal pages (the browser pass looks at them; these stop decisions from regressing)."""
import os
import re

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
PAGES = open(os.path.join(STATIC, "caregiver-pages.js"), encoding="utf-8").read()
SHELL = open(os.path.join(STATIC, "caregiver.js"), encoding="utf-8").read()
HTML = open(os.path.join(STATIC, "caregiver.html"), encoding="utf-8").read()


def fn(name, src=PAGES):
    start = src.index(f"function {name}(")
    nxt = re.search(r"\n(?:async )?function \w+\(", src[start + 10:])
    return src[start:start + 10 + (nxt.start() if nxt else len(src))]


def test_each_page_is_its_own_function_with_its_own_route():
    for name in ("pagePatients", "pageToday", "pageMedicines", "pageNotes", "pageHistory", "pageEmergency", "pageSettings"):
        assert f"function {name}(" in PAGES, name
    for route in ("#/settings", "today|medicines|notes|history|emergency"):
        assert route in SHELL, route
    assert "hashchange" in SHELL and "pageToday" in SHELL and "pageNotes" in SHELL
    assert "caregiver-pages.js" in HTML and HTML.index("caregiver-pages.js") < HTML.index("caregiver.js\"")   # pages load first


def test_the_old_single_long_page_is_gone():
    assert "renderOverview" not in SHELL and "renderOverview" not in PAGES
    assert 'id="overview"' not in HTML and "patientPicker" not in HTML


def test_notes_page_has_messages_to_the_patient_and_care_team_only_handover_notes():
    body = fn("pageNotes")
    assert "Messages to the patient" in body and "Handover notes" in body and "care team only" in body.lower()
    assert "notify" in body.lower() and "kind: 'message'" in body and "kind: 'handover'" in body


def test_medicines_page_lets_the_caregiver_add_a_note_on_a_medicine():
    body = fn("pageMedicines")
    assert "Add note" in body and "kind: 'medicine'" in body and "medicine_id" in body


def test_today_page_has_remind_now_and_call_and_whatsapp():
    body = fn("pageToday")
    assert "Remind now" in body and "/nudge" in body
    assert "tel:" in body and "wa.me/" in body


def test_settings_page_controls_phone_alerts_per_patient():
    body = fn("pageSettings")
    assert "/caregiver/push/subscribe" in PAGES and "notify_missed" in body and "/prefs" in body
    assert "logout" in body.lower() or "logout" in SHELL


def test_every_piece_of_server_text_is_escaped_before_it_is_drawn():
    assert "const esc = " in PAGES
    for name in ("pagePatients", "pageToday", "pageMedicines", "pageNotes", "pageHistory", "pageEmergency", "noteListHtml"):
        body = fn(name)
        for raw in ("${n.body}", "${p.name}", "${n.author_name}", "${d.medicine_name}", "${o.patient.name}</h2>", "${m.name}</strong>"):
            assert raw not in body, f"{name} draws {raw} unescaped"
    assert "esc(n.body)" in fn("noteListHtml")


def test_a_revoked_link_shows_a_plain_message():
    assert "no longer linked" in SHELL.lower()
