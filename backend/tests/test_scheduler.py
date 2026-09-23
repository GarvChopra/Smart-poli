import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from db import Medicine, Dose  # noqa: E402
from scheduler import (  # noqa: E402
    generate_doses, SchedulingBlocked, compute_adherence,
    mark_taken, mark_missed, snooze, MAX_SNOOZES,
)


def make_medicine(**overrides):
    defaults = dict(
        id=1, prescription_id=1, raw_text="Tab X 1-0-1 5 days",
        name="X", normalized_name="x", dose_amount="500", dose_unit="mg",
        schedule_code="1-0-1", slots='["morning", "night"]',
        times='["08:30", "20:30"]', food="any", duration_days=5,
        is_prn=False, confidence=1.0, status="verified",
    )
    defaults.update(overrides)
    return Medicine(**defaults)


def test_needs_confirmation_is_blocked_from_scheduling():
    med = make_medicine(status="needs_confirmation")
    with pytest.raises(SchedulingBlocked):
        generate_doses(med)


def test_prn_generates_zero_dose_rows():
    med = make_medicine(is_prn=True, times=None, schedule_code="SOS")
    doses = generate_doses(med)
    assert doses == []


def test_5_day_course_generates_10_doses_for_twice_daily():
    med = make_medicine(duration_days=5)
    doses = generate_doses(med, start_at=datetime(2026, 1, 1, 6, 0))
    assert len(doses) == 10  # 2/day * 5 days


def test_ongoing_medicine_schedules_30_days():
    med = make_medicine(duration_days=None)
    doses = generate_doses(med, start_at=datetime(2026, 1, 1, 6, 0))
    assert len(doses) == 60  # 2/day * 30 days


def test_stat_generates_exactly_one_dose_not_repeated():
    med = make_medicine(schedule_code="STAT", times='[]', duration_days=None)
    doses = generate_doses(med, start_at=datetime(2026, 1, 1, 9, 0))
    assert len(doses) == 1


# ------------------------------------------------------- adherence, the critical bug

def test_day_1_of_5_day_course_is_not_20_percent():
    """
    All 10 doses generated up front; only the first 2 (day 1) have happened.
    Pending future doses must be excluded from the denominator, or this
    reads as 2/10 = 20% and looks like a broken product on day one.
    """
    med = make_medicine(duration_days=5, id=1)
    doses = generate_doses(med, start_at=datetime(2026, 1, 1, 6, 0))
    for d in doses:
        d.medicine_id = 1
    # Day 1's two doses: taken.
    mark_taken(doses[0])
    mark_taken(doses[1])
    # Everything else is still pending (in the future) — untouched.

    result = compute_adherence(doses)
    assert result["adherence_percent"] == 100.0, result
    assert result["pending"] == 8


def test_adherence_excludes_pending_but_counts_missed_and_skipped():
    med = make_medicine(duration_days=2, id=1)
    doses = generate_doses(med, start_at=datetime(2026, 1, 1, 6, 0))
    mark_taken(doses[0])
    mark_missed(doses[1])
    result = compute_adherence(doses)
    assert result["adherence_percent"] == 50.0
    assert result["pending"] == 2


def test_adherence_is_none_when_nothing_has_happened_yet():
    med = make_medicine(duration_days=5, id=1)
    doses = generate_doses(med, start_at=datetime(2026, 1, 1, 6, 0))
    result = compute_adherence(doses)
    assert result["adherence_percent"] is None


# ------------------------------------------------------- snooze escalation

def test_snooze_pushes_time_and_caps_at_max_then_forces_pending():
    dose = Dose(id=1, medicine_id=1, scheduled_at=datetime(2026, 1, 1, 8, 30),
                state="pending", snooze_count=0)
    original = dose.scheduled_at
    for _ in range(MAX_SNOOZES):
        snooze(dose)
    assert dose.snooze_count == MAX_SNOOZES
    assert dose.scheduled_at == original + timedelta(minutes=15 * MAX_SNOOZES)
    assert dose.state == "snoozed"

    # One more snooze attempt: forced back to pending, no further push.
    pushed_time = dose.scheduled_at
    snooze(dose)
    assert dose.state == "pending"
    assert dose.scheduled_at == pushed_time
