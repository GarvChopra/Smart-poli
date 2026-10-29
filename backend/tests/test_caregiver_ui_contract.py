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
    for name in ("pagePatients", "pageToday", "pageNotes", "pageHistory", "pageEmergency", "pageSettings"):
        assert f"function {name}(" in PAGES, name
    for route in ("#/settings", "today|history|report|notes|emergency"):
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


def test_there_is_no_medicines_page_and_history_is_a_month_calendar_you_tap_a_day_on():
    assert "pageMedicines" not in PAGES and "pageMedicines" not in SHELL and "['medicines', 'Medicines']" not in SHELL
    body = fn("pageHistory")
    assert "/calendar" in body and "hc-grid" in body and "data-day" in body and "DayTimeline.listHtml" in body   # month, tapped day
    assert "Per-medicine adherence" not in body and "Missed doses" not in body and "symptom" not in body.lower() and "hc-ring" not in body   # only the calendar
    popup = fn("openAddMedicinePopup")
    assert 'data-how="scan"' in popup and 'data-how="pdf"' in popup and 'data-how="manual"' in popup
    today = fn("pageToday")
    assert "cgAddMedBtn" in today and "openAddMedicinePopup" in today


def test_care_report_page_is_the_persons_own_report_read_only_with_a_pdf():
    body = fn("pageReport")
    assert "/report" in body and "Download PDF" in body and "rp-medbox" in body and "How doses went" in body
    assert "pageReport" in SHELL and "Care report" in SHELL


def test_today_page_has_remind_now_and_no_numbers_or_alert_strips():
    body = fn("pageToday")
    assert "Remind now" in body and "/nudge" in body
    assert "tel:" not in body and "wa.me/" not in body and "o.alerts" not in body and "nudges" not in body and "adherence" not in body.lower()


def test_settings_page_controls_phone_alerts_per_patient():
    body = fn("pageSettings")
    assert "/caregiver/push/subscribe" in PAGES and "notify_missed" in body and "/prefs" in body
    assert "logout" in body.lower() or "logout" in SHELL


def test_every_piece_of_server_text_is_escaped_before_it_is_drawn():
    assert "const esc = " in PAGES
    for name in ("pagePatients", "pageToday", "pageNotes", "pageHistory", "pageEmergency", "noteListHtml"):
        body = fn(name)
        for raw in ("${n.body}", "${p.name}", "${n.author_name}", "${d.medicine_name}", "${o.patient.name}</h2>", "${m.name}</strong>"):
            assert raw not in body, f"{name} draws {raw} unescaped"
    assert "esc(n.body)" in fn("noteListHtml")


def test_a_revoked_link_shows_a_plain_message():
    assert "no longer linked" in SHELL.lower()


def test_page_links_live_in_the_left_menu_not_in_a_tab_bar_on_the_page():
    assert "cg-tabs" not in PAGES and "<nav class=\"cg-tabs\"" not in PAGES
    assert 'id="drawerNav"' in HTML
    body = fn("renderDrawerNav", SHELL)
    for label in ("Today", "Notes", "History", "Care report", "Emergency"):
        assert label in SHELL, label
    assert "Family" in body and "Family home" not in body and "My medicines" not in body and "Settings" in body and "#/p/" in body
    assert "renderDrawerNav(" in fn("route", SHELL)


def test_the_family_home_has_a_remove_button_in_front_of_each_person_that_shares_the_confirm_popup():
    home = fn("pagePatients")
    assert "fam-remove" in home and "data-remove" in home and "openRemovePerson" in home
    assert "<a" in home and home.index("fam-item") < home.index("fam-remove")                 # a button beside the link, not inside it
    popup = fn("openRemovePerson")
    assert "/unlink" in popup and "not deleted" in popup and "told" in popup
    assert "openRemovePerson" in fn("patientFrame")                                           # the person's page uses the same popup
