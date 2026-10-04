"""
The Web Push sender with REAL VAPID keys (from tools/generate_vapid_keys.py's
format) and a REAL browser-style subscription (P-256 key + auth secret), so the
payload encryption and the VAPID signature are genuinely exercised. Only the
final HTTP POST to the push service is replaced, so nothing leaves the machine.
"""

import base64
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

import webpush_service  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
B64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731


@pytest.fixture()
def vapid_env(monkeypatch):
    out = subprocess.run([sys.executable, os.path.join(HERE, "..", "tools", "generate_vapid_keys.py")],
                         capture_output=True, text=True, check=True).stdout
    keys = dict(line.split("=", 1) for line in out.strip().splitlines())
    monkeypatch.setenv("SMARTPOLI_VAPID_PRIVATE_KEY", keys["SMARTPOLI_VAPID_PRIVATE_KEY"])
    monkeypatch.setenv("SMARTPOLI_VAPID_PUBLIC_KEY", keys["SMARTPOLI_VAPID_PUBLIC_KEY"])
    monkeypatch.setenv("SMARTPOLI_VAPID_SUBJECT", "mailto:test@example.com")
    return keys


@pytest.fixture()
def subscription():
    """What a browser's pushManager.subscribe() hands back."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": "https://push.example.test/send/abc", "p256dh": B64(pub), "auth": B64(os.urandom(16))}


class FakeResponse:
    def __init__(self, status):
        self.status_code = status
        self.reason = "fake"          # pywebpush reads .reason/.text when status > 202
        self.text = ""
        self.headers = {}


def patch_post(monkeypatch, status, seen):
    import requests

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        seen.append({"url": url, "data": data, "headers": headers})
        return FakeResponse(status)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests.Session, "post", lambda self, url, **kw: fake_post(url, **kw), raising=False)


def test_generated_keys_have_the_format_browsers_and_pywebpush_expect(vapid_env):
    pub = base64.urlsafe_b64decode(vapid_env["SMARTPOLI_VAPID_PUBLIC_KEY"] + "==")
    priv = base64.urlsafe_b64decode(vapid_env["SMARTPOLI_VAPID_PRIVATE_KEY"] + "==")
    assert len(pub) == 65 and pub[0] == 4          # uncompressed P-256 point, as pushManager.subscribe() wants
    assert len(priv) == 32
    assert webpush_service.is_configured() and webpush_service.public_key() == vapid_env["SMARTPOLI_VAPID_PUBLIC_KEY"]


def test_send_encrypts_signs_and_posts_to_the_subscription_endpoint(vapid_env, subscription, monkeypatch):
    seen = []
    patch_post(monkeypatch, 201, seen)
    status = webpush_service.send(subscription["endpoint"], subscription["p256dh"], subscription["auth"],
                                  {"title": "Time to take Telmisartan", "body": "due now", "kind": "due"})
    assert status == "sent"
    req = seen[0]
    assert req["url"] == subscription["endpoint"]
    assert "vapid" in req["headers"]["authorization"].lower() or "WebPush" in req["headers"]["authorization"]
    assert req["headers"]["content-encoding"] in ("aes128gcm", "aesgcm")
    assert isinstance(req["data"], (bytes, bytearray)) and b"Telmisartan" not in req["data"]    # body is encrypted


def test_expired_subscriptions_are_reported_gone(vapid_env, subscription, monkeypatch):
    for code in (404, 410):
        seen = []
        patch_post(monkeypatch, code, seen)
        assert webpush_service.send(subscription["endpoint"], subscription["p256dh"], subscription["auth"],
                                    {"title": "x", "body": "y"}) == "gone"


def test_transient_push_service_errors_are_retryable_not_gone(vapid_env, subscription, monkeypatch):
    for code in (429, 500, 503):
        seen = []
        patch_post(monkeypatch, code, seen)
        assert webpush_service.send(subscription["endpoint"], subscription["p256dh"], subscription["auth"],
                                    {"title": "x", "body": "y"}) == "error"


def test_without_keys_nothing_is_sent(monkeypatch, subscription):
    monkeypatch.delenv("SMARTPOLI_VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("SMARTPOLI_VAPID_PUBLIC_KEY", raising=False)
    assert not webpush_service.is_configured()
    assert webpush_service.send(subscription["endpoint"], subscription["p256dh"], subscription["auth"], {}) == "error"


def test_a_malformed_subscription_never_raises(vapid_env, monkeypatch):
    assert webpush_service.send("https://push.example.test/x", "not-a-key", "not-an-auth", {"title": "x"}) == "error"
