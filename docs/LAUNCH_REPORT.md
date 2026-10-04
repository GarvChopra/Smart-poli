# SmartPoli — pre-launch implementation report (2026-10-04)

Everything below was implemented in the existing FastAPI + vanilla-JS codebase (no new
framework, no rewrite, existing endpoints/permissions/models kept). Nothing is committed,
nothing is deployed, nothing is published to Google Play. "Done" below means implemented **and**
covered by the tests listed in section 6; things that need a real phone or a clinician are
listed as **not verified**, not as done.

## 1. Existing code audited

* Stack confirmed from the repo: FastAPI + SQLAlchemy (SQLite dev / Supabase Postgres prod),
  vanilla-JS SPA (`backend/static`), APScheduler in-process, Twilio WhatsApp bot, Tesseract/TrOCR OCR,
  JWT + bcrypt auth with patient/caregiver/doctor roles. Deployed on Render (Docker, free plan).
* Traced the full dose lifecycle: prescription → parse → review gate → confirm → dose rows →
  reminders → take / miss / skip / snooze → adherence → report.
* Existing and kept: OCR + deterministic parser, confidence gate, interaction + food rules,
  emergency card (tokenised QR), caregiver/doctor links, audit log, WhatsApp bot, voice assistant.
* Not previously present: any browser/app notification, patient time zone, a web-app manifest for the
  whole site, Digital Asset Links, a push service worker, official-source (regulatory) lookups,
  spacing rules, medicine-specific missed-dose rules.
* Repository facts worth knowing: the local `.env` points at the **production** Supabase database
  (see section 8 — one accidental local run created empty tables there).

## 2. Bugs found and fixed (each with regression tests)

| # | Bug | Fix |
|---|---|---|
| 1 | Dose times are patient-local wall-clock but the server compared them with **UTC** → for India every reminder and every auto-"missed" was ~5.5 h off | `clock.py`; per-patient timezone; sweeps, reminders, dashboard, WhatsApp "today" use the patient's clock |
| 2 | Confirming a prescription at 4 pm created a pending 8 am dose that was auto-marked **missed** minutes later | `generate_doses(skip_past=True)` on every confirm path (web + WhatsApp) |
| 3 | A **snoozed** dose that was never acted on never became missed | sweep covers snoozed doses |
| 4 | "Take" twice overwrote the first time and double-audited; not race-safe | one conditional `UPDATE` → idempotent; late doses keep their real time and are audited `dose_taken_late` |
| 5 | Impossible transitions allowed (miss/skip a taken dose, snooze a missed one) | 409 responses |
| 6 | `acted_at` stamped in UTC while the rest is local (broke undo window / spacing maths) | stamped in the patient's local time |
| 7 | Only one WhatsApp reminder per dose, none in the app; sweep ran every 5 min | multi-step reminder engine (below); sweep every minute |
| 8 | Scheduler `add_job` crashed on a second startup; a live timer made tests flaky | `replace_existing`, guarded start/stop, `SMARTPOLI_DISABLE_SCHEDULER` |
| 9 | Prescription upload had **no size or type check** | 10 MB cap, magic-byte image check (415/413) |
| 10 | Login had **no brute-force protection**; unknown email answered faster than a wrong password (account enumeration by timing) | 8 failures / 15 min → 429; dummy bcrypt compare |
| 11 | Logout only forgot the token client-side; a copied JWT worked for 7 days | `jti` + server-side revocation table, `POST /auth/logout` |
| 12 | A shared phone kept showing the previous patient's dose reminders after logout | logout unsubscribes the device's push subscription |
| 13 | Edits to a prescription line logged only the raw text | audit now records before → after per field + confirmation timestamp |
| 14 | Insecure default JWT secret could silently reach production | startup warning; `SMARTPOLI_REQUIRE_SECURE_CONFIG=1` refuses to start (set in `render.yaml`) |
| 15 | (my own, caught by review) an Indian brand could be shown "Verified record found" from ingredient + strength alone | capped at "Potential match" |
| 16 | (my own, caught in the browser) official-source check ran sequentially (slow), scan form layout broken, a missing icon | concurrent prefetch + cache; CSS; icon |

Not touched: the voice assistant (the existing 92 voice tests pass; "interrupted session" behaviour on a
real phone is **not verified**), OCR accuracy, doctor-dashboard priority maths (uses a 7-day UTC window;
the ≤5.5 h skew is immaterial there).

## 3. New features

* **Smart missed-dose handling** (`safety_engine.py`, `safety_service.py`): label-based and medicine-specific;
  take-now / wait-until / skip-and-continue / follow-label / contact-provider; never double; high-risk
  medicines never get a catch-up time; every decision audited with ruleset version + inputs.
* **Spacing & conflict detection**: ciprofloxacin/levofloxacin/levothyroxine/alendronate rules from FDA labels;
  asymmetric gaps; "label says separate but gives no time" → needs clarification; same-medicine and
  same-ingredient duplicates; conservative **proposals** the patient can accept (`/doses/{id}/reschedule`,
  re-validated server-side, audited, reminders re-armed).
* **Notifications** (the main feature): every dose gets an **early heads-up (default 30 min before, 0-120 min,
  per patient)**, a **last heads-up 10 min before**, a reminder **at the dose time**, and a **follow-up 15 min
  later if it is not marked taken**; a missed dose gets one message (never double, next dose time, spacing hint).
  Beyond doses: a **timing-conflict notice** right after a prescription is confirmed (once per medicine pair,
  generic text - the detail stays in the app with its source) and a **"last dose of your course" notice**
  (today / tomorrow, never before 09:00). Web Push (VAPID) + opted-in WhatsApp; each reminder at most once per
  dose/kind/channel (DB unique constraint); retried if delivery fails; expired subscriptions removed; snooze re-arms.
  Settings card with permission flow and **Send a test**.
* **Dashboard shows TODAY only** (in the patient's own timezone): "Today's medicines" with every state
  (taken / missed / due / upcoming), Take-Snooze-Skip on open doses, "Left today" instead of "Upcoming 83";
  the next day the same screen is simply that day. The 30 generated days stay in the database, not on screen.
  Recent misses (24 h) sit in a separate quiet card with a calm guidance popup; WhatsApp button.
* **"Take" cannot be pressed for a dose that is not due**: only from 2 h before to 12 h after its time; earlier shows a
  popup ("Too early … you can mark it taken from …") and the server refuses (409), so repeated taps can no longer
  "take" the whole course. The Next-dose card shows "Not due yet" instead of a button.
* **Gap checks when "Mark taken" is tapped** (same medicine too soon; another medicine that must be kept apart, e.g. Ciprofloxacin
  after Calcium): curated FDA-label rules first, then label-grounded AI answers (openFDA + Groq, graded "label" vs "AI estimate",
  cached, never loosening a curated rule). Popup says why and when it is OK; "I already took it" is allowed only for soft warnings and is audited.
  See `docs/SCHEDULING.md`.
* **Patient's own routine** (Settings): morning/afternoon/evening/night/bedtime times, before-food = 30 min earlier,
  optional reminders (last heads-up, follow-up); new medicines follow it and "Save & move my current medicines"
  re-times only future untouched doses. See `docs/SCHEDULING.md` for the design, sources and limits.
* **Simpler screens**: Prescription tab = *Scan medicine* (opens the camera), *Choose file* (opens the phone's files),
  *type it in*. Dashboard = next dose + today's medicines (+ a one-line timing hint, WhatsApp as a popup).
  Adherence/progress moved to the Care report; interactions/food/timing stay in Safety center. The "70 medicines need
  confirmation" came from abandoned drafts piling up forever — they now age out after 3 days and are no longer listed in the
  Care report. "Export calendar" / "Download PDF" sent no login token (401); they now download with the token.
* **Alerts redesigned**: one component for every patient alert - plain title, "What's the problem", a gap bar
  ("Now 0 min apart - needed 4 h"), "What to do", and a collapsible "Why am I seeing this?" with the label
  quote and link. Colours: amber = needs your action, soft yellow = use with care, blue = good to know,
  green = all fine, with a legend. **Red is not used** (kept for real emergencies). The old red boxes on the
  patient screens were converted.
* **Official status** (`regulatory.py`): CDSCO prohibited/restricted FDC lists imported from CDSCO's own PDFs
  (188 rows); openFDA approvals and recalls; India and US kept separate; explicit statuses; cached with
  "last checked"; a failed lookup is "source unavailable", never a restriction.
* **Scan a medicine** with the camera: OCR → candidate names (with the text they were read from) → patient
  confirms name/strength and **types the schedule** → normal review gate. Nothing is guessed or added silently.
* **Verification status** per medicine: transcription / name match / schedule / official status as four
  separate checks, plus the "not clinically appropriate" note.
* **Android/PWA**: whole-site manifest, icons (incl. maskable), `sw-push.js`, `assetlinks.json` route,
  `android/twa-manifest.json`, `tools/check_twa_readiness.py`, `docs/ANDROID_TWA.md`.
* **Ops**: external-cron endpoint `POST /internal/reminder-sweep` (secret-guarded) because a free Render
  instance sleeps; `tools/verify_rule_sources.py`, `generate_vapid_keys.py`, `import_cdsco_fdc.py`.

## 4. Files

Modified: `backend/{main,db,auth,auth_router,scheduler,schemas,serializers,prescription_service,whatsapp_bot,ocr_plugin}.py`,
`backend/static/{app.js,auth.js,index.html,style.css}`, `backend/requirements{,-ingest}.txt`, `render.yaml`, `.gitignore`,
tests `conftest.py`, `test_ocr_endpoint.py`, `test_whatsapp_bot.py`.
Created: `backend/{clock,safety_engine,safety_service,safety_rules.json,reminders,webpush_service,regulatory,medicine_scan,verification}.*`,
`backend/data/cdsco_prohibited_fdc.json`, `backend/tools/*` (4 scripts), `backend/static/{push.js,sw-push.js,app.webmanifest,app-icon*}`,
`android/twa-manifest.json`, `docs/{ANDROID_TWA,QA_CHECKLIST,MEDICATION_RULES,LAUNCH_REPORT}.md`, `.env.example`,
and 13 new test files (229 tests) plus a shared helper.
Untracked and unrelated: `docx` (planning notes) and the WhatsApp `.mp4` in the repo root — keep both out of commits.

## 5. Database migrations and environment variables

**Migrations:** none destructive, no existing table altered. Five **new tables** are created by `create_all` on
startup: `patient_settings`, `push_subscriptions`, `reminder_log`, `regulatory_lookup_cache`, `revoked_tokens`.
(New *tables* rather than columns because the project's migration helper can add columns only on SQLite.)
A test builds an old-schema SQLite DB and proves data survives `init_db()` twice. **Back up Supabase before deploying.**
Already created in production by an accidental local run (empty): all five — see section 8.

**New environment variables** (`.env.example` has the full list):
`SMARTPOLI_DEFAULT_TZ`, `SMARTPOLI_VAPID_PRIVATE_KEY` / `_PUBLIC_KEY` / `_SUBJECT`, `SMARTPOLI_CRON_SECRET`,
`SMARTPOLI_ANDROID_PACKAGE`, `SMARTPOLI_ANDROID_SHA256`, `OPENFDA_API_KEY` (optional),
`SMARTPOLI_REQUIRE_SECURE_CONFIG`, `SMARTPOLI_DISABLE_SCHEDULER` (tests). `render.yaml` declares them.
New dependencies: `pywebpush==2.5.0`, `tzdata==2026.2` (runtime); `pdfplumber` (ingest tool only).

## 6. Tests actually run (2026-10-04)

* **Python:** `pytest tests` → **568 passed, 0 failed** (339 before this work; +229 new).
* **JavaScript:** `auth_next` 3, `voice_commands` 11, `voice_engine` 18, `shorthand` 26 — all pass.
* **Rule sources:** `verify_rule_sources.py` → 25 sources, **0 failures** against the live FDA labels.
* **TWA readiness:** local tree 21 pass / 2 warn / 0 fail. **Production as currently deployed: 4 fail**
  (no manifest, assetlinks, service worker, theme colour) — expected until this branch is deployed.
* **Browser smoke (Chrome, temp SQLite DB, demo data):** dashboard, missed-dose popup, conflict card
  (including the engine *refusing* a proposal that would break another rule), scan card + add flow,
  Settings reminders card, live openFDA lookups (~1 s) — all rendered, no console errors.

**Not tested — do not assume it works:** a real Android device; **push delivery to a real phone** (the sender is
tested with real VAPID keys + real payload encryption but the final POST is mocked; no VAPID keys are
configured on the server); real OCR on a real strip/box (Tesseract is not installed on this machine, OCR is
mocked); Twilio; the Android build (no JDK/SDK here); voice/microphone inside the TWA; downloads inside the TWA.

## 7. TWA build and install

**Who built the TWA:** not this work. No Android package or signing key was created here (no JDK / Android SDK on this
machine); only the web side (manifest, icons, `assetlinks.json` route, push service worker), a Bubblewrap config
template and a checker were added. If the Android app was built separately, **it only opens full-screen if
`SMARTPOLI_ANDROID_PACKAGE` / `SMARTPOLI_ANDROID_SHA256` on Render equal THAT app's package id and release-signing
fingerprint** (the template's `app.smartpoli.twa` is only a suggestion). Verify with
`python backend/tools/check_twa_readiness.py https://<host> --package <your.package> --sha256 <your fingerprint>`.

See `docs/ANDROID_TWA.md` (Bubblewrap, keystore, assetlinks, `adb install`, notification caveats).
Manual device pass: `docs/QA_CHECKLIST.md`.

## 8. Incomplete / known gaps

* **Accidental production touch:** `.env` points at the live Supabase DB; a local server start (GET requests only)
  created the 5 empty tables above. No data changed. Stored as a project memory so it is not repeated.
* Original prescription image is **not stored** (privacy-first). The raw OCR text and every correction are kept;
  a side-by-side "image vs extracted fields" screen was not built.
* No new image-quality guidance for unreadable prescriptions beyond the existing needs-confirmation gate.
* CDSCO monthly "Not of Standard Quality" alerts and Drugs@CDSCO approvals are **not** automated (no feed).
* Medicine matching uses the existing ~360-name catalogue; there is no licensed product database (so no
  barcode scanning, and many real brands will be "not matched").
* Rate-limit for logins is in-memory (per process).
* Time-zone change mid-course shifts future reminders but does not rewrite generated doses.
* Offline "dose taken while offline" is **not** supported (no offline queue); the app needs a connection.
* Doctor/caregiver dashboards do not show the new timing conflicts.
* No automated end-to-end browser test suite (manual checklist only).

## 9. Needs clinical / reference validation before real patients

* **Every rule in `safety_rules.json` is unreviewed** (`reviewed:false`). See `docs/MEDICATION_RULES.md`:
  US-label rules applied to Indian brands, the 6-hour ciprofloxacin reading, the high-risk list,
  patient-facing wording, and that unlisted pairs (e.g. iron + calcium) are *unverified*, not safe.
* Regulatory wording ("potential match", "restricted status identified") should be checked by someone with
  Indian drug-regulation knowledge; the FDC match is by ingredient combination only.
* Legal: whether giving timing guidance makes the app Software as a Medical Device under India's Medical
  Devices Rules / CDSCO (and equivalents abroad) is **unresolved here** — get legal advice before launch.

## 10. Prioritised blockers before public launch

1. **Clinician/pharmacist review** of the rules and patient wording (or ship without the missed-dose/spacing
   guidance and with label-text-only).
2. **Legal/regulatory opinion** on medical-device status and a **privacy policy + health-data consent**
   (also required by Google Play) — and a data-retention/deletion process.
3. **Reminders must run while the server sleeps:** paid always-on instance *or* an external 1-minute cron on
   `/internal/reminder-sweep` (secret set) — otherwise reminders silently stop.
4. **Prove push delivery on real phones** (3+ vendors incl. a battery-aggressive one) using the QA checklist;
   decide the fallback (WhatsApp) for devices that fail.
5. **Deploy + back up Supabase first**, set VAPID keys / cron secret / Android package + SHA-256, re-run
   `check_twa_readiness.py` against production until 0 failed.
6. Build and sign the AAB (needs JDK + Android SDK + your keystore); back the keystore up; Play App Signing.
7. Real-device test of camera scan and OCR quality on actual Indian packaging; tune or add manual-entry nudges.
8. Move the login throttle and the reminder lock to a shared store before running more than one instance.
9. Offline dose logging, image-vs-fields confirmation screen, doctor-side timing view (nice-to-have).
