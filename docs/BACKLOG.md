# SmartPoli backlog (to do after the security work is finished)

Reported by Garv on 2026-10-07. Not started yet - do these once the security checklist work is done and pushed.

## 1. Missed-dose popup glitches
- **No popup for a dose that is due.** Example: Creatine was due at 18:30 and had not been taken; when the app was opened there was no popup.
  Find out why the due / missed-dose popup did not appear on app open (is it only shown at certain moments? does it depend on a push or a timer?),
  and make it show every time the app is opened while a dose is due or missed.
- **Missed-dose popup UI.** The popup looks bad: redesign it (calm, compact, clear buttons: Take now / Skip), in line with the rest of the app.

## 2. Removed medicines still show in "Today's medicines"
- Added 3 medicines, removed them, added 2 others. The removed ones are gone from the dashboard summary, but still appear in the
  **Today's medicines** list.
- Likely cause to check first: removal deletes the medicine but its doses (or a cached list in the page) are still read by the Today list.
  Make the Today list ignore removed medicines and refresh after a removal; add a test (add -> remove -> Today list does not contain it).

## 3. "How SmartPoli works" tutorial for beginners
- A very simple, friendly walkthrough that explains what the app does for you, in plain words, for first-time users:
  - we remind you when a dose is due
  - we notice missed doses
  - we warn when two medicines should not be taken close together, and suggest a gap
  - we check medicines against trusted sources (and AI checks, labelled as estimates)
  - caregivers can follow along and leave notes
- Shown once after the first-login details popup, and reachable again from the menu. Short screens, big text, skip at any time, no jargon.

## Also still open
- In-app "Delete my account" (needed for Google Play) and a data-retention policy.
- Per-user (not only per-IP) quota for AI calls.
- Make the AI checks fast under Groq's rate limit (in-memory controller, cache, seed file) - plan approved in principle, not built.
- GitHub / Supabase / Render / Cloudflare settings from the security checklist (see the security reply in chat).
