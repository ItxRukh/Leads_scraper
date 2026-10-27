import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
from config import settings


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "boss")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "s3cret-pass")
    auth._fails.clear()

    app = FastAPI()
    app.include_router(auth.router)
    app.add_middleware(auth.AuthMiddleware)

    @app.get("/leads")
    def leads():
        return {"ok": True}

    @app.post("/webhooks/instantly")
    def hook():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return TestClient(app)


def login(client, password="s3cret-pass", username="boss"):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_no_password_set_means_the_api_stays_open(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "")
    app = FastAPI()
    app.add_middleware(auth.AuthMiddleware)

    @app.get("/leads")
    def leads():
        return {"ok": True}

    assert TestClient(app).get("/leads").status_code == 200
    assert auth.auth_enabled() is False


def test_protected_routes_need_a_token(secured):
    assert secured.get("/leads").status_code == 401
    assert secured.get("/leads", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    token = login(secured).json()["token"]
    assert secured.get("/leads", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_health_status_and_webhooks_stay_public(secured):
    assert secured.get("/health").status_code == 200
    assert secured.get("/auth/status").json() == {"auth_required": True}
    assert secured.post("/webhooks/instantly").status_code == 200


def test_wrong_credentials_are_rejected(secured):
    assert login(secured, password="nope").status_code == 401
    assert login(secured, username="someone").status_code == 401


def test_five_wrong_attempts_lock_the_client_out_even_for_the_right_password(secured):
    for _ in range(5):
        assert login(secured, password="nope").status_code == 401
    assert login(secured).status_code == 429


def test_a_tampered_or_expired_token_is_refused(secured, monkeypatch):
    token = login(secured).json()["token"]
    payload, sig = token.split(".")
    assert auth.verify_token(f"{payload}.{sig[:-2]}AA") is None            # bad signature
    assert auth.verify_token(token) is not None

    monkeypatch.setattr(settings, "AUTH_TOKEN_HOURS", -1)
    expired, _ = auth.make_token("boss")
    assert auth.verify_token(expired) is None


def test_me_checks_the_token(secured):
    assert secured.get("/auth/me").status_code == 401
    token = login(secured).json()["token"]
    assert secured.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json() == {"ok": True}


def test_preflight_requests_are_not_blocked(secured):
    assert secured.options("/leads", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"}).status_code != 401
