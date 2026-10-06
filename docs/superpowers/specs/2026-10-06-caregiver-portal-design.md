# Caregiver portal — design

Date: 2026-10-06 · Status: awaiting review

## Goal
Turn the caregiver portal (today one long read-only page) into a set of separate pages where a caregiver can **write**:
notes to the patient, notes on a specific medicine, and handover notes for the care team — and can be **alerted** when a dose
is missed and **nudge** the patient with "Remind now".

## Who and what stays the same
* A caregiver is a user with role `caregiver` linked to a patient through an ACTIVE `CaregiverLink` (invite code, patient can revoke).
  Every endpoint below checks that link on the server; the screen never decides access.
* A caregiver still **cannot** change medicines, doses, schedules or the patient's profile. Those stay with the patient.
* Notes are information from a person, not medical advice; the patient's screen says so.

## Pages (one HTML page, hash routes, each route its own render module)
| Route | Content |
|---|---|
| `#/` Patients | existing patient cards (priority badge, search), "Link a patient" |
| `#/p/:id/today` | next doses, alerts, today taken/missed, **Remind now**, **Call / WhatsApp** (only if the patient has a phone on file) |
| `#/p/:id/medicines` | each medicine with adherence + its notes thread + "Add note" |
| `#/p/:id/notes` | **Messages to the patient** (with "notify their phone now") and **Handover notes** (care team only) |
| `#/p/:id/history` | adherence, missed doses, symptom checks |
| `#/p/:id/emergency` | emergency information (existing content) |
| `#/settings` | alert switches, enable phone alerts, link a patient, larger text, log out |

## Data (new tables only; created by `create_all`, no ALTER)
* `care_notes(id, patient_id, author_user_id, author_role, kind, medicine_id NULL, body ≤500, visibility, created_at, seen_by_patient_at NULL, deleted_at NULL)`
  * `kind`: `message` | `medicine` | `handover`
  * `visibility`: `patient` (message, medicine) | `care_team` (handover: caregivers + doctors linked to the patient, **not** the patient)
* `user_push_subscriptions(id, user_id, endpoint UNIQUE, p256dh, auth, created_at, last_success_at)` — a caregiver's phone.
* `caregiver_prefs(user_id, patient_id, notify_missed bool default true)` — per caregiver per patient.

## API
Caregiver (role + active link):
* `GET/POST /caregiver/patients/{id}/notes?kind=` — list / create (`message`, `medicine` with `medicine_id`, `handover`)
* `DELETE /caregiver/notes/{note_id}` — author only (soft delete)
* `POST /caregiver/patients/{id}/nudge` — "Remind now": push to the patient's devices; once per 15 min per patient; audited
* `POST /caregiver/push/subscribe`, `POST /caregiver/push/unsubscribe`
* `GET/PUT /caregiver/patients/{id}/prefs`
Patient (owner):
* `GET /patients/{id}/care-notes` — visibility `patient` only, newest first, with `unread` count
* `POST /patients/{id}/care-notes/seen` — marks seen
Doctor read access to handover/medicine notes reuses `has_read_access` (active doctor link).

## Behaviour
* **Missed-dose alert:** when `notify_missed` dose handling marks a dose missed, each linked caregiver with a push subscription and
  `notify_missed` on gets "<Name> missed <Medicine> (<time>)". Deduplicated with `ReminderLog` (`channel = "caregiver:<user_id>"`).
* **Message notify:** "notify their phone now" sends a push to the patient's devices with the first 80 characters.
* **Patient app:** dashboard card "From your caregiver" (latest 2, unread badge); the note shows under its medicine in the Safety center;
  opening the card marks notes seen. Line shown: "Notes from your caregiver are not medical advice."
* **Limits:** 500 characters, 30 notes/hour per author, text always HTML-escaped on output, revoked link ⇒ 403 everywhere and the
  portal shows "You're no longer linked to this patient".
* **Audit:** note created/deleted, nudge sent.

## Out of scope (later)
Refill tracker, appointments, health log, weekly digest, caregiver editing medicines.

## Testing
Server: access matrix (stranger, unlinked, revoked, patient, doctor) for every endpoint; handover hidden from the patient; medicine note
must belong to that patient; limits; missed-dose fan-out with a fake push function and dedupe; nudge rate limit.
Screens: UI-contract tests per page; real-browser check of each route at phone width.
