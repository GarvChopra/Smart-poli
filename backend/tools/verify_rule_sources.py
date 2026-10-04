"""
Re-check every quote in safety_rules.json against the live FDA label.

    python tools/verify_rule_sources.py

For each unique source (set_id) it fetches the label from openFDA and
confirms the quoted sentence still appears, word for word (whitespace
normalised), in the named section. A label that was revised, withdrawn or
re-worded shows up here as a FAIL - that rule must be re-read by a person
before it is used again. Exit code 1 if anything fails.

This only proves 'the quote is still in the label'. It does NOT prove the
rule is clinically appropriate - see the review_status in safety_rules.json.
"""

import json
import os
import re
import sys

import httpx

RULES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "safety_rules.json")
API = "https://api.fda.gov/drug/label.json"


def norm(s: str) -> str:
    s = s.replace("\u2019", "'").replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def collect_sources(node, out):
    if isinstance(node, dict):
        if "set_id" in node and "quote" in node:
            out.append(node)
        for v in node.values():
            collect_sources(v, out)
    elif isinstance(node, list):
        for v in node:
            collect_sources(v, out)


def main() -> int:
    with open(RULES, "r", encoding="utf-8") as f:
        rules = json.load(f)
    sources = []
    collect_sources(rules, sources)

    labels = {}
    failures = 0
    for src in sources:
        set_id = src["set_id"]
        if set_id not in labels:
            r = httpx.get(API, params={"search": f'set_id:"{set_id}"', "limit": 1}, timeout=40)
            labels[set_id] = r.json()["results"][0] if r.status_code == 200 and r.json().get("results") else None
        label = labels[set_id]
        if label is None:
            print(f"FAIL  label not found: {set_id}  ({src['title']})")
            failures += 1
            continue
        field = src["section"].split()[0]
        text = norm(" ".join(label.get(field, []) or []))
        if label.get("effective_time") and label["effective_time"] != src["effective"].replace("-", ""):
            print(f"WARN  {set_id}: label now effective {label['effective_time']} (rule recorded {src['effective']})")
        for key in ("quote", "context_quote"):
            if key in src and norm(src[key]) not in text:
                print(f"FAIL  {key} not found in {field} of {set_id}: {src[key][:90]!r}")
                failures += 1
                break
        else:
            print(f"ok    {set_id[:8]} {field:28} {src['quote'][:70]!r}")
    print(f"\n{len(sources)} sources checked, {failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
