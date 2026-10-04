# How SmartPoli decides *when* a dose is — design notes and sources

## The problem this solves

* Every patient used to get the **same fixed clock times** (08:30 / 14:00 / 18:00 / 20:30 / 22:00), and every
  medicine in a slot landed on the **same minute**.
* The prescription says *morning / night* and *before / after food*, not a clock time.
* Patients left to space doses themselves do it badly. A study of how people read dosing instructions
  (*Archives of Internal Medicine*, 2011, discussed in
  [PMC3968427](https://pmc.ncbi.nlm.nih.gov/articles/PMC3968427/)) found that for twice-daily medicines people
  averaged 10.3 hours between doses, with gaps from as little as 1 to as many as 18 hours, and for
  three-times-daily medicines the intervals ranged from 1 to 13 hours. *(Verified by reading the article.)*

So the app builds the schedule for the patient, around **their own routine**, and keeps medicines that must be
kept apart apart (see `MEDICATION_RULES.md`).

## What the app does

| Piece | Behaviour |
|---|---|
| **Daily routine** (Settings) | The patient sets when their morning / afternoon / evening / night / bedtime doses are. Defaults are the old standard times, so nothing changes until they save. |
| **Before / after food** | After food (or no instruction) → the routine time for the slot. **Before food (AC) → 30 minutes earlier.** |
| **Exact times / "every N hours"** | `Q8H`, `Q12H`, explicit clock times keep exactly what was written. They never follow the routine. |
| **As-needed (PRN)** | No scheduled times. |
| **Changing the routine** | "Save & move my current medicines" moves only **future, untouched** doses. Doses already taken/missed/skipped, and doses moved for a verified spacing rule, are never touched. Repeating it changes nothing. |
| **Medicines that must be apart** | Not decided by the routine. Label-based rules (FDA) detect them, propose a later time for the later dose, and the patient accepts one by one or **"Apply all"**. No rule → nothing claimed. |
| **Reminders** | Early heads-up (default 30 min, 0–120), a last heads-up at 10 min (can be turned off), at the dose time (always), a follow-up 15 min later if not marked (can be turned off), one message if missed. |
| **"Take" button** | A dose can be marked taken only from **2 hours before** its time until **12 hours after** (the same window the voice assistant already used). Earlier → a popup says it is too early and when it opens. The server enforces it, not just the screen. |

## What is a product default and what is evidence

These are **product choices**, adjustable by the patient — not medical claims:
the default times, the 30-minute before-food shift, the 2 h / 12 h take window, the 30 / 10 / 15-minute reminder offsets.

What the research and official sources support (read as general background, *not* as dosing rules):

* **Doses should be spread through waking hours, not at 3 identical-looking times** — and nobody needs to be woken at
  night for a "three times a day" medicine. An NHS trust leaflet gives the example *8 am, 3 pm and 10 pm*
  ([Buckinghamshire Healthcare NHS Trust – Antibiotics](https://www.buckshealthcare.nhs.uk/pifs/antibiotics/)).
  *(Seen in a search snippet; the page itself returned 403 to automated access, so the wording is not verified verbatim.)*
* **"Three times a day" is not the same as "every 8 hours."** Where a medicine says *every N hours* it is kept exactly
  (`Q8H`, `Q12H`); where it says *times a day* it is spread over the day. *(Common clinical convention; secondary sources only.)*
* **"Empty stomach" is roughly an hour before a meal or two hours after** — NHS and NIH-hosted pages
  ([NHS: Why must some medicines be taken on an empty stomach?](https://www.nhs.uk/common-health-questions/medicines/why-must-some-medicines-be-taken-on-an-empty-stomach/),
  [NIA: Taking medicines safely](https://www.nia.nih.gov/health/medicines-common-questions-answered))
  *(search snippets; the NHS page itself did not load for automated fetch).* Which is why the 30-minute
  before-food shift is a **convenience default, and a medicine whose label says something stricter keeps the label's rule**
  (e.g. levothyroxine: *"one-half to one hour before breakfast"* in its FDA label).
* **AC / PC** (*ante cibum* / *post cibum*) mean before / after meals.

## Limits to be honest about

* The routine does not know a patient's real meals or what is in them, only the times they type.
* "After food" is placed at the routine time (about when a meal ends); it does not verify they ate.
* Staggering **every** medicine by a few minutes was deliberately **not** done: it adds notifications and effort without
  a safety reason. Separation is applied where a label gives a reason (and the pairs it covers are listed in
  `MEDICATION_RULES.md`). Pairs with no rule are *unverified*, not safe.
* Nothing here has been reviewed by a pharmacist or doctor.


## Gaps checked at the moment of "Mark taken" (AI-assisted, label-grounded)

The tap is checked against what the patient has **actually taken**, not just the schedule.

| Check | Where the number comes from | If it fails |
|---|---|---|
| **Window** | product rule: from 2 h before to 12 h after the dose time | popup, nothing recorded |
| **Same medicine** (e.g. Metformin again too soon) | floor = half the shortest interval the prescription implies (BD → 6 h). An official label or an AI estimate can **raise** it, but never above 3/4 of that interval, so a dose that is properly due is never blocked | popup with the time it is OK; label/schedule gaps cannot be overridden, AI-estimated ones can ("I already took it") |
| **Other medicines** (e.g. Ciprofloxacin after Calcium) | 1. curated FDA-label rules (`safety_rules.json`), which always win and are asymmetric; 2. otherwise an AI answer grounded in the labels' interaction text | popup with the time it is OK and "I already took it" (recorded as an override in the audit log) |

**How the AI is used** (`gap_ai.py`): the label text comes from the government's **openFDA** API; a Groq LLM reads it and
answers in JSON. Each answer is graded: **"label"** only if the quoted sentence really is in the label text *and* contains
the number of hours; everything else is an **"AI estimate"** and is shown as such ("ask your pharmacist"). Out-of-range or
malformed output is discarded. Only generic medicine names are sent to the LLM. Answers (including "no gap needed") are cached in
the database because the free LLM tier rate-limits quickly; the cache is filled in the background after a prescription is
confirmed and when the dashboard opens, and a tap is answered from the cache only. Any failure simply means no AI answer - the schedule
floor and the curated rules still apply. `SMARTPOLI_AI_GAPS=0` switches the AI off.

Observed on real data (2026-10-04, openFDA + Groq `qwen/qwen3.8-27b`): levothyroxine + calcium → 4 h graded *label*;
metformin → 12 h graded *AI estimate* (its quote said "3 times a day", not 12 h); calcium + iron → 2 h *AI estimate*;
metformin + telmisartan → no gap. In the first trial the AI said 2 h for ciprofloxacin + iron where the FDA label
says "2 hours before **or 6 hours after**" - which is why a curated label rule is always consulted first and AI never loosens it.
**Not reviewed by a pharmacist; the AI's general-knowledge answers can be wrong.**
