"""Configured destinations for links that users share outside SmartPoli."""

import os
import re
from urllib.parse import urlsplit

LOCAL_URL = "http://127.0.0.1:8000"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_HOSTNAME = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*")


def public_base_url() -> str:
    """Use only trusted environment settings to build shareable public URLs."""
    secure = os.getenv("SMARTPOLI_REQUIRE_SECURE_CONFIG") == "1"
    configured = next((value for name in ("SMARTPOLI_PUBLIC_URL", "SMARTPOLI_PUBLIC_BASE_URL")
                       if (value := os.getenv(name, "").strip())), "")
    if not configured:
        if secure:
            raise ValueError("Set SMARTPOLI_PUBLIC_BASE_URL to the app's HTTPS origin.")
        return LOCAL_URL

    origin = configured.rstrip("/")
    try:
        parsed = urlsplit(origin)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise ValueError("Set SMARTPOLI_PUBLIC_BASE_URL to a valid app origin.") from None
    valid_scheme = parsed.scheme == "https" or (
        not secure and parsed.scheme == "http" and host in _LOCAL_HOSTS
    )
    if (
        not valid_scheme or not host or (host != "::1" and not _HOSTNAME.fullmatch(host))
        or (secure and host in _LOCAL_HOSTS)
        or port == 0 or "@" in parsed.netloc or parsed.path or parsed.query or parsed.fragment
        or re.search(r"[\s\\\x00-\x1f?#]", origin)
    ):
        raise ValueError("Set SMARTPOLI_PUBLIC_BASE_URL to the app's HTTPS origin, without a path.")
    return origin


def allowed_hosts() -> list[str]:
    """Exact hostnames, with no schemes, paths, ports, redirects, or wildcards."""
    hosts = [host.strip().lower() for host in os.getenv("SMARTPOLI_ALLOWED_HOSTS", "").split(",") if host.strip()]
    if any(not _HOSTNAME.fullmatch(host) for host in hosts):
        raise ValueError("SMARTPOLI_ALLOWED_HOSTS must contain only exact hostnames.")
    return hosts
