# Voice Assistant Implementation Plan

> **For agentic workers:** executed inline (superpowers:executing-plans), TDD per task.

**Goal:** A voice-first PWA page where Groq holds the conversation and SmartPoli's existing services (scheduler, prescription service, triage engine) perform every action and decide every severity.

**Architecture:** `voice_safety.py` (deterministic red flags) → `voice_assistant.py` (Groq tool-calling loop) → `voice_tools.py` (validated action router over existing services) ; `voice_fallback.py` when Groq is unavailable. One endpoint `POST /patients/{id}/voice/turn`. Frontend: standalone `static/voice.html` PWA.

**Tech Stack:** FastAPI, SQLAlchemy, groq 0.9 (tool calling), Web Speech API, speechSynthesis, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-09-28-voice-assistant-design.md`

## Global Constraints

- Groq never writes to the DB and never decides severity; severity = `triage.evaluate_check` escalated only by `voice_safety` red flags.
- Unmapped symptoms → `NOT_ASSESSED`, never saved as LOW.
- Every write goes through a router function that checks the patient owns the record.
- Tests never hit the network: Groq is replaced by a scripted fake (`monkeypatch.setattr("groq.Groq", Fake)`, as `test_llm_triage_assist.py` does).
- No `alert`/`confirm`/`prompt` in the UI. Commits without a Claude co-author trailer.

## Review Focus

1. Patient says "I took it" when two doses are due → the tool must not silently pick one; the prompt requires asking and the popup shows what was marked (Task 3).
2. Client-supplied `state` is tampered (answers for another symptom, fake prescription id) → router re-validates ids against the ruleset and ownership (Task 3/5).
3. Groq returns malformed tool arguments or an unknown tool name → the turn still returns a sensible reply, nothing written (Task 5).
4. Groq times out / key missing mid-conversation → fallback reply, red-flag scan still ran (Task 5).
5. Very long or empty text → 422, no Groq call (Task 5).

## Tasks

1. **Red flags** — `voice_safety.py`: `scan_red_flags(text) -> list[str]` (reasons); table-driven tests EN/Hinglish/Devanagari + near-misses.
2. **Shared confirm + dose undo** — move confirm logic to `prescription_service.confirm_prescription(db, prescription, actor)`; main uses it (existing tests stay green). New `POST /doses/{id}/undo` (taken ≤10 min ago → pending) with tests.
3. **Action router** — `voice_tools.py`: `TOOLS` (OpenAI-style JSON schemas) + `run_tool(db, patient_id, user, name, args, state) -> (result_dict, actions, state)`; every tool tested directly.
4. **Fallback intents** — `voice_fallback.py`: `fallback_turn(db, patient_id, user, text, lang, now) -> dict`; tests with no key.
5. **Turn loop + endpoint** — `voice_assistant.py`: `run_turn(...)`; `POST /patients/{id}/voice/turn`, `GET /voice/available`, rate limit, validation; fake-Groq tests incl. severity override attempt and bad tool calls.
6. **Login `next`** — login.js/auth.js honour same-site `/static/` `next`; JS unit check via node.
7. **PWA page** — voice.html/js/css, manifest, sw, icon; app mic button opens it; app honours `#tab=` so "open timeline" lands on the right tab.
8. **Manual check in Chrome**, then final review.
