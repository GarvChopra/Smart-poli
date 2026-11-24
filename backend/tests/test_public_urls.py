import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from public_urls import LOCAL_URL, allowed_hosts, public_base_url


def test_local_default_and_production_requires_an_https_origin(monkeypatch):
    monkeypatch.delenv("SMARTPOLI_PUBLIC_URL", raising=False)
    monkeypatch.delenv("SMARTPOLI_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("SMARTPOLI_REQUIRE_SECURE_CONFIG", raising=False)
    assert public_base_url() == LOCAL_URL

    monkeypatch.setenv("SMARTPOLI_REQUIRE_SECURE_CONFIG", "1")
    with pytest.raises(ValueError):
        public_base_url()
    for origin in ("http://app.example.test", "https://localhost:8000", "https://app.example.test/login"):
        monkeypatch.setenv("SMARTPOLI_PUBLIC_BASE_URL", origin)
        with pytest.raises(ValueError):
            public_base_url()
    monkeypatch.setenv("SMARTPOLI_PUBLIC_BASE_URL", "https://app.example.test/")
    assert public_base_url() == "https://app.example.test"


def test_hostname_allowlist_accepts_exact_hosts_only(monkeypatch):
    monkeypatch.setenv("SMARTPOLI_ALLOWED_HOSTS", "APP.example.test, service.example.test")
    app = FastAPI()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts(), www_redirect=False)

    @app.get("/")
    def home():
        return {}

    client = TestClient(app)
    assert client.get("/", headers={"Host": "app.example.test"}).status_code == 200
    assert client.get("/", headers={"Host": "service.example.test"}).status_code == 200
    assert client.get("/", headers={"Host": "other.example.test"}).status_code == 400
    monkeypatch.setenv("SMARTPOLI_ALLOWED_HOSTS", "*.example.test")
    with pytest.raises(ValueError):
        allowed_hosts()
