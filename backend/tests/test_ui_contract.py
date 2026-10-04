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
    assert "dash.left_today" in dash and ">Upcoming<" not in dash          # "Left today", not "Upcoming 83"
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
