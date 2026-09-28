import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from db import SessionLocal, User  # noqa: E402
from conftest import register_and_login  # noqa: E402
import clinical_knowledge  # noqa: E402
from voice_tools import ToolContext, run_tool  # noqa: E402

REQUIRED = {"source", "publisher", "document", "version", "url", "topic", "kind", "text"}


def test_every_indexed_item_keeps_its_provenance():
    items = clinical_knowledge.all_items()
    assert len(items) > 50
    for it in items:
        assert REQUIRED <= set(it), it.get("id")
        assert it["url"].startswith("https://"), it["id"]
        assert it["kind"] in ("overview", "prevention_counselling", "when_to_seek_care", "self_care")


def test_patient_items_never_contain_treatment():
    drug = re.compile(r"\b(mg|mcg|dose|dosage|tablets?|injection|inhaled|drugs?|medications?|antibiotics?|"
                      r"steroids?|LABA|SABA|ICS|nebuli\w*)\b", re.I)
    leaking = [it["id"] for it in clinical_knowledge.all_items()
               if it["source"] == "mohfw_stg" and drug.search(it["text"])]
    assert leaking == []


def test_search_finds_india_specific_guidance_for_a_known_condition():
    hits = clinical_knowledge.search("asthma wheezing breathing difficulty")
    assert hits
    assert any(h["source"] == "mohfw_stg" and "Asthma" in h["topic"] for h in hits)
    assert all(REQUIRED <= set(h) for h in hits)


def test_search_finds_patient_friendly_overview():
    hits = clinical_knowledge.search("cough")
    assert any(h["source"] == "medlineplus" and h["topic"] == "Cough" for h in hits)


def test_symptom_ids_bring_their_sourced_self_care():
    hits = clinical_knowledge.search("sir dard", symptom_ids=["headache"])
    care = [h for h in hits if h["kind"] == "self_care"]
    assert care and all(h["url"].startswith("https://") for h in care)


def test_nothing_relevant_returns_nothing():
    assert clinical_knowledge.search("") == []
    assert clinical_knowledge.search("zxqv blorp") == []


def test_groq_tool_returns_guidance_and_shows_sources():
    with TestClient(app) as client:
        user = register_and_login(client)
        pid = client.post("/patients", json={"name": "Knowledge Test"}).json()["id"]
    db = SessionLocal()
    try:
        ctx = ToolContext(db=db, patient_id=pid, user=db.query(User).get(user["id"]), now=datetime.now(),
                          lang="hi", state={})
        r = run_tool(ctx, "search_clinical_guidance", {"query": "copd breathlessness"})
    finally:
        db.close()
    assert r["ok"] is True and r["guidance"]
    assert "rules" in r["use"].lower()
    src = next(a for a in ctx.actions if a["type"] == "sources")
    assert src["items"] and all(s["url"].startswith("https://") for s in src["items"])


def test_disease_guidance_only_when_the_condition_is_asked_about_or_in_the_record():
    topics = [h["topic"] for h in clinical_knowledge.search("cough")]
    assert "Sarcoidosis" not in topics and "Pleural Diseases" not in topics
    assert "Cough" in topics
    asthma = clinical_knowledge.search("breathing trouble", conditions=["Asthma"])
    assert any(h["source"] == "mohfw_stg" and "Asthma" in h["topic"] for h in asthma)


def test_cough_has_sourced_self_care_without_medicines():
    care = [h for h in clinical_knowledge.search("cough") if h["kind"] == "self_care"]
    assert care and all("nhs.uk" in h["url"] for h in care)
    assert not any(re.search(r"paracetamol|ibuprofen|pelargonium", h["text"], re.I) for h in care)
