#!/usr/bin/env python3
"""Register (or clear) the Telegram webhook for the i3l.exe bot.

Run this **after** deploying to Vercel and configuring the environment
variables. It calls Telegram's ``setWebhook`` API with the deployed URL and the
shared secret token. It refuses to run unless ``BOT_TOKEN``, ``WEBHOOK_URL``
and ``WEBHOOK_SECRET`` are configured, so the webhook cannot be pointed at an
unconfigured deployment by accident.

Usage
-----
    export BOT_TOKEN="123456:ABC..."
    export OWNER_ID="6341879634"
    export WEBHOOK_URL="https://i3lexebot.vercel.app/api/webhook"
    export WEBHOOK_SECRET="a-long-random-string"
    python scripts/register_webhook.py

    # Remove the webhook (returns the bot to polling-free, webhook-off state):
    python scripts/register_webhook.py --unset

    # Validate configuration without calling Telegram:
    python scripts/register_webhook.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from telegram import Bot  # noqa: E402

from bot import config  # noqa: E402


async def _set(settings: config.Config) -> int:
    bot = Bot(token=settings.bot_token)
    info = await bot.get_webhook_info()
    current = info.url if info else ""
    print(f"Current webhook URL : {current or '(none)'}")
    result = await bot.set_webhook(
        settings.webhook_url,
        secret_token=settings.webhook_secret,
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=False,
    )
    if result:
        print(f"Webhook registered: {settings.webhook_url}")
        return 0
    print("Telegram did not confirm webhook registration.")
    return 1


async def _unset(settings: config.Config) -> int:
    bot = Bot(token=settings.bot_token)
    result = await bot.delete_webhook(drop_pending_updates=False)
    print("Webhook removed." if result else "Telegram did not confirm removal.")
    return 0 if result else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--unset", action="store_true",
                        help="Delete the webhook instead of registering it.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate configuration without calling Telegram.")
    args = parser.parse_args()

    settings = config.load_config()
    try:
        settings.validate_webhook_url()
    except config.ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    if args.dry_run:
        print("Configuration OK.")
        print(f"  WEBHOOK_URL     : {settings.webhook_url}")
        print(f"  WEBHOOK_SECRET  : {'<set>' if settings.webhook_secret else '<missing>'}")
        return 0

    if args.unset:
        return asyncio.run(_unset(settings))
    return asyncio.run(_set(settings))


if __name__ == "__main__":
    raise SystemExit(main())
