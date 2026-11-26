"""Async PostgreSQL access layer for the i3l.exe bot.

Design notes
------------
* Uses ``psycopg`` v3 + ``AsyncConnectionPool`` (async, pooled). The pool is a
  module-level singleton created lazily on first use so a warm Vercel container
  reuses connections across invocations instead of reconnecting every request.
* ``autocommit=True`` keeps the ported handlers simple: each statement is
  committed immediately, matching the old SQLite code which called ``commit()``
  after each logical step.
* All queries are parameterised with ``%s`` placeholders.
* ``ensure_ready()`` is idempotent and creates the schema on first use, so a
  freshly provisioned database works without a manual step.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional, Sequence

import psycopg
from psycopg_pool import AsyncConnectionPool

from .schema import ALL_SQL

logger = logging.getLogger(__name__)


class DatabaseError(RuntimeError):
    """Raised when the database cannot be reached or a query fails."""


class Database:
    """Thin async wrapper around a shared psycopg connection pool."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn if dsn is not None else os.environ.get("DATABASE_URL", "")
        self._pool: AsyncConnectionPool | None = None
        self._lock = asyncio.Lock()
        self._schema_ready = False

    @property
    def dsn(self) -> str:
        return self._dsn

    async def _open_pool(self) -> AsyncConnectionPool:
        if not self._dsn:
            raise DatabaseError("DATABASE_URL is not configured.")
        pool = AsyncConnectionPool(
            self._dsn,
            min_size=1,
            max_size=max(1, int(os.environ.get("DB_POOL_MAX", "4"))),
            open=False,
            timeout=float(os.environ.get("DB_POOL_TIMEOUT", "10")),
            reconnect_timeout=300,
            kwargs={"autocommit": True},
        )
        try:
            await pool.open(wait=True)
        except Exception as exc:  # pragma: no cover - depends on external DB
            raise DatabaseError(f"Could not open PostgreSQL connection pool: {exc}") from exc
        self._pool = pool
        return pool

    async def _pool_ready(self) -> AsyncConnectionPool:
        if self._pool is None or self._pool.closed:
            async with self._lock:
                if self._pool is None or self._pool.closed:
                    await self._open_pool()
        assert self._pool is not None
        return self._pool

    async def ensure_ready(self) -> None:
        """Open the pool (once) and make sure the schema exists."""
        pool = await self._pool_ready()
        if self._schema_ready:
            return
        async with self._lock:
            if self._schema_ready:
                return
            try:
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(ALL_SQL)
                self._schema_ready = True
                logger.info("PostgreSQL schema is ready.")
            except Exception as exc:
                raise DatabaseError(f"Could not initialise database schema: {exc}") from exc

    async def execute(self, query: str, params: Sequence[Any] | None = None) -> int:
        """Run a statement, committing immediately. Returns the row count."""
        pool = await self._pool_ready()
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(query, params)
                    return cur.rowcount
        except psycopg.Error as exc:
            raise DatabaseError(str(exc)) from exc

    async def fetchone(
        self, query: str, params: Sequence[Any] | None = None
    ) -> Optional[tuple]:
        pool = await self._pool_ready()
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(query, params)
                    return cur.fetchone()
        except psycopg.Error as exc:
            raise DatabaseError(str(exc)) from exc

    async def fetchall(
        self, query: str, params: Sequence[Any] | None = None
    ) -> list[tuple]:
        pool = await self._pool_ready()
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(query, params)
                    return cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseError(str(exc)) from exc

    async def execute_returning(
        self, query: str, params: Sequence[Any] | None = None
    ) -> Optional[tuple]:
        """Run an INSERT/UPDATE ... RETURNING and return the first row."""
        pool = await self._pool_ready()
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(query, params)
                    return cur.fetchone()
        except psycopg.Error as exc:
            raise DatabaseError(str(exc)) from exc

    async def close(self) -> None:
        if self._pool is not None and not self._pool.closed:
            try:
                await self._pool.close()
            except Exception:  # pragma: no cover - best effort shutdown
                logger.debug("Error while closing connection pool", exc_info=True)
        self._pool = None
        self._schema_ready = False
