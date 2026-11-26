"""Persistent, per-user conversation state.

The old bot stored multi-step interaction state in ``context.user_data``, an
in-memory dict that is lost when a Vercel function invocation ends. This store
keeps the same information in PostgreSQL so state survives cold starts and
concurrent invocations.

Values are JSON-encoded so any Python object (e.g. the ``await_role_user`` dict)
can be stored.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .db import Database


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

    async def set(self, user_id: int, key: str, value: Any) -> None:
        encoded = json.dumps(value)
        await self._db.execute(
            "INSERT INTO conversation_state(user_id, state_key, state_value, updated_at) "
            "VALUES(%s, %s, %s, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, state_key) DO UPDATE SET "
            "state_value = excluded.state_value, updated_at = CURRENT_TIMESTAMP",
            (user_id, key, encoded),
        )

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

    async def clear(self, user_id: int) -> None:
        await self._db.execute(
            "DELETE FROM conversation_state WHERE user_id = %s", (user_id,)
        )
