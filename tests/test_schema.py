"""Verify the PostgreSQL schema preserves every legacy table and column."""

from bot.schema import FOREIGN_KEY_SQL, SCHEMA_SQL

EXPECTED_TABLES = [
    "media", "votes", "owner_votes", "profile_votes", "users",
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


def test_unique_constraints_present():
    assert "UNIQUE (media_id, voter_id)" in SCHEMA_SQL
    assert "UNIQUE (profile_id, voter_id)" in SCHEMA_SQL


def test_foreign_keys_defined():
    for name in ("fk_votes_media", "fk_moderation_audit_media", "fk_group_counts_chat"):
        assert name in FOREIGN_KEY_SQL, name
