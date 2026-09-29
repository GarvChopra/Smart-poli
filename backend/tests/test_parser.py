import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import parse_medicine_line, is_likely_header_line  # noqa: E402


def test_well_formed_line_is_verified():
    r = parse_medicine_line('Tab Dolo 650mg 1-0-1 PC x5d')
    assert r['name'] == 'Dolo'
    assert r['dose_amount'] == '650'
    assert r['dose_unit'] == 'mg'
    assert r['food'] == 'after'
    assert r['duration_days'] == 5
    assert r['status'] in ('verified', 'review')
    assert r['is_prn'] is False


def test_sos_is_prn_and_never_needs_scheduling():
    r = parse_medicine_line('Syrup Crocin 5ml SOS')
    assert r['is_prn'] is True
    assert r['doses'] == []


def test_malformed_slot_code_is_needs_confirmation():
    r = parse_medicine_line('Tab Dolo 1-?-1')
    assert r['status'] == 'needs_confirmation'
    assert r['confidence'] < 0.70


def test_unknown_drug_name_drags_confidence_down():
    r = parse_medicine_line('Tab Zqxywnotarealdrug123 500mg 1-0-1')
    assert r['field_confidence']['name'] < 0.70
    assert r['status'] == 'needs_confirmation'
    # raw_text must survive regardless of confidence
    assert r['raw_text'] == 'Tab Zqxywnotarealdrug123 500mg 1-0-1'


def test_no_frequency_gives_needs_confirmation():
    r = parse_medicine_line('Tab Paracetamol 650mg')
    assert r['confidence'] == 0
    assert r['status'] == 'needs_confirmation'


def test_dotted_abbreviation_line():
    r = parse_medicine_line('Tab Crocin b.d. p.c.')
    assert r['name'] == 'Crocin'
    assert r['food'] == 'after'


def test_header_lines_without_medicine_shape_are_detected():
    """A photo of a real prescription includes doctor/patient/complaint
    lines above the actual medicines -- these aren't medicines at all, so
    fuzzy-matching them against the drug list correctly scores near zero.
    That's not a confidence bug; the bug is trying to parse these lines as
    medicines in the first place.

    Deliberately does NOT try to catch every possible symptom description
    ("Mild headache", "Low grade fever") via keywords -- that vocabulary is
    unbounded and risks false-positiving on real drug names. Those still
    fall through to needs_confirmation like any other unmatched line; the
    WhatsApp format rolls them into a single count instead of spelling out
    each one, which is what actually fixes the wall-of-text problem."""
    for line in [
        "SAMPLE PRESCRIPTION",
        "Dr. Ananya Mehta MBBS, MD (General Medicine)",
        "Date: 28 September 2026 Patient Gary Chopra 18 years Male",
        "Chief Complaints",
    ]:
        assert is_likely_header_line(line), line


def test_lines_with_medicine_shape_are_never_treated_as_headers():
    """Safety net: a line with a form word, a dose amount+unit, or a
    schedule token always goes through full parsing, even if it also
    happens to contain a header-ish word -- a real medicine must never be
    silently dropped."""
    for line in [
        "Tab Dolo 650mg 1-0-1 PC x5d",
        "Tab Zqxywnotarealdrug123 500mg 1-0-1",
        "Syrup Crocin 5ml SOS",
    ]:
        assert not is_likely_header_line(line), line
