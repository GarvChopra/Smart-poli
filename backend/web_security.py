"""
Web-layer hardening that does not belong to any one feature:

  * security headers on every response (CSP, no-sniff, no framing, referrer, permissions, HSTS)
  * a small per-IP rate limiter for endpoints that need no login but cost money or can be abused (LLM calls, triage)
  * one 404 page for unknown browser URLs (JSON stays JSON for the API)

Everything here is in-memory and per-process, which is what a single Render instance needs. If the app ever runs on
several instances, move the limiter to a shared store.
"""

import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# The app's own scripts/styles plus Google Fonts (the only third party it uses). Inline script/style stay allowed
# because the pages use inline event handlers and style attributes; everything else is locked to this origin.
_CSP = ("default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "connect-src 'self'; "
        "worker-src 'self'; manifest-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


_STATIC_ASSET_EXT = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".woff", ".woff2")


def cache_policy(path: str, versioned: bool) -> str:
    """API answers are private (never cached). The pages carry a version in every script/style URL (?v=...), so those
    files can be cached for a year and a new deploy still reaches every phone at once (the version changes). A script or
    style requested WITHOUT a version is re-checked each time; images may be kept for a day."""
    if not path.startswith("/static/"):
        return "no-store"
    if versioned:
        return "public, max-age=31536000, immutable"
    return "public, max-age=86400" if path.lower().endswith(_STATIC_ASSET_EXT) else "no-cache"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("Content-Security-Policy", _CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=(), payment=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        # Private pages and API answers must not be kept by shared caches or the back button.
        if "cache-control" not in h:
            h["Cache-Control"] = cache_policy(request.url.path, "v" in request.query_params)
        return response


# ---------------------------------------------------------------- rate limiting

_hits: dict = defaultdict(deque)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:                                   # Render puts the real client first
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(name: str, limit: int, window_seconds: int):
    """FastAPI dependency: at most `limit` calls per `window_seconds` per client IP for the named bucket."""
    def dep(request: Request):
        if os.getenv("SMARTPOLI_DISABLE_RATE_LIMIT") == "1":
            return
        now = time.monotonic()
        q = _hits[(name, client_ip(request))]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "Too many requests. Please wait a moment and try again.",
                                headers={"Retry-After": str(window_seconds)})
        q.append(now)
        if len(_hits) > 5000:                 # keep memory bounded
            for k in [k for k, v in _hits.items() if not v][:1000]:
                _hits.pop(k, None)
    return dep


# ---------------------------------------------------------------- 404

NOT_FOUND_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><meta name="robots" content="noindex">
<title>Page not found — SmartPoli</title>
<style>body{font-family:-apple-system,system-ui,"Segoe UI",Roboto,sans-serif;background:#F4F7F6;color:#1a1a1a;margin:0;
display:grid;place-items:center;min-height:100vh;text-align:center;padding:24px}
h1{font-size:56px;margin:0;color:#0F8A70}p{color:#555;font-size:17px}
a{display:inline-block;margin-top:12px;background:#0F8A70;color:#fff;text-decoration:none;font-weight:700;padding:12px 22px;border-radius:12px}</style>
</head><body><main><h1>404</h1><p>We couldn't find that page.</p><a href="/">Go to SmartPoli</a></main></body></html>"""


def wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "") and not request.url.path.startswith(
        ("/auth", "/patients", "/doses", "/medicines", "/prescriptions", "/triage", "/doctor/", "/caregiver/", "/api"))


async def not_found_handler(request: Request, exc):
    if exc.status_code == 404 and wants_html(request):
        return HTMLResponse(NOT_FOUND_HTML, status_code=404)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))
