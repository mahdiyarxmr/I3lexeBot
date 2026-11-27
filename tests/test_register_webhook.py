"""Tests for scripts/register_webhook.py (registration parameters + check mode).

The Telegram ``Bot`` is mocked so no network access is required. These tests
pin the exact ``setWebhook`` parameters: the registered URL, the secret token,
``allowed_updates`` covering the bot's handlers, and — critically —
``drop_pending_updates=False`` so pending Telegram updates are preserved.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

import scripts.register_webhook as rw
from bot import config


class FakeBot:
    """Minimal fake of telegram.Bot recording the setWebhook arguments."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.webhook_info = None
        self.set_result = True
        self.unset_result = True

    async def get_webhook_info(self):
        return self.webhook_info

    async def set_webhook(self, url, secret_token=None, allowed_updates=None,
                          drop_pending_updates=None):
        self.last_set = {
            "url": url,
            "secret_token": secret_token,
            "allowed_updates": allowed_updates,
            "drop_pending_updates": drop_pending_updates,
        }
        return self.set_result

    async def delete_webhook(self, drop_pending_updates=None):
        self.last_unset = {"drop_pending_updates": drop_pending_updates}
        return self.unset_result


def _settings(**overrides):
    base = {
        "BOT_TOKEN": "123456:MOCK",
        "OWNER_ID": "6341879634",
        "WEBHOOK_URL": "https://kiramartin.vercel.app/api/webhook",
        "WEBHOOK_SECRET": "mock-secret",
    }
    base.update(overrides)
    return config.Config.from_env(base)


def test_set_webhook_parameters_preserve_pending_updates():
    fake = FakeBot("123456:MOCK")
    with patch.object(rw, "Bot", lambda token: fake):
        rc = asyncio.run(rw._set(_settings()))

    assert rc == 0
    assert fake.last_set["url"] == "https://kiramartin.vercel.app/api/webhook"
    assert fake.last_set["secret_token"] == "mock-secret"
    # All handlers are message / callback_query based.
    assert fake.last_set["allowed_updates"] == ["message", "callback_query"]
    # Pending updates must be preserved.
    assert fake.last_set["drop_pending_updates"] is False


def test_set_webhook_refuses_url_not_on_api_webhook():
    settings = _settings(WEBHOOK_URL="https://kiramartin.vercel.app/wrong")
    with pytest.raises(config.ConfigError):
        settings.validate_webhook_url()


def test_unset_webhook_does_not_drop_pending_updates():
    fake = FakeBot("123456:MOCK")
    with patch.object(rw, "Bot", lambda token: fake):
        rc = asyncio.run(rw._unset(_settings()))

    assert rc == 0
    assert fake.last_unset["drop_pending_updates"] is False


def test_check_reports_not_registered():
    fake = FakeBot("123456:MOCK")
    fake.webhook_info = MagicMock(url="", pending_update_count=2863,
                                  last_error_date=None, last_error_message=None,
                                  allowed_updates=None, ip_address=None,
                                  certificate_fingerprint=None)
    with patch.object(rw, "Bot", lambda token: fake):
        rc = asyncio.run(rw._check(_settings()))

    assert rc == 1  # not registered


def test_check_reports_registered_at_expected_url():
    fake = FakeBot("123456:MOCK")
    fake.webhook_info = MagicMock(
        url="https://kiramartin.vercel.app/api/webhook",
        pending_update_count=0,
        last_error_date=None, last_error_message=None,
        allowed_updates=["message", "callback_query"],
        ip_address="1.2.3.4", certificate_fingerprint=None,
    )
    with patch.object(rw, "Bot", lambda token: fake):
        rc = asyncio.run(rw._check(_settings()))

    assert rc == 0  # registered at expected URL


def test_check_reports_mismatched_url():
    fake = FakeBot("123456:MOCK")
    fake.webhook_info = MagicMock(
        url="https://old.example.com/api/webhook",
        pending_update_count=5,
        last_error_date=None, last_error_message=None,
        allowed_updates=None, ip_address=None, certificate_fingerprint=None,
    )
    with patch.object(rw, "Bot", lambda token: fake):
        rc = asyncio.run(rw._check(_settings()))

    assert rc == 1  # registered at a different URL
