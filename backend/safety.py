"""
SmartPoli — Medication Safety Center (Part 3 of the brief).

Aggregates the EXISTING, already-deterministic engines — interactions.py
and food_warnings.py — into one response, plus one narrow addition: a
structural dosage sanity check. This module adds no second interaction or
food-warning engine, and the sanity check never asserts a clinical maximum
dose for a specific drug (that would be exactly the kind of invented
medical instruction CLAUDE.md section 13 forbids) — it only flags values
that are almost certainly a parsing mistake, for a human to check against
the original prescription.
"""

from db import Medicine
from interactions import normalize_for_interactions, check_interactions
from food_warnings import check_food_warnings

# A single recorded dose at or above this is far more likely to be an
# OCR/parsing slip (e.g. "500" misread as "5000") than a real instruction.
IMPLAUSIBLE_AMOUNT = 5000.0


def _dosage_sanity_checks(medicines: list[Medicine]) -> list[dict]:
    warnings = []
    for m in medicines:
        name = m.name or m.raw_text
        if m.dose_amount:
            try:
                amount = float(m.dose_amount)
            except (TypeError, ValueError):
                amount = None
            if amount is not None:
                if amount <= 0:
                    warnings.append({
                        "medicine_id": m.id, "medicine": name, "severity": "REVIEW",
                        "message": f"Recorded dose amount is {m.dose_amount}{m.dose_unit or ''} — check this line "
                                   "against the original prescription.",
                    })
                elif amount >= IMPLAUSIBLE_AMOUNT:
                    warnings.append({
                        "medicine_id": m.id, "medicine": name, "severity": "REVIEW",
                        "message": f"Recorded dose amount ({m.dose_amount}{m.dose_unit or ''}) looks unusually "
                                   "large — verify against the original prescription.",
                    })
        if m.dose_amount and not m.dose_unit:
            warnings.append({
                "medicine_id": m.id, "medicine": name, "severity": "INFO",
                "message": "No dose unit (mg/ml/tab) was recorded — verify against the original prescription.",
            })
    return warnings


def _brand_generic_map(medicines: list[Medicine]) -> list[dict]:
    out = []
    for m in medicines:
        if not m.name:
            continue
        brand_key = m.name.strip().lower()
        generic = normalize_for_interactions(m.name)
        out.append({
            "medicine_id": m.id,
            "name": m.name,
            "generic_name": generic if generic != brand_key else None,
        })
    return out


def build_safety_center(interaction_ruleset: dict, food_ruleset: dict, medicines: list[Medicine]) -> dict:
    active = [m for m in medicines if m.status != "needs_confirmation" and m.name]
    names = [m.name for m in active]
    return {
        "medicines": _brand_generic_map(active),
        "interactions": check_interactions(interaction_ruleset, names),
        "food_warnings": check_food_warnings(food_ruleset, names),
        "dosage_warnings": _dosage_sanity_checks(active),
        "disclaimer": "Deterministic, rule-based checks only — not a clinical review. "
                       "Always follow the original prescription and consult a doctor before changing it.",
    }
