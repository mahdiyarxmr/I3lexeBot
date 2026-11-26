"""Shared, lazily-initialised runtime singletons.

The webhook function runs in a serverless environment, so connections and
application state are created once per warm container and reused across
invocations. Nothing here stores *bot state* in memory — user conversation
state and rate-limit counters live in PostgreSQL (see ``state`` and
``rate_limiter``).
"""

from __future__ import annotations

from . import config
from .db import Database
from .ratelimit import RateLimiter
from .state import StateStore

settings = config.load_config()
OWNER_ID = settings.owner_id
AUTO_EDIT_EVERY = config.AUTO_EDIT_EVERY

# Singletons shared by every invocation on a warm Vercel container.
db = Database()
state = StateStore(db)
rate_limiter = RateLimiter(db)


async def ensure_ready() -> None:
    """Open the database pool and ensure the schema exists (idempotent)."""
    await db.ensure_ready()
