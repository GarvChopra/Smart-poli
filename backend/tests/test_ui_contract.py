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


def test_prescription_tab_has_scan_or_enter_manually_choose_file_and_type():
    body = fn_body("renderPrescriptions")
    assert "Scan medicine" in body and "Enter manually" in body and 'class="or-divider"' in body
    assert "Choose file" in body and "Or type it in" in body
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
    # the report is deliberately short: medicines, how doses went, then details behind "more" links
    assert "How my doses went" in rep and "Medicines I take" in rep and "rp-more" in rep


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


def test_the_take_button_is_always_there_and_always_explains_itself():
    today = fn_body("renderTodaySchedule")
    assert "is-early" in today and 'data-act="take"' in today                         # far doses keep a (muted) Take button
    dash = fn_body("renderDashboard")
    assert "data-hero-take" in dash and "is-early" in dash and "Not due yet" in dash  # hero button also stays, muted
    guard = fn_body("takeDoseWithGuard")
    assert "confirmDialog(" in guard and "Taking this early?" in guard                # soft question when 30 min - 2 h early
    assert "showNotice(" in guard and "Not yet — don’t take this now" in guard         # hard refusal shown as a popup
    assert ".is-early" in CSS


def test_confirm_dialog_resolves_only_on_the_yes_button():
    body = fn_body("confirmDialog")
    assert "resolve(true)" not in body or "done(true)" in body
    assert "#cdYes" in body and "#cdNo" in body and "done(false)" in body


def test_refused_taps_explain_why_and_only_soft_warnings_offer_i_already_took_it():
    guard = fn_body("takeDoseWithGuard")
    assert "info.can_override" in guard and "I already took it" in guard and "{ override: true }" in guard
    assert "These two medicines need a gap" in guard and "Too soon after your last dose" in guard
    auth_js = open(os.path.join(STATIC, "auth.js"), encoding="utf-8").read()
    assert "err.info = detail" in auth_js and "err.status = res.status" in auth_js           # the structured reason reaches the UI


def test_each_open_dose_says_when_and_how_to_take_it_without_locking_anything():
    today = fn_body("renderTodaySchedule")
    assert "d.slot" in today and "take after food" in today and "take before food" in today
    assert "disabled" not in today and "lock" not in today.lower()                         # no locked/disabled state: tapping explains


def test_when_the_safe_answer_is_to_wait_the_wait_button_is_the_prominent_one():
    assert "safeIsNo" in fn_body("confirmDialog") and "'OK, I’ll wait', true)" in fn_body("takeDoseWithGuard")


def test_first_login_asks_name_and_age_in_a_popup_not_a_create_patient_page():
    assert "createNewPatient" not in JS and "Create patient profile" not in JS and "newPatientBtn" not in JS
    body = fn_body("askFirstProfile")
    assert "fpName" in body and "fpAge" in body and "'POST', '/patients'" in body
    assert "askFirstProfile()" in fn_body("renderNoPatientState")


def test_push_is_sent_with_high_urgency_so_android_delivers_it_while_dozing():
    src = open(os.path.join(os.path.dirname(__file__), "..", "webpush_service.py"), encoding="utf-8").read()
    assert '"Urgency": "high"' in src


def test_scan_flow_has_failure_popup_confirm_popup_and_schedule_step():
    assert "Scan failed" in fn_body("scanFailedPopup") and "Enter manually" in fn_body("scanFailedPopup")
    wiz = fn_body("medicineWizard")
    assert "Is this your medicine?" in wiz and "No, enter manually" in wiz
    assert "How many times a day?" in wiz and "At what time?" in wiz and "Repeat" in wiz and "Choose days" in wiz
    assert "With food?" in wiz and "For how long?" in wiz
    assert "'/medicines/manual'" in wiz and "/confirm" not in wiz        # added and scheduled in one step: no confirm
    assert "scanFailedPopup()" in fn_body("scanMedicinePhoto")


def test_adding_a_medicine_checks_it_against_the_others_and_shows_a_warning_popup():
    body = fn_body("afterMedicineAdded")
    assert "/pair-check" in body and "Please check this" in body and "No known clash" in body
    assert "pairWarningHtml" in body and "interactionWarningHtml" in body and "timingAlertHtml" in body
    assert "wireShiftButtons" in body and "/shift" in fn_body("wireShiftButtons")      # "Move X to <time>" button
    assert "loadMedicineInfoInto" in body                              # "Used for: ..." under the new medicine


def test_what_a_medicine_is_for_shows_in_the_safety_center_and_the_care_report():
    assert "data-med-info" in fn_body("renderSafetyCenter") and "loadMedicineInfoInto" in fn_body("renderSafetyCenter")
    rep = fn_body("renderReport")
    # the Care report does NOT repeat what the Safety center shows
    assert "data-med-info" not in rep and "Why I may be taking these" not in rep and "Medicine warnings" not in rep
    assert "renderInteractionRows" not in rep and "renderFoodWarningRows" not in rep


def test_typed_prescriptions_and_the_safety_center_run_the_same_combination_check():
    assert "checkPrescriptionCombinations" in fn_body("renderRxResult")
    assert "/pair-check" in fn_body("checkPrescriptionCombinations") and "Please check this" in fn_body("checkPrescriptionCombinations")
    assert "loadCombinationCheckInto" in fn_body("renderSafetyCenter") and "safetyCombos" in fn_body("renderSafetyCenter")
    assert "No known clash between your medicines" in fn_body("loadCombinationCheckInto")


def test_every_warning_popup_lets_the_person_remove_a_medicine():
    for fn in ("showOpenWarnings", "afterMedicineAdded", "checkPrescriptionCombinations"):
        body = fn_body(fn)
        assert "removeButtonsHtml" in body and "wireRemoveButtons" in body, fn
    assert "These shouldn’t be taken together" in fn_body("showOpenWarnings")
    assert "/open-warnings" in fn_body("showOpenWarnings") and "sessionStorage" in fn_body("showOpenWarnings")
    assert "data-remove-med" in fn_body("renderSafetyCenter")
    assert "DELETE" in fn_body("removeMedicine")
