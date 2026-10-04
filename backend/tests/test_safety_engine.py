"""
Timing-safety engine: spacing conflicts, proposals, missed-dose guidance.

Pure-function tests with deterministic fixtures (no database, no network, no
real patient data). They prove the ENGINE behaves as the rule file says; they
do not prove the rules themselves are clinically right - that needs a
pharmacist (see review_status in safety_rules.json).
"""

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import safety_engine as eng  # noqa: E402

RULES = eng.load_rules()
D = datetime(2026, 10, 5, 0, 0)          # an arbitrary fixed day (patient-local)


def at(h, m=0, day=0):
    return D + timedelta(days=day, hours=h, minutes=m)


_id = iter(range(1, 10_000))


def dose(when, state="pending", acted=None):
    return {"id": next(_id), "scheduled_at": when, "state": state, "acted_at": acted}


def med(mid, name, doses):
    return {"medicine_id": mid, "name": name, "doses": doses}


def timing(conflicts):
    return [c for c in conflicts if c["kind"] == "timing"]


# ---------------------------------------------------------------- rule file integrity

def test_every_rule_has_a_quoted_dated_source_and_is_marked_unreviewed():
    sources = []

    def walk(n):
        if isinstance(n, dict):
            if "set_id" in n and "quote" in n:
                sources.append(n)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(RULES)
    assert len(sources) >= 20
    for s in sources:
        assert s["quote"].strip() and len(s["set_id"]) == 36 and s["effective"] and s["title"]
    assert "UNREVIEWED" in RULES["review_status"]
    every_rule = (RULES["spacing_rules"] + RULES["interval_unspecified_rules"] + [RULES["alendronate_rule"]]
                  + list(RULES["missed_dose_rules"].values()))
    assert all(r.get("reviewed") is False for r in every_rule)


def test_spacing_rule_ingredients_all_exist_in_alias_table():
    keys = set(RULES["ingredient_aliases"])
    for r in RULES["spacing_rules"] + RULES["interval_unspecified_rules"]:
        assert r["a"] in keys and r["b"] in keys, r["id"]


# ---------------------------------------------------------------- ingredient matching

def test_brand_and_supplement_names_resolve():
    assert eng.resolve_ingredients("Tab Shelcal 500mg", RULES) == {"calcium"}
    assert eng.resolve_ingredients("Ciplox 500", RULES) == {"ciprofloxacin"}
    assert eng.resolve_ingredients("Iron", RULES) == {"iron"}
    assert eng.resolve_ingredients("Ferrous sulphate", RULES) == {"iron"}


def test_salt_names_are_not_mistaken_for_supplements():
    # "calcium" / "magnesium" here are salt forms of other drugs, not supplements.
    assert "calcium" not in eng.resolve_ingredients("Atorvastatin calcium 10 mg", RULES)
    assert eng.resolve_ingredients("Omeprazole Magnesium", RULES) == {"ppi"}
    assert "zinc" not in eng.resolve_ingredients("Zincovit multivitamin", RULES)


def test_unknown_medicine_resolves_to_nothing():
    assert eng.resolve_ingredients("Mystery Tablet", RULES) == set()


# ---------------------------------------------------------------- spacing conflicts

def test_levothyroxine_and_calcium_at_the_same_time_conflict_with_verified_source():
    levo = med(1, "Levothyroxine", [dose(at(8))])
    ca = med(2, "Calcium carbonate", [dose(at(8)), dose(at(20))])
    found = timing(eng.detect_schedule_conflicts([levo, ca], RULES, now=at(6)))
    assert len(found) == 1
    c = found[0]
    assert c["required_hours"] == 4 and c["order"] == "same_time"
    assert "4 hours apart" in c["source"]["quote"]
    assert c["source"]["set_id"] == "008de8fd-150f-4022-8ccb-c8bfa94875c7"
    assert c["reviewed"] is False and "pharmacist" in c["disclaimer"]


def test_four_hours_apart_is_fine_and_three_is_not():
    levo = med(1, "Levothyroxine", [dose(at(8))])
    assert not timing(eng.detect_schedule_conflicts([levo, med(2, "Calcium", [dose(at(12))])], RULES, at(6)))
    assert timing(eng.detect_schedule_conflicts([levo, med(2, "Calcium", [dose(at(11))])], RULES, at(6)))
    # the same gap in the other direction (calcium first)
    assert timing(eng.detect_schedule_conflicts([levo, med(2, "Calcium", [dose(at(5))])], RULES, at(4)))
    assert not timing(eng.detect_schedule_conflicts([levo, med(2, "Calcium", [dose(at(4))])], RULES, at(3)))


def test_ciprofloxacin_rule_is_asymmetric_2h_before_or_6h_after():
    cipro = lambda: med(1, "Ciprofloxacin", [dose(at(8))])  # noqa: E731
    # calcium 1h AFTER cipro: needs 2h
    c1 = timing(eng.detect_schedule_conflicts([cipro(), med(2, "Calcium", [dose(at(9))])], RULES, at(7)))
    assert len(c1) == 1 and c1[0]["required_hours"] == 2
    # calcium 2h after: fine
    assert not timing(eng.detect_schedule_conflicts([cipro(), med(2, "Calcium", [dose(at(10))])], RULES, at(7)))
    # calcium 3h BEFORE cipro: needs 6h
    c2 = timing(eng.detect_schedule_conflicts([cipro(), med(2, "Calcium", [dose(at(5))])], RULES, at(4)))
    assert len(c2) == 1 and c2[0]["required_hours"] == 6
    # calcium exactly 6h before: fine
    assert not timing(eng.detect_schedule_conflicts([cipro(), med(2, "Calcium", [dose(at(2))])], RULES, at(1)))


def test_levofloxacin_with_iron_needs_2h_either_way():
    levo = med(1, "Levofloxacin", [dose(at(9))])
    assert timing(eng.detect_schedule_conflicts([levo, med(2, "Ferrous sulfate", [dose(at(10))])], RULES, at(8)))
    assert timing(eng.detect_schedule_conflicts([levo, med(2, "Ferrous sulfate", [dose(at(8))])], RULES, at(7)))
    assert not timing(eng.detect_schedule_conflicts([levo, med(2, "Ferrous sulfate", [dose(at(11))])], RULES, at(8)))


def test_alendronate_needs_half_an_hour_before_any_other_medicine():
    alen = med(1, "Alendronate", [dose(at(7))])
    other = med(2, "Telmisartan", [dose(at(7, 15))])
    c = timing(eng.detect_schedule_conflicts([alen, other], RULES, at(6)))
    assert len(c) == 1 and c[0]["required_hours"] == 0.5
    assert not timing(eng.detect_schedule_conflicts([alen, med(2, "Telmisartan", [dose(at(7, 30))])], RULES, at(6)))
    # the label says nothing about another medicine taken BEFORE alendronate -> no claim made
    assert not timing(eng.detect_schedule_conflicts([alen, med(2, "Telmisartan", [dose(at(6, 50))])], RULES, at(6)))


def test_iron_and_calcium_have_no_verified_rule_so_nothing_is_claimed():
    out = eng.detect_schedule_conflicts(
        [med(1, "Iron", [dose(at(8))]), med(2, "Calcium", [dose(at(8))])], RULES, at(6))
    assert out == []   # silence = "not verified", never "safe" (the API adds that caveat)


def test_label_that_says_separate_but_gives_no_interval_needs_clarification():
    out = eng.detect_schedule_conflicts(
        [med(1, "Doxycycline", [dose(at(8))]), med(2, "Calcium", [dose(at(20))])], RULES, at(6))
    assert len(out) == 1
    c = out[0]
    assert c["kind"] == "interval_unspecified" and c["status"] == "needs_clarification"
    assert c["proposal"] is None and c["required_hours"] is None
    assert "does not give a spacing time" in c["message"]


def test_missed_and_skipped_doses_impose_no_timing():
    levo = med(1, "Levothyroxine", [dose(at(8))])
    for state in ("missed", "skipped"):
        ca = med(2, "Calcium", [dose(at(8), state=state)])
        assert not timing(eng.detect_schedule_conflicts([levo, ca], RULES, at(9)))


def test_a_taken_dose_counts_at_the_time_it_was_actually_taken():
    # Calcium was scheduled 08:00 but taken late at 11:00; levothyroxine due 12:00.
    ca = med(2, "Calcium", [dose(at(8), state="taken", acted=at(11))])
    levo = med(1, "Levothyroxine", [dose(at(12))])
    c = timing(eng.detect_schedule_conflicts([levo, ca], RULES, now=at(11, 30)))
    assert len(c) == 1 and c[0]["actual_hours"] == 1.0
    # taken on time (08:00) there is no conflict with the 12:00 dose
    ca_on_time = med(2, "Calcium", [dose(at(8), state="taken", acted=at(8))])
    assert not timing(eng.detect_schedule_conflicts([levo, ca_on_time], RULES, now=at(11, 30)))


def test_same_medicine_twice_within_an_hour_is_flagged_as_possible_duplicate():
    out = eng.detect_schedule_conflicts([med(1, "Telmisartan", [dose(at(8)), dose(at(8, 30))])], RULES, at(7))
    assert [c["kind"] for c in out] == ["duplicate_dose"]
    assert out[0]["status"] == "needs_clarification"


def test_two_medicines_with_the_same_ingredient_are_flagged():
    out = eng.detect_schedule_conflicts(
        [med(1, "Telmisartan", [dose(at(8))]), med(2, "Telma", [dose(at(20))])], RULES, at(7))
    assert [c["kind"] for c in out] == ["duplicate_ingredient"]


def test_doses_far_outside_the_window_are_ignored():
    levo = med(1, "Levothyroxine", [dose(at(8))])
    ca = med(2, "Calcium", [dose(at(8, day=10))])
    assert eng.detect_schedule_conflicts([levo, ca], RULES, at(6)) == []


# ---------------------------------------------------------------- proposals

def test_proposal_moves_the_later_dose_later_to_the_earliest_allowed_time():
    levo = med(1, "Levothyroxine", [dose(at(8))])
    ca_dose = dose(at(8))
    ca = med(2, "Calcium", [ca_dose, dose(at(20))])
    c = timing(eng.detect_schedule_conflicts([levo, ca], RULES, at(6)))[0]
    assert c["status"] == "proposal_available"
    p = c["proposal"]
    assert p["dose_id"] == ca_dose["id"] and p["medicine"] == "Calcium"
    assert datetime.fromisoformat(p["to"]) == at(12) and datetime.fromisoformat(p["to"]) > datetime.fromisoformat(p["from"])
    assert "Nothing changes unless you accept" in p["note"]


def test_no_proposal_when_the_move_would_reach_the_medicines_next_dose():
    levo = med(1, "Levothyroxine", [dose(at(8))])
    ca = med(2, "Calcium", [dose(at(8)), dose(at(11))])     # next calcium dose is before 12:00
    c = timing(eng.detect_schedule_conflicts([levo, ca], RULES, at(6)))[0]
    assert c["proposal"] is None and c["status"] == "needs_clarification"


def test_no_proposal_to_move_a_dose_that_was_already_taken():
    levo = med(1, "Levothyroxine", [dose(at(8))])
    ca = med(2, "Calcium", [dose(at(8), state="taken", acted=at(8))])
    c = timing(eng.detect_schedule_conflicts([levo, ca], RULES, now=at(9)))
    # levothyroxine (pending, in the future relative to now? 08:00 < 09:00) -> cannot be moved either
    assert all(x["proposal"] is None for x in c)


def test_high_risk_medicine_is_never_moved():
    # Warfarin is not in a spacing rule, so construct the case with a fluoroquinolone partner:
    # the mover would be the later dose; mark that medicine high-risk through its name.
    assert eng.high_risk_class("Insulin glargine", RULES) == "insulin"
    assert eng.high_risk_class("Warfarin 5mg", RULES) == "warfarin"
    assert eng.high_risk_class("Clopidogrel", RULES) == "antiplatelet"
    assert eng.high_risk_class("Paracetamol", RULES) is None


def test_proposal_is_refused_if_it_would_break_another_rule():
    levo = med(1, "Levothyroxine", [dose(at(8))])
    ca = med(2, "Calcium", [dose(at(8)), dose(at(23))])
    cipro_dose = dose(at(12))                     # exactly where calcium would land (08:00 + 4h)
    cipro = med(3, "Ciprofloxacin", [cipro_dose])
    conflicts = timing(eng.detect_schedule_conflicts([levo, ca, cipro], RULES, at(6)))
    levo_ca = next(c for c in conflicts if c["rule_id"] == "levothyroxine-calcium")
    assert levo_ca["proposal"] is None


def test_validate_reschedule_accepts_the_proposal_and_rejects_everything_else():
    levo = med(1, "Levothyroxine", [dose(at(8))])
    ca_dose = dose(at(8))
    ca = med(2, "Calcium", [ca_dose, dose(at(20))])
    meds = [levo, ca]
    ok, _ = eng.validate_reschedule(meds, RULES, at(6), ca_dose["id"], at(12))
    assert ok
    ok, why = eng.validate_reschedule(meds, RULES, at(6), ca_dose["id"], at(11))
    assert not ok and "earlier" in why
    ok, why = eng.validate_reschedule(meds, RULES, at(6), ca_dose["id"], at(20))
    assert not ok and "next dose" in why
    ok, _ = eng.validate_reschedule(meds, RULES, at(6), 999999, at(12))
    assert not ok


# ---------------------------------------------------------------- missed dose

def _guide(name, doses, missed_idx, now, others=None):
    m = med(1, name, doses)
    return eng.missed_dose_guidance(m, doses[missed_idx], RULES, now, others)


def test_ciprofloxacin_missed_dose_window_is_computed_from_the_label():
    missed = dose(at(8), state="missed")
    # next dose 20:00; now 09:00 -> 11h remain (>= 6) -> label allows taking it
    g = _guide("Ciprofloxacin", [missed, dose(at(20))], 0, at(9))
    assert g["action"] == "take_now_ok" and g["hours_since_due"] == 1.0 and g["hours_to_next"] == 11.0
    assert "6 hours" in g["label_quote"] and g["never_double"] is True
    # now 15:00 -> 5h remain (< 6) -> skip and continue
    g = _guide("Ciprofloxacin", [dose(at(8), state="missed"), dose(at(20))], 0, at(15))
    assert g["action"] == "skip_and_continue"
    # exactly 6h remain is allowed ("not later than 6 hours prior")
    g = _guide("Ciprofloxacin", [dose(at(8), state="missed"), dose(at(20))], 0, at(14))
    assert g["action"] == "take_now_ok"


def test_ciprofloxacin_with_no_next_dose_is_not_guessed():
    g = _guide("Ciprofloxacin", [dose(at(8), state="missed")], 0, at(9))
    assert g["action"] == "contact_provider" and g["next_dose_at"] is None


def test_missed_cipro_waits_for_the_gap_after_a_recent_calcium_dose():
    # Calcium taken 08:30; cipro (missed 08:00) now 09:30. Label: calcium before cipro needs 6h -> 14:30.
    ca = med(2, "Calcium", [dose(at(8, 30), state="taken", acted=at(8, 30))])
    cipro_doses = [dose(at(8), state="missed"), dose(at(23))]
    g = _guide("Ciprofloxacin", cipro_doses, 0, at(9, 30), [ca])
    assert g["action"] == "wait_until"
    assert datetime.fromisoformat(g["earliest_safe_time"]) == at(14, 30)
    assert g["spacing_notes"] and "6 hours" in g["spacing_notes"][0]


def test_missed_cipro_is_skipped_if_the_spacing_wait_would_pass_the_label_window():
    ca = med(2, "Calcium", [dose(at(8, 30), state="taken", acted=at(8, 30))])
    # earliest safe = 14:30; next dose 20:00 -> only 5.5h left (< 6) -> skip
    g = _guide("Ciprofloxacin", [dose(at(8), state="missed"), dose(at(20))], 0, at(9, 30), [ca])
    assert g["action"] == "skip_and_continue"


def test_metformin_label_says_skip_and_continue():
    g = _guide("Metformin", [dose(at(8), state="missed"), dose(at(20))], 0, at(10))
    assert g["action"] == "skip_and_continue" and "next dose as prescribed" in g["label_quote"]


def test_label_text_only_drugs_are_not_given_an_invented_time():
    for name in ("Amlodipine", "Telmisartan", "Atorvastatin", "Alendronate"):
        g = _guide(name, [dose(at(8), state="missed"), dose(at(20))], 0, at(10))
        assert g["action"] == "follow_label_text", name
        assert g["earliest_safe_time"] is None and g["label_quote"]


def test_high_risk_medicines_never_get_a_catch_up_time():
    for name in ("Warfarin", "Insulin glargine", "Apixaban", "Clopidogrel", "Digoxin"):
        g = _guide(name, [dose(at(8), state="missed"), dose(at(20))], 0, at(10))
        assert g["action"] == "contact_provider" and g["high_risk"] is True, name
        assert g["earliest_safe_time"] is None
        assert "catch up" in g["headline"].lower() and "double" in g["universal_warning"].lower()


def test_warfarin_shows_the_label_sentence_not_a_calculation():
    g = _guide("Warfarin", [dose(at(8), state="missed")], 0, at(10))
    assert g["label_quote"] == "If you miss a dose of warfarin sodium tablets, call your healthcare provider."


def test_unknown_medicine_gets_a_safe_default_and_no_made_up_quote():
    g = _guide("Mystery Tablet", [dose(at(8), state="missed"), dose(at(20))], 0, at(10))
    assert g["action"] == "contact_provider" and g["label_quote"] is None and g["source"] is None
    assert g["never_double"] is True


def test_every_missed_dose_result_carries_the_never_double_warning():
    for name in ("Ciprofloxacin", "Metformin", "Warfarin", "Mystery", "Amlodipine"):
        g = _guide(name, [dose(at(8), state="missed"), dose(at(20))], 0, at(10))
        assert g["never_double"] is True and "double" in g["universal_warning"].lower()


def test_missed_notification_text_has_next_dose_and_never_double():
    g = _guide("Metformin", [dose(at(8), state="missed"), dose(at(20))], 0, at(10))
    n = eng.build_missed_notification(g)
    assert "Don't take a double dose" in n["body"] and "20:00" in n["body"]
    g = _guide("Warfarin", [dose(at(8), state="missed"), dose(at(20))], 0, at(10))
    assert "doctor or pharmacist" in eng.build_missed_notification(g)["body"]


def test_rules_json_is_valid_and_loads_twice_identically():
    assert eng.load_rules() == json.loads(json.dumps(RULES))
