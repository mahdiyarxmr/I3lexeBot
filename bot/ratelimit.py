"""Serverless-safe anti-spam rate limiting backed by PostgreSQL.

The old bot kept counters in process-local dictionaries. That does not survive
Vercel's serverless model (a cold start resets the limits). This module keeps
the counters in PostgreSQL and performs the check + increment in a single
atomic ``INSERT ... ON CONFLICT ... RETURNING`` statement, so concurrent
requests cannot race each other.
"""

from __future__ import annotations

import logging
import time

from .db import Database

logger = logging.getLogger(__name__)

# Bucket names used by the handlers.
COMMAND_BUCKET = "command"
MEDIA_BUCKET = "media"


class RateLimiter:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def allow(
        self, user_id: int, bucket: str, limit: int, window_seconds: int
    ) -> bool:
        """Return True if the action is allowed; False if it is rate limited.

        The first request inside a window always succeeds; subsequent requests
        are counted until ``limit`` is exceeded.
        """
        now = int(time.time())
        window_end = now - window_seconds
        row = await self._db.fetchone(
            "INSERT INTO rate_limits(bucket, user_id, count, window_start) "
            "VALUES(%s, %s, 1, %s) "
            "ON CONFLICT (bucket, user_id) DO UPDATE SET "
            "count = CASE WHEN rate_limits.window_start <= %s "
            "THEN 1 ELSE rate_limits.count + 1 END, "
            "window_start = CASE WHEN rate_limits.window_start <= %s "
            "THEN %s ELSE rate_limits.window_start END "
            "RETURNING count",
            (bucket, user_id, now, window_end, window_end, now),
        )
        count = int(row[0]) if row and row[0] is not None else 1
        return count <= limit
