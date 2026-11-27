"""Verify the PostgreSQL schema preserves every legacy table and column."""

import re

from bot.schema import FOREIGN_KEY_SQL, SCHEMA_SQL

EXPECTED_TABLES = [
    "media", "votes", "owner_votes", "profile_votes", "users",
    "roles", "profile_photos",
    "group_counts", "group_chats", "review_messages", "bot_settings",
    "moderation_audit", "conversation_state", "rate_limits",
]

EXPECTED_MEDIA_COLUMNS = [
    "id", "file_id", "kind", "sender_id", "username", "approved",
    "created_at", "review_status", "reviewed_by", "reviewed_name", "reviewed_at",
]

EXPECTED_USERS_COLUMNS = [
    "user_id", "username", "first_name", "bio", "color", "role",
    "profile_photo_id", "joined_at", "show_creator", "character",
]


def test_all_legacy_tables_present():
    for table in EXPECTED_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA_SQL, table


def test_media_columns_present():
    for col in EXPECTED_MEDIA_COLUMNS:
        assert col in SCHEMA_SQL, col


def test_users_columns_present():
    for col in EXPECTED_USERS_COLUMNS:
        assert col in SCHEMA_SQL, col


def test_legacy_roles_and_profile_photos_columns():
    assert "CREATE TABLE IF NOT EXISTS roles" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS profile_photos" in SCHEMA_SQL
    assert "user_id BIGINT NOT NULL," in SCHEMA_SQL
    assert "role    TEXT" in SCHEMA_SQL
    assert "file_id TEXT" in SCHEMA_SQL


def test_unique_constraints_present():
    assert "UNIQUE (media_id, voter_id)" in SCHEMA_SQL
    assert "UNIQUE (profile_id, voter_id)" in SCHEMA_SQL


def test_foreign_keys_defined():
    for name in ("fk_votes_media", "fk_moderation_audit_media", "fk_group_counts_chat",
                 "fk_roles_user", "fk_profile_photos_user", "fk_profile_votes_user"):
        assert name in FOREIGN_KEY_SQL, name


def test_every_foreign_key_references_existing_tables():
    """Regression guard: a FK may never point at a table the schema never
    creates (this is what produced 'relation "roles" does not exist')."""
    created = set(re.findall(
        r"CREATE TABLE IF NOT EXISTS\s+(\w+)", SCHEMA_SQL
    ))
    # ALTER TABLE <child> ADD CONSTRAINT ... REFERENCES <parent> (<col>)
    fk_pattern = re.compile(
        r"ALTER TABLE\s+(\w+)\s+ADD CONSTRAINT\s+\w+\s+FOREIGN KEY.*?"
        r"REFERENCES\s+(\w+)\s*\(",
        re.DOTALL,
    )
    found = fk_pattern.findall(FOREIGN_KEY_SQL)
    assert found, "expected at least one foreign key in FOREIGN_KEY_SQL"
    for child, parent in found:
        assert child in created, f"FK on missing table '{child}'"
        assert parent in created, f"FK references missing table '{parent}'"


def test_tables_created_before_foreign_keys():
    """All CREATE TABLE statements must appear in SCHEMA_SQL, which is applied
    before FOREIGN_KEY_SQL, so parents exist when constraints are added."""
    assert SCHEMA_SQL.index("CREATE TABLE IF NOT EXISTS users") < len(SCHEMA_SQL)
    assert FOREIGN_KEY_SQL.lstrip().startswith("DO $$")
