"""
SmartPoli - Web Push sender (RFC 8030 + VAPID).

Why Web Push and not a browser-only Notification: a page can only show a
notification while it is open. A dose reminder has to arrive when the app is
closed, so the SERVER sends it through the browser vendor's push service and
a service worker (static/sw-push.js) displays it.

Configure with environment variables (generate them with
tools/generate_vapid_keys.py):
    SMARTPOLI_VAPID_PRIVATE_KEY   secret
    SMARTPOLI_VAPID_PUBLIC_KEY    given to the browser to subscribe
    SMARTPOLI_VAPID_SUBJECT       "mailto:you@example.com"

Without keys, is_configured() is False and every caller skips push - the
rest of the app (and the WhatsApp reminders) are unaffected.

Delivery is best effort: Android may delay or drop pushes for apps under
battery optimisation. Verify on real devices (docs/ANDROID_TWA.md).
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(os.getenv("SMARTPOLI_VAPID_PRIVATE_KEY") and os.getenv("SMARTPOLI_VAPID_PUBLIC_KEY"))


def public_key() -> Optional[str]:
    return os.getenv("SMARTPOLI_VAPID_PUBLIC_KEY") or None


def send(endpoint: str, p256dh: str, auth: str, payload: dict) -> str:
    """Send one push. Returns 'sent', 'gone' (subscription expired - delete it)
    or 'error' (try again later / not configured)."""
    if not is_configured():
        return "error"
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush is not installed; push disabled.")
        return "error"
    try:
        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=json.dumps(payload),
            vapid_private_key=os.environ["SMARTPOLI_VAPID_PRIVATE_KEY"],
            vapid_claims={"sub": os.getenv("SMARTPOLI_VAPID_SUBJECT", "mailto:admin@example.com")},
            ttl=3600,
            headers={"Urgency": "high"},   # normal-priority pushes are held back while the phone is dozing
            timeout=10,
        )
        return "sent"
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):
            return "gone"
        logger.warning("Web Push failed (status=%s)", status)
        return "error"
    except Exception as e:  # noqa: BLE001 - a push failure must never break the sweep
        logger.warning("Web Push failed: %s", type(e).__name__)
        return "error"
