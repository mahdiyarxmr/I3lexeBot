#!/usr/bin/env python3
"""Safely migrate the i3l.exe bot from SQLite to PostgreSQL.

This utility:

* reads one or more historical SQLite databases (by default ``i3l_backup.db``),
* creates the PostgreSQL schema (the exact same DDL the runtime uses),
* copies every existing table and row — users, media, votes, review messages,
  group settings, moderation audit, and all legacy tables — preserving primary
  keys and unique constraints,
* resets the identity sequences so new inserts do not collide,
* adds foreign keys idempotently (a dangling legacy reference is reported and
  skipped rather than failing the whole import),
* **never** modifies or deletes the source SQLite files.

The import is idempotent: re-running it will not duplicate rows or overwrite
existing PostgreSQL rows.

Usage
-----
    # From the repository root:
    export DATABASE_URL="postgresql://user:pass@host:5432/i3lexebot"
    python scripts/migrate_sqlite_to_postgres.py

    # Import several databases, inspect without writing:
    python scripts/migrate_sqlite_to_postgres.py \
        --sqlite i3l_backup.db --sqlite i3l_v1.db --dry-run

    # Create the target database first (best effort, needs maintenance access):
    python scripts/migrate_sqlite_to_postgres.py --create-database
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from urllib.parse import urlparse, urlunparse

# Make the ``bot`` package importable regardless of the current working dir.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import psycopg  # noqa: E402

from bot.schema import BACKFILL_SQL, FOREIGN_KEY_SQL, SCHEMA_SQL  # noqa: E402

# Tables that carry an identity/serial primary key and need a setval afterwards.
IDENTITY_TABLES = ("media", "owner_votes", "moderation_audit")

# Copy order: parents before children so foreign keys (once added) hold.
TABLE_ORDER = [
    "users",
    "media",
    "votes",
    "owner_votes",
    "profile_votes",
    "roles",
    "profile_photos",
    "group_chats",
    "group_counts",
    "review_messages",
    "bot_settings",
    "moderation_audit",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def pg_target_columns(cur, table: str) -> list[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
        (table,),
    )
    return [r[0] for r in cur.fetchall()]


def sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def target_tables(cur) -> set[str]:
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public'"
    )
    return {r[0] for r in cur.fetchall()}


def copy_table(pg_cur, src: sqlite3.Connection, table: str, dry_run: bool) -> int:
    target_cols = pg_target_columns(pg_cur, table)
    if not target_cols:
        return 0
    src_cols = sqlite_columns(src, table)
    common = [c for c in src_cols if c in target_cols]
    if not common:
        return 0

    col_sql = ", ".join(f'"{c}"' for c in common)
    select_cols = ", ".join(f'"{c}"' for c in common)
    placeholders = ", ".join(["%s"] * len(common))
    insert_sql = (
        f'INSERT INTO "{table}" ({col_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
    )
    select_sql = f'SELECT {select_cols} FROM "{table}"'

    rows = src.execute(select_sql).fetchall()
    inserted = 0
    if dry_run:
        log(f"  [dry-run] {table}: {len(rows)} row(s) would be copied")
        return len(rows)
    for row in rows:
        pg_cur.execute(insert_sql, tuple(row))
        inserted += 1
    return inserted


def reset_sequences(pg_cur) -> None:
    for table in IDENTITY_TABLES:
        seq = pg_cur.execute(
            "SELECT pg_get_serial_sequence(%s, 'id')", (table,)
        ).fetchone()[0]
        if not seq:
            continue
        cur_max = pg_cur.execute(f'SELECT COALESCE(MAX(id), 1) FROM "{table}"').fetchone()[0]
        pg_cur.execute(f"SELECT setval(%s, GREATEST(%s::bigint, 1))", (seq, cur_max))
        log(f"  sequence {table}.id -> {cur_max}")


def apply_foreign_keys(pg_cur) -> None:
    try:
        pg_cur.execute(FOREIGN_KEY_SQL)
        log("  foreign keys applied")
    except Exception as exc:  # pragma: no cover - depends on legacy data
        log(f"  WARNING: could not apply all foreign keys: {exc}")
        log("  Data was imported; foreign keys can be added manually later.")


def ensure_database(dsn: str, target_db: str) -> bool:
    """Best-effort creation of the target database via a maintenance connection."""
    parsed = urlparse(dsn)
    maintenance = urlunparse(parsed._replace(path="/postgres"))
    try:
        conn = psycopg.connect(maintenance, autocommit=True, connect_timeout=10)
    except Exception as exc:
        log(f"  Could not open maintenance connection to create database: {exc}")
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
            if cur.fetchone():
                log(f"  database '{target_db}' already exists")
                return True
            quoted = '"' + target_db.replace('"', '""') + '"'
            cur.execute(f"CREATE DATABASE {quoted}")
        log(f"  created database '{target_db}'")
        return True
    except Exception as exc:
        log(f"  Could not create database '{target_db}': {exc}")
        return False
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sqlite",
        action="append",
        dest="sqlite_files",
        help="SQLite database file to import (repeatable). Defaults to i3l_backup.db.",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--create-database", action="store_true",
                        help="Best-effort create the target database before importing.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be imported without writing.")
    args = parser.parse_args()

    sqlite_files = args.sqlite_files or ["i3l_backup.db"]
    if not args.database_url:
        log("ERROR: DATABASE_URL is not set and --database-url was not provided.")
        return 2

    for path in sqlite_files:
        if not os.path.exists(path):
            log(f"ERROR: SQLite file not found: {path}")
            return 2

    target_db = urlparse(args.database_url).path.lstrip("/") or "i3lexebot"

    log("=" * 70)
    log("i3l.exe SQLite -> PostgreSQL migration")
    log(f"  source databases : {', '.join(sqlite_files)}")
    log(f"  target database  : {target_db}")
    log(f"  dry run          : {args.dry_run}")
    log("=" * 70)

    if args.create_database and not args.dry_run:
        ensure_database(args.database_url, target_db)

    try:
        conn = psycopg.connect(args.database_url, autocommit=True, connect_timeout=15)
    except Exception as exc:
        log(f"ERROR: Could not connect to PostgreSQL: {exc}")
        return 2

    try:
        with conn.cursor() as cur:
            log("Creating PostgreSQL schema ...")
            cur.execute(SCHEMA_SQL)
            existing = target_tables(cur)

            for sqlite_path in sqlite_files:
                log(f"\nImporting {sqlite_path} ...")
                src = sqlite3.connect(sqlite_path)
                try:
                    # Import in dependency order; skip tables absent from source.
                    for table in TABLE_ORDER:
                        if table not in existing:
                            continue
                        src_cols = sqlite_columns(src, table) if src.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                        ).fetchone() else []
                        if not src_cols:
                            continue
                        count = copy_table(cur, src, table, args.dry_run)
                        log(f"  {table}: {count} row(s)")
                finally:
                    src.close()

            if not args.dry_run:
                log("\nBackfilling legacy review_status ...")
                cur.execute(BACKFILL_SQL)
                log("Resetting identity sequences ...")
                reset_sequences(cur)
                log("Applying foreign keys ...")
                apply_foreign_keys(cur)

            log("\nMigration summary:")
            for table in TABLE_ORDER:
                if table not in existing:
                    continue
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                log(f"  {table:<18} {cur.fetchone()[0]}")
    finally:
        conn.close()

    log("\nDone. The source SQLite databases were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
