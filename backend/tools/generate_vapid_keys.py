"""
Generate a VAPID key pair for Web Push.

    python tools/generate_vapid_keys.py

Prints two lines to paste into your environment (Render dashboard / .env):
    SMARTPOLI_VAPID_PRIVATE_KEY=...   (secret - never commit it)
    SMARTPOLI_VAPID_PUBLIC_KEY=...    (safe to expose; the browser needs it)
Also set SMARTPOLI_VAPID_SUBJECT to "mailto:you@example.com".
Changing the key pair later invalidates every existing push subscription.
"""
import base64

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


v = Vapid02()
v.generate_keys()
private_raw = v.private_key.private_numbers().private_value.to_bytes(32, "big")
public_raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
print(f"SMARTPOLI_VAPID_PRIVATE_KEY={b64url(private_raw)}")
print(f"SMARTPOLI_VAPID_PUBLIC_KEY={b64url(public_raw)}")
print("SMARTPOLI_VAPID_SUBJECT=mailto:you@example.com")
