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


_derived: dict = {}


def _public_key_of_private() -> Optional[str]:
    """The public key that belongs to the configured PRIVATE key, worked out the same way pywebpush reads it.
    Phones must subscribe with exactly this key: if SMARTPOLI_VAPID_PUBLIC_KEY was set from a different pair, every
    phone that subscribes gets a subscription our server can never send to (the push service answers 403) - reminders
    look 'on' and silently never arrive."""
    priv = os.getenv("SMARTPOLI_VAPID_PRIVATE_KEY") or ""
    if priv not in _derived:
        _derived[priv] = None
        if priv:
            try:
                import base64
                from cryptography.hazmat.primitives import serialization
                from py_vapid import Vapid
                raw = Vapid.from_string(priv).public_key.public_bytes(serialization.Encoding.X962,
                                                                      serialization.PublicFormat.UncompressedPoint)
                _derived[priv] = base64.urlsafe_b64encode(raw).decode().rstrip("=")
            except Exception as e:  # noqa: BLE001 - fall back to the configured public key
                logger.warning("Could not work out the VAPID public key from the private key: %s", type(e).__name__)
        configured = (os.getenv("SMARTPOLI_VAPID_PUBLIC_KEY") or "").rstrip("=")
        if _derived[priv] and configured and configured != _derived[priv]:
            logger.error("SMARTPOLI_VAPID_PUBLIC_KEY does not belong to SMARTPOLI_VAPID_PRIVATE_KEY - using the public "
                         "key of the private key, so new subscriptions work. Fix the variable on the server.")
    return _derived[priv]


def public_key() -> Optional[str]:
    return _public_key_of_private() or os.getenv("SMARTPOLI_VAPID_PUBLIC_KEY") or None


def keys_match() -> Optional[bool]:
    """False when the configured public key is not the private key's."""
    derived = _public_key_of_private()
    configured = (os.getenv("SMARTPOLI_VAPID_PUBLIC_KEY") or "").rstrip("=")
    return None if not derived or not configured else derived == configured


_last_failure: dict = {}


def last_failure(endpoint: str) -> Optional[tuple]:
    """(HTTP status, the push service's reason) of the last failed send to this endpoint, if any."""
    return _last_failure.get(endpoint)


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
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        reason = " ".join((getattr(resp, "text", "") or "").split())[:120]
        _last_failure[endpoint] = (status, reason)
        if status in (404, 410):
            return "gone"
        if status == 403:
            # The push service refuses OUR key for this subscription (it was made with another public key). It will
            # never accept it: treat it like an expired one, so the phone is asked for a fresh subscription.
            logger.warning("Web Push refused (403): %s", reason)
            return "gone"
        logger.warning("Web Push failed (status=%s): %s", status, reason)
        return "error"
    except Exception as e:  # noqa: BLE001 - a push failure must never break the sweep
        _last_failure[endpoint] = (None, type(e).__name__)
        logger.warning("Web Push failed: %s", type(e).__name__)
        return "error"


_noted: dict = {}


def note_failure(db, patient_id: int, endpoint: str, status: str) -> None:
    """Keep WHY a push to a patient's phone did not go through in the audit log (status code and the push service's
    reason - no message text, no endpoint), at most once per phone per 6 hours, so 'notifications are on but nothing
    arrives' can be diagnosed from the database."""
    import time
    from db import endpoint_hash, log_audit
    key = endpoint_hash(endpoint)
    if time.time() - _noted.get(key, 0) < 6 * 3600:
        return
    _noted[key] = time.time()
    code, reason = last_failure(endpoint) or (None, "")
    what = "expired or refused" if status == "gone" else "failed"
    try:
        log_audit(db, patient_id, "system", "push_delivery_failed", f"{what}: status={code} {reason}"[:240])
    except Exception:  # noqa: BLE001 - diagnosis must never break delivery
        db.rollback()
