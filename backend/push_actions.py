"""
SmartPoli - one-tap actions inside a notification ("Remind now").

A notification's button runs in the phone's service worker, which holds no login. So the caregiver's "Missed dose"
notification carries a small signed token that can do exactly one thing: send ONE reminder to ONE patient on behalf of
ONE caregiver. It is not a session (a session token is refused here, and this one is refused as a session), it stops
working after six hours, and the server still checks on use that the caregiver is linked to that patient right now - so
removing the caregiver cancels every token already sitting in their notifications. The same 15-minute cooldown as the
button in the app applies.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from auth import JWT_ALGO, JWT_SECRET

TOKEN_HOURS = 6
PURPOSE = "nudge"


def make_nudge_token(caregiver_user_id: int, patient_id: int, dose_ids: Optional[list] = None) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"typ": PURPOSE, "u": int(caregiver_user_id), "p": int(patient_id),
                       "d": [int(i) for i in (dose_ids or [])][:10],
                       "iat": now, "exp": now + timedelta(hours=TOKEN_HOURS)}, JWT_SECRET, algorithm=JWT_ALGO)


def read_nudge_token(token: str) -> dict:
    """The token's claims, or ValueError (expired, tampered, or not a nudge token at all)."""
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO], options={"require": ["exp", "typ"]})
    except jwt.PyJWTError as e:
        raise ValueError("This reminder button has expired. Open SmartPoli to send it.") from e
    if claims.get("typ") != PURPOSE or "u" not in claims or "p" not in claims:
        raise ValueError("This reminder button has expired. Open SmartPoli to send it.")
    return claims
