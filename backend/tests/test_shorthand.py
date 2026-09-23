"""
Pytest port of shorthand.test.js — keep every behaviour identical to the JS suite.
"""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shorthand import decode_schedule, parse_slot_code  # noqa: E402


# ------------------------------------------------------------ slot notation

def test_1_0_1_gives_morning_and_night_only():
    r = decode_schedule('Tab Paracetamol 650mg 1-0-1 PC x5d')
    assert r['dosesPerDay'] == 2
    assert r['slots'] == ['morning', 'night']
    assert r['times'] == ['08:30', '20:30']
    assert r['food'] == 'after'
    assert r['durationDays'] == 5
    assert r['confidence'] == 1


def test_1_0_1_plain_language_lists_every_slot_including_zero():
    r = decode_schedule('1-0-1 after meals')
    assert re.search(r'Morning — 1 tablet', r['plainLanguage'])
    assert re.search(r'Afternoon — 0 tablets', r['plainLanguage'])
    assert re.search(r'Night — 1 tablet', r['plainLanguage'])
    assert re.search(r'Take after meals\.', r['plainLanguage'])


def test_1_1_1_gives_three_doses():
    r = decode_schedule('Tab Azithral 500 1-1-1 x3d')
    assert r['dosesPerDay'] == 3
    assert r['slots'] == ['morning', 'afternoon', 'night']


def test_0_1_0_gives_afternoon_only():
    r = decode_schedule('0-1-0')
    assert r['dosesPerDay'] == 1
    assert r['slots'] == ['afternoon']


def test_2_0_2_counts_units_separately_from_doses():
    r = decode_schedule('Tab Shelcal 2-0-2')
    assert r['dosesPerDay'] == 2
    assert r['unitsPerDay'] == 4


def test_four_part_code_maps_to_morning_afternoon_evening_night():
    r = decode_schedule('1-0-1-1')
    assert r['slots'] == ['morning', 'evening', 'night']
    assert r['dosesPerDay'] == 3


def test_half_tablets_parse_from_glyph_and_from_fraction():
    glyph = decode_schedule('½-0-½')
    assert glyph['unitsPerDay'] == 1
    frac = decode_schedule('1/2-0-1/2')
    assert frac['unitsPerDay'] == 1


# ------------------------------------------------------------ abbreviations

def test_tds_before_food_gives_three_doses_shifted_30_min_earlier():
    r = decode_schedule('Cap Amoxicillin 500mg TDS AC 7 days')
    assert r['dosesPerDay'] == 3
    assert r['food'] == 'before'
    assert r['times'] == ['08:00', '13:30', '20:00']
    assert r['durationDays'] == 7


def test_od_before_breakfast():
    r = decode_schedule('Pantoprazole 40mg OD before breakfast')
    assert r['dosesPerDay'] == 1
    assert r['food'] == 'before'


def test_hs_schedules_at_bedtime():
    r = decode_schedule('Tab Alprazolam 0.25mg HS 10 days')
    assert r['slots'] == ['bedtime']
    assert r['times'] == ['22:00']
    assert r['durationDays'] == 10


def test_bd_and_bid_are_equivalent():
    assert decode_schedule('BD')['dosesPerDay'] == decode_schedule('BID')['dosesPerDay']


def test_q8h_uses_fixed_clock_times_not_named_slots():
    r = decode_schedule('Inj Ceftriaxone Q8H x3d')
    assert r['times'] == ['06:00', '14:00', '22:00']
    assert len(r['slots']) == 0
    assert r['dosesPerDay'] == 3


def test_dotted_abbreviations_normalise():
    r = decode_schedule('Tab X b.d. p.c.')
    assert r['dosesPerDay'] == 2
    assert r['food'] == 'after'


# ------------------------------------------------------------ safety cases

def test_sos_generates_zero_scheduled_doses():
    r = decode_schedule('Syrup Crocin 5ml SOS')
    assert r['prn'] is True
    assert len(r['doses']) == 0
    assert len(r['times']) == 0
    assert r['dosesPerDay'] == 0
    assert re.search(r'only when needed', r['plainLanguage'], re.I)


def test_prn_generates_zero_scheduled_doses():
    r = decode_schedule('Tab Dolo 650 PRN')
    assert r['prn'] is True
    assert len(r['doses']) == 0


def test_malformed_slot_code_is_flagged_never_guessed():
    r = decode_schedule('Tab Unknown 1-?-1')
    assert '1-?-1' in r['unparsed']
    assert r['confidence'] < 0.7, f"expected low confidence, got {r['confidence']}"
    assert r['dosesPerDay'] == 0


def test_no_frequency_at_all_gives_zero_confidence_and_no_doses():
    r = decode_schedule('Tab Paracetamol 650mg')
    assert r['confidence'] == 0
    assert len(r['doses']) == 0
    assert re.search(r'could not be read', r['plainLanguage'], re.I)


def test_conflicting_frequencies_drop_confidence_and_are_reported():
    r = decode_schedule('Tab X 1-0-1 TDS')
    assert r['confidence'] < 0.7, f"expected low confidence, got {r['confidence']}"
    assert any(re.search(r'conflict', n, re.I) for n in r['notes'])


def test_empty_input_is_handled_without_throwing():
    r = decode_schedule('')
    assert r['confidence'] == 0
    assert len(r['doses']) == 0


def test_0_0_0_is_treated_as_no_doses_and_low_confidence():
    r = decode_schedule('Tab X 0-0-0 x5d')
    assert r['dosesPerDay'] == 0
    assert r['confidence'] < 0.7


# ------------------------------------------------------------ duration

def test_ongoing_treatment_has_no_end_date():
    r = decode_schedule('Tab Metformin 500mg 1-0-1 continue')
    assert r['ongoing'] is True
    assert r['durationDays'] is None
    assert re.search(r'until your doctor', r['plainLanguage'], re.I)


def test_uk_shorthand_5_7_means_five_days():
    assert decode_schedule('BD 5/7')['durationDays'] == 5


def test_uk_shorthand_2_52_means_two_weeks():
    assert decode_schedule('OD 2/52')['durationDays'] == 14


def test_weeks_convert_to_days():
    assert decode_schedule('BD 2 weeks')['durationDays'] == 14


def test_total_tablet_count_derives_duration_and_flags_uneven():
    r = decode_schedule('Tab Y 1-0-1 10 tabs')
    assert r['durationDays'] == 5
    uneven = decode_schedule('Tab Y 1-1-1 10 tabs')
    assert uneven['durationDays'] == 3
    assert any(re.search(r'confirm', n, re.I) for n in uneven['notes'])


# ------------------------------------------------------------ config

def test_custom_slot_times_are_honoured():
    r = decode_schedule('1-0-1', options={'slotTimes': {'morning': '07:00', 'night': '21:00'}})
    assert r['times'] == ['07:00', '21:00']
