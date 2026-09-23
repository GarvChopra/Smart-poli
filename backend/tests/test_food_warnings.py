import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from food_warnings import load_ruleset, check_food_warnings  # noqa: E402

RULESET = load_ruleset()


def test_brand_name_resolves_to_generic_warning():
    found = check_food_warnings(RULESET, ["Dolo"])  # paracetamol
    assert any(w["drug"] == "paracetamol" for w in found)


def test_known_critical_warning_is_found():
    found = check_food_warnings(RULESET, ["Metronidazole"])
    assert len(found) == 1
    assert found[0]["severity"] == "CRITICAL"
    assert "alcohol" in found[0]["avoid"]


def test_drug_with_no_warning_returns_empty():
    assert check_food_warnings(RULESET, ["Amoxicillin"]) == []


def test_multiple_drugs_each_checked_independently():
    found = check_food_warnings(RULESET, ["Metronidazole", "Simvastatin"])
    drugs = {w["drug"] for w in found}
    assert drugs == {"metronidazole", "simvastatin"}


def test_results_sorted_most_severe_first():
    found = check_food_warnings(RULESET, ["Metronidazole", "Simvastatin", "Iron"])
    ranks = ["MINOR", "MODERATE", "CRITICAL"]
    ranked = [ranks.index(w["severity"]) for w in found]
    assert ranked == sorted(ranked, reverse=True)


def test_no_diagnosis_language():
    found = check_food_warnings(RULESET, ["Metronidazole"])
    for w in found:
        text = w["description"].lower()
        assert "diagnosis" not in text and "you have" not in text
