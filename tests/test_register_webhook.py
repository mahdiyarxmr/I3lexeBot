"""Tests for scripts/register_webhook.py (registration parameters + check mode).

The Telegram ``Bot`` is mocked so no network access is required. These tests
pin the exact ``setWebhook`` parameters: the registered URL, the secret token,
``allowed_updates`` covering the bot's handlers, and — critically —
``drop_pending_updates=False`` so pending Telegram updates are preserved.

The fake ``WebhookInfo`` objects deliberately use a plain object with only the
fields PTB 22.x actually provides — they do NOT define
``certificate_fingerprint`` — so the tests would fail if ``_check`` accessed
that attribute directly instead of via ``getattr``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

import scripts.register_webhook as rw
from bot import config


class WebhookInfo:
    """Plain stand-in for telegram.WebhookInfo (PTB 22.x fields only).

    ``certificate_fingerprint`` is intentionally absent: PTB 22.x does not
    expose it, and accessing it directly raises AttributeError.
    """

    def __init__(self, *, url="", pending_update_count=0, last_error_date=None,
                 last_error_message=None, allowed_updates=None, ip_address=None,
                 certificate_fingerprint=None):
        self.url = url
        self.pending_update_count = pending_update_count
        self.last_error_date = last_error_date
        self.last_error_message = last_error_message
        self.allowed_updates = allowed_updates
        self.ip_address = ip_address
        # Only set when explicitly provided, mimicking an attribute that may
        # not exist on the real object.
        if certificate_fingerprint is not None:
            self.certificate_fingerprint = certificate_fingerprint


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
    fake.webhook_info = WebhookInfo(url="", pending_update_count=2863)
    with patch.object(rw, "Bot", lambda token: fake):
        rc = asyncio.run(rw._check(_settings()))

    assert rc == 1  # not registered


def test_check_reports_registered_at_expected_url():
    fake = FakeBot("123456:MOCK")
    fake.webhook_info = WebhookInfo(
        url="https://kiramartin.vercel.app/api/webhook",
        pending_update_count=0,
        allowed_updates=["message", "callback_query"],
        ip_address="1.2.3.4",
    )
    with patch.object(rw, "Bot", lambda token: fake):
        rc = asyncio.run(rw._check(_settings()))

    assert rc == 0  # registered at expected URL


def test_check_reports_mismatched_url():
    fake = FakeBot("123456:MOCK")
    fake.webhook_info = WebhookInfo(
        url="https://old.example.com/api/webhook",
        pending_update_count=5,
    )
    with patch.object(rw, "Bot", lambda token: fake):
        rc = asyncio.run(rw._check(_settings()))

    assert rc == 1  # registered at a different URL


def test_check_does_not_crash_without_certificate_fingerprint():
    """PTB 22.x WebhookInfo has no certificate_fingerprint attribute.

    _check must read it via getattr and not raise AttributeError.
    """
    fake = FakeBot("123456:MOCK")
    info = WebhookInfo(
        url="https://kiramartin.vercel.app/api/webhook",
        pending_update_count=0,
    )
    assert not hasattr(info, "certificate_fingerprint")
    fake.webhook_info = info
    with patch.object(rw, "Bot", lambda token: fake):
        rc = asyncio.run(rw._check(_settings()))

    assert rc == 0  # no crash, correctly reports registered


def test_check_reads_certificate_fingerprint_when_present():
    """If the attribute does exist, it is read and printed safely."""
    fake = FakeBot("123456:MOCK")
    fake.webhook_info = WebhookInfo(
        url="https://kiramartin.vercel.app/api/webhook",
        pending_update_count=0,
        certificate_fingerprint="AB:CD:EF",
    )
    with patch.object(rw, "Bot", lambda token: fake):
        rc = asyncio.run(rw._check(_settings()))

    assert rc == 0


def test_check_handles_none_info():
    fake = FakeBot("123456:MOCK")
    fake.webhook_info = None
    with patch.object(rw, "Bot", lambda token: fake):
        rc = asyncio.run(rw._check(_settings()))

    assert rc == 1
