# Talk to SmartPoli — voice assistant design

Date: 2026-09-28 (revised the same day with the user's final direction)
Status: approved direction; details below are rulings made on the user's behalf.

## Goal

A voice-first page any patient can open on their phone (installable as a
PWA) and just talk to — Hindi, English or Hinglish. No dashboard, no menus:
a large microphone, live transcription, SmartPoli's reply on screen and
spoken aloud. The assistant performs real actions through SmartPoli's
existing services and conducts symptom checks conversationally.

> "Maine apni BP ki dawai le li." → dose marked taken, popup, next dose told
> "Meri agli medicine kab hai?" → next dose from the real schedule
> "Mere sir mein bahut dard ho raha hai." → conversational symptom check →
> existing triage engine decides LOW / MODERATE / EMERGENCY → explained

## The one rule that shapes everything

**Groq is the conversational layer. SmartPoli's existing backend is the
decision and safety layer.**

```
Voice → Speech-to-Text (browser) → Groq (understanding + conversation)
      → Action router (voice_tools.py: validated, permission-checked)
      → Existing SmartPoli services (scheduler, prescription_service,
        serializers, triage.py) → Database
      → Response → text on screen + spoken aloud
```

- Groq never writes to the database. It can only *request* a tool; the
  router validates arguments, checks the patient owns the record, and calls
  the same functions the app's buttons call.
- Groq never decides severity. Severity comes only from `triage.py`
  (`evaluate_check` over the existing `triage_rules.json`) plus a
  deterministic red-flag phrase check (below). Groq explains the result in
  simple language; its text cannot change it.

## Symptom checks

1. **Intake (Groq).** The patient describes a symptom in their own words.
   Groq asks natural follow-up questions one at a time ("Ye kab se ho raha
   hai?"), in the patient's language.
2. **Structuring (router).** Groq calls `update_symptom_check` with the
   symptoms mapped to rule ids (validated against `triage_rules.json`;
   unknown ids dropped, as `llm_helper.py` already does), free-text labels,
   and yes/no answers to rule question ids. The router returns the rule
   questions still unanswered (`triage.next_question`) with their English
   and Hindi text; Groq must work these into the conversation. Groq may ask
   extra context questions (onset, duration) of its own.
3. **Decision (triage.py).** Groq calls `finish_symptom_check`. The router
   refuses while rule questions remain unanswered (unless severity is
   already EMERGENCY, matching `next_question`'s own rule). It then runs
   `evaluate_check` and saves a `SymptomCheck` exactly like
   `POST /triage/check` (same fields, audit entry `triage_check`), so the
   doctor dashboard, care report and emergency card all see it.
4. **Explanation (Groq).** The router returns severity, reasons and the
   localized action; Groq explains them simply. The screen shows the
   rule-engine result verbatim as a result card, independent of Groq's
   wording.
5. **Symptoms the rules don't cover** (cough, back pain…): the rule engine
   cannot rate them, so the result is `NOT_ASSESSED` — never a guessed
   severity. The assistant says SmartPoli can't rate this symptom
   automatically and to contact a doctor if it is severe or getting worse;
   the check is not saved as a LOW result.
6. **Red flags (deterministic, every turn).** `voice_safety.py` scans the
   patient's raw words (English, Hinglish, Devanagari) for emergency phrases
   — can't breathe / saans nahi aa rahi / साँस नहीं, unconscious / behosh,
   seizure / daura, chest pain with sweating, face drooping / slurred speech,
   heavy bleeding, suicidal thoughts / khudkushi / आत्महत्या, overdose. A hit
   returns EMERGENCY immediately, before any Groq call. This is a fixed
   list in code, not an LLM judgement, and it can only raise severity.
7. **EMERGENCY** (from rules or red flags): the page stops listening and
   speaking and shows a persistent red panel — Call 112, Call my emergency
   contact, Show my emergency card — with the reasons, until the patient
   dismisses it. The spoken line is a fixed sentence, not Groq text.

## Tools (action router, `voice_tools.py`)

Each tool is a plain function `(db, patient_id, user, **args) -> dict`,
testable without Groq; it returns JSON for the model and optional UI
actions for the page.

| tool | does | reuses | UI action |
|---|---|---|---|
| `get_today_schedule` | today's doses + state, marks which are due now | dose rows | — |
| `get_next_dose` | next pending dose | dashboard logic | — |
| `mark_dose_taken(dose_id)` | marks taken, returns next dose | `scheduler.mark_taken` + audit `dose_taken_via_voice` | `dose_taken` popup with Undo |
| `log_prn_taken(medicine_id)` | logs an SOS use | same as `/log-prn` | `prn_logged` popup |
| `get_medicines` | current medicines, dose, timing, food | serializers | — |
| `get_adherence` | taken / missed / skipped, today and overall | `compute_adherence` | — |
| `get_emergency_card` | allergies, contact, blood group, card link | `emergency_card_data` | `open_card` |
| `get_treatment_history` | prescriptions with dates + recent symptom checks | db | — |
| `open_screen(screen)` | opens the full app on a tab | — | `navigate` |
| `decode_prescription(text)` | parses text into a **draft** | `create_prescription_from_lines` | `prescription_draft` |
| `confirm_prescription(id)` | schedules a draft — only if the previous turn asked and the patient said yes | same as `/confirm` | `prescription_confirmed` |
| `update_symptom_check(...)`, `finish_symptom_check()` | see above | `triage.py` | `triage_result` / `emergency` |

Dose rules: `mark_dose_taken` accepts a dose of this patient scheduled from
12 h before to 2 h after now that is pending, snoozed, or auto-missed by the
2-hour sweep (the patient is correcting the sweep's guess). If several doses
fit ("I took my medicine" with two due), Groq must ask which; the popup
shows exactly what was marked and has **Undo** (new
`POST /doses/{id}/undo`: taken within the last 10 minutes → back to pending,
audit logged).

## Backend

- `voice_assistant.py` — the turn loop: red-flag scan → Groq chat with tool
  definitions (max 5 tool rounds) → router → reply + actions + state.
  System prompt: patient's first name, the browser's local time, reply in
  the patient's language, one question at a time, never diagnose, never
  tell the patient to start/stop/change a medicine, never state a severity
  that didn't come from `finish_symptom_check`, every fact from a tool.
- `voice_tools.py` — the router (above). `voice_safety.py` — red flags.
  `voice_fallback.py` — keyword intents when `GROQ_API_KEY` is unset or
  Groq fails: took medicine, next dose, today's medicines, open a screen;
  symptoms → red-flag check, then "use the symptom check" + opens it.
- `POST /patients/{id}/voice/turn` (patient write access) with
  `{text, lang, client_time, history (last 20 {role, content}), state}`;
  returns `{reply, lang, actions, state}`. The server is stateless between
  turns; `state` carries the in-progress symptom check and a pending
  prescription confirmation. `GET /voice/available`.
- Per-user rate limit 30 turns/minute; text capped at 1,000 characters.
- Model: env `GROQ_VOICE_MODEL`, default `openai/gpt-oss-120b` (tool calling; the older Llama models are retired on Groq).

## The voice PWA page

- `static/voice.html` + `voice.js` + `voice.css`, `manifest.webmanifest`,
  a small service worker for the app shell, an SVG icon. Opens straight to:
  "SmartPoli" · "How can I help you today?" · large glowing mic.
- Under the mic: live transcript (interim results while speaking), the
  conversation as bubbles, SmartPoli's reply spoken via `speechSynthesis`.
  Language toggle हिं / EN (sets `hi-IN` / `en-IN` recognition; `hi-IN`
  handles Hinglish); mute toggle; a text box as an alternative to speaking.
  Suggestion chips: *Aaj kaun si medicine?*, *Maine dawai le li*,
  *Mujhe theek nahi lag raha*.
- Actions render as popups (✓ Metformin 500 mg — Taken 8:12 AM + Undo),
  result cards (triage result verbatim from the rule engine), the emergency
  panel, and "Open in SmartPoli" links for navigation.
- Login: not logged in → `login.html?next=/static/voice.html`; login.js
  honours `next` only for same-site `/static/` paths. Patient accounts only.
- Browsers without speech recognition (Firefox) get the text box with a
  one-line note. The app's existing mic button opens this page instead of
  its old keyword-only voice feature.

## Privacy

Transcripts go to Groq (already true for free-text triage); the page says
so on first use. Only this patient's data, fetched through permission-
checked tools, ever reaches the prompt.

## Testing

pytest with a scripted fake Groq client (no network):
- every tool directly: data, ownership (another patient's dose refused),
  dose window, auto-missed correction, already-taken refused, PRN;
- undo endpoint: within 10 min ok, later refused, other user refused;
- `finish_symptom_check` refused while rule questions are open; result
  equals `evaluate_check` for the same answers; saved `SymptomCheck`
  matches `/triage/check`; model text claiming "not serious" cannot change
  an EMERGENCY result;
- unmapped symptom → `NOT_ASSESSED`, nothing saved;
- red flags: EN / Hinglish / Devanagari phrases → EMERGENCY without any
  Groq call; near-misses ("no chest pain") don't trigger;
- `confirm_prescription` refused without the prior question;
- fallback mode with no key: "maine dawai le li", "next dose",
  "timeline dikhao", a red-flag phrase;
- login `next` accepts `/static/voice.html`, rejects external URLs.
Manual: Chrome — mic in Hindi and English, dose popup + undo, a full
headache conversation, emergency panel from a red-flag phrase, install.
