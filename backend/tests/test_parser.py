import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import parse_medicine_line  # noqa: E402


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
