"""Tests for conversation-state handling (bug fixes + in-group review).

Covers the reported bug: after ``await_media_delete`` was armed, every text
message was treated as a media ID and non-numeric input re-armed the flag
forever, trapping users in a "شناسه محتوا باید عددی باشد" loop.

Scenarios:
1. A stale/armed state must not swallow ordinary messages.
2. Non-numeric input gives guidance but does NOT re-arm the state forever.
3. /cancel clears all waiting state.
4. Waiting states expire after their TTL.
5. Waiting states are mutually exclusive (they do not interfere).
6. cancel_waiting_command clears every related state key.
7. /cancel is wired as a registered CommandHandler.
8. Delete-media still works end-to-end for owner/admin.
9. In-group media submission posts the review back into the same group.

PostgreSQL is provided by the bundled ``pgserver`` package; tests skip
gracefully if it is not installed. No production data is touched.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from types import SimpleNamespace

# telegram_app builds a real Application at import time; give it a syntactically
# valid (dummy) token. This is a test value, never a real bot token.
os.environ.setdefault("BOT_TOKEN", "123456789:TESTtestTESTtestTESTtestTESTtest")

import pytest

pgserver = pytest.importorskip("pgserver")

from bot.db import Database  # noqa: E402
from bot.handlers import (  # noqa: E402
    cancel_waiting_command,
    ensure_user,
    media_delete_callback,
    receive_media,
    text_router,
)
from bot.ratelimit import RateLimiter  # noqa: E402
from bot.runtime import OWNER_ID  # noqa: E402
from bot.state import DEFAULT_TTL_SECONDS, StateStore  # noqa: E402

OWNER = 123456
USER = 42


@pytest.fixture(scope="module")
def pg_dsn():
    tmp = tempfile.mkdtemp(prefix="i3lexe-state-")
    server = pgserver.get_server(tmp, cleanup_mode="delete")
    yield server.get_uri()
    server.cleanup()


@pytest.fixture()
def database(pg_dsn, monkeypatch):
    db = Database(pg_dsn)

    async def _init():
        await db.ensure_ready()

    asyncio.run(_init())
    monkeypatch.setattr("bot.handlers.db", db)
    store = StateStore(db)
    monkeypatch.setattr("bot.handlers.state", store)
    monkeypatch.setattr("bot.handlers.rate_limiter", RateLimiter(db))
    monkeypatch.setattr("bot.handlers.OWNER_ID", OWNER)
    yield db
    asyncio.run(db.close())


def _msg(text, user_id=USER, chat_type="private", **kw):
    replies = []

    async def _reply_text(text, **kwargs):
        replies.append(text)

    chat = SimpleNamespace(id=chat_type if chat_type != "private" else USER,
                           type=chat_type, title="گروه")
    effective_message = SimpleNamespace(
        text=text, reply_text=_reply_text, chat=chat, reply_to_message=None
    )
    return SimpleNamespace(
        text=text,
        text_content=text,
        effective_user=SimpleNamespace(id=user_id, username="u", first_name="U"),
        effective_message=effective_message,
        message=effective_message,
        chat=chat,
    ), replies


async def _arm_delete_wait(db, user_id=OWNER):
    store = StateStore(db)
    await store.set_pending(user_id, "await_media_delete", True)


def test_1_stale_state_does_not_swallow_ordinary_messages(database):
    """A first unrelated message gets a one-time guidance reply, but the state
    is consumed — the very next ordinary message is processed normally instead
    of being trapped in a permanent 'شناسه محتوا' loop."""
    asyncio.run(_arm_delete_wait(database, OWNER))

    update1, replies1 = _msg("یک پیام عادی", user_id=OWNER)
    asyncio.run(text_router(update1, None))
    assert any("شناسه محتوا" in r for r in replies1)

    # State was consumed, so a normal message is no longer swallowed.
    update2, replies2 = _msg("پروفایل", user_id=OWNER)
    asyncio.run(text_router(update2, None))
    assert not any("شناسه محتوا" in r for r in replies2)


def test_2_non_numeric_input_does_not_rearm_forever(database):
    """Guidance is given, but the wait state is consumed (not re-armed), so the
    user is not trapped in an endless ID loop."""
    asyncio.run(_arm_delete_wait(database))
    update, replies = _msg("ابله", user_id=OWNER)

    async def _run():
        await text_router(update, None)

    asyncio.run(_run())
    assert any("شناسه محتوا باید عددی باشد" in r for r in replies)

    store = StateStore(database)
    assert asyncio.run(store.get(USER, "await_media_delete", False)) is False


def test_3_cancel_command_clears_state(database):
    asyncio.run(_arm_delete_wait(database))
    update, replies = _msg("/cancel", user_id=OWNER)

    async def _run():
        await cancel_waiting_command(update, None)

    asyncio.run(_run())
    assert any("لغو" in r for r in replies)

    store = StateStore(database)
    assert asyncio.run(store.get(OWNER, "await_media_delete", False)) is False

    # After cancel, a normal message is processed normally again.
    update2, replies2 = _msg("پروفایل")
    asyncio.run(text_router(update2, None))
    assert not any("شناسه محتوا" in r for r in replies2)


def test_4_wait_state_expires_after_ttl(database):
    """State older than the TTL is ignored by pop_fresh / get_fresh."""
    store = StateStore(database)
    asyncio.run(store.set(USER, "await_media_delete", True))
    # Force the row to look stale (11 minutes old).
    asyncio.run(database.execute(
        "UPDATE conversation_state SET updated_at = CURRENT_TIMESTAMP - INTERVAL '11 minutes' "
        "WHERE user_id = %s AND state_key = %s",
        (USER, "await_media_delete"),
    ))

    async def _run():
        return await store.pop_fresh(USER, "await_media_delete", False)

    assert asyncio.run(_run()) is False
    # Still present in storage, but never consumed.
    assert asyncio.run(store.get(USER, "await_media_delete", False)) is True

    # Fresh state within TTL is consumed normally.
    asyncio.run(store.set(USER, "await_media_delete", True))
    async def _run2():
        return await store.pop_fresh(USER, "await_media_delete", False)
    assert asyncio.run(_run2()) is True


def test_5_waiting_states_are_mutually_exclusive(database):
    store = StateStore(database)

    async def _run():
        await store.set_pending(USER, "await_bio", True)
        await store.set_pending(USER, "await_media_delete", True)

    asyncio.run(_run())
    assert asyncio.run(store.get(USER, "await_bio", False)) is False
    assert asyncio.run(store.get(USER, "await_media_delete", False)) is True


def test_6_clear_removes_all_related_state(database):
    store = StateStore(database)

    async def _run():
        await store.set_pending(USER, "await_bio", True)
        await store.set_pending(USER, "await_media_delete", True)
        await store.set_pending(USER, "await_role_user", {"action": "add", "role": "Admin"})
        await store.clear(USER)
        return (
            await store.get(USER, "await_bio", False),
            await store.get(USER, "await_media_delete", False),
            await store.get(USER, "await_role_user"),
        )

    assert asyncio.run(_run()) == (False, False, None)


def test_7_cancel_is_registered_handler(monkeypatch):
    from types import SimpleNamespace as SN

    from telegram.ext import CommandHandler

    from bot import telegram_app

    # Config is frozen; replace the module attribute with a stub token holder.
    monkeypatch.setattr(
        telegram_app,
        "settings",
        SN(bot_token="123456789:TESTtestTESTtestTESTtestTESTtest"),
    )

    app = telegram_app.build_application()
    commands = set()
    # PTB v20+: app.handlers is a dict {group: [handlers]} (or list of groups).
    groups = app.handlers.values() if isinstance(app.handlers, dict) else app.handlers
    for group in groups:
        entries = group if isinstance(group, (list, tuple)) else [group]
        for h in entries:
            if isinstance(h, CommandHandler):
                commands.update(h.commands)
    assert "cancel" in commands


def test_8_delete_media_still_works_for_admin(database, monkeypatch):
    """Owner/admin numeric ID deletes media + its votes."""
    async def _seed():
        await ensure_user(SimpleNamespace(id=USER, username="u", first_name="U"))
        mid = await database.execute_returning(
            "INSERT INTO media(file_id,kind,sender_id,username,approved) "
            "VALUES('f1','video',%s,'u',1) RETURNING id", (USER,)
        )
        await database.execute(
            "INSERT INTO votes(media_id,voter_id) VALUES(%s,%s)",
            (mid[0], USER),
        )
        return mid[0]

    media_id = asyncio.run(_seed())
    asyncio.run(_arm_delete_wait(database))

    update, replies = _msg(str(media_id), user_id=OWNER)
    asyncio.run(text_router(update, None))
    assert any("حذف شد" in r for r in replies)

    async def _check():
        media_row = await database.fetchone("SELECT id FROM media WHERE id=%s", (media_id,))
        votes = await database.fetchone("SELECT COUNT(*) FROM votes WHERE media_id=%s", (media_id,))
        return media_row, votes

    media_row, votes = asyncio.run(_check())
    assert media_row is None
    assert votes[0] == 0


def test_9_upload_confirms_to_sender_and_sends_review_to_admin_group(database, monkeypatch):
    """Upload flow: the sender gets a star confirmation, the content stays
    pending, and the review request (media + caption + inline buttons) is
    sent to the configured review group — never to an admin's DM."""
    sent = {}

    async def _send_media(chat_id, file_id, kind, caption, markup, context):
        sent["chat_id"] = chat_id
        sent["caption"] = caption
        sent["markup"] = markup
        return SimpleNamespace(message_id=999)

    monkeypatch.setattr("bot.handlers.send_media", _send_media)

    # Configure the review (admins) group.
    async def _set_group():
        await database.execute(
            "INSERT INTO bot_settings(setting_key, setting_value) VALUES('review_group_id', '-1005871054118') "
            "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value"
        )

    asyncio.run(_set_group())

    replies = []

    async def _reply_text(text, **kwargs):
        replies.append(text)

    group_msg = SimpleNamespace(
        video=None, audio=None, document=None,
        photo=[SimpleNamespace(file_id="pid")],
        reply_text=_reply_text,
        chat_id=777,
        chat=SimpleNamespace(id=777, chat_id=777, type="supergroup", title="گپ"),
        text=None,
    )
    update = SimpleNamespace(
        message=group_msg,
        effective_message=group_msg,
        effective_user=SimpleNamespace(id=USER, username="u", first_name="U"),
    )

    async def _run():
        await receive_media(update, None)

    asyncio.run(_run())

    # 1. Sender receives the star confirmation — and nothing about archiving.
    assert any("⭐ محتوای شما برای تأیید ارسال شد" in r for r in replies)
    assert not any("آرشیو" in r for r in replies)

    # 2. Review goes to the configured admins group, with buttons.
    assert sent.get("chat_id") == -1005871054118
    assert sent.get("markup") is not None
    caption = sent.get("caption", "")
    assert "⭐ <b>محتوای جدید</b>" in caption
    assert "فرستنده: @u" in caption
    assert "<code>" in caption
    assert "نوع: عکس" in caption

    # 3. The media is stored pending, not approved/archived.
    async def _check():
        row = await database.fetchone(
            "SELECT approved, review_status, chat_id, message_id "
            "FROM media m JOIN review_messages r ON r.media_id = m.id WHERE r.chat_id = -1005871054118"
        )
        return row

    row = asyncio.run(_check())
    assert row is not None
    assert row[0] == 0
    assert row[1] == "pending"
    assert row[2] == -1005871054118
    assert row[3] == 999


def test_10_default_review_group_used_until_changed(database, monkeypatch):
    """With no setting stored, the built-in default review group is used and
    the review is posted there (the setting only overrides the default)."""
    from bot.handlers import DEFAULT_REVIEW_GROUP_ID

    sent = {}

    async def _send_media(chat_id, file_id, kind, caption, markup, context):
        sent["chat_id"] = chat_id
        return SimpleNamespace(message_id=999)

    monkeypatch.setattr("bot.handlers.send_media", _send_media)

    # Ensure no review group is configured (other tests may have set one).
    async def _clear_group():
        await database.execute(
            "DELETE FROM bot_settings WHERE setting_key='review_group_id'"
        )

    asyncio.run(_clear_group())
    assert DEFAULT_REVIEW_GROUP_ID == -1005871054118

    replies = []

    async def _reply_text(text, **kwargs):
        replies.append(text)

    msg = SimpleNamespace(
        video=None, audio=None, document=None,
        photo=[SimpleNamespace(file_id="pid-default")],
        reply_text=_reply_text,
        chat_id=USER,
        chat=SimpleNamespace(id=USER, chat_id=USER, type="private", title=""),
        text=None,
    )
    update = SimpleNamespace(
        message=msg,
        effective_message=msg,
        effective_user=SimpleNamespace(id=USER, username="u", first_name="U"),
    )

    asyncio.run(receive_media(update, None))

    assert any("⭐ محتوای شما برای تأیید ارسال شد" in r for r in replies)
    assert sent.get("chat_id") == DEFAULT_REVIEW_GROUP_ID

    async def _check():
        row = await database.fetchone(
            "SELECT approved, review_status FROM media WHERE file_id='pid-default'"
        )
        return row

    assert asyncio.run(_check()) == (0, "pending")


def test_11_owner_upload_also_requires_review(database, monkeypatch):
    """Even the owner's upload is NOT auto-approved: it stays pending, the
    sender gets the star confirmation, and the review goes to the group."""
    sent = {}

    async def _send_media(chat_id, file_id, kind, caption, markup, context):
        sent["chat_id"] = chat_id
        return SimpleNamespace(message_id=999)

    monkeypatch.setattr("bot.handlers.send_media", _send_media)

    replies = []

    async def _reply_text(text, **kwargs):
        replies.append(text)

    msg = SimpleNamespace(
        video=None, audio=None, document=None,
        photo=[SimpleNamespace(file_id="pid-owner")],
        reply_text=_reply_text,
        chat_id=OWNER,
        chat=SimpleNamespace(id=OWNER, chat_id=OWNER, type="private", title=""),
        text=None,
    )
    update = SimpleNamespace(
        message=msg,
        effective_message=msg,
        effective_user=SimpleNamespace(id=OWNER, username="owner", first_name="O"),
    )

    asyncio.run(receive_media(update, None))

    assert any("⭐ محتوای شما برای تأیید ارسال شد" in r for r in replies)
    assert not any("آرشیو" in r for r in replies)  # never "ذخیره شد و وارد آرشیو شد"
    assert sent.get("chat_id") is not None  # review was still sent out

    async def _check():
        row = await database.fetchone(
            "SELECT approved, review_status FROM media WHERE file_id='pid-owner'"
        )
        return row

    assert asyncio.run(_check()) == (0, "pending")


def test_12_owner_panel_review_group_button_sets_group(database, monkeypatch):
    """The owner-panel 'گروه بررسی' button arms a wait; a numeric group ID
    saves the setting, non-numeric input gives guidance and exits the wait."""
    from bot.handlers import review_group_callback

    edits = []

    async def _edit(text, **kwargs):
        edits.append(text)

    q = SimpleNamespace(
        from_user=SimpleNamespace(id=OWNER, username="owner", first_name="O"),
        answer=_noop_answer,
        message=SimpleNamespace(
            photo=None, video=None, audio=None, document=None,
            edit_message_text=_edit, edit_message_caption=_edit,
        ),
        edit_message_text=_edit,
        edit_message_caption=_edit,
    )
    update = SimpleNamespace(callback_query=q)
    asyncio.run(review_group_callback(update, None))

    store = StateStore(database)
    assert asyncio.run(store.get(OWNER, "await_review_group", False)) is True
    assert any("همین‌حالا گروه بررسی" in t for t in edits)

    # Numeric input saves the setting.
    update2, replies2 = _msg("-1001234567890", user_id=OWNER)
    asyncio.run(text_router(update2, None))
    assert any("گروه بررسی جدید تنظیم شد" in r for r in replies2)

    async def _check():
        row = await database.fetchone(
            "SELECT setting_value FROM bot_settings WHERE setting_key='review_group_id'"
        )
        return row

    assert asyncio.run(_check()) == ("-1001234567890",)

    # Non-numeric input: guidance once, wait state consumed (no trap).
    asyncio.run(review_group_callback(update, None))
    update3, replies3 = _msg("نامعلوم", user_id=OWNER)
    asyncio.run(text_router(update3, None))
    assert any("شناسه گروه باید عددی باشد" in r for r in replies3)
    assert asyncio.run(store.get(OWNER, "await_review_group", False)) is False


async def _noop_answer(**kwargs):
    return None


def test_13_bot_commands_published_for_slash_suggestions():
    """The command list published via set_my_commands covers the bot's
    commands, so typing '/' shows them as suggestions."""
    from bot.telegram_app import bot_commands

    commands = {c.command for c in bot_commands()}
    for expected in ("start", "help", "profile", "cancel", "manage",
                     "setreviewgroup", "restoremedia"):
        assert expected in commands, expected
    # Every command needs a non-empty description.
    for c in bot_commands():
        assert c.description


def test_default_ttl_is_ten_minutes():
    assert DEFAULT_TTL_SECONDS == 600
