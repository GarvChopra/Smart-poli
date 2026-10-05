"""Route table, static-file allow-list, security headers, 404 page, rate limits."""
import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path", ["/", "/login", "/voice", "/install", "/caregiver", "/doctor"])
def test_pages_live_at_clean_urls(client, path):
    r = client.get(path)
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


@pytest.mark.parametrize("old,new", [("index", "/"), ("login", "/login"), ("voice", "/voice"), ("doctor", "/doctor")])
def test_old_static_html_urls_redirect(client, old, new):
    r = client.get(f"/static/{old}.html?x=1", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == new + "?x=1"


def test_unknown_static_html_is_404(client):
    assert client.get("/static/secrets.html").status_code == 404


@pytest.mark.parametrize("path", ["/static/../main.py", "/static/%2e%2e/main.py", "/static/..%2fmain.py",
                                  "/static/", "/static", "/static/.env", "/static/app.js.map", "/static/style.css.bak",
                                  "/main.py", "/smartpoli.db", "/.env", "/admin", "/trial", "/css", "/dashboard", "/patient"])
def test_no_source_or_unknown_route_is_served(client, path):
    r = client.get(path)
    assert r.status_code in (404, 405), (path, r.status_code)
    assert b"import " not in r.content and b"SMARTPOLI_" not in r.content


def test_real_assets_still_served(client):
    for p in ("/static/app.js", "/static/style.css", "/static/app-icon-192.png", "/static/manifest.webmanifest"):
        assert client.get(p).status_code == 200, p


def test_unknown_url_gets_html_404_for_browsers_and_json_for_api(client):
    r = client.get("/admin", headers={"accept": "text/html"})
    assert r.status_code == 404 and "<h1>404</h1>" in r.text
    r = client.get("/patients/999999/nope", headers={"accept": "application/json"})
    assert r.status_code in (401, 404) and r.headers["content-type"].startswith("application/json")


def test_api_docs_are_not_public(client):
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(p).status_code == 404


def test_security_headers_present(client):
    h = client.get("/").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert "object-src 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert "no-store" in client.get("/auth/me").headers["cache-control"]      # API answers are never cached


def test_protected_api_needs_login(client):
    for p in ("/patients", "/patients/1", "/patients/1/dashboard", "/auth/me", "/doctor/patients", "/caregiver/patients"):
        assert client.get(p).status_code in (401, 403), p


def test_rate_limit_triggers(client, monkeypatch):
    monkeypatch.delenv("SMARTPOLI_DISABLE_RATE_LIMIT", raising=False)
    import web_security
    web_security._hits.clear()
    codes = [client.post("/triage/next-question", json={"symptom_id": "x", "answers": {}}).status_code for _ in range(65)]
    assert 429 in codes
    web_security._hits.clear()


def test_install_page_assets_and_login_link(client):
    assert 'href="/install"' in client.get("/login").text
    page = client.get("/install").text
    assert "Care Together" in page and "Live Healthier" in page and "Install SmartPoli" in page
    for p in ("/static/install.css", "/static/install.js", "/static/install-family.jpg"):
        assert client.get(p).status_code == 200, p


def test_app_files_are_revalidated_and_api_answers_are_never_cached(client):
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"
    assert client.get("/auth/me").headers["cache-control"] == "no-store"


def test_pages_carry_a_version_so_app_files_can_be_cached_for_a_year(client):
    import re
    html = client.get("/").text
    versions = set(re.findall(r'/static/[\w\-.]+\.(?:js|css)\?v=(\w+)', html))
    assert len(versions) == 1 and "/static/app.js?v=" in html and "/static/style.css?v=" in html
    v = versions.pop()
    r = client.get(f"/static/app.js?v={v}")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"          # unversioned: re-checked each time
    assert "max-age=86400" in client.get("/static/app-icon-192.png").headers["cache-control"]
    for page in ("/login", "/voice", "/install", "/doctor", "/caregiver"):
        assert "?v=" in client.get(page).text, page


def test_responses_are_gzip_compressed(client):
    r = client.get("/static/app.js", headers={"accept-encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    assert client.get("/static/app.js", headers={"accept-encoding": "identity"}).headers.get("content-encoding") is None


def test_the_builtin_sweep_skips_when_the_external_one_just_ran(monkeypatch):
    import time
    import main
    calls = []
    monkeypatch.setattr(main, "_reminder_sweep_job", lambda: calls.append(1))
    main._last_external_sweep[0] = time.time()
    main._in_process_sweep_job()
    assert calls == []
    main._last_external_sweep[0] = time.time() - 1000
    main._in_process_sweep_job()
    assert calls == [1]
