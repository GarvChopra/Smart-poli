# SmartPoli — manual QA checklist (web + Android)

Automated tests (`cd backend && python -m pytest -q tests`) cover logic and the HTTP API.
They do **not** cover real phones, real notifications, real cameras or real OCR. This list
is for those. Use a **test account and test data only** — never real patient records.

> **Never point a local server at the production database.** `.env` contains the live
> Supabase URL. For manual testing run with
> `SMARTPOLI_DATABASE_URL=sqlite:///./qa.db SMARTPOLI_DISABLE_SCHEDULER=1 python -m uvicorn main:app`
> and `python seed.py` with the same override (demo password `demo1234`).

Mark each line PASS / FAIL / N/A with the device, Android version and date.

## A. Web (desktop and phone browser)

**Account & session**
- [ ] Register, log in, log out. After logout the Back button does not show patient data.
- [ ] A token from before logout no longer works (open DevTools → copy the Authorization header → replay → 401).
- [ ] 9 wrong passwords in a row → "Too many failed attempts" (429); correct password works after the wait.
- [ ] Patient A cannot open Patient B's `/patients/<id>/dashboard`, `/regulatory`, `/schedule-conflicts` (403).
- [ ] Caregiver / doctor can view but not take/skip/reschedule a patient's doses.

**Prescription & medicine entry**
- [ ] Type a prescription → "Parsed medicines" shows four separate checks (Transcription / Name / Schedule / Official status) and the "not clinically appropriate" note.
- [ ] A line with `?` in the schedule is "needs_confirmation" and is **not** scheduled until corrected.
- [ ] Upload a prescription photo: JPG, PNG; a 12 MB file is refused with a clear message; a renamed `.txt` is refused.
- [ ] **Scan a medicine**: photograph a real strip/box. Candidates show with the text they were read from. Add without choosing a schedule → refused. Add with schedule → appears in review; nothing reminds until "Confirm & schedule".
- [ ] Scan a blurry photo → "No text could be read" or "couldn't match" — never a guess added silently.

**Doses & missed doses**
- [ ] Confirm a prescription at an awkward time (e.g. 4 pm for a 8 am/8 pm medicine): no instantly-"missed" 8 am dose appears.
- [ ] Take a dose twice quickly → recorded once, original time kept. Taking a missed dose late is recorded with its real time.
- [ ] Skip needs a reason. Snooze 3× then it returns to pending. Undo works only within 10 minutes.
- [ ] A dose >2 h overdue becomes **Missed** (check after waiting, or via the cron call), sits in "Missed recently", and the guidance popup opens once.
- [ ] Popup: states "never take a double dose", shows the label sentence + source link, and for warfarin/insulin shows **no** catch-up time.
- [ ] Try to mark a dose that is hours/days away: **no button** on the list, and if forced you get the popup "Not yet — don't take this now … from HH:MM". Tapping Take many times never takes more than the due dose.
- [ ] Prescription tab shows only **Scan medicine**, **Choose file**, **type it in**. Scan opens the camera; Choose file opens the phone's files.
- [ ] Settings → My daily routine: change the morning time, "Save & move my current medicines" → upcoming doses move; today's past/taken ones do not; repeat → "already match". Turn off the 10-min and follow-up reminders → they stop; the at-dose-time reminder still arrives.
- [ ] Care report: Export calendar and Download PDF both download a file (no error page); no "needs confirmation" rows or alerts.
- [ ] Dashboard shows **only today's** medicines (taken / missed / due / upcoming); no 30-day list; "Left today" tile. After midnight (or changing the phone date) the next day's doses appear as the new "today".
- [ ] Alerts: each one has a plain title, "What's the problem", "What to do", and (for timing) the gap bar; colours are amber / yellow / blue / green - nothing is emergency-red; the legend is visible. Ask someone who has not seen the app to say what each alert wants them to do.

**Timing safety**
- [ ] Levothyroxine + calcium at the same time → conflict card with the 4-hour rule and a DailyMed source link; "Move it" shifts only the later dose and is audited.
- [ ] Doxycycline + calcium → "Not resolved. Ask your pharmacist" (no invented time).
- [ ] Iron + calcium → no rule claimed; the page says unlisted pairs are **not** shown to be safe.
- [ ] Open each "Source:" link — the quoted sentence is really in that label.

**Official status (Safety center → Check official sources)**
- [ ] Each medicine lists IN and US checks separately with source links and dates.
- [ ] A known prohibited combination (e.g. "Nimesulide + Paracetamol") shows "Restricted status identified" with the notification number.
- [ ] Turn the network off → "Source unavailable … not a restriction" (never "restricted").
- [ ] An Indian brand name shows "Potential match", never "Verified record".

**Settings**
- [ ] Timezone shown matches the device. Change the device timezone, reload → it updates.
- [ ] WhatsApp card / dashboard button opens WhatsApp with the join message.

## B. Android app (TWA) — on at least 3 real phones (e.g. one Pixel/stock, one Samsung, one Xiaomi/Oppo/Vivo)

Record: model · Android version · battery-saver setting.

**Install & link**
- [ ] `adb install app-release-signed.apk` works; app icon is the pill icon, name "SmartPoli".
- [ ] App opens **without a URL bar** (Digital Asset Links verified). A URL bar = package/fingerprint mismatch.
- [ ] Splash colour and status-bar colour are the teal theme.
- [ ] Back button navigates within the app, then exits; it never lands on a blank page.

**Camera, files, downloads**
- [ ] Prescription "Choose File" offers Camera and Gallery; a fresh photo uploads and is read.
- [ ] Medicine **Scan** opens the rear camera; result flow works end to end.
- [ ] Care report PDF and the calendar `.ics` download and open.
- [ ] External links (DailyMed, CDSCO) open in the browser, then Back returns to the app.

**Voice**
- [ ] First use asks for microphone permission; after allowing, voice turns work; after denying, a clear message appears (no frozen UI).
- [ ] Interrupt a voice turn (switch apps / lock the screen) → returning shows a sane state, not a stuck "listening".

**Notifications (the critical part)**
- [ ] Settings → Dose reminders → Turn on reminders → Android permission prompt appears → allow.
- [ ] **Send a test** → arrives within ~10 s with the app open **and** with the app swiped away **and** with the screen off.
- [ ] Real dose 40 min away: **early heads-up (30 min)** arrives, then the **10-min** one, then **at dose time**, then **follow-up** if not marked - exactly one of each (no duplicates).
- [ ] Confirm a prescription containing levothyroxine + calcium: a **"Check your medicine timing"** notification arrives once (and not again on the next confirm).
- [ ] A finite course whose last dose is tomorrow: a **"Last dose of … tomorrow"** notification arrives after 09:00, once.
- [ ] Mark the dose taken → no follow-up arrives.
- [ ] Miss a dose (>2 h): ONE message arrives with "Don't take a double dose" and the next dose time.
- [ ] Leave the phone idle overnight, battery saver ON → note whether next morning's reminders arrive. If not: record the vendor's "app sleeping"/"auto-start" setting that fixes it.
- [ ] Log out → the phone no longer receives that patient's reminders.
- [ ] Airplane mode for 1 h then back online → delayed pushes arrive once, not repeatedly.

**Time zone**
- [ ] Set the phone to another timezone, open the app → Settings shows the new zone; reminders follow the new local time.

## C. Server / ops
- [ ] External cron calls `POST /internal/reminder-sweep` every minute (200 with the secret, 404 without). Instance stays awake.
- [ ] Render logs show "Reminder sweep: sent N" around dose times and no tracebacks.
- [ ] `python backend/tools/check_twa_readiness.py https://<host> --package … --sha256 …` → 0 failed.
- [ ] `python backend/tools/verify_rule_sources.py` → 0 failures (re-checks every medicine rule quote against the live FDA label). Run it monthly.
- [ ] Database backup taken before deploying a release; restore procedure tested once.
