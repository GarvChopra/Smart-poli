# 3D Digital Health Card — design

Date: 2026-09-28
Status: approved direction from the user's brief (pasted twice, "start now, don't ask"); details below are rulings made on their behalf.
Depends on: `2026-09-28-emergency-card-token-design.md` (token URL, revoke, last updated).

## Goal

Replace the patient's Emergency card screen with one hero element: a
realistic, rotatable 3D health ID card (front/back), with the actions
**Share · Download · Print · Edit · Revoke QR** under it. The QR opens the
public, token-based emergency view, which shows only what the patient
chose to share. Existing backend behaviour is kept; everything is additive.

## What exists vs. what's added

| Card field | Source |
|---|---|
| Name, age, sex, blood group, allergies, emergency contact | `patients` (exists) |
| Current medicines | confirmed prescriptions (exists) |
| Emergency indicator | `has_emergency_triage_history` (exists) |
| QR, last updated, revoke | token work (exists) |
| Caregiver / doctor names | active `caregiver_links` / `doctor_links` → `users.name` (exists, newly surfaced) |
| **Photo, medical conditions, emergency instructions, share choices** | **new table `emergency_card_profiles`** |

New table (not new columns — Render Postgres isn't migrated by `init_db`):

| column | type | notes |
|---|---|---|
| id | int PK | |
| patient_id | int FK, unique | one profile per patient, created on first save |
| conditions | text, nullable | free text, ≤ 500 chars |
| instructions | text, nullable | free text, ≤ 500 chars |
| photo | text, nullable | `data:image/jpeg;base64,…` or png, ≤ 200 KB; browser resizes to 256 px before upload |
| share | text (JSON) | `{"photo","blood_group","allergies","conditions","medicines","emergency_contact","caregiver","doctor","instructions"}` → bool; missing key = true (today's page already shows everything, so default stays "share") |
| updated_at | datetime | |

Name is always shown (a card without a name is useless to a responder).

## Backend

- `emergency_card_data` gains `profile` (`conditions`, `instructions`,
  `has_photo`, `share`) and `care_team` (`caregivers`, `doctors`: lists of
  names). Existing keys unchanged.
- `GET /patients/{id}/emergency-card/profile`, `PUT …/profile` (write
  access; validates lengths, photo prefix/size, share keys; audit log
  `emergency_profile_updated`, which also moves `last_updated`).
- `GET /patients/{id}/emergency-card/photo` (read access) → image bytes.
- `GET /emergency/{token}/photo` (public) → image only if `share.photo`,
  else 404. Same token rules as the page.
- Public page `/emergency/{token}` renders, in order and only when shared:
  photo, name (+age/sex/blood group), allergies, conditions, medicines,
  emergency contact, caregiver, doctor, instructions, last updated.
- `GET /patients/{id}/emergency-card/card.pdf` (read access) — A4 page with
  front and back at real card size (85.6 × 54 mm) side by side, cut
  guides, QR embedded; respects share choices (it's meant for a wallet).
- All patient-entered text on the public page and PDF is HTML/markup
  escaped.

## Frontend (`static/health-card.js`, styles in `style.css`)

- **Card:** credit-card ratio 85.6:54, width `min(92vw, 460px)`. CSS 3D:
  `perspective` on a stage, `transform-style: preserve-3d` card, two faces
  with `backface-visibility: hidden`, thin edge made of a few stacked
  layers for thickness, soft shadow that shifts with angle, one faint
  sheen whose position follows rotation. White card, teal accent, no heavy
  gradients.
- **Rotation:** pointer drag/swipe rotates Y (and a small X tilt); on
  release it eases to the nearest face (0° or 180°). Tap or the "Flip"
  control flips; keyboard: card is focusable, Enter/Space flips.
  `prefers-reduced-motion`: no easing, instant flip.
- **Front:** SmartPoli mark + "EMERGENCY HEALTH ID"; photo or initials
  avatar; full name; age · sex; blood group chip; allergy strip (alarm
  tone, "No known allergies recorded" otherwise); emergency indicator dot
  (red with "Emergency history" if flagged); QR in a white tile with teal
  corner marks and "Scan in an emergency" caption.
- **Back:** two-column blocks — Emergency contact, Caregiver, Doctor /
  care team, Conditions, Current medicines (max 4 + "+N more"),
  Instructions; footer: Last updated · "Active · verified by SmartPoli"
  status with the card id's last 4 chars. Empty fields show "—", never
  overflow (line clamp).
- **Actions:** Share (`navigator.share` with the card link; fallback copies
  the link and shows "Link copied"), Download (PDF above), Print (print
  stylesheet: only front+back flat at 85.6 × 54 mm), Edit (inline panel:
  allergies, blood group, emergency contact, conditions, instructions,
  photo upload/remove, "What the QR shows" toggles — saves via existing
  `PATCH /patients/{id}` + new `PUT …/profile`), Revoke QR (the inline
  confirm from the token work).
- The existing "Care team" linking card stays below, unchanged.
- No `alert`/`confirm`/`prompt` (the old Edit buttons used `prompt`; the
  Edit panel replaces them).

## Testing

pytest: profile GET/PUT round-trip; validation (too-long text, non-image
photo, oversized photo, unknown share key → 422); read-only user can't
PUT (403); public page hides each unshared field; public photo 404 when
not shared or token revoked; caregiver/doctor names appear; public page
escapes `<script>` in conditions; PDF endpoint returns `%PDF`; existing
emergency tests still pass.
Manual (Chrome): drag rotate, flip button, keyboard flip, mobile width
(390 px), edit + save, share fallback, print preview, download, revoke.
