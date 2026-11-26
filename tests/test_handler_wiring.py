"""Verify the Telegram Application is wired with every original handler."""

import os

os.environ.setdefault("BOT_TOKEN", "123456789:TESTTOKEN1234567890")
os.environ.setdefault("OWNER_ID", "6341879634")

from bot.telegram_app import build_application  # noqa: E402

EXPECTED_CALLBACKS = {
    "start", "help_command", "profile_command", "announce_command",
    "admin_panel_command", "set_review_group", "restore_media_command",
    "receive_media", "text_router", "count_group_messages", "random_video",
    "random_music", "random_photo", "ranking", "review_callback",
    "vote_callback", "admin_panel_callback", "role_action_callback",
    "media_delete_callback", "menu_callback", "welcome",
}


def _registered_callbacks(application):
    found = set()
    for handlers in application.handlers.values():
        for handler in handlers:
            name = getattr(handler.callback, "__name__", None)
            if name:
                found.add(name)
    return found


def test_all_handlers_registered():
    application = build_application()
    found = _registered_callbacks(application)
    missing = EXPECTED_CALLBACKS - found
    assert not missing, f"Missing handlers: {sorted(missing)}"


def test_application_not_started():
    application = build_application()
    # Building must not initialise/start anything (no network, no loop).
    assert application._initialized is False
