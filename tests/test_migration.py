"""Tests for the SQLite -> PostgreSQL import logic (no real database needed)."""

import sqlite3

from scripts.migrate_sqlite_to_postgres import TABLE_ORDER, copy_table


class FakePgCursor:
    """Records executed INSERT statements."""

    def __init__(self, target_columns):
        self.target_columns = target_columns
        self.inserts = []

    def execute(self, sql, params=None):
        self.last_sql = sql
        if sql.lstrip().upper().startswith("INSERT"):
            self.inserts.append((sql, params))
        if "information_schema.columns" in sql:
            self._result = [(c,) for c in self.target_columns]
        return self

    def fetchall(self):
        return self._result


def _make_source():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, role TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'alice', 'Admin'), (2, 'bob', 'Member')")
    conn.execute("CREATE TABLE media (id INTEGER PRIMARY KEY, file_id TEXT, kind TEXT)")
    conn.execute("INSERT INTO media VALUES (1, 'f1', 'video'), (2, 'f2', 'photo')")
    return conn


def test_table_order_includes_legacy_tables():
    for table in ("users", "media", "votes", "owner_votes", "roles", "profile_photos"):
        assert table in TABLE_ORDER, table


def test_copy_table_dry_run(monkeypatch):
    src = _make_source()
    pg = FakePgCursor(["user_id", "username", "role", "extra_target"])
    monkeypatch.setattr(
        "scripts.migrate_sqlite_to_postgres.pg_target_columns",
        lambda cur, table: pg.target_columns,
    )
    count = copy_table(pg, src, "users", dry_run=True)
    assert count == 2
    assert pg.inserts == []


def test_copy_table_inserts(monkeypatch):
    src = _make_source()
    pg = FakePgCursor(["user_id", "username", "role"])
    monkeypatch.setattr(
        "scripts.migrate_sqlite_to_postgres.pg_target_columns",
        lambda cur, table: pg.target_columns,
    )
    count = copy_table(pg, src, "users", dry_run=False)
    assert count == 2
    assert len(pg.inserts) == 2
    # Only the common columns are inserted (no target-only column).
    assert "user_id" in pg.inserts[0][0]
    assert "extra_target" not in pg.inserts[0][0]
    assert pg.inserts[0][0].endswith("ON CONFLICT DO NOTHING")
