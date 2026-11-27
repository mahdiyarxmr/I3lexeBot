"""Persistent, per-user conversation state.

The old bot stored multi-step interaction state in ``context.user_data``, an
in-memory dict that is lost when a Vercel function invocation ends. This store
keeps the same information in PostgreSQL so state survives cold starts and
concurrent invocations.

Values are JSON-encoded so any Python object (e.g. the ``await_role_user`` dict)
can be stored.

Waiting states are bounded:

* ``set_pending`` enforces mutual exclusion — entering one waiting state clears
  any other waiting state for the same user, so two flows can never overlap.
* ``get_fresh`` / ``pop_fresh`` honour a time-to-live (TTL) based on the row's
  ``updated_at`` timestamp, so stale, abandoned states expire automatically
  and never swallow later, unrelated messages.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .db import Database

# All keys that represent an active multi-step waiting state.
PENDING_KEYS = (
    "await_media_delete",
    "await_role_user",
    "await_bio",
    "await_profile_photo",
)

# Waiting states expire after this many seconds (10 minutes by default).
DEFAULT_TTL_SECONDS = 600


class StateStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, user_id: int, key: str, default: Any = None) -> Any:
        row = await self._db.fetchone(
            "SELECT state_value FROM conversation_state "
            "WHERE user_id = %s AND state_key = %s",
            (user_id, key),
        )
        if row is None or row[0] is None:
            return default
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return default

    async def get_fresh(
        self,
        user_id: int,
        key: str,
        default: Any = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> Any:
        """Like ``get``, but ignores state older than ``ttl_seconds``."""
        row = await self._db.fetchone(
            "SELECT state_value FROM conversation_state "
            "WHERE user_id = %s AND state_key = %s "
            "AND updated_at::timestamptz >= CURRENT_TIMESTAMP - make_interval(secs => %s::int)",
            (user_id, key, int(ttl_seconds)),
        )
        if row is None or row[0] is None:
            return default
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return default

    async def set(self, user_id: int, key: str, value: Any) -> None:
        encoded = json.dumps(value)
        await self._db.execute(
            "INSERT INTO conversation_state(user_id, state_key, state_value, updated_at) "
            "VALUES(%s, %s, %s, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, state_key) DO UPDATE SET "
            "state_value = excluded.state_value, updated_at = CURRENT_TIMESTAMP",
            (user_id, key, encoded),
        )

    async def set_pending(self, user_id: int, key: str, value: Any) -> None:
        """Enter a waiting state, clearing any other waiting state first.

        This enforces mutual exclusion between flows (e.g. a delete wait can
        never coexist with a bio-edit wait for the same user).
        """
        others = [k for k in PENDING_KEYS if k != key]
        if others:
            marks = ",".join(["%s"] * len(others))
            await self._db.execute(
                f"DELETE FROM conversation_state "
                f"WHERE user_id = %s AND state_key IN ({marks})",
                (user_id, *others),
            )
        await self.set(user_id, key, value)

    async def pop(self, user_id: int, key: str, default: Any = None) -> Any:
        row = await self._db.fetchone(
            "DELETE FROM conversation_state "
            "WHERE user_id = %s AND state_key = %s RETURNING state_value",
            (user_id, key),
        )
        if row is None or row[0] is None:
            return default
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return default

    async def pop_fresh(
        self,
        user_id: int,
        key: str,
        default: Any = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> Any:
        """Like ``pop``, but only removes and returns state within its TTL."""
        row = await self._db.fetchone(
            "DELETE FROM conversation_state "
            "WHERE user_id = %s AND state_key = %s "
            "AND updated_at::timestamptz >= CURRENT_TIMESTAMP - make_interval(secs => %s::int) "
            "RETURNING state_value",
            (user_id, key, int(ttl_seconds)),
        )
        if row is None or row[0] is None:
            return default
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return default

    async def clear(self, user_id: int) -> None:
        await self._db.execute(
            "DELETE FROM conversation_state WHERE user_id = %s", (user_id,)
        )
