"""
Check that a deployed SmartPoli site is ready to be wrapped as an Android
Trusted Web Activity (TWA).

    python tools/check_twa_readiness.py https://your-domain
    python tools/check_twa_readiness.py https://your-domain --package app.smartpoli.twa --sha256 AA:BB:...
    python tools/check_twa_readiness.py http://127.0.0.1:8000 --allow-http     # local, HTTPS check skipped

It reads the live site and reports PASS / FAIL / WARN for each requirement,
and exits 1 if anything FAILED. It does not prove the app works on a phone -
notifications, microphone and file picking still need the manual device pass
in docs/QA_CHECKLIST.md.
"""

import argparse
import json
import re
import sys
from urllib.parse import urljoin, urlparse

import httpx

results = []


def check(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"{status:5} {name}" + (f" - {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--package", help="Android applicationId expected in assetlinks.json")
    ap.add_argument("--sha256", help="Release signing certificate SHA-256 fingerprint expected in assetlinks.json")
    ap.add_argument("--allow-http", action="store_true", help="skip the HTTPS requirement (local testing only)")
    args = ap.parse_args()

    base = args.base_url.rstrip("/") + "/"
    client = httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "smartpoli-twa-check"})

    if urlparse(base).scheme == "https":
        check("PASS", "site is served over HTTPS")
    elif args.allow_http:
        check("WARN", "HTTPS check skipped (--allow-http)", "a TWA will not verify against http://")
    else:
        check("FAIL", "site is served over HTTPS", "TWA requires HTTPS")

    try:
        home = client.get(base)
        check("PASS" if home.status_code == 200 else "FAIL", "home page loads", f"HTTP {home.status_code}")
    except httpx.HTTPError as e:
        check("FAIL", "home page loads", type(e).__name__)
        return 1

    m = re.search(r'<link[^>]+rel=["\']manifest["\'][^>]+href=["\']([^"\']+)', home.text)
    check("PASS" if m else "FAIL", "home page links a web app manifest", m.group(1) if m else "no <link rel=manifest>")
    manifest_url = urljoin(base, m.group(1)) if m else urljoin(base, "manifest.webmanifest")
    check("PASS" if re.search(r'name=["\']theme-color["\']', home.text) else "WARN", "theme-color meta tag present")

    manifest = None
    try:
        r = client.get(manifest_url)
        if r.status_code == 200:
            manifest = r.json()
            check("PASS", "manifest is reachable JSON", manifest_url)
        else:
            check("FAIL", "manifest is reachable JSON", f"HTTP {r.status_code} at {manifest_url}")
    except (httpx.HTTPError, ValueError) as e:
        check("FAIL", "manifest is reachable JSON", type(e).__name__)

    if manifest:
        check("PASS" if manifest.get("name") else "FAIL", "manifest has a name", manifest.get("name", ""))
        check("PASS" if manifest.get("short_name") else "WARN", "manifest has a short_name")
        check("PASS" if manifest.get("display") in ("standalone", "fullscreen") else "FAIL",
              "manifest display is standalone/fullscreen", str(manifest.get("display")))
        check("PASS" if manifest.get("start_url") else "FAIL", "manifest has start_url", str(manifest.get("start_url")))
        scope = manifest.get("scope", "/")
        start = manifest.get("start_url", "/")
        in_scope = urljoin(base, start).startswith(urljoin(base, scope))
        check("PASS" if in_scope else "FAIL", "start_url is inside scope", f"start_url={start} scope={scope}")
        check("PASS" if re.fullmatch(r"#[0-9a-fA-F]{6}", manifest.get("theme_color", "")) else "FAIL", "theme_color is a hex colour")
        check("PASS" if re.fullmatch(r"#[0-9a-fA-F]{6}", manifest.get("background_color", "")) else "FAIL", "background_color is a hex colour")
        icons = manifest.get("icons", [])
        sizes = {(i.get("sizes"), i.get("purpose", "any")) for i in icons}
        check("PASS" if any(s == "512x512" and "any" in p for s, p in sizes) else "FAIL", "has a 512x512 icon")
        check("PASS" if any(s == "192x192" for s, _ in sizes) else "FAIL", "has a 192x192 icon")
        check("PASS" if any("maskable" in p for _, p in sizes) else "WARN", "has a maskable icon (Android adaptive icons)")
        for icon in icons:
            try:
                ir = client.get(urljoin(manifest_url, icon["src"]))
                ok = ir.status_code == 200 and ir.headers.get("content-type", "").startswith("image/")
                check("PASS" if ok else "FAIL", f"icon is served: {icon['src']}", f"HTTP {ir.status_code}")
            except httpx.HTTPError as e:
                check("FAIL", f"icon is served: {icon['src']}", type(e).__name__)

    # Digital Asset Links
    al_url = urljoin(base, ".well-known/assetlinks.json")
    try:
        r = client.get(al_url)
        data = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        data = None
    if not data:
        check("FAIL", "assetlinks.json lists the Android app", "empty or missing - set SMARTPOLI_ANDROID_PACKAGE / SMARTPOLI_ANDROID_SHA256")
    else:
        targets = [t["target"] for t in data if t.get("target", {}).get("namespace") == "android_app"]
        check("PASS" if targets else "FAIL", "assetlinks.json has an android_app target")
        if targets and args.package:
            hit = [t for t in targets if t.get("package_name") == args.package]
            check("PASS" if hit else "FAIL", f"assetlinks.json package matches {args.package}")
            if hit and args.sha256:
                want = args.sha256.upper()
                have = [f.upper() for t in hit for f in t.get("sha256_cert_fingerprints", [])]
                check("PASS" if want in have else "FAIL", "assetlinks.json fingerprint matches the signing key")
        elif targets:
            check("WARN", "package / fingerprint not compared", "pass --package and --sha256 to compare")

    # Service worker for push
    try:
        sw = client.get(urljoin(base, "sw-push.js"))
        check("PASS" if sw.status_code == 200 and "push" in sw.text else "FAIL", "push service worker is served", f"HTTP {sw.status_code}")
    except httpx.HTTPError as e:
        check("FAIL", "push service worker is served", type(e).__name__)

    try:
        pk = client.get(urljoin(base, "push/public-key")).json()
        check("PASS" if pk.get("configured") else "WARN", "server has VAPID keys (push reminders can be sent)",
              "" if pk.get("configured") else "run tools/generate_vapid_keys.py and set the SMARTPOLI_VAPID_* variables")
    except (httpx.HTTPError, ValueError):
        check("WARN", "could not read /push/public-key")

    fails = [r for r in results if r[0] == "FAIL"]
    warns = [r for r in results if r[0] == "WARN"]
    print(f"\n{len(results)} checks: {len(results) - len(fails) - len(warns)} passed, {len(warns)} warnings, {len(fails)} failed.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
