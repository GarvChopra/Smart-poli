# WhatsApp bot setup (Twilio)

The bot itself (`backend/whatsapp_bot.py`, `backend/whatsapp_router.py`) is
fully implemented and tested (`backend/tests/test_whatsapp_bot.py`). What's
left is account setup on Twilio's side — steps only you can do, since they
need your own phone number and (for a real WhatsApp number, not the free
sandbox) a Twilio account with billing enabled.

## 1. Twilio account + WhatsApp Sandbox (free, no billing needed for testing)

1. Sign up at twilio.com and open the **Console**.
2. Go to **Messaging → Try it out → Send a WhatsApp message** to reach the
   WhatsApp Sandbox. It gives you a sandbox number (usually
   `+1 415 523 8886`) and a join code like `join right-elephant`.
3. From your own WhatsApp, send that join code to that number. This links
   *your* phone to the sandbox for testing — anyone who wants to test the
   bot has to do this same join step first (sandbox limitation, not
   SmartPoli's).
4. Copy your **Account SID** and **Auth Token** from the Console dashboard.

## 2. Expose your local server publicly (Twilio can't reach 127.0.0.1)

Use any tunnel tool, e.g. [ngrok](https://ngrok.com):

```
ngrok http 8000
```

Copy the `https://xxxx.ngrok-free.app` URL it prints.

## 3. Set the environment variables

In `.env` (project root):

```
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
SMARTPOLI_PUBLIC_BASE_URL=https://xxxx.ngrok-free.app
SMARTPOLI_SKIP_TWILIO_SIGNATURE=0
```

Restart the server after editing `.env`.

## 4. Point the sandbox at your webhook

Back in the Twilio Console's WhatsApp Sandbox settings, set **"When a
message comes in"** to:

```
https://xxxx.ngrok-free.app/whatsapp/webhook
```

Method: `POST`.

## 5. Test it

Message the sandbox number from WhatsApp. You should get the welcome
message immediately. Send your name, then try:

- A photo of a prescription (or any typed text — the OCR path needs a real
  photo, but everything else works with plain text)
- `NOTIFY YES` / `NOTIFY NO`
- `EMERGENCY`
- `TODAY`
- `HELP`

## Going to production (a real WhatsApp Business number)

The sandbox is for testing only — anyone using it has to send the join
code first, and Twilio may reset the sandbox number periodically. For a
real, always-on WhatsApp number:

1. In the Twilio Console, request a **WhatsApp-enabled Sender** (requires
   Meta Business verification — this takes Twilio/Meta a few days and
   needs a real business).
2. Point that number's webhook at your deployed server's
   `/whatsapp/webhook` (not ngrok — a real, stable HTTPS URL).
3. Set `TWILIO_WHATSAPP_NUMBER` to that number and leave
   `SMARTPOLI_SKIP_TWILIO_SIGNATURE=0` always.

## What SmartPoli does NOT need from Twilio

No SDK dependency was added — signature verification and outbound sends
are both plain `httpx` calls against Twilio's documented REST API
(`whatsapp_router.py`, `whatsapp_bot.send_whatsapp_message`). If Twilio
isn't configured at all, every other SmartPoli feature (web app, OCR,
triage, reports, doctor/caregiver dashboards) is completely unaffected —
the webhook just returns `503` instead of the server failing to start.
