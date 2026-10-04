# SmartPoli on Android — Trusted Web Activity (TWA)

A TWA is the SmartPoli website running full-screen inside Chrome, packaged as an
Android app. There is no second codebase: whatever the site does, the app does.

> **Status:** the website side is ready and verified (see "What was verified").
> **No Android build was produced** — this machine has no JDK / Android SDK, and
> signing needs *your* keystore. The steps below produce the release `.aab`.
> **Nothing has been published to Google Play.**

## What was verified (and how)

`python backend/tools/check_twa_readiness.py <url>` checks the live site.

| Target | Result |
|---|---|
| Local working tree | 21 pass, 2 warnings (HTTPS skipped on localhost; VAPID keys not set), 0 fail |
| `https://smartpoli.onrender.com` **as currently deployed** | 4 fail: no web-app manifest, no `assetlinks.json`, no push service worker, no theme colour — these arrive when this branch is deployed |

HTTPS on Render: yes (the deployed site answers over HTTPS). Re-run the check
against production after you deploy; do not trust this table for the deployed state.

## One-time setup

1. **Deploy this branch** to Render so `/manifest.webmanifest`, `/sw-push.js`,
   `/.well-known/assetlinks.json` and `/static/app-icon-*.png` exist.
2. **Pick the Android package id** — `app.smartpoli.twa` is a suggestion
   (`android/twa-manifest.json` → `packageId`). It is permanent once on Google Play.
3. **Confirm the host.** `smartpoli.onrender.com` is what the repo's Render config
   and the live site suggest; change `host` and the URLs in `twa-manifest.json`
   if you use a custom domain. (A custom domain is recommended: a TWA is tied to
   the origin forever.)
4. Install tooling: Node 18+, a JDK 17, and the Android SDK (Bubblewrap can
   download both JDK and SDK for you on first run).
   ```
   npm i -g @bubblewrap/cli
   ```

## Build

```
cd android
bubblewrap init --manifest=https://<host>/manifest.webmanifest     # or reuse twa-manifest.json
bubblewrap build
```

`bubblewrap build` asks for the keystore password. Output: `app-release-bundle.aab`
(upload to Play) and `app-release-signed.apk` (install on a phone for testing).

### Signing key (keep it OUT of git)

```
keytool -genkeypair -v -keystore smartpoli-release.keystore -alias smartpoli \
        -keyalg RSA -keysize 2048 -validity 10000
```

* `*.keystore`, `*.jks`, `keystore.properties`, `*.aab`, `*.apk` are in `.gitignore`.
* **Back the keystore up somewhere safe.** Lose it and you can never update the app
  (unless you enrol in Play App Signing — recommended: Google then holds the real
  signing key and yours becomes an upload key).
* The fingerprint you need for Digital Asset Links:
  ```
  keytool -list -v -keystore smartpoli-release.keystore -alias smartpoli | findstr SHA256
  ```
  If you use **Play App Signing**, use the *app signing key* SHA-256 shown in
  Play Console → Setup → App integrity (and optionally the upload key too).

## Digital Asset Links (what removes the browser address bar)

Set on Render, then redeploy:

```
SMARTPOLI_ANDROID_PACKAGE=app.smartpoli.twa
SMARTPOLI_ANDROID_SHA256=AA:BB:...:FF          # comma-separate several fingerprints
```

`https://<host>/.well-known/assetlinks.json` then serves the link. Verify:

```
python backend/tools/check_twa_readiness.py https://<host> \
       --package app.smartpoli.twa --sha256 AA:BB:...:FF
```

Until the package **and** fingerprint match, Chrome shows a URL bar over the app
(it falls back to a Custom Tab). That is the signal the link is wrong.

## Install on a test phone

```
adb install app-release-signed.apk
```
(or send the APK to the phone and open it with "install unknown apps" allowed).
Uninstall any older build first if the signature differs.

## Reminders: what to configure and what NOT to assume

Dose reminders are **server-sent Web Push** (VAPID) shown by `sw-push.js`.

1. `python backend/tools/generate_vapid_keys.py` → set `SMARTPOLI_VAPID_PRIVATE_KEY`,
   `SMARTPOLI_VAPID_PUBLIC_KEY`, `SMARTPOLI_VAPID_SUBJECT` on Render.
2. `enableNotifications: true` is already in `twa-manifest.json`
   (Chrome's notification delegation, so the permission belongs to the Android app).
3. In the app: **Settings → Dose reminders → Turn on reminders**, then **Send a test**.
4. **Sleeping server:** a free Render instance sleeps after ~15 min idle, which stops the
   in-process reminder timer. Create an external scheduler that calls
   ```
   POST https://<host>/internal/reminder-sweep      header:  X-Cron-Secret: <SMARTPOLI_CRON_SECRET>
   ```
   **every minute** (cron-job.org, a Render Cron Job, GitHub Actions…). It runs the sweep
   *and* keeps the instance awake. A paid always-on instance removes the need.

**Do not assume** notifications reach every phone. Android may delay or drop pushes for
apps under battery optimisation (notably Xiaomi/Oppo/Vivo/Samsung "app sleeping"). It
must be tested on real devices — see `docs/QA_CHECKLIST.md`. If a target device class
fails, the WhatsApp reminders (already built) are the fallback; a native alarm layer
would be a separate, non-TWA piece of work.

## Feature support inside the TWA (verify on a device — not yet tested)

| Feature | Expected to work? | Needs |
|---|---|---|
| Login / logout / navigation | yes (same Chrome engine) | — |
| Prescription / medicine photo (`<input type=file capture>`) | yes — opens the camera / gallery | Android camera app available |
| Voice assistant microphone | should, via Chrome | microphone permission prompt on first use; **test** |
| Push notifications | should, via notification delegation | permission, VAPID keys, battery settings; **test** |
| PDF / `.ics` downloads | should (Chrome downloads) | **test** the report PDF and calendar export |
| External links (DailyMed, CDSCO) | open in the browser/Custom Tab | — |
| WhatsApp button (`wa.me`) | opens WhatsApp if installed | — |
| Background execution | **not provided by a TWA** | reminders must come from the server |

## Releasing (not done)

Google Play needs, outside this repo: a developer account, store listing and screenshots,
a **privacy policy URL** (health data → required), the Data safety form, content rating,
and a medical-app declaration. Medical apps get extra review; see
`docs/LAUNCH_REPORT.md` for the regulatory/legal items to settle first.
