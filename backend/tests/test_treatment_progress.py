import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Medicine  # noqa: E402
from scheduler import generate_doses, compute_treatment_progress  # noqa: E402


def make_medicine(**overrides):
    defaults = dict(
        id=1, prescription_id=1, raw_text="Tab X 1-0-1 5 days", name="X",
        schedule_code="1-0-1", times='["08:30", "20:30"]', food="any",
        duration_days=5, is_prn=False, confidence=1.0, status="verified",
    )
    defaults.update(overrides)
    return Medicine(**defaults)


def test_day_1_on_the_first_day():
    med = make_medicine()
    doses = generate_doses(med, start_at=datetime(2026, 1, 1, 6, 0))
    progress = compute_treatment_progress(med, doses, now=datetime(2026, 1, 1, 9, 0))
    assert progress == {"current_day": 1, "total_days": 5, "ongoing": False}


def test_day_3_on_the_third_day():
    med = make_medicine()
    doses = generate_doses(med, start_at=datetime(2026, 1, 1, 6, 0))
    progress = compute_treatment_progress(med, doses, now=datetime(2026, 1, 3, 9, 0))
    assert progress["current_day"] == 3


def test_day_never_exceeds_total_days():
    med = make_medicine(duration_days=5)
    doses = generate_doses(med, start_at=datetime(2026, 1, 1, 6, 0))
    progress = compute_treatment_progress(med, doses, now=datetime(2026, 2, 1, 9, 0))
    assert progress["current_day"] == 5


def test_ongoing_medicine_has_no_total_days():
    med = make_medicine(duration_days=None)
    doses = generate_doses(med, start_at=datetime(2026, 1, 1, 6, 0))
    progress = compute_treatment_progress(med, doses, now=datetime(2026, 1, 10, 9, 0))
    assert progress["total_days"] is None
    assert progress["ongoing"] is True
    assert progress["current_day"] == 10


def test_prn_medicine_has_no_day_count():
    med = make_medicine(is_prn=True, times="[]")
    doses = generate_doses(med)
    progress = compute_treatment_progress(med, doses)
    assert progress["current_day"] is None
