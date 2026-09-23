import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interactions import load_ruleset, check_interactions, normalize_for_interactions  # noqa: E402

RULESET = load_ruleset()


def test_brand_names_normalize_to_generic():
    assert normalize_for_interactions("Dolo") == "paracetamol"
    assert normalize_for_interactions("Telma") == "telmisartan"
    assert normalize_for_interactions("Glycomet") == "metformin"


def test_known_critical_pair_is_found():
    found = check_interactions(RULESET, ["Warfarin", "Aspirin"])
    assert len(found) == 1
    assert found[0]["severity"] == "CRITICAL"


def test_brand_name_resolves_to_the_same_interaction_as_its_generic():
    found = check_interactions(RULESET, ["Dolo", "Warfarin"])
    assert len(found) == 1
    assert found[0]["severity"] == "MODERATE"


def test_unrelated_drugs_produce_no_interaction():
    found = check_interactions(RULESET, ["Telmisartan", "Metformin"])
    assert found == []


def test_multiple_medicines_check_every_pair():
    # Warfarin+Aspirin (CRITICAL), Aspirin+Ibuprofen (MODERATE) — both should surface.
    found = check_interactions(RULESET, ["Warfarin", "Aspirin", "Ibuprofen"])
    severities = {f["severity"] for f in found}
    assert "CRITICAL" in severities
    assert "MODERATE" in severities


def test_results_sorted_most_severe_first():
    found = check_interactions(RULESET, ["Warfarin", "Aspirin", "Ibuprofen"])
    ranks = ["MINOR", "MODERATE", "CRITICAL"]
    ranked = [ranks.index(f["severity"]) for f in found]
    assert ranked == sorted(ranked, reverse=True)


def test_single_medicine_has_no_pairs_to_check():
    assert check_interactions(RULESET, ["Paracetamol"]) == []


def test_no_diagnosis_or_treatment_language():
    found = check_interactions(RULESET, ["Warfarin", "Aspirin"])
    forbidden = ["you have", "diagnosis", "prescribe", "stop taking"]
    for f in found:
        text = f["description"].lower()
        assert not any(word in text for word in forbidden)
