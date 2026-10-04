"""
Static contracts for the patient UI. These cannot replace looking at the page
(the manual checklist and the browser pass do that) but they stop the
decisions made with the product owner from silently regressing:

  * the dashboard shows TODAY, not a 30-day list;
  * alerts use one calm design and never the emergency red;
  * every alert says what the problem is AND what to do.
"""

import os
import re

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
JS = open(os.path.join(STATIC, "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(STATIC, "style.css"), encoding="utf-8").read()


def fn_body(name):
    start = JS.index(f"function {name}(")
    nxt = re.search(r"\n(?:async )?function \w+\(", JS[start + 10:])
    return JS[start:start + 10 + (nxt.start() if nxt else len(JS))]


def test_dashboard_shows_today_and_not_the_whole_course():
    dash = fn_body("renderDashboard")
    assert "renderTodaySchedule(dash)" in dash and "Today's medicines" in dash
    assert "Upcoming doses" not in dash and "renderDoseCalendar" not in dash
    assert ">Upcoming<" not in dash                                        # no "Upcoming 83" tile
    assert "dash.today_doses" in fn_body("renderTodaySchedule")


def test_todays_list_covers_every_state_with_the_right_actions():
    body = fn_body("renderTodaySchedule")
    for state in ("taken", "skipped", "missed"):
        assert f"d.state === '{state}'" in body
    assert 'data-act="take"' in body and 'data-act="snooze"' in body and 'data-act="skip"' in body
    assert "data-missed-help" in body and "All done for today" in body


def test_alerts_have_one_shared_component_with_problem_and_action_sections():
    comp = fn_body("alertCardHtml")
    assert "What's the problem" in comp and "What to do" in comp
    for tone in ("action", "care", "info", "clear"):
        assert f".alert-card.tone-{tone}" in CSS


def test_every_alert_surface_uses_the_shared_component():
    for fn in ("timingAlertHtml", "renderInteractionRows", "renderFoodWarningRows", "conflictsHtml"):
        assert "alertCardHtml" in fn_body(fn), fn


def test_alerts_never_use_the_emergency_red():
    patient_alert_css = r"\.(?:alert-(?:card|icon|body|title|label|text|do|legend|summary|why)|gap-)"
    alert_css = "\n".join(re.findall(r"[^{}]*" + patient_alert_css + r"[^{}]*\{[^}]*\}", CSS))
    assert alert_css, "alert styles missing"
    assert "var(--alarm" not in alert_css
    assert "tone: 'action'" in JS and not re.search(r"tone: ?'(danger|alarm|emergency|critical)'", JS)


def test_a_colour_legend_explains_the_tones():
    legend = fn_body("alertLegendHtml")
    for label in ("Needs your action", "Use with care", "Good to know", "All fine"):
        assert label in legend


def test_timing_alert_always_ends_in_a_next_step_and_a_source_for_rule_based_cases():
    body = fn_body("timingAlertHtml")
    assert "Ask your pharmacist" in body and "whyDetailsHtml(c)" in body
    assert "Nothing changes unless you tap" in body and "don’t change your doses on your own" in body


def test_old_red_alert_boxes_are_gone_from_the_patient_screens():
    assert 'class="alert-item"' not in fn_body("renderDashboard")       # "medicines need confirmation"
    assert 'class="alert-item"' not in fn_body("renderSafetyCenter")    # "dosage checks"


def test_dashboard_is_not_cluttered():
    dash = fn_body("renderDashboard")
    for clutter in ("stat-row", "Treatment progress", "Drug interactions", "Food &amp; substance", "nudge",
                    "unconfirmed", "need confirmation", "dashConflictsCard", "Export calendar", "dashWhatsApp\""):
        assert clutter not in dash, clutter
    # what stays: next dose, today's list, a one-line timing hint, a WhatsApp popup button, misses/as-needed only when present
    for kept in ("heroHtml", "Today's medicines", "dashTimingChip", "showWhatsAppPopup", "missedHtml"):
        assert kept in dash, kept


def test_whatsapp_is_a_popup_not_a_dashboard_card():
    assert "async function showWhatsAppPopup" in JS and "showSafetyModal" in fn_body("showWhatsAppPopup")
    assert "renderDashboardWhatsApp" not in JS


def test_prescription_tab_has_only_scan_choose_file_and_type():
    body = fn_body("renderPrescriptions")
    assert "Scan medicine" in body and "Choose file" in body and "Or type it in" in body
    for gone in ("Upload a photo", "upload-zone", "uploadBtn", "Drag &amp; drop", "Read prescription"):
        assert gone not in body, gone
    assert 'id="scanInput" accept="image/*" capture="environment" hidden' in body       # opens the camera
    assert 'id="rxImageInput" accept="image/*" hidden' in body                          # opens the phone's files
    assert "scanInput.click()" in body and "rxInput.click()" in body


def test_patient_never_sees_the_pile_of_unconfirmed_drafts():
    assert "need confirmation" not in fn_body("renderDashboard")
    assert "need confirmation</div>" not in fn_body("renderGlance")


def test_taking_a_dose_early_shows_a_popup_instead_of_silently_succeeding():
    guard = fn_body("takeDoseWithGuard")
    assert "showNotice(" in guard and "catch (e)" in guard
    assert "isTakeableNow" in fn_body("renderTodaySchedule") and "Later today" in fn_body("renderTodaySchedule")
    assert "takeDoseWithGuard(" in fn_body("renderDashboard")                           # hero + list both go through it
    assert "Not due yet" in fn_body("renderDashboard")                                  # hero shows no button for a far dose


def test_downloads_carry_the_login_token():
    assert "async function downloadAuthed" in JS and "Authorization" in fn_body("downloadAuthed")
    # a bare <a href> to a protected endpoint sends no token - that was the "Export calendar" error
    assert 'href="/patients/${state.patientId}/calendar.ics"' not in JS
    assert 'href="/patients/${state.patientId}/report/pdf"' not in JS
    assert "downloadAuthed('/patients/${state.patientId}/calendar.ics'" in fn_body("renderReport")


def test_progress_and_adherence_live_in_the_care_report_not_the_dashboard():
    rep = fn_body("renderReport")
    assert "treatmentProgressHtml(dashForProgress)" in rep and "Adherence" in rep


def test_several_timing_suggestions_can_be_applied_in_one_tap():
    assert "data-apply-all" in fn_body("conflictsHtml")
    body = fn_body("applyAllSuggestions")
    assert "/reschedule" in body and "schedule-conflicts" in body and "i < 20" in body       # re-checks after every move, bounded
    assert "[data-apply-all]" in fn_body("wireConflictActions")


def test_dashboard_does_not_repeat_todays_misses_in_a_second_card_or_claim_all_done_after_a_miss():
    dash = fn_body("renderDashboard")
    assert "earlierMissed" in dash and "Missed earlier" in dash and "Missed recently" not in dash
    today = fn_body("renderTodaySchedule")
    assert "Nothing more scheduled today." in today and "All done for today ✓" in today and "anyMissed" in today
