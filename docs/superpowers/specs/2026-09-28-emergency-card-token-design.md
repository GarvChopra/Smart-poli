# Emergency card: secure QR token — design

Date: 2026-09-28
Status: draft, awaiting review

## Problem

The public, no-login emergency page lives at `/emergency/{patient_id}`, where
`patient_id` is a sequential integer. Anyone can walk `/emergency/1`,
`/emergency/2`, … and read every patient's name, age, allergies, emergency
contact and current medicines. The QR code, the caregiver view and the
WhatsApp bot all hand out this guessable URL.

## Goal

The QR (and every shared link) carries only a random, unguessable token.
The patient can revoke it at any time — the old QR / printed card then shows
nothing. The page shows when its information was last updated.

Out of scope here (roadmap item 3, later): printable PDF card, lock-screen
wallpaper, per-field "what to expose" choices, photo.

## Design

### Data

New table `emergency_card_tokens` (a new table, not new columns on
`patients`, because `init_db()` only migrates columns on SQLite — on the
Render Postgres DB `create_all` can create new tables but cannot alter
existing ones):

| column       | type                      | notes                                  |
|--------------|---------------------------|----------------------------------------|
| id           | int PK                    |                                        |
| patient_id   | int FK patients.id        | indexed                                |
| token        | string, unique, indexed   | `secrets.token_urlsafe(16)` (~128 bit) |
| created_at   | datetime                  |                                        |
| revoked_at   | datetime, nullable        | set on revoke; row kept for audit      |

A patient has at most one active (non-revoked) token. It is created lazily
the first time the card / QR / link is requested.

Helper in a new small module `emergency_tokens.py`:
- `get_or_create_active_token(db, patient_id) -> str`
- `rotate_token(db, patient_id) -> str` — revokes the active one, creates a new one
- `patient_id_for_token(db, token) -> Optional[int]` — `None` if unknown or revoked

### Endpoints

- `GET /emergency/{token}` — public page, now looked up by token. Unknown or
  revoked token → 404 page saying "This emergency card is no longer active"
  (no patient data). The old integer URLs therefore stop working; there is
  deliberately no redirect, since a redirect would keep the leak open.
- `GET /patients/{id}/emergency-card` — response gains `card_url` (full URL
  with token) and `last_updated`.
- `GET /patients/{id}/emergency-card/qr.png` — encodes the token URL.
- `POST /patients/{id}/emergency-card/revoke` — patient write access only;
  rotates the token, writes an audit log entry, returns the new `card_url`.

Page hardening on `/emergency/{token}`: `<meta name="robots" content="noindex">`
and `Referrer-Policy: no-referrer` so the token isn't leaked to other sites.
The existing service worker scope `/emergency/` still matches.

### "Last updated"

Computed, no new column: the latest of the patient's `created_at`, the newest
`AuditLog.at` for that patient, and the newest prescription `created_at`.
Shown on the public page and in the app as e.g. "Last updated 28 Sep 2026,
11:20 AM". (The plan will verify that patient profile edits write an audit
entry; if not, `PATCH /patients/{id}` gets one.)

### Callers updated

- `static/app.js` emergency tab — uses `card_url` from the API instead of
  building `/emergency/${patientId}`; adds a "Revoke & make new QR" button
  with an inline confirm (no `window.confirm`).
- `static/caregiver.js` — uses `card_url`.
- `whatsapp_bot.py::_handle_emergency` — uses the token URL.

## Testing

- Public page by token renders allergy/contact (existing test, updated).
- `/emergency/{integer id}` → 404, no patient data in body.
- After revoke: old token → 404 with no patient data; new token works; QR
  PNG encodes the new token.
- Revoke by a user without write access → 403.
- Token is not derivable from the patient id (two patients → unrelated tokens).
- WhatsApp emergency reply contains the token URL, not `/emergency/{id}`.
- `last_updated` moves forward after editing allergies.
