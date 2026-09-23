"""
SmartPoli — optional Feature E: food / medicine warnings.

Same philosophy as interactions.py and triage.py: a small versioned local
JSON table, single-drug lookups (not pairwise), deterministic, no network,
no invented advice. This is distinct from the shorthand engine's `food`
field (before/after meals timing) — this is specific substances to avoid
altogether (grapefruit, dairy, alcohol), not meal timing.
"""

import json
import os
from typing import Optional

from interactions import normalize_for_interactions

SEVERITY_ORDER = ["MINOR", "MODERATE", "CRITICAL"]

_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "food_warnings.json")


def load_ruleset(path: Optional[str] = None) -> dict:
    with open(path or _RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _rank(severity: str) -> int:
    return SEVERITY_ORDER.index(severity)


def check_food_warnings(ruleset: dict, drug_names: list[str]) -> list[dict]:
    """One lookup per medicine — no pairing needed, unlike drug interactions."""
    normalized = {normalize_for_interactions(n) for n in drug_names if n}
    found = [w for w in ruleset["warnings"] if w["drug"] in normalized]
    found.sort(key=lambda w: _rank(w["severity"]), reverse=True)
    return [
        {"drug": w["drug"], "avoid": w["avoid"], "severity": w["severity"], "description": w["description"]}
        for w in found
    ]
