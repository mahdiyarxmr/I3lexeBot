"""Tests for the FastAPI webhook endpoint (secret validation + routing)."""

from fastapi.testclient import TestClient

import app as app_module


def _client(monkeypatch, secret="s3cret"):
    monkeypatch.setenv("BOT_TOKEN", "123:test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("WEBHOOK_SECRET", secret)
    monkeypatch.setenv("OWNER_ID", "6341879634")
    # Avoid any real Telegram / PostgreSQL calls.
    monkeypatch.setattr(app_module, "ensure_ready", _async_noop)
    monkeypatch.setattr(app_module, "_process", _async_noop)
    return TestClient(app_module.app)


async def _async_noop(*args, **kwargs):
    return None


def test_health(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_missing_secret_rejected(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/api/webhook", json={"update_id": 1})
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_secret"


def test_wrong_secret_rejected(monkeypatch):
    client = _client(monkeypatch, secret="correct")
    resp = client.post(
        "/api/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert resp.status_code == 401


def test_valid_secret_dispatches(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post(
        "/api/webhook",
        json={"update_id": 42, "message": {"message_id": 1}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_invalid_json_rejected(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post(
        "/api/webhook",
        content="not-json",
        headers={
            "X-Telegram-Bot-Api-Secret-Token": "s3cret",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 400


def test_missing_config_returns_500(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    client = TestClient(app_module.app)
    resp = client.post(
        "/api/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert resp.status_code == 500
