# Caregiver Portal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (run natively in one session — the human partner said "start now, don't ask"). Steps use checkbox syntax.

**Goal:** Split the caregiver portal into separate pages and let caregivers write notes to the patient, notes on a medicine and handover notes, get missed-dose alerts on their phone, and nudge the patient with "Remind now".

**Architecture:** New additive tables (`care_notes`, `user_push_subscriptions`, `caregiver_prefs`); one new module `care_notes.py` (rules + queries, no HTTP); endpoints added to `caregiver_router.py` and `main.py`; the portal becomes a hash-routed multi-page SPA (`caregiver.html` + `static/caregiver/*.js`); patient app gets a "From your caregiver" card.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite dev / Postgres prod), pywebpush (existing `webpush_service`), vanilla JS.

**Spec:** `docs/superpowers/specs/2026-10-06-caregiver-portal-design.md`

## Global Constraints
- New tables only (`create_all`); no ALTER of existing tables (Postgres cannot be altered by the app).
- Every endpoint checks the ACTIVE `CaregiverLink` on the server (`auth.has_read_access`); caregivers never write to medicines, doses, schedules or profile.
- Note body ≤ 500 characters; 30 notes/hour per author; all note text HTML-escaped on output.
- Handover notes (`visibility="care_team"`) never appear on any patient endpoint.
- "Remind now": at most once per 15 minutes per patient, audited. Missed-dose alerts at most once per (dose, caregiver) via `ReminderLog.channel = "caregiver:<user_id>"`.
- Copy: "Notes from your caregiver are not medical advice."
- No Claude co-author in commits (author is GarvChopra).

## Review Focus
- A revoked caregiver (link flipped to `revoked`) can still read/write → must be 403 everywhere, including delete and nudge.
- A medicine note whose `medicine_id` belongs to a DIFFERENT patient → must be rejected (422).
- Note text containing `<script>` or HTML → must come back escaped/inert in the patient card.
- Two caregivers of the same patient, one disabling `notify_missed` → only the other is alerted.
- Missed-dose sweep running twice → no duplicate caregiver push.
- Nudge pressed twice quickly → second call returns 429 with the wait time.
- Empty / whitespace-only / 501-char note → rejected (422).

---

### Task 1: Data model + rules module
**Files:** Modify `backend/db.py`; Create `backend/care_notes.py`; Test `backend/tests/test_care_notes.py`
**Interfaces:**
- Produces: `CareNote`, `UserPushSubscription`, `CaregiverPrefs` models; `care_notes.MAX_LEN=500`, `care_notes.RATE_LIMIT=30`,
  `create_note(db, patient_id, author, kind, body, medicine_id=None) -> CareNote` (raises `ValueError` with a plain message),
  `list_notes(db, patient_id, viewer_role, kind=None, medicine_id=None) -> list[dict]` (care-team viewers see all kinds; patient sees only `visibility="patient"`),
  `serialize_note(note) -> dict` (`id, kind, medicine_id, body, author_name, author_role, created_at, seen`),
  `unread_count(db, patient_id) -> int`, `mark_seen(db, patient_id) -> int`, `soft_delete(db, note_id, user) -> bool`.
- [ ] Write failing tests: create validates length/kind/medicine ownership; patient listing hides handover; rate limit; soft delete only by author.
- [ ] Implement models + module; run tests; commit.

### Task 2: Notes API
**Files:** Modify `backend/caregiver_router.py`, `backend/main.py` (patient endpoints), `backend/schemas.py`; Test `backend/tests/test_care_notes_api.py`
**Interfaces:**
- Consumes: Task 1 functions.
- Produces: `GET/POST /caregiver/patients/{id}/notes` (query `kind`, body `{kind, body, medicine_id?, notify?}`), `DELETE /caregiver/notes/{note_id}`,
  `GET /patients/{id}/care-notes` → `{notes:[…], unread:int, disclaimer}`, `POST /patients/{id}/care-notes/seen` → `{seen:int}`,
  `GET /doctor/patients/{id}/care-notes` (all kinds, active doctor link).
- [ ] Failing access-matrix tests: stranger / unlinked / revoked caregiver / patient / doctor for each endpoint; handover hidden from the patient; XSS text stored raw but escaped by the client (server returns raw string, test asserts JSON-safe).
- [ ] Implement; run; commit.

### Task 3: Caregiver phone alerts
**Files:** Modify `backend/reminders.py`, `backend/caregiver_router.py`; Test `backend/tests/test_caregiver_alerts.py`
**Interfaces:**
- Produces: `POST /caregiver/push/subscribe` `{endpoint, keys:{p256dh,auth}}`, `POST /caregiver/push/unsubscribe`,
  `GET/PUT /caregiver/patients/{id}/prefs` (`{notify_missed:bool}`), `reminders.notify_caregivers_missed(db, missed, push_fn=webpush_service.send) -> int` called from `notify_missed`.
- [ ] Failing tests with a fake `push_fn`: both caregivers alerted; one with `notify_missed=False` skipped; revoked skipped; second call sends nothing (dedupe); gone subscription deleted.
- [ ] Implement; run; commit.

### Task 4: Remind now
**Files:** Modify `backend/caregiver_router.py`; Test `backend/tests/test_caregiver_nudge.py`
**Interfaces:** `POST /caregiver/patients/{id}/nudge` → `{sent:int}`; 429 `{detail, retry_after_seconds}` within 15 min; audit action `caregiver_nudge`.
- [ ] Failing tests (fake push via monkeypatch of `webpush_service.send`): sent once, second call 429, revoked 403, no patient subscription → 200 `{sent:0}` with a plain note.
- [ ] Implement; run; commit.

### Task 5: Portal pages
**Files:** Modify `backend/static/caregiver.html`; Create `backend/static/caregiver-pages.js` (router + one render function per page: patients, today, medicines, notes, history, emergency, settings); Modify `backend/static/caregiver.js` (slim to shell: session, drawer, a11y); Modify `backend/static/style.css`; Test `backend/tests/test_caregiver_ui_contract.py`
**Interfaces:** hash routes `#/`, `#/p/:id/today|medicines|notes|history|emergency`, `#/settings`; each page function `async function pageX(root, patientId)`.
- [ ] Failing contract tests: every route has a render function; notes page contains "Messages to the patient" and "Handover notes"; medicines page has "Add note"; today page has "Remind now"; all dynamic text goes through `escHtml`.
- [ ] Implement pages; real-browser check at phone width; commit.

### Task 6: Patient side
**Files:** Modify `backend/static/app.js`, `backend/static/style.css`; Test `backend/tests/test_ui_contract.py`
- [ ] Failing contract tests: dashboard has a "From your caregiver" card fed by `/patients/{id}/care-notes`, marks seen on open, shows the disclaimer; Safety center shows a medicine's notes under it.
- [ ] Implement; commit.

### Task 7: Finish
- [ ] Full suite + node tests; update `docs/LAUNCH_REPORT.md` (new tables, endpoints); push.
