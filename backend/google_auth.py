"""
SmartPoli - "Continue with Google".

The browser shows Google's own sign-in button and gets back a Google ID token (a JWT signed by Google). The server
never trusts what the browser says about the person: it checks the token's signature against Google's published keys,
that it was issued FOR THIS APP (audience = our client id), by Google, is not expired, and that Google has verified
the email. Only then is the email used to find or create the SmartPoli account.

Off unless SMARTPOLI_GOOGLE_CLIENT_ID is set (the OAuth client id from Google Cloud Console; it is public, not a
secret). With it unset the button is simply not shown, and email + password works as before.
"""

import os
from typing import Optional

import jwt

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

_jwks_client: Optional[jwt.PyJWKClient] = None


class GoogleTokenError(Exception):
    """The token is not a valid Google sign-in for this app."""


def client_id() -> str:
    return os.getenv("SMARTPOLI_GOOGLE_CLIENT_ID", "").strip()


def is_enabled() -> bool:
    return bool(client_id())


def _signing_key(token: str):
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(GOOGLE_CERTS_URL, cache_keys=True, lifespan=3600, timeout=10)
    return _jwks_client.get_signing_key_from_jwt(token).key


def verify_id_token(token: str, key_fn=None) -> dict:
    """Return {"email", "name", "sub"} for a valid Google ID token for this app, else raise GoogleTokenError.
    `key_fn(token)` gives the public key to check the signature with (tests pass their own)."""
    if not is_enabled():
        raise GoogleTokenError("Google sign-in is not switched on.")
    if not token or len(token) > 4096:
        raise GoogleTokenError("Missing Google sign-in.")
    try:
        key = (key_fn or _signing_key)(token)
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=client_id(),
                            options={"require": ["exp", "iat", "iss", "aud", "sub"]}, leeway=60)
    except Exception as e:  # noqa: BLE001 - any failure means "not a valid sign-in"; the reason is not shown to callers
        raise GoogleTokenError("That Google sign-in could not be verified.") from e
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise GoogleTokenError("That sign-in did not come from Google.")
    email = (claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") not in (True, "true"):
        raise GoogleTokenError("Your Google account's email is not verified.")
    return {"email": email, "name": (claims.get("name") or email.split("@")[0])[:100], "sub": str(claims["sub"])}
