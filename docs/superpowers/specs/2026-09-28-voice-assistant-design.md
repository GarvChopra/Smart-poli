# Talk to SmartPoli — voice assistant design

Date: 2026-09-28
Status: draft, awaiting review

## Goal

A voice-first patient app. The patient opens SmartPoli, sees one big
"TALK TO SMARTPOLI" orb, and does everything by speaking — in Hindi, English
or Hinglish. The assistant answers on screen and out loud, performs real
actions in the app (the same actions the buttons perform), and confirms each
action with a popup. The screens remain for checking updates.

Example session:

> "Meri aaj ki medicines kya hain?" → "Aaj aapki 3 medicines hain…"
> "Maine subah wali le li." → "Done. Morning dose recorded." + popup
> ✓ Metformin 500 mg — Taken 8:12 AM
> "Ab mujhe kya lena hai?" → "Aapki next medicine Metformin 500 mg hai, 8:30 PM par."
> "Mere sar mein bahut dard hai." → symptom mode (below)

## Decisions already agreed

- Languages: Hindi, English and mixed Hinglish; reply in the language spoken.
- The brain is Groq (LLM). It is not limited to the rules table — it can
  hold a symptom conversation about any symptom.
- A safety layer runs alongside it and can only raise severity. On
  EMERGENCY the conversation is interrupted by a persistent emergency panel.
- The orb screen becomes the patient home screen; existing screens stay
  reachable from the menu.
- Health-app integrations and the rest of the health-card roadmap are
  separate projects, not part of this one.

## Architecture

```
Browser (patient app)                     Backend (FastAPI)
─────────────────────                     ────────────────────────────────
Mic → Web Speech API (hi-IN / en-IN)
   → transcript ───────────────►  POST /patients/{id}/voice/turn
                                        │
                                        ├─ red-flag scan (deterministic)
                                        ├─ Groq chat with tools
                                        │     tools call existing modules
                                        │     (scheduler, prescription_service,
                                        │      serializers, triage, food/interactions)
                                        ├─ safety merge (rules + red flags + LLM,
                                        │   escalate-only)
                                        ▼
   ◄───── {reply, lang, actions[], state} 
Render reply text, speak it (speechSynthesis),
run actions (popup / navigate / emergency panel)
```

The server is stateless between turns: the client sends the recent message
history (capped at the last 20 messages) plus an opaque `state` object the
server returned last time (current mode, symptom answers collected so far).

### New backend units

- **`voice_assistant.py`** — the conversation loop. Builds the system prompt
  (patient's name, the current local date/time sent by the browser with
  each turn as an ISO timestamp with UTC offset, language
  rule, safety wording rules), calls Groq with tool definitions, executes
  tool calls (max 5 tool rounds per turn), returns reply + actions.
- **`voice_tools.py`** — one plain function per tool. Each takes `(db,
  patient_id, user, **args)`, validates ownership, calls the existing
  module, and returns JSON for the model plus an optional UI action. This
  keeps every tool testable without the LLM.
- **`voice_safety.py`** — deterministic red-flag scanner and the severity
  merge (details under *Symptom mode*).
- **`voice_fallback.py`** — a keyword intent matcher for when
  `GROQ_API_KEY` is unset or Groq fails (see *Degraded mode*).
- **Route** in `main.py`: `POST /patients/{patient_id}/voice/turn`, patient
  write access (same dependency the dose endpoints use), plus
  `GET /voice/available`.

Model: env `GROQ_VOICE_MODEL`, default a Groq model that supports tool
calling (e.g. `llama-3.3-70b-versatile`). The existing `GROQ_MODEL` for
triage extraction is unchanged.

### Tools

| tool | what it does | reuses | UI action |
|---|---|---|---|
| `get_today_schedule` | today's doses with state (pending/taken/missed) | dashboard query | — |
| `get_next_dose` | next pending dose after now | dashboard query | — |
| `mark_dose_taken(dose_id)` | marks taken, returns the next dose | `mark_taken` + audit log, same as `POST /doses/{id}/take` | `dose_taken` popup |
| `log_prn_taken(medicine_id)` | logs an SOS/PRN use | same as `/log-prn` | `prn_logged` popup |
| `get_medicines` | current medicines, dose, timing, food instruction, plain-language text | serializers | — |
| `get_adherence_summary` | taken/missed counts, today and last 7 days | dashboard data | — |
| `get_food_and_interaction_warnings` | food warnings and drug interactions | `food_warnings`, `interactions` | — |
| `get_emergency_card` | allergies, contact, blood group | `emergency_card_data` | — |
| `open_screen(screen)` | switches tab: dashboard, prescriptions, safety, triage, report, timeline, emergency, settings | — | `navigate` |
| `decode_prescription(text)` | parses prescription text into a **draft** | `prescription_service.create_prescription_from_lines` | `navigate` to prescriptions + show decoded draft |
| `confirm_prescription(prescription_id)` | schedules a draft | same as `POST /prescriptions/{id}/confirm` | `prescription_confirmed` popup |
| `record_symptom_findings(...)` | see Symptom mode | `triage` | possibly `emergency` |

Rules for actions:
- **Dose matching.** "Maine subah wali le li" → the model calls
  `get_today_schedule` and picks the dose. `mark_dose_taken` only accepts a
  pending dose belonging to this patient, scheduled between 12 h before and
  2 h after now. Otherwise it returns an error the model must relay. If more
  than one dose fits ("I took my medicine" at 9 AM with two morning pills),
  the model must ask which one. This is a prompt rule: `get_today_schedule`
  marks which doses are "due now" so the model can see there are several.
  If exactly one fits, it marks it directly, as in the example. Every
  marked dose is shown in the popup, so a wrong match is visible at once.
  The popup has an **Undo** button backed by a new
  `POST /doses/{id}/undo` (same write access; only for a dose marked taken
  in the last 10 minutes; returns it to pending, with an audit entry). The
  app has no undo today, so this is new, and it's needed because voice
  matching can pick the wrong dose.
- **Scheduling a prescription always needs an explicit "yes"** from the
  patient in a later turn (it creates many doses). `confirm_prescription`
  only runs if the previous assistant turn asked for confirmation of that
  same prescription id (tracked in `state`).
- The model never invents data: every fact about medicines/doses must come
  from a tool result in the same turn.

### Symptom mode (the safety pipeline)

```
VOICE → SYMPTOM UNDERSTANDING → FOLLOW-UP QUESTIONS → RULES / SAFETY CHECK → SEVERITY → NEXT ACTION
          (Groq)                  (Groq, doctor-like)   (voice_safety + triage.py)
```

1. **Understanding.** When the patient reports a symptom, the model calls
   `record_symptom_findings` with the free-text symptom(s), any rule
   `symptom_id`s it maps to (validated against `triage_rules.json`; unknown
   ids dropped, as in `llm_helper.py`) and any answered rule `question_id`s.
2. **Follow-up questions.** Groq asks one question at a time, naturally,
   like a doctor (onset, severity, duration, associated symptoms, what
   medicines were taken). For symptoms covered by the rules, the tool
   returns the still-unanswered red-flag questions (`triage.next_question`)
   and the model **must** cover them before finishing — phrased
   conversationally, in the patient's language (`text_hi` exists). For
   other symptoms (cough, back pain, …) Groq chooses the questions itself.
3. **Safety check — runs on every turn, not only at the end:**
   - **Red-flag scanner** (`voice_safety.py`): a deterministic phrase list
     in English, Hinglish (Latin script) and Devanagari over the patient's
     raw transcript — e.g. can't breathe / saans nahi aa rahi / साँस नहीं,
     unconscious / behosh, seizure / daura / mirgi, chest pain with sweating,
     face drooping / slurred speech, heavy bleeding, suicidal thoughts /
     khud ko nuksan / आत्महत्या, overdose / "zyada goliyan kha li". A hit
     means EMERGENCY immediately, without waiting for the model.
   - **Rules engine:** `triage.evaluate_check` over the mapped rule
     symptoms and answers.
   - **Model's assessment:** the model states LOW / MODERATE / EMERGENCY
     with its reasons.
   - **Final severity** = `escalate(red_flags, rules, model)` — the highest
     of the three. Nothing can lower a rules or red-flag result.
4. **Next action.**
   - LOW / MODERATE: the model gives what to do now, following wording
     rules in the prompt: no diagnosis ("this could be…", "discuss with
     your doctor"), never tell the patient to start, stop or change a
     prescribed medicine, and mention relevant current medicines/food
     warnings from tools as *context*, not cause. MODERATE always includes
     "contact your doctor today".
   - EMERGENCY: the server returns an `emergency` action. The client stops
     speech recognition and TTS mid-sentence and shows a **persistent red
     panel** that stays until the patient dismisses it: *Call 112*, *Call
     my doctor / emergency contact* (from the emergency card), *Show
     emergency card*, with the reasons listed. The spoken reply is a short
     fixed sentence in the patient's language, not LLM text.
5. **Record.** The finished check (or any EMERGENCY) is saved as a
   `SymptomCheck` row — `symptoms` = rule ids plus free-text labels,
   `answers`, final `severity`, `reasons` (tagged `[rules]`, `[red flag]`
   or `[assistant]`), `ruleset_version` = `"<version>+voice"` — so the
   doctor dashboard, care report and emergency-history flag all see it,
   exactly like a manual check.

### Frontend

- **`static/voice.js`** (new) plus styles in `style.css`; `index.html` adds
  a `voice` view and makes it the default tab for patients.
- **Orb screen:** large pulsing orb labelled "TALK TO SMARTPOLI" / "What do
  you need?"; tap to listen, tap again to stop (it also auto-stops after
  silence). The conversation shows as transcript bubbles under it, with a
  text box for typing as an alternative. Suggestion chips: *Today's
  medicines*, *I took my dose*, *I'm not feeling well*.
- **Speech-to-text:** `SpeechRecognition` / `webkitSpeechRecognition`. A
  language toggle (हिं / EN, default from the app's existing i18n setting)
  sets `hi-IN` or `en-IN`; `hi-IN` handles Hinglish. Browsers without it
  (Firefox) get typing only, with a one-line note.
- **Text-to-speech:** `speechSynthesis` with a `hi-IN` or `en-IN` voice
  matching the reply's `lang`; a mute toggle.
- **Actions:** `dose_taken` / `prn_logged` / `prescription_confirmed` show
  the existing taken popup (✓ Metformin 500 mg — Taken 8:12 AM) and refresh
  cached dashboard data; `navigate` clicks the matching `data-tab` after the
  reply is spoken; `emergency` shows the persistent panel described above.
- Popups and the panel are in-page elements, never `alert`/`confirm`.
- Home: patients land on the orb; a "Show dashboard" link sits under it.

### Degraded mode (no Groq key or Groq down)

`voice_fallback.py` handles a small set of intents with keyword matching
(EN + Hinglish + Devanagari): *took my medicine* (marks the single
unambiguous current dose, or lists candidates as tap-to-mark chips),
*next dose*, *today's medicines*, *open <screen>*. Any symptom phrase →
the red-flag scan still runs; if there's no red flag it replies "Let's use
the symptom check" and opens the existing manual symptom-check tab.
The rest of the app is unaffected (the codebase's "runs with zero API
keys" rule).

### Privacy and limits

- Transcripts are sent to Groq (already true for free-text triage); the
  settings screen and the orb's first-use hint say so.
- Only the patient's own data is ever put in the prompt, fetched through
  tools scoped to the `patient_id` the user has write access to.
- Voice turns that change data write the same audit log entries as the
  buttons (actor `patient:{id}`, action suffixed `_via_voice`).
- The server rate-limits `/voice/turn` per user (e.g. 30 turns/minute),
  and each user message is capped at 1,000 characters.

## Testing

Backend (pytest, Groq replaced by a scripted fake client that returns
predetermined tool calls/replies — no network in tests):
- Each tool in `voice_tools.py` directly: correct data, ownership enforced
  (another patient's dose → error), dose window enforced, already-taken
  dose refused, PRN logging.
- `mark_dose_taken` via a turn writes the same dose state and audit entry
  as `POST /doses/{id}/take`.
- `confirm_prescription` refused without a prior confirmation question.
- Red-flag scanner: a table of EN / Hinglish / Devanagari phrases → EMERGENCY,
  plus near-miss phrases that must not trigger (e.g. "no chest pain").
- Severity merge: model says LOW + rules say EMERGENCY → EMERGENCY; model
  says EMERGENCY + rules LOW → EMERGENCY (never lowered either way).
- Headache conversation: unanswered rule red-flag questions are returned
  to the model until covered.
- Finished check is saved as a `SymptomCheck` and appears in the
  emergency-card history flag.
- Fallback mode with `GROQ_API_KEY` unset: "maine dawai le li", "next dose",
  "open report", and a red-flag phrase all behave as specified.

Frontend: manual run in Chrome — orb, Hindi and English speech, the taken
popup, navigation, and the emergency panel triggered by a red-flag phrase.
