"""Ensure the deployed runtime never starts a polling loop."""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RUNTIME_FILES = [
    "app.py",
    "main.py",
    "api/webhook.py",
    "bot/handlers.py",
    "bot/telegram_app.py",
    "bot/runtime.py",
    "bot/db.py",
    "bot/state.py",
    "bot/ratelimit.py",
    "bot/config.py",
    "bot/schema.py",
    "bot/ui.py",
]


def test_no_run_polling_in_runtime():
    for rel in RUNTIME_FILES:
        path = os.path.join(ROOT, rel)
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        assert "run_polling" not in content, f"run_polling found in {rel}"


def test_no_run_webhook_in_runtime():
    for rel in RUNTIME_FILES:
        path = os.path.join(ROOT, rel)
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        assert not re.search(r"\.run_webhook\s*\(", content), f"run_webhook found in {rel}"


def test_webhook_route_defined():
    with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
        content = fh.read()
    assert "/api/webhook" in content
