"""Real-PostgreSQL integration tests for schema initialisation.

These tests spin up an isolated, throwaway PostgreSQL instance (via the
``pgserver`` package, which bundles PostgreSQL binaries) so the schema
initialisation, foreign keys and user registration are exercised against a
genuine database. They never touch ``DATABASE_URL`` or any production data.

If ``pgserver`` is not installed, every test is skipped gracefully.
"""

from __future__ import annotations

import asyncio
import tempfile
from types import SimpleNamespace

import pytest

pgserver = pytest.importorskip("pgserver")

from bot.db import Database, DatabaseError  # noqa: E402
from bot.handlers import ensure_user, start  # noqa: E402
from bot.schema import ALL_SQL  # noqa: E402

EXPECTED_TABLES = [
    "media", "votes", "owner_votes", "profile_votes", "users",
    "roles", "profile_photos",
    "group_counts", "group_chats", "review_messages", "bot_settings",
    "moderation_audit", "conversation_state", "rate_limits",
]

EXPECTED_FK_NAMES = [
    "fk_votes_media", "fk_owner_votes_media", "fk_moderation_audit_media",
    "fk_review_messages_media", "fk_group_counts_chat", "fk_roles_user",
    "fk_profile_photos_user", "fk_profile_votes_user",
]


@pytest.fixture(scope="module")
def pg_dsn():
    """Start an isolated PostgreSQL instance for the whole test module."""
    tmp = tempfile.mkdtemp(prefix="i3lexe-pg-")
    server = pgserver.get_server(tmp, cleanup_mode="delete")
    yield server.get_uri()
    server.cleanup()


def _tables(database: Database) -> set[str]:
    async def _run():
        return {
            r[0]
            for r in await database.fetchall(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        }
    return asyncio.run(_run())


def _constraints(database: Database) -> set[str]:
    async def _run():
        return {
            r[0]
            for r in await database.fetchall(
                "SELECT conname FROM pg_constraint "
                "WHERE contype = 'f' AND connamespace = 'public'::regnamespace"
            )
        }
    return asyncio.run(_run())


async def _ensure(database: Database) -> None:
    await database.ensure_ready()


def test_schema_initial_creation(pg_dsn):
    """Task 1/2: ensure_ready creates every table, including roles."""
    database = Database(pg_dsn)
    asyncio.run(_ensure(database))
    tables = _tables(database)
    for table in EXPECTED_TABLES:
        assert table in tables, f"table '{table}' was not created"
    asyncio.run(database.close())


def test_reinitialisation_is_idempotent(pg_dsn):
    """Task 5: running initialisation repeatedly never errors or duplicates."""
    database = Database(pg_dsn)
    asyncio.run(_ensure(database))
    first = _tables(database)
    for _ in range(3):
        asyncio.run(_ensure(database))
    assert _tables(database) == first
    # And a brand-new Database instance over the same database also succeeds.
    second = Database(pg_dsn)
    asyncio.run(_ensure(second))
    assert _tables(second) == first
    asyncio.run(database.close())
    asyncio.run(second.close())


def test_all_foreign_keys_created(pg_dsn):
    """Task 4: every declared FK exists and no FK references a missing table."""
    database = Database(pg_dsn)
    asyncio.run(_ensure(database))
    created = _constraints(database)
    for name in EXPECTED_FK_NAMES:
        assert name in created, f"foreign key '{name}' missing"
    asyncio.run(database.close())


def test_foreign_keys_enforced(pg_dsn):
    """A FK violation against roles must be rejected by the database."""
    database = Database(pg_dsn)
    asyncio.run(_ensure(database))

    async def _run():
        await database.execute(
            "INSERT INTO users(user_id, username) VALUES (777, 'u777')"
        )
        # roles.user_id must reference users.user_id
        await database.execute(
            "INSERT INTO roles(user_id, role) VALUES (999999, 'Admin')"
        )  # no user 999999 -> must fail

    with pytest.raises(DatabaseError):
        asyncio.run(_run())

    async def _cleanup():
        await database.execute("DELETE FROM users WHERE user_id = 777")
    asyncio.run(_cleanup())
    asyncio.run(database.close())


def test_register_new_user(pg_dsn, monkeypatch):
    """Task 10: registering a new user (ensure_user) works after init."""
    database = Database(pg_dsn)
    asyncio.run(_ensure(database))
    monkeypatch.setattr("bot.handlers.db", database)

    user = SimpleNamespace(id=4242, username="newbie", first_name="New")

    async def _run():
        await ensure_user(user)
        row = await database.fetchone(
            "SELECT username, role, color FROM users WHERE user_id=%s",
            (user.id,),
        )
        assert row is not None
        assert row[0] == "newbie"
        assert row[1] == "Member"
        # Registering again updates, never duplicates.
        await ensure_user(user)
        count = await database.fetchone(
            "SELECT COUNT(*) FROM users WHERE user_id=%s", (user.id,)
        )
        assert count[0] == 1

    asyncio.run(_run())
    asyncio.run(database.close())


def test_concurrent_initialisation_is_safe(pg_dsn):
    """Task 6: many concurrent webhook-style initialisations all succeed."""
    databases = [Database(pg_dsn) for _ in range(8)]

    async def _run():
        await asyncio.gather(*(_ensure(d) for d in databases))

    asyncio.run(_run())  # must not raise (advisory-lock serialisation)
    for d in databases:
        tables = _tables(d)
        for table in EXPECTED_TABLES:
            assert table in tables, f"table '{table}' missing after race"
        asyncio.run(d.close())


def test_start_responds_when_database_ready(pg_dsn, monkeypatch):
    """Task 11: /start works end-to-end once the database is ready."""
    database = Database(pg_dsn)
    asyncio.run(_ensure(database))
    monkeypatch.setattr("bot.handlers.db", database)
    monkeypatch.setattr("bot.handlers.OWNER_ID", 999999)

    reply = {}

    async def _reply_text(text, **kwargs):
        reply["text"] = text

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=555, username="starter", first_name="S"),
        effective_message=SimpleNamespace(reply_text=_reply_text),
    )
    asyncio.run(start(update, None))

    assert "به i3l.exe خوش اومدی" in reply["text"]
    async def _check():
        row = await database.fetchone(
            "SELECT username, role FROM users WHERE user_id=%s", (555,)
        )
        assert row == ("starter", "Member")
    asyncio.run(_check())
    asyncio.run(database.close())


def test_start_survives_database_outage(monkeypatch):
    """Task 9: a DatabaseError must not stop /start from replying."""

    class BrokenDb:
        async def execute(self, *a, **k):
            raise DatabaseError("database down")

        async def fetchone(self, *a, **k):
            raise DatabaseError("database down")

    monkeypatch.setattr("bot.handlers.db", BrokenDb())
    monkeypatch.setattr("bot.handlers.OWNER_ID", 999999)

    reply = {}

    async def _reply_text(text, **kwargs):
        reply["text"] = text

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=666, username="outage", first_name="O"),
        effective_message=SimpleNamespace(reply_text=_reply_text),
    )
    # Must not raise.
    asyncio.run(start(update, None))
    assert "به i3l.exe خوش اومدی" in reply["text"]
    assert "⚠️ سرویس در حال بازگشت" in reply["text"]


def test_raw_ddl_is_idempotent_when_reapplied(pg_dsn):
    """Every DDL statement tolerates re-application (IF NOT EXISTS / DO blocks)."""
    database = Database(pg_dsn)

    async def _run():
        await database.ensure_ready()
        # Re-apply the full DDL on an already-initialised database — this is what
        # a retried init would do; it must not raise or duplicate anything.
        await database.ensure_ready()
        async with database._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(ALL_SQL)

    asyncio.run(_run())
    for table in EXPECTED_TABLES:
        assert table in _tables(database)
    asyncio.run(database.close())
