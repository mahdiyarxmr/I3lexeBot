"""Tests for persistent conversation state and the anti-spam rate limiter."""

from bot.ratelimit import RateLimiter
from bot.state import StateStore


class FakeDatabase:
    def __init__(self, fetchone_result=None):
        self.fetchone_result = fetchone_result
        self.executed = []

    async def fetchone(self, sql, params=None):
        self.last_fetch = (sql, params)
        return self.fetchone_result

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return 0


def test_state_set_uses_upsert(monkeypatch):
    db = FakeDatabase()
    store = StateStore(db)
    import asyncio

    asyncio.run(store.set(42, "await_bio", True))
    sql, params = db.executed[0]
    assert "ON CONFLICT(user_id, state_key)" in sql
    assert params[0] == 42 and params[1] == "await_bio"
    assert params[2] == "true"  # JSON encoded


def test_state_get_decodes_json():
    db = FakeDatabase(fetchone_result=("true",))
    store = StateStore(db)
    import asyncio

    assert asyncio.run(store.get(7, "await_bio")) is True


def test_state_pop_uses_delete_returning():
    db = FakeDatabase(fetchone_result=('{"action": "add", "role": "Admin"}',))
    store = StateStore(db)
    import asyncio

    value = asyncio.run(store.pop(7, "await_role_user"))
    assert value == {"action": "add", "role": "Admin"}
    assert "RETURNING" in db.last_fetch[0]


def test_rate_limiter_allows_up_to_limit():
    # Simulate the database returning an increasing count.
    db = FakeDatabase()
    counts = iter([1, 2, 10, 11])
    captured = []

    async def fake_fetchone(sql, params=None):
        captured.append((sql, params))
        return (next(counts),)

    db.fetchone = fake_fetchone
    limiter = RateLimiter(db)

    import asyncio

    assert asyncio.run(limiter.allow(1, "command", 10, 2)) is True
    assert asyncio.run(limiter.allow(1, "command", 10, 2)) is True
    assert asyncio.run(limiter.allow(1, "command", 10, 2)) is True
    assert asyncio.run(limiter.allow(1, "command", 10, 2)) is False
    # The SQL uses a single atomic statement with RETURNING.
    sql, params = captured[-1]
    assert "INSERT INTO rate_limits" in sql
    assert "RETURNING count" in sql
