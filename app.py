"""ASGI entrypoint for the i3l.exe Telegram bot.

This module exposes a valid ASGI ``app`` (a FastAPI application) that:

* serves ``POST /api/webhook`` — the Telegram webhook endpoint,
* serves ``GET /health`` — a lightweight liveness probe,
* validates the webhook secret,
* parses Telegram updates with ``telegram.Update.de_json`` and dispatches them
  through the python-telegram-bot ``Application``.

It never starts a polling loop and never runs a background process, so it
runs correctly inside Vercel serverless functions. ``api/webhook.py`` re-exports
this ``app`` as the Vercel function entrypoint.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from telegram import Update

from bot import config
from bot.db import DatabaseError
from bot.runtime import ensure_ready
from bot.telegram_app import build_application

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("i3lexebot.webhook")

app = FastAPI(
    title="i3l.exe Telegram Bot",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

_application = None
_application_lock = asyncio.Lock()


async def get_application():
    """Return a lazily-created, initialized Telegram Application singleton.

    The Application is created once per warm container and reused across
    requests. ``initialize()`` only prepares the bot/session; it does **not**
    start a webhook server or a polling loop.
    """
    global _application
    if _application is None:
        async with _application_lock:
            if _application is None:
                settings = config.load_config()
                settings.validate_runtime()
                _application = build_application()
                await _application.initialize()
    return _application


async def _process(body: dict) -> None:
    application = await get_application()
    update = Update.de_json(body, application.bot)
    await application.process_update(update)


async def webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
):
    settings = config.load_config()
    try:
        settings.validate_runtime()
    except config.ConfigError as exc:
        # Never leak configuration details; just signal a server misconfiguration.
        logger.error("Runtime configuration error: %s", exc)
        return JSONResponse({"ok": False, "error": "server_misconfigured"}, status_code=500)

    # Validate the Telegram webhook secret token (constant-time comparison).
    expected = settings.webhook_secret
    if (
        not x_telegram_bot_api_secret_token
        or not expected
        or not secrets.compare_digest(str(expected), str(x_telegram_bot_api_secret_token))
    ):
        return JSONResponse({"ok": False, "error": "invalid_secret"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "invalid_body"}, status_code=400)

    try:
        await ensure_ready()
        await _process(body)
    except DatabaseError as exc:
        logger.error("Database error while processing update: %s", exc)
        return JSONResponse({"ok": False, "error": "database_unavailable"}, status_code=503)
    except Exception:
        # python-telegram-bot already logs handler errors internally. Acknowledge
        # the update so Telegram does not retry a non-retryable failure forever.
        logger.exception("Unexpected error while processing update")
        return JSONResponse({"ok": True, "processed": False}, status_code=200)

    return JSONResponse({"ok": True, "processed": True}, status_code=200)


async def health():
    return {"status": "ok"}


# TEMPORARY verification route (removed after migration verification):
# returns row counts from the configured PostgreSQL. Never returns the
# DATABASE_URL value or any other secret.
@app.get("/api/db-counts")
async def db_counts():
    import os

    import psycopg

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return {"ok": False, "error": "DATABASE_URL not set"}
    tables = [
        "users", "media", "votes", "owner_votes", "profile_votes", "roles",
        "profile_photos", "group_chats", "group_counts", "review_messages",
        "bot_settings", "moderation_audit", "conversation_state", "rate_limits",
    ]
    try:
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            counts = {}
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
                existing = {r[0] for r in cur.fetchall()}
                for table in tables:
                    if table in existing:
                        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                        counts[table] = cur.fetchone()[0]
                    else:
                        counts[table] = "MISSING_TABLE"
            return {"ok": True, "counts": counts}
    except Exception as exc:  # never leak the DSN or credentials
        logger.error("db_counts check failed: %s", type(exc).__name__)
        return {"ok": False, "error": type(exc).__name__}


@app.get("/health-verbose")
async def health_verbose():
    # TEMPORARY: health plus row counts, for rollout diagnostics.
    import os

    import psycopg

    out = {"status": "ok"}
    dsn = os.environ.get("DATABASE_URL", "")
    if dsn:
        try:
            with psycopg.connect(dsn, connect_timeout=10) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM users")
                    out["users"] = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM media")
                    out["media"] = cur.fetchone()[0]
        except Exception as exc:
            out["db_error"] = type(exc).__name__
    return out


async def root():
    return {"status": "ok", "service": "i3l.exe", "endpoint": "/api/webhook"}


app.add_api_route("/api/webhook", webhook, methods=["POST"])
# Alias so the handler works whether or not the serverless runtime strips the
# function's path prefix before handing the request to the ASGI app.
app.add_api_route("/", webhook, methods=["POST"])
app.add_api_route("/health", health, methods=["GET"])
app.add_api_route("/", root, methods=["GET"])
