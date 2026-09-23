"""
SmartPoli — optional Feature D: drug interaction detection.

Not part of the four mandatory PS4 features — this is an addition on top of
them (CLAUDE.md section 4 lists it as optional; the mandatory features must
never be weakened to make room for it).

Deterministic, table-driven, same philosophy as triage.py: a versioned local
JSON table is the primary source of truth and needs no network or API key,
so it works identically online or offline. An RxNorm lookup (a free,
keyless NIH API) is offered as an OPTIONAL enrichment on top — never the
only source, and never allowed to block or fail the primary check, the same
"deterministic fallback so the app runs with zero external dependency"
instinct behind the manual prescription path.

Studied against github.com/alv1n25/Grassroots-Hackathon-Dr.Nudge's
drugService.js (RxNorm + OpenFDA interaction check with a local fallback
table) and reimplemented independently — that repo has no LICENSE file, so
its code was not copied, only the pattern.

Never a diagnosis: severities are CRITICAL / MODERATE / MINOR, each with a
plain-language mechanism, not a treatment recommendation.
"""

import itertools
import json
import os
from typing import Optional

SEVERITY_ORDER = ["MINOR", "MODERATE", "CRITICAL"]

_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "interaction_rules.json")

# Brand/local names (as seen in indian_drugs.py and real prescriptions) mapped
# to the generic ingredient name the interaction table is keyed on. Only
# covers names that appear in interaction_rules.json — extend both together.
BRAND_TO_GENERIC = {
    "dolo": "paracetamol", "dolo 650": "paracetamol", "dolo 650 mg": "paracetamol",
    "crocin": "paracetamol", "crocin advance": "paracetamol", "calpol": "paracetamol",
    "calpol 500": "paracetamol", "pacimol": "paracetamol", "paracip": "paracetamol",
    "telma": "telmisartan",
    "glycomet": "metformin",
    "ecosprin": "aspirin", "ecospirin": "aspirin", "asprin gr": "aspirin", "disprin": "aspirin",
    "clopilet": "clopidogrel",
    "brufen": "ibuprofen",
    "storvas": "atorvastatin", "atorva": "atorvastatin",
    "restyl": "alprazolam", "restyl md": "alprazolam", "etilaam": "alprazolam",
    "stamlo": "amlodipine",
    "lasix": "furosemide",
}


def load_ruleset(path: Optional[str] = None) -> dict:
    with open(path or _RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _rank(severity: str) -> int:
    return SEVERITY_ORDER.index(severity)


def normalize_for_interactions(name: str) -> str:
    n = (name or "").strip().lower()
    return BRAND_TO_GENERIC.get(n, n)


def _build_lookup(ruleset: dict) -> dict:
    lookup = {}
    for pair in ruleset["pairs"]:
        key = frozenset({pair["a"], pair["b"]})
        lookup[key] = pair
    return lookup


def check_interactions(ruleset: dict, drug_names: list[str]) -> list[dict]:
    """
    Pairwise-check a patient's active medicines against the local table.
    Never invents an interaction: silence means "no known interaction in
    this table", not "verified safe" — the same raw-text-preserving honesty
    as the confidence gate elsewhere in this codebase.
    """
    lookup = _build_lookup(ruleset)
    normalized = sorted({normalize_for_interactions(n) for n in drug_names if n})

    found = []
    for a, b in itertools.combinations(normalized, 2):
        pair = lookup.get(frozenset({a, b}))
        if pair:
            found.append({
                "drug_a": a, "drug_b": b,
                "severity": pair["severity"],
                "description": pair["description"],
            })

    found.sort(key=lambda p: _rank(p["severity"]), reverse=True)
    return found


def fetch_rxnorm_enrichment(drug_names: list[str], timeout_seconds: float = 2.5) -> Optional[list[dict]]:
    """
    Optional online enrichment via RxNorm's public interaction API (no key
    needed). Returns None on ANY failure — no network, DNS down, slow judge
    wifi, RxNorm rate limit — so it can never break the primary, offline
    check above. Not called anywhere by default; a caller must opt in.
    """
    try:
        import httpx
    except ImportError:
        return None

    try:
        results = []
        with httpx.Client(timeout=timeout_seconds) as client:
            rxcuis = []
            for name in drug_names:
                r = client.get("https://rxnav.nlm.nih.gov/REST/rxcui.json", params={"name": name})
                r.raise_for_status()
                ids = r.json().get("idGroup", {}).get("rxnormId") or []
                if ids:
                    rxcuis.append(ids[0])

            if len(rxcuis) < 2:
                return []

            r = client.get(
                "https://rxnav.nlm.nih.gov/REST/interaction/list.json",
                params={"rxcuis": rxcuis[:2].__str__()[1:-1].replace("'", "")},
            )
            r.raise_for_status()
            groups = r.json().get("fullInteractionTypeGroup") or []
            for group in groups:
                for itype in group.get("fullInteractionType", []):
                    for pair in itype.get("interactionPair", []):
                        results.append({"description": pair.get("description", "")})
        return results
    except Exception:
        return None
