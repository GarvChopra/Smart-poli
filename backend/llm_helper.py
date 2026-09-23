"""
SmartPoli — optional LLM assist for free-text symptom interpretation.

This is the ONE place an LLM is allowed near triage. It only proposes which
structured symptoms and boolean answers a sentence like "I have chest pain
and can't breathe" might map to — a SUGGESTION the user reviews before
anything is submitted. triage.py::evaluate_check is the only thing that
ever decides severity, deterministically, from the versioned rules table;
this module has no severity field in its output on purpose, and every
symptom_id/question_id it returns is validated against the real ruleset
before being trusted, in case the model hallucinates one that doesn't exist.

If GROQ_API_KEY is not set, this feature is simply unavailable — nothing
else in SmartPoli depends on it (CLAUDE.md section 14: runs with zero API
keys). The manual symptom-picker flow works identically with or without it.
"""

import json
import os
from typing import Optional


class LLMUnavailable(Exception):
    """Raised when there's no GROQ_API_KEY, or the call itself fails. Callers
    must fall back to the manual symptom picker — never block triage on this."""


def is_available() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def _build_catalog_prompt(ruleset: dict) -> str:
    lines = ["You map a patient's free-text description to a structured symptom checklist.",
             "Available symptoms and their yes/no follow-up questions:"]
    for sid, s in ruleset["symptoms"].items():
        qids = ", ".join(f'{q["id"]} ("{q["text"]}")' for q in s["questions"])
        lines.append(f'- {sid} ("{s["label"]}"): {qids}')
    lines.append(
        "Reply with ONLY a JSON object: "
        '{"symptom_ids": [...], "answers": {"<question_id>": true|false, ...}}. '
        "Only include a question_id if the text gives a clear yes/no signal for it — "
        "omit anything unclear rather than guessing. Never invent a symptom_id or "
        "question_id that isn't listed above. Never include a severity, diagnosis, "
        "or treatment — this is extraction only."
    )
    return "\n".join(lines)


def interpret_free_text(text: str, ruleset: dict) -> dict:
    """
    Returns {"symptom_ids": [...], "answers": {...}}. Both are validated
    against the real ruleset — any hallucinated id is silently dropped,
    never passed downstream.
    """
    if not is_available():
        raise LLMUnavailable("GROQ_API_KEY is not set.")

    try:
        from groq import Groq
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _build_catalog_prompt(ruleset)},
                {"role": "user", "content": text},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content)
    except Exception as e:
        raise LLMUnavailable(f"Groq call failed: {e}") from e

    valid_symptom_ids = [sid for sid in parsed.get("symptom_ids", []) if sid in ruleset["symptoms"]]

    answers = {}
    for sid in valid_symptom_ids:
        valid_qids = {q["id"] for q in ruleset["symptoms"][sid]["questions"]}
        for qid, val in (parsed.get("answers") or {}).items():
            if qid in valid_qids and isinstance(val, bool):
                answers[qid] = val

    return {"symptom_ids": valid_symptom_ids, "answers": answers}
