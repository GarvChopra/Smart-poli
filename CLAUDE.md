# CLAUDE.md — SmartPoli

Read this file completely before writing any code. It is the single source of truth for this project.

---

## 1. What we are building

**SmartPoli** — a patient-assistance web app for hackathon problem statement **PS 4: Medical Prescription Decoder & Patient Care Portal** (BitNBuild, Quiksort × GDG, Web/App Development track).

### The problem, in one sentence

Patients can't read handwritten prescriptions, can't decode shorthand like `1-0-1 after meals`, lose track of complex daily schedules, and panic over post-treatment symptoms without knowing whether they need emergency care or routine follow-up.

### The four required features

**Feature 1 — Prescription Parser & Decoder**
Accept a handwritten prescription image *or* typed text. Extract medicine name, dosage, frequency, duration, before/after food, and other instructions. Convert `1-0-1 after meals` into:
```
Morning — 1 tablet
Afternoon — 0 tablets
Night — 1 tablet
Take after meals.
```

**Feature 2 — Medication Adherence Scheduler**
Daily timeline, upcoming-dose alerts, missed-dose logging, completed-dose logging, treatment progress, adherence percentage, medication history.

**Feature 3 — Rule-Based Symptom Triage**
Patient records symptoms. System asks structured follow-up questions and assigns LOW / MODERATE / EMERGENCY, explains *why*, and routes to continued monitoring, routine consultation (with a doctor booking link), or emergency attention.
The PS states explicitly: this must **not** be a generative diagnosis system. It must be transparent and rule-based.

**Feature 4 — Caregiver & Doctor Export**
Downloadable report: patient info, prescription, medicines, dosages, schedule, adherence, missed doses, symptoms, triage history, important alerts.

### The central value proposition

> Turn a confusing prescription into an understandable treatment journey, then help the patient safely follow and monitor that journey.

SmartPoli is **not** an OCR tool. OCR is the commodity part. The journey is the product.

---

## 2. Research already done — do not redo it

I surveyed 20 related open-source projects by reading their source, file listings and licences. Key conclusion:

**No repo implements the shorthand decoder, the confidence gate, or a unified report.** Those three are the graded differentiators and must be built.

Ceiling of any single repo against PS 4 is about 40%.

### Critical lesson: READMEs in this space are unreliable

Eight of the twenty had README claims contradicted by their own code. Verify before trusting. Examples found:
- One README described `backend/` and `frontend/` dirs; the repo was flat with no backend.
- One claimed MIT in the README with no LICENSE file present.
- One's LICENSE said GPL-3.0 while its README badge said MIT.
- One promised "medication reminders"; the Flask app had no medication endpoint at all.
- One reported a word error rate of 1.15 — worse than outputting nothing.
- One published 72%/85% accuracy figures with no test harness in the repo.

**Never repeat a performance number from any of these READMEs.**

---

## 3. Base repository

### Clone this

**PrescriptionReader** — https://github.com/princygupta-01/prescription_reader

Chosen because: FastAPI + Next.js + SQLite (nothing external to configure), it owns the hard half of PS 4 (OpenCV preprocessing, TrOCR, LLM extraction, per-field confidence, ReportLab PDF), and everything missing from it is pure logic.

Its structure:
```
backend/
  main.py           FastAPI server + endpoints
  pipeline.py       stage orchestration
  preprocess.py     OpenCV image enhancement
  ocr.py            TrOCR wrapper + confidence
  extractor.py      Groq/Llama extraction + JSON parsing
  validator.py      OpenFDA + Indian drug list validation
  pdf_generator.py  ReportLab
  database.py       SQLAlchemy models
  models.py         Pydantic schemas
  indian_drugs.py   ~150 Indian medicine names
frontend/
  app/ components/ lib/
```

### Licence action required

Its README says "MIT License — see LICENSE file" but **there is no LICENSE file in the repo**. Open an issue asking the author to add the file they already claim. Record the outcome in `THIRD_PARTY.md`. Until resolved, treat the code as default-copyright and prefer reimplementing over copying.

---

## 4. Reference repositories — study, reimplement, do not copy

All four below have **no LICENSE file**, so their code is under default copyright and is not reusable. Architectural ideas are not copyrightable; reimplement them.

| Repo | Link | Take this |
|---|---|---|
| **smart-healthcare-triage** | https://github.com/ardhendudebnath/smart-healthcare-triage | The entire triage design. Best-engineered repo in the survey — 122 tests, spaCy offline extraction, 79 symptoms / 487 phrases, content-hashed ruleset versions, append-only SQLite audit. |
| **Ccare** | https://github.com/Zhanbingli/Ccare-app | Dose state machine and medication UX. iOS/Swift, so read only. |
| **HealthCare** | https://github.com/clickBait-404/HealthCare | node-cron reminder worker pattern; deterministic fallback so the app runs with zero API keys; seeded demo personas for the pitch. |
| **handwritten-prescription-recognition** | https://github.com/HimSingh10/handwritten-prescription-recognition | The confidence ladder: ≥85 auto-verify, 70–85 review, <70 manual, with RapidFuzz name matching. |

### Docs worth reading (for schema shape and doc style only)

- **LifeDoc** — https://github.com/MohitSoni2021/lifedoc — `ARCHITECTURE.md`, `DFD.md`, ERD. Best documentation in the survey. No LICENSE; triage is Gemini-based and returns `suggestedMedicines` from an LLM, which is exactly what we must not do.
- **HealthCare** — `SYSTEM_DESIGN.md`.

### Surveyed and rejected — do not clone

| Repo | Link | Why not |
|---|---|---|
| ScanPlus | https://github.com/Gupta-Aryaman/scanPlus | MIT, but needs AWS Textract account + Spark cluster |
| Swasthya-AI | https://github.com/indresh404/Swasthya-AI | MIT, but needs Neo4j + Supabase + Sarvam + Groq |
| Swasthya-AI-Demo | https://github.com/indresh404/Swasthya-AI-Demo | Frontend only despite README describing a backend |
| Swasthya-Saathi | https://github.com/PredictiveManish/Swasthya-Saathi | No medication endpoints exist; triage is Gemini |
| Swasth Saathi | https://github.com/ajharshal45/swasth-saathi | Merge-conflict markers committed in README; rules engine unverified |
| ArogyaTrack | https://github.com/Aashutosh-Mahajan/ArogyaTrack | Explicitly Proprietary |
| TRxNSLATE | https://github.com/fathur-rs/trxnslate | LICENSE says GPL-3.0, README says MIT — ambiguous copyleft |
| AgoraCare | https://github.com/iamaanahmad/AgoraCare | MIT, but LLM triage and requires Firebase + Agora SDK accounts |
| MedTracker | https://github.com/damacus/med-tracker | MIT, Rails, no prescription side |
| MedMed | https://github.com/lizlove/medmed | MIT, Rails, ~2015, dead deploy link |
| Health Companion | https://github.com/Argyrys/health-companion | Android/Kotlin + Firebase |
| Medical-Prescription-OCR | https://github.com/JonSnow1807/Medical-Prescription-OCR | Trained on synthetic data; README says research use only, do not process real prescriptions |
| Medical-Prescription-Analyzer | https://github.com/SOHAM-3T/Medical-Prescription-Analyzer- | WER 0.87 → 1.15, failed fine-tune |

---

## 5. The architecture decision that matters most

**OCR is a plugin, not a foundation.**

```
[ OCR path ]     image → preprocess → OCR → line text ─┐
                                                        ├→ parser → Medicine[] → review gate → confirm → Dose[] → dashboard → report
[ Manual path ]  textarea → line text ─────────────────┘
                                                        └→ triage (independent of prescription)
```

Every stage after extraction consumes the same `Medicine` object and does not know or care where it came from.

**Why this matters:** the manual path always works. If TrOCR won't install, a model download times out, or the Groq key rate-limits during judging, the product still demos end to end. Several surveyed projects made OCR the spine and became undemoable when one dependency failed.

**Build the manual path first. Add OCR last, only when features 1–4 are done and tested.**

---

## 6. Canonical data model

One model. Do not create a second medicine representation anywhere in the codebase.

```python
Patient(id, name, age, sex, created_at)

Prescription(id, patient_id, doctor_name, issued_date,
             source,          # 'manual' | 'ocr'
             status,          # 'draft' | 'confirmed'
             created_at)

Medicine(id, prescription_id,
         raw_text,            # original line — NEVER discarded
         name, normalized_name,
         dose_amount, dose_unit,
         schedule_code,       # '1-0-1', 'BD', 'SOS', ...
         slots,               # JSON ['morning','night']
         times,               # JSON ['08:30','20:30']
         food,                # 'before' | 'after' | 'any'
         duration_days,       # null = ongoing
         is_prn,              # True = as-needed, generates NO doses
         confidence,          # 0.0–1.0 overall
         field_confidence,    # JSON per field
         status)              # 'verified' | 'review' | 'needs_confirmation'

Dose(id, medicine_id, scheduled_at,
     state,                   # 'pending'|'taken'|'missed'|'skipped'|'snoozed'
     acted_at, reason, snooze_count)

SymptomCheck(id, patient_id, created_at,
             symptoms, answers,   # JSON
             severity,            # 'LOW'|'MODERATE'|'EMERGENCY'
             reasons,             # JSON — the rules that fired
             action, ruleset_version)

AuditLog(id, patient_id, at, actor, action, detail)
```

**Two invariants:**

1. `Dose` rows are generated **once** at confirmation and never recomputed. Adherence is then row counting — no date maths at render time, no drift.
2. `raw_text` is never discarded. When confidence is low, show the raw text instead of a guess.

---

## 7. Feature 1 — Shorthand engine

**This is the centrepiece. No repo in the survey has it. Build it first.**

A working, tested JavaScript implementation exists at `shorthand.js` with 26 passing tests in `shorthand.test.js`. **Port it to Python as `backend/shorthand.py` and port the tests to pytest.** Keep every behaviour identical. The spec below documents what it does.

### 7.1 Slot notation

Digits separated by hyphens. 3 parts = morning/afternoon/night. 4 parts = morning/afternoon/evening/night.

```
1-0-1    → morning 1, afternoon 0, night 1
1-1-1    → all three
0-1-0    → afternoon only
2-0-2    → 2 doses/day, 4 units/day
1-0-1-1  → morning, evening, night
½-0-½    → accept ½, ¼, ¾, 1/2, 0.5
```

If **any** part fails to parse (`1-?-1`), the whole code is invalid → `needs_confirmation`. Never partially guess.

### 7.2 Abbreviations

| Code | Meaning | Doses/day | Slots |
|---|---|---|---|
| OD / QD / SID | once daily | 1 | morning |
| OM / QAM | once, morning | 1 | morning |
| BD / BID | twice daily | 2 | morning, night |
| TDS / TID | three times daily | 3 | morning, afternoon, night |
| QID / QDS | four times daily | 4 | morning, afternoon, evening, night |
| HS / ON / NOCTE | at bedtime | 1 | bedtime |
| Q4H | every 4 hours | 6 | 06,10,14,18,22,02 |
| Q6H | every 6 hours | 4 | 06,12,18,00 |
| Q8H | every 8 hours | 3 | 06,14,22 |
| Q12H | every 12 hours | 2 | 08,20 |
| STAT | immediately, once | 1 | now |
| SOS / PRN | as needed | **0** | none |

Normalise before matching: strip dots and punctuation, uppercase. `b.d.` must equal `BD`.

**SOS and PRN generate zero `Dose` rows.** They appear on the dashboard as an as-needed card with a manual log button. Scheduling them is a correctness bug, and it is the most common one in this feature.

### 7.3 Food

`AC` / `a.c.` → before. `PC` / `p.c.` → after. Phrases: "after meals/food/breakfast", "before food", "empty stomach", "with food".
Default when absent: `any`. **Do not assume "after meals".**
`before` shifts every dose 30 minutes earlier.

### 7.4 Duration

`5 days`, `x5d`, `5/7` (5 days), `2/52` (2 weeks), `3/12` (3 months), `1 week`, `10 tabs` (days = tabs ÷ units-per-day, floored, flag if uneven), `continue` / `ongoing` → `duration_days = None`, schedule 30 days forward, mark ongoing. **Never invent a stop date.**

### 7.5 Default slot times

```
morning 08:30   afternoon 14:00   evening 18:00   night 20:30   bedtime 22:00
```
Configurable per patient.

### 7.6 Required test cases

```
"Tab Paracetamol 650mg 1-0-1 PC x5d"    → 2/day, after food, 5 days, confidence 1.0
"Cap Amoxicillin 500mg TDS AC 7 days"   → 3/day, before food, times 08:00/13:30/20:00
"Pantoprazole 40mg OD before breakfast" → 1/day, before food
"Tab Alprazolam 0.25mg HS 10 days"      → bedtime 22:00, 10 days
"Syrup Crocin 5ml SOS"                  → PRN, ZERO dose rows
"Tab Metformin 500mg 1-0-1 continue"    → ongoing, no end date
"Tab X 1-?-1"                           → confidence 0, no doses, flagged
"Tab Paracetamol 650mg"                 → confidence 0 (no frequency at all)
"Tab X 1-0-1 TDS"                       → conflict, confidence < 0.7
"Tab Y 1-1-1 10 tabs"                   → 3 days, flagged as uneven
"Tab X b.d. p.c."                       → 2/day, after food
```

---

## 8. Feature 1 (cont.) — Parser & confidence gate

### Parser

Line of text → `Medicine`. Extract form (`Tab`/`Cap`/`Syp`/`Inj`), name, dose amount + unit (`650mg`, `5ml`, `0.25mg`), then hand the remainder to the shorthand engine.

### Confidence — computed, never invented

```
name_confidence     = fuzzy match score vs local drug list (RapidFuzz)
schedule_confidence = structural parse score from the shorthand engine
overall             = min(name_confidence, schedule_confidence)
```

Extend `indian_drugs.py` to ~200 names (Dolo, Crocin, Augmentin, Pan-D, Azithral, Montek, Shelcal, Zerodol, Cetzine, Metrogyl…). This is the validation layer — **no OpenFDA network call during the demo**. Keep OpenFDA as an optional enrichment only.

### Thresholds

| Overall | Status | Behaviour |
|---|---|---|
| ≥ 0.85 | `verified` | green badge, schedulable |
| 0.70–0.85 | `review` | amber badge, schedulable, flagged for attention |
| < 0.70 | `needs_confirmation` | red badge, **blocked from scheduling** |

**Enforce the gate in the scheduler, not the UI.** A `needs_confirmation` medicine reaching the scheduler must raise an exception. A disabled button is not a safety control.

For `needs_confirmation`, display `raw_text` verbatim next to the best guess, clearly labelled unconfirmed. Never render a guessed dosage as if it were read.

---

## 9. Feature 2 — Scheduler & adherence

### Generation

On confirm, for each medicine that is not PRN and not `needs_confirmation`:

```python
for day in range(duration_days or 30):
    for time in medicine.times:
        Dose(scheduled_at=start + day at time, state='pending')
```

### Dose state machine

`pending → taken | missed | skipped | snoozed`

- **taken** — record `acted_at`
- **missed** — explicit, or auto-set by a sweep when `scheduled_at` is >2h past and still pending
- **skipped** — requires a reason string
- **snoozed** — push `scheduled_at` +15 min, increment `snooze_count`, max 3 then force back to pending

### Adherence

```
adherence = taken / (taken + missed + skipped) * 100
```

**Pending future doses are excluded from the denominator.** Otherwise day 1 of a 5-day course reads 20% and looks broken. This is the single most common bug in this feature.

Also compute: doses completed, doses missed, current day / total days, per-medicine breakdown.

### Reminders

Background worker (APScheduler for FastAPI). In-browser notifications are enough for the demo. Log every dispatch to `AuditLog`.

---

## 10. Feature 3 — Rule-based triage

Deterministic rules in JSON. **The severity decision is never LLM-generated.**

### Structure

```json
{
  "ruleset_version": "1.0.0",
  "symptoms": {
    "chest_pain": {
      "label": "Chest pain",
      "base": "MODERATE",
      "questions": [
        {"id": "difficulty_breathing", "text": "Are you having difficulty breathing?", "type": "boolean"},
        {"id": "fainting", "text": "Have you felt faint or passed out?", "type": "boolean"},
        {"id": "pain_radiating", "text": "Does the pain spread to your arm, neck or jaw?", "type": "boolean"}
      ]
    }
  },
  "rules": [
    {"if": {"symptom": "chest_pain", "answers": {"difficulty_breathing": true}},
     "then": "EMERGENCY",
     "because": "Chest pain with difficulty breathing"}
  ]
}
```

### The four rules that make it safe

1. **Answers escalate only.** A `false` answer means "no new information", never "less urgent than we thought". Implement as `severity = max(current, rule_result)` over an ordered enum. **Then write a test that walks every answer combination against every starting severity and asserts severity never decreases.** This is the most important test in the project — it is the only place where a wrong rule is dangerous rather than annoying.
2. **Stop asking once EMERGENCY.** That person needs an ambulance, not a questionnaire. Skip remaining questions, render the action immediately.
3. **Answers are optional.** The first result, before any follow-up, is a real result.
4. **Explain with the rules that fired.** Output the `because` strings in order. That is the explanation — not generated prose.

### Output

```
Severity: EMERGENCY
Why:
  • Chest pain reported
  • Difficulty breathing reported
Action: Seek emergency medical attention immediately. Call 112.
```

**Never state a diagnosis. Never name a condition.** Output is an urgency grade and an action, nothing else.

### Severity → action

| Severity | Action | Route |
|---|---|---|
| EMERGENCY | Seek emergency care now | red banner + `tel:112` link |
| MODERATE | Consultation within 24–48h | doctor booking link (the PS asks for this explicitly) |
| LOW | Continue monitoring, recheck if worse | log, return to dashboard |

Stamp every `SymptomCheck` with `ruleset_version` so old results still say which rules produced them.

### Minimum symptom set

chest_pain, breathlessness, fever, vomiting, nausea, rash, dizziness, abdominal_pain, headache, bleeding. Ten is enough — depth of rules beats breadth of symptoms.

---

## 11. Feature 4 — Report

One HTML page + print stylesheet → `window.print()` → PDF. PrescriptionReader already has ReportLab if a server-side PDF is preferred; the browser route is faster and needs no new dependency.

```
SMARTPOLI PATIENT CARE REPORT
Patient · Doctor · Treatment period · Generated on

PRESCRIPTION      medicine, dose, frequency in plain language, duration, food
ADHERENCE         completed, missed, percentage, per-medicine table
MISSED DOSES      date, time, medicine, reason if given
SYMPTOM HISTORY   date, symptoms, severity, action
TRIAGE HISTORY    with ruleset_version stamps
ALERTS            adherence <80%, any EMERGENCY triage, unconfirmed medicines
```

Print CSS: no colour wash behind black text, no page-break inside a table row, header band full width.

Footer on every report: *Generated by SmartPoli. Assistive tool, not a medical device. Not a diagnosis.*

---

## 12. Build order

Ordered by demo value per hour, so running out of time stops you at a working product rather than a broken one.

| # | Task | Est | Blocks |
|---|---|---|---|
| 1 | Port `shorthand.js` → `shorthand.py` + pytest | 1h | everything |
| 2 | Parser, drug list to 200 names, confidence | 2h | 3 |
| 3 | Review & confirm screen with the gate | 2h | 4 |
| 4 | Scheduler, dose states, adherence, dashboard | 3h | 6 |
| 5 | Triage engine + escalate-only test | 3h | 6 |
| 6 | Report + print CSS | 2h | — |
| 7 | Seed demo data + personas | 1h | — |
| 8 | OCR path (existing PrescriptionReader pipeline) | optional | — |

Steps 1–7 ≈ 14h and produce a complete, demoable product.

---

## 13. Safety requirements — non-negotiable

- No diagnosis claims anywhere in UI or output
- No fabricated medicine names or dosages — low confidence shows `raw_text`
- Low-confidence extraction blocked from scheduling, enforced in the scheduler
- Triage severity from rules only, never generative
- Every AI-assisted interpretation labelled as a suggestion requiring confirmation
- PRN/SOS never generates scheduled doses
- `AuditLog` row for every medication change and every triage result
- Keys in `.env`, `.env` in `.gitignore`, `.env.example` committed
- Input validation on every endpoint

---

## 14. Demo readiness

Patterns worth copying from the better surveyed projects:

- **Seed demo accounts** with a persona switcher. Judges should never fight a signup form.
- **Runs with zero API keys.** Deterministic paths must work with no `.env` at all. This is why the manual path is built first.
- **Screenshots in the README.**
- **An "Honest failures" section** naming what doesn't work. It reads as confidence, not weakness.
- **`SYSTEM_DESIGN.md`** with the architecture diagram.

### Claims discipline

Label every claim in the presentation:
**Implemented** (code exists and runs) · **Tested** (has assertions) · **Prototype** (demo path only) · **Planned** (not built).

Do not state an accuracy percentage unless measured on our own test set, and say what the set was. Do not claim doctor-approved, clinically validated, or any regulatory status.

---

## 15. THIRD_PARTY.md

Create and maintain it. Columns: Project · URL · Licence · What was studied · What was reused · What was independently implemented.

Record these findings:

- **No LICENSE file** (default copyright, code not reusable): PrescriptionReader (README claims MIT), smart-healthcare-triage, Ccare, HealthCare, handwritten-prescription-recognition, Swasthya-Saathi, Swasth Saathi, Swasthya-AI-Demo, LifeDoc, health-companion
- **Licence conflict**: TRxNSLATE — LICENSE is GPL-3.0, README badge says MIT
- **Proprietary**: ArogyaTrack
- **MIT**: ScanPlus, Swasthya-AI, AgoraCare, MedTracker, MedMed, Medical-Prescription-OCR, Medical-Prescription-Analyzer

Ideas adopted, credited as inspiration (ideas are not copyrightable):

- Escalate-only triage, ruleset versioning, append-only audit — *smart-healthcare-triage*
- Confidence ladder auto/review/manual — *handwritten-prescription-recognition*
- Staged pipeline with per-field confidence and graceful degradation — *PrescriptionReader*
- Dose state machine, snooze escalation, caregiver alert on consecutive misses — *Ccare*
- Deterministic fallback so the app runs without API keys; cron reminder worker — *HealthCare*

---

## 16. Definition of done

- [ ] `1-0-1 after meals` renders the four-line plain-language block from the PS
- [ ] `SOS` produces zero dose rows
- [ ] `1-?-1` produces confidence 0 and is blocked from scheduling
- [ ] Adherence on day 1 of a 5-day course is not 20%
- [ ] No triage answer combination can lower a severity (proven by test)
- [ ] EMERGENCY skips remaining questions
- [ ] Report includes prescription, adherence, symptoms and triage in one document
- [ ] App runs end to end with an empty `.env`
- [ ] No API keys in git history
- [ ] `THIRD_PARTY.md` complete
- [ ] No console errors, responsive to mobile
