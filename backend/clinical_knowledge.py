"""
SmartPoli — clinical knowledge retrieval (the voice assistant's knowledge layer).

Groq is NOT the medical knowledge base. Before explaining anything medical,
the assistant retrieves approved items from here and explains only those:

  - MoHFW / DGHS Standard Treatment Guidelines (India) — recognition,
    prevention & counselling, and referral criteria; drug/treatment text is
    never indexed (knowledge/ingest.py)
  - MedlinePlus.gov health topics — patient-friendly overviews and when to
    get care (knowledge/ingest.py)
  - self_care.json — short sourced self-care steps (NHS, Asthma + Lung UK,
    BHF, Cambridge University Hospitals)

Every item keeps source, publisher, document, version/date, url, topic,
kind and text. Retrieval is a small local BM25 over the committed
knowledge/index.json — no network calls during a conversation. The
patient's own prescription and doctor's instructions come first (they are
not in here); the triage rules still decide severity.
"""

import json
import math
import os
import re
from collections import Counter
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(HERE, "knowledge", "index.json")
SELF_CARE_PATH = os.path.join(HERE, "self_care.json")

# the rule symptoms' plain-English search words
SYMPTOM_TERMS = {
    "headache": "headache head pain migraine",
    "fever": "fever high temperature",
    "breathlessness": "shortness of breath breathlessness breathing difficulty asthma copd wheezing",
    "chest_pain": "chest pain",
    "vomiting": "vomiting nausea",
    "nausea": "nausea vomiting",
    "rash": "rash skin itching",
    "dizziness": "dizziness vertigo",
    "abdominal_pain": "abdominal pain stomach",
    "bleeding": "bleeding",
}

_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "for", "with", "on", "at", "be", "as",
         "it", "this", "that", "by", "from", "can", "may", "your", "you", "if", "not", "have", "has", "was"}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP and len(t) > 1]


@lru_cache(maxsize=1)
def _index() -> dict:
    with open(INDEX_PATH, encoding="utf-8") as f:
        items = json.load(f)["items"]
    with open(SELF_CARE_PATH, encoding="utf-8") as f:
        care = json.load(f)
    for sid, entry in care["symptoms"].items():
        for n, item in enumerate(entry["items"]):
            src = care["sources"][item["source"]]
            items.append({
                "id": f"sc_{sid}_{n}", "source": item["source"], "publisher": src["title"].split(" - ")[0],
                "document": src["title"], "version": f"self_care.json {care['version']}", "url": src["url"],
                "topic": sid.replace("_", " "), "symptom_id": sid, "kind": "self_care", "audience": "patient",
                "text": item["en"], "text_hi": item.get("hi"), "levels": item["levels"],
            })
    docs = [_tokens(it["topic"] + " " + it["topic"] + " " + it["text"]) for it in items]
    df = Counter(t for d in docs for t in set(d))
    avg = sum(len(d) for d in docs) / max(len(docs), 1)
    return {"items": items, "docs": [Counter(d) for d in docs], "lens": [len(d) for d in docs],
            "df": df, "n": len(docs), "avg": avg}


def all_items() -> list[dict]:
    return list(_index()["items"])


def _bm25(query_tokens: list[str], i: int, idx: dict, k1: float = 1.4, b: float = 0.75) -> float:
    score, tf, dl = 0.0, idx["docs"][i], idx["lens"][i]
    for t in query_tokens:
        if t not in tf:
            continue
        idf = math.log(1 + (idx["n"] - idx["df"][t] + 0.5) / (idx["df"][t] + 0.5))
        score += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * dl / idx["avg"]))
    return score


def search(query: str, symptom_ids: list[str] | None = None, limit: int = 6) -> list[dict]:
    """Most relevant approved items for this query (plain English) and rule symptoms."""
    symptom_ids = [s for s in (symptom_ids or []) if s in SYMPTOM_TERMS]
    q = _tokens(query + " " + " ".join(SYMPTOM_TERMS[s] for s in symptom_ids))
    if not q:
        return []
    idx = _index()
    scored = sorted(((_bm25(q, i, idx), i) for i in range(idx["n"])), reverse=True)
    top = scored[0][0] if scored else 0
    if top < 1.0:
        return []
    hits = [dict(idx["items"][i]) for s, i in scored if s >= max(1.0, top * 0.35)][:limit]
    # the symptom's own sourced self-care steps always come along
    for sid in symptom_ids:
        for it in idx["items"]:
            if it.get("symptom_id") == sid and it["id"] not in {h["id"] for h in hits}:
                hits.append(dict(it))
    return hits
